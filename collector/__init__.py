"""
Music collector: finds music that is legally free to download in the genres you choose, and
suggests it. Nothing is downloaded until an admin approves a suggestion on the Admin page.

Legal rules this package enforces in code:
  * Only releases whose own data carries a Creative Commons or public-domain license URL are
    suggested (see licenses.py). No license data means no suggestion.
  * Only documented, public APIs and download links on an allow-list of hosts (see http.py).
  * Every added release records where it came from and under which license.
"""

import os
import pathlib

import dotenv

import paths

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
dotenv.load_dotenv(paths.env_file())

# COLLECTOR_DIR is the current name; DISCOVER_DIR is accepted from older installs.
DATA_DIR = pathlib.Path(
    os.environ.get("COLLECTOR_DIR") or os.environ.get("DISCOVER_DIR") or paths.home() / "reports"
)


def user_agent() -> str:
    """Built per request: the contact comes from the Account page (or .env) and can change while the
    app runs."""
    import app_settings

    contact = app_settings.contact()
    return (
        "TOTR-collector/1.0 (personal self-hosted music library"
        + (f"; contact: {contact}" if contact else "")
        + ")"
    )
