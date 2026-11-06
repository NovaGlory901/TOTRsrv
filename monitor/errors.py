"""The last few warnings and errors the server logged, for the Status tab ("recent problems").

A logging handler keeps the most recent 50 in memory. Messages are shortened, and anything that
looks like a password or a connection string is blanked, so the list is safe to show an admin. It is
not saved anywhere."""

import logging
import re
import threading
import time
from collections import deque

MAX_ITEMS = 50
MAX_LENGTH = 300
_SECRET = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|client[_-]?id)\s*[=:]\s*\S+"
)
_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://\S+")


def clean(text: str) -> str:
    text = _URL.sub("[link removed]", _SECRET.sub(lambda m: m.group(1) + "=[hidden]", text))
    return re.sub(r"\s+", " ", text).strip()[:MAX_LENGTH]


class RecentErrors(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.items = deque(maxlen=MAX_ITEMS)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord):
        try:
            message = clean(record.getMessage())
            if record.exc_info and record.exc_info[0]:
                message += f" ({record.exc_info[0].__name__})"
            with self._lock:
                self.items.appendleft(
                    {
                        "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created)),
                        "level": record.levelname,
                        "logger": record.name[:40],
                        "message": message,
                    }
                )
        except Exception:
            pass  # logging must never raise

    def recent(self, limit: int = 20) -> list:
        with self._lock:
            return list(self.items)[:limit]


recent_errors = RecentErrors()


def install():
    """Start collecting. Safe to call twice."""
    root = logging.getLogger()
    if recent_errors not in root.handlers:
        root.addHandler(recent_errors)
