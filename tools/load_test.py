"""How many people can browse at once? Starts the real server (one worker, as installed) on a
library you give it, with sign-in replaced by a stand-in, then runs N simulated listeners at once.
Each loops: search, artist list, an artist's albums, an album's tracks, an artist's songs, with a
short pause like a person.

    python tools/load_test.py --data DIR [--users 50,200] [--seconds 30]     (SQLite)
    python tools/load_test.py --pg '{"host":...,"port":...,"dbname":...,"user":...,"password":...}' [--users ...]     (PostgreSQL)

DIR is a data folder that holds totr.db. Make a test library with:  python tools/bench_library.py
5000 /tmp/lib5k   (then --data /tmp/lib5k). Prints, per user count: requests per second, the
50th/95th/99th percentile and the slowest time in milliseconds, and the number of errors."""

import argparse
import http.client
import json
import os
import pathlib
import random
import subprocess
import sys
import tempfile
import threading
import time

PF = pathlib.Path(__file__).resolve().parent.parent
SERVER = r"""
import os, sys, uvicorn
sys.path.insert(0, %r); os.chdir(%r)
import main
from auth import service, permissions
user = {"user_id": 1, "username": "load", "role": "member", "permissions": permissions.effective("member", ""), "csrf": "t", "token_hash": "h"}
service.configured = lambda: True; service.validate_session = lambda t: user
uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="error")
"""


def run(port, users, seconds, artist_ids, album_ids):
    times, errors, stop = [], [0], time.time() + seconds
    lock = threading.Lock()
    words = "dream night echo neon river glass static velvet orbit".split()

    def listener(seed):
        rnd = random.Random(seed)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        while time.time() < stop:
            path = rnd.choice(
                [
                    f"/api/search?q={rnd.choice(words)}",
                    "/api/artists",
                    f"/api/artists/{rnd.choice(artist_ids)}/albums",
                    f"/api/albums/{rnd.choice(album_ids)}/tracks",
                    f"/api/artists/{rnd.choice(artist_ids)}/songs",
                    f"/api/albums/{rnd.choice(album_ids)}",
                ]
            )
            t = time.time()
            try:
                conn.request("GET", path, headers={"Cookie": "totr_session=x"})
                r = conn.getresponse()
                r.read()
                ok = r.status == 200
            except Exception:
                ok = False
                conn.close()
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            with lock:
                times.append((time.time() - t) * 1000)
                errors[0] += not ok
            time.sleep(rnd.uniform(0.2, 1.0))

    threads = [threading.Thread(target=listener, args=(i,)) for i in range(users)]
    start = time.time()
    [t.start() for t in threads]
    [t.join() for t in threads]
    took = time.time() - start
    times.sort()
    q = lambda p: times[min(len(times) - 1, int(len(times) * p))]
    print(
        f"{users:5d} users  {len(times) / took:7.1f} req/s   p50 {q(0.5):7.0f}  p95 {q(0.95):7.0f}  p99 {q(0.99):7.0f}  max {times[-1]:7.0f} ms   errors {errors[0]}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="folder with totr.db (SQLite)")
    ap.add_argument(
        "--pg",
        help="instead of --data: PostgreSQL as JSON {host,port,dbname,user,password} (library made by tools/bench_library.py with BENCH_PG)",
    )
    ap.add_argument("--users", default="50,200")
    ap.add_argument("--seconds", type=int, default=30)
    a = ap.parse_args()
    if bool(a.data) == bool(a.pg):
        ap.error("give exactly one of --data and --pg")
    port = 18999
    if a.pg:
        pg = json.loads(a.pg)
        a.data = tempfile.mkdtemp(prefix="totr-load-")
        env_db = {
            "DB_ENGINE": "postgres",
            "DB_HOST": pg["host"],
            "DB_PORT": str(pg["port"]),
            "DB_NAME": pg["dbname"],
            "DB_SSLMODE": "disable",
            "DB_SINGLE_ROLE": "true",
            **{
                k: pg[v]
                for k in (
                    "DB_USER",
                    "DB_WRITE_USER",
                    "DB_MAINT_USER",
                    "DB_AUTH_USER",
                    "DB_MIXTAPE_USER",
                )
                for v in ("user",)
            },
            **{
                k: pg["password"]
                for k in (
                    "DB_PASSWORD",
                    "DB_WRITE_PASSWORD",
                    "DB_MAINT_PASSWORD",
                    "DB_AUTH_PASSWORD",
                    "DB_MIXTAPE_PASSWORD",
                )
            },
        }
    else:
        env_db = {"DB_ENGINE": "sqlite", "DB_AUTH_USER": "x", "DB_AUTH_PASSWORD": "y"}
    env = {
        **os.environ,
        **env_db,
        "TOTR_DATA_DIR": a.data,
        "MAINT_REPORT": a.data + "/r/m.json",
        "COLLECTOR_DIR": a.data + "/r",
        "APP_SETTINGS": a.data + "/r/a.json",
    }
    server = subprocess.Popen(
        [sys.executable, "-c", SERVER % (str(PF), str(PF)), str(port)], env=env
    )
    try:
        for _ in range(100):
            try:
                http.client.HTTPConnection("127.0.0.1", port, timeout=2).request("GET", "/health")
                break
            except OSError:
                time.sleep(0.2)
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
        get = lambda p: (
            c.request("GET", p, headers={"Cookie": "totr_session=x"}),
            json.loads(c.getresponse().read()),
        )[1]
        get("/api/search?q=warm")  # the first search builds the index
        artists = get("/api/artists")
        artist_ids = [x["artist_id"] for x in artists][:200]
        album_ids = [
            al["album_id"] for i in artist_ids[:30] for al in get(f"/api/artists/{i}/albums")
        ] or [1]
        print(f"library: {len(artists)} artists; server pid {server.pid}; {a.seconds} s per run")
        for n in (int(x) for x in a.users.split(",")):
            run(port, n, a.seconds, artist_ids, album_ids)
    finally:
        server.terminate()


if __name__ == "__main__":
    main()
