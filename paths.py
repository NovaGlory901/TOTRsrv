"""Where TOTR keeps what is not code: the settings file (.env), the built-in database, reports,
certificates and the app installers.

Run from a checkout (what has always been done), these sit next to the code and nothing changes. An
installed server (deploy/install.sh) keeps the code in releases/<version>/ and swaps a link to the
new one on update, so everything that must survive an update lives in one state folder instead. The
service points at it with TOTR_STATE_DIR. Individual settings (TOTR_ENV_FILE, TOTR_DATA_DIR,
MAINT_REPORT, COLLECTOR_DIR, APP_SETTINGS, TOTR_DOWNLOADS_DIR) still win.
"""

import os
import pathlib

CODE_DIR = pathlib.Path(__file__).resolve().parent


def state_dir() -> pathlib.Path | None:
    value = (os.environ.get("TOTR_STATE_DIR") or "").strip()
    return pathlib.Path(value) if value else None


def home() -> pathlib.Path:
    """The folder that holds .env, data/, reports/ and certs/: the state folder of an installed
    server, otherwise the code folder."""
    return state_dir() or CODE_DIR


def env_file() -> pathlib.Path:
    return pathlib.Path(os.environ.get("TOTR_ENV_FILE") or home() / ".env")


def downloads_dir() -> pathlib.Path:
    """Where the app installers are served from (/static/downloads/). Installed server:
    state/downloads; a checkout: static/downloads, as always."""
    if os.environ.get("TOTR_DOWNLOADS_DIR"):
        return pathlib.Path(os.environ["TOTR_DOWNLOADS_DIR"])
    state = state_dir()
    return state / "downloads" if state else CODE_DIR / "static" / "downloads"
