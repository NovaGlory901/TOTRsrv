"""Who is listening right now. The pages send a small "still playing" message about every 30 seconds
while audio plays (and when it pauses or stops); a listener who has not been heard from for 75
seconds is gone. Kept in memory only: it is a live view, not a history. One entry per signed-in
device, so the same person on a phone and a laptop shows twice."""

import ipaddress
import threading
import time

TTL = 75  # seconds without a message before a listener is dropped
MAX_ENTRIES = 500  # bounds memory if a signed-in client floods the endpoint
STATES = ("playing", "paused", "stopped")


def device_name(user_agent: str) -> str:
    """ "Edge on Windows" from a browser's user-agent text. Plain matching; unknown things say so."""
    ua = user_agent or ""
    browser = next(
        (
            name
            for pat, name in (
                ("Edg/", "Edge"),
                ("OPR/", "Opera"),
                ("Firefox/", "Firefox"),
                ("Chrome/", "Chrome"),
                ("Safari/", "Safari"),
            )
            if pat in ua
        ),
        "a browser",
    )
    system = next(
        (
            name
            for pat, name in (
                ("Windows", "Windows"),
                ("Android", "Android"),
                ("iPhone", "iPhone"),
                ("iPad", "iPad"),
                ("Mac OS X", "Mac"),
                ("CrOS", "ChromeOS"),
                ("Linux", "Linux"),
            )
            if pat in ua
        ),
        None,
    )
    return f"{browser} on {system}" if system else browser


def where(ip: str) -> str:
    """The address, and whether it is on the home network. No outside lookups: nothing about a
    listener leaves this machine."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown address"
    kind = "this server" if addr.is_loopback else "home network" if addr.is_private else "internet"
    return f"{ip} ({kind})"


class Listeners:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.items = {}  # device key -> entry

    def ping(
        self, key: str, username: str, track_id, state: str, source: str, ip: str, user_agent: str
    ):
        if state not in STATES:
            return
        now = self.clock()
        with self.lock:
            if state == "stopped":
                self.items.pop(key, None)
                return
            old = self.items.get(key)
            # a new song restarts the clock
            since = old["since"] if old and old["track_id"] == track_id else now
            self.items[key] = {
                "user": username,
                "track_id": track_id,
                "state": state,
                "source": source,
                "device": device_name(user_agent),
                "where": where(ip),
                "since": since,
                "last": now,
            }
            if len(self.items) > MAX_ENTRIES:
                for stale in sorted(self.items, key=lambda k: self.items[k]["last"])[
                    : len(self.items) - MAX_ENTRIES
                ]:
                    del self.items[stale]

    def active(self) -> list:
        now = self.clock()
        with self.lock:
            for key in [k for k, v in self.items.items() if now - v["last"] > TTL]:
                del self.items[key]
            rows = [
                {**v, "listening_s": int(now - v["since"]), "last_s": int(now - v["last"])}
                for v in self.items.values()
            ]
        return sorted(rows, key=lambda r: (r["user"].casefold(), r["since"]))
