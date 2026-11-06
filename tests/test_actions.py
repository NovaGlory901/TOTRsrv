import os
import pathlib
import re
import sys
import tempfile

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["MUSIC_ROOT"] = str(tmp / "music")
os.environ["DB_MAINT_USER"] = "x"
os.environ["DB_MAINT_PASSWORD"] = "y"
os.environ["UPLOAD_PASSWORD"] = "pw"

import dotenv

# the maintenance role's real login, for the PREPARE checks only
REAL_ENV = dotenv.dotenv_values(pathlib.Path(PF) / ".env")
import sqlite_layout as layout
from check import check, finish

import catalog
import db
import uploader
from spiders import actions


# 1. every SQL statement parses against the real schema (PREPARE never executes anything)
# Needs a database with the TOTR layout (views and media grants) and the maintenance role's login
# in .env, because the statements touch media. Without them these are skipped, not failed.
def maint_connection():
    if not (REAL_ENV.get("DB_MAINT_USER") and REAL_ENV.get("DB_MAINT_PASSWORD")):
        return None
    saved = {k: os.environ.get(k) for k in ("DB_MAINT_USER", "DB_MAINT_PASSWORD")}
    os.environ["DB_MAINT_USER"], os.environ["DB_MAINT_PASSWORD"] = (
        REAL_ENV["DB_MAINT_USER"],
        REAL_ENV["DB_MAINT_PASSWORD"],
    )
    try:
        return db.get_maint_conn()
    finally:
        for k, v in saved.items():
            os.environ[k] = v


real_conn = maint_connection() if layout.live_ready() else None
if real_conn is not None:
    with real_conn as conn:
        for name, sql in actions.SQL.items():
            n = [0]

            def repl(_m, n=n):
                n[0] += 1
                return f"${n[0]}"

            try:
                conn.execute(f"PREPARE stmt_{name} AS " + re.sub(r"%s", repl, sql))
                conn.execute(f"DEALLOCATE stmt_{name}")
                check(f"sql parses: {name}", True)
            except Exception as e:
                conn.rollback()
                check(f"sql parses: {name}", False, str(e).splitlines()[0])
else:
    print(
        "skip  sql parses against the real schema  (needs a database with the TOTR layout and DB_MAINT_* in .env)"
    )

# 2. handlers, run for real against the SQLite stand-in
DB = tmp / "music.sqlite"
layout.create(DB, layout.MEDIA)
add = lambda *a, **k: layout.add_item(DB, *a, **k)
q = lambda sql, params=(): layout.raw(DB, sql, params)
db.get_maint_conn = layout.shim(DB)
live = lambda: [r["title"] for r in q("SELECT title FROM tracks ORDER BY track_id")]

root = pathlib.Path(os.environ["MUSIC_ROOT"])
(root / "A").mkdir(parents=True)
keep_f, dup_f, shared_f, ext_f = (
    root / "A" / "keep.wav",
    root / "A" / "dup.wav",
    root / "A" / "shared.wav",
    root / "A" / "theirs.wav",
)
for f in (keep_f, dup_f, shared_f, ext_f):
    f.write_bytes(b"x")
outside = tmp / "outside.wav"
outside.write_bytes(b"x")

artist = add("artist", "Band")
album = add("album", "Album", year=2000)
layout.relate(DB, album, "album_by", artist)


def song(title, path, ours=True, **kw):
    i = add("song", title, ours=ours, file_path=str(path), **kw)
    layout.relate(DB, i, "performed_by", artist)
    layout.relate(DB, i, "track_on_album", album)
    return i


s_keep, s_dup, s_shared = song("Keep", keep_f), song("Dup", dup_f), song("Shared", shared_f)
s_theirs = song("Theirs", ext_f, ours=False)  # an item TOTR did not create

# remove_tracks removes the items, trashes only the file nobody else uses
hooks = []
with db.get_maint_conn() as conn:
    # another song uses shared.wav
    shared_twin = catalog.add_song(
        conn, album, [artist], "Shared again", None, str(shared_f), "test"
    )
    msg = actions.remove_tracks(
        conn, {"keep": s_keep, "remove": [s_dup, s_shared], "trash_files": True}, hooks
    )
