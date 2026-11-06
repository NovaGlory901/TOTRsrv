"""The `media` schema is a fixed design and must be the same in every install.
sql/reference/media_schema_reference.sql is the pg_dump of it. This checks, without needing a
PostgreSQL server, that what a new install builds has the same tables, columns (name, type, default,
null, order), identity sequences, constraints, indexes, trigger and function as that reference, and
that the SQLite copy has the same columns, indexes and triggers. (The check against a real
PostgreSQL install is in deploy/lab-test.sh, which pg_dumps the result and compares it with the
reference.)

Known, deliberate differences: the two pg_bigm title indexes are made only where the extension
exists (so they are checked for being in the migration, not for being created); SQLite cannot copy
types, NORMALIZE or trigger functions (see the comment at the top of
migrations/sqlite/0001_base.sql)."""

import pathlib
import re
import sqlite3
import sys

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
from check import check, finish


def squeeze(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().rstrip(",")


def table_bodies(sql: str, create=r"CREATE TABLE(?: IF NOT EXISTS)?") -> dict:
    """{ 'items': body text } for every CREATE TABLE media.<name> ( ... );"""
    out = {}
    for m in re.finditer(create + r"\s+media\.(\w+)\s*\(", sql):
        depth, i = 1, m.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            i += 1
        out[m.group(1)] = sql[m.end() : i - 1]
    return out


def split_top(body: str) -> list:
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return parts + [cur] if cur.strip() else parts


IDENTITY = re.compile(r"\s*GENERATED ALWAYS AS IDENTITY\s*\((.*?)\)", re.S)


def columns(sql: str) -> dict:
    """{table: [(column, normalised definition)]}. The reference writes an identity column as
    `integer NOT NULL` and adds the identity later; the migration writes it inline, so the inline
    clause is taken out (it is compared on its own, below) and NOT NULL, which an identity column
    always is, is put back."""
    out = {}
    for table, body in table_bodies(sql).items():
        cols = []
        for part in split_top(body):
            part = squeeze(part)
            if re.match(r"(CONSTRAINT|PRIMARY|UNIQUE|FOREIGN)\b", part):
                continue
            name, rest = part.split(" ", 1)
            if IDENTITY.search(rest):
                rest = squeeze(IDENTITY.sub("", rest)) + " NOT NULL"
            cols.append((name, squeeze(rest)))
        out[table] = cols
    return out


def reference_identity(ref: str) -> dict:
    """{table.column: squeezed sequence options} from the reference's `ALTER TABLE ... ADD GENERATED
    ALWAYS AS IDENTITY (...)`."""
    return {
        f"{m.group(1)}.{m.group(2)}": squeeze(m.group(3))
        for m in re.finditer(
            r"ALTER TABLE media\.(\w+) ALTER COLUMN (\w+) ADD GENERATED ALWAYS AS IDENTITY \((.*?)\);",
            ref,
            re.S,
        )
    }


def migration_identity(mig: str) -> dict:
    out = {}
    for table, body in table_bodies(mig).items():
        for part in split_top(body):
            m = re.match(
                r"\s*(\w+)\s+integer\s+GENERATED ALWAYS AS IDENTITY\s*\((.*)\)\s*$", part, re.S
            )
            if m:
                out[f"{table}.{m.group(1)}"] = squeeze(m.group(2))
    return out


def constraints_ref_full(ref: str) -> dict:
    return {
        m.group(2): (m.group(1), squeeze(m.group(3)))
        for m in re.finditer(
            r"ALTER TABLE ONLY media\.(\w+)\s+ADD CONSTRAINT (\w+) (.*?);", ref, re.S
        )
    }


def constraints_mig(mig: str) -> dict:
    out = {}
    for table, body in table_bodies(mig).items():
        for part in split_top(body):
            m = re.match(r"\s*CONSTRAINT (\w+) (.*)$", part, re.S)
            if m:
                out[m.group(1)] = (table, squeeze(m.group(2)))
    return out


def indexes(sql: str) -> dict:
    return {
        m.group(1): squeeze(m.group(0))
        .replace("CREATE UNIQUE INDEX IF NOT EXISTS", "CREATE UNIQUE INDEX")
        .replace("CREATE INDEX IF NOT EXISTS", "CREATE INDEX")
        for m in re.finditer(
            r"CREATE (?:UNIQUE )?INDEX (?:IF NOT EXISTS )?(\w+) ON media\.\w+ USING [^;]*;", sql
        )
    }


def triggers(sql: str) -> dict:
    return {
        m.group(1): squeeze(m.group(0)).replace("CREATE OR REPLACE TRIGGER", "CREATE TRIGGER")
        for m in re.finditer(r"CREATE (?:OR REPLACE )?TRIGGER (\w+) BEFORE UPDATE[^;]*;", sql)
    }


def function_body(sql: str) -> str:
    m = re.search(
        r"CREATE (?:OR REPLACE )?FUNCTION media\.set_updated_at\(\) RETURNS trigger\s+LANGUAGE plpgsql\s+AS \$\$(.*?)\$\$;",
        sql,
        re.S,
    )
    return squeeze(m.group(1)) if m else ""


def read_sql(*parts) -> str:
    """The file without its `--` comment lines (a statement that is only mentioned in a comment is
    not there)."""
    text = (PF.joinpath(*parts)).read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


ref = read_sql("sql", "reference", "media_schema_reference.sql")
mig = read_sql("migrations", "postgres", "0001_base.sql")
fresh = read_sql("sql", "media_ontology.sql")

# PostgreSQL: the migration, and the older hand-run script
ref_cols = columns(ref)
check(
    "the reference has the four media tables",
    sorted(ref_cols) == ["item_relations", "item_types", "items", "relation_types"],
    sorted(ref_cols),
)
for label, text in (("migration 0001", mig), ("sql/media_ontology.sql", fresh)):
    cols = columns(text)
    check(f"{label}: the same tables", sorted(cols) == sorted(ref_cols), sorted(cols))
    for table in ref_cols:
        check(
            f"{label}: media.{table} has the same columns in the same order (name, type, default, null)",
            cols.get(table) == ref_cols[table],
            f"\n   reference: {ref_cols[table]}\n   here:      {cols.get(table)}",
        )
    check(
        f"{label}: the same identity sequences",
        migration_identity(text) == reference_identity(ref),
        (migration_identity(text), reference_identity(ref)),
    )
    c_ref, c_here = constraints_ref_full(ref), constraints_mig(text)
    check(
        f"{label}: the same constraints (names, tables and definitions)",
        c_ref == c_here,
        f"\n   only in reference: { {k: v for k, v in c_ref.items() if c_here.get(k) != v} }\n   only here: { {k: v for k, v in c_here.items() if c_ref.get(k) != v} }",
    )
    i_ref, i_here = indexes(ref), indexes(text)
    plain = lambda d: {k: v for k, v in d.items() if "bigm" not in k}
    check(
        f"{label}: the same indexes (apart from pg_bigm)",
        plain(i_ref) == plain(i_here),
        f"\n   reference: {plain(i_ref)}\n   here:      {plain(i_here)}",
    )
    check(
        f"{label}: the pg_bigm indexes are there, conditional on the extension",
        all(n in text for n in ("idx_items_title_bigm", "idx_items_title_search_bigm"))
        and "gin_bigm_ops" in text
        and "pg_extension" in text,
    )
    check(
        f"{label}: the same updated_at triggers",
        triggers(ref) == triggers(text),
        (triggers(ref), triggers(text)),
    )
    check(
        f"{label}: the same set_updated_at() function",
        function_body(ref) == function_body(text) != "",
        (function_body(ref), function_body(text)),
    )
    comment = re.search(r"COMMENT ON SCHEMA media IS '([^']*)'", ref).group(1)
    check(
        f"{label}: the same comment on the schema",
        f"COMMENT ON SCHEMA media IS '{comment}'" in text,
    )
    check(
        f"{label}: no extra media table, function or trigger beyond the reference",
        len(re.findall(r"CREATE (?:OR REPLACE )?(?:TRIGGER|FUNCTION media\.)", text))
        == len(re.findall(r"CREATE (?:OR REPLACE )?(?:TRIGGER|FUNCTION media\.)", ref)),
    )

# the starter rows (the same five kinds of item and four kinds of link as the reference dump)
want_types = ["song", "video", "image", "artist", "album"]
want_rels = ["is_music_video_for", "track_on_album", "performed_by", "album_by"]
for label, text in (("migration 0001", mig), ("sql/media_ontology.sql", fresh)):
    types = re.findall(
        r"^\s*\('(\w+)',\s+'",
        text.split("INSERT INTO media.item_types", 1)[1].split("ON CONFLICT", 1)[0],
        re.M,
    )
    rels = re.findall(
        r"^\s*\('(\w+)',\s+'",
        text.split("INSERT INTO media.relation_types", 1)[1].split("ON CONFLICT", 1)[0],
        re.M,
    )
    check(
        f"{label}: the starter item types and relation types are the reference's, in order",
        types == want_types and rels == want_rels,
        (types, rels),
    )

# SQLite: the same columns, indexes and triggers
import migrate  # noqa: E402

lite = sqlite3.connect(":memory:")
migrate.upgrade(lite)
for table, cols in ref_cols.items():
    have = [r[1] for r in lite.execute(f"PRAGMA table_xinfo(media_{table})")]
    check(
        f"SQLite: media_{table} has the same column names in the same order",
        have == [c[0] for c in cols],
        (have, [c[0] for c in cols]),
    )
names = {
    r[0]
    for r in lite.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
    )
}
check(
    "SQLite: the same named indexes (apart from pg_bigm)",
    {k for k in indexes(ref) if "bigm" not in k} <= names,
    names,
)
trg = {r[0] for r in lite.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
check("SQLite: the same three updated_at triggers", set(triggers(ref)) <= trg, trg)
check(
    "SQLite: the starter rows are the reference's",
    [r[0] for r in lite.execute("SELECT item_type_slug FROM media_item_types ORDER BY type_id")]
    == want_types
    and [
        r[0]
        for r in lite.execute(
            "SELECT relation_slug FROM media_relation_types ORDER BY relation_type_id"
        )
    ]
    == want_rels,
)
lite.execute("INSERT INTO media_items (type_id, title) VALUES (1, 'a')")
row = lite.execute(
    "SELECT data_id, created_at, updated_at, title_search FROM media_items"
).fetchone()
check(
    "SQLite: a new item gets a uuid, both timestamps and title_search by itself",
    all(row)
    and re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", row[0]
    ),
    row,
)
lite.execute(
    "UPDATE media_items SET created_at = '2000-01-01T00:00:00Z', updated_at = '2000-01-01T00:00:00Z'"
)
lite.execute("UPDATE media_items SET title = 'b'")
check(
    "SQLite: changing an item sets updated_at (the trigger)",
    lite.execute("SELECT updated_at FROM media_items").fetchone()[0] > "2001",
)

finish()
