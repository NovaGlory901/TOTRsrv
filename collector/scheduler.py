"""Runs the search on the schedule from the settings (default: weekly, at 3 am). Searching only,
never downloading."""

import logging
import threading
from datetime import UTC, datetime, timedelta

import moving

from . import finder, state

log = logging.getLogger("totr.collector")
CHECK_EVERY_SECONDS = 600


def due(cfg: dict, last_search_iso, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    if not cfg["auto_search"] or not cfg["genres"]:
        return False
    if last_search_iso:
        try:
            last = datetime.fromisoformat(last_search_iso)
        except ValueError:
            last = None
        if last and now - last < timedelta(days=cfg["interval_days"]):
            return False
    return now.astimezone().hour == cfg["run_hour"]


def loop(stop: threading.Event):
    while not stop.wait(CHECK_EVERY_SECONDS):
        try:
            last = (state.read_suggestions().get("last_search") or {}).get("at")
            if due(state.load_config(), last) and not finder.is_running() and not moving.active():
                log.info("scheduled music search starting")
                finder.search_all()
        except Exception:
            log.exception("scheduled music search failed")


def start() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=loop, args=(stop,), name="collector-scheduler", daemon=True).start()
    return stop
