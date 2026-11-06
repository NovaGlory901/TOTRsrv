"""Which version of the database layout there is, and which this TOTR expects. READ ONLY: nothing in
this file changes a database.

The running server imports this file (to know its layout version and to check it at start). The code
that changes a database is migrate.py, which the server never imports; tests/test_guards.py checks
that. See migrate.py for how upgrades are made.

The layout of the database is a numbered list of upgrade files, migrations/<engine>/NNNN_name.sql
(the same numbers and names for SQLite and PostgreSQL). Which of them a database has is recorded in
its schema_version table (PostgreSQL: totr.schema_version, SQLite: totr_schema_version).
"""

import pathlib
import re
import sqlite3

BASE_DIR = pathlib.Path(__file__).resolve().parent
DIR = BASE_DIR / "migrations"
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
ENGINES = ("sqlite", "postgres")


class MigrateError(RuntimeError):
    """Something the person running the upgrade needs to know, in plain words."""


class Migration:
    def __init__(self, version: int, name: str, path: pathlib.Path):
        self.version, self.name, self.path = version, name, path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def files(engine: str) -> list[Migration]:
    """The upgrade files for an engine, in order. The numbers must run 1, 2, 3 ... with no gap and
    no repeat."""
    if engine not in ENGINES:
        raise MigrateError(f"unknown engine {engine!r}")
    found = []
    for p in sorted((DIR / engine).glob("*.sql")):
        m = _NAME.match(p.name)
        if not m:
            raise MigrateError(f"{p} is not named NNNN_name.sql")
        found.append(Migration(int(m.group(1)), m.group(2), p))
    if [m.version for m in found] != list(range(1, len(found) + 1)):
        raise MigrateError(
            f"the numbers in migrations/{engine}/ must run 1, 2, 3 ... without gaps: {[m.version for m in found]}"
        )
    return found


def latest(engine: str) -> int:
    return files(engine)[-1].version


# What must exist for version N to be there (used to check a hand-made database before it is
# adopted).
EXPECTED = {
    "sqlite": {
        1: [
            "media_items",
            "media_item_types",
            "media_relation_types",
            "media_item_relations",
            "totr_users",
            "totr_sessions",
            "totr_invites",
            "totr_mixtapes",
            "totr_mixtape_tracks",
            "totr_v_artists",
            "totr_v_albums",
            "totr_v_tracks",
            "totr_v_track_artists",
        ],
        2: ["totr_user_data"],
        3: ["totr_join_requests"],
        4: ["totr_song_offers", "totr_song_offer_items", "totr_song_requests"],
    },
    "postgres": {
        1: [
            "media.items",
            "media.item_types",
            "media.relation_types",
            "media.item_relations",
            "totr.users",
            "totr.sessions",
            "totr.invites",
            "totr.mixtapes",
            "totr.mixtape_tracks",
            "totr.v_artists",
            "totr.v_albums",
            "totr.v_tracks",
            "totr.v_track_artists",
        ],
        2: ["totr.user_data"],
        3: ["totr.join_requests"],
        4: ["totr.song_offers", "totr.song_offer_items", "totr.song_requests"],
    },
}


def _first(row):
    """The first column of a row, whether the connection gives tuples or dicts (the server's
    connections give dicts)."""
    return next(iter(row.values())) if isinstance(row, dict) else row[0]


class Sqlite:
    engine = "sqlite"
    table = "totr_schema_version"

    def __init__(self, conn: sqlite3.Connection):
        conn.isolation_level = None  # the writer (migrate.py) says BEGIN / COMMIT itself
        self.conn = conn

    def has(self, name: str) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone()
            is not None
        )

    def has_version_table(self) -> bool:
        return self.has(self.table)

    def versions(self) -> list[int]:
        return (
            [r[0] for r in self.conn.execute(f"SELECT version FROM {self.table} ORDER BY version")]
            if self.has_version_table()
            else []
        )

    def legacy_version(self) -> int:
        """Before schema_version existed, the layout version was PRAGMA user_version, and its
        numbers are the same as the migration numbers."""
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def objects_missing(self, names: list[str]) -> list[str]:
        return [n for n in names if not self.has(n)]

    def is_unversioned(self) -> bool:
        return self.has("totr_users")


