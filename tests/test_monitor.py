"""Status tab: system readings, data-streamed counting, who is listening, recent problems, and the
endpoints that serve them."""

import asyncio
import logging
import os
import pathlib
import re
import sys
import tempfile
import time
import wave
from datetime import datetime

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["APP_SETTINGS"] = str(tmp / "reports" / "app_settings.json")

from check import check, finish

from monitor import errors, system
from monitor import listeners as listeners_mod
from monitor import traffic as traffic_mod

# system readings
PROC_STAT = "cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 50 0 25 400 25 0 0 0 0 0\n"
check(
    "cpu: /proc/stat gives busy and total (idle and iowait count as idle)",
    system.parse_proc_stat(PROC_STAT) == (150, 1000),
    system.parse_proc_stat(PROC_STAT),
)
check(
    "cpu: junk gives None",
    system.parse_proc_stat("nonsense") is None
    and system.parse_proc_stat("") is None
    and system.parse_proc_stat("cpu  a b c") is None,
)
check(
    "cpu: percent between two readings",
    system.cpu_percent((150, 1000), (350, 1400)) == 50.0
    and system.cpu_percent((0, 100), (100, 200)) == 100.0
    and system.cpu_percent((0, 100), (0, 200)) == 0.0,
)
check(
    "cpu: nothing elapsed or missing readings is None, never a crash",
    system.cpu_percent((1, 100), (1, 100)) is None
    and system.cpu_percent(None, (1, 2)) is None
    and system.cpu_percent((5, 50), (6, 40)) is None,
)
check("cpu: always within 0-100", 0 <= system.cpu_percent((0, 0), (999, 1)) <= 100)
MEMINFO = "MemTotal:       16000000 kB\nMemFree:         1000000 kB\nMemAvailable:    4000000 kB\nBuffers: 100 kB\n"
m = system.parse_meminfo(MEMINFO)
check(
    "memory: /proc/meminfo in bytes, using MemAvailable",
    m == {"total": 16000000 * 1024, "available": 4000000 * 1024},
    m,
)
check(
    "memory: older kernels without MemAvailable are estimated, junk gives None",
    system.parse_meminfo("MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 25 kB\n")[
        "available"
    ]
    == 175 * 1024
    and system.parse_meminfo("garbage") is None
    and system.parse_meminfo("") is None,
)
check(
    "memory: available is never more than total",
    system.parse_meminfo("MemTotal: 100 kB\nMemAvailable: 900 kB\n")["available"] == 100 * 1024,
)

sysroot = tmp / "sys"
(sysroot / "thermal" / "thermal_zone0").mkdir(parents=True)
(sysroot / "thermal" / "thermal_zone0" / "type").write_text("x86_pkg_temp\n")
(sysroot / "thermal" / "thermal_zone0" / "temp").write_text("45500\n")
(sysroot / "thermal" / "thermal_zone1").mkdir()
(sysroot / "thermal" / "thermal_zone1" / "temp").write_text("not a number")
(sysroot / "thermal" / "thermal_zone2").mkdir()
(sysroot / "thermal" / "thermal_zone2" / "temp").write_text("999000")
(sysroot / "hwmon" / "hwmon0").mkdir(parents=True)
(sysroot / "hwmon" / "hwmon0" / "name").write_text("coretemp\n")
(sysroot / "hwmon" / "hwmon0" / "temp1_input").write_text("52000\n")
(sysroot / "hwmon" / "hwmon0" / "temp1_label").write_text("Package id 0\n")
(sysroot / "hwmon" / "hwmon0" / "temp2_input").write_text("-90000\n")
temps = system.read_temps(sysroot)
check(
    "temperatures: thermal zones and hardware monitors, with labels",
    {"label": "x86_pkg_temp", "celsius": 45.5} in temps
    and {"label": "coretemp Package id 0", "celsius": 52.0} in temps,
    temps,
)
check("temperatures: junk and impossible values are left out", len(temps) == 2)
check(
    "temperatures: a machine with no sensors gives an empty list",
    system.read_temps(tmp / "nowhere") == [],
)
many = tmp / "many"
for i in range(30):
    (many / "thermal" / f"thermal_zone{i}").mkdir(parents=True)
    (many / "thermal" / f"thermal_zone{i}" / "temp").write_text("40000")
