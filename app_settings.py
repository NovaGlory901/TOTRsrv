"""Settings the admin edits on the website instead of in .env. None of these are secrets.

Stored in one JSON file next to the other state files. Real secrets (database passwords, API keys)
stay in .env. Each setting is read when it is needed, so a change on the Account page applies
without a restart.
"""

import json
import os
import pathlib
import re
import threading
import urllib.parse

import paths

BASE_DIR = pathlib.Path(__file__).resolve().parent
SETTINGS_PATH = pathlib.Path(
    os.environ.get("APP_SETTINGS", paths.home() / "reports" / "app_settings.json")
)
LOCK = threading.RLock()

EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}$")
URL_SAFE = re.compile(r"^[^\s;()<>\"'\\]+$")
CONTACT_HELP = (
    "Enter an email address like name@example.com, or a web address starting with https://."
)


def clean_contact(raw) -> str:
    """The contact goes into the User-Agent header of every outbound request, so it must be plain
    and header-safe. Returns "" for empty input. Raises ValueError with a message meant for the
    user."""
    value = str(raw or "").strip()
    if not value:
        return ""
    if len(value) > 120 or not value.isascii():
        raise ValueError(CONTACT_HELP)
    if EMAIL.match(value):
        return value
    if (
        value.startswith("https://")
        and URL_SAFE.match(value)
        and urllib.parse.urlparse(value).hostname
    ):
        return value
    raise ValueError(CONTACT_HELP)


def _read() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_contact() -> tuple[str, str]:
    """(contact, where it came from): the website setting first, then COLLECTOR_CONTACT in .env,
    else ("", "none")."""
    for source, raw in (
        ("settings", _read().get("contact")),
        ("env", os.environ.get("COLLECTOR_CONTACT")),
    ):
        try:
            value = clean_contact(raw)
        except ValueError:
            continue  # a hand-edited value that would break the header is ignored rather than sent
        if value:
            return value, source
    return "", "none"


def contact() -> str:
    return get_contact()[0]


def set_contact(raw) -> str:
    """Saves the contact ("" clears it, so .env applies again). Returns the cleaned value."""
    value = clean_contact(raw)
    with LOCK:
        data = _read()
        data["contact"] = value
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, SETTINGS_PATH)
    return value


def get_join_open() -> bool:
    """Whether this server answers "can I join?" from apps. Off until an admin turns it on."""
    return _read().get("join_open") is True


def set_join_open(on: bool) -> bool:
    with LOCK:
        data = _read()
        data["join_open"] = bool(on)
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, SETTINGS_PATH)
    return bool(on)
