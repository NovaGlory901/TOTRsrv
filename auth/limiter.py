import threading
import time
from collections import defaultdict, deque


class Limiter:
    """Sliding-window failure counter kept in memory. Resets on restart, which is acceptable
    because an attacker cannot trigger a restart."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window = limit, window_seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key, now):
        q = self._hits[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        if not q:
            self._hits.pop(key, None)
        return q

    def retry_after(self, key, now=None) -> int:
        """0 if the key may try, otherwise seconds until it may."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._trim(key, now)
            if len(q) < self.limit:
                return 0
            return max(1, int(q[0] + self.window - now) + 1)

    def record(self, key, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            self._trim(key, now)
            self._hits[key].append(now)

    def clear(self, key):
        with self._lock:
            self._hits.pop(key, None)


class LoginLimits:
    """Three overlapping limits, so one attacker cannot lock a real user out of their own account
    from another address, yet a distributed guessing run still slows down:
      - per address                (20 failures / 15 min)
      - per address + username     ( 5 failures / 15 min)
      - per username, any address  (40 failures / 15 min)
    """

    def __init__(self):
        window = 15 * 60
        self.by_ip = Limiter(20, window)
        self.by_pair = Limiter(5, window)
        self.by_user = Limiter(40, window)

    @staticmethod
    def keys(ip: str, username: str):
        u = username.casefold()
        return ip, f"{ip}|{u}", u

    def retry_after(self, ip: str, username: str) -> int:
        ip_k, pair_k, user_k = self.keys(ip, username)
        return max(
            self.by_ip.retry_after(ip_k),
            self.by_pair.retry_after(pair_k),
            self.by_user.retry_after(user_k),
        )

    def failure(self, ip: str, username: str):
        ip_k, pair_k, user_k = self.keys(ip, username)
        self.by_ip.record(ip_k)
        self.by_pair.record(pair_k)
        self.by_user.record(user_k)

    def success(self, ip: str, username: str):
        _, pair_k, _ = self.keys(ip, username)
        self.by_pair.clear(pair_k)
