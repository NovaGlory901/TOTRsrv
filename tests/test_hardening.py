"""Holes found by attacking a test server (docs/threat-model.md, "Break-it run on the server"). Each
check here failed before its fix: a chunked body walked around every size limit; an unhandled error
went out without security headers; an id of 20 digits was a 500 (or a wrong "needs one more database
step"); an upload with a long name was a 500 and a file that is not a song was stored; a made-up
invite link cost a full password hash per try and the real link sat in the request log; a missing
downloads folder made every /static/downloads/ request a 500."""

import io
import logging
import logging.config
import math
import os
import pathlib
import re
import struct
import sys
import tempfile
import wave

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
os.environ["DB_ENGINE"] = "postgres"
tmp = pathlib.Path(tempfile.mkdtemp(prefix="totr-hard-"))
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["APP_SETTINGS"] = str(tmp / "reports" / "app_settings.json")
# a server with no installers yet; main.py makes the folder
os.environ["TOTR_DOWNLOADS_DIR"] = str(tmp / "no-such-folder" / "downloads")
os.environ.pop("COOKIE_SECURE", None)
import sqlite_layout as layout
from check import check, finish

DB, MEDIA = tmp / "auth.sqlite", tmp / "media.sqlite"
layout.create(DB, layout.ACCOUNTS, layout.MIXTAPES)
layout.create(MEDIA, layout.MEDIA)
for k in ("DB_AUTH_USER", "DB_AUTH_PASSWORD"):
    os.environ[k] = "x"
import psycopg
from fastapi.testclient import TestClient

import db
import main
import uploader
from auth import passwords, service
from mixtapes import web as mixtapes_web

service.connect = layout.shim(DB)
db.get_conn = lambda: layout.Conn(MEDIA)
db.get_write_conn = lambda: layout.Conn(MEDIA)
db.write_enabled = lambda: True
db.mixtape_enabled = lambda: True
mixtapes_web.connect = layout.shim(DB)
uploader.MUSIC_ROOT = tmp / "music"
uploader.MUSIC_ROOT.mkdir()
PW = "correct horse battery staple"
service.create_user("boss", PW, "admin")
service.create_user("rae", PW + "1")


def signed_in(user, pw, **kw):
    c = TestClient(main.app, raise_server_exceptions=False, **kw)
    assert c.post("/api/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c, {"X-CSRF-Token": c.get("/api/auth/me").json()["csrf"]}


anon = TestClient(main.app, raise_server_exceptions=False)
boss, BH = signed_in("boss", PW)
rae, RH = signed_in("rae", PW + "1")
SEC = ("x-content-type-options", "x-frame-options", "referrer-policy", "content-security-policy")

# 1. a request body must have a length the server can check
r = anon.post(
    "/api/auth/login",
    content=b"{}",
    headers={"Content-Length": "2", "Transfer-Encoding": "chunked"},
)
check(
    "a body with Transfer-Encoding is refused (411), even with a small Content-Length beside it",
    r.status_code == 411,
    r.status_code,
)
r = rae.post(
    "/api/mixtapes",
    content=b"{}",
    headers={**RH, "Content-Length": "2", "Transfer-Encoding": "chunked"},
)
check("...also when signed in", r.status_code == 411, r.status_code)
r = anon.get("/health", headers={"Transfer-Encoding": "chunked"})
check("...and on a GET", r.status_code == 411, r.status_code)
check("the refusal carries the security headers", all(h in r.headers for h in SEC))
check(
    "a normal login still works",
    anon.post("/api/auth/login", json={"username": "rae", "password": PW + "1"}).status_code == 200,
)


# 2. an unhandled error: generic JSON, security headers, nothing from the inside
@main.app.get("/api/_boom")
def _boom():
    raise RuntimeError("secret /home/alice/totr/.env SELECT * FROM users password=hunter2")


@main.app.get("/api/_range")
def _range():
    raise OverflowError("Python int too large to convert to SQLite INTEGER")


@main.app.get("/api/_pgrange")
def _pgrange():
    raise psycopg.errors.NumericValueOutOfRange("integer out of range")


r = rae.get("/api/_boom")
check(
    "an unhandled error is a 500 with a JSON message",
    r.status_code == 500 and r.text.strip() == '{"detail":"Something went wrong on the server."}',
    (r.status_code, r.text[:80]),
)
check(
    "...that shows nothing from the inside (path, SQL, password)",
    not any(w in r.text for w in ("secret", "/home", "SELECT", "hunter2", "Traceback")),
)
check(
    "...and still carries the security headers", all(h in r.headers for h in SEC), dict(r.headers)
)
for path, label in (
    ("/api/_range", "SQLite's too-large-number error"),
    ("/api/_pgrange", "PostgreSQL's out-of-range error"),
):
    r = rae.get(path)
    check(
        f"{label} is a 422 'out of range'",
        r.status_code == 422 and "out of range" in r.text,
        (r.status_code, r.text[:80]),
    )

# 3. ids too big for the database, through real routes
for path in (
    "/api/tracks/99999999999999999999/audio",
    "/api/albums/99999999999999999999/cover",
    "/api/artists/99999999999999999999",
    "/api/albums/99999999999999999999/tracks",
):
    r = rae.get(path)
    check(f"{path} is 422, not 500", r.status_code == 422, (r.status_code, r.text[:60]))
r = rae.post("/api/mixtapes/99999999999999999999/delete", json={}, headers=RH)
check(
    "a mixtape id of 20 digits is 422, not 500", r.status_code == 422, (r.status_code, r.text[:60])
)
r = boss.post("/api/admin/users/99999999999999999999/disable", json={}, headers=BH)
check(
    "a user id of 20 digits is 422, not 503 or 500",
    r.status_code == 422,
    (r.status_code, r.text[:60]),
)


# 4. uploads
def wav(freq=330, seconds=0.2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / 8000)))
                for i in range(int(8000 * seconds))
            )
        )
    return buf.getvalue()


