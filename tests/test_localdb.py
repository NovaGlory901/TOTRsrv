"""The built-in local database (SQLite) for people without their own PostgreSQL: first-run copy of
the template, the layout matching the PostgreSQL one, the SQL translations, every music route and
the sign-in flow running on it, and how db.py picks the engine. Everything happens in a temporary
folder; no real database is touched."""

import os
import pathlib
import sqlite3
import sys
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
sys.path.insert(0, str(PF / "tools"))
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["DB_ENGINE"] = "sqlite"
os.environ["TOTR_DATA_DIR"] = str(tmp / "data")
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["APP_SETTINGS"] = str(tmp / "reports" / "app_settings.json")

# 1. the template and the first-run copy
import build_local_template
from check import check, finish

import db
import localdb

fresh = build_local_template.build(tmp / "fresh.db")
check(
    "the committed template is what the migrations build (rebuild it after changing one)",
    sorted(
        sqlite3.connect(fresh)
        .execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
        .fetchall()
    )
    == sorted(
        sqlite3.connect(f"file:{localdb.TEMPLATE}?mode=ro", uri=True)
        .execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
        .fetchall()
    ),
)
check(
    "the template carries the layout version",
    localdb.file_version(localdb.TEMPLATE) == localdb.SCHEMA_VERSION,
)
check(
    "no data folder yet, so nothing is created before it is needed", not localdb.data_dir().exists()
)
path = localdb.ensure()
check(
    "first run copies the template into the data folder",
    path.exists() and path == localdb.db_path() and not path.with_name("totr.db.part").exists(),
)
conn = sqlite3.connect(path)
check(
    "the copy has the media types and relation types the app needs",
    {r[0] for r in conn.execute("SELECT item_type_slug FROM media_item_types")}
    >= {"song", "artist", "album"}
    and {r[0] for r in conn.execute("SELECT relation_slug FROM media_relation_types")}
    >= {"performed_by", "album_by", "track_on_album"},
)
check(
    "the copy holds no accounts and no music",
    conn.execute("SELECT count(*) FROM totr_users").fetchone()[0] == 0
    and conn.execute("SELECT count(*) FROM media_items").fetchone()[0] == 0,
)
conn.execute("INSERT INTO media_items (type_id, title) VALUES (1, 'keep me')")
conn.commit()
conn.close()
localdb.ensure()
check(
    "a second start never overwrites the existing database",
    sqlite3.connect(path).execute("SELECT title FROM media_items").fetchone()[0] == "keep me",
)
sqlite3.connect(path).execute("DELETE FROM media_items").connection.commit()

check(
    "info() reports the file, versions and size",
    localdb.info()["schema_version"] == localdb.SCHEMA_VERSION and localdb.info()["size_bytes"] > 0,
)
real_version = localdb.sqlite3.sqlite_version_info
localdb.sqlite3.sqlite_version_info = (3, 30, 0)
try:
    localdb.ensure()
    check("a SQLite too old for the JSON operator is refused with a clear message", False)
except localdb.LocalDbError as e:
    check(
        "a SQLite too old for the JSON operator is refused with a clear message", "3.38" in str(e)
    )
localdb.sqlite3.sqlite_version_info = real_version
other = tmp / "wrongversion"
os.environ["TOTR_DATA_DIR"] = str(other)
other.mkdir()
c = sqlite3.connect(other / "totr.db")
c.execute("PRAGMA user_version = 99")
c.execute("CREATE TABLE x (a)")
c.commit()
c.close()
try:
    localdb.connect()
    check("a database file with another layout version is refused, not used", False)
except localdb.LocalDbError as e:
    check("a database file with another layout version is refused, not used", "99" in str(e))
os.environ["TOTR_DATA_DIR"] = str(tmp / "data")

