"""SQL injection guard: every query that is built from pieces of text (an f-string, .format, % or +)
is listed here with what it puts in, and each of those is a name from the code itself, never
something a person typed. A new one fails this test until someone reads it and adds it below. Values
always go in as parameters."""

import ast
import pathlib
import re
import sys

PF = pathlib.Path(__file__).resolve().parent.parent
SQL = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|REVOKE|GRANT|PRAGMA|TRUNCATE)\b|^\s*SET\s+\w+\s*=",
    re.I,
)
# catalog.py looks types up by a slug written in the code
LITERAL_SLUG = re.compile(r"^_(type|rel)\('[a-z_]+'\)$")
SKIP = {".venv", "__pycache__", "tests", "node_modules", "static", "vendor"}

# file -> the pieces each built query may contain, and why that is safe
# (main.py: view is a key of LIBRARY_VIEWS, a constant dict; track_order() is one fixed ORDER BY)
ALLOWED = {
    "schemaver.py": {"self.table"},  # the version table's name, a constant of the class
    "main.py": {
        "view",
        "track_order()",
    },
    "migrate.py": {
        "self.table",
        "int(m.version)",
        "int(upto)",
        "views",
        "rights",
    },  # views: the fixed list of our four views; rights: one of two literal strings
    "catalog.py": {
        "_OURS",
        "APP",
        "_OWN_REL_IDS",
    },  # fixed fragments built from constants at import (the app's own name, its relation slugs)
    "tools/migrate_db.py": {
        "table",
        "column",
        "override",
    },  # override: " OVERRIDING SYSTEM VALUE" or nothing
    "tools/move_to_postgres.py": {"table"},
    "tools/bench_library.py": {
        "A",
        "B",
        "S_",
        "ab",
        "ta",
        "pb",
        "nm",
        "artists",
        "n_albums",
        "n_songs",
        "albums_per",
        "songs_per",
        "artists + n_albums",
    },  # the benchmark's own numbers and ids, never typed by anyone
}


def built_queries():
    for path in sorted(PF.rglob("*.py")):
        rel = path.relative_to(PF)
        if SKIP & set(rel.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            first = None
            if (
                isinstance(node, ast.JoinedStr)
                and node.values
                and isinstance(node.values[0], ast.Constant)
                and isinstance(node.values[0].value, str)
            ):
                first = node.values[0].value
                pieces = {
                    ast.unparse(v.value) for v in node.values if isinstance(v, ast.FormattedValue)
                }
            elif (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, (ast.Add, ast.Mod))
                and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
            ):
                first = node.left.value
                pieces = {ast.unparse(node.right)}
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"
                and isinstance(node.func.value, ast.Constant)
                and isinstance(node.func.value.value, str)
            ):
                first = node.func.value.value
                pieces = {ast.unparse(a) for a in node.args} | {
                    ast.unparse(k.value) for k in node.keywords
                }
            if first is not None and SQL.match(first) and pieces:
                yield rel.as_posix(), node.lineno, pieces


fails = 0
found = list(built_queries())
for rel, line, pieces in found:
    ok = all(
        x in ALLOWED.get(rel, set()) or (rel == "catalog.py" and LITERAL_SLUG.match(x))
        for x in pieces
    )
    fails += not ok
    print(
        ("ok  " if ok else "BAD ")
        + f"{rel}:{line} builds a query from {sorted(pieces)}"
        + (
            ""
            if ok
            else "   <- not on the list: if it is a name from the code, add it to ALLOWED; if it can hold anything a person typed, use a parameter"
        )
    )
unused = [
    (f, p)
    for f, ps in ALLOWED.items()
    for p in ps
    if not any(r == f and p in pcs for r, _, pcs in found)
]
for f, p in unused:
    fails += 1
    print(f"BAD {f}: {p!r} is on the list but no longer used (remove it)")
print(
    f"\n{'ALL PASSED' if not fails else str(fails) + ' FAILED'} ({len(found)} built queries, all from names in the code)"
    if not fails
    else f"\nFAILED ({fails})"
)
sys.exit(1 if fails else 0)
