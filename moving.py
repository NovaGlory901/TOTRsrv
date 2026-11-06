"""A flag the move to PostgreSQL (dbmove.py) raises while it copies the data. While it is up the
gate refuses every request that would change something (uploads, sign-ins, playlist edits, listening
pings) and the collector and spiders do not start, so what is copied is exactly what is there at the
end."""

import threading

_on = threading.Event()


def active() -> bool:
    return _on.is_set()


def set_active(value: bool) -> None:
    (_on.set if value else _on.clear)()
