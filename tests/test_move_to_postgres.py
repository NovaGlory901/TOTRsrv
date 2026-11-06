"""The Move to PostgreSQL flow: the one-time helper (tools/move_to_postgres.py) and the admin API
that drives it (dbmove.py).

What can be checked without a PostgreSQL server is checked here: the request rules, the .env
rewrite, setting the SQLite file aside, that secrets never leave the helper, the copy and the counts
(SQLite to SQLite, with the same import code), the API's progress and failure handling through the
real sign-in gate with a stand-in helper, the pause while copying, and the page's script. Talking to
a real PostgreSQL (creating the database and roles) is checked by deploy/lab-test.sh (`move`)."""

import io
import json
import os
import pathlib
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
sys.path.insert(0, str(PF / "tools"))
os.chdir(PF)
TMP = pathlib.Path(tempfile.mkdtemp(prefix="totr-move-"))
os.environ.update(
    DB_ENGINE="sqlite",
    TOTR_DATA_DIR=str(TMP / "data"),
    MAINT_REPORT=str(TMP / "reports" / "maintenance.json"),
    COLLECTOR_DIR=str(TMP / "reports"),
    APP_SETTINGS=str(TMP / "reports" / "app_settings.json"),
    TOTR_ENV_FILE=str(TMP / ".env"),
    DB_AUTH_USER="x",
    DB_AUTH_PASSWORD="y",
)
os.environ.pop("INVOCATION_ID", None)

import migrate_db
import move_to_postgres as mv
from check import check, finish
from fastapi.testclient import TestClient

import dbmove
import localdb
import main
import moving
from auth import permissions, service

GOOD = {
    "mode": "move",
    "host": "db.example.org",
    "port": "5432",
    "dbname": "totr",
    "sslmode": "require",
    "roles": "several",
    "admin_user": "postgres",
    "admin_password": "s3cret-admin-pw",
}


def refused(**change):
    try:
        mv.validate({**GOOD, **change})
    except mv.MoveError as e:
        return str(e)
    return None


# 1. what is asked
ok = mv.validate(GOOD)
check(
    "a good request passes and is cleaned",
    ok["host"] == "db.example.org" and ok["dbname"] == "totr" and ok["roles"] == "several",
)
for what, change in {
    "a bad mode": {"mode": "drop"},
    "a host with a space": {"host": "a b"},
    "a host with a quote": {"host": "x';--"},
    "an empty host": {"host": ""},
    "a database name with a dash": {"dbname": "my-db"},
    "a database name that starts with a digit": {"dbname": "1db"},
    "a database name with a quote": {"dbname": 'a"b'},
    "a too long database name": {"dbname": "a" * 42},
    "a port that is not a number": {"port": "54x2"},
    "a port out of range": {"port": "70000"},
    "an unknown encryption mode": {"sslmode": "maybe"},
    "an unknown role setup": {"roles": "many"},
    "no administrator name": {"admin_user": ""},
    "no administrator password": {"admin_password": ""},
    "a control character in the account name": {"admin_user": "pg\nadmin"},
}.items():
    check("refused: " + what, refused(**change) is not None)
try:
    mv.validate("not an object")
    check("a request that is not an object is refused", False)
except mv.MoveError:
    check("a request that is not an object is refused", True)
check("a socket path is a valid host", refused(host="/run/postgresql") is None)
check(
    "role names start with the database's name (roles are shared by the whole server)",
    set(mv.role_names(ok).values())
    == {"totr_read", "totr_write", "totr_maint", "totr_auth", "totr_mixtape"},
)
check(
    "one-role setup uses one name for every purpose",
    set(mv.role_names({**ok, "roles": "single"}).values()) == {"totr_app"},
)

