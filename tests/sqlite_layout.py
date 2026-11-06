"""
A throwaway SQLite stand-in for the database layout the app expects (docs/database.md):

  schema totr   accounts, sessions, invites, mixtapes, mixtape_tracks, and TOTR's views over media
  schema media  the ontology: items, item_types, item_relations, relation_types

SQLite has no schemas, so `totr.users` becomes the table `totr_users` and `media.items` becomes
`media_items` (`lite()` rewrites the names). The views mirror sql/totr_views.sql, written for
SQLite. The tests also get the old table names (artists, albums, tracks, track_artists) as read-only
views so their assertions can say what they always said.
"""

import pathlib
import re
import sqlite3
from datetime import UTC, datetime

ACCOUNTS = """
CREATE TABLE totr_users (user_id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member','admin')), disabled BOOLEAN NOT NULL DEFAULT 0,
  permissions TEXT NOT NULL DEFAULT '', created_at TEXT, last_login_at TEXT);
CREATE UNIQUE INDEX totr_users_username_lower_idx ON totr_users (lower(username));
CREATE TABLE totr_sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES totr_users(user_id) ON DELETE CASCADE,
  csrf_token TEXT NOT NULL, created_at TEXT, last_seen TEXT, expires_at TEXT, ip TEXT, user_agent TEXT);
CREATE TABLE totr_invites (token_hash TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'member', permissions TEXT NOT NULL DEFAULT '',
  created_by INTEGER, created_at TEXT, expires_at TEXT, used_at TEXT, used_by INTEGER);
"""

# the accounts tables as they were before user_permissions.sql (the app still has a fallback for
# them)
ACCOUNTS_NO_PERMISSIONS = re.sub(r"\s*permissions TEXT NOT NULL DEFAULT '',", "", ACCOUNTS)

USER_DATA = """
CREATE TABLE totr_user_data (user_id INTEGER NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL, updated_ms INTEGER NOT NULL, saved_at TEXT, PRIMARY KEY (user_id, name));
"""

JOIN_REQUESTS = (
    pathlib.Path(__file__).resolve().parent.parent
    / "migrations"
    / "sqlite"
    / "0003_join_requests.sql"
).read_text(encoding="utf-8")

SONG_OFFERS = (
    pathlib.Path(__file__).resolve().parent.parent
    / "migrations"
    / "sqlite"
    / "0004_song_offers.sql"
).read_text(encoding="utf-8")

MIXTAPES = """
CREATE TABLE totr_mixtapes (mixtape_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL, created_at TEXT, updated_at TEXT);
CREATE UNIQUE INDEX totr_mixtapes_user_name_idx ON totr_mixtapes (user_id, lower(name));
CREATE TABLE totr_mixtape_tracks (entry_id INTEGER PRIMARY KEY AUTOINCREMENT, mixtape_id INTEGER NOT NULL, track_id INTEGER NOT NULL,
  position INTEGER NOT NULL, added_at TEXT);
"""

MEDIA = """
CREATE TABLE media_item_types (type_id INTEGER PRIMARY KEY AUTOINCREMENT, data_id TEXT NOT NULL DEFAULT (lower(hex(randomblob(16)))) UNIQUE, item_type_slug TEXT NOT NULL UNIQUE,
  description TEXT, parent_type_id INTEGER, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')), updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')));
CREATE TABLE media_relation_types (relation_type_id INTEGER PRIMARY KEY AUTOINCREMENT, data_id TEXT NOT NULL DEFAULT (lower(hex(randomblob(16)))) UNIQUE, relation_slug TEXT NOT NULL UNIQUE,
  description TEXT, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')), updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')));
CREATE TABLE media_items (item_id INTEGER PRIMARY KEY AUTOINCREMENT, data_id TEXT NOT NULL DEFAULT (lower(hex(randomblob(16)))) UNIQUE, type_id INTEGER NOT NULL REFERENCES media_item_types(type_id),
  title TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')), updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  title_search TEXT GENERATED ALWAYS AS (lower(title)) STORED);
CREATE TABLE media_item_relations (relation_id INTEGER PRIMARY KEY AUTOINCREMENT, from_item_id INTEGER NOT NULL REFERENCES media_items(item_id),
  relation_type_id INTEGER NOT NULL REFERENCES media_relation_types(relation_type_id), to_item_id INTEGER NOT NULL REFERENCES media_items(item_id),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')), UNIQUE (from_item_id, relation_type_id, to_item_id));
INSERT INTO media_item_types (item_type_slug) VALUES ('song'), ('video'), ('image'), ('artist'), ('album');
INSERT INTO media_relation_types (relation_slug) VALUES ('is_music_video_for'), ('track_on_album'), ('performed_by'), ('album_by');

CREATE VIEW totr_v_artists AS
SELECT i.item_id AS artist_id, i.title AS name, coalesce(i.metadata->>'created_by_app' = 'totr', 0) AS totr_created
FROM media_items i WHERE i.type_id = (SELECT type_id FROM media_item_types WHERE item_type_slug = 'artist');
CREATE VIEW totr_v_albums AS
SELECT al.item_id AS album_id, ab.to_item_id AS artist_id, al.title, al.metadata->>'year' AS year,
       al.metadata->>'cover_art_path' AS cover_art_path, coalesce(al.metadata->>'created_by_app' = 'totr', 0) AS totr_created
FROM media_items al
LEFT JOIN media_item_relations ab ON ab.from_item_id = al.item_id
  AND ab.relation_type_id = (SELECT relation_type_id FROM media_relation_types WHERE relation_slug = 'album_by')
WHERE al.type_id = (SELECT type_id FROM media_item_types WHERE item_type_slug = 'album');
CREATE VIEW totr_v_tracks AS
SELECT s.item_id AS track_id, tb.to_item_id AS album_id, s.metadata->>'track_number' AS track_number, s.title,
       s.metadata->>'file_path' AS file_path, s.metadata->>'released' AS released,
       coalesce(s.metadata->>'created_by_app' = 'totr', 0) AS totr_created
FROM media_items s
LEFT JOIN media_item_relations tb ON tb.from_item_id = s.item_id
  AND tb.relation_type_id = (SELECT relation_type_id FROM media_relation_types WHERE relation_slug = 'track_on_album')
WHERE s.type_id = (SELECT type_id FROM media_item_types WHERE item_type_slug = 'song') AND s.metadata->>'file_path' IS NOT NULL;
CREATE VIEW totr_v_track_artists AS
SELECT r.from_item_id AS track_id, r.to_item_id AS artist_id
FROM media_item_relations r JOIN media_items s ON s.item_id = r.from_item_id
WHERE r.relation_type_id = (SELECT relation_type_id FROM media_relation_types WHERE relation_slug = 'performed_by')
  AND s.type_id = (SELECT type_id FROM media_item_types WHERE item_type_slug = 'song') AND s.metadata->>'file_path' IS NOT NULL;

-- the old names, read-only, for test assertions
CREATE VIEW artists AS SELECT artist_id, name FROM totr_v_artists;
CREATE VIEW albums AS SELECT album_id, artist_id, title, year, cover_art_path FROM totr_v_albums;
CREATE VIEW tracks AS SELECT track_id, album_id, track_number, title, file_path FROM totr_v_tracks;
CREATE VIEW track_artists AS SELECT track_id, artist_id FROM totr_v_track_artists;
"""