# 2. the SQL translations
check(
    "lite: schema names become table prefixes",
    localdb.lite("SELECT * FROM totr.v_tracks JOIN media.items")
    == "SELECT * FROM totr_v_tracks JOIN media_items",
)
check(
    "lite: ANY(list) becomes IN",
    localdb.lite("WHERE x = ANY(%s::int[])") == "WHERE x IN %s"
    and localdb.lite("WHERE x = ANY(%s)") == "WHERE x IN %s",
)
check(
    "lite: string_agg(DISTINCT) becomes the local aggregate",
    localdb.lite("string_agg(DISTINCT ar.name, ', ') AS a") == "string_agg_distinct(ar.name) AS a",
)
with db.get_conn() as c:
    c.execute(
        "INSERT INTO media_items (type_id, title) VALUES (1, 'a'), (1, 'b'), (1, 'c')"
    ) if False else None
    rows = c.execute(
        "SELECT string_agg(DISTINCT n, ', ') AS s FROM (SELECT 'b' AS n UNION ALL SELECT 'a' UNION ALL SELECT 'b') GROUP BY 1=1"
    ).fetchall()
check("string_agg(DISTINCT): distinct, sorted, joined with ', '", rows == [{"s": "a, b"}], rows)
with db.get_conn() as c:
    check(
        "ANY with a list keeps those ids",
        [r["x"] for r in c.execute("SELECT 1 AS x WHERE 1 = ANY(%s::int[])", ([1, 2],)).fetchall()]
        == [1],
    )
    check(
        "NOT ANY with an empty list is true, like PostgreSQL",
        c.execute("SELECT 1 AS x WHERE NOT (1 = ANY(%s::int[]))", ([],)).fetchall() == [{"x": 1}],
    )
try:
    with db.get_conn() as c:
        c.execute("SELECT 1 WHERE 1 = 'x' OR 2 = 2", ("oops",))
    check("a value given without a %s placeholder is refused", False)
except AssertionError:
    check("a value given without a %s placeholder is refused", True)

# 3. the app's music routes on the local database
import sqlite_layout as layout

DB = localdb.db_path()
ar = layout.add_item(DB, "artist", "Kanal Vier")
ar2 = layout.add_item(DB, "artist", "Gast")
al = layout.add_item(DB, "album", "Cold Wave", year=2020)
s1 = layout.add_item(DB, "song", "Eis", file_path="/m/eis.mp3", track_number=1)
s2 = layout.add_item(DB, "song", "Single", file_path="/m/single.mp3")
layout.relate(DB, al, "album_by", ar)
layout.relate(DB, s1, "track_on_album", al)
layout.relate(DB, s1, "performed_by", ar)
layout.relate(DB, s1, "performed_by", ar2)
layout.relate(DB, s2, "performed_by", ar)
import library_search
import main
from mixtapes import web as mixtapes_web

artists = main.list_artists()
check(
    "artists: listed with their song counts",
    {a["name"]: a["track_count"] for a in artists} == {"Kanal Vier": 2, "Gast": 1},
    artists,
)
check(
    "albums: an artist's albums",
    [a["title"] for a in main.list_albums(ar)] == ["Cold Wave"],
    main.list_albums(ar),
)
check(
    "album tracks: with every credited artist joined",
    sorted(main.list_tracks(al)[0]["artists"].split(", ")) == ["Gast", "Kanal Vier"],
    main.list_tracks(al),
)
check(
    "artist songs: all of them, loose ones included",
    sorted(s["title"] for s in main.list_artist_songs(ar)) == ["Eis", "Single"],
)
check(
    "singles: the loose song",
    [t["title"] for t in main.list_discography(ar)] == ["Single"],
    main.list_discography(ar),
)
check("compilations: the query runs", isinstance(main.list_compilations(), list))
radio = main.radio_next("")
check("radio: a random song comes back", radio["track_id"] in (s1, s2), radio)
check("radio: the exclude list is honoured", main.radio_next(str(s1))["track_id"] == s2)
check(
    "radio: odd exclude ids are ignored instead of crashing the query",
    main.radio_next("²,99999999999,abc,-1")["track_id"] in (s1, s2),
)
check("radio media: the query runs", isinstance(main.radio_media(), list))
with db.get_conn() as c:
    check(
        "library stats: counts",
        main.library_counts(c) == {"artists": 2, "albums": 1, "tracks": 2},
        main.library_counts(c),
    )
