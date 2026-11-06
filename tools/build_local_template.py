"""Builds sql/sqlite/totr_template.db by running migrations/sqlite/*.sql. Run it after adding or
changing a migration, then commit the result:

    python tools/build_local_template.py

The app only ever copies the result (localdb.ensure); this script is the one place the local layout
is created."""

import pathlib
import sqlite3
import sys

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
import localdb
import migrate


def build(target: pathlib.Path = localdb.TEMPLATE) -> pathlib.Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    c = sqlite3.connect(target)
    migrate.upgrade(c)
    # the same bytes every build, not the minute it was made
    c.execute("UPDATE totr_schema_version SET applied_at = 'template'")
    c.execute("PRAGMA journal_mode = DELETE")  # one plain file, no -wal next to it
    c.execute("VACUUM")
    c.close()
    return target


if __name__ == "__main__":
    print("built", build())
