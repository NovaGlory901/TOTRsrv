import os
import pathlib
import sys
import tempfile

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
from collector import http

LIVE = "--live" in sys.argv
from check import check, finish

http.BACKOFF = (0, 0, 0)  # no waiting in tests
http.MIN_SECONDS_BETWEEN_REQUESTS = 0


class Resp:
    """Stands in for a response: serves `body`, optionally drops the connection after `drop_after`
    bytes."""

    def __init__(self, body: bytes, status=200, drop_after=None, content_length=True):
        self.body, self.status, self.pos, self.drop_after = body, status, 0, drop_after
        self.headers = {"Content-Length": str(len(body))} if content_length else {}

    def read(self, n=-1):
        if self.drop_after is not None and self.pos >= self.drop_after:
            raise ConnectionResetError("connection reset by peer")
        end = len(self.body) if n < 0 else self.pos + n
        if self.drop_after is not None:
            end = min(end, self.drop_after)
        chunk = self.body[self.pos : end]
        self.pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def script(steps):
    """Replaces the single-request function with a script: each call pops the next step (exception
    or Resp)."""
    calls = []

    def fake(url, timeout=30, headers=None):
        calls.append(dict(headers or {}))
        step = steps[min(len(calls) - 1, len(steps) - 1)]
        if isinstance(step, Exception):
            raise step
        return step() if callable(step) else step

    http._open_once = fake
    return calls


DATA = bytes(range(256)) * 400  # 102,400 bytes
URL = "https://archive.org/download/x/a.mp3"


def run(steps, max_bytes=10**9, referer=None):
    calls = script(steps)
    dest = tmp / f"f{len(list(tmp.iterdir()))}.bin"
    seen = []
    try:
        size = http.download(URL, dest, max_bytes, seen.append, referer=referer)
        return calls, dest, size, sum(seen), None
    except http.FetchError as e:
        return calls, dest, None, sum(seen), e


# the failures from the real run: 500, 503, SSL handshake timeout
calls, dest, size, prog, err = run(
    [
        http.FetchError("500 from archive.org", retryable=True),
        http.FetchError("503 from archive.org", retryable=True),
        http.FetchError(
            "could not reach archive.org: _ssl.c:1064: The handshake operation timed out",
            retryable=True,
        ),
        Resp(DATA),
    ]
)
check(
    "retries 500, 503 and an SSL handshake timeout, then succeeds",
    err is None and dest.read_bytes() == DATA and len(calls) == 4,
    err,
)
check("progress equals the file size after retries", prog == len(DATA), prog)

calls, dest, size, prog, err = run([http.FetchError("403 from ccmixter.org", retryable=False)])
check(
    "a 403 is not retried (one attempt)",
    err is not None and len(calls) == 1 and "403" in str(err),
    (len(calls), err),
)
calls, dest, size, prog, err = run([http.FetchError("404 from archive.org", retryable=False)])
check("a 404 is not retried", err is not None and len(calls) == 1)
calls, dest, size, prog, err = run([http.FetchError("503 from archive.org", retryable=True)])
check(
    "gives up after the last retry (4 attempts), with the real reason",
    err is not None and len(calls) == 4 and "503" in str(err),
    (len(calls), err),
)

# resume
first = lambda: Resp(DATA, drop_after=40_000)
second = lambda: Resp(DATA[40_000:], status=206)
calls, dest, size, prog, err = run([first, second])
check(
    "a dropped connection resumes instead of starting over",
    err is None and dest.read_bytes() == DATA,
    err,
)
check(
    "...by asking for the remaining bytes (Range header)",
    len(calls) == 2 and calls[1].get("Range") == "bytes=40000-",
    calls,
)
check("...and progress is not double counted", prog == len(DATA), prog)

ignores_range = lambda: Resp(DATA, status=200)
calls, dest, size, prog, err = run([lambda: Resp(DATA, drop_after=30_000), ignores_range])
check(
    "a server that ignores Range restarts the file cleanly",
    err is None and dest.read_bytes() == DATA,
    err,
)
check("...and progress still ends at the file size", prog == len(DATA), prog)

calls, dest, size, prog, err = run([lambda: Resp(DATA, drop_after=10_000)] * 4)
check(
    "a connection that keeps dropping eventually fails plainly",
    err is not None and "dropped" in str(err),
    err,
)

# size limits
calls, dest, size, prog, err = run([Resp(DATA)], max_bytes=1000)
check(
    "a file declared larger than the limit is refused before writing",
    err is not None and not dest.exists() and len(calls) == 1,
    err,
)
calls, dest, size, prog, err = run([Resp(DATA, content_length=False)], max_bytes=1000)
check(
    "a file that lies about its size is cut off at the limit",
    err is not None and "size limit" in str(err),
    err,
)

# the ccMixter fix: Referer only when it is honest
page = "https://ccmixter.org/files/morgantj/21453"
cc_url = "https://ccmixter.org/content/morgantj/x.mp3"


def referer_sent(url, referer):
    calls = script([Resp(b"abc")])
    http.download(url, tmp / "r.bin", 10**6, referer=referer)
    return calls[0].get("Referer")


check("sends the release page as Referer for same-site files", referer_sent(cc_url, page) == page)
check(
    "...also for archive.org files",
    referer_sent(URL, "https://archive.org/details/x") == "https://archive.org/details/x",
)
check(
    "never sends a Referer from a different site",
    referer_sent(cc_url, "https://archive.org/details/x") is None,
)
check(
    "never sends a non-https or javascript Referer",
    referer_sent(cc_url, "javascript:alert(1)") is None
    and referer_sent(cc_url, "http://ccmixter.org/x") is None,
)
check("no Referer when none is given", referer_sent(cc_url, None) is None)
check(
    "download() never overrides the honest User-Agent",
    "User-Agent" not in http.download.__code__.co_consts
    and "user-agent" not in http.download.__code__.co_consts,
)

# JSON retry
real_open_once = http._open_once


class JsonResp(Resp):
    pass


calls = script([http.FetchError("503 from archive.org", retryable=True), JsonResp(b'{"ok": true}')])
check(
    "get_json retries a 503 and then succeeds",
    http.get_json("https://archive.org/advancedsearch.php") == {"ok": True} and len(calls) == 2,
)
script([http.FetchError("403 from archive.org", retryable=False)])
try:
    http.get_json("https://archive.org/x")
    check("get_json does not retry a 403", False)
except http.FetchError:
    check("get_json does not retry a 403", True)

# live, header-only (connection opened and closed without reading the file)
if LIVE:
    import importlib

    importlib.reload(http)
    http.MIN_SECONDS_BETWEEN_REQUESTS = 1.0
    print("--- live header checks (nothing downloaded)")
    for label, url, ref in (
        (
            "ccMixter, with the release page as Referer",
            "https://ccmixter.org/content/morgantj/morgantj_-_8-bit_kraftsman.mp3",
            "https://ccmixter.org/files/morgantj/21453",
        ),
        (
            "ccMixter, no Referer (the old behaviour)",
            "https://ccmixter.org/content/morgantj/morgantj_-_8-bit_kraftsman.mp3",
            None,
        ),
    ):
        headers = {"Referer": ref} if ref else {}
        try:
            with http._open_once(url, timeout=20, headers=headers) as r:
                print(
                    f"     {label}: HTTP {r.status}, {r.headers.get('Content-Length')} bytes, {r.headers.get('Content-Type')}"
                )
                check(f"live: {label}", (r.status == 200) == bool(ref))
        except http.FetchError as e:
            print(f"     {label}: {e}")
            check(f"live: {label}", not ref)

finish()