check("search index: built from the views", len(library_search.get_index()["songs"]) == 2)
found = mixtapes_web.lookup_tracks([s1, 999999])
check(
    "mixtape lookup: known ids with titles, unknown absent",
    set(found) == {s1} and found[s1]["artists"] == "Gast, Kanal Vier",
    found,
)

# 4. sign-in, sessions, mixtapes and the cascades on the real template
from fastapi.testclient import TestClient

from auth import service

PW = "correct horse battery staple"
check(
    "accounts are enabled without any role variables (local mode)",
    db.auth_enabled() and db.maint_enabled() and db.write_enabled() and db.mixtape_enabled(),
)
uid = service.create_user("Nova", PW, "admin")
cl = TestClient(main.app)
r = cl.post("/api/auth/login", json={"username": "Nova", "password": PW})
check(
    "sign-in works on the local database",
    r.status_code == 200 and r.json()["role"] == "admin",
    r.text,
)
check(
    "a signed-in request reads the library",
    cl.get("/api/artists").status_code == 200 and len(cl.get("/api/artists").json()) == 2,
)
check("the session survives the next request", cl.get("/api/auth/me").json()["username"] == "Nova")
csrf = cl.get("/api/auth/me").json().get("csrf_token") or cl.get("/api/auth/me").json().get("csrf")
check(
    "the admin health page reports the database as fine",
    cl.get("/api/health").json()["checks"]["database"]["ok"] is True,
)
check(
    "lite: plain string_agg becomes group_concat",
    localdb.lite("string_agg(ar.name, ', ')") == "group_concat(ar.name, ', ')",
)
with db.get_mixtape_conn() as c:
    c.execute(
        "INSERT INTO totr.mixtapes (user_id, name, created_at, updated_at) VALUES (%s, 'Mine', 'x', 'x')",
        (uid,),
    )
    mid = c.execute("SELECT mixtape_id FROM totr.mixtapes").fetchone()["mixtape_id"]
    c.execute(
        "INSERT INTO totr.mixtape_tracks (mixtape_id, track_id, position, added_at) VALUES (%s, %s, 1, 'x')",
        (mid, s1),
    )
with db.get_conn() as c:
    c.execute("DELETE FROM media.item_relations WHERE from_item_id = %s", (s1,))
    c.execute("DELETE FROM media.items WHERE item_id = %s", (s1,))
with db.get_mixtape_conn() as c:
    check(
        "deleting a song removes it from mixtapes (the cascade is a real foreign key)",
        c.execute("SELECT count(*) AS n FROM totr.mixtape_tracks").fetchone()["n"] == 0,
    )
with db.get_auth_conn() as c:
    c.execute("DELETE FROM totr.users WHERE user_id = %s", (uid,))
with db.get_conn() as c:
    check(
        "deleting a user removes their sessions and mixtapes",
        c.execute("SELECT count(*) AS n FROM totr.sessions").fetchone()["n"] == 0
        and c.execute("SELECT count(*) AS n FROM totr.mixtapes").fetchone()["n"] == 0,
    )
try:
    with db.get_conn() as c:
        c.execute("INSERT INTO totr.users (username, password_hash) VALUES ('a', 'x')")
        c.execute("INSERT INTO totr.users (username, password_hash) VALUES ('A', 'x')")
    check("usernames stay unique ignoring case", False)
except sqlite3.IntegrityError:
    check("usernames stay unique ignoring case", True)
with db.get_conn() as c:
    check(
        "a failed transaction was rolled back",
        c.execute("SELECT count(*) AS n FROM totr.users").fetchone()["n"] == 0,
    )