def upload(artist, files, album="", who=boss, hdr=BH):
    return who.post(
        "/api/upload",
        data={"artist": artist, "album": album},
        files=[("files", f) for f in files],
        headers=hdr,
    )


leftover = lambda: sorted(p.name for p in uploader.MUSIC_ROOT.rglob("*"))
GOOD = wav()
r = upload("Junk Band", [("junk.mp3", os.urandom(4000), "audio/mpeg")], album="Junk Album")
check(
    "a file named .mp3 that is not a song is refused (422) and named",
    r.status_code == 422 and "junk.mp3" in r.text,
    (r.status_code, r.text[:80]),
)
check("...and leaves no files or folders behind", leftover() == [], leftover())
check(
    "...nor an empty file",
    upload("Junk Band", [("empty.flac", b"", "audio/flac")]).status_code == 422
    and leftover() == [],
)
r = upload(
    "Junk Band",
    [("good.wav", GOOD, "audio/wav"), ("bad.wav", b"RIFF" + os.urandom(100), "audio/wav")],
)
check(
    "one bad file in an album refuses the whole upload and removes the good one too",
    r.status_code == 422 and leftover() == [],
    (r.status_code, leftover()),
)
r = upload("Real Band", [("real.wav", GOOD, "audio/wav")])
check(
    "a real song is still accepted",
    r.status_code == 200 and (uploader.MUSIC_ROOT / "Real Band" / "Singles" / "real.wav").is_file(),
    (r.status_code, r.text[:80]),
)
r = upload("L" * 300, [("a.wav", GOOD, "audio/wav")], album="A" * 300)
check(
    "an artist and album of 300 characters: accepted, folder names cut to what the file system takes",
    r.status_code == 200
    and all(len(p.name.encode()) <= 255 for p in uploader.MUSIC_ROOT.rglob("*")),
    (r.status_code, r.text[:80]),
)
r = upload("L" * 301, [("a.wav", GOOD, "audio/wav")])
check("301 characters of artist is refused (422)", r.status_code == 422, r.status_code)
r = upload("Long File Band", [("f" * 400 + ".wav", GOOD, "audio/wav")])
names = [p.name for p in (uploader.MUSIC_ROOT / "Long File Band" / "Singles").glob("*")]
check(
    "a file name of 400 characters: accepted, cut, and the .wav is kept",
    r.status_code == 200
    and len(names) == 1
    and names[0].endswith(".wav")
    and len(names[0].encode()) <= 255,
    (r.status_code, names),
)
check(
    "a plain member still cannot upload",
    upload("Nope", [("a.wav", GOOD, "audio/wav")], who=rae, hdr=RH).status_code == 403,
)
for original, want in (("a" * 500 + ".mp3", ".mp3"), ("é" * 300 + ".flac", ".flac"), ("name", "")):
    cut = uploader.safe_name(original, want)
    check(
        f"safe_name keeps the ending {want!r} and stays under 255 bytes ({len(original)} characters in)",
        cut.endswith(want) and len(cut.encode()) <= 255 and (cut.encode().decode("utf-8") == cut),
        len(cut.encode()),
    )
check(
    "safe_name still removes separators and dots at the ends",
    uploader.safe_name("../../x/y\\z.") == "xyz" and uploader.safe_name("..") == "Unknown",
)

