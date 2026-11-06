"""Runs every spider, the online `enrich` one included, on a schedule (default: nightly, at 3 am).

Same pattern as the collector's scheduler. Scanning only reads; fixes still wait for an admin's
approval.
"""

import json
import logging
import os
import threading
from datetime import UTC, datetime, timedelta

import moving

from . import REPORT_PATH, store
from . import run as spider_run

log = logging.getLogger("totr.spiders")
CHECK_EVERY_SECONDS = 600
CONFIG_PATH = REPORT_PATH.with_name("spider_schedule.json")
DEFAULT_CONFIG = {"auto_scan": True, "interval_days": 1, "run_hour": 3}


def clean_config(raw: dict) -> dict:
    """Validates settings from the browser. Raises ValueError with a message meant for the user."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["auto_scan"] = bool(raw.get("auto_scan", True))
    for key, lo, hi in (("interval_days", 1, 90), ("run_hour", 0, 23)):
        try:
            value = int(raw.get(key, cfg[key]))
        except (TypeError, ValueError):
            raise ValueError(f"{key.replace('_', ' ')} must be a number.")
        cfg[key] = min(max(value, lo), hi)
    return cfg


def load_config() -> dict:
    try:
        return clean_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, AttributeError):
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def last_full_run(report) -> str | None:
    """The oldest time any spider last ran. A spider that never ran (or has no time) makes a scan due."""
    ran_at = (report or {}).get("ran_at") or {}
    times = [ran_at.get(name) for name in spider_run.SPIDERS]
    return None if not all(times) else min(times)


def due(cfg: dict, last_iso, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    if not cfg["auto_scan"]:
        return False
    if last_iso:
        try:
            last = datetime.fromisoformat(last_iso)
        except ValueError:
            last = None
        # An hour of slack: the check only fires inside the chosen hour, so a run at 3:05 must not
        # push tomorrow's to 4 am.
        if last and now - last < timedelta(days=cfg["interval_days"]) - timedelta(hours=1):
            return False
    return now.astimezone().hour == cfg["run_hour"]


def loop(stop: threading.Event):
    while not stop.wait(CHECK_EVERY_SECONDS):
        try:
            if (
                due(load_config(), last_full_run(store.read_report()))
                and not spider_run.is_running()
                and not moving.active()
            ):
                log.info("scheduled spider run starting")
                spider_run.run(set(spider_run.SPIDERS))
        except Exception:
            log.exception("scheduled spider run failed")


def start() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=loop, args=(stop,), name="spider-scheduler", daemon=True).start()
    return stop
