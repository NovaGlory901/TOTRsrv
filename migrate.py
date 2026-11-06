"""Changes the layout of the database: builds it, upgrades it, records an existing one, and sets who
may do what. The only code that does.

    python migrate.py status     what the database has and what this TOTR expects
    python migrate.py up         apply the missing upgrades (each in its own transaction, so a failure leaves the database as it was)
    python migrate.py adopt      put an existing, hand-made database under version control without changing it
    python migrate.py grants     give the roles named in .env the access each one needs (PostgreSQL)

Who may run this: the installer, the updater and an admin at the command line. NEVER the running
server, which does not import this file: it only reads versions (schemaver.py) and refuses to start
on a database newer than itself (tests/test_guards.py checks both). For PostgreSQL it needs an
account that may create tables; give it with DB_ADMIN_USER / DB_ADMIN_PASSWORD or it asks. That
login is used for this run only and is never stored. The upgrade files are described in
schemaver.py. They contain no role names and no passwords: roles are made by create_role, access
given by apply_grants.
"""

import datetime
import os
import sqlite3
import sys

import schemaver

# re-exported: the installer and the tests use them from here
from schemaver import MigrateError, Migration, files, latest, state, wrap  # noqa: F401


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


class _SqliteWriter(schemaver.Sqlite):
    def ensure_version_table(self):
        self.conn.execute(
            f"CREATE TABLE IF NOT EXISTS {self.table} (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )

    def record(self, m: Migration):
        self.conn.execute(
            f"INSERT INTO {self.table} (version, name, applied_at) VALUES (?, ?, ?)",
            (m.version, m.name, _now()),
        )

    def apply(self, m: Migration):
        self.ensure_version_table()
        script = f"BEGIN;\n{m.sql}\n;INSERT INTO {self.table} (version, name, applied_at) VALUES ({int(m.version)}, '{m.name}', '{_now()}');\nCOMMIT;"
        try:
            self.conn.executescript(script)
        except Exception as e:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise MigrateError(
                f"upgrade {m.version:04d}_{m.name} failed and was undone: {e}"
            ) from e
        # the quick check localdb makes on every start
        self.conn.execute(f"PRAGMA user_version = {int(m.version)}")

    def mark_all(self, upto: int, ms: list[Migration]):
        self.ensure_version_table()
        self.conn.execute("BEGIN")
        for m in ms:
            if m.version <= upto:
                self.record(m)
        self.conn.execute("COMMIT")
        self.conn.execute(f"PRAGMA user_version = {int(upto)}")


class _PostgresWriter(schemaver.Postgres):
    def ensure_version_table(self):
        self.conn.execute("CREATE SCHEMA IF NOT EXISTS totr")
        self.conn.execute(
            f"CREATE TABLE IF NOT EXISTS {self.table} (version integer PRIMARY KEY, name text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        self.conn.execute(f"REVOKE ALL ON {self.table} FROM PUBLIC")
        self.conn.commit()

    def record(self, m: Migration):
        self.conn.execute(
            f"INSERT INTO {self.table} (version, name) VALUES (%s, %s)", (m.version, m.name)
        )

    def apply(self, m: Migration):
        self.ensure_version_table()
        try:
            with self.conn.transaction():
                self.conn.execute(m.sql)
                self.record(m)
        except Exception as e:
            raise MigrateError(
                f"upgrade {m.version:04d}_{m.name} failed and was undone: {e}"
            ) from e

    def mark_all(self, upto: int, ms: list[Migration]):
        self.ensure_version_table()
        with self.conn.transaction():
            for m in ms:
                if m.version <= upto:
                    self.record(m)


def writer(conn):
    return _SqliteWriter(conn) if isinstance(conn, sqlite3.Connection) else _PostgresWriter(conn)


def upgrade(conn, log=lambda s: None) -> list[str]:
    """Apply every missing upgrade, each in its own transaction. Returns the names applied (empty
    when already current)."""
    db = writer(conn)
    s = state(conn, db)
    if s["state"] in ("newer", "unversioned"):
        raise MigrateError(s["message"])
    ms = files(db.engine)
    if s["legacy"]:  # an older local database file: its user_version already said what it has
        db.mark_all(s["current"], ms)
        log(f"recorded versions 1..{s['current']} of an older database file")
    applied = []
    for m in ms:
        if m.version > s["current"]:
            log(f"applying {m.version:04d}_{m.name}")
            db.apply(m)
            applied.append(f"{m.version:04d}_{m.name}")
    return applied


def adopt(conn, log=lambda s: None) -> int:
    """Record what an existing hand-made database already has, after checking it really has it.
    Changes nothing else. Returns the version recorded."""
    db = writer(conn)
    s = state(conn, db)
    if s["state"] != "unversioned":
        raise MigrateError(
            f"adopt is for a database with TOTR's tables and no version; this one is '{s['state']}'. {s['message']}"
        )
    ms, upto = files(db.engine), 0
    for version, names in sorted(schemaver.EXPECTED[db.engine].items()):
        missing = db.objects_missing(names)
        if missing:
            if version == 1:
                raise MigrateError(
                    "this is not a complete TOTR database; missing: " + ", ".join(missing)
                )
            log(
                f"version {version} is not there yet (missing {', '.join(missing)}): recording up to {upto}; `up` will add the rest"
            )
            break
        upto = version
    db.mark_all(upto, ms)
    log(f"recorded versions 1..{upto}")
    return upto


PURPOSES = ("read", "write", "maint", "auth", "mixtape")
_VIEWS = ["v_artists", "v_albums", "v_tracks", "v_track_artists"]


def grant_statements(role: str, purpose: str, dbname: str):
    """The GRANTs one purpose needs, for the role that will be used for it. Nothing here creates a
    table or changes one."""
    from psycopg import sql

    r, d = sql.Identifier(role), sql.Identifier(dbname)

    def s(text, *a):
        return sql.SQL(text).format(*a)

    # so every role can read the version at start-up
    out = [
        s("GRANT CONNECT ON DATABASE {} TO {}", d, r),
        s("GRANT USAGE ON SCHEMA totr TO {}", r),
        s("GRANT SELECT ON totr.schema_version TO {}", r),
    ]
    views = ", ".join(f"totr.{v}" for v in _VIEWS)
    if purpose == "read":
        out.append(s("GRANT SELECT ON " + views + " TO {}", r))
    elif purpose in ("write", "maint"):
        rights = "SELECT, INSERT" if purpose == "write" else "SELECT, INSERT, UPDATE, DELETE"
        out += [
            s("GRANT USAGE ON SCHEMA media TO {}", r),
            s("GRANT SELECT ON media.item_types, media.relation_types TO {}", r),
            s("GRANT " + rights + " ON media.items, media.item_relations TO {}", r),
            s("GRANT SELECT ON " + views + " TO {}", r),
            s(
                "GRANT USAGE, SELECT ON SEQUENCE media.items_item_id_seq, media.item_relations_relation_id_seq TO {}",
                r,
            ),
        ]
    elif purpose == "auth":
        out += [
            s(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON totr.users, totr.sessions, totr.invites, totr.user_data, totr.join_requests, totr.song_requests, totr.song_offers, totr.song_offer_items TO {}",
                r,
            ),
            s("GRANT USAGE, SELECT ON SEQUENCE totr.users_user_id_seq TO {}", r),
        ]
    elif purpose == "mixtape":
        out += [
            s(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON totr.mixtapes, totr.mixtape_tracks TO {}",
                r,
            ),
            s(
                "GRANT USAGE, SELECT ON SEQUENCE totr.mixtapes_mixtape_id_seq, totr.mixtape_tracks_entry_id_seq TO {}",
                r,
            ),
        ]
    else:
        raise MigrateError(f"unknown purpose {purpose!r}")
    return out


def apply_grants(conn, roles: dict[str, str]):
    """roles: purpose -> role name, e.g. {'read': 'robots', 'auth': 'totr_auth'}. With one role for
    everything (DB_SINGLE_ROLE) pass the same name for every purpose; the grants add up. Safe to run
    again."""
    dbname = schemaver._first(conn.execute("SELECT current_database()").fetchone())
    if not conn.autocommit:
        # so the block below is a real transaction, not a savepoint inside the read above
        conn.commit()
    with conn.transaction():
        for purpose, role in roles.items():
            for stmt in grant_statements(role, purpose, dbname):
                conn.execute(stmt)


def create_role(conn, name: str, password: str) -> bool:
    """Make a login role with this password unless it exists. Returns True when it was made. The
    password is never logged or stored here."""
    from psycopg import sql

    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
    if not exists:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(name), sql.Literal(password)
            )
        )
    if not conn.autocommit:
        conn.commit()
    return not exists