check("temperatures: at most 12 are reported", len(system.read_temps(many)) == 12)

(tmp / "a").mkdir()
(tmp / "b").mkdir()
dk = system.disks(
    {"music library": tmp / "a", "app": tmp / "b", "dream folder": tmp / "does-not-exist"}
)
check(
    "disks: folders on one drive are listed once with both names",
    sum(1 for d in dk if d["ok"]) == 1 and "music library + app" in [d["label"] for d in dk],
    dk,
)
good = next(d for d in dk if d["ok"])
check(
    "disks: total, used, free and percent make sense",
    good["total"] > 0
    and good["used"] + good["free"] <= good["total"] * 1.01
    and 0 <= good["percent"] <= 100,
)
check(
    "disks: a missing folder is reported, not fatal",
    {"label": "dream folder", "path": str(tmp / "does-not-exist"), "ok": False} in dk,
)

snap = system.snapshot({"app": tmp})
check(
    "snapshot: has every field the page reads",
    {"platform", "cores", "cpu_percent", "load", "memory", "uptime_s", "temps", "disks"}
    <= set(snap),
    list(snap),
)
check(
    "snapshot: readings are sane or None on this machine",
    (snap["cpu_percent"] is None or 0 <= snap["cpu_percent"] <= 100)
    and (
        snap["memory"] is None
        or (
            0 <= snap["memory"]["percent"] <= 100
            and snap["memory"]["used"] <= snap["memory"]["total"]
        )
    )
    and (snap["uptime_s"] is None or snap["uptime_s"] > 0)
    and isinstance(snap["temps"], list)
    and isinstance(snap["disks"], list),
    snap,
)
time.sleep(0.6)
check(
    "snapshot: CPU can be read a second time",
    system.snapshot({})["cpu_percent"] is None or 0 <= system.snapshot({})["cpu_percent"] <= 100,
)


# data streamed
class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


base = datetime(2026, 10, 3, 12, 0, 0).timestamp()
clock = Clock(base)
tr = traffic_mod.Traffic(None, clock)
tr.record("audio", "alice", 5_000_000)
tr.record("media", "alice", 1000)
tr.record("audio", "bob", 2_000_000)
s = tr.snapshot()
check(
    "traffic: today's totals add up, music and pictures kept apart",
    s["today"] == {"audio": 7_000_000, "media": 1000, "total": 7_001_000},
    s["today"],
)
check(
    "traffic: per person, biggest first",
    s["users_today"]
    == [{"user": "alice", "bytes": 5_001_000}, {"user": "bob", "bytes": 2_000_000}],
    s["users_today"],
)
tr.record("video", "x", 5)
tr.record("audio", "x", 0)
tr.record("audio", "x", -5)
check(
    "traffic: unknown kinds, zero and negative amounts are ignored",
    tr.snapshot()["today"]["total"] == 7_001_000,
)
tr.record("audio", None, 10)
tr.record("audio", "n" * 200, 10)
names = {u["user"] for u in tr.snapshot()["users_today"]}
check(
    "traffic: a missing name is 'unknown' and long names are cut",
    "unknown" in names and "n" * 64 in names and "n" * 65 not in names,
)

tr2 = traffic_mod.Traffic(None, Clock(base))
tr2.clock.t = base - 3
tr2.record("audio", "a", 5_000_000)
tr2.clock.t = base
check(
    "traffic: the live rate is the last five seconds averaged",
    tr2.bytes_per_second() == 1_000_000.0,
    tr2.bytes_per_second(),
)
tr2.clock.t = base + 10
check("traffic: the rate falls to zero when nothing is sent", tr2.bytes_per_second() == 0)
tr2.record("audio", "a", 100)
tr2.clock.t = base + 100
tr2.record("audio", "a", 100)
check("traffic: only the last minute of seconds is remembered", len(tr2.seconds) == 1)
tr2.clock.t = base + 3 * 86400
tr2.record("audio", "a", 7)
days = tr2.snapshot()["days"]
check(
    "traffic: a new day starts a new total, the old one stays in the history",
    tr2.snapshot()["today"]["audio"] == 7 and len(days) == 2 and days[0]["audio"] >= 5_000_000,
    days,
)