# 2. .env
OLD = "# my settings\nSITE_NAME=Home\nDB_ENGINE=sqlite\nexport DB_HOST=old.example\nDB_PASSWORD=old\n#DB_USER=commented\nMUSIC_ROOT=/srv/music\nDB_SINGLE_ROLE=true\n"
names = mv.role_names(ok)
pw = {n: "pw-" + n for n in set(names.values())}
settings = mv.env_settings(ok, names, pw)
text = mv.rewrite_env(OLD, settings)
check(
    "other settings and comments are kept",
    "SITE_NAME=Home" in text
    and "MUSIC_ROOT=/srv/music" in text
    and "# my settings" in text
    and "#DB_USER=commented" in text,
)
check(
    "every old database line is replaced (also an exported one)",
    "old.example" not in text and "DB_PASSWORD=old" not in text and "DB_ENGINE=sqlite" not in text,
)
check(
    "a leftover single-role flag cannot linger when the new setup has several roles",
    "DB_SINGLE_ROLE" not in text,
)
lines = dict(l.split("=", 1) for l in text.splitlines() if "=" in l and not l.startswith("#"))
check(
    "the new settings name the server, the database and encryption",
    lines["DB_ENGINE"] == "postgres"
    and lines["DB_HOST"] == "db.example.org"
    and lines["DB_NAME"] == "totr"
    and lines["DB_SSLMODE"] == "require"
    and lines["DB_PORT"] == "5432",
)
check(
    "each purpose gets its own role and password",
    lines["DB_USER"] == "totr_read"
    and lines["DB_AUTH_USER"] == "totr_auth"
    and lines["DB_MIXTAPE_USER"] == "totr_mixtape"
    and lines["DB_WRITE_USER"] == "totr_write"
    and lines["DB_MAINT_USER"] == "totr_maint"
    and lines["DB_AUTH_PASSWORD"] == "pw-totr_auth",
)
single = mv.env_settings(
    {**ok, "roles": "single"}, mv.role_names({**ok, "roles": "single"}), {"totr_app": "pw-one"}
)
check(
    "one-role setup writes the single-role flag and one login",
    single["DB_SINGLE_ROLE"] == "true"
    and single["DB_USER"] == "totr_app"
    and "DB_AUTH_USER" not in single,
)
check(
    "the administrator account is never written to .env",
    "DB_ADMIN" not in text and "postgres\n" not in text.replace("DB_ENGINE=postgres", ""),
)
check("the administrator's password is nowhere in the new settings", "s3cret-admin-pw" not in text)

env = TMP / "envtest" / ".env"
env.parent.mkdir()
env.write_text(OLD)
backup = mv.write_env(env, settings)
check(
    ".env is rewritten, owner-only, and the old one is kept (also owner-only)",
    "DB_ENGINE=postgres" in env.read_text()
    and stat.S_IMODE(env.stat().st_mode) == 0o600
    and backup.read_text() == OLD
    and stat.S_IMODE(backup.stat().st_mode) == 0o600,
)
check("no half-written .env.part is left", not env.with_name(".env.part").exists())
mv.restore_env(env, backup)
check("restore puts the old .env back exactly", env.read_text() == OLD and not backup.exists())
env.unlink()
backup = mv.write_env(env, settings)
check("with no .env before, there is no backup", backup is None and env.exists())
mv.restore_env(env, backup)
check("and restoring removes the new one", not env.exists())

# 3. setting the SQLite file aside
d = TMP / "aside"
d.mkdir()
(d / "totr.db").write_text("one")
(d / "totr.db-wal").write_text("w")
(d / "totr.db-shm").write_text("s")
kept = mv.set_sqlite_aside(d / "totr.db")
check(
    "totr.db becomes totr.db.before-postgres, with its side files, nothing deleted",
    kept.name == "totr.db.before-postgres"
    and kept.read_text() == "one"
    and (d / "totr.db.before-postgres-wal").exists()
    and not (d / "totr.db").exists(),
)
(d / "totr.db").write_text("two")
kept2 = mv.set_sqlite_aside(d / "totr.db")
check(
    "an older kept file is never overwritten",
    kept2.name == "totr.db.before-postgres-2"
    and kept.read_text() == "one"
    and kept2.read_text() == "two",
)

# 4. secrets stay inside
buf = io.StringIO()
real = sys.stdout
sys.stdout = buf
out = mv.Out(hide=["s3cret-admin-pw"])
out.add_secret('gen/er"ated-pw')
out.send(
    {
        "event": "result",
        "ok": False,
        "message": 'login failed for s3cret-admin-pw and gen/er"ated-pw',
    }
)
sys.stdout = real
check(
    "the output filter hides the administrator's password and generated ones (also JSON-escaped)",
    "s3cret" not in buf.getvalue() and "ated-pw" not in buf.getvalue() and "***" in buf.getvalue(),
    buf.getvalue(),
)
check(
    "the error text shown for a failed login hides secrets",
    "s3cret"
    not in mv.plain_error(
        RuntimeError("could not log in with s3cret-admin-pw\nmore"), ("s3cret-admin-pw",)
    ),
)
buf = io.StringIO()
sys.stdout = buf
code = mv.main(stdin=io.StringIO("this is not json"))
sys.stdout = real
res = json.loads(buf.getvalue().splitlines()[-1])
check(
    "garbage on standard input gives a plain refusal, not a crash",
    code == 1
    and res["event"] == "result"
    and res["ok"] is False
    and "Traceback" not in buf.getvalue(),
)
buf = io.StringIO()
sys.stdout = buf
code = mv.main(
    stdin=io.StringIO(
        json.dumps({**GOOD, "mode": "test", "host": "127.0.0.1", "port": "1", "sslmode": "disable"})
    )
)
sys.stdout = real
res = json.loads(buf.getvalue().splitlines()[-1])
check(
    "a test against a server that is not there says so, in words, without the password",
    code == 0
    and res["ok"] is False
    and res["checks"][0]["ok"] is False
    and "s3cret" not in buf.getvalue(),
    buf.getvalue()[:200],
)

