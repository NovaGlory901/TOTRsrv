"""
The only way this package talks to the internet.

  * https only, no IP-address hosts, no odd ports.
  * The host must be on ALLOWED_SUFFIXES. Redirects are re-checked hop by hop, so an allowed
    site cannot bounce us to somewhere else. Search results come from third parties, so a
    poisoned result must never be able to make your server fetch something on your home network.
  * Every address the host resolves to must be a public one (blocks DNS tricks that point an
    allowed-looking name at 10.x.x.x or 127.0.0.1).
  * Polite: identifies itself honestly, waits between requests to the same host, caps sizes.
  * Sturdy: the Internet Archive is often overloaded, so temporary failures (500/502/503/504/429,
    timeouts, dropped connections) are retried with growing waits, and a half-finished download
    resumes where it stopped. Real refusals (403, 404) are not retried.
"""

import http.client
import ipaddress
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import user_agent

# Deezer: public catalogue API and its cover pictures (metadata only, no audio)
ALLOWED_SUFFIXES = {
    "archive.org",
    "ccmixter.org",
    "jamendo.com",
    "musicbrainz.org",
    "coverartarchive.org",
    "deezer.com",
    "dzcdn.net",
}
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
MAX_JSON_BYTES = 8 * 1024 * 1024
CHECK_DNS = True  # tests turn this off for a local test server
BACKOFF = (5, 15, 45)  # seconds to wait before retry 1, 2, 3
RETRY_CODES = {429, 500, 502, 503, 504}

_last_request: dict = {}
_lock = threading.Lock()


class FetchError(Exception):
    def __init__(self, message: str, retryable: bool = False, retry_after: int = 0):
        super().__init__(message)
        self.retryable, self.retry_after = retryable, retry_after


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def host_allowed(url: str, suffixes=None) -> bool:
    """`suffixes` replaces the collector's host list for one caller (the updater uses it for
    GitHub); everything else about the check is the same."""
    suffixes = suffixes or ALLOWED_SUFFIXES
    try:
        p = urllib.parse.urlparse(url)
        host = (p.hostname or "").lower()
        if p.scheme != "https" or not host or p.port not in (None, 443) or p.username or p.password:
            return False
        try:
            ipaddress.ip_address(host)
            return False  # no IP-address hosts
        except ValueError:
            pass
        if not any(host == s or host.endswith("." + s) for s in suffixes):
            return False
        if CHECK_DNS:
            for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
                if not ipaddress.ip_address(info[4][0]).is_global:
                    return False
        return True
    except (ValueError, OSError):
        return False


def link_allowed(url: str) -> bool:
    """For links shown to people (never fetched): https, and a site we use. Blocks javascript: and
    lookalikes."""
    try:
        p = urllib.parse.urlparse(url)
        host = (p.hostname or "").lower()
        return (
            p.scheme == "https"
            and bool(host)
            and any(host == d or host.endswith("." + d) for d in ALLOWED_SUFFIXES)
        )
    except ValueError:
        return False