store = tmp / "traffic.json"
t3 = traffic_mod.Traffic(store, Clock(base))
t3.record("audio", "alice", 123)
t3.flush()
t4 = traffic_mod.Traffic(store, Clock(base))
check(
    "traffic: totals survive a restart",
    t4.snapshot()["today"]["audio"] == 123 and t4.snapshot()["users_today"][0]["user"] == "alice",
)
store.write_text("{not json", encoding="utf-8")
check(
    "traffic: a damaged file starts fresh instead of failing",
    traffic_mod.Traffic(store, Clock(base)).snapshot()["today"]["total"] == 0,
)
store.write_text(
    '{"days": {"2026-10-03": {"audio": "lots", "users": 5}, "2026-10-02": {"audio": 9, "media": 1, "users": {"x": 10}}, "2026-10-01": []}}',
    encoding="utf-8",
)
t5 = traffic_mod.Traffic(store, Clock(base))
check(
    "traffic: bad entries in the file are skipped, good ones kept",
    [d["date"] for d in t5.snapshot()["days"]] == ["2026-10-02"],
    t5.snapshot()["days"],
)
blocked = traffic_mod.Traffic(tmp / "traffic.json" / "inside-a-file.json", Clock(base))
blocked.record("audio", "a", 5)
blocked.flush()
check("traffic: a failed save never breaks counting", blocked.snapshot()["today"]["audio"] == 5)
t6 = traffic_mod.Traffic(None, Clock(base))
for i in range(traffic_mod.KEEP_DAYS + 20):
    t6.days[f"2020-01-{i:05d}"] = {"audio": 1, "media": 0, "users": {}}
t6.path = tmp / "prune.json"
t6.flush()
check("traffic: only the last 90 days are kept", len(t6.days) == traffic_mod.KEEP_DAYS)
t7 = traffic_mod.Traffic(None, Clock(base))
for i in range(traffic_mod.MAX_USERS_PER_DAY + 50):
    t7.record("audio", f"user{i}", 1)
check(
    "traffic: a flood of different names cannot grow it without limit, but the total stays right",
    len(t7.snapshot()["users_today"]) == traffic_mod.MAX_USERS_PER_DAY
    and t7.snapshot()["today"]["audio"] == traffic_mod.MAX_USERS_PER_DAY + 50,
)
t8 = traffic_mod.Traffic(tmp / "throttle.json", Clock(base))
t8.record("audio", "a", 1)
check("traffic: the file is not rewritten on every request", not (tmp / "throttle.json").exists())
t8.clock.t = base + traffic_mod.FLUSH_EVERY + 1
t8.record("audio", "a", 1)
check("traffic: ...but is written once enough time has passed", (tmp / "throttle.json").exists())
check(
    "traffic: only audio streams and media files are counted by path",
    all(traffic_mod.classify(p) == "audio" for p in ("/api/tracks/5/audio",))
    and all(
        traffic_mod.classify(p) == "media"
        for p in ("/api/albums/3/cover", "/api/radio/media/file/a/b.mp4")
    )
    and all(
        traffic_mod.classify(p) is None
        for p in (
            "/api/tracks/x/audio",
            "/api/artists",
            "/api/health/live",
            "/api/tracks/5/audio/extra",
            "/api/auth/me",
        )
    ),
)

# the wrapper that counts bytes as they leave
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient as StarClient


class SetUser:  # stands in for the sign-in gate: says who the request is from
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("state", {})["user"] = {
                "username": scope["headers"]
                and dict(scope["headers"]).get(b"x-user", b"").decode()
                or "nobody"
            }
        await self.app(scope, receive, send)


async def chunks():
    for _ in range(3):
        yield b"c" * 100


