"""Updates from the developer: find a newer signed release on GitHub, download and verify it, and
hand it to the update service to install.

    signing.py   the release format, the signature check, safe unpacking (no network, no database)
    fetch.py     asks GitHub which release is newest, downloads and verifies one, and files the request (all outside traffic goes through collector/http.py)
    web.py       the admin-only API (/api/admin/updates*)
    deploy/apply-update.py (not part of the web app) installs a verified release: it is run by systemd as root, so the web app never rewrites its own code.

Settings (in .env): UPDATE_REPO=owner/name (the GitHub repository whose releases carry the server
bundles); the developer's public key is update_pubkey.txt.
"""

import os
import pathlib
import re

import paths

CODE_DIR = paths.CODE_DIR
# where a release may be fetched from (redirects are checked against the same list)
SUFFIXES = {
    "github.com",
    "githubusercontent.com",
}
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9._-]{1,100}$")


def pubkey_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("UPDATE_PUBKEY_FILE") or CODE_DIR / "update_pubkey.txt")


def repo() -> str:
    """owner/name of the GitHub repository, or '' when not set (or not a plausible name)."""
    value = (os.environ.get("UPDATE_REPO") or "").strip()
    return value if REPO_RE.match(value) else ""


def updates_dir() -> pathlib.Path:
    """Where downloads wait and where the update service reports (inside the state folder, so it
    survives the code being swapped)."""
    return paths.home() / "updates"