_SCHEMA_NAME = re.compile(r"\b(totr|media)\.(?=[A-Za-z_])")


def lite(sql: str) -> str:
    """The app's SQL for SQLite: schema.name -> schema_name."""
    return _SCHEMA_NAME.sub(r"\1_", sql)


def create(path, *parts):
    c = sqlite3.connect(path)
    for part in parts:
        c.executescript(part)
    c.commit()
    c.close()


def conv(v):
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(v, bool):
        return int(v)
    return v


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


class Conn:
    """What psycopg's connection looks like to the app: execute(sql with %s, params), `with` commits
    or rolls back. A list parameter after `= ANY(%s)` becomes an IN list."""

    def __init__(self, path):
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        assert "%s" in sql or not params, "parameters must be bound, never formatted into the query"
        sql = lite(sql).replace("= ANY(%s)", "IN %s")
        parts, flat, out = sql.split("%s"), [], []
        for i, p in enumerate(params):
            if isinstance(p, (list, tuple)):
                out.append(parts[i] + ("(" + ",".join("?" * len(p)) + ")" if p else "(NULL)"))
                flat += [conv(x) for x in p]
            else:
                out.append(parts[i] + "?")
                flat.append(conv(p))
        out.append(parts[len(params)])
        return Cur(self.c.execute("".join(out), flat))

    def rollback(self):
        self.c.rollback()

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        (self.c.rollback if et else self.c.commit)()
        self.c.close()


def shim(path):
    """A connect() replacement: `service.connect = sqlite_layout.shim(DB_PATH)`."""
    return lambda: Conn(path)


def raw(path, sql, params=()):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.commit()
    conn.close()
    return rows


# seeding the ontology for tests
import json


def add_item(path, slug, title, ours=True, **meta):
    """An artist/album/song/... item. `ours` marks it as created by TOTR (metadata.created_by_app)."""
    if ours:
        meta = {"created_by_app": "totr", "source": "test", **meta}
    c = sqlite3.connect(path)
    cur = c.execute(
        "INSERT INTO media_items (type_id, title, metadata) VALUES "
        "((SELECT type_id FROM media_item_types WHERE item_type_slug = ?), ?, ?)",
        (slug, title, json.dumps(meta, ensure_ascii=False)),
    )
    c.commit()
    item_id = cur.lastrowid
    c.close()
    return item_id


def relate(path, from_id, slug, to_id):
    c = sqlite3.connect(path)
    c.execute(
        "INSERT INTO media_item_relations (from_item_id, relation_type_id, to_item_id) VALUES "
        "(?, (SELECT relation_type_id FROM media_relation_types WHERE relation_slug = ?), ?)",
        (from_id, slug, to_id),
    )
    c.commit()
    c.close()


def live_ready():
    """True once the real database has the TOTR layout, so the checks that read the real database
    can run."""
    try:
        import db

        with db.get_conn() as conn:
            conn.execute("SELECT 1 FROM totr.v_tracks LIMIT 1").fetchone()
        return True
    except Exception:
        return False