routes = [
    Route("/api/tracks/5/audio", lambda r: Response(b"a" * 1000)),
    Route("/api/tracks/6/audio", lambda r: Response(b"p" * 400, status_code=206)),
    Route("/api/tracks/7/audio", lambda r: PlainTextResponse("gone", status_code=404)),
    Route("/api/albums/3/cover", lambda r: Response(b"i" * 500)),
    Route("/api/tracks/8/audio", lambda r: StreamingResponse(chunks())),
    Route("/api/artists", lambda r: Response(b"j" * 900)),
]
counter = traffic_mod.Traffic(None, time.time)
star = StarClient(traffic_mod.TrafficMiddleware(SetUser(Starlette(routes=routes)), counter))
star.get("/api/tracks/5/audio", headers={"x-user": "alice"})
star.get("/api/tracks/6/audio", headers={"x-user": "bob"})
star.get("/api/tracks/7/audio", headers={"x-user": "alice"})
star.get("/api/albums/3/cover", headers={"x-user": "alice"})
star.get("/api/artists", headers={"x-user": "alice"})
star.get("/api/tracks/8/audio", headers={"x-user": "bob"})
star.head("/api/tracks/5/audio", headers={"x-user": "alice"})
t = counter.snapshot()
check(
    "counting: audio and media bytes are counted as sent (full, partial and streamed responses)",
    t["today"] == {"audio": 1000 + 400 + 300, "media": 500, "total": 2200},
    t["today"],
)
check(
    "counting: who got them",
    {u["user"]: u["bytes"] for u in t["users_today"]} == {"alice": 1500, "bob": 700},
    t["users_today"],
)

sent_file = tmp / "sendme.bin"
sent_file.write_bytes(b"z" * 4321)


async def pathsend_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.pathsend", "path": str(sent_file)})


ps = traffic_mod.Traffic(None, time.time)


async def drive():
    msgs = []

    async def recv():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def snd(m):
        msgs.append(m)

    await traffic_mod.TrafficMiddleware(pathsend_app, ps)(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/tracks/1/audio",
            "headers": [],
            "state": {"user": {"username": "carol"}},
        },
        recv,
        snd,
    )


asyncio.run(drive())
check(
    "counting: a file the server sends itself (pathsend) counts at its size",
    ps.snapshot()["today"]["audio"] == 4321 and ps.snapshot()["users_today"][0]["user"] == "carol",
)


# who is listening
lc = Clock(1000.0)
L = listeners_mod.Listeners(lc)
UA_EDGE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 Edg/120.0"
L.ping("k1", "alice", 5, "playing", "radio", "192.168.1.20", UA_EDGE)
rows = L.active()
check(
    "listeners: a listener shows who, what, from where and on what",
    len(rows) == 1
    and rows[0]["user"] == "alice"
    and rows[0]["track_id"] == 5
    and rows[0]["state"] == "playing"
    and rows[0]["source"] == "radio"
    and rows[0]["device"] == "Edge on Windows"
    and rows[0]["where"] == "192.168.1.20 (home network)",
    rows,
)
lc.t += 30
L.ping("k1", "alice", 5, "playing", "radio", "192.168.1.20", UA_EDGE)
check("listeners: the same song keeps its clock", L.active()[0]["listening_s"] == 30)
lc.t += 30
L.ping("k1", "alice", 6, "playing", "radio", "192.168.1.20", UA_EDGE)
check(
    "listeners: a new song restarts the clock",
    L.active()[0]["listening_s"] == 0 and L.active()[0]["track_id"] == 6,
)
lc.t += 10
L.ping("k1", "alice", 6, "paused", "radio", "192.168.1.20", UA_EDGE)
check("listeners: paused stays listed as paused", L.active()[0]["state"] == "paused")
L.ping(
    "k2",
    "alice",
    7,
    "playing",
    "archive",
    "10.0.0.5",
    "Mozilla/5.0 (Linux; Android 14) Chrome/120 Mobile Safari/537.36",
)
check(
    "listeners: the same person on two devices is two rows",
    [r["device"] for r in L.active() if r["user"] == "alice"].count("Chrome on Android") == 1
    and len(L.active()) == 2,
)
lc.t += listeners_mod.TTL - 5
check("listeners: still listed shortly before the timeout", len(L.active()) == 2)
lc.t += 10
check("listeners: gone after 75 seconds of silence", L.active() == [])
L.ping("k3", "bob", 1, "playing", "radio", "10.0.0.6", "")
L.ping("k3", "bob", None, "stopped", "radio", "10.0.0.6", "")
check("listeners: 'stopped' removes at once", L.active() == [])
L.ping("k4", "bob", 1, "exploding", "radio", "10.0.0.6", "")
check("listeners: an unknown state is ignored", L.active() == [])
for i in range(listeners_mod.MAX_ENTRIES + 25):
    L.ping(f"flood{i}", "mallory", 1, "playing", "radio", "10.0.0.9", "")