# 5. copying and counting (the same import code, SQLite to SQLite)
src, dst = TMP / "src", TMP / "dst"
for folder in (src, dst):
    os.environ["TOTR_DATA_DIR"] = str(folder)
    localdb.ensure()
os.environ["TOTR_DATA_DIR"] = str(src)
k = sqlite3.connect(localdb.db_path())
tid = lambda slug: k.execute(
    "SELECT type_id FROM media_item_types WHERE item_type_slug = ?", (slug,)
).fetchone()[0]
for n in range(7):
    k.execute("INSERT INTO media_items (type_id, title) VALUES (?, ?)", (tid("song"), f"song {n}"))
k.execute("INSERT INTO media_items (type_id, title) VALUES (?, 'an artist')", (tid("artist"),))
k.execute("INSERT INTO media_items (type_id, title) VALUES (?, 'an album')", (tid("album"),))
k.execute(
    "INSERT INTO totr_users (user_id, username, password_hash, role) VALUES (5, 'ann', 'hash', 'admin')"
)
k.execute(
    "INSERT INTO totr_user_data (user_id, name, value, updated_ms) VALUES (5, 'theme', '{\"a\":1}', 1700000000000)"
)
k.execute("INSERT INTO totr_mixtapes (mixtape_id, user_id, name) VALUES (1, 5, 'road')")
k.execute("INSERT INTO totr_mixtape_tracks (mixtape_id, track_id, position) VALUES (1, 1, 1)")
k.commit()
k.close()
with localdb.connect() as c:
    before = mv.count_local(c)
    data = migrate_db.export_data(c)
check(
    "the export carries each person's synced settings (they used to be left behind)",
    len(data["tables"]["user_data"]) == 1 and data["tables"]["user_data"][0]["name"] == "theme",
)
check(
    "the numbers shown on the page come from the same counts",
    mv.summary(before) == {"artists": 1, "albums": 1, "songs": 7, "accounts": 1, "playlists": 1},
    mv.summary(before),
)
os.environ["TOTR_DATA_DIR"] = str(dst)
ticks = []
with localdb.connect() as c:
    migrate_db.import_data(c, data, progress=lambda done, total: ticks.append((done, total)))
    after = mv.count_local(c)
check(
    "everything arrives, and the counts of both sides match",
    mv.mismatches(before, after) == [] and after["user_data"] == 1 and after["mixtape_tracks"] == 1,
    mv.mismatches(before, after),
)
check(
    "progress is reported and ends at the total",
    ticks
    and ticks[-1][0] == ticks[-1][1]
    and ticks[-1][1] == sum(len(v) for v in data["tables"].values()),
    ticks[-3:],
)
check(
    "a count that differs is named, with both numbers",
    mv.mismatches({"items": 5, "type:song": 4}, {"items": 5, "type:song": 3})
    == ["songs: 4 in SQLite, 3 in PostgreSQL"],
)
old_export = {**data, "tables": {k_: v for k_, v in data["tables"].items() if k_ != "user_data"}}
os.environ["TOTR_DATA_DIR"] = str(TMP / "dst2")
localdb.ensure()
with localdb.connect() as c:
    check(
        "an export made before settings were moved still imports",
        migrate_db.import_data(c, old_export)["user_data"] == 0,
    )
os.environ["TOTR_DATA_DIR"] = str(TMP / "data")