# 5. which engine db.py picks, and the PostgreSQL settings
saved = {
    k: os.environ.get(k)
    for k in (
        "DB_ENGINE",
        "DB_HOST",
        "DB_NAME",
        "DB_PORT",
        "DB_SSLMODE",
        "DB_SINGLE_ROLE",
        "DB_USER",
        "DB_PASSWORD",
        "DB_AUTH_USER",
        "DB_AUTH_PASSWORD",
    )
}


def env(**kw):
    for k in saved:
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in kw.items() if v is not None})


env()
check("no DB_HOST: the local database", db.engine() == "sqlite")
env(DB_HOST="10.0.0.5")
check("a DB_HOST names a PostgreSQL server", db.engine() == "postgres")
env(DB_HOST="10.0.0.5", DB_ENGINE="sqlite")
check("DB_ENGINE can force the local database", db.engine() == "sqlite")
env(DB_HOST="/run/postgresql", DB_NAME="x")
check(
    "a socket path means no SSL",
    db.connect_settings("u", "p")["sslmode"] == "disable"
    and "port" not in db.connect_settings("u", "p"),
)
env(DB_HOST="db.example", DB_NAME="x", DB_PORT="5433")
s = db.connect_settings("u", "p")
check(
    "a network host requires SSL by default and takes a port",
    s["sslmode"] == "require" and s["port"] == 5433 and s["host"] == "db.example",
)
env(DB_HOST="db.example", DB_NAME="x", DB_SSLMODE="verify-full")
check("DB_SSLMODE overrides", db.connect_settings("u", "p")["sslmode"] == "verify-full")
env(DB_HOST="db.example", DB_USER="reader", DB_PASSWORD="pw")
check(
    "PostgreSQL: a feature without its own role is off",
    db.credentials("read") == ("reader", "pw")
    and db.credentials("auth") is None
    and not db.auth_enabled()
    and not db.maint_enabled(),
)
env(DB_HOST="db.example", DB_USER="one", DB_PASSWORD="pw", DB_SINGLE_ROLE="true")
check(
    "single-role setup: every feature uses the one role",
    all(db.credentials(p) == ("one", "pw") for p in db.ROLES) and db.auth_enabled(),
)
try:
    env(DB_HOST="db.example")
    db.get_auth_conn()
    check("an unconfigured role says which one is missing", False)
except RuntimeError as e:
    check("an unconfigured role says which one is missing", "auth" in str(e))
env(DB_ENGINE="sqlite")

# 6. the admin Database page
import dbadmin
from auth.limiter import LoginLimits

service.limits = LoginLimits()
service.create_user("Nova", PW, "admin")
service.create_user("Gast", PW, "member")
adm, mem = TestClient(main.app), TestClient(main.app)
adm.post("/api/auth/login", json={"username": "Nova", "password": PW})
mem.post("/api/auth/login", json={"username": "Gast", "password": PW})
check(
    "the Database page opens for an admin",
    adm.get("/admin/database").status_code == 200 and "Database" in adm.get("/admin/database").text,
)
check(
    "the Database page and report are closed to a member",
    mem.get("/admin/database", follow_redirects=False).status_code in (302, 303, 403)
    and mem.get("/api/admin/database").status_code == 403,
)
check(
    "the Database page and report are closed to anonymous visitors",
    TestClient(main.app).get("/api/admin/database").status_code == 401,
)
rep = adm.get("/api/admin/database").json()
check(
    "the report for the local database names the file and finds no problems",
    rep["engine"] == "sqlite" and rep["path"].endswith("totr.db") and rep["problems"] == [],
    rep,
)
check(
    "the Database page is linked in the admin navigation",
    'href="/admin/database"' in adm.get("/admin/users").text,
)
check("...but not for a member", 'href="/admin/database"' not in mem.get("/archive").text)
check(
    "the page has no inline handlers (the CSP would block them) and builds with textContent",
    "onclick=" not in adm.get("/admin/database").text
    and "innerHTML" not in adm.get("/admin/database").text,
)

# PostgreSQL report, with the connection faked to fail: a clear problem, and no password anywhere in
# it
env(DB_HOST="db.example", DB_NAME="x", DB_USER="reader", DB_PASSWORD="hunter2-secret")