check(
    "listeners: a flood cannot grow the list past its limit",
    len(L.items) <= listeners_mod.MAX_ENTRIES,
)
L2 = listeners_mod.Listeners(Clock(5.0))
L2.ping("a", "Zed", 1, "playing", "radio", "1.1.1.1", "")
L2.ping("b", "amy", 1, "playing", "radio", "1.1.1.1", "")
check(
    "listeners: sorted by name, ignoring case", [r["user"] for r in L2.active()] == ["amy", "Zed"]
)
check(
    "devices: browser and system names",
    all(
        listeners_mod.device_name(ua) == want
        for ua, want in (
            (UA_EDGE, "Edge on Windows"),
            (
                "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
                "Firefox on Linux",
            ),
            (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605 Version/17 Mobile Safari/604.1",
                "Safari on iPhone",
            ),
            ("", "a browser"),
            ("curl/8.0", "a browser"),
            (None, "a browser"),
        )
    ),
)
check(
    "addresses: home network, this server, internet, nonsense (and nothing is looked up online)",
    [
        listeners_mod.where(x)
        for x in ("192.168.1.5", "10.0.0.5", "127.0.0.1", "8.8.8.8", "::1", "garbage", "")
    ]
    == [
        "192.168.1.5 (home network)",
        "10.0.0.5 (home network)",
        "127.0.0.1 (this server)",
        "8.8.8.8 (internet)",
        "::1 (this server)",
        "unknown address",
        "unknown address",
    ],
)
check(
    "listeners: nothing in the code reaches the network",
    not re.search(
        r"import (requests|urllib|http\.client|socket)|urlopen",
        open("monitor/listeners.py", encoding="utf-8").read(),
    ),
)


# recent problems
h = errors.RecentErrors()
lg = logging.getLogger("totr.test.monitor")
lg.propagate = False
lg.addHandler(h)
lg.setLevel(logging.DEBUG)
lg.info("just chatting")
lg.warning("disk is %s full", "93%")
lg.error(
    "connect failed password=hunter2 to postgresql://totr:secret@10.0.0.5/totr token: abc123 api_key=KEY"
)
try:
    raise ValueError("boom")
except ValueError:
    lg.exception("it broke")
rec = h.recent()
check(
    "problems: warnings and errors are kept, newest first, chatter is not",
    [r["level"] for r in rec] == ["ERROR", "ERROR", "WARNING"]
    and rec[2]["message"] == "disk is 93% full",
    rec,
)
msg = rec[1]["message"]
check(
    "problems: passwords, tokens, keys and connection strings are blanked",
    all(x not in msg for x in ("hunter2", "secret", "abc123", "KEY", "10.0.0.5"))
    and "[hidden]" in msg
    and "[link removed]" in msg,
    msg,
)
check(
    "problems: an exception adds its type, never its trace",
    rec[0]["message"] == "it broke (ValueError)" and "Traceback" not in rec[0]["message"],
)
lg.error("x" * 2000)
check("problems: long messages are cut", len(h.recent()[0]["message"]) <= errors.MAX_LENGTH)
for i in range(errors.MAX_ITEMS + 30):
    lg.warning("n %d", i)
check(
    "problems: only the last 50 are remembered",
    len(h.items) == errors.MAX_ITEMS
    and h.recent(5)[0]["message"] == f"n {errors.MAX_ITEMS + 29}"
    and len(h.recent(3)) == 3,
)
errors.install()
errors.install()
check(
    "problems: installing twice adds one handler",
    logging.getLogger().handlers.count(errors.recent_errors) == 1,
)
check(
    "problems: the cleaner is safe on odd input",
    errors.clean("") == ""
    and errors.clean("a   b\n c") == "a b c"
    and "[hidden]" in errors.clean("PASSWORD: x"),
)

# the endpoints
import sqlite_layout as layout

AUTH_DB = tmp / "auth.sqlite"
layout.create(AUTH_DB, layout.ACCOUNTS)
Shim = layout.shim(AUTH_DB)


