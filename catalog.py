"""
Writes to the music catalog. The catalog lives in the existing media ontology (schema `media`: items,
item_types, item_relations, relation_types), not in TOTR tables. Rules this file keeps:

  * One real thing is one item. An artist is looked up among ALL artist items (by normalised name)
    before one is created,
    so an artist the owner already has is linked to, never duplicated. Two matches are an error shown to the owner, not a guess.
  * Only generic types and relations that already exist: song, album, artist; track_on_album,
    performed_by, album_by.
  * Everything TOTR creates carries metadata  created_by_app = "totr"  and  source  (upload,
    collector, library-import ...).
  * TOTR edits or deletes only items it created, and only relations that hang off such items and are
    of its own three types.
    It never changes the ontology tables' structure and does not rely on ON DELETE CASCADE: deletes are done here, in code,
    and refuse when something outside TOTR links to the item (a music video, a place, a job ...).
  * Reads go through the views in schema `totr` (sql/totr_views.sql): v_artists, v_albums, v_tracks,
    v_track_artists.

Every value is a bound parameter. Type and relation names in the SQL come only from the constants
below. Dates and numbers inside metadata are read with ->> so the same SQL runs on SQLite in the
tests.
"""

import json

from textnorm import norm

APP = "totr"
OWN_RELATIONS = ("performed_by", "track_on_album", "album_by")
SONG_OUT = ("performed_by", "track_on_album")  # relations that may leave a song TOTR created
ALBUM_OUT = ("album_by",)
ARTIST_OUT = ()


class CatalogError(Exception):
    """A refusal that is safe to show to the user."""


def _type(slug: str) -> str:
    return f"(SELECT type_id FROM media.item_types WHERE item_type_slug = '{slug}')"


def _rel(slug: str) -> str:
    return f"(SELECT relation_type_id FROM media.relation_types WHERE relation_slug = '{slug}')"


_OWN_REL_IDS = (
    "(SELECT relation_type_id FROM media.relation_types WHERE relation_slug IN "
    + "("
    + ", ".join(f"'{s}'" for s in OWN_RELATIONS)
    + "))"
)
_OURS = f"coalesce(metadata->>'created_by_app', '') = '{APP}'"

# Every statement is here so the tests can PREPARE them against the real schema once it exists.
# never strips relations from an item TOTR did not create
SQL = {
    "sel_artists": "SELECT artist_id, name, totr_created FROM totr.v_artists",
    "ins_artist": f"INSERT INTO media.items (type_id, title, metadata) VALUES ({_type('artist')}, %s, %s) RETURNING item_id",
    "ins_album": f"INSERT INTO media.items (type_id, title, metadata) VALUES ({_type('album')}, %s, %s) RETURNING item_id",
    "ins_song": f"INSERT INTO media.items (type_id, title, metadata) VALUES ({_type('song')}, %s, %s) RETURNING item_id",
    "ins_performed_by": "INSERT INTO media.item_relations (from_item_id, relation_type_id, to_item_id) "
    f"VALUES (%s, {_rel('performed_by')}, %s) ON CONFLICT (from_item_id, relation_type_id, to_item_id) DO NOTHING",
    "ins_track_on_album": "INSERT INTO media.item_relations (from_item_id, relation_type_id, to_item_id) "
    f"VALUES (%s, {_rel('track_on_album')}, %s) ON CONFLICT (from_item_id, relation_type_id, to_item_id) DO NOTHING",
    "ins_album_by": "INSERT INTO media.item_relations (from_item_id, relation_type_id, to_item_id) "
    f"VALUES (%s, {_rel('album_by')}, %s) ON CONFLICT (from_item_id, relation_type_id, to_item_id) DO NOTHING",
    "sel_album": "SELECT album_id, totr_created FROM totr.v_albums WHERE artist_id = %s AND title = %s ORDER BY album_id LIMIT 1",
    "sel_song": "SELECT track_id, album_id, file_path, title, track_number, totr_created FROM totr.v_tracks WHERE track_id = %s",
    "sel_song_by_path": "SELECT track_id, totr_created FROM totr.v_tracks WHERE file_path = %s",
    "sel_album_song_path": "SELECT file_path FROM totr.v_tracks WHERE album_id = %s ORDER BY track_id LIMIT 1",
    "sel_album_row": "SELECT album_id, totr_created FROM totr.v_albums WHERE album_id = %s",
    "sel_artist_row": "SELECT artist_id, name, totr_created FROM totr.v_artists WHERE artist_id = %s",
    "sel_credit_artists": "SELECT artist_id FROM totr.v_track_artists WHERE track_id = %s",
    "sel_meta": f"SELECT metadata FROM media.items WHERE item_id = %s AND type_id = %s AND {_OURS}",
    "upd_meta": f"UPDATE media.items SET metadata = %s WHERE item_id = %s AND {_OURS}",
    "upd_title": f"UPDATE media.items SET title = %s WHERE item_id = %s AND type_id = {_type('song')} AND {_OURS}",
    "type_song": f"SELECT {_type('song')} AS type_id",
    "type_album": f"SELECT {_type('album')} AS type_id",
    # what links to an item that TOTR would be about to delete
    "count_in_foreign": "SELECT count(*) AS n FROM media.item_relations r JOIN media.items f ON f.item_id = r.from_item_id "
    f"WHERE r.to_item_id = %s AND (r.relation_type_id NOT IN {_OWN_REL_IDS} "
    f"OR coalesce(f.metadata->>'created_by_app', '') <> '{APP}')",
    "count_in_any": "SELECT count(*) AS n FROM media.item_relations WHERE to_item_id = %s",
    "count_out_foreign": "SELECT count(*) AS n FROM media.item_relations r JOIN media.relation_types t ON t.relation_type_id = r.relation_type_id "
    "WHERE r.from_item_id = %s AND t.relation_slug NOT IN (%s, %s)",
    "sel_out_targets": "SELECT to_item_id FROM media.item_relations WHERE from_item_id = %s",
    "del_out_relations": f"DELETE FROM media.item_relations WHERE from_item_id = %s AND EXISTS (SELECT 1 FROM media.items WHERE item_id = from_item_id AND {_OURS})",
    "count_out_any": "SELECT count(*) AS n FROM media.item_relations WHERE from_item_id = %s",
    "del_item": f"DELETE FROM media.items WHERE item_id = %s AND {_OURS}",
    "move_credits": "INSERT INTO media.item_relations (from_item_id, relation_type_id, to_item_id) "
    "SELECT from_item_id, relation_type_id, CAST(%s AS integer) FROM media.item_relations "
    f"WHERE to_item_id = %s AND relation_type_id IN {_OWN_REL_IDS} "
    "ON CONFLICT (from_item_id, relation_type_id, to_item_id) DO NOTHING",
    "del_in_relations": "DELETE FROM media.item_relations WHERE to_item_id = %s",
}


