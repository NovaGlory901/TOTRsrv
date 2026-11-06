"""The built-in local database (SQLite) for people who run TOTR without their own PostgreSQL.

On first start the app copies a ready-made database file (sql/sqlite/totr_template.db, built from
migrations/sqlite/ by tools/build_local_template.py) into its data folder. Copying a file is all
that happens: the server never creates or alters tables (migrate.py does, when an admin or the
updater asks; the server does not import it). `Conn` makes SQLite look like the psycopg connection
the rest of the app expects (`execute(sql with %s, params)`, `with conn:` commits or rolls back,
rows are dicts) and translates the few PostgreSQL spellings the app uses.
"""

import os
import pathlib
import re
import shutil
import sqlite3
from datetime import UTC, datetime

import paths
import schemaver

BASE_DIR = pathlib.Path(__file__).resolve().parent
TEMPLATE = BASE_DIR / "sql" / "sqlite" / "totr_template.db"
# versions before user_data (1), join requests (2) and song offers (3) still work; syncing / joining
# / offering then say they need setting up
OLDER_OK = (
    1,
    2,
    3,
)
# PRAGMA user_version of the template: the number of the newest migrations/sqlite file
SCHEMA_VERSION = schemaver.latest("sqlite")
MIN_SQLITE = (3, 38, 0)  # the `->>` JSON operator the views use


class LocalDbError(RuntimeError):
    """The local database cannot be used; the message says why in plain words."""


def data_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("TOTR_DATA_DIR") or paths.home() / "data")


def db_path() -> pathlib.Path:
    return data_dir() / "totr.db"


def sqlite_ok() -> bool:
    return sqlite3.sqlite_version_info >= MIN_SQLITE


def ensure() -> pathlib.Path:
    """Make sure the local database file exists, copying the template on first run. Never touches an
    existing file."""
    if not sqlite_ok():
        raise LocalDbError(
            f"The local database needs SQLite {'.'.join(map(str, MIN_SQLITE))} or newer; this Python has {sqlite3.sqlite_version}."
        )
    path = db_path()
    if path.exists():
        return path
    if not TEMPLATE.exists():
        raise LocalDbError(
            f"The database template {TEMPLATE} is missing. Run `python tools/build_local_template.py`."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    shutil.copyfile(TEMPLATE, part)
    os.replace(part, path)  # a half-copied file is never mistaken for a database
    return path


def file_version(path: pathlib.Path) -> int:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


def info() -> dict:
    """What the admin Database page shows."""
    path = db_path()
    out = {
        "engine": "sqlite",
        "path": str(path),
        "sqlite_version": sqlite3.sqlite_version,
        "sqlite_ok": sqlite_ok(),
        "exists": path.exists(),
        "schema_version": None,
        "expected_version": SCHEMA_VERSION,
        "size_bytes": None,
    }
    if path.exists():
        out["size_bytes"] = path.stat().st_size
        out["schema_version"] = file_version(path)
    return out


_SCHEMA_NAME = re.compile(r"\b(totr|media)\.(?=[A-Za-z_])")
_ANY = re.compile(r"=\s*ANY\(%s(?:::int\[\])?\)")
_STRING_AGG = re.compile(r"string_agg\(DISTINCT\s+([^,()]+?),\s*', '\)")


def lite(sql: str) -> str:
    """The app's SQL for SQLite: schema.name -> schema_name, ANY(list) -> IN, string_agg(DISTINCT x,
    ', ') -> our aggregate, string_agg -> group_concat."""
    sql = _SCHEMA_NAME.sub(r"\1_", sql)
    sql = _STRING_AGG.sub(r"string_agg_distinct(\1)", sql)
    # SQLite only knows string_agg from 3.44; group_concat is the same thing
    sql = sql.replace("string_agg(", "group_concat(")
    return _ANY.sub("IN %s", sql)


def conv(v):
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(v, bool):
        return int(v)
    return v


class _StringAggDistinct:
    """string_agg(DISTINCT x, ', ') as PostgreSQL gives it: distinct values, in sorted order, joined
    with ', '."""

    def __init__(self):
        self.values = set()

    def step(self, v):
        if v is not None:
            self.values.add(v)

    def finalize(self):
        return ", ".join(sorted(self.values)) if self.values else None


class Cur:
    def __init__(self, cur):
        self.cur = cur

    def fetchone(self):
        r = self.cur.fetchone()
        return dict(r) if r is not None else None

    def fetchall(self):
        return [dict(r) for r in self.cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self.cur.rowcount

    @property
    def lastrowid(self):
        return self.cur.lastrowid


class Conn:
    """What psycopg's connection looks like to the app. A list parameter after `= ANY(%s)` becomes
    an IN list."""

    def __init__(self, path):
        self.c = sqlite3.connect(path, timeout=30)
        self.c.row_factory = sqlite3.Row
        # the cascades (sessions, mixtapes) are real foreign keys here
        self.c.execute("PRAGMA foreign_keys = ON")
        self.c.execute("PRAGMA journal_mode = WAL")  # readers do not block the one writer
        self.c.create_aggregate("string_agg_distinct", 1, _StringAggDistinct)

    def execute(self, sql, params=()):
        assert "%s" in sql or not params, "parameters must be bound, never formatted into the query"
        parts, flat, out = lite(sql).split("%s"), [], []
        for i, p in enumerate(params):
            if isinstance(p, (list, tuple)):
                # an empty list must stay an empty set, so NOT IN () is true like in PostgreSQL
                out.append(
                    parts[i]
                    + ("(" + ",".join("?" * len(p)) + ")" if p else "(SELECT NULL WHERE 0)")
                )
                flat += [conv(x) for x in p]
            else:
                out.append(parts[i] + "?")
                flat.append(conv(p))
        out.append(parts[len(params)])
        return Cur(self.c.execute("".join(out), flat))

    def rollback(self):
        self.c.rollback()

    def close(self):
        self.c.close()

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        (self.c.rollback if et else self.c.commit)()
        self.c.close()


_checked = set()


def connect() -> Conn:
    path = ensure()
    if path not in _checked:  # the layout version is checked once per file, not on every request
        version = file_version(path)
        if version not in (SCHEMA_VERSION, *OLDER_OK):
            raise LocalDbError(
                f"The local database {path} has layout version {version}, this TOTR expects {SCHEMA_VERSION}."
            )
        _checked.add(path)
    return Conn(path)
