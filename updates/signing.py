"""What makes an update trustworthy: a release is a bundle (a .tar.gz of this server's code) plus a
small signed manifest.

    release.json        {"format": "totr-release", "format_version": 1, "product": "TOTRsrv", "release": "0.7.1", "created": "...",
                         "bundle": {"name": "TOTRsrv-0.7.1.tar.gz", "sha256": "...", "size": 123}, "min_from": "0.6.0", "notes": "..."}
    release.json.sig    the Ed25519 signature of the exact bytes of release.json, in base64

The signing key is held only by the developer. Its public half is in update_pubkey.txt, shipped with
every server, so a server can tell a real update from a fake one. The signature covers the manifest
and the manifest pins the bundle's SHA-256, so changing one byte of either is refused. The manifest
also carries the version, so an old genuine release cannot be replayed as if it were new (installing
only ever goes forward).

Nothing in this file touches the network or the database. The server (updates/fetch.py) and the
installer of updates (deploy/apply-update.py) both use it, and the second one checks everything
again by itself: it does not trust what the web app says it verified.
"""

import base64
import binascii
import hashlib
import json
import os
import pathlib
import re
import tarfile

PRODUCT = "TOTRsrv"
FORMAT = "totr-release"
FORMAT_VERSION = 1
MAX_MANIFEST_BYTES = 64 * 1024
MAX_BUNDLE_BYTES = 300 * 1024 * 1024
MAX_FILES = 20000
MAX_UNPACKED_BYTES = 600 * 1024 * 1024
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
BUNDLE_NAME_RE = re.compile(r"^TOTRsrv-\d{1,4}\.\d{1,4}\.\d{1,4}\.tar\.gz$")


class UpdateError(Exception):
    """A refusal that is safe to show to the admin."""


def version_tuple(v: str) -> tuple:
    if not isinstance(v, str) or not VERSION_RE.match(v):
        raise UpdateError(f"{v!r} is not a version like 1.2.3")
    return tuple(int(x) for x in v.split("."))


def is_newer(candidate: str, current: str) -> bool:
    return version_tuple(candidate) > version_tuple(current)


def parse_pubkey(text: str) -> bytes:
    """update_pubkey.txt: a line `ed25519 <base64 of the 32 key bytes> [comment]`. Blank lines and #
    comments are ignored."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2 or parts[0] != "ed25519":
            raise UpdateError("update_pubkey.txt must hold a line like: ed25519 <base64 key>")
        try:
            key = base64.b64decode(parts[1], validate=True)
        except (binascii.Error, ValueError):
            raise UpdateError("the public key in update_pubkey.txt is not valid base64")
        if len(key) != 32:
            raise UpdateError("an Ed25519 public key is 32 bytes")
        return key
    raise UpdateError("update_pubkey.txt holds no key")


def fingerprint(pubkey: bytes) -> str:
    """A short code to compare by eye (the first 12 hex digits of the key's SHA-256, in groups of
    four): 9f3a-77c1-0be2."""
    h = hashlib.sha256(pubkey).hexdigest()[:12]
    return "-".join(h[i : i + 4] for i in range(0, 12, 4))


def load_pubkey(path) -> bytes | None:
    """The key, or None when there is no key file or it is empty (updates are then simply not set up)."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    if not any(line.strip() and not line.strip().startswith("#") for line in text.splitlines()):
        return None
    return parse_pubkey(text)


def _ed25519():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError:
        raise UpdateError(
            "the 'cryptography' package is not installed (pip install -r requirements.txt)"
        )
    return ed25519, InvalidSignature


def verify(data: bytes, signature_b64: str, pubkey: bytes) -> None:
    """Raises UpdateError unless `signature_b64` is a valid Ed25519 signature of `data` by `pubkey`."""
    ed25519, InvalidSignature = _ed25519()
    try:
        signature = base64.b64decode((signature_b64 or "").strip(), validate=True)
    except (binascii.Error, ValueError):
        raise UpdateError("the signature is not valid base64")
    if len(signature) != 64:
        raise UpdateError("the signature has the wrong length")
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(pubkey).verify(signature, data)
    except InvalidSignature:
        raise UpdateError(
            "the signature does not match: this release was not signed with the developer's key"
        )
    except ValueError:
        raise UpdateError("the public key is not usable")


def sign(data: bytes, private_key) -> str:
    """Base64 signature of `data` (used by the developer's release tool; `private_key` is a
    cryptography Ed25519PrivateKey)."""
    return base64.b64encode(private_key.sign(data)).decode("ascii")


