"""Guards so TOTR's tables never end up in the wrong schema again (docs/database.md):

  * the app contains no DDL aimed at the database (only the scripts in sql/ create or change
    structure);
  * every table or view name in the app's SQL names its schema (totr. / media.), never a bare name
    or public.;
  * the scripts for fresh databases name their schema everywhere;
  * tools/schema_check.py's layout rules work.
Nothing here touches a database."""

import os
import pathlib
import re
import sys

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
sys.path.insert(0, str(PF / "tools"))

from check import check, finish

SKIP_DIRS = {".venv", "__pycache__", "tests", "tools", "sql", "reports", "node_modules", ".git"}


def app_files():
    for root, dirs, files in os.walk(PF):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield pathlib.Path(root) / f


every = list(app_files())
check(
    "the guard sees the app's code",
    len(every) > 20 and any(f.name == "catalog.py" for f in every),
    len(every),
)
# migrate.py is the one file that changes the layout (builds, upgrades, grants). It is for the
# installer, the updater and an admin at the command line, so it is held to a different rule:
# nothing in the running server may import it. (schemaver.py, which the server does import, only
# reads.)
files = [f for f in every if f.name != "migrate.py"]
check(
    "the server never imports migrate.py (the code that changes the layout); it reads versions through schemaver.py",
    not [
        f.name
        for f in files
        if re.search(
            r"^\s*(import migrate\b|from migrate import)", f.read_text(encoding="utf8"), re.M
        )
    ]
    and any(f.name == "schemaver.py" for f in files),
    [f.name for f in files if "import migrate" in f.read_text(encoding="utf8")],
)

DDL = re.compile(
    r"\b(CREATE|ALTER|DROP)\s+(OR\s+REPLACE\s+)?(TEMP(ORARY)?\s+)?(TABLE|SCHEMA|INDEX|VIEW|SEQUENCE|ROLE|DATABASE|EXTENSION|FUNCTION|TRIGGER|TYPE)\b"
    r"|\bTRUNCATE\s+(TABLE\s+)?[A-Za-z_\"]",
    re.I,
)
hits = [
    f"{f.relative_to(PF)}:{n}: {line.strip()[:80]}"
    for f in files
    for n, line in enumerate(f.read_text(encoding="utf8").splitlines(), 1)
    if DDL.search(line)
]
check(
    "no DDL in the app: only sql/ creates, alters or drops anything (and nothing runs TRUNCATE)",
    not hits,
    hits[:5],
)

BARE = re.compile(
    r"\b(FROM|JOIN|INTO|UPDATE)\s+(users|sessions|invites|mixtapes|mixtape_tracks|user_data|join_requests|song_offers|song_offer_items|song_requests|artists|albums|tracks|track_artists|items|item_relations|"
    r"item_types|relation_types|v_artists|v_albums|v_tracks|v_track_artists)\b"
)
hits = [
    f"{f.relative_to(PF)}:{n}: {line.strip()[:80]}"
    for f in files
    for n, line in enumerate(f.read_text(encoding="utf8").splitlines(), 1)
    if BARE.search(line)
]
check(
    "every table and view name in the app's SQL names its schema (totr. or media.)",
    not hits,
    hits[:5],
)
hits = [
    f"{f.relative_to(PF)}:{n}"
    for f in files
    for n, line in enumerate(f.read_text(encoding="utf8").splitlines(), 1)
    if re.search(r"\bpublic\.[a-z_]+", line) and not line.strip().startswith("#")
]
check("nothing in the app refers to the public schema", not hits, hits[:5])

# the scripts for fresh databases
FRESH = sorted((PF / "sql").glob("*.sql"))
check("the fresh-database scripts are found", len(FRESH) >= 7, [f.name for f in FRESH])


def statements(path):
    text = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf8"))
    return [s.strip() for s in text.split(";") if s.strip()]