check(
    "remove_tracks removes the duplicate songs",
    "Dup" not in live() and "Shared" not in live() and "Keep" in live(),
    live(),
)
check(
    "remove_tracks queues exactly one file for trash (the one no other song uses)", len(hooks) == 1
)
check("nothing trashed before commit", dup_f.exists())
print("     ", hooks[0]())
check(
    "unused duplicate file moved to .trash",
    not dup_f.exists() and any(root.glob(".trash/*/A/dup.wav")),
)
check("a file another song still uses is kept", shared_f.exists())
check("keeper untouched", keep_f.exists() and "Keep" in live())
check(
    "the songs' own relations went with them",
    not q("SELECT 1 FROM media_item_relations WHERE from_item_id IN (?, ?)", (s_dup, s_shared)),
)
check(
    "the album and artist stay while songs remain",
    q("SELECT 1 FROM albums") and q("SELECT 1 FROM artists"),
)


def refuses(fn, params, why, conn_factory=db.get_maint_conn):
    try:
        with conn_factory() as conn:
            fn(conn, params, [])
        check(why, False, "did not refuse")
    except actions.ActionError:
        check(why, True)


refuses(actions.remove_tracks, {"keep": 9999, "remove": [1]}, "refuses when the kept song is gone")
refuses(
    actions.remove_tracks,
    {"keep": s_keep, "remove": [9999]},
    "refuses when duplicates are already gone",
)
refuses(
    actions.remove_missing_track, {"track_id": s_keep}, "refuses to remove a song whose file exists"
)
missing = song("Gone", root / "nope.wav")
with db.get_maint_conn() as conn:
    actions.remove_missing_track(conn, {"track_id": missing}, [])
check("removes a song whose file is really missing", "Gone" not in live())

# the rule: never touch what TOTR did not create
refuses(
    actions.remove_tracks,
    {"keep": s_keep, "remove": [s_theirs]},
    "refuses to remove a song TOTR did not add",
)
refuses(
    actions.set_title,
    {"track_id": s_theirs, "title": "x"},
    "refuses to rename a song TOTR did not add",
)
refuses(
    actions.set_track_number,
    {"track_id": s_theirs, "track_number": 4},
    "refuses to renumber a song TOTR did not add",
)
foreign_album = add("album", "Theirs Album", ours=False)
before = (
    q("SELECT * FROM media_items ORDER BY item_id"),
    q("SELECT * FROM media_item_relations ORDER BY relation_id"),
)
refuses(
    actions.set_year,
    {"album_id": foreign_album, "year": 1999},
    "refuses to change an album TOTR did not add",
)
refuses(
    actions.set_cover,
    {"album_id": foreign_album, "path": str(keep_f)},
    "refuses a cover for an album TOTR did not add",
)
check(
    "...and nothing changed",
    before
    == (
        q("SELECT * FROM media_items ORDER BY item_id"),
        q("SELECT * FROM media_item_relations ORDER BY relation_id"),
    ),
)

# something else links to a song TOTR created: it stays
video = add("video", "Official video", ours=False)
layout.relate(DB, video, "is_music_video_for", s_keep)
refuses(
    actions.remove_tracks,
    {"keep": s_shared, "remove": [s_keep]},
    "refuses to remove a song that a music video links to",
)
check("...and the song is still there", "Keep" in live())

# safety: drive unmounted => never delete rows
real_root = uploader.MUSIC_ROOT
uploader.MUSIC_ROOT = tmp / "not-mounted"
refuses(
    actions.remove_missing_track,
    {"track_id": s_keep},
    "refuses everything when the music folder is not mounted",
)
uploader.MUSIC_ROOT = real_root

refuses(
    actions.set_cover,
    {"album_id": album, "path": str(outside)},
    "refuses a cover outside the music folder",
)
refuses(
    actions.import_track,
    {"path": str(outside)},
    "refuses to import a file outside the music folder",
)
refuses(
    actions.import_track, {"path": str(keep_f)}, "refuses to import a file already in the library"
)
refuses(
    actions.import_track,
    {"path": str(root / "A" / "new.wav")},
    "refuses to import a file that is not there",
)
(root / "A" / "new.wav").write_bytes(b"x")
refuses(
    actions.import_track,
    {"path": str(root / "A" / "new.wav")},
    "refuses to import a file with no artist tag",
)