# 6. the API, through the real gate, with a stand-in helper
HELPER_LOG = TMP / "helper-log.json"
GO = TMP / "go"
FAKE = TMP / "fake_helper.py"
FAKE.write_text("""
import json, os, sys, time, pathlib
raw = sys.stdin.read(); req = json.loads(raw)
pathlib.Path(os.environ["FAKE_LOG"]).write_text(json.dumps({"argv": sys.argv, "env_has_password": any(req["admin_password"] in v for v in os.environ.values()), "stdin": req}))
def send(o): print(json.dumps(o), flush=True)
if req["mode"] == "test":
    send({"event": "result", "ok": req["host"] != "bad.example", "checks": [{"what": "connected", "ok": True}, {"what": "this account may create databases", "ok": req["host"] != "bad.example"}]}); sys.exit(0)
if req["host"] == "crash.example":
    sys.stderr.write("boom " + req["admin_password"]); sys.exit(3)
send({"event": "step", "name": "database and roles made", "state": "doing"})
send({"event": "progress", "percent": 10})
while not pathlib.Path(os.environ["FAKE_GO"]).exists(): time.sleep(0.05)
send({"event": "step", "name": "database and roles made", "state": "done"})
send({"event": "progress", "percent": 100})
if req["host"] == "fail.example":
    send({"event": "result", "ok": False, "message": "The counts did not match: songs: 7 in SQLite, 5 in PostgreSQL"}); sys.exit(1)
if req["mode"] == "rehearse":
    send({"event": "result", "ok": True, "rehearsal": True, "before": {"songs": 7}, "after": {"songs": 7}}); sys.exit(0)
send({"event": "result", "ok": True, "before": {"songs": 7}, "after": {"songs": 7}, "kept_file": "/x/totr.db.before-postgres", "env_backup": "/x/.env.before-postgres", "roles": ["totr_read"]})
""")
os.environ["FAKE_LOG"], os.environ["FAKE_GO"] = str(HELPER_LOG), str(GO)
dbmove.HELPER = FAKE
dbmove.RESTART_AFTER = 0


def user(name, role, perms=()):
    return {
        "user_id": 1,
        "username": name,
        "role": role,
        "permissions": permissions.effective(role, ",".join(perms)),
        "csrf": "tok-" + name,
        "token_hash": "h-" + name,
    }


SESSIONS = {
    "admin": user("admin", "admin"),
    "member": user("member", "member"),
    "maint": user("maint", "member", ("maintenance", "collector", "users", "settings", "upload")),
}
service.configured = lambda: True
service.validate_session = lambda token: SESSIONS.get(token)
plain = TestClient(main.app)  # http://testserver: not a private channel
secure = TestClient(main.app, base_url="https://testserver")  # https
as_ = lambda who: {"cookie": f"totr_session={who}"}


def call(client, method, path, who="admin", body=None):
    headers = {**as_(who), "X-CSRF-Token": SESSIONS.get(who, {}).get("csrf", "")}
    if method == "GET":
        return client.get(path, headers=headers)
    return client.post(path, headers=headers, json=body if body is not None else {})


