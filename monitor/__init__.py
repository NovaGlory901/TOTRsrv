"""Live status for the Admin page's Status tab: listeners, data streamed, system health, library
size, background jobs and recent problems. Everything is in memory or read-only; the page asks for
it every few seconds while the tab is visible.

    system.py     CPU, memory, disks, temperatures, uptime
    traffic.py    bytes sent per second / per day / per person (an ASGI wrapper counts them)
    listeners.py  who is playing what, from where (fed by a heartbeat from the pages)
    errors.py     the last warnings and errors the server logged
"""

import os
import threading
import time
from datetime import UTC, datetime

from spiders import REPORT_PATH

from . import errors, system
from . import listeners as listeners_mod
from . import traffic as traffic_mod

# bytes sent (the instance; the module is monitor.traffic)
meter = traffic_mod.Traffic(REPORT_PATH.with_name("traffic.json"))
audience = listeners_mod.Listeners()  # who is listening now
# seconds; adding up every file's size is the one slow thing here, so it runs in the background
LIBRARY_SIZE_EVERY = 600

_size = {"bytes": None, "files": None, "at": None, "running": False}
_size_lock = threading.Lock()


def _measure_library(get_paths):
    try:
        total, files = 0, 0
        for path in get_paths():
            try:
                total += os.path.getsize(path)
                files += 1
            except OSError:
                pass
        with _size_lock:
            _size.update(bytes=total, files=files, at=time.time())
    finally:
        with _size_lock:
            _size["running"] = False


def library_size(get_paths) -> dict:
    """The size of the music on disk, from the last background measurement (started now if it is
    stale)."""
    with _size_lock:
        stale = _size["at"] is None or time.time() - _size["at"] > LIBRARY_SIZE_EVERY
        start = stale and not _size["running"]
        if start:
            _size["running"] = True
        out = {"bytes": _size["bytes"], "files": _size["files"], "measuring": _size["running"]}
    if start:
        threading.Thread(
            target=_measure_library, args=(get_paths,), name="library-size", daemon=True
        ).start()
    return out


def live_snapshot(resolve_tracks, places: dict, library_counts, library_paths) -> dict:
    """One answer for the Status tab. `resolve_tracks(ids)` names the songs being played;
    `library_counts()` and `library_paths()` read the library. Each part is isolated: if one fails
    the others still show."""

    def safe(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    rows = audience.active()
    ids = [r["track_id"] for r in rows if r["track_id"]]
    names = safe(lambda: resolve_tracks(ids), {}) if ids else {}
    for r in rows:
        t = names.get(r["track_id"])
        r["track"] = (
            {"track_id": t["track_id"], "title": t["title"], "artists": t.get("artists") or ""}
            if t
            else None
        )

    from collector import finder, jobs
    from collector import state as collector_state
    from spiders import run as spider_run
    from spiders import store as spider_store

    def spiders_part():
        report = spider_store.read_report() or {}
        return {
            "running": spider_run.is_running(),
            "progress": spider_run.progress["text"],
            "last_scan": report.get("generated_at"),
            "findings": len(report.get("findings", [])),
            "ran_at": report.get("ran_at", {}),
        }

    def collector_part():
        data = collector_state.read_suggestions()
        return {
            "searching": finder.is_running(),
            "progress": finder.progress["text"],
            "last_search": (data.get("last_search") or {}).get("at"),
            "suggestions": len(data["suggestions"]),
            "downloads_active": sum(
                1 for j in jobs.listing() if j["status"] in ("queued", "running")
            ),
            "added": len([a for a in collector_state.added() if not a.get("undone_at")]),
        }

    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "listeners": rows,
        "traffic": meter.snapshot(),
        "system": system.snapshot(places),
        "library": {"counts": safe(library_counts), "size": library_size(library_paths)},
        "spiders": safe(spiders_part),
        "collector": safe(collector_part),
        "problems": errors.recent_errors.recent(20),
    }
