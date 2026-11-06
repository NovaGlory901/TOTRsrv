"""Move TOTR's data from the database it runs on now (usually the built-in local one) into your own
PostgreSQL.

    python tools/migrate_db.py export totr-export.json         # reads the database the app is configured for; writes one file
    python tools/migrate_db.py import totr-export.json         # loads it into the database named by the TARGET_DB_* variables

The import target must already have the TOTR layout (the scripts in sql/, in the order docs/setup.md
gives) and must hold no TOTR data yet: it refuses if it finds any songs, artists or accounts, so it
cannot overwrite anything (and cannot be pointed at a database that is already in use). Ids are
kept, so playlists and links stay valid. Add `--dry-run` to import to check everything and then undo
it.

TARGET_DB_HOST, TARGET_DB_NAME, TARGET_DB_USER, TARGET_DB_PASSWORD, optional TARGET_DB_PORT and
TARGET_DB_SSLMODE: set them in your shell for the import, not in a file. The role needs INSERT on
the tables (the owner/superuser role is simplest). The export file holds password hashes and file
paths: keep it private and delete it when done. Moved too: each person's synced settings. Not moved:
sessions (people sign in again), invites, and the music files themselves (copy your music folder
yourself; the `file_path` of every song is kept as it is, so set MUSIC_ROOT on the new setup to
match, or edit the paths)."""

import argparse
import json
import os
import pathlib
import sys

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))

FORMAT, VERSION = "totr-export", 1

# (name in the file, what to read, columns to write). Order matters on import: parents before
# children.
READS = {
    "items": "SELECT i.item_id, t.item_type_slug AS type, i.title, i.metadata FROM media.items i JOIN media.item_types t ON t.type_id = i.type_id ORDER BY i.item_id",
    "relations": "SELECT r.from_item_id, t.relation_slug AS type, r.to_item_id FROM media.item_relations r JOIN media.relation_types t ON t.relation_type_id = r.relation_type_id ORDER BY r.relation_id",
    "users": "SELECT user_id, username, password_hash, role, disabled, permissions, created_at, last_login_at FROM totr.users ORDER BY user_id",
    "mixtapes": "SELECT mixtape_id, user_id, name, created_at, updated_at FROM totr.mixtapes ORDER BY mixtape_id",
    "mixtape_tracks": "SELECT entry_id, mixtape_id, track_id, position, added_at FROM totr.mixtape_tracks ORDER BY entry_id",
    "user_data": "SELECT user_id, name, value, updated_ms, saved_at FROM totr.user_data ORDER BY user_id, name",
}
OPTIONAL = {"user_data"}  # a database one upgrade behind has no such table yet: nothing to move
COUNTS = {"items": "media.items", "users": "totr.users", "mixtapes": "totr.mixtapes"}
TABLES = {
    "items": "media.items",
    "relations": "media.item_relations",
    "users": "totr.users",
    "mixtapes": "totr.mixtapes",
    "mixtape_tracks": "totr.mixtape_tracks",
    "user_data": "totr.user_data",
}


class MigrateError(Exception):
    pass


def _plain(v):
    """JSON-friendly: PostgreSQL gives dicts for metadata and datetimes for times; the local
    database gives text."""
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _read(conn, name, sql):
    try:
        return [{k: _plain(v) for k, v in row.items()} for row in conn.execute(sql).fetchall()]
    except Exception:
        if name not in OPTIONAL:
            raise
        if hasattr(conn, "rollback"):
            conn.rollback()
        return []


def export_data(conn) -> dict:
    tables = {name: _read(conn, name, sql) for name, sql in READS.items()}
    return {"format": FORMAT, "version": VERSION, "tables": tables}


def _is_postgres(conn) -> bool:
    return hasattr(conn, "pgconn")


