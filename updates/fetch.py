"""Finding, downloading and verifying a release. Nothing here installs anything: a verified release
is put in updates/pending/ with a request file, and the update service (deploy/apply-update.py, run
by systemd as root) checks it again by itself and installs it."""

import json
import os
import pathlib
import shutil
import threading
import time
from datetime import UTC, datetime

import version
from collector import http

from . import SUFFIXES, pubkey_path, repo, signing, updates_dir

API = "https://api.github.com/repos/{repo}/releases?per_page=20"
MANIFEST, SIGNATURE = "release.json", "release.json.sig"
LOCK = threading.Lock()
_last_check: dict = {}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_json(path: pathlib.Path, data: dict, mode: int = 0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def app_status() -> dict:
    return _read_json(updates_dir() / "app-status.json") or {"stage": "idle"}


def _say(stage: str, **more):
    _write_json(updates_dir() / "app-status.json", {"stage": stage, "at": _now(), **more})


def service_status():
    """What the update service last reported (it writes status.json as root)."""
    return _read_json(updates_dir() / "status.json")


def key_fingerprint():
    """The short code of this server's developer key, or None when there is no (usable) key."""
    try:
        key = signing.load_pubkey(pubkey_path())
    except signing.UpdateError:
        return None
    return signing.fingerprint(key) if key else None


def configured() -> tuple:
    """(ok, why not)."""
    if not repo():
        return False, "no GitHub repository is set (UPDATE_REPO in .env)"
    try:
        if signing.load_pubkey(pubkey_path()) is None:
            return (
                False,
                "this server has no developer key (update_pubkey.txt), so it cannot tell a real update from a fake one",
            )
    except signing.UpdateError as e:
        return False, str(e)
    return True, ""


def _pick(releases, current: str):
    """The newest published release that carries a manifest and its signature: (assets by name) or
    None."""
    for rel in releases if isinstance(releases, list) else []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        assets = {
            a.get("name"): a
            for a in rel.get("assets", [])
            if isinstance(a, dict) and isinstance(a.get("name"), str)
        }
        if MANIFEST in assets and SIGNATURE in assets:
            return assets
    return None


def _small(url: str, limit: int) -> bytes:
    tmp = updates_dir() / "tmp" / f"small-{os.getpid()}-{threading.get_ident()}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        http.download(url, tmp, limit, suffixes=SUFFIXES)
        return tmp.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)


def _signed_manifest(assets: dict, pubkey: bytes):
    m_url, s_url = (
        assets[MANIFEST].get("browser_download_url", ""),
        assets[SIGNATURE].get("browser_download_url", ""),
    )
    data = _small(m_url, signing.MAX_MANIFEST_BYTES)
    sig = _small(s_url, 4096).decode("ascii", "replace")
    return data, sig, signing.verify_release(data, sig, pubkey)


def check() -> dict:
    """Asks GitHub for the newest release and reports it. Only a release whose signature checks out
    is ever reported."""
    ok, why = configured()
    out = {
        "configured": ok,
        "current": version.SERVER_VERSION,
        "repo": repo(),
        "checked_at": _now(),
    }
    if not ok:
        out["problem"] = why
        _last_check.clear()
        _last_check.update(out)
        return out
    try:
        pubkey = signing.load_pubkey(pubkey_path())
        releases = http.get_json(
            API.format(repo=repo()),
            suffixes=SUFFIXES,
            headers={"Accept": "application/vnd.github+json"},
        )
        assets = _pick(releases, version.SERVER_VERSION)
        if assets is None:
            out["latest"], out["newer"] = None, False
        else:
            _, _, manifest = _signed_manifest(assets, pubkey)
            out["latest"] = manifest["release"]
            out["notes"] = manifest.get("notes", "")
            out["size"] = manifest["bundle"]["size"]
            out["created"] = manifest.get("created", "")
            try:
                signing.check_manifest_for(manifest, version.SERVER_VERSION)
                out["newer"] = True
            except signing.UpdateError as e:
                out["newer"] = False
                if signing.is_newer(manifest["release"], version.SERVER_VERSION):
                    # newer, but cannot be installed straight over this version
                    out["problem"] = str(e)
    except (signing.UpdateError, http.FetchError) as e:
        out["problem"] = str(e)
    _last_check.clear()
    _last_check.update(out)
    return out


def last_check() -> dict:
    return dict(_last_check)


def stage(requested_by: str = "") -> dict:
    """Downloads the newest release, verifies it, and files the request for the update service.
    Raises UpdateError with a message for the admin."""
    if not LOCK.acquire(blocking=False):
        raise signing.UpdateError("an update is already being fetched")
    try:
        ok, why = configured()
        if not ok:
            raise signing.UpdateError(why)
        svc = service_status() or {}
        if (updates_dir() / "request.json").exists() or svc.get("stage") in ("working",):
            raise signing.UpdateError("an update is already waiting to be installed")
        pubkey = signing.load_pubkey(pubkey_path())
        _say("checking")
        try:
            releases = http.get_json(
                API.format(repo=repo()),
                suffixes=SUFFIXES,
                headers={"Accept": "application/vnd.github+json"},
            )
            assets = _pick(releases, version.SERVER_VERSION)
            if assets is None:
                raise signing.UpdateError("GitHub has no signed server release yet")
            manifest_bytes, sig, manifest = _signed_manifest(assets, pubkey)
            signing.check_manifest_for(manifest, version.SERVER_VERSION)
            name = manifest["bundle"]["name"]
            asset = assets.get(name)
            if not asset:
                raise signing.UpdateError(
                    f"the release lists {name} but it is not attached to the release"
                )
            incoming = updates_dir() / f"incoming-{os.getpid()}-{int(time.time())}"
            shutil.rmtree(incoming, ignore_errors=True)
            incoming.mkdir(parents=True)
            try:
                _say("downloading", release=manifest["release"], size=manifest["bundle"]["size"])
                done = [0]

                def progress(n):
                    done[0] = max(0, done[0] + n)

                http.download(
                    asset.get("browser_download_url", ""),
                    incoming / name,
                    manifest["bundle"]["size"],
                    progress,
                    suffixes=SUFFIXES,
                )
                _say("verifying", release=manifest["release"])
                signing.verify_bundle(incoming / name, manifest)
                (incoming / MANIFEST).write_bytes(manifest_bytes)
                (incoming / SIGNATURE).write_text(sig.strip() + "\n", encoding="ascii")
                pending = updates_dir() / "pending"
                shutil.rmtree(pending, ignore_errors=True)
                os.replace(incoming, pending)
            finally:
                shutil.rmtree(incoming, ignore_errors=True)
            _write_json(
                updates_dir() / "request.json",
                {"release": manifest["release"], "requested_by": requested_by, "at": _now()},
            )
            _say("waiting", release=manifest["release"])
            return {"release": manifest["release"], "state": "waiting"}
        except http.FetchError as e:
            _say("failed", message=str(e))
            raise signing.UpdateError(f"the download failed: {e}")
        except signing.UpdateError as e:
            _say("failed", message=str(e))
            raise
    finally:
        LOCK.release()
