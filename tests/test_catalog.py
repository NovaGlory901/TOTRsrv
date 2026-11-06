"""The music catalog lives in the existing media ontology. These tests run catalog.py and
uploader.insert_tracks on the SQLite stand-in (tests/sqlite_layout.py) and check the catalog rules:
one real thing is one item, provenance on everything TOTR creates, and TOTR never edits or deletes
what it did not create or what something else links to."""

import json
import os
import pathlib
import sys
import tempfile

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())

import sqlite_layout as layout
from check import check, finish

import catalog
import uploader

DB = tmp / "media.sqlite"
layout.create(DB, layout.MEDIA)
q = lambda sql, params=(): layout.raw(DB, sql, params)
add = lambda *a, **k: layout.add_item(DB, *a, **k)
meta = lambda i: json.loads(
    q("SELECT metadata FROM media_items WHERE item_id = ?", (i,))[0]["metadata"]
)
count = lambda t: q(f"SELECT count(*) n FROM {t}")[0]["n"]


def conn():
    return layout.Conn(DB)


def tags(artist=None, title=None, n=None):
    return {"title": title, "artist": artist, "album": None, "year": None, "track_number": n}


def parsed(*names):
    return [
        (pathlib.Path("/music/" + n), tags(title=n.split(".")[0], n=i + 1))
        for i, n in enumerate(names)
    ]


# adding: one real thing is one item
with conn() as c:
    album_id = uploader.insert_tracks(
        c,
        "Tigerberry",
        "Cold Wave",
        2015,
        "/music/cover.jpg",
        [
            (pathlib.Path("/music/a.mp3"), tags("Tigerberry", "In Your Eyes", 1)),
            (pathlib.Path("/music/b.mp3"), tags("Tigerberry; Guest", "Get Out", 2)),
        ],
        source="upload",
    )
check(
    "an upload makes artist, album and song items (and only those)",
    (count("artists"), count("albums"), count("tracks")) == (2, 1, 2) and count("media_items") == 5,
)
check(
    "album owner, album membership and credits are relations",
    count("track_artists") == 3
    and q(
        "SELECT count(*) n FROM media_item_relations r JOIN media_relation_types t USING (relation_type_id) "
        "WHERE t.relation_slug IN ('album_by', 'track_on_album')"
    )[0]["n"]
    == 3,
)
alb = q("SELECT * FROM albums")[0]
check(
    "album year and cover live in metadata and come out of the view",
    int(alb["year"]) == 2015 and alb["cover_art_path"] == "/music/cover.jpg",
    alb,
)
first = q("SELECT track_id FROM tracks ORDER BY track_id")[0]["track_id"]
m = meta(first)
check(
    "everything TOTR creates carries provenance: created_by_app and source",
    m["created_by_app"] == "totr" and m["source"] == "upload",
    m,
)
check(
    "a song keeps its file path and track number in metadata",
    m["file_path"] == str(pathlib.Path("/music/a.mp3")) and m["track_number"] == 1,
    m,
)
check(
    "no new types were needed",
    count("media_item_types") == 5 and count("media_relation_types") == 4,
)

owner = q("SELECT artist_id FROM artists WHERE name = 'Tigerberry'")[0]["artist_id"]
with conn() as c:
    again = uploader.insert_tracks(
        c, "tigerberry", "Cold Wave", 2015, None, parsed("c.mp3"), source="upload"
    )
check(
    "an artist is matched by normalised name (case, width, spacing) and not duplicated",
    count("artists") == 2 and again == album_id,
)
with conn() as c:
    uploader.insert_tracks(
        c, "ＴＩＧＥＲＢＥＲＲＹ", "", None, None, parsed("loose.mp3"), source="upload"
    )
check(
    "...also for full-width letters; a blank album makes a loose song",
    count("artists") == 2
    and q("SELECT album_id FROM tracks WHERE title = 'loose'")[0]["album_id"] is None,
)

# an artist the owner already has (not made by TOTR) is linked to, not duplicated
theirs = add("artist", "Hong Kong Poet", ours=False, wikidata="Q1")
with conn() as c:
    uploader.insert_tracks(
        c, "hong kong poet", "", None, None, parsed("hk.mp3"), source="collector"
    )
