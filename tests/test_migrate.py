"""Database versions and upgrades (migrate.py): SQLite always, and PostgreSQL on a throwaway cluster
when the PostgreSQL tools (initdb, pg_ctl, psql, pg_dump) are installed and this is not Windows
(otherwise the PostgreSQL part prints `skip`)."""

import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
sys.path.insert(0, str(PF / "tests"))
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["TOTR_DATA_DIR"] = str(tmp / "data")
for k in ("DB_HOST", "DB_ENGINE"):
    os.environ.pop(k, None)

from check import check, finish

import localdb
import migrate
import schemaver


def raises(fn):
    try:
        fn()
    except migrate.MigrateError as e:
        return str(e)
    return None


def objects(conn):
    return sorted(
        conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )


# 1. the upgrade files
lite_files, pg_files = migrate.files("sqlite"), migrate.files("postgres")
check(
    "both engines have the same numbers and names",
    [(m.version, m.name) for m in lite_files] == [(m.version, m.name) for m in pg_files],
    (lite_files, pg_files),
)
check(
    "the numbers start at 1 (they match the old PRAGMA user_version of local databases)",
    [m.version for m in lite_files][:2] == [1, 2],
)
check(
    "localdb's layout version is the newest SQLite upgrade",
    localdb.SCHEMA_VERSION == migrate.latest("sqlite"),
)

real_dir = schemaver.DIR
bad = tmp / "gap"
(bad / "sqlite").mkdir(parents=True)
shutil.copy(real_dir / "sqlite" / "0001_base.sql", bad / "sqlite")
(bad / "sqlite" / "0003_x.sql").write_text("SELECT 1;")
schemaver.DIR = bad
check("a gap in the numbers is refused", raises(lambda: migrate.files("sqlite")) is not None)
(bad / "sqlite" / "0003_x.sql").rename(bad / "sqlite" / "two.sql")
check("a file with the wrong name is refused", raises(lambda: migrate.files("sqlite")) is not None)
schemaver.DIR = real_dir

# 2. SQLite: a new database
c = sqlite3.connect(tmp / "fresh.db")
s = migrate.state(c)
check("an empty file is 'empty'", s["state"] == "empty" and s["current"] == 0, s)
done = migrate.upgrade(c)
check(
    "up builds everything, in order",
    done == [f"{m.version:04d}_{m.name}" for m in lite_files],
    done,
)
s = migrate.state(c)
check(
    "then it is 'current', and PRAGMA user_version agrees",
    s["state"] == "current"
    and c.execute("PRAGMA user_version").fetchone()[0] == migrate.latest("sqlite"),
    s,
)
check("up again does nothing", migrate.upgrade(c) == [])
check(
    "the ontology is seeded",
    c.execute("SELECT count(*) FROM media_item_types").fetchone()[0] == 5
    and c.execute("SELECT count(*) FROM media_relation_types").fetchone()[0] == 4,
)
fresh_objects = objects(c)
tpl = sqlite3.connect(f"file:{localdb.TEMPLATE}?mode=ro", uri=True)
check(
    "the committed template is what the migrations build (rebuild it after changing one: python tools/build_local_template.py)",
    objects(tpl) == fresh_objects,
)
check(
    "the template says which upgrades it has",
    [r[0] for r in tpl.execute("SELECT version FROM totr_schema_version ORDER BY version")]
    == [m.version for m in lite_files],
)

# the stand-in layout many other tests use must have the real columns
import sqlite_layout as layout

stand = sqlite3.connect(":memory:")
for part in (
    layout.MEDIA,
    layout.ACCOUNTS,
    layout.MIXTAPES,
    layout.USER_DATA,
    layout.JOIN_REQUESTS,
    layout.SONG_OFFERS,
):
    stand.executescript(part)
cols = lambda conn, t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
names = [
    r[0]
    for r in stand.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name LIKE 'totr\\_%' ESCAPE '\\' OR name LIKE 'media\\_%' ESCAPE '\\'"
    )
]
off = [t for t in names if cols(stand, t) != cols(c, t)]
check("tests/sqlite_layout.py has the same columns as the real layout", names and not off, off)
c.close()


