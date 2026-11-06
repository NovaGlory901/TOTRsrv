"""How much music and pictures this server sends out: the rate right now, and totals per day and per
person.

Counted where the bytes leave the program (a thin ASGI wrapper, TrafficMiddleware), so Range
requests, resumed downloads and seeking are all counted for what was really sent. Daily totals are
kept in a small JSON file so a restart does not lose them. Only two kinds of response count: audio
streams and media files (album covers, the Dream folder).
"""

import json
import os
import pathlib
import re
import threading
import time
from collections import deque
from datetime import datetime

KEEP_DAYS = 90
MAX_USERS_PER_DAY = 200
FLUSH_EVERY = 30  # seconds between writes of the totals file
AUDIO = re.compile(r"^/api/tracks/\d+/audio$")
MEDIA = re.compile(r"^/api/(albums/\d+/cover|radio/media/file/.+)$")


def classify(path: str):
    if AUDIO.match(path):
        return "audio"
    if MEDIA.match(path):
        return "media"
    return None


class Traffic:
    def __init__(self, path=None, clock=time.time):
        self.path = pathlib.Path(path) if path else None
        self.clock = clock
        self.lock = threading.Lock()
        self.days = {}  # "2026-10-03" -> {"audio": n, "media": n, "users": {name: n}}
        self.seconds = deque()  # (whole second, bytes) for the last minute: the live rate
        self.dirty, self.last_flush = False, clock()
        self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path else {}
        except (OSError, ValueError):
            data = {}
        for day, entry in (data.get("days") or {}).items() if isinstance(data, dict) else []:
            try:
                self.days[str(day)] = {
                    "audio": int(entry.get("audio", 0)),
                    "media": int(entry.get("media", 0)),
                    "users": {str(k): int(v) for k, v in (entry.get("users") or {}).items()},
                }
            except (AttributeError, TypeError, ValueError):
                continue

    def flush(self):
        if not self.path:
            return
        with self.lock:
            self._prune()
            payload = json.dumps({"days": self.days})
            self.dirty, self.last_flush = False, self.clock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # a full disk must never break streaming

    def _prune(self):
        for day in sorted(self.days)[:-KEEP_DAYS]:
            del self.days[day]

    def _day(self, now):
        return datetime.fromtimestamp(now).strftime("%Y-%m-%d")

    def record(self, kind: str, username: str, nbytes: int):
        if kind not in ("audio", "media") or nbytes <= 0:
            return
        now = self.clock()
        with self.lock:
            entry = self.days.setdefault(self._day(now), {"audio": 0, "media": 0, "users": {}})
            entry[kind] += nbytes
            users, name = entry["users"], (username or "unknown")[:64]
            if name in users or len(users) < MAX_USERS_PER_DAY:
                users[name] = users.get(name, 0) + nbytes
            sec = int(now)
            if self.seconds and self.seconds[-1][0] == sec:
                self.seconds[-1] = (sec, self.seconds[-1][1] + nbytes)
            else:
                self.seconds.append((sec, nbytes))
            while self.seconds and self.seconds[0][0] < sec - 60:
                self.seconds.popleft()
            self.dirty = True
            due = now - self.last_flush >= FLUSH_EVERY
        if due:
            self.flush()

    def bytes_per_second(self, window: int = 5) -> float:
        """Average over the last `window` whole seconds (the current, unfinished second is left out)."""
        now = int(self.clock())
        with self.lock:
            total = sum(b for s, b in self.seconds if now - window <= s < now)
        return total / window

    def snapshot(self, days: int = 14) -> dict:
        now = self.clock()
        today = self._day(now)
        with self.lock:
            entry = self.days.get(today, {"audio": 0, "media": 0, "users": {}})
            recent = [
                {"date": d, "audio": e["audio"], "media": e["media"]}
                for d, e in sorted(self.days.items())[-days:]
            ]
            users = sorted(
                ({"user": u, "bytes": b} for u, b in entry["users"].items()),
                key=lambda r: -r["bytes"],
            )
        return {
            "bps": round(self.bytes_per_second(), 1),
            "today": {
                "audio": entry["audio"],
                "media": entry["media"],
                "total": entry["audio"] + entry["media"],
            },
            "users_today": users,
            "days": recent,
        }


class TrafficMiddleware:
    """Counts the bytes of every audio and media response, and who they went to. Pure ASGI, so it
    sees the real stream."""

    def __init__(self, app, traffic):
        self.app, self.traffic = app, traffic

    async def __call__(self, scope, receive, send):
        kind = (
            classify(scope.get("path", ""))
            if scope.get("type") == "http" and scope.get("method") != "HEAD"
            else None
        )
        if kind is None:
            return await self.app(scope, receive, send)
        sent = {"status": 0}

        async def counting(message):
            if message["type"] == "http.response.start":
                sent["status"] = message["status"]
            elif sent["status"] in (200, 206):
                n = 0
                if message["type"] == "http.response.body":
                    n = len(message.get("body") or b"")
                # the server sends the file itself
                elif message["type"] == "http.response.pathsend":
                    try:
                        n = os.path.getsize(message["path"])
                    except OSError:
                        n = 0
                if n:
                    user = (scope.get("state") or {}).get("user") or {}
                    self.traffic.record(kind, user.get("username", "unknown"), n)
            await send(message)

        await self.app(scope, receive, counting)