def import_data(conn, data: dict, progress=None) -> dict:
    """Loads an export into an empty TOTR layout. Raises MigrateError, with nothing written, if
    anything is off. The caller commits. `progress(done, total)`, if given, is called as rows go in
    (the admin page's progress bar)."""
    if data.get("format") != FORMAT or data.get("version") != VERSION:
        raise MigrateError("This is not a TOTR export file this version understands.")
    t = data["tables"]
    for table in COUNTS.values():
        if conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]:
            raise MigrateError(
                f"The target already holds data ({table}). Import only goes into an empty TOTR layout."
            )
    types = {
        r["item_type_slug"]: r["type_id"]
        for r in conn.execute("SELECT type_id, item_type_slug FROM media.item_types").fetchall()
    }
    rels = {
        r["relation_slug"]: r["relation_type_id"]
        for r in conn.execute(
            "SELECT relation_type_id, relation_slug FROM media.relation_types"
        ).fetchall()
    }
    missing = sorted({i["type"] for i in t["items"]} - set(types)) + sorted(
        {r["type"] for r in t["relations"]} - set(rels)
    )
    if missing:
        raise MigrateError(
            "The target layout lacks these item or relation types: " + ", ".join(missing)
        )
    # media's id columns are GENERATED ALWAYS (as in the reference schema), so PostgreSQL only takes
    # the ids from the export if told to
    override = " OVERRIDING SYSTEM VALUE" if _is_postgres(conn) else ""
    # exports made before this table was moved do not list it
    t = {
        **t,
        "user_data": t.get("user_data", []),
    }
    total, done = sum(len(rows) for rows in t.values()), 0

    def tick():
        nonlocal done
        done += 1
        if progress and (done % 200 == 0 or done == total):
            progress(done, total)

    for i in t["items"]:
        conn.execute(
            "INSERT INTO media.items (item_id, type_id, title, metadata)"
            + override
            + " VALUES (%s, %s, %s, %s)",
            (i["item_id"], types[i["type"]], i["title"], i["metadata"]),
        )
        tick()
    for r in t["relations"]:
        conn.execute(
            "INSERT INTO media.item_relations (from_item_id, relation_type_id, to_item_id) VALUES (%s, %s, %s)",
            (r["from_item_id"], rels[r["type"]], r["to_item_id"]),
        )
        tick()
    for u in t["users"]:
        conn.execute(
            "INSERT INTO totr.users (user_id, username, password_hash, role, disabled, permissions, created_at, last_login_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                u["user_id"],
                u["username"],
                u["password_hash"],
                u["role"],
                bool(u["disabled"]),
                u["permissions"],
                u["created_at"],
                u["last_login_at"],
            ),
        )
        tick()
    for m in t["mixtapes"]:
        conn.execute(
            "INSERT INTO totr.mixtapes (mixtape_id, user_id, name, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
            (m["mixtape_id"], m["user_id"], m["name"], m["created_at"], m["updated_at"]),
        )
        tick()
    for e in t["mixtape_tracks"]:
        conn.execute(
            "INSERT INTO totr.mixtape_tracks (entry_id, mixtape_id, track_id, position, added_at) VALUES (%s, %s, %s, %s, %s)",
            (e["entry_id"], e["mixtape_id"], e["track_id"], e["position"], e["added_at"]),
        )
        tick()
    for d in t["user_data"]:
        conn.execute(
            "INSERT INTO totr.user_data (user_id, name, value, updated_ms, saved_at) VALUES (%s, %s, %s, %s, %s)",
            (d["user_id"], d["name"], d["value"], d["updated_ms"], d["saved_at"]),
        )
        tick()
    if _is_postgres(conn):  # ids were given explicitly, so move each counter past the highest one
        for table, column in (
            ("media.items", "item_id"),
            ("media.item_relations", "relation_id"),
            ("totr.users", "user_id"),
            ("totr.mixtapes", "mixtape_id"),
            ("totr.mixtape_tracks", "entry_id"),
        ):
            conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'), COALESCE((SELECT max({column}) FROM {table}), 0) + 1, false)"
            )
    return {name: len(rows) for name, rows in t.items()}


def _target():
    import psycopg
    from psycopg.rows import dict_row

    need = ("TARGET_DB_HOST", "TARGET_DB_NAME", "TARGET_DB_USER", "TARGET_DB_PASSWORD")
    absent = [v for v in need if not os.environ.get(v)]
    if absent:
        raise MigrateError("Set these in your shell first: " + ", ".join(absent))
    host = os.environ["TARGET_DB_HOST"]
    kw = {
        "host": host,
        "dbname": os.environ["TARGET_DB_NAME"],
        "user": os.environ["TARGET_DB_USER"],
        "password": os.environ["TARGET_DB_PASSWORD"],
        "sslmode": os.environ.get("TARGET_DB_SSLMODE")
        or ("disable" if host.startswith("/") else "require"),
        "row_factory": dict_row,
    }
    if os.environ.get("TARGET_DB_PORT"):
        kw["port"] = int(os.environ["TARGET_DB_PORT"])
    return psycopg.connect(**kw)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=("export", "import"))
    ap.add_argument("file")
    ap.add_argument("--dry-run", action="store_true", help="import: check everything, then undo it")
    args = ap.parse_args(argv)
    try:
        if args.action == "export":
            import db

            with db.get_conn() as conn:
                data = export_data(conn)
            path = pathlib.Path(args.file)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
            print(f"Wrote {path}: " + ", ".join(f"{len(v)} {k}" for k, v in data["tables"].items()))
            return 0
        data = json.loads(pathlib.Path(args.file).read_text(encoding="utf8"))
        conn = _target()
        try:
            counts = import_data(conn, data)
            if args.dry_run:
                conn.rollback()
                print("Dry run: everything would load. Nothing was written.", counts)
            else:
                conn.commit()
                print("Imported:", counts)
        finally:
            conn.close()
        return 0
    except MigrateError as e:
        print("Stopped:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