def _open():
    """A connection that may change the layout. SQLite: the local file (created if missing).
    PostgreSQL: the admin login, used once."""
    import db
    import localdb

    if db.engine() == "sqlite":
        path = localdb.db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(path)
    import getpass

    import psycopg

    user = os.environ.get("DB_ADMIN_USER") or input("Database account that may create tables: ")
    password = os.environ.get("DB_ADMIN_PASSWORD") or getpass.getpass(
        f"Password for {user} (used now, not stored): "
    )
    return psycopg.connect(**db.connect_settings(user, password), autocommit=True)


def main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd not in ("status", "up", "adopt", "grants"):
        print(__doc__)
        return 2
    import db

    conn = _open()
    try:
        if cmd == "status":
            print(state(conn)["message"])
        elif cmd == "up":
            done = upgrade(conn, print)
            conn.commit()
            print("applied: " + ", ".join(done) if done else "nothing to do")
        elif cmd == "adopt":
            adopt(conn, print)
            conn.commit()
        else:
            if db.engine() != "postgres":
                raise MigrateError("grants are for PostgreSQL; the local database has no roles")
            roles = {p: u for p in PURPOSES if (u := (db.credentials(p) or (None,))[0])}
            apply_grants(conn, roles)
            print("granted: " + ", ".join(f"{p} -> {r}" for p, r in roles.items()))
    except MigrateError as e:
        print(e)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