for k in ("DB_AUTH_USER", "DB_AUTH_PASSWORD"):
    os.environ[k] = "x"
from fastapi.testclient import TestClient

import main
import monitor
from auth import service
from mixtapes import web as mixtapes_web

service.connect = Shim
PW = "correct horse battery staple"
service.create_user("Nova", PW, "admin")
service.create_user("alice", PW + "1")
service.create_user("viewer", PW + "2", "member", ["status"])


def signed_in(user, pw):
    cl = TestClient(main.app)
    assert cl.post("/api/auth/login", json={"username": user, "password": pw}).status_code == 200
    return cl, {"X-CSRF-Token": cl.get("/api/auth/me").json()["csrf"]}


admin, AH = signed_in("Nova", PW)
alice, LH = signed_in("alice", PW + "1")
viewer, VH = signed_in("viewer", PW + "2")
anon = TestClient(main.app)
mixtapes_web.lookup_tracks = lambda ids: {
    i: {"track_id": i, "title": f"Song {i}", "artists": f"Artist {i}", "album_id": None}
    for i in ids
    if i < 100
}

check(
    "live: anonymous is refused, members are refused",
    anon.get("/api/health/live").status_code == 401
    and alice.get("/api/health/live").status_code == 403,
)
check(
    "live: admins and anyone with the status permission may look",
    admin.get("/api/health/live").status_code == 200
    and viewer.get("/api/health/live").status_code == 200,
)
live = admin.get("/api/health/live").json()
check(
    "live: has every part the page draws",
    {"at", "listeners", "traffic", "system", "library", "spiders", "collector", "problems"}
    <= set(live),
    list(live),
)
check(
    "live: the library counts come from the real library and the size is measured in the background",
    (live["library"]["counts"] is not None or not layout.live_ready())
    and {"bytes", "files", "measuring"} <= set(live["library"]["size"]),
    live["library"],
)
check(
    "live: the traffic part has the rate, today and per person",
    {"bps", "today", "users_today", "days"} <= set(live["traffic"]),
)
check(
    "live: jobs report what the spiders and collector are doing",
    {"running", "last_scan", "findings"} <= set(live["spiders"])
    and {"searching", "suggestions", "downloads_active", "added"} <= set(live["collector"]),
    (live["spiders"], live["collector"]),
)
check(
    "live: never contains session secrets",
    "token_hash" not in admin.get("/api/health/live").text
    and "csrf" not in admin.get("/api/health/live").text.lower(),
)

ping = lambda cl, h, **b: cl.post(
    "/api/listening/ping",
    json={"track_id": 5, "state": "playing", "source": "radio", **b},
    headers=h,
)
check(
    "ping: any signed-in person may send it, anonymous may not, and the CSRF token is needed",
    ping(alice, LH).status_code == 200
    and ping(anon, {}).status_code == 401
    and alice.post(
        "/api/listening/ping", json={"track_id": 5, "state": "playing", "source": "radio"}
    ).status_code
    == 403,
)
os.environ["TRUSTED_PROXY"] = "testclient"
r = alice.post(
    "/api/listening/ping",
    json={"track_id": 5, "state": "playing", "source": "archive"},
    headers={**LH, "X-Forwarded-For": "192.168.1.50", "User-Agent": UA_EDGE},
)
del os.environ["TRUSTED_PROXY"]
mine = [l for l in admin.get("/api/health/live").json()["listeners"] if l["user"] == "alice"]
check(
    "ping: the admin sees who, which song (by name), from where and on what",
    len(mine) == 1
    and mine[0]["track"] == {"track_id": 5, "title": "Song 5", "artists": "Artist 5"}
    and mine[0]["where"] == "192.168.1.50 (home network)"
    and mine[0]["device"] == "Edge on Windows"
    and mine[0]["source"] == "archive"
    and mine[0]["state"] == "playing",
    mine,
)
check(
    "ping: a song missing from the library still lists the listener",
    (
        ping(alice, LH, track_id=5000),
        [l for l in admin.get("/api/health/live").json()["listeners"] if l["user"] == "alice"][0][
            "track"
        ]
        is None,
    )[1],
)
check(
    "ping: no song at all (paused on an empty queue) is fine",
    ping(alice, LH, track_id=None, state="paused").status_code == 200,
)
for bad in (
    {"state": "dancing"},
    {"source": "tv"},
    {"track_id": 0},
    {"track_id": -4},
    {"track_id": 2**40},
    {"track_id": "x"},
    {"track_id": 1.5},
):
    check(f"ping: refuses {bad}", ping(alice, LH, **bad).status_code == 422)
