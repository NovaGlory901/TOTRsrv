"""
Downloads take a while, so approved adds run one at a time in a background worker and the page
watches their progress. One at a time keeps the load on the source sites (and your drive) gentle.
"""

import itertools
import logging
import queue
import threading
from collections import deque

from .acquire import AcquireError

log = logging.getLogger("totr.collector")
_ids = itertools.count(1)
_queue: queue.Queue = queue.Queue()
_jobs: deque = deque(maxlen=25)
_lock = threading.Lock()
_worker = None


class Job:
    def __init__(self, title: str, fn, total_bytes: int, key: str | None):
        self.id = next(_ids)
        self.title, self.fn, self.total, self.key = title, fn, total_bytes, key
        self.done, self.status, self.message = 0, "queued", ""

    def progress(self, n: int):
        self.done += n

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "key": self.key,
            "title": self.title,
            "status": self.status,
            "message": self.message,
            "done": self.done,
            "total": self.total,
        }


def _run():
    while True:
        job = _queue.get()
        job.status = "running"
        try:
            job.message = job.fn(job.progress)
            job.status = "done"
        except AcquireError as e:
            job.status, job.message = "failed", str(e)
        except Exception:
            log.exception("collector job failed: %s", job.title)
            job.status, job.message = (
                "failed",
                "That did not work and nothing was added. See the server log.",
            )
        finally:
            _queue.task_done()


def submit(title: str, fn, total_bytes: int = 0, key: str | None = None) -> dict:
    global _worker
    job = Job(title, fn, total_bytes, key)
    with _lock:
        _jobs.appendleft(job)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="collector-worker", daemon=True)
            _worker.start()
    _queue.put(job)
    return job.to_dict()


def listing() -> list:
    return [j.to_dict() for j in _jobs]


def busy() -> bool:
    return any(j.status in ("queued", "running") for j in _jobs)


def active(key: str) -> bool:
    """True if a job for this suggestion is already waiting or running, so it is never queued twice."""
    return any(j.key == key and j.status in ("queued", "running") for j in _jobs)