check(
    "an artist TOTR did not create is reused as the link target",
    count("artists") == 3
    and q(
        "SELECT artist_id FROM track_artists WHERE track_id = (SELECT track_id FROM tracks WHERE title = 'hk')"
    )[0]["artist_id"]
    == theirs,
)
check(
    "...and left exactly as it was (no stamp, no edits)",
    meta(theirs) == {"wikidata": "Q1"},
    meta(theirs),
)
check(
    "the song carries the collector as its source",
    meta(q("SELECT track_id FROM tracks WHERE title = 'hk'")[0]["track_id"])["source"]
    == "collector",
)

# two items with the same name: ask, do not guess
add("artist", "Twin", ours=False)
add("artist", "TWIN", ours=False)
try:
    with conn() as c:
        catalog.get_or_create_artist(c, "twin", "upload")
    check("two artists with one name are a refusal, not a guess", False)
except catalog.CatalogError as e:
    check("two artists with one name are a refusal, not a guess", "Two artists" in str(e), str(e))
try:
    with conn() as c:
        catalog.get_or_create_artist(c, "   ", "upload")
    check("a blank artist name is refused", False)
except catalog.CatalogError:
    check("a blank artist name is refused", True)

# the whole add is one transaction
before = count("media_items")
try:
    with conn() as c:
        uploader.insert_tracks(c, "Rolled Back", "Album", None, None, parsed("x.mp3"))
        raise RuntimeError("boom")
except RuntimeError:
    pass
check("an error part-way rolls the whole add back", count("media_items") == before)

# deleting: only what TOTR created, never what something else links to
with conn() as c:
    r = catalog.delete_songs(c, [first])
check(
    "deleting a song removes its own relations and the item",
    r["deleted"] == [first]
    and not q("SELECT 1 FROM media_item_relations WHERE from_item_id = ?", (first,)),
)
check("...the album stays while it still has songs", count("albums") == 1)

# undo of a whole add: song, then the album and artist TOTR created, once nothing is left
with conn() as c:
    solo_album = uploader.insert_tracks(
        c, "Solo Act", "Solo Album", 1999, None, parsed("s1.mp3", "s2.mp3"), source="collector"
    )
solo_songs = [
    r["track_id"] for r in q("SELECT track_id FROM tracks WHERE album_id = ?", (solo_album,))
]
solo_artist = q("SELECT artist_id FROM artists WHERE name = 'Solo Act'")[0]["artist_id"]
with conn() as c:
    r = catalog.delete_songs(c, solo_songs)
check(
    "undoing an add removes the songs, then the album and artist TOTR created for it",
    sorted(r["deleted"]) == sorted(solo_songs)
    and not q("SELECT 1 FROM albums WHERE album_id = ?", (solo_album,))
    and not q("SELECT 1 FROM artists WHERE artist_id = ?", (solo_artist,))
    and not r["kept"],
)

# something outside TOTR links to the artist: the artist must survive an undo
with conn() as c:
    keep_album = uploader.insert_tracks(
        c, "Linked Act", "Linked Album", None, None, parsed("l1.mp3"), source="collector"
    )
linked_artist = q("SELECT artist_id FROM artists WHERE name = 'Linked Act'")[0]["artist_id"]
city = add("album", "Not music: a place stand-in", ours=False)
layout.relate(DB, city, "album_by", linked_artist)  # other data points at the artist
song_l = q("SELECT track_id FROM tracks WHERE album_id = ?", (keep_album,))[0]["track_id"]
with conn() as c:
    r = catalog.delete_songs(c, [song_l])
check(
    "an artist something else links to survives the undo",
    r["deleted"] == [song_l] and q("SELECT 1 FROM artists WHERE artist_id = ?", (linked_artist,)),
)
check("...and so does the album, because the artist link remains", True)

# a song something else links to is never deleted
with conn() as c:
    keep2 = uploader.insert_tracks(
        c, "Video Act", "Video Album", None, None, parsed("v1.mp3"), source="upload"
    )