def boom(purpose):
    raise OSError("no route to host")


real_connect, db._connect = db._connect, boom
rep = dbadmin.report()
db._connect = real_connect
check(
    "PostgreSQL report: a failed connection is reported as a problem",
    rep["engine"] == "postgres" and any("cannot connect" in p for p in rep["problems"]),
    rep["problems"],
)
check(
    "PostgreSQL report: a missing sign-in role is a problem, not 'everything checks out'",
    any("sign-in and accounts" in p for p in rep["problems"]),
    rep["problems"],
)
check("PostgreSQL report: never contains a password", "hunter2-secret" not in str(rep))
check(
    "PostgreSQL report: roles not set in .env are listed as off",
    [r["purpose"] for r in rep["roles"] if not r["configured"]]
    == ["write", "maint", "auth", "mixtape"],
    rep["roles"],
)
env(DB_ENGINE="sqlite")

# 7. moving the data (export, then import into an empty layout)
import json

import migrate_db

service.create_user("Third", PW, "member")
with db.get_conn() as c:
    uid2 = c.execute("SELECT user_id FROM totr.users WHERE username = 'Gast'").fetchone()["user_id"]
    c.execute(
        "INSERT INTO totr.mixtapes (user_id, name, created_at, updated_at) VALUES (%s, 'Road', 'x', 'x')",
        (uid2,),
    )
    mid2 = c.execute("SELECT mixtape_id FROM totr.mixtapes WHERE name = 'Road'").fetchone()[
        "mixtape_id"
    ]
    c.execute(
        "INSERT INTO totr.mixtape_tracks (mixtape_id, track_id, position, added_at) VALUES (%s, %s, 1, 'x')",
        (mid2, s2),
    )
with db.get_conn() as c:
    exported = migrate_db.export_data(c)
file = json.loads(json.dumps(exported))
check(
    "export: items, relations, users, mixtapes, synced settings, no sessions",
    set(file["tables"])
    == {"items", "relations", "users", "mixtapes", "mixtape_tracks", "user_data"}
    and len(file["tables"]["users"]) == 3,
    {k: len(v) for k, v in file["tables"].items()},
)
check(
    "export: carries the password hashes and not the passwords",
    "$argon2id$" in file["tables"]["users"][0]["password_hash"] and PW not in json.dumps(file),
)
target = tmp / "target"
os.environ["TOTR_DATA_DIR"] = str(target)
other_db = localdb.ensure()
tc = localdb.Conn(other_db)
counts = migrate_db.import_data(tc, file)
tc.c.commit()
check(
    "import: loads everything into an empty layout, ids kept",
    counts == {k: len(v) for k, v in file["tables"].items()}
    and [
        r["title"] for r in tc.execute("SELECT title FROM media.items ORDER BY item_id").fetchall()
    ]
    == [i["title"] for i in file["tables"]["items"]],
)
check(
    "import: the library reads the same afterwards",
    tc.execute("SELECT count(*) AS n FROM totr.v_tracks").fetchone()["n"] == 1
    and tc.execute("SELECT count(*) AS n FROM media.item_relations").fetchone()["n"]
    == len(file["tables"]["relations"])
    and tc.execute("SELECT count(*) AS n FROM totr.mixtape_tracks").fetchone()["n"] == 1,
)
check(
    "import: accounts keep their hashes (people sign in with the same passwords)",
    sorted(
        r["password_hash"] for r in tc.execute("SELECT password_hash FROM totr.users").fetchall()
    )
    == sorted(u["password_hash"] for u in file["tables"]["users"]),
)
try:
    migrate_db.import_data(tc, file)
    check("import: refuses a target that already holds TOTR data", False)
except migrate_db.MigrateError as e:
    check("import: refuses a target that already holds TOTR data", "already holds data" in str(e))