# 3. SQLite: databases that already exist
def old_style(path, version):
    """A local database made before schema_version existed: the tables, and only PRAGMA user_version
    to say what it has."""
    k = sqlite3.connect(path)
    k.executescript(lite_files[0].sql)
    if version >= 2:
        k.executescript(lite_files[1].sql)
    if version >= 3:
        k.executescript(lite_files[2].sql)
    if version >= 4:
        k.executescript(lite_files[3].sql)
    k.execute(f"PRAGMA user_version = {version}")
    k.commit()
    return k


k = old_style(tmp / "old4.db", 4)
s = migrate.state(k)
check(
    "an older file at the newest version counts as current",
    s["state"] == "current" and s["legacy"],
    s,
)
check(
    "up records what it has and changes nothing else",
    migrate.upgrade(k) == []
    and [r[0] for r in k.execute("SELECT version FROM totr_schema_version")] == [1, 2, 3, 4],
)
k.close()
k = old_style(tmp / "old2.db", 2)
s = migrate.state(k)
check("an older file at version 2 is 'old'", s["state"] == "old" and s["current"] == 2, s)
check(
    "up adds the join requests and song offers tables",
    migrate.upgrade(k) == ["0003_join_requests", "0004_song_offers"]
    and k.execute("SELECT count(*) FROM totr_join_requests").fetchone()[0] == 0
    and k.execute("SELECT count(*) FROM totr_song_offers").fetchone()[0] == 0,
)
k.close()
k = old_style(tmp / "old1.db", 1)
s = migrate.state(k)
check("an older file at version 1 is 'old'", s["state"] == "old" and s["current"] == 1, s)
check(
    "up adds only what is missing",
    migrate.upgrade(k) == ["0002_user_data", "0003_join_requests", "0004_song_offers"]
    and k.execute("SELECT count(*) FROM totr_user_data").fetchone()[0] == 0,
)
k.close()

k = sqlite3.connect(tmp / "newer.db")
migrate.upgrade(k)
k.execute("INSERT INTO totr_schema_version VALUES (99, 'future', 'now')")
k.commit()
check("a database ahead of this TOTR is 'newer'", migrate.state(k)["state"] == "newer")
check("and up refuses to touch it", raises(lambda: migrate.upgrade(k)) is not None)
k.close()
k = sqlite3.connect(tmp / "gaps.db")
migrate.upgrade(k)
k.execute("DELETE FROM totr_schema_version WHERE version = 1")
k.commit()
check(
    "a hole in the recorded upgrades is reported, not guessed at",
    raises(lambda: migrate.state(k)) is not None,
)
k.close()

k = sqlite3.connect(tmp / "unversioned.db")
k.executescript(lite_files[0].sql)
k.commit()
s = migrate.state(k)
check("tables with no version are 'unversioned'", s["state"] == "unversioned", s)
check("up refuses until they are adopted", "adopt" in (raises(lambda: migrate.upgrade(k)) or ""))
check(
    "adopt records what is really there (version 1; user_data is missing)",
    migrate.adopt(k) == 1 and migrate.state(k)["state"] == "old",
)
check(
    "and up then adds the rest",
    migrate.upgrade(k) == ["0002_user_data", "0003_join_requests", "0004_song_offers"],
)
check(
    "adopting a database that is not unversioned is refused",
    raises(lambda: migrate.adopt(k)) is not None,
)
k.close()
k = sqlite3.connect(tmp / "partial.db")
k.execute("CREATE TABLE totr_users (user_id INTEGER)")
k.commit()
check(
    "adopt refuses a database that is missing TOTR's tables",
    "missing" in (raises(lambda: migrate.adopt(k)) or ""),
)
k.close()

# 4. SQLite: a failed upgrade leaves the database as it was
fail_dir = tmp / "failing"
(fail_dir / "sqlite").mkdir(parents=True)
shutil.copy(real_dir / "sqlite" / "0001_base.sql", fail_dir / "sqlite")
(fail_dir / "sqlite" / "0002_user_data.sql").write_text(
    "CREATE TABLE half_done (a INTEGER);\nINSERT INTO no_such_table VALUES (1);\n"
)
k = sqlite3.connect(tmp / "failing.db")
schemaver.DIR = fail_dir
msg = raises(lambda: migrate.upgrade(k))
schemaver.DIR = real_dir
check("a broken upgrade says so and names itself", msg is not None and "0002_user_data" in msg, msg)
check(
    "nothing of it stays: no half-made table, version 1 only",
    k.execute("SELECT count(*) FROM sqlite_master WHERE name = 'half_done'").fetchone()[0] == 0
    and [r[0] for r in k.execute("SELECT version FROM totr_schema_version")] == [1]
    and k.execute("PRAGMA user_version").fetchone()[0] == 1,
)
k.close()