def same_site(a: str, b: str) -> bool:
    ha, hb = _host(a), _host(b)
    return bool(ha and hb) and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha))


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, suffixes=None):
        super().__init__()
        self.suffixes = suffixes

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not host_allowed(newurl, self.suffixes):
            raise FetchError(f"refused a redirect to a host that is not allowed: {_host(newurl)}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_CheckedRedirects)


def _opener_for(suffixes):
    return _opener if not suffixes else urllib.request.build_opener(_CheckedRedirects(suffixes))


def _wait_turn(url: str):
    host = _host(url)
    with _lock:
        wait = _last_request.get(host, 0) + MIN_SECONDS_BETWEEN_REQUESTS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()


def _open_once(url: str, timeout: int = 30, headers: dict | None = None, suffixes=None):
    """One request. Raises FetchError, marked retryable when trying again might work."""
    if not host_allowed(url, suffixes):
        raise FetchError(f"host not allowed: {_host(url)}")
    _wait_turn(url)
    req = urllib.request.Request(
        url, headers={"User-Agent": user_agent(), "Accept": "*/*", **(headers or {})}
    )
    try:
        return _opener_for(suffixes).open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            retry_after = min(int(e.headers.get("Retry-After", "0") or 0), 60)
        except ValueError:
            retry_after = 0
        raise FetchError(
            f"{e.code} from {_host(url)}", retryable=e.code in RETRY_CODES, retry_after=retry_after
        )
    except FetchError:
        raise
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        raise FetchError(f"could not reach {_host(url)}: {getattr(e, 'reason', e)}", retryable=True)


def _pause(error: FetchError, attempt: int):
    time.sleep(max(error.retry_after, BACKOFF[attempt]))


def _open(url: str, timeout: int = 30, headers: dict | None = None, suffixes=None):
    for attempt in range(len(BACKOFF) + 1):
        try:
            return _open_once(url, timeout, headers, **({"suffixes": suffixes} if suffixes else {}))
        except FetchError as e:
            if not e.retryable or attempt == len(BACKOFF):
                raise
            _pause(e, attempt)


def get_json(url: str, params: dict | None = None, suffixes=None, headers: dict | None = None):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    for attempt in range(len(BACKOFF) + 1):
        try:
            with _open(url, headers=headers, suffixes=suffixes) as resp:
                body = resp.read(MAX_JSON_BYTES + 1)
            break
        except FetchError:
            raise
        except (OSError, http.client.HTTPException) as e:  # dropped mid-response
            if attempt == len(BACKOFF):
                raise FetchError(f"connection to {_host(url)} dropped: {e}")
            time.sleep(BACKOFF[attempt])
    if len(body) > MAX_JSON_BYTES:
        raise FetchError("response too large")
    try:
        return json.loads(body)
    except ValueError:
        raise FetchError("the site did not return valid JSON")


def download(
    url: str, dest, max_bytes: int, progress=None, referer: str | None = None, suffixes=None
) -> int:
    """
    Streams url into dest, never more than max_bytes, and returns the size written. Temporary
    failures retry and resume from the bytes already on disk. referer: the page this file belongs
    to. Some sites (ccMixter) refuse files unless the request says which of their own pages it came
    from, exactly as a browser does when you click the link. It is only sent when it is on the same
    site as the file, and the User-Agent stays honest.
    """
    headers = (
        {"Referer": referer}
        if referer and link_allowed(referer) and same_site(referer, url)
        else {}
    )
    done = 0
    for attempt in range(len(BACKOFF) + 1):
        try:
            req_headers = dict(headers)
            if done:
                req_headers["Range"] = f"bytes={done}-"
            resp = _open_once(
                url, timeout=60, headers=req_headers, **({"suffixes": suffixes} if suffixes else {})
            )
            with resp:
                if done and getattr(resp, "status", 200) != 206:
                    if progress:
                        progress(-done)  # the server ignored Range: start this file over
                    done = 0
                declared = resp.headers.get("Content-Length")
                if declared and declared.isdigit() and done + int(declared) > max_bytes:
                    raise FetchError("file is larger than the size limit")
                with open(dest, "ab" if done else "wb") as out:
                    while True:
                        try:
                            chunk = resp.read(1 << 16)
                        except (OSError, http.client.HTTPException) as e:
                            raise FetchError(f"connection dropped: {e}", retryable=True)
                        if not chunk:
                            return done
                        done += len(chunk)
                        if done > max_bytes:
                            raise FetchError("file is larger than the size limit")
                        # a disk error here is not retried: it is not the network's fault
                        out.write(chunk)
                        if progress:
                            progress(len(chunk))
        except FetchError as e:
            if not e.retryable or attempt == len(BACKOFF):
                raise
            if not os.path.exists(dest):
                done = 0
            _pause(e, attempt)
    raise FetchError("download did not complete")