# edits go into metadata, only on TOTR's own items
with db.get_maint_conn() as conn:
    actions.set_title(conn, {"track_id": s_keep, "title": "Renamed"}, [])
    actions.set_track_number(conn, {"track_id": s_keep, "track_number": 7}, [])
    actions.set_year(conn, {"album_id": album, "year": 2011}, [])
    actions.set_cover(conn, {"album_id": album, "path": str(keep_f)}, [])
row = q("SELECT * FROM tracks WHERE track_id = ?", (s_keep,))[0]
alb = q("SELECT * FROM albums WHERE album_id = ?", (album,))[0]
check(
    "title and track number are changed",
    row["title"] == "Renamed" and int(row["track_number"]) == 7,
    row,
)
check(
    "year and cover are changed",
    int(alb["year"]) == 2011 and alb["cover_art_path"] == str(keep_f),
    alb,
)
check(
    "other metadata is kept (provenance and file path survive an edit)",
    q("SELECT metadata FROM media_items WHERE item_id = ?", (s_keep,))[0]["metadata"].count(
        "created_by_app"
    )
    == 1
    and "file_path"
    in q("SELECT metadata FROM media_items WHERE item_id = ?", (s_keep,))[0]["metadata"],
)

# merge: moves albums and credits, never deletes the keeper, leaves what is not TOTR's
a2, a3, foreign_artist = (
    add("artist", "Band (dup)"),
    add("artist", "Band (dup 2)"),
    add("artist", "Someone Else", ours=False),
)
extra_album = add("album", "Other", year=1)
layout.relate(DB, extra_album, "album_by", a2)
extra_song = add("song", "Dup artist song", file_path=str(root / "A" / "x.wav"))
layout.relate(DB, extra_song, "performed_by", a2)
layout.relate(DB, extra_song, "performed_by", a3)
with db.get_maint_conn() as conn:
    msg = actions.merge_artists(
        conn, {"keep": artist, "merge": [artist, a2, a3, foreign_artist]}, []
    )
check(
    "merge moves the credits and the album owner to the kept artist",
    q(
        "SELECT count(*) n FROM track_artists WHERE track_id = ? AND artist_id = ?",
        (extra_song, artist),
    )[0]["n"]
    == 1
    and q("SELECT artist_id FROM albums WHERE album_id = ?", (extra_album,))[0]["artist_id"]
    == artist,
    msg,
)
check(
    "merge removes the duplicates but never the keeper",
    not q("SELECT 1 FROM artists WHERE artist_id IN (?, ?)", (a2, a3))
    and q("SELECT 1 FROM artists WHERE artist_id = ?", (artist,)),
)
check(
    "merge leaves an artist TOTR did not add, and says so",
    q("SELECT 1 FROM artists WHERE artist_id = ?", (foreign_artist,)) and "left alone" in msg,
    msg,
)
linked = add("artist", "Linked Dup")
# something outside TOTR points at the duplicate
place = add("album", "Not an artist link", ours=False)
layout.relate(DB, place, "album_by", linked)
refuses(
    actions.merge_artists,
    {"keep": artist, "merge": [linked]},
    "refuses to merge an artist something else links to",
)
refuses(
    actions.merge_artists,
    {"keep": 9999, "merge": [a2]},
    "refuses to merge into an artist that is gone",
)

# execute(): unknown type, malformed params, not configured, catalog refusals are shown, not raised
for bad, why in (
    ({"type": "drop_everything", "params": {}}, "unknown action type is refused"),
    (
        {"type": "set_title", "params": {"track_id": s_theirs, "title": "x"}},
        "execute turns a catalog refusal into an ActionError",
    ),
    (
        {"type": "set_title", "params": {}},
        "execute treats missing parameters as a malformed proposal",
    ),
):
    try:
        actions.execute(bad)
        check(why, False)
    except actions.ActionError:
        check(why, True)
check(
    "only the 10 known actions exist",
    sorted(actions.HANDLERS)
    == sorted(
        [
            "remove_missing_track",
            "remove_tracks",
            "import_track",
            "set_cover",
            "merge_artists",
            "set_title",
            "set_year",
            "set_track_number",
            "add_wanted",
            "fetch_cover",
        ]
    ),
)

finish()