tc.close()
os.environ["TOTR_DATA_DIR"] = str(tmp / "target2")
empty = localdb.Conn(localdb.ensure())
bad = json.loads(json.dumps(file))
bad["tables"]["items"][0]["type"] = "podcast"
try:
    migrate_db.import_data(empty, bad)
    check("import: refuses types the target layout lacks", False)
except migrate_db.MigrateError as e:
    check("import: refuses types the target layout lacks", "podcast" in str(e))
try:
    migrate_db.import_data(empty, {"format": "other"})
    check("import: refuses a file that is not an export", False)
except migrate_db.MigrateError:
    check("import: refuses a file that is not an export", True)
empty.close()
os.environ["TOTR_DATA_DIR"] = str(tmp / "data")
out = tmp / "export.json"
check(
    "the export command writes a file from the configured database",
    migrate_db.main(["export", str(out)]) == 0
    and json.loads(out.read_text(encoding="utf8"))["format"] == "totr-export",
)
check(
    "the import command stops with a clear message when the target is not set",
    migrate_db.main(["import", str(out)]) == 1,
)

# 8. an existing database is recognised and never recreated
guard = tmp / "guard"
os.environ["TOTR_DATA_DIR"] = str(guard)
env(DB_HOST="db.example", DB_NAME="x", DB_USER="reader", DB_PASSWORD="pw")
real_connect, db._connect = db._connect, boom
dbadmin.report()
db._connect = real_connect
check(
    "with PostgreSQL configured, no local database file or folder is ever created",
    not guard.exists(),
)
env(DB_ENGINE="sqlite")
check(
    "the Database page's report does not create a missing local database",
    dbadmin.report()["exists"] is False and not guard.exists(),
)
check(
    "a missing local database is reported as not created yet",
    localdb.info()["schema_version"] is None and localdb.info()["size_bytes"] is None,
)
localdb.ensure()
first = localdb.db_path().stat().st_mtime_ns
c = sqlite3.connect(localdb.db_path())
c.execute("INSERT INTO media_items (type_id, title) VALUES (1, 'mine')")
c.commit()
c.close()
for _ in range(3):
    localdb.ensure()
    localdb.connect().close()
check(
    "an existing local database is found and left alone on every later start",
    sqlite3.connect(localdb.db_path()).execute("SELECT title FROM media_items").fetchone()[0]
    == "mine"
    and localdb.info()["exists"] is True,
)
(guard / "totr.db.part").write_bytes(b"half a copy")
localdb.ensure()
check(
    "a leftover half-copy never replaces or damages the real database",
    sqlite3.connect(localdb.db_path()).execute("SELECT count(*) FROM media_items").fetchone()[0]
    == 1,
)
os.environ["TOTR_DATA_DIR"] = str(tmp / "data")

# 9. the artist grid asks only for covers that exist
os.environ["TOTR_DATA_DIR"] = str(tmp / "data")
bare = layout.add_item(DB, "album", "No Cover Here")
covered = layout.add_item(DB, "album", "Has One", cover_art_path="/m/cover.jpg")
s_bare = layout.add_item(DB, "song", "S1", file_path="/m/s1.mp3")
s_cov = layout.add_item(DB, "song", "S2", file_path="/m/s2.mp3")
only, mixed = layout.add_item(DB, "artist", "Only Bare"), layout.add_item(DB, "artist", "Mixed")
layout.relate(DB, s_bare, "track_on_album", bare)
layout.relate(DB, s_cov, "track_on_album", covered)
layout.relate(DB, s_bare, "performed_by", only)
layout.relate(DB, s_bare, "performed_by", mixed)
layout.relate(DB, s_cov, "performed_by", mixed)
cov = {a["name"]: a["cover_album_id"] for a in main.list_artists()}
check(
    "artist grid: the picture is the first album that HAS a cover (not one without, which would 404 on every load)",
    cov["Mixed"] == covered,
    cov,
)
check(
    "artist grid: an artist whose albums have no cover gets none, so the page makes no request",
    cov["Only Bare"] is None,
    cov,
)

finish()
