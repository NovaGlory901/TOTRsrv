#!/usr/bin/env python3
"""Reads (never changes) the database's layout version and prints it as one line of JSON: {"engine",
"state", "current", "latest", "message"}. Used by deploy/apply-update.py, run with the NEW release's
Python and code, to learn whether the update needs the database brought forward. state is one of:
empty, unversioned, old, current, newer (see schemaver.py). It needs the same .env as the server
(TOTR_STATE_DIR points at it)."""

import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import db  # noqa: E402  (loads the .env)
import localdb  # noqa: E402
import schemaver  # noqa: E402

engine = db.engine()
if engine == "sqlite":
    path = localdb.db_path()
    if not path.exists():
        print(
            json.dumps(
                {
                    "engine": engine,
                    "state": "empty",
                    "current": 0,
                    "latest": schemaver.latest("sqlite"),
                    "message": "no local database yet",
                }
            )
        )
        sys.exit(0)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
else:
    conn = db.get_conn()
try:
    s = schemaver.state(conn)
finally:
    conn.close()
print(
    json.dumps({"engine": engine, **{k: s[k] for k in ("state", "current", "latest", "message")}})
)