def parse_manifest(data: bytes) -> dict:
    """Checks the shape of release.json (after its signature has been verified) and returns it.
    Anything unexpected is refused."""
    if len(data) > MAX_MANIFEST_BYTES:
        raise UpdateError("release.json is too big")
    try:
        m = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise UpdateError("release.json is not valid JSON")
    if (
        not isinstance(m, dict)
        or m.get("format") != FORMAT
        or m.get("format_version") != FORMAT_VERSION
    ):
        raise UpdateError("release.json is not a TOTR release this version understands")
    if m.get("product") != PRODUCT:
        raise UpdateError(f"this release is for {m.get('product')!r}, not {PRODUCT}")
    version_tuple(m.get("release"))
    b = m.get("bundle")
    if not isinstance(b, dict):
        raise UpdateError("release.json names no bundle")
    if (
        not isinstance(b.get("name"), str)
        or not BUNDLE_NAME_RE.match(b["name"])
        or b["name"] != f"{PRODUCT}-{m['release']}.tar.gz"
    ):
        raise UpdateError("the bundle name does not match the release")
    if not isinstance(b.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", b["sha256"]):
        raise UpdateError("the bundle checksum is not a SHA-256")
    if (
        not isinstance(b.get("size"), int)
        or isinstance(b.get("size"), bool)
        or not 0 < b["size"] <= MAX_BUNDLE_BYTES
    ):
        raise UpdateError("the bundle size is missing or too large")
    if "min_from" in m:
        version_tuple(m["min_from"])
    if "notes" in m and (not isinstance(m["notes"], str) or len(m["notes"]) > 4000):
        raise UpdateError("the release notes are not text, or are too long")
    return m


def check_manifest_for(manifest: dict, current: str) -> None:
    """Is this release one the server at version `current` may install?"""
    if not is_newer(manifest["release"], current):
        raise UpdateError(
            f"{manifest['release']} is not newer than the installed {current}; updates only go forward"
        )
    if "min_from" in manifest and version_tuple(current) < version_tuple(manifest["min_from"]):
        raise UpdateError(
            f"this release can only be installed over {manifest['min_from']} or newer; install the releases in between first"
        )


def verify_release(manifest_bytes: bytes, signature_b64: str, pubkey: bytes) -> dict:
    """Signature first, then the shape: nothing in an unsigned manifest is ever looked at."""
    verify(manifest_bytes, signature_b64, pubkey)
    return parse_manifest(manifest_bytes)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_bundle(path, manifest: dict) -> None:
    size = os.path.getsize(path)
    if size != manifest["bundle"]["size"]:
        raise UpdateError("the downloaded bundle is not the size the signed release says")
    if sha256_file(path) != manifest["bundle"]["sha256"]:
        raise UpdateError("the downloaded bundle does not match the signed checksum")


def safe_unpack(bundle, dest) -> int:
    """Unpacks a bundle into `dest` (which must not exist yet). Only plain files and folders, only
    inside `dest`, no links or devices, capped in number and total size. A bundle that tries
    anything else is refused and `dest` is removed. Returns the number of files."""
    dest = pathlib.Path(dest)
    if dest.exists():
        raise UpdateError(f"{dest} already exists")
    dest.mkdir(parents=True)
    files = total = 0
    try:
        with tarfile.open(bundle, "r:gz") as tar:
            for member in tar:
                name = member.name
                parts = pathlib.PurePosixPath(name).parts
                if (
                    not name
                    or name.startswith("/")
                    or ".." in parts
                    or "\\" in name
                    or "\x00" in name
                ):
                    raise UpdateError(f"the bundle holds an unsafe path: {name!r}")
                if parts and parts[0] in (".", ""):
                    parts = parts[1:]
                if not parts:
                    continue
                target = dest.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isreg():
                    raise UpdateError(
                        f"the bundle holds something that is not a plain file or folder: {name!r}"
                    )
                files += 1
                total += member.size
                if files > MAX_FILES or total > MAX_UNPACKED_BYTES:
                    raise UpdateError("the bundle is too large to unpack")
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(member)
                with open(target, "wb") as out:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
    except (tarfile.TarError, EOFError, OSError) as e:
        _remove(dest)
        raise UpdateError(f"the bundle could not be unpacked: {e}")
    except UpdateError:
        _remove(dest)
        raise
    return files


def _remove(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)