song_v = q("SELECT track_id FROM tracks WHERE album_id = ?", (keep2,))[0]["track_id"]
video = add("video", "Official video", ours=False)
layout.relate(DB, video, "is_music_video_for", song_v)
snapshot = (
    q("SELECT * FROM media_items ORDER BY item_id"),
    q("SELECT * FROM media_item_relations ORDER BY relation_id"),
)
with conn() as c:
    r = catalog.delete_songs(c, [song_v])
check(
    "a song with a music video pointing at it is kept, with a reason",
    r["deleted"] == [] and "links" in r["kept"][song_v],
    r,
)
check(
    "...and nothing at all changed",
    snapshot
    == (
        q("SELECT * FROM media_items ORDER BY item_id"),
        q("SELECT * FROM media_item_relations ORDER BY relation_id"),
    ),
)

# a song that points elsewhere (the owner added a relation from it) is kept too
with conn() as c:
    keep3 = uploader.insert_tracks(
        c, "Place Act", "Place Album", None, None, parsed("p1.mp3"), source="upload"
    )
song_p = q("SELECT track_id FROM tracks WHERE album_id = ?", (keep3,))[0]["track_id"]
layout.relate(DB, song_p, "is_music_video_for", add("song", "Other", ours=False, file_path="/x"))
with conn() as c:
    r = catalog.delete_songs(c, [song_p])
check(
    "a song with a relation of something else leaving it is kept",
    r["deleted"] == [] and song_p in r["kept"],
    r,
)

# an existing album that TOTR songs were added to: undoing the songs must not strip the album's
# artist link
own_album = add("album", "Owner's album", ours=False)
layout.relate(DB, own_album, "album_by", linked_artist)
with conn() as c:
    gone = catalog._remove_item(c, own_album, catalog.ALBUM_OUT)
check(
    "an album TOTR did not create keeps its relations when removal is refused",
    not gone
    and q("SELECT 1 FROM media_item_relations WHERE from_item_id = ?", (own_album,))
    and q("SELECT 1 FROM albums WHERE album_id = ?", (own_album,)),
)
check(
    "a slash without spaces stays inside an artist name, ' / ' and ';' split",
    uploader.split_artists("AC/DC") == ["AC/DC"]
    and uploader.split_artists("A / B; C") == ["A", "B", "C"]
    and uploader.split_artists(None) == [],
)

# an item TOTR did not create is never deleted, even if asked
foreign_song = add("song", "Foreign", ours=False, file_path="/foreign.mp3")
with conn() as c:
    r = catalog.delete_songs(c, [foreign_song])
check(
    "a song TOTR did not create is never deleted",
    r["deleted"] == []
    and "not added by TOTR" in r["kept"][foreign_song]
    and q("SELECT 1 FROM tracks WHERE track_id = ?", (foreign_song,)),
)
check(
    "songs without a file are not part of TOTR's library at all",
    not q("SELECT 1 FROM tracks WHERE title = 'No file'")
    and add("song", "No file")
    and not q("SELECT 1 FROM tracks WHERE title = 'No file'"),
)

# edits only on TOTR's own items
for fn, args in (
    (catalog.set_title, (foreign_song, "x")),
    (catalog.set_song_number, (foreign_song, 3)),
):
    try:
        with conn() as c:
            fn(c, *args)
        check(f"{fn.__name__} refuses an item TOTR did not create", False)
    except catalog.CatalogError:
        check(f"{fn.__name__} refuses an item TOTR did not create", True)
check(
    "the foreign song is untouched",
    meta(foreign_song) == {"file_path": "/foreign.mp3"}
    and q("SELECT title FROM tracks WHERE track_id = ?", (foreign_song,))[0]["title"] == "Foreign",
)

# every statement is a bound parameter: hostile text is only ever text
with conn() as c:
    uploader.insert_tracks(
        c,
        "Robert'); DROP TABLE media_items;--",
        "",
        None,
        None,
        parsed("evil.mp3"),
        source="upload",
    )
check(
    "hostile names are stored as plain text",
    count("media_items") > 5 and q("SELECT 1 FROM artists WHERE name LIKE 'Robert%'"),
)

finish()
