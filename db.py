import os
import pathlib

import dotenv
import psycopg
from psycopg.rows import dict_row

import localdb
import paths

BASE_DIR = pathlib.Path(__file__).resolve().parent
dotenv.load_dotenv(paths.env_file())

# Role variables, by purpose. With DB_SINGLE_ROLE=true every purpose uses the main DB_USER /
# DB_PASSWORD (the simple setup).
ROLES = {
    "read": ("DB_USER", "DB_PASSWORD"),
    "write": ("DB_WRITE_USER", "DB_WRITE_PASSWORD"),
    "maint": ("DB_MAINT_USER", "DB_MAINT_PASSWORD"),
    "auth": ("DB_AUTH_USER", "DB_AUTH_PASSWORD"),
    "mixtape": ("DB_MIXTAPE_USER", "DB_MIXTAPE_PASSWORD"),
}


def engine() -> str:
    """'postgres' when the .env names a PostgreSQL server (DB_HOST, or DB_ENGINE=postgres),
    otherwise the built-in local database."""
    chosen = os.environ.get("DB_ENGINE", "").strip().lower()
    if chosen in ("sqlite", "postgres"):
        return chosen
    return "postgres" if os.environ.get("DB_HOST") else "sqlite"


def single_role() -> bool:
    return os.environ.get("DB_SINGLE_ROLE", "").strip().lower() in ("1", "true", "yes")


def credentials(purpose: str) -> tuple[str, str] | None:
    """(user, password) for a purpose, or None when that feature is not configured."""
    user_var, password_var = ROLES["read" if single_role() else purpose]
    user, password = os.environ.get(user_var), os.environ.get(password_var)
    return (user, password) if user and password else None


def connect_settings(user: str, password: str) -> dict:
    """psycopg connection arguments from the .env: DB_HOST (or a socket path starting with /),
    DB_PORT, DB_NAME, DB_SSLMODE."""
    host = os.environ["DB_HOST"]
    settings = {"dbname": os.environ["DB_NAME"], "user": user, "password": password, "host": host}
    if os.environ.get("DB_PORT"):
        settings["port"] = int(os.environ["DB_PORT"])
    settings["sslmode"] = os.environ.get("DB_SSLMODE") or (
        "disable" if host.startswith("/") else "require"
    )
    return settings


def _connect(purpose: str):
    if engine() == "sqlite":
        return localdb.connect()
    creds = credentials(purpose)
    if creds is None:
        raise RuntimeError(f"The {purpose} database role is not configured in .env")
    return psycopg.connect(**connect_settings(*creds), row_factory=dict_row)


def _enabled(purpose: str) -> bool:
    return engine() == "sqlite" or credentials(purpose) is not None


def get_conn():
    """Read-only role used by the website and the spiders' scans."""
    return _connect("read")


def write_enabled() -> bool:
    return _enabled("write")


def get_write_conn():
    """Upload role: can add artists, albums and tracks, nothing else (sql/upload_role.sql)."""
    return _connect("write")


def maint_enabled() -> bool:
    return _enabled("maint")


def get_maint_conn():
    """Maintenance role: can fix and remove rows once the user approves a spider's proposal
    (sql/maintenance_role.sql). Only spiders/actions.py uses it."""
    return _connect("maint")


def auth_enabled() -> bool:
    return _enabled("auth")


def get_auth_conn():
    """Accounts role: can touch only the users, sessions and invites tables (sql/auth_schema.sql).
    The read-only role used for music queries has no access to those tables."""
    return _connect("auth")


def mixtape_enabled() -> bool:
    return _enabled("mixtape")


def get_mixtape_conn():
    """Mixtape role: can touch only the mixtapes and mixtape_tracks tables (sql/mixtapes.sql)."""
    return _connect("mixtape")