# the inspect step (reads the tags of the chosen files before the form is filled in) and a big
# upload are not held in memory
r = boss.post(
    "/api/upload/inspect",
    files=[("files", ("a.wav", GOOD, "audio/wav")), ("files", ("note.txt", b"x", "text/plain"))],
    headers=BH,
)
check(
    "inspect reads the tags of an audio file and skips the others",
    r.status_code == 200 and r.json()["tracks"] == 1,
    (r.status_code, r.text[:80]),
)
import tracemalloc

big = tmp / "big.wav"
with wave.open(str(big), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(8000)
    chunk = b"\x00\x00" * 8000
    for _ in range(1500):  # about 24 MB of silence
        w.writeframes(chunk)
size = big.stat().st_size
tracemalloc.start()
with open(big, "rb") as fh:
    # what the upload does with the file the framework keeps on disk
    uploader.write_file(tmp / "copy.wav", fh)
peak = tracemalloc.get_traced_memory()[1]
tracemalloc.stop()
check(
    f"a {size // 1024} KB upload is copied to its place in pieces (memory peak {peak // 1024} KB)",
    (tmp / "copy.wav").stat().st_size == size and peak < 4 * 1024 * 1024,
    peak,
)
check(
    "...and the same bytes are written when a plain bytes value is given",
    (uploader.write_file(tmp / "b.bin", b"abc"), (tmp / "b.bin").read_bytes())[1] == b"abc",
)
main_src = pathlib.Path("main.py").read_text(encoding="utf-8")
up_src = main_src.split("async def upload(", 1)[1].split("# ---------- maintenance spiders", 1)[0]
# the layout of the code does not matter here
up_src = re.sub(r"\(\s+", "(", " ".join(up_src.split()))
check(
    "the upload route hands the open files on (no f.read() of a whole song) and saves off the event loop",
    "await f.read()" not in up_src
    and "await cover.read()" not in up_src
    and "(f.filename, f.file)" in up_src
    and "run_in_threadpool(uploader.save_album" in up_src,
)
r = boss.post(
    "/api/upload",
    data={"artist": "Big Band", "album": ""},
    files=[("files", ("big.wav", big.open("rb"), "audio/wav"))],
    headers=BH,
)
check(
    "a 24 MB song through the route is stored whole",
    r.status_code == 200
    and (uploader.MUSIC_ROOT / "Big Band" / "Singles" / "big.wav").stat().st_size == size,
    (r.status_code, r.text[:80]),
)

# the temporary files of an upload go to disk next to the server's other files, not to the system's
# /tmp (memory on many systems)
import tempfile

check(
    "uploads are spooled under reports/upload-tmp (not /tmp, which is memory on Arch)",
    pathlib.Path(tempfile.gettempdir()).name == "upload-tmp"
    and pathlib.Path(tempfile.gettempdir()).is_dir(),
    tempfile.gettempdir(),
)
stale = pathlib.Path(tempfile.gettempdir()) / "tmp-old-crash"
stale.write_bytes(b"x")
os.utime(stale, (1, 1))
fresh_one = pathlib.Path(tempfile.gettempdir()) / "tmp-new"
fresh_one.write_bytes(b"x")
main.use_disk_for_uploads()
check(
    "...and a file a crash left there more than an hour ago is removed at start, a new one is not",
    not stale.exists() and fresh_one.exists(),
)
fresh_one.unlink()

# 5. a server with no installers yet
r = anon.get("/static/downloads/TOTR-1.0-win.exe")
check(
    "a missing installer is a 404 even when the downloads folder did not exist at first",
    r.status_code == 404 and (tmp / "no-such-folder" / "downloads").is_dir(),
    r.status_code,
)
check("...and the folder listing is not served", anon.get("/static/downloads/").status_code == 404)

# 6. made-up invite links
hashed = []
real_hash = passwords.hash_password
passwords.hash_password = lambda pw: (hashed.append(1), real_hash(pw))[1]
service.invite_tries._hits.clear()
body = lambda tok, name="newbie": {
    "token": tok,
    "username": name,
    "password": "a long quiet passphrase 91",
}
r = anon.post("/api/auth/accept-invite", json=body("not-a-real-link"))
check(
    "a made-up link is refused (400) before any password is hashed",
    r.status_code == 400 and not hashed,
    (r.status_code, len(hashed)),
)
tok = service.create_invite({"user_id": 1, "role": "admin"}, "member")
r = anon.post("/api/auth/accept-invite", json=body(tok, "newbie1"))
check(
    "a real link still works, and hashes once",
    r.status_code == 200 and len(hashed) == 1,
    (r.status_code, len(hashed)),
)
r = anon.post("/api/auth/accept-invite", json=body(tok, "newbie2"))
check("the same link again is refused without hashing", r.status_code == 400 and len(hashed) == 1)
service.invite_tries._hits.clear()
codes = [
    anon.post("/api/auth/accept-invite", json=body(f"guess-{i}")).status_code for i in range(25)
]
check(
    "after 20 wrong links an address is told to wait (429)",
    codes[:20] == [400] * 20 and codes[20:] == [429] * 5 and len(hashed) == 1,
    codes,
)
r = anon.post("/api/auth/accept-invite", json=body("x"))
check("...with a Retry-After", r.status_code == 429 and int(r.headers.get("retry-after", 0)) > 0)
passwords.hash_password = real_hash
service.invite_tries._hits.clear()
tok = service.create_invite({"user_id": 1, "role": "admin"}, "member")
r = anon.post(
    "/api/auth/accept-invite", json={"token": tok, "username": "weakling", "password": "short"}
)
check(
    "a weak password for a real link is still 422 and the link stays usable",
    r.status_code == 422
    and anon.post("/api/auth/accept-invite", json=body(tok, "stronger")).status_code == 200,
)

# 6b. the layers underneath, kept honest. Since the link is looked at first (6), the database
# statements below it are the second line of defence. Two people redeeming one link at the same
# moment both pass the first look; only the single atomic UPDATE decides which one wins. These
# checks call it directly, so it cannot be loosened without a test failing.
import hashlib
from datetime import UTC, datetime, timedelta

raw = lambda sql, params=(): layout.raw(DB, sql, params)
tok = service.create_invite({"user_id": 1, "role": "admin"}, "member")
th, now = hashlib.sha256(tok.encode()).hexdigest(), datetime.now(UTC)
with service.connect() as conn:
    first, second = store_consume = (
        service.store.consume_invite(conn, th, now, True),
        service.store.consume_invite(conn, th, now, True),
    )
check(
    "redeeming an invite twice at the statement level: the second gets nothing",
    first is not None and second is None,
    (first, second),
)
tok = service.create_invite({"user_id": 1, "role": "admin"}, "member")
th = hashlib.sha256(tok.encode()).hexdigest()
raw(
    "UPDATE totr_invites SET expires_at = ? WHERE token_hash = ?",
    (layout.conv(now - timedelta(days=1)), th),
)
with service.connect() as conn:
    check(
        "an expired invite gives nothing at the statement level",
        service.store.consume_invite(conn, th, now, True) is None
        and service.store.invite_usable(conn, th, now) is False,
    )
tok = service.create_invite({"user_id": 1, "role": "admin"}, "member")
th = hashlib.sha256(tok.encode()).hexdigest()
with service.connect() as conn:
    check("a fresh invite is usable", service.store.invite_usable(conn, th, now) is True)
    service.store.consume_invite(conn, th, now, True)
    check("...and not after it was redeemed", service.store.invite_usable(conn, th, now) is False)

# a disabled person's session stops at the lookup itself, not only because disabling also deletes
# the sessions
service.create_user("ghost", PW + "9")
g, GH = signed_in("ghost", PW + "9")
check("ghost can use the API", g.get("/api/auth/me").status_code == 200)
raw("UPDATE totr_users SET disabled = 1 WHERE username = 'ghost'")
check(
    "the session of a disabled account is refused by the lookup, with the session row still there",
    raw(
        "SELECT count(*) AS n FROM totr_sessions s JOIN totr_users u ON u.user_id = s.user_id WHERE u.username = 'ghost'"
    )[0]["n"]
    == 1
    and g.get("/api/auth/me").status_code == 401,
)

# a POST that says nothing about its length (no Content-Length and no Transfer-Encoding) is refused
import asyncio


async def bare_post():
    sent = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/auth/login",
        "raw_path": b"/api/auth/login",
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("1.2.3.4", 1),
        "server": ("testserver", 80),
    }

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        sent.append(message)

    await main.app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