def _lock(conn):
    """Serialises 'find an artist, else create it' between two uploads: media has no unique index on
    names (it is an existing table)."""
    if hasattr(conn, "pgconn"):
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('totr-catalog'))")


def dumps(meta: dict) -> str:
    return json.dumps({k: v for k, v in meta.items() if v is not None}, ensure_ascii=False)


def loads(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    return json.loads(value) if value else {}


def _meta(source: str, extra: dict | None = None, **fields) -> str:
    return dumps({"created_by_app": APP, "source": source, **(extra or {}), **fields})


def get_or_create_artist(conn, name: str, source: str, extra: dict | None = None) -> int:
    """The artist item with this name (case, width and spacing ignored), made if none exists."""
    name = (name or "").strip()
    if not name:
        raise CatalogError("An artist needs a name.")
    _lock(conn)
    key = norm(name)
    found = [
        r["artist_id"]
        for r in conn.execute(SQL["sel_artists"]).fetchall()
        if norm(r["name"]) == key
    ]
    if len(found) > 1:
        raise CatalogError(
            f'Two artists in your database are called "{name}" (items {", ".join(map(str, sorted(found)))}). '
            "Say which one is meant, or merge them, before adding music."
        )
    if found:
        return found[0]
    return conn.execute(SQL["ins_artist"], (name, _meta(source, extra))).fetchone()["item_id"]


def get_or_create_album(
    conn, owner_id: int, title: str, year, cover_path, source: str, extra: dict | None = None
) -> int:
    row = conn.execute(SQL["sel_album"], (owner_id, title)).fetchone()
    if row:
        return row["album_id"]
    album_id = conn.execute(
        SQL["ins_album"],
        (
            title,
            _meta(source, extra, year=year, cover_art_path=str(cover_path) if cover_path else None),
        ),
    ).fetchone()["item_id"]
    conn.execute(SQL["ins_album_by"], (album_id, owner_id))
    return album_id


def add_song(
    conn,
    album_id,
    artist_ids,
    title: str,
    track_number,
    file_path: str,
    source: str,
    extra: dict | None = None,
) -> int:
    song_id = conn.execute(
        SQL["ins_song"],
        (title, _meta(source, extra, file_path=str(file_path), track_number=track_number)),
    ).fetchone()["item_id"]
    if album_id:
        conn.execute(SQL["ins_track_on_album"], (song_id, album_id))
    for artist_id in artist_ids:
        conn.execute(SQL["ins_performed_by"], (song_id, artist_id))
    return song_id


def song(conn, track_id: int):
    return conn.execute(SQL["sel_song"], (int(track_id),)).fetchone()


def song_by_path(conn, path: str):
    return conn.execute(SQL["sel_song_by_path"], (path,)).fetchone()


def _ours(row) -> bool:
    return bool(row["totr_created"])


def require_ours(row, what: str, gone: str):
    if row is None:
        raise CatalogError(gone)
    if not _ours(row):
        raise CatalogError(
            f"That {what} was not added by TOTR, so TOTR leaves it alone. Change it where you keep that data."
        )


def set_title(conn, track_id: int, title: str):
    require_ours(song(conn, track_id), "song", "That song no longer exists.")
    conn.execute(SQL["upd_title"], (title, int(track_id)))


def _set_meta(conn, type_sql_key: str, item_id: int, key: str, value):
    type_id = conn.execute(SQL[type_sql_key]).fetchone()["type_id"]
    row = conn.execute(SQL["sel_meta"], (int(item_id), type_id)).fetchone()
    if row is None:
        return False
    meta = loads(row["metadata"])
    meta[key] = value
    conn.execute(SQL["upd_meta"], (dumps(meta), int(item_id)))
    return True


def set_song_number(conn, track_id: int, number: int):
    require_ours(song(conn, track_id), "song", "That song no longer exists.")
    _set_meta(conn, "type_song", track_id, "track_number", int(number))


def set_album_year(conn, album_id: int, year: int):
    require_ours(
        conn.execute(SQL["sel_album_row"], (int(album_id),)).fetchone(),
        "album",
        "That album no longer exists.",
    )
    _set_meta(conn, "type_album", album_id, "year", int(year))


def set_album_cover(conn, album_id: int, path: str):
    require_ours(
        conn.execute(SQL["sel_album_row"], (int(album_id),)).fetchone(),
        "album",
        "That album no longer exists.",
    )
    _set_meta(conn, "type_album", album_id, "cover_art_path", str(path))


# deleting (only what TOTR created, never what something else links to)


def _remove_item(conn, item_id: int, out_allowed: tuple) -> bool:
    """Deletes one item and its own outgoing relations, but only if TOTR created it, every relation
    leaving it is one of `out_allowed`, and nothing links to it except TOTR's own relations from
    TOTR's own items."""
    if conn.execute(SQL["count_in_any"], (item_id,)).fetchone()["n"]:
        return False
    if out_allowed:
        pad = (out_allowed + (out_allowed[-1],))[:2]
        if conn.execute(SQL["count_out_foreign"], (item_id, *pad)).fetchone()["n"]:
            return False
    elif conn.execute(SQL["count_out_any"], (item_id,)).fetchone()["n"]:
        return False
    conn.execute(SQL["del_out_relations"], (item_id,))
    return conn.execute(SQL["del_item"], (item_id,)).rowcount == 1


def delete_songs(conn, track_ids) -> dict:
    """Removes songs. Returns {"deleted": [ids], "kept": {id: reason}}. Albums and artists TOTR
    created that are left with nothing at all are removed too (that is what undoing an add means);
    ones anything else touches stay."""
    deleted, kept, albums, artists = [], {}, set(), set()
    for track_id in [int(t) for t in track_ids]:
        row = song(conn, track_id)
        if row is None:
            continue
        if not _ours(row):
            kept[track_id] = "not added by TOTR"
            continue
        album_id = row["album_id"]
        credited = [
            r["artist_id"] for r in conn.execute(SQL["sel_credit_artists"], (track_id,)).fetchall()
        ]
        if _remove_item(conn, track_id, SONG_OUT):
            deleted.append(track_id)
            if album_id:
                albums.add(album_id)
            artists.update(credited)
        else:
            kept[track_id] = "something else in your database links to it"
    for album_id in albums:
        owners = [
            r["to_item_id"] for r in conn.execute(SQL["sel_out_targets"], (album_id,)).fetchall()
        ]
        if _remove_item(conn, album_id, ALBUM_OUT):
            artists.update(owners)
    for artist_id in artists:
        row = conn.execute(SQL["sel_artist_row"], (artist_id,)).fetchone()
        if row is not None and _ours(row):
            _remove_item(conn, artist_id, ARTIST_OUT)
    return {"deleted": deleted, "kept": kept}


def merge_artists(conn, keep: int, merge) -> dict:
    """Moves TOTR's albums and credits from the duplicates to `keep` and removes each duplicate. A
    duplicate that TOTR did not create, or that anything else links to, is left alone and reported."""
    keep_row = conn.execute(SQL["sel_artist_row"], (int(keep),)).fetchone()
    if keep_row is None:
        raise CatalogError("The artist to keep no longer exists, so nothing was merged.")
    merged, kept = [], {}
    for m in [int(x) for x in merge if int(x) != int(keep)]:
        row = conn.execute(SQL["sel_artist_row"], (m,)).fetchone()
        if row is None:
            continue
        if not _ours(row):
            kept[m] = "not added by TOTR"
            continue
        if (
            conn.execute(SQL["count_in_foreign"], (m,)).fetchone()["n"]
            or conn.execute(SQL["count_out_any"], (m,)).fetchone()["n"]
        ):
            kept[m] = "something else in your database links to it"
            continue
        conn.execute(SQL["move_credits"], (int(keep), m))
        conn.execute(SQL["del_in_relations"], (m,))
        conn.execute(SQL["del_item"], (m,))
        merged.append(m)
    return {"merged": merged, "kept": kept}
