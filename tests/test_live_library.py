"""Read-only smoke test of every music read route against the REAL database (through the read-only
role), using real ids. The route SQL uses PostgreSQL features SQLite cannot run (string_agg, ANY,
view-based GROUP BY), so this is the only place those queries are really executed. It needs a
database set up from sql/; until then it prints "skip". Nothing is written."""

import os
import pathlib
import sys
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["APP_SETTINGS"] = str(tmp / "reports" / "app_settings.json")

import sqlite_layout as layout

sys.stdout.reconfigure(errors="replace")  # real song titles can be in any script
from check import check, finish

if not layout.live_ready():
    print(
        "skip  every music read route against the real library  (no database with the TOTR layout is set up)"
    )
    sys.exit(0)

import fastapi

import db
import library_search
import main
from mixtapes import web as mixtapes_web

artists = main.list_artists()
check(
    "artists: the list is not empty and every artist has songs",
    bool(artists) and all(a["track_count"] > 0 for a in artists),
)
a = artists[0]
check("artist: one artist by id", main.get_artist(a["artist_id"])["name"] == a["name"])
try:
    main.get_artist(2_000_000_000)
    check("artist: an unknown id is a 404", False)
except fastapi.HTTPException as e:
    check("artist: an unknown id is a 404", e.status_code == 404)

albums = []
for ar in artists:
    albums = main.list_albums(ar["artist_id"])
    if albums:
        break
check(
    "albums: an artist's albums come back with owner and counts",
    bool(albums)
    and {"album_id", "title", "year", "owner", "their_tracks", "total_tracks"} <= set(albums[0]),
    albums[:1],
)
al = albums[0]
check("album: one album by id", main.get_album(al["album_id"])["title"] == al["title"])
tracks = main.list_tracks(al["album_id"])
check(
    "album tracks: songs with artists, in track-number order",
    bool(tracks) and all(t["artists"] for t in tracks),
    tracks[:1],
)
songs = main.list_artist_songs(a["artist_id"])
check(
    "artist songs: every song credited to an artist, A to Z, with its album",
    bool(songs)
    and {"track_id", "title", "album", "artists"} <= set(songs[0])
    and [s["title"].lower() for s in songs] == sorted(s["title"].lower() for s in songs),
    songs[:1],
)
check(
    "artist songs: at least as many as the album tracks and singles together",
    len(songs)
    >= sum(x["their_tracks"] for x in main.list_albums(a["artist_id"]))
    + len(main.list_discography(a["artist_id"])),
)
check("compilations: the query runs", isinstance(main.list_compilations(), list))
for ar in artists:
    main.list_discography(ar["artist_id"])
check("discography: the query runs for every artist (loose songs, oldest first)", True)
radio = main.radio_next("")
check(
    "radio: a random song with its artists",
    {"track_id", "title", "album_id", "artists"} <= set(radio),
    radio,
)
check(
    "radio: the exclude list is honoured",
    main.radio_next(str(radio["track_id"]))["track_id"] != radio["track_id"] or len(tracks) <= 1,
)
check("radio media: album covers come back", isinstance(main.radio_media(), list))
with db.get_conn() as conn:
    check(
        "library stats: counts for artists, albums and tracks",
        set(main.library_counts(conn)) == {"artists", "albums", "tracks"},
    )
check("search index: built from the views, with songs", bool(library_search.get_index()["songs"]))
found = mixtapes_web.lookup_tracks([radio["track_id"], 2_000_000_000])
check(
    "mixtape lookup: real ids come back with titles and artists, unknown ids are absent",
    set(found) == {radio["track_id"]},
    found,
)

with db.get_conn() as conn:
    p = conn.execute("SELECT file_path FROM totr.v_tracks LIMIT 1").fetchone()
    check(
        "tracks: every song the site lists has a file path",
        conn.execute("SELECT count(*) AS n FROM totr.v_tracks WHERE file_path IS NULL").fetchone()[
            "n"
        ]
        == 0
        and p,
    )
    check(
        "covers: the view gives paths or nothing",
        conn.execute(
            "SELECT count(*) AS n FROM totr.v_albums WHERE cover_art_path = ''"
        ).fetchone()["n"]
        == 0,
    )

finish()