check("a POST with no Content-Length at all is refused (411)", asyncio.run(bare_post()) == 411)

# 6c. https-only headers
secure = TestClient(main.app, base_url="https://testserver", raise_server_exceptions=False)
check(
    "over https every answer says Strict-Transport-Security (also a 404 and a refusal)",
    all(
        "max-age=" in secure.get(p).headers.get("strict-transport-security", "")
        for p in ("/health", "/nothing-here", "/api/artists")
    ),
)
check(
    "over plain http it does not (a browser would ignore it, and it would mislead)",
    "strict-transport-security" not in anon.get("/health").headers,
)
r = secure.post("/api/auth/login", json={"username": "rae", "password": PW + "1"})
check(
    "the session cookie is Secure over https and HttpOnly, SameSite=Lax always",
    "Secure" in r.headers["set-cookie"]
    and "HttpOnly" in r.headers["set-cookie"]
    and "samesite=lax" in r.headers["set-cookie"].lower(),
    r.headers["set-cookie"],
)
r = anon.post("/api/auth/login", json={"username": "rae", "password": PW + "1"})
check(
    "...and without Secure over plain http (a Secure cookie would never come back)",
    "Secure" not in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"],
)

# 6d. the database administrator's password never reaches the log
import dbmove

fake = tmp / "helper.py"
fake.write_text(
    "import json, sys\nd = json.loads(sys.stdin.read())\nsys.stderr.write('connection failed for password ' + d['admin_password'] + ' on host x')\nsys.exit(1)\n",
    encoding="utf-8",
)
dbmove.HELPER = fake
seen = []


