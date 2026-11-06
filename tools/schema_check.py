"""Read-only check that the database matches the documented layout (docs/database.md). Run from TOTRsrv:

    .venv\\Scripts\\python.exe tools\\schema_check.py

It connects with the read-only role, runs only SELECTs on pg_catalog, and prints what it finds:
  * PROBLEM  something of TOTR's is in `public` (a sandbox schema), or an object in `totr` is not in the documented layout,
             or something the layout needs is missing;
  * info     other objects in `public` / `media` / other schemas, which TOTR does not touch.
Exit code 0 means no problems. The layout itself is the LAYOUT table below; change it together with docs/database.md.
"""

import os
import sys

# schema -> the objects TOTR owns there (tables and views). Sequences belonging to these tables are
# expected too.
LAYOUT = {
    "totr": {
        "users",
        "sessions",
        "invites",
        "mixtapes",
        "mixtape_tracks",
        "user_data",
        "v_artists",
        "v_albums",
        "v_tracks",
        "v_track_artists",
    },
}
# TOTR's own bookkeeping table (migrate.py records which layout version a database has). It is fine
# when there and fine when not: a hand-made database has none until `migrate.py adopt` is run, and
# that is the administrator's choice. The join and offer tables are added by sql/join_requests.sql and
# sql/song_offers.sql (or migrate.py up).
OPTIONAL = {
    "totr": {"schema_version", "join_requests", "song_offers", "song_offer_items", "song_requests"}
}
# the ontology that TOTR needs to exist (it never creates or changes it on the existing
# database)
NEEDS = {"media": {"items", "item_types", "item_relations", "relation_types"}}
# names TOTR used to put in `public`; if they are still there, the move is not finished
OLD_TOTR_NAMES = {
    "artists",
    "albums",
    "tracks",
    "track_artists",
    "users",
    "sessions",
    "invites",
    "mixtapes",
    "mixtape_tracks",
}
SYSTEM_SCHEMAS = {"information_schema"}


def classify(objects) -> list:
    """objects: iterable of (schema, name, kind) with kind 'r' table, 'v' view, 'S' sequence.
    Returns [(level, message)]."""
    found = {(s, n, k) for s, n, k in objects}
    out = []
    names = {(s, n) for s, n, k in found if k in ("r", "v", "p", "m", "f")}
    for s, n, k in sorted(found):
        if s in SYSTEM_SCHEMAS or s.startswith("pg_"):
            continue
        if s == "public" and n in OLD_TOTR_NAMES and k != "S":
            out.append(
                (
                    "PROBLEM",
                    f"public.{n} is one of TOTR's tables, but TOTR must keep nothing in public",
                )
            )
        elif s == "public" and k == "S" and any(n.startswith(t + "_") for t in OLD_TOTR_NAMES):
            out.append(("PROBLEM", f"public.{n} looks like a sequence of one of TOTR's old tables"))
        elif s in OPTIONAL and n in OPTIONAL[s] and k != "S":
            continue
        elif s in LAYOUT and k != "S" and n not in LAYOUT[s]:
            out.append(("PROBLEM", f"{s}.{n} is not in the documented layout for schema {s}"))
        elif s in LAYOUT and k == "S" and not any(n.startswith(t + "_") for t in LAYOUT[s]):
            out.append(("PROBLEM", f"{s}.{n} is a sequence that belongs to no documented table"))
    for s, needed in {**LAYOUT, **NEEDS}.items():
        for n in sorted(needed - {n for sc, n in names if sc == s}):
            out.append(("PROBLEM", f"{s}.{n} is missing"))
    mine = {s for s in LAYOUT} | set(NEEDS) | {"public"}
    others = sorted(
        {
            s
            for s, _, _ in found
            if s not in mine and s not in SYSTEM_SCHEMAS and not s.startswith("pg_")
        }
    )
    for s in others:
        out.append(("info", f"schema {s} is not TOTR's and is left alone"))
    for s in ("public", "media"):
        extra = sorted(
            n
            for sc, n in names
            if sc == s and n not in OLD_TOTR_NAMES and n not in NEEDS.get(s, ())
        )
        if extra:
            out.append(
                (
                    "info",
                    f"{s}: {len(extra)} object(s) of yours that TOTR does not touch ({', '.join(extra[:8])}{', ...' if len(extra) > 8 else ''})",
                )
            )
    return out


def read_objects(conn) -> list:
    return [
        (r["nspname"], r["relname"], r["relkind"])
        for r in conn.execute(
            "SELECT n.nspname, c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'v', 'm', 'S', 'p', 'f') AND n.nspname NOT LIKE 'pg_%%' AND n.nspname <> 'information_schema' "
            "ORDER BY 1, 2"
        ).fetchall()
    ]


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import db

    with db.get_conn() as conn:
        conn.read_only = True
        results = classify(read_objects(conn))
    for level, message in results:
        print(f"{level:8s} {message}")
    problems = [m for lvl, m in results if lvl == "PROBLEM"]
    print(
        f"\n{len(problems)} problem(s)."
        if problems
        else "\nNo problems: the database matches the documented layout."
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
