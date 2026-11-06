"""Accounts, sessions and access control. See sql/auth_schema.sql for the tables.

Nothing here stores a plain-text secret: passwords are Argon2id hashes, and session and invite
tokens are kept only as SHA-256 hashes.
"""

import os

COOKIE_NAME = "totr_session"
SESSION_ABSOLUTE_DAYS = 30  # a login never lasts longer than this
SESSION_IDLE_DAYS = 7  # ... or this long without being used
TOUCH_EVERY_SECONDS = 300  # how often "last seen" is written back
INVITE_DAYS = 7
MIN_PASSWORD = 12
MAX_PASSWORD = 128  # argon2 is deliberately slow; don't let huge inputs be a DoS

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "2048"))
MAX_BODY_BYTES = 64 * 1024  # for every non-upload request


def env_flag(name: str, default: str = "auto") -> str:
    return os.environ.get(name, default).strip().lower()