class Grab(logging.Handler):
    def emit(self, record):
        seen.append(record.getMessage())


grab = Grab()
dbmove.log.addHandler(grab)
SECRET = "Zq9-hunter-secret-4477"
res = dbmove._run_helper({"admin_password": SECRET, "mode": "test"}, 20, lambda e: None)
dbmove.log.removeHandler(grab)
check(
    "a helper that dies and echoes the password in its error text: the log and the answer do not carry it",
    res.get("ok") is False and SECRET not in " ".join(seen) + str(res) and "***" in " ".join(seen),
    (seen, res),
)

# 6e. server text is never turned into page markup. Names, notes, titles and tags come from people
# and files. The pages and the app draw them as text; nothing in our own code writes markup, runs a
# string, or builds a function from one.
HTML_SINKS = re.compile(
    r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|dangerouslySetInnerHTML|(?<![A-Za-z_.])eval\(|new Function\("
)
own = [
    p
    for p in sorted(pathlib.Path("static").glob("*.js"))
    if not p.name.startswith(("dejavuamp-core", "falsehorizon"))
] + sorted(pathlib.Path("templates").glob("*.html"))
hits = [
    f"{p}:{n}"
    for p in own
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
    if HTML_SINKS.search(line)
]
check(
    f"the server's pages and scripts ({len(own)} files) never write markup or run strings",
    not hits,
    hits[:5],
)
spa = pathlib.Path("..") / "TOTRspa" / "TOTRspa_web" / "src"
if spa.is_dir():
    hits = [
        f"{p.name}:{n}"
        for p in sorted(spa.rglob("*"))
        if p.suffix in (".js", ".jsx", ".ts", ".tsx")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if HTML_SINKS.search(line)
    ]
    check("...nor does the listener app's own source", not hits, hits[:5])
else:
    print("skip  the listener app's source is not next to this server (an installed server)")

# 7. secrets in addresses stay out of the request log
import logredact

check(
    "an invite link in a logged address is hidden",
    logredact.redact("GET /invite/AbC123-_xyzSECRET HTTP/1.1") == "GET /invite/[hidden] HTTP/1.1",
)
check(
    "a join request id is hidden, a query string after it too",
    logredact.redact("/api/join/0123456789abcdef0123456789abcdef/withdraw")
    == "/api/join/[hidden]/withdraw",
)
check(
    "other addresses are left alone",
    logredact.redact("/api/tracks/5/audio?x=1") == "/api/tracks/5/audio?x=1"
    and logredact.redact("/api/join") == "/api/join",
)
cfg = logredact.log_config()
check(
    "the access log's handler has the filter",
    cfg["handlers"]["access"].get("filters") == ["redact"]
    and cfg.get("filters", {}).get("redact", {}).get("()") == "logredact.Redact",
)
logging.config.dictConfig(cfg)
stream = io.StringIO()
access = logging.getLogger("uvicorn.access")
access.handlers[0].stream = stream
access.info(
    '%s - "%s %s HTTP/%s" %d',
    "10.0.0.5:5555",
    "GET",
    "/invite/SUPERSECRETTOKEN0123456789abcdefghijklmnop",
    "1.1",
    200,
)
check(
    "through uvicorn's own logger the token is not written, the rest is",
    "SUPERSECRET" not in stream.getvalue()
    and "/invite/[hidden]" in stream.getvalue()
    and "200" in stream.getvalue(),
    stream.getvalue(),
)
src = pathlib.Path("tlsconf.py").read_text(encoding="utf-8")
check(
    "the server starts uvicorn with that log setup on every port",
    src.count("log_config=log_config") == 3 and "logredact.log_config()" in src,
)

import shutil

shutil.rmtree(tmp, ignore_errors=True)
finish()