# 5. what the running server checks at start

os.environ["DB_ENGINE"] = "sqlite"
check("no local database yet: fine, it is made on first use", schemaver.check_database()[0] == "ok")
localdb.db_path().parent.mkdir(parents=True, exist_ok=True)
k = sqlite3.connect(localdb.db_path())
migrate.upgrade(k)
k.close()
check("a current database: ok", schemaver.check_database()[0] == "ok")
k = sqlite3.connect(localdb.db_path())
k.execute("DELETE FROM totr_schema_version WHERE version = 4")
k.execute("DROP TABLE totr_song_offer_items")
k.execute("DROP TABLE totr_song_offers")
k.execute("DROP TABLE totr_song_requests")
k.commit()
k.close()
check(
    "a database behind this TOTR: a warning, the server still starts",
    schemaver.check_database()[0] == "warn",
)
k = sqlite3.connect(localdb.db_path())
k.execute("INSERT INTO totr_schema_version VALUES (99, 'future', 'now')")
k.commit()
k.close()
level, note = schemaver.check_database()
check(
    "a database ahead of this TOTR: refused, and it says why",
    level == "refuse" and "newer" in note,
    (level, note),
)
os.environ.pop("DB_ENGINE")


# 6. PostgreSQL
def pg_tools():
    return os.name != "nt" and all(shutil.which(t) for t in ("initdb", "pg_ctl", "psql", "pg_dump"))


if not pg_tools():
    print("skip  PostgreSQL upgrades  (needs initdb, pg_ctl, psql and pg_dump on Linux)")