class Postgres:
    engine = "postgres"
    table = "totr.schema_version"

    def __init__(self, conn):
        self.conn = conn

    def has(self, name: str) -> bool:
        return _first(self.conn.execute("SELECT to_regclass(%s) IS NOT NULL", (name,)).fetchone())

    def has_version_table(self) -> bool:
        return self.has(self.table)

    def versions(self) -> list[int]:
        return (
            [
                _first(r)
                for r in self.conn.execute(
                    f"SELECT version FROM {self.table} ORDER BY version"
                ).fetchall()
            ]
            if self.has_version_table()
            else []
        )

    def legacy_version(self) -> int:
        return 0

    def objects_missing(self, names: list[str]) -> list[str]:
        return [n for n in names if not self.has(n)]

    def is_unversioned(self) -> bool:
        return self.has("totr.users")


def wrap(conn):
    """A database connection (sqlite3 or psycopg) as the small interface above."""
    return Sqlite(conn) if isinstance(conn, sqlite3.Connection) else Postgres(conn)


def state(conn, db=None) -> dict:
    """{'engine', 'state', 'current', 'latest', 'message', 'legacy'}. state is one of: empty
    nothing of TOTR's is there yet: `migrate.py up` builds it unversioned TOTR's tables are there
    but no version is recorded (a hand-made database): `migrate.py adopt` records it old         the
    database is behind this TOTR: `migrate.py up` brings it forward current     the database matches
    this TOTR newer       the database is ahead of this TOTR: update TOTR, do not touch the database"""
    db = db or wrap(conn)
    lat = latest(db.engine)
    have = db.versions()
    from_legacy = False
    if not have and db.legacy_version():
        have, from_legacy = list(range(1, db.legacy_version() + 1)), True
    current = max(have, default=0)
    # upgrades this TOTR knows must run 1, 2, 3 ... (a newer database also has numbers it does not
    # know)
    known = [v for v in have if v <= lat]
    if known != list(range(1, len(known) + 1)):
        raise MigrateError(f"the recorded upgrades have a gap: {have}")
    if current == 0:
        st = "unversioned" if db.is_unversioned() else "empty"
    else:
        st = "newer" if current > lat else "current" if current == lat else "old"
    msgs = {
        "empty": "The database is empty: run `python migrate.py up`.",
        "unversioned": "The database has TOTR's tables but no version: run `python migrate.py adopt` (it changes nothing else).",
        "old": f"The database is at version {current}, this TOTR needs {lat}: run `python migrate.py up`.",
        "current": f"The database is at version {current}, as this TOTR expects.",
        "newer": f"The database is at version {current}, newer than this TOTR ({lat}). Update TOTR; do not change the database.",
    }
    return {
        "engine": db.engine,
        "state": st,
        "current": current,
        "latest": lat,
        "message": msgs[st],
        "legacy": from_legacy,
    }


def check_database():
    """('ok' | 'warn' | 'refuse', message) for the database the server is configured to use. Only a
    database that is positively NEWER than this TOTR is refused; anything unknown (not reachable,
    not versioned yet) is a warning at most, so a start is never blocked by a guess."""
    import db
    import localdb

    try:
        if db.engine() == "sqlite":
            path = localdb.db_path()
            if not path.exists():
                return "ok", "no local database yet; it is created on first use"
            c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        else:
            c = db.get_conn()
        try:
            s = state(c)
        finally:
            c.close()
    except MigrateError as e:
        return "warn", str(e)
    except Exception:
        return "ok", "could not read the database version"
    if s["state"] == "newer":
        return "refuse", s["message"]
    return ("ok" if s["state"] == "current" else "warn"), s["message"]