def wait_stage(stage, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if call(secure, "GET", "/api/admin/database/move").json()["stage"] == stage:
            return True
        time.sleep(0.05)
    return False


FORM = {
    "host": "db.example.org",
    "port": "5432",
    "dbname": "totr",
    "sslmode": "require",
    "roles": "several",
    "admin_user": "postgres",
    "admin_password": "s3cret-admin-pw",
}
URLS = [
    "/api/admin/database/move/test",
    "/api/admin/database/move/start",
    "/api/admin/database/move/dismiss",
]

check(
    "anonymous: the status and every action are locked",
    plain.get("/api/admin/database/move").status_code == 401
    and all(plain.post(u, json={}).status_code in (401, 403) for u in URLS),
)
for who in ("member", "maint"):
    check(
        f"{who} (not the admin role): refused everywhere",
        call(secure, "GET", "/api/admin/database/move", who).status_code == 403
        and all(
            call(secure, "POST", u, who, {**FORM, "mode": "move"}).status_code == 403 for u in URLS
        ),
    )
s = call(secure, "GET", "/api/admin/database/move").json()
check(
    "the admin sees the engine, what would be copied, and that the page's channel is private",
    s["engine"] == "sqlite"
    and s["stage"] == "idle"
    and s["private_channel"] is True
    and set(s["counts"]) == {"artists", "albums", "songs", "accounts", "playlists"},
)
check(
    "over plain http from another machine the page is told the channel is not private",
    call(plain, "GET", "/api/admin/database/move").json()["private_channel"] is False,
)
r = call(plain, "POST", "/api/admin/database/move/test", body=FORM)
check(
    "the administrator's password is refused over plain http (nothing was run)",
    r.status_code == 400 and "unencrypted" in r.json()["detail"] and not HELPER_LOG.exists(),
    r.text,
)
r = call(plain, "POST", "/api/admin/database/move/start", body={**FORM, "mode": "move"})
check(
    "so is starting a move",
    r.status_code == 400 and not moving.active() and not HELPER_LOG.exists(),
)

r = call(secure, "POST", "/api/admin/database/move/test", body=FORM)
log = json.loads(HELPER_LOG.read_text())
check(
    "test: the helper's findings come back",
    r.status_code == 200
    and r.json()["ok"] is True
    and r.json()["checks"][0]["what"] == "connected",
    r.text,
)
check(
    "the password reached the helper on standard input only: not in its arguments, not in its environment",
    log["stdin"]["admin_password"] == "s3cret-admin-pw"
    and "s3cret-admin-pw" not in " ".join(log["argv"])
    and log["env_has_password"] is False,
    log,
)
check(
    "test: a failing finding makes ok false",
    call(
        secure, "POST", "/api/admin/database/move/test", body={**FORM, "host": "bad.example"}
    ).json()["ok"]
    is False,
)
check(
    "test: junk in the form is refused by the server's own checks before anything runs",
    call(
        secure, "POST", "/api/admin/database/move/test", body={**FORM, "host": "x" * 300}
    ).status_code
    == 422,
)

r = call(secure, "POST", "/api/admin/database/move/start", body={**FORM, "mode": "explode"})
check("start: an unknown mode is refused", r.status_code == 400)

GO.unlink(missing_ok=True)
r = call(secure, "POST", "/api/admin/database/move/start", body={**FORM, "mode": "rehearse"})
check("start (rehearse): accepted", r.status_code == 200 and r.json() == {"started": True}, r.text)
check(
    "while it runs the status shows the steps and progress",
    (
        lambda s_: (
            s_["stage"] == "running"
            and s_["mode"] == "rehearse"
            and s_["percent"] in (0, 10)
            and s_["counts"] is None
        )
    )(call(secure, "GET", "/api/admin/database/move").json()),
)
time.sleep(0.4)
s = call(secure, "GET", "/api/admin/database/move").json()
check(
    "the helper's progress and steps show up",
    s["percent"] == 10 and s["steps"] == [{"name": "database and roles made", "state": "doing"}],
    s,
)
check(
    "the pause is on: changes are refused with a plain message, reading still works",
    moving.active()
    and call(secure, "POST", "/api/auth/logout").status_code == 503
    and "moving"
    in call(secure, "POST", "/api/auth/logout")
    .json()
    .get("error", call(secure, "POST", "/api/auth/logout").text)
    .lower()
    and call(secure, "GET", "/api/admin/database/move").status_code == 200,
)
check(
    "anonymous requests that change things are paused too (sign-in included)",
    plain.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code == 503,
)
check(
    "a second start while one runs is refused",
    call(
        secure, "POST", "/api/admin/database/move/start", body={**FORM, "mode": "move"}
    ).status_code
    == 409,
)
check(
    "a test while one runs is refused",
    call(secure, "POST", "/api/admin/database/move/test", body=FORM).status_code == 409,
)
check(
    "the report cannot be dismissed mid-run",
    call(secure, "POST", "/api/admin/database/move/dismiss").status_code == 409,
)
GO.write_text("go")
check("it finishes", wait_stage("done"))
s = call(secure, "GET", "/api/admin/database/move").json()
check(
    "a rehearsal ends with a rehearsal report, and the pause is lifted",
    s["result"]["rehearsal"] is True and moving.active() is False and s["percent"] == 100,
)
check(
    "and it keeps nothing: no saved move report, engine unchanged",
    s["engine"] == "sqlite" and not dbmove._result_file().exists(),
)
check("no response ever held the password", "s3cret" not in json.dumps(s))
check(
    "dismiss clears the report",
    call(secure, "POST", "/api/admin/database/move/dismiss").status_code == 200
    and call(secure, "GET", "/api/admin/database/move").json()["stage"] == "idle",
)

GO.unlink(missing_ok=True)
call(
    secure,
    "POST",
    "/api/admin/database/move/start",
    body={**FORM, "host": "fail.example", "mode": "move"},
)
GO.write_text("go")
check(
    "a failed move is reported with the reason",
    wait_stage("failed")
    and "did not match" in call(secure, "GET", "/api/admin/database/move").json()["message"],
)
check(
    "and the pause is lifted again, the report is not saved, TOTR is not restarted",
    moving.active() is False and not dbmove._result_file().exists(),
)
call(secure, "POST", "/api/admin/database/move/dismiss")

call(
    secure,
    "POST",
    "/api/admin/database/move/start",
    body={**FORM, "host": "crash.example", "mode": "move"},
)
check(
    "a helper that dies without a result is reported, and its error text never shows the password",
    wait_stage("failed")
    and "s3cret" not in json.dumps(call(secure, "GET", "/api/admin/database/move").json())
    and "stopped unexpectedly" in call(secure, "GET", "/api/admin/database/move").json()["message"],
)
check("a crash lifts the pause as well", moving.active() is False)
call(secure, "POST", "/api/admin/database/move/dismiss")

# the real move: restart under a service manager, or ask for a manual restart
killed = []
real_kill = dbmove.os.kill
dbmove.os.kill = lambda pid, sig: killed.append((pid, sig))
os.environ["INVOCATION_ID"] = "abc"
GO.unlink(missing_ok=True)
call(secure, "POST", "/api/admin/database/move/start", body={**FORM, "mode": "move"})
GO.write_text("go")
check("a real move finishes with its report", wait_stage("done"))
time.sleep(0.5)
s = call(secure, "GET", "/api/admin/database/move").json()
check(
    "the report names the file kept and the old settings kept",
    s["result"]["kept_file"].endswith("totr.db.before-postgres")
    and s["result"]["env_backup"].endswith(".env.before-postgres"),
)
check(
    "under a service manager TOTR asks to be restarted (SIGTERM to itself)",
    s["restart"] == "automatic" and killed and killed[0][0] == os.getpid(),
    killed,
)
check(
    "the report is saved so it survives that restart, and holds no password",
    dbmove._result_file().exists() and "s3cret" not in dbmove._result_file().read_text(),
)
check(
    "until then the pause stays up (nothing may be written to the file that was set aside)",
    moving.active() is True,
)
moving.set_active(False)
call(secure, "POST", "/api/admin/database/move/dismiss")
check("dismiss removes the saved report", not dbmove._result_file().exists())
os.environ.pop("INVOCATION_ID")
killed.clear()
GO.unlink(missing_ok=True)
call(secure, "POST", "/api/admin/database/move/start", body={**FORM, "mode": "move"})
GO.write_text("go")
wait_stage("done")
time.sleep(0.3)
check(
    "without a service manager it does not kill itself and asks for a manual restart",
    not killed and call(secure, "GET", "/api/admin/database/move").json()["restart"] == "manual",
)
moving.set_active(False)
dbmove.os.kill = real_kill
dbmove._state.update(stage="idle", result=None)

# 7. the schedulers and the page
src_text = lambda p: (PF / p).read_text(encoding="utf-8")
check(
    "the collector's and the spiders' schedulers stand still during a move",
    "moving.active()" in src_text("collector/scheduler.py")
    and "moving.active()" in src_text("spiders/scheduler.py"),
)
page = src_text("templates/database.html")
script = page.split("<script>", 1)[1]
check(
    "the page puts nothing into itself as HTML",
    not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(", script),
)
check(
    "the page never stores the administrator's password (no local or session storage, no cookie)",
    not re.search(r"localStorage|sessionStorage|document\.cookie|indexedDB", script),
)
check(
    "the password field is a password field and has no autofill hint to remember it",
    "'password'" in script and "autocomplete = 'off'" in script,
)
check(
    "the old .env-lines panel and its copy-paste commands are gone",
    "Show the .env lines" not in page and "TARGET_DB_PASSWORD" not in page,
)
check(
    "the server never imports the tool that changes the layout (only the helper process does)",
    not re.search(
        r"^\s*(import|from)\s+(migrate|migrate_db|move_to_postgres)\b", src_text("dbmove.py"), re.M
    ),
)
check(
    "the helper is started with the password on standard input only",
    "stdin=subprocess.PIPE" in src_text("dbmove.py")
    and "admin_password"
    not in re.sub(
        r"payload.get\(\"admin_password\"[^)]*\)|payload\[\"admin_password\"\] = \"\"",
        "",
        src_text("dbmove.py").split("def _run_helper", 1)[1].split("proc.stdin.write", 1)[0],
    ),
)

shutil.rmtree(TMP, ignore_errors=True)
finish()