else:
    import psycopg
    from psycopg.rows import dict_row

    cluster, sock = tmp / "pgdata", tmp / "pgsock"
    sock.mkdir()
    run = lambda *a, **k: subprocess.run([str(x) for x in a], capture_output=True, text=True, **k)
    r = run(
        "initdb",
        "-D",
        cluster,
        "-U",
        "postgres",
        "-A",
        "trust",
        "-E",
        "UTF8",
        "--locale=C",
        "--no-sync",
    )
    check("a throwaway PostgreSQL cluster is made", r.returncode == 0, r.stderr[-300:])
    r = run(
        "pg_ctl",
        "-D",
        cluster,
        "-o",
        f"-c listen_addresses= -c unix_socket_directories={sock} -c fsync=off",
        "-l",
        tmp / "pg.log",
        "-w",
        "start",
    )
    check(
        "and started",
        r.returncode == 0,
        r.stderr[-300:] + (tmp / "pg.log").read_text()[-300:]
        if (tmp / "pg.log").exists()
        else r.stderr[-300:],
    )
    try:
        connect = lambda dbname="postgres", user="postgres", **kw: psycopg.connect(
            host=str(sock), user=user, dbname=dbname, **kw
        )
        admin = connect(autocommit=True)
        for name in ("totr", "migrated", "rollback", "adopted"):
            admin.execute(f'CREATE DATABASE "{name}"')
        psql = lambda dbname, *a: run(
            "psql",
            "-h",
            sock,
            "-U",
            "postgres",
            "-d",
            dbname,
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            *a,
        )

        # the old hand-run scripts (what a hand-made database is built from) build one database; the
        # migrations build another
        order = [
            "media_ontology",
            "auth_schema",
            "totr_views",
            "mixtapes",
            "upload_role",
            "maintenance_role",
            "robots_role",
            "user_data",
            "join_requests",
            "song_offers",
        ]
        bad_script = [
            n for n in order if psql("totr", "-f", PF / "sql" / f"{n}.sql").returncode != 0
        ]
        check(
            "the old SQL scripts still build a database (the reference to compare with)",
            not bad_script,
            bad_script,
        )

        m = connect("migrated", autocommit=True)
        check("an empty PostgreSQL database is 'empty'", migrate.state(m)["state"] == "empty")
        done = migrate.upgrade(m)
        check(
            "up builds everything, in order",
            done == [f"{x.version:04d}_{x.name}" for x in pg_files],
            done,
        )
        check(
            "it is then current, and up again does nothing",
            migrate.state(m)["state"] == "current" and migrate.upgrade(m) == [],
        )
        check(
            "running the first upgrade's SQL a second time by hand is harmless (idempotent)",
            psql("migrated", "-f", pg_files[0].path).returncode == 0
            and psql("migrated", "-f", pg_files[1].path).returncode == 0,
        )

        def dump(dbname):
            out = run(
                "pg_dump",
                "-h",
                sock,
                "-U",
                "postgres",
                "-d",
                dbname,
                "-s",
                "-x",
                "-O",
                "--no-comments",
            ).stdout
            out = "\n".join(
                l
                for l in out.splitlines()
                if l.strip()
                and not l.startswith(
                    ("--", "\\restrict", "\\unrestrict", "SET ", "SELECT pg_catalog.set_config")
                )
            )
            blocks = [b.strip() for b in out.split(";\n") if b.strip()]
            return sorted(b for b in blocks if "schema_version" not in b)

        a, b = dump("totr"), dump("migrated")
        check(
            "the comparison looked at the real layout (tables, indexes, views), not at two empty dumps",
            len(a) >= 20 and any("CREATE VIEW totr.v_tracks" in x for x in a),
            len(a),
        )
        check(
            "the migrations make the same tables, indexes, constraints and views as the old scripts",
            a == b,
            ([x for x in a if x not in b][:2], [x for x in b if x not in a][:2]),
        )

        # grants: the same access for the same roles
        roles = {
            "read": "robots",
            "write": "totr_uploader",
            "maint": "totr_maint",
            "auth": "totr_auth",
            "mixtape": "totr_mixtapes",
        }
        migrate.apply_grants(m, roles)
        migrate.apply_grants(m, roles)  # twice is harmless

        def grants(dbname):
            c2 = connect(dbname)
            names = tuple(roles.values())
            t = c2.execute(
                "SELECT grantee, table_schema, table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee = ANY(%s) "
                "AND table_name <> 'schema_version' ORDER BY 1,2,3,4",
                (list(names),),
            ).fetchall()
            q = c2.execute(
                "SELECT r.rolname, n.nspname FROM pg_namespace n, pg_roles r WHERE r.rolname = ANY(%s) AND n.nspname IN ('media','totr') "
                "AND has_schema_privilege(r.rolname, n.nspname, 'USAGE') ORDER BY 1,2",
                (list(names),),
            ).fetchall()
            seq = c2.execute(
                "SELECT r.rolname, c.relname, has_sequence_privilege(r.rolname, c.oid, 'USAGE') u, has_sequence_privilege(r.rolname, c.oid, 'SELECT') s "
                "FROM pg_class c, pg_roles r WHERE c.relkind = 'S' AND r.rolname = ANY(%s) ORDER BY 1,2",
                (list(names),),
            ).fetchall()
            conn_ok = c2.execute(
                "SELECT r.rolname, has_database_privilege(r.rolname, current_database(), 'CONNECT') FROM pg_roles r WHERE r.rolname = ANY(%s) ORDER BY 1",
                (list(names),),
            ).fetchall()
            c2.close()
            return t, q, seq, conn_ok

        ga, gb = grants("totr"), grants("migrated")
        check(
            "the grants comparison looked at real grants",
            len(ga[0]) >= 25 and len(ga[1]) >= 7,
            (len(ga[0]), len(ga[1])),
        )
        check(
            "each role gets the same table access as the old scripts gave it",
            ga[0] == gb[0],
            ([x for x in ga[0] if x not in gb[0]][:3], [x for x in gb[0] if x not in ga[0]][:3]),
        )
        check(
            "and the same schema, sequence and connect rights",
            ga[1:] == gb[1:],
            [(x, y) for x, y in zip(ga[1:], gb[1:]) if x != y][:2],
        )

        # what a role can really do
        def can(user, sql_text):
            c3 = connect("migrated", user=user, autocommit=True)
            try:
                c3.execute(sql_text)
                return True
            except psycopg.errors.InsufficientPrivilege:
                return False
            finally:
                c3.close()

        check(
            "the accounts role can use users, sessions, invites and user_data",
            can("totr_auth", "SELECT count(*) FROM totr.users")
            and can("totr_auth", "SELECT count(*) FROM totr.user_data"),
        )
        check(
            "the accounts role cannot read the music",
            not can("totr_auth", "SELECT count(*) FROM media.items"),
        )
        check(
            "the read-only role reads the music views and nothing of the accounts",
            can("robots", "SELECT count(*) FROM totr.v_tracks")
            and not can("robots", "SELECT count(*) FROM totr.users")
            and not can("robots", "SELECT count(*) FROM media.items"),
        )
        check(
            "every role can read the version (so the server can check it at start)",
            all(can(r, "SELECT version FROM totr.schema_version") for r in roles.values()),
        )
        check(
            "the upload role adds music but cannot change or delete it",
            can("totr_uploader", "INSERT INTO media.items (type_id, title) VALUES (4, 'x')")
            and not can("totr_uploader", "UPDATE media.items SET title = 'y'")
            and not can("totr_uploader", "DELETE FROM media.items"),
        )
        check(
            "the maintenance role can change and delete music but cannot make tables",
            can("totr_maint", "UPDATE media.items SET title = 'y'")
            and can("totr_maint", "DELETE FROM media.items")
            and not can("totr_maint", "CREATE TABLE totr.nope (a int)"),
        )
        check(
            "the mixtape role cannot read accounts",
            not can("totr_mixtapes", "SELECT count(*) FROM totr.users")
            and can("totr_mixtapes", "SELECT count(*) FROM totr.mixtapes"),
        )

        # the server's own connection (rows are dicts) can read the state
        dc = connect("migrated", user="robots", row_factory=dict_row)
        check(
            "the state can be read through the server's dict-row connection",
            migrate.state(dc)["state"] == "current",
        )
        dc.close()

        # roles
        check(
            "create_role makes a login role once",
            migrate.create_role(m, "totr_extra", "pa'ss\"word") is True
            and migrate.create_role(m, "totr_extra", "other") is False,
        )
        check(
            "and it can log in",
            m.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'totr_extra'").fetchone()[0]
            is True,
        )

        # an existing hand-made database is adopted without changes
        g = connect("totr", autocommit=True)
        before = dump("totr")
        s = migrate.state(g)
        check("a database made by hand is 'unversioned'", s["state"] == "unversioned", s)
        check(
            "up refuses until it is adopted", "adopt" in (raises(lambda: migrate.upgrade(g)) or "")
        )
        check(
            "adopt records all the upgrades after checking the tables are there",
            migrate.adopt(g) == 4 and migrate.state(g)["state"] == "current",
        )
        check("adopt changed nothing else", [x for x in dump("totr")] == before)
        check("and up has nothing to do", migrate.upgrade(g) == [])
        g.close()

        # a failed upgrade is undone on PostgreSQL too
        fail_dir = tmp / "pgfailing"
        (fail_dir / "postgres").mkdir(parents=True)
        shutil.copy(real_dir / "postgres" / "0001_base.sql", fail_dir / "postgres")
        (fail_dir / "postgres" / "0002_user_data.sql").write_text(
            "CREATE TABLE totr.half_done (a int);\nINSERT INTO totr.no_such_table VALUES (1);\n"
        )
        rb = connect("rollback", autocommit=True)
        schemaver.DIR = fail_dir
        msg = raises(lambda: migrate.upgrade(rb))
        schemaver.DIR = real_dir
        check(
            "a broken upgrade says so and names itself",
            msg is not None and "0002_user_data" in msg,
            msg,
        )
        check(
            "nothing of it stays: no half-made table, version 1 only",
            rb.execute("SELECT to_regclass('totr.half_done') IS NULL").fetchone()[0]
            and [
                r[0]
                for r in rb.execute("SELECT version FROM totr.schema_version ORDER BY 1").fetchall()
            ]
            == [1],
        )
        check(
            "fixing it and running up again finishes the job",
            migrate.upgrade(rb) == ["0002_user_data", "0003_join_requests", "0004_song_offers"],
        )
        rb.close()
        m.close()
        admin.close()
    finally:
        run("pg_ctl", "-D", cluster, "-m", "immediate", "stop")

shutil.rmtree(tmp, ignore_errors=True)
finish()