problems = []
for path in FRESH:
    for st in statements(path):
        for m in re.finditer(
            r"\b(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|VIEW|SEQUENCE))\s+(?:IF NOT EXISTS\s+)?([A-Za-z_\"]+\.?[A-Za-z_]*)",
            st,
        ):
            if "." not in m.group(2):
                problems.append(f"{path.name}: {m.group(0)}")
        for m in re.finditer(r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+\w+\s+ON\s+([A-Za-z_\.]+)", st):
            if "." not in m.group(1):
                problems.append(f"{path.name}: index on {m.group(1)}")
        for m in re.finditer(r"\bALTER\s+TABLE\s+(?:IF EXISTS\s+)?([A-Za-z_\.]+)", st):
            if "." not in m.group(1):
                problems.append(f"{path.name}: alter {m.group(1)}")
        for m in re.finditer(r"\bREFERENCES\s+([A-Za-z_\.]+)", st):
            if "." not in m.group(1):
                problems.append(f"{path.name}: references {m.group(1)}")
        for m in re.finditer(
            r"\b(?:GRANT|REVOKE)\b.*?\bON\s+(?!DATABASE|SCHEMA|SEQUENCE|ALL)(.+?)\s+(?:TO|FROM)\b",
            st,
            re.S,
        ):
            for name in [x.strip() for x in m.group(1).split(",")]:
                if "." not in name:
                    problems.append(f"{path.name}: grant on {name}")
check(
    "every fresh-database script names the schema of every table, view, index, grant and reference",
    not problems,
    problems[:5],
)
check(
    "no script puts anything in public",
    all(
        not re.search(r"\bpublic\.", re.sub(r"--[^\n]*", "", p.read_text(encoding="utf8")))
        for p in FRESH
    ),
)
check(
    "the old released-date script is gone (released lives in item metadata)",
    not (PF / "sql" / "optional_released_date.sql").exists(),
)


# tools/schema_check.py
import schema_check as sc

GOOD = (
    [
        ("totr", t, "r")
        for t in (
            "users",
            "sessions",
            "invites",
            "mixtapes",
            "mixtape_tracks",
            "user_data",
            "join_requests",
            "song_offers",
            "song_offer_items",
            "song_requests",
        )
    ]
    + [("totr", v, "v") for v in ("v_artists", "v_albums", "v_tracks", "v_track_artists")]
    + [
        ("totr", "users_user_id_seq", "S"),
        ("totr", "mixtapes_mixtape_id_seq", "S"),
        ("totr", "mixtape_tracks_entry_id_seq", "S"),
    ]
    + [("media", t, "r") for t in ("items", "item_types", "item_relations", "relation_types")]
    + [
        ("public", "my_practice_table", "r"),
        ("ag_catalog", "ag_graph", "r"),
        ("demo_graph", "Song", "r"),
    ]
)
levels = lambda objs: [lvl for lvl, _ in sc.classify(objs)]
check(
    "schema_check: the finished layout has no problems (a sandbox schema and AGE are only information)",
    "PROBLEM" not in levels(GOOD),
    sc.classify(GOOD),
)
check(
    "schema_check: totr.schema_version (the migration bookkeeping) is allowed, and not required",
    "PROBLEM" not in levels(GOOD + [("totr", "schema_version", "r")]),
)
check(
    "schema_check: a TOTR table left in public is a problem",
    "PROBLEM" in levels(GOOD + [("public", "tracks", "r")]),
)
check(
    "schema_check: so is one of the old sequences",
    "PROBLEM" in levels(GOOD + [("public", "artists_artist_id_seq", "S")]),
)
check(
    "schema_check: an unknown table in totr is a problem",
    "PROBLEM" in levels(GOOD + [("totr", "extras", "r")]),
)
check(
    "schema_check: a missing view is a problem",
    "PROBLEM" in levels([o for o in GOOD if o[1] != "v_tracks"]),
)
check(
    "schema_check: a missing ontology table is a problem",
    "PROBLEM" in levels([o for o in GOOD if o[1] != "items"]),
)
check(
    "schema_check: extra objects in media are not TOTR's business",
    "PROBLEM" not in levels(GOOD + [("media", "places", "r")]),
)

finish()