check(
    "ping: a missing state or source is refused",
    alice.post("/api/listening/ping", json={"track_id": 1}, headers=LH).status_code == 422,
)
ping(alice, LH, state="stopped")
check(
    "ping: 'stopped' takes the listener off the list",
    all(l["user"] != "alice" for l in admin.get("/api/health/live").json()["listeners"]),
)
check(
    "ping: the heartbeat is a member-level route, the numbers are not",
    alice.get("/api/health/live").status_code == 403,
)

# the real app counts real audio responses, per person
wav = tmp / "tone.wav"
with wave.open(str(wav), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(8000)
    w.writeframes(b"\x01\x02" * 16000)
size = wav.stat().st_size


class FakeConn:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        class R:
            def fetchone(s):
                return {"file_path": str(wav)}

        return R()


real_get_conn = main.db.get_conn
main.db.get_conn = lambda: FakeConn(wav)
before = monitor.meter.snapshot()
before_user = {u["user"]: u["bytes"] for u in before["users_today"]}
r1 = alice.get("/api/tracks/5/audio")
r2 = alice.get("/api/tracks/5/audio", headers={"Range": "bytes=0-99"})
anon_try = anon.get("/api/tracks/5/audio")
main.db.get_conn = real_get_conn
after = monitor.meter.snapshot()
after_user = {u["user"]: u["bytes"] for u in after["users_today"]}
check(
    "streaming: the app serves the file, a Range request is partial",
    r1.status_code == 200 and r2.status_code == 206 and len(r2.content) == 100,
)
check(
    "streaming: the real app counts what it sent, to the right person",
    after["today"]["audio"] - before["today"]["audio"] == size + 100
    and after_user.get("alice", 0) - before_user.get("alice", 0) == size + 100,
    (after["today"]["audio"] - before["today"]["audio"], size),
)
check(
    "streaming: a refused request sends nothing and counts nothing",
    anon_try.status_code == 401
    and after["today"]["audio"] - before["today"]["audio"] == size + 100,
)
check(
    "streaming: the admin's live view shows today's total and the person",
    {u["user"] for u in admin.get("/api/health/live").json()["traffic"]["users_today"]}
    >= {"alice"},
)

logging.getLogger("totr.test").warning("something broke password=abc123")
probs = admin.get("/api/health/live").json()["problems"]
check(
    "problems: the admin sees what went wrong, with secrets blanked",
    any("something broke" in p["message"] and "abc123" not in p["message"] for p in probs),
    probs[:3],
)
check(
    "pages: the Admin page has the live Status panels and no inline handlers",
    all(
        x in admin.get("/admin").text
        for x in ('id="listeners"', 'id="bps"', 'id="disks"', 'id="problems"', 'id="jobs"')
    )
    and " onclick=" not in admin.get("/admin").text,
)
check(
    "pages: the shared script carries the listening heartbeat",
    "trackListening" in open("static/app.js", encoding="utf-8").read()
    and "trackListening(audio, 'radio'" in open("templates/radio.html", encoding="utf-8").read()
    and "trackListening(player, 'archive'"
    in open("templates/archive.html", encoding="utf-8").read(),
)


# the Status tab, in a browser
sys.path.insert(0, str(pathlib.Path(PF) / "tests"))
import browser_runner as br

edge = br.find_browser()
if not edge:
    print("skip Status tab in a browser: no Edge/Chromium found (set EDGE_PATH to run it)")
else:
    try:
        res = br.run(
            edge,
            br.page_from_template(tmp, "admin.html", "status_stubs.js", "status_driver.js"),
            tmp,
            budget=60000,
            timeout=200,
        )
    except Exception as e:
        res = None
        print("note: Status tab run failed:", repr(e)[:200])
    check("Status tab in a browser: produced a result", res is not None)
    if res is not None:
        st = res["steps"]
        check("Status tab in a browser: no script errors", not res["errors"], res["errors"])
        check(
            "Status tab in a browser: both the slow health check and the live numbers load once at the start",
            st["firstLoad"] == {"health": 1, "live": 1} and st["overall"] == "OK",
            st["firstLoad"],
        )
        who = st["listeners"]
        check(
            "Status tab in a browser: listeners show who, what, on what, from where and for how long",
            who[1]
            == [
                "alice",
                "In Your Eyes - Tigerberry",
                "playing",
                "radio, Edge on Windows",
                "192.168.1.50 (home network)",
                "2m 5s",
            ]
            and who[3][1] == "(a song that is not in the library)"
            and who[3][5] == "1h 1m",
            who,
        )
        check(
            "Status tab in a browser: names and titles that contain HTML stay plain text",
            st["injection"] == {"pwned": None, "imgs": 0}
            and who[2][0].startswith("<img")
            and st["problemBold"] == 0,
            st["injection"],
        )
        check(
            "Status tab in a browser: current rate and today's totals",
            st["bps"] == "1.5 MB/s"
            and st["today"] == "Today: 3.0 GB (3.0 GB of music, 5.0 MB of pictures and videos).",
            (st["bps"], st["today"]),
        )
        check(
            "Status tab in a browser: data streamed per person and per day",
            st["trafficUsers"][1:] == [["alice", "2.0 GB"], ["bob", "1.0 GB"]]
            and st["trafficDays"] == [["2026-10-03", "3.0 GB"], ["2026-10-02", "1.0 GB"]],
            (st["trafficUsers"], st["trafficDays"]),
        )
        check(
            "Status tab in a browser: system info",
            st["sys"][:2] == ["processor | 37.5%", "memory | 6.0 GB of 16.0 GB (37.5%)"]
            and "4 cores" in st["sys"][2]
            and "10d 0h 0m" in st["sys"][3]
            and "52" in st["sys"][4],
            st["sys"],
        )
        check(
            "Status tab in a browser: drives show use, a nearly full one is marked, a missing one says so",
            "92.5%" in st["disks"][1]
            and st["diskBars"] == {"hot": 1, "bars": 1}
            and "not found" in st["disks"][2],
            (st["disks"], st["diskBars"]),
        )
        check(
            "Status tab in a browser: library size, scans and collector jobs",
            st["jobs"][0] == "library | 56 artists / 5 albums / 142 songs"
            and st["jobs"][1] == "size on disk | 12.0 GB in 142 files"
            and "7 item(s)" in st["jobs"][2]
            and "24 suggestion(s)" in st["jobs"][3]
            and "2 waiting" in st["jobs"][4]
            and st["jobs"][5].endswith("11"),
            st["jobs"],
        )
        check(
            "Status tab in a browser: recent problems are listed",
            st["problems"][1][2] == "disk <b>full</b> (OSError)"
            and st["problems"][2][1] == "uvicorn.error",
            st["problems"],
        )
        check(
            "Status tab in a browser: while visible the live numbers refresh by themselves, the slow check does not",
            st["visiblePolling"] == {"health": 1, "live": 2},
            st["visiblePolling"],
        )
        check(
            "Status tab in a browser: nothing is fetched while the page is hidden",
            st["hiddenPolling"]["before"] == st["hiddenPolling"]["after"],
            st["hiddenPolling"],
        )
        check(
            "Status tab in a browser: coming back refreshes at once",
            st["backAgain"]["after"]
            == {
                "health": st["backAgain"]["before"]["health"] + 1,
                "live": st["backAgain"]["before"]["live"] + 1,
            },
            st["backAgain"],
        )
        check(
            "Status tab in a browser: on another tab the status polling stops, and the live panels hide",
            st["otherTab"]["liveHidden"] is True
            and st["otherTab"]["before"] == st["otherTab"]["after"],
            st["otherTab"],
        )
        check(
            "Status tab in a browser: coming back to the tab shows the panels and loads again",
            st["returned"]["liveHidden"] is False
            and st["returned"]["after"]["live"] > st["otherTab"]["after"]["live"],
            st["returned"],
        )

finish()
