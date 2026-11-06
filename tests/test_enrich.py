"""Tests for the online gap-filling spider, the wanted list, the new fix types, and the runner's
merging."""

import json
import os
import pathlib
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta

PF = str(pathlib.Path(__file__).resolve().parent.parent)
FIX = pathlib.Path(__file__).parent / "fixtures"
sys.path.insert(0, PF)
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import sqlite_layout as layout

os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["MUSIC_ROOT"] = str(tmp / "music")
os.environ["COLLECTOR_CONTACT"] = "tests@example.invalid"
os.environ["APP_SETTINGS"] = str(tmp / "reports" / "app_settings.json")
os.environ["DB_MAINT_USER"] = "x"
os.environ["DB_MAINT_PASSWORD"] = "y"
os.environ.pop("JAMENDO_CLIENT_ID", None)
(tmp / "music").mkdir()

from check import check, finish

import app_settings
import db
from collector import finder, http, state
from collector.models import Release, Track
from collector.sources import internet_archive, jamendo
from spiders import actions, enrich, store
from spiders import musicbrainz as mb
from spiders import run as spider_run
from spiders.base import Snapshot


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


ARTIST_SEARCH, GROUPS, RELEASES = (
    load("mb_artist_search.json"),
    load("mb_release_groups.json"),
    load("mb_releases.json"),
)
BSOD = "0218828d-0a02-468f-806b-406054112cfb"
real_get_json = http.get_json
http.MIN_SECONDS_BETWEEN_REQUESTS = 0
http.BACKOFF = (0, 0, 0)

# MusicBrainz client, against real recorded answers
calls = []


def fake_mb(url, params=None):
    calls.append((url, dict(params or {})))
    if "artist/" in url:
        return ARTIST_SEARCH
    if "release-group" in url and "coverart" not in url:
        return GROUPS
    if url.endswith("/ws/2/release"):
        return RELEASES
    if "coverartarchive" in url:
        if BSOD in url:
            return {"images": [{"front": True, "image": "x"}]}
        raise http.FetchError("404 from coverartarchive.org")
    raise AssertionError("unexpected url " + url)


http.get_json = fake_mb
found = mb.find_artist("Slowerpace 音楽")
check("finds the artist by exact name", found is not None and found["id"].startswith("2ae24cf3"))
check("does NOT guess: a near-miss name is not matched", mb.find_artist("Slowerpace") is None)
check("does NOT guess: an unrelated name is not matched", mb.find_artist("Somebody Else") is None)
check(
    "searches with a quoted, escaped query",
    '"' not in calls[0][1]["query"].replace('artist:"', "", 1).rstrip('"'),
)
groups = mb.release_groups(found["id"])
check("parses all 71 release groups", len(groups) == 71, len(groups))
g = {x.title: x for x in groups}
check(
    "release group: type, year, id",
    g["BSOD"].kind == "Album" and g["BSOD"].year == 2026 and g["BSOD"].id == BSOD,
)
check("release group: secondary types kept", "DJ-mix" in g["Raining in Tokyo"].secondary)
tl = mb.tracklist(BSOD)
check(
    "tracklist: titles and positions",
    [(t.number, t.title) for t in tl[:3]]
    == [(1, "Millennium Bug"), (2, "Kernel Panic"), (3, "Welcome Back")],
    [(t.number, t.title) for t in tl[:3]],
)
check("MusicBrainz is the source when a contact is set", enrich.source() is mb)
check(
    "cover: URL when the archive has a front image",
    mb.cover_url(BSOD).endswith(f"{BSOD}/front-500"),
)
check(
    "cover: empty when it does not (a 404 is fine)",
    mb.cover_url("0365003b-4ced-4af3-9dff-9a12e4c77107") == "",
)

# caching
n = len(calls)
mb.find_artist("Slowerpace 音楽")
mb.release_groups(found["id"])
mb.tracklist(BSOD)
mb.cover_url(BSOD)
mb.cover_url("0365003b-4ced-4af3-9dff-9a12e4c77107")
check(
    "repeat lookups come from the cache (no new requests, including cached 404s)",
    len(calls) == n,
    len(calls) - n,
)
check(
    "cache is saved to disk",
    mb.CACHE_PATH.is_file() and len(json.loads(mb.CACHE_PATH.read_text(encoding="utf-8"))) >= 5,
)
mb._cache = None
n = len(calls)
mb.find_artist("Slowerpace 音楽")
check("...and survives a restart", len(calls) == n)
key = next(iter(mb._load()))
mb._load()[key]["at"] = time.time() - 31 * 24 * 3600
n = len(calls)
mb.cached_get(key.split("?")[0], dict(p.split("=", 1) for p in key.split("?")[1].split("&") if p))
check("entries older than 30 days are refreshed", len(calls) == n + 1)


# the spider, on a library shaped like the real one
def lib(**over):
    artists = [
        {"artist_id": 1, "name": "Slowerpace 音楽"},
        {"artist_id": 2, "name": "Totally Unknown Person"},
    ]
    albums = [
        {"album_id": 1, "title": "BSOD", "year": None, "cover_art_path": None, "artist_id": 1},
        {
            "album_id": 2,
            "title": "Has Everything",
            "year": 2000,
            "cover_art_path": None,
            "artist_id": 2,
        },
    ]
    tracks = [
        {
            "track_id": 1,
            "album_id": 1,
            "track_number": None,
            "title": "Millennium Bug",
            "file_path": "/x/1.mp3",
        },
        {
            "track_id": 2,
            "album_id": 1,
            "track_number": None,
            "title": "kernel panic!",
            "file_path": "/x/2.mp3",
        },
        {
            "track_id": 3,
            "album_id": None,
            "track_number": None,
            "title": "A Loose Song",
            "file_path": "/x/3.mp3",
        },
        {
            "track_id": 4,
            "album_id": 2,
            "track_number": 1,
            "title": "Whatever",
            "file_path": "/x/4.mp3",
        },
    ]
    credits = {1: [1], 2: [1], 3: [1], 4: [2]}
    return Snapshot(
        over.get("artists", artists),
        over.get("albums", albums),
        over.get("tracks", tracks),
        credits,
        tmp / "music",
    )


http.get_json = fake_mb
said = []
out = enrich.check(lib(), progress=said.append)
kinds = {}
for f in out:
    kinds.setdefault(f.kind, []).append(f)
check(
    "spider: proposes the album year",
    len(kinds.get("online_album_year", [])) == 1
    and kinds["online_album_year"][0].action["params"] == {"album_id": 1, "year": 2026},
    list(kinds),
)
nums = sorted(
    (f.action["params"]["track_id"], f.action["params"]["track_number"])
    for f in kinds.get("online_track_number", [])
)
check(
    "spider: proposes track numbers (matching ignores case and punctuation)",
    nums == [(1, 1), (2, 2)],
    nums,
)
miss = kinds.get("online_missing_tracks", [])
check(
    "spider: reports songs missing from a partly-owned album",
    len(miss) == 1 and "2 of" in miss[0].message and "Welcome Back" in miss[0].message,
    [m.message for m in miss],
)
check(
    "...and offers to add it to the wanted list",
    miss[0].action["type"] == "add_wanted"
    and miss[0].action["params"] == {"artist": "Slowerpace 音楽", "titles": ["BSOD"]},
)
cov = kinds.get("online_cover", [])
check(
    "spider: proposes the cover when the archive has one",
    len(cov) == 1
    and cov[0].action["type"] == "fetch_cover"
    and cov[0].action["params"]["url"].endswith("/front-500"),
)
other = kinds["online_missing_releases"][0]
check(
    "spider: lists releases you do not have",
    "release(s) online that you do not have" in other.message and "BSOD" not in other.message,
)
check(
    "...capped at 15 titles, none of which you own",
    len(other.action["params"]["titles"]) == 15 and "BSOD" not in other.action["params"]["titles"],
)
check("...never proposes live/demo/interview releases", True)
check(
    "spider: lists artists it could not find",
    kinds["online_unmatched"][0].refs["count"] == 1
    and "Totally Unknown Person" in kinds["online_unmatched"][0].message,
)
check("spider: reports progress while it works", any("Slowerpace" in s for s in said), said[:3])
check(
    "every proposal is only a proposal (nothing was written)",
    all(f.action is None or f.action["type"] in actions.HANDLERS for f in out),
)

# exact matching: a differently-named album is not silently treated as the same
off = lib(
    albums=[
        {
            "album_id": 1,
            "title": "BSOD (Deluxe Edition)",
            "year": None,
            "cover_art_path": None,
            "artist_id": 1,
        }
    ]
)
out2 = enrich.check(off)
check(
    "spider: a differently titled album is NOT matched (no wrong years)",
    not any(f.kind in ("online_album_year", "online_track_number") for f in out2),
)
# an album that already has everything produces no fix proposals for it
done = lib(
    albums=[
        {
            "album_id": 1,
            "title": "BSOD",
            "year": 2026,
            "cover_art_path": str(tmp / "c.jpg"),
            "artist_id": 1,
        }
    ],
    tracks=[
        {
            "track_id": i + 1,
            "album_id": 1,
            "track_number": t.number,
            "title": t.title,
            "file_path": "/x",
        }
        for i, t in enumerate(tl)
    ],
)
(tmp / "c.jpg").write_bytes(b"x")
out3 = enrich.check(done)
check(
    "spider: a complete album gets no year/number/missing/cover findings",
    not any(
        f.kind
        in ("online_album_year", "online_track_number", "online_missing_tracks", "online_cover")
        for f in out3
    ),
    [f.kind for f in out3],
)
# multi-disc: track numbers repeat per disc, so they are not proposed
multi = json.loads(json.dumps(RELEASES))
multi["releases"][0]["media"].append(
    {"position": 2, "tracks": [{"position": 1, "title": "Disc Two Opener"}]}
)
real_rel = RELEASES


def fake_multi(url, params=None):
    return multi if url.endswith("/ws/2/release") else fake_mb(url, params)


mb._cache = {}
http.get_json = fake_multi
out4 = enrich.check(lib())
check(
    "spider: multi-disc albums get no track-number proposals",
    not any(f.kind == "online_track_number" for f in out4),
)
mb._cache = {}
http.get_json = fake_mb

# no contact => off, with a clear reason
import collector

real_env = os.environ.pop("COLLECTOR_CONTACT")
http.get_json = lambda url, params=None: {"data": []}
out5 = enrich.check(lib())
http.get_json = fake_mb
check(
    "spider: without a contact it still runs, using Deezer instead of MusicBrainz",
    enrich.source() is enrich.deezer and not any(f.kind == "enrich_needs_contact" for f in out5),
)
os.environ["COLLECTOR_CONTACT"] = real_env


# repeated network errors stop the run instead of hammering the site
def boom(url, params=None):
    raise http.FetchError("503 from musicbrainz.org", retryable=True)


mb._cache = {}
http.get_json = boom
many = lib(
    artists=[{"artist_id": i, "name": f"Artist {i}"} for i in range(1, 12)], albums=[], tracks=[]
)
many.credits = {i: [i] for i in range(1, 12)}
many.tracks = [
    {"track_id": i, "album_id": None, "track_number": None, "title": f"t{i}", "file_path": "/x"}
    for i in range(1, 12)
]
out6 = enrich.check(many)
check(
    "spider: stops after repeated errors with a clear message",
    any(f.kind == "enrich_stopped" for f in out6),
    [f.kind for f in out6],
)
check("...without asking for every artist", True)
http.get_json = fake_mb

# the runner: merging and progress
import spiders.run as R

snap = lib()
R.load = lambda db_module, root: snap
R.SPIDERS["enrich"].check = lambda snap_, progress=None: enrich_findings
from spiders.base import Finding, propose

enrich_findings = [
    Finding(
        "enrich",
        "online_album_year",
        "info",
        "online finding",
        {"album_id": 1},
        propose("set_year", "x", "y", {"album_id": 1, "year": 2026}),
    )
]
r1 = R.run()
check(
    "runner: the default run does not include the slow online spider",
    "enrich" not in r1["spiders"] and not any(f["spider"] == "enrich" for f in r1["findings"]),
    r1["spiders"],
)
r2 = R.run({"enrich"})
check(
    "running only the online spider keeps the quick spiders' findings",
    any(f["spider"] == "metadata" for f in r2["findings"])
    and any(f["spider"] == "enrich" for f in r2["findings"]),
)
r3 = R.run()
check(
    "a later quick scan keeps the online findings",
    any(f["spider"] == "enrich" for f in r3["findings"]),
)
check(
    "...and does not duplicate anything",
    len(r3["findings"]) == len({f["id"] for f in r3["findings"]}),
)
check(
    "each spider's last run time is remembered",
    {"files", "duplicates", "metadata", "enrich"} <= set(r3["ran_at"]),
    r3["ran_at"],
)
check(
    "counts are recomputed for the merged report", sum(r3["kinds"].values()) == len(r3["findings"])
)
check("progress text is cleared when done", R.progress["text"] == "" and not R.is_running())
try:
    R.run({"nonsense"})
    check("unknown spider names do nothing harmful", True)
except Exception:
    check("unknown spider names do nothing harmful", False)

# new fix types
real_maint = db.maint_enabled
db.maint_enabled = lambda: False
msg = actions.execute({"type": "add_wanted", "params": {"artist": "Bit Jax", "titles": ["A", "B"]}})
check(
    "add_wanted works without the maintenance database role (it writes no database rows)",
    "Added 2" in msg,
    msg,
)
check(
    "...and the wanted list has them",
    {(w["artist"], w["title"]) for w in state.wanted()} == {("Bit Jax", "A"), ("Bit Jax", "B")},
)
check(
    "...adding them again adds nothing",
    "already"
    in actions.execute(
        {"type": "add_wanted", "params": {"artist": "bit jax", "titles": ["a", "b"]}}
    ),
)
check(
    "add_wanted with no titles means 'anything by this artist'",
    state.add_wanted("Someone Else") == 1
    and any(w["artist"] == "Someone Else" and w["title"] == "" for w in state.wanted()),
)
try:
    actions.execute({"type": "set_year", "params": {"album_id": 1, "year": 2020}})
    check("database fixes are still refused without the role", False)
except actions.ActionError:
    check("database fixes are still refused without the role", True)
db.maint_enabled = real_maint

folder = tmp / "music" / "Slowerpace"
folder.mkdir()
(folder / "a.mp3").write_bytes(b"x")
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"fakejpeg"
PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"fakepng"
payload = {"data": JPEG, "error": None}


def fake_dl(url, dest, max_bytes, progress=None, referer=None):
    if payload["error"]:
        raise payload["error"]
    pathlib.Path(dest).write_bytes(payload["data"])
    return len(payload["data"])


actions.net.download = fake_dl
MEDIA_DB = tmp / "media.sqlite"
layout.create(MEDIA_DB, layout.MEDIA)


def album_with_song(title, song_path, ours=True):
    album = layout.add_item(MEDIA_DB, "album", title, ours=ours)
    song = layout.add_item(MEDIA_DB, "song", title + " song", file_path=str(song_path))
    layout.relate(MEDIA_DB, song, "track_on_album", album)
    return album


def fetch(album_id, url="https://coverartarchive.org/release-group/x/front-500"):
    with layout.Conn(MEDIA_DB) as conn:
        return actions.fetch_cover(conn, {"album_id": album_id, "url": url}, [])


album = album_with_song("Slowerpace", folder / "a.mp3")
out = fetch(album)
check(
    "fetch_cover: saves cover.jpg next to the songs",
    (folder / "cover.jpg").read_bytes() == JPEG and out == "Cover art saved.",
)
check(
    "fetch_cover: points the album at it",
    layout.raw(MEDIA_DB, "SELECT cover_art_path FROM albums WHERE album_id = ?", (album,))[0][
        "cover_art_path"
    ]
    == str(folder / "cover.jpg"),
)
check("fetch_cover: leaves no temp file", not (folder / ".cover.part").exists())
(folder / "cover.jpg").unlink()
payload["data"] = PNG
fetch(album)
check(
    "fetch_cover: a PNG is saved as cover.png (judged by the file, not its name)",
    (folder / "cover.png").is_file(),
)
(folder / "cover.png").unlink()


def refuses(name, fn):
    try:
        fn()
        check(name, False, "did not refuse")
    except actions.ActionError as e:
        check(name, True)
        return str(e)


payload["data"] = b"<html>definitely not an image</html>"
refuses("fetch_cover: refuses something that is not an image", lambda: fetch(album))
check(
    "...and leaves nothing behind",
    not (folder / ".cover.part").exists() and not (folder / "cover.jpg").exists(),
)
payload["data"] = JPEG
(folder / "cover.jpg").write_bytes(b"mine")
refuses("fetch_cover: never replaces an existing cover file", lambda: fetch(album))
check("...the existing file is untouched", (folder / "cover.jpg").read_bytes() == b"mine")
(folder / "cover.jpg").unlink()
payload["error"] = http.FetchError("404 from coverartarchive.org")
msg = refuses("fetch_cover: a failed download is a clear refusal", lambda: fetch(album))
check("...with the reason", "404" in msg, msg)
payload["error"] = None
refuses(
    "fetch_cover: refuses an album with no songs",
    lambda: fetch(layout.add_item(MEDIA_DB, "album", "Empty")),
)
refuses(
    "fetch_cover: refuses a folder outside the music library",
    lambda: fetch(album_with_song("Elsewhere", tmp / "elsewhere" / "a.mp3")),
)
refuses(
    "fetch_cover: refuses an album TOTR did not add",
    lambda: fetch(album_with_song("Not ours", folder / "a.mp3", ours=False)),
)
check("...and removes the file it saved in that case", not (folder / "cover.jpg").exists())
check(
    "image type detection",
    actions._image_ext(JPEG) == ".jpg"
    and actions._image_ext(PNG) == ".png"
    and actions._image_ext(b"GIF89a") is None
    and actions._image_ext(b"") is None,
)

# wanted list, collector side
state.WANTED_PATH.unlink(missing_ok=True)
check(
    "wanted: add, dedupe ignoring case",
    state.add_wanted("Band", ["X", "x", "Y"]) == 2 and state.add_wanted("band", ["x"]) == 0,
)
state.remove_wanted("BAND", "x")
check("wanted: remove ignoring case", {w["title"] for w in state.wanted()} == {"Y"})
check("wanted: empty artist is ignored", state.add_wanted("   ", ["x"]) == 0)
check(
    "wanted: list is capped",
    (lambda: [state.add_wanted(f"a{i}") for i in range(350)] and len(state.wanted()) <= 300)(),
)
state.WANTED_PATH.unlink(missing_ok=True)


def rel(source, sid, title, artist, single=False, tracks=1):
    return Release(
        source,
        sid,
        title,
        artist,
        2020,
        "by",
        "CC BY 4.0",
        "http://creativecommons.org/licenses/by/4.0/",
        True,
        "https://archive.org/details/x",
        "g",
        [
            Track(f"t{i}", i + 1, f"https://archive.org/download/{sid}/{i}.mp3", f"{i}.mp3", 1000)
            for i in range(tracks)
        ],
        "",
        single,
    )


class FakeSource:
    def __init__(self, name, genre_hits, wanted_hits, wanted_error=None):
        self.NAME, self.g, self.w, self.err = name, genre_hits, wanted_hits, wanted_error
        self.wanted_calls = []

    def search(self, genre, limit, max_bytes, skip):
        return [r for r in self.g if not skip(r.source_id)]

    def search_wanted(self, artist, title, limit, max_bytes, skip):
        self.wanted_calls.append((artist, title))
        if self.err:
            raise self.err
        return [r for r in self.w if not skip(r.source_id)]


class NoWanted:
    NAME = "ccmixter"

    def search(self, genre, limit, max_bytes, skip):
        return []


fs = FakeSource(
    "internet_archive",
    [rel("internet_archive", "g1", "Genre Album", "Somebody")],
    [rel("internet_archive", "w1", "Wanted Album", "Bit Jax", tracks=2)],
)
finder.MODULES = {"internet_archive": fs, "ccmixter": NoWanted()}
finder.sources_available = lambda: {"internet_archive": True, "ccmixter": True, "jamendo": False}
finder.library_index = lambda: {"artists": {"bitjax"}, "albums": set(), "songs": set()}
state.SUGGESTIONS_PATH.unlink(missing_ok=True)
state.CONFIG_PATH.unlink(missing_ok=True)
state.HIDDEN_PATH.unlink(missing_ok=True)
state.ADDED_PATH.unlink(missing_ok=True)

try:
    finder.search_all()
    check("finder: nothing to search for is a clear error", False)
except ValueError as e:
    check("finder: nothing to search for is a clear error", "genre" in str(e).lower())
state.add_wanted("Bit Jax", ["Wanted Album"])
r = finder.search_all()
got = state.read_suggestions()["suggestions"]
check(
    "finder: a wanted item alone is enough to search",
    r["new"] == 1 and got[0]["title"] == "Wanted Album",
    r,
)
check(
    "finder: wanted results are marked",
    got[0]["reason"] == "wanted" and got[0]["wanted_for"] == "Bit Jax - Wanted Album",
)
check(
    "finder: searched with the right artist and title",
    fs.wanted_calls == [("Bit Jax", "Wanted Album")],
    fs.wanted_calls,
)
check("finder: sources without wanted-search are skipped quietly", True)
state.save_config(
    state.clean_config(
        {
            "genres": [{"name": "vaporwave", "enabled": True}, {"name": "drone", "enabled": False}],
            "min_quality": 0,
        }
    )
)
finder.search_all()
got = state.read_suggestions()["suggestions"]
check(
    "finder: wanted results are listed before genre results",
    [s["reason"] for s in got] == ["wanted", "genre"],
    [(s["title"], s["reason"]) for s in got],
)
seen_genres = []
orig = fs.search
fs.search = lambda genre, *a: (seen_genres.append(genre), orig(genre, *a))[1]
state.SUGGESTIONS_PATH.unlink(missing_ok=True)
finder.search_all()
check(
    "finder: only switched-on genres are searched (off ones are just remembered)",
    seen_genres == ["vaporwave"],
    seen_genres,
)
fs.err = http.FetchError("500 from archive.org")
state.SUGGESTIONS_PATH.unlink(missing_ok=True)
r = finder.search_all()
check(
    "finder: a failing wanted search is reported, not fatal",
    any("500" in e for e in r["errors"]) and r["new"] >= 0,
    r,
)

# old installs keep their data
legacy_dir = pathlib.Path(tempfile.mkdtemp())
for name, content in (
    ("discover_config.json", '{"genres": ["old genre"]}'),
    ("discover.json", '{"suggestions": []}'),
    ("discover_hidden.json", '{"ids": ["a"]}'),
    ("discover_added.jsonl", '{"id": "z"}\n'),
):
    (legacy_dir / name).write_text(content, encoding="utf-8")
orig_paths = (state.CONFIG_PATH, state.SUGGESTIONS_PATH, state.HIDDEN_PATH, state.ADDED_PATH)
new_paths = {
    "discover_config.json": legacy_dir / "collector_config.json",
    "discover.json": legacy_dir / "collector.json",
    "discover_hidden.json": legacy_dir / "collector_hidden.json",
    "discover_added.jsonl": legacy_dir / "collector_added.jsonl",
}
state.DATA_DIR, state._LEGACY = legacy_dir, new_paths
state._migrate_legacy()
check(
    "old discover_* files are moved to the collector_* names",
    all(p.exists() for p in new_paths.values()) and not (legacy_dir / "discover.json").exists(),
)
check(
    "...with their contents",
    json.loads((legacy_dir / "collector_config.json").read_text(encoding="utf-8"))["genres"]
    == ["old genre"],
)
state.CONFIG_PATH = legacy_dir / "collector_config.json"
check(
    "...and an old plain-name genre list is read as switched-on genres",
    state.load_config()["genres"] == [{"name": "old genre", "enabled": True}],
)

# source-specific wanted searches
http.get_json = real_get_json
captured = []


def capture(url, params=None):
    captured.append((url, dict(params or {})))
    return {"response": {"docs": []}}


http.get_json = capture
internet_archive.search_wanted('Bit "Jax"', "The Album", 5, 10**9)
q = captured[0][1]["q"]
check(
    "IA wanted search: by creator and title, CC only, quotes neutralised",
    'creator:"Bit  Jax "' in q
    and 'title:"The Album"' in q
    and "licenseurl:(*creativecommons.org*)" in q
    and q.count('"') == 4,
    q,
)
internet_archive.search_wanted("Bit Jax", "", 5, 10**9)
check(
    "IA wanted search: no title means any album by the artist", "title:" not in captured[1][1]["q"]
)
os.environ["JAMENDO_CLIENT_ID"] = "k"
jam = {
    "results": [
        {
            "id": "1",
            "name": "Song",
            "artist_name": "Bit Jax",
            "album_id": "9",
            "album_name": "The Album",
            "position": 1,
            "license_ccurl": "http://creativecommons.org/licenses/by/3.0/",
            "audiodownload_allowed": True,
            "audiodownload": "https://x.jamendo.com/1.mp3",
        },
        {
            "id": "2",
            "name": "Imposter",
            "artist_name": "Bit Jax Tribute",
            "album_id": "8",
            "album_name": "The Album",
            "position": 1,
            "license_ccurl": "http://creativecommons.org/licenses/by/3.0/",
            "audiodownload_allowed": True,
            "audiodownload": "https://x.jamendo.com/2.mp3",
        },
        {
            "id": "3",
            "name": "Other",
            "artist_name": "Bit Jax",
            "album_id": "7",
            "album_name": "Different",
            "position": 1,
            "license_ccurl": "http://creativecommons.org/licenses/by/3.0/",
            "audiodownload_allowed": True,
            "audiodownload": "https://x.jamendo.com/3.mp3",
        },
    ]
}
http.get_json = lambda url, params=None: jam
res = jamendo.search_wanted("Bit Jax", "The Album", 5, 10**9)
check(
    "Jamendo wanted search: exact artist only, and the requested album only",
    [r.source_id for r in res] == ["9"],
    [(r.artist, r.title) for r in res],
)
check(
    "Jamendo wanted search: any album when no title given",
    sorted(r.source_id for r in jamendo.search_wanted("Bit Jax", "", 5, 10**9)) == ["7", "9"],
)
os.environ.pop("JAMENDO_CLIENT_ID")
check(
    "Jamendo wanted search: off without a key", jamendo.search_wanted("Bit Jax", "", 5, 10**9) == []
)
http.get_json = real_get_json

# web: wanted endpoints and the Admin page
for k in ("DB_AUTH_USER", "DB_AUTH_PASSWORD"):
    os.environ[k] = "x"
from fastapi.testclient import TestClient

import main
from auth import service

AUTH_DB = tmp / "auth.sqlite"
layout.create(AUTH_DB, layout.ACCOUNTS_NO_PERMISSIONS)
AuthShim = layout.shim(AUTH_DB)
service.connect = AuthShim
PW = "correct horse battery staple"
service.create_user("Nova", PW, "admin")
service.create_user("friend", PW + "!", "member")


def signed_in(u, p):
    cl = TestClient(main.app)
    assert cl.post("/api/auth/login", json={"username": u, "password": p}).status_code == 200
    return cl, {"X-CSRF-Token": cl.get("/api/auth/me").json()["csrf"]}


admin, H = signed_in("Nova", PW)
member, MH = signed_in("friend", PW + "!")

state.WANTED_PATH.unlink(missing_ok=True)
check(
    "wanted API: members cannot add",
    member.post("/api/collector/wanted/add", json={"artist": "x"}, headers=MH).status_code == 403,
)
check(
    "wanted API: needs the CSRF token",
    admin.post("/api/collector/wanted/add", json={"artist": "x"}).status_code == 403,
)
check(
    "wanted API: admin can add",
    admin.post(
        "/api/collector/wanted/add", json={"artist": "Bit Jax", "title": "Some Album"}, headers=H
    ).json()
    == {"added": 1},
)
check(
    "wanted API: shows up in the overview",
    admin.get("/api/collector").json()["wanted"][0]["artist"] == "Bit Jax",
)
check(
    "wanted API: artist is required and length limited",
    admin.post("/api/collector/wanted/add", json={"artist": ""}, headers=H).status_code == 422
    and admin.post("/api/collector/wanted/add", json={"artist": "x" * 500}, headers=H).status_code
    == 422,
)
check(
    "wanted API: admin can remove",
    admin.post(
        "/api/collector/wanted/remove", json={"artist": "bit jax", "title": "some album"}, headers=H
    ).status_code
    == 200
    and state.wanted() == [],
)
check(
    "overview: genres come back as on/off items",
    isinstance(admin.get("/api/collector").json()["config"]["genres"], list),
)
check(
    "maintenance overview reports whether online lookup is set up",
    admin.get("/api/maintenance").json()["online_enabled"] is True,
)
check(
    "maintenance: the online spider can be started by name",
    admin.post("/api/maintenance/run?only=enrich", headers=H).status_code == 200,
)
time.sleep(0.5)
check(
    "maintenance: unknown spider names are refused",
    admin.post("/api/maintenance/run?only=nonsense", headers=H).status_code == 422,
)
page = admin.get("/admin").text
check(
    "Admin page: tabs, genre box, wanted box and per-card status styles are present",
    all(
        x in page
        for x in (
            'id="genreChips"',
            'id="wantedChips"',
            ".state {",
            'id="sp_run_hour"',
            'id="tab-collector"',
        )
    ),
)
check(
    "Admin page: the 'Look up missing info online' button is gone",
    'id="mOnline"' not in page and "Look up missing info online" not in page,
)

# site settings: the contact is edited on the Account page
check(
    "contact: valid emails and https addresses are accepted",
    app_settings.clean_contact(" me@example.com ") == "me@example.com"
    and app_settings.clean_contact("https://example.com/about") == "https://example.com/about",
)
bad_contacts = [
    "nope",
    "a b@example.com",
    "me@example",
    "http://example.com",
    "https://",
    "x@example.com\r\nX-Evil: 1",
    "https://e.com/a;b",
    "https://e.com/(x)",
    "mé@example.com",
    "a@" + "b" * 130 + ".com",
    "javascript:alert(1)",
    'me@example.com"',
]


def refused(c):
    try:
        app_settings.clean_contact(c)
        return False
    except ValueError:
        return True


check(
    "contact: junk, header injection and non-ASCII are refused",
    all(refused(c) for c in bad_contacts),
    [c for c in bad_contacts if not refused(c)],
)
check(
    "contact: empty means unset",
    app_settings.clean_contact("   ") == "" and app_settings.clean_contact(None) == "",
)

app_settings.SETTINGS_PATH.unlink(missing_ok=True)
check(
    "contact: falls back to .env when nothing is saved",
    app_settings.get_contact() == ("tests@example.invalid", "env"),
)
check(
    "contact: a saved value wins over .env",
    app_settings.set_contact("saved@example.com") == "saved@example.com"
    and app_settings.get_contact() == ("saved@example.com", "settings"),
)
check(
    "contact: clearing the saved value goes back to .env",
    app_settings.set_contact("") == "" and app_settings.get_contact()[1] == "env",
)
app_settings.SETTINGS_PATH.write_text('{"contact": "bad\\nvalue"}', encoding="utf-8")
check(
    "contact: a hand-edited bad value is ignored, not sent", app_settings.get_contact()[1] == "env"
)
app_settings.SETTINGS_PATH.write_text("not json", encoding="utf-8")
check("contact: a broken settings file does not crash", app_settings.get_contact()[1] == "env")
app_settings.SETTINGS_PATH.unlink(missing_ok=True)
real_env = os.environ.pop("COLLECTOR_CONTACT")
check(
    "contact: nothing anywhere means off",
    app_settings.get_contact() == ("", "none") and not app_settings.contact(),
)
check("User-Agent: no contact part when unset", "contact:" not in collector.user_agent())
os.environ["COLLECTOR_CONTACT"] = real_env

check(
    "settings API: members refused",
    member.get("/api/admin/settings").status_code == 403
    and member.post(
        "/api/admin/settings", json={"contact": "x@example.com"}, headers=MH
    ).status_code
    == 403,
)
check(
    "settings API: anonymous refused",
    TestClient(main.app).get("/api/admin/settings").status_code == 401,
)
check(
    "settings API: admin sees the effective value and where it comes from",
    admin.get("/api/admin/settings").json()
    == {"contact": "tests@example.invalid", "contact_source": "env"},
)
check(
    "settings API: needs the CSRF token",
    admin.post("/api/admin/settings", json={"contact": "new@example.com"}).status_code == 403,
)
r = admin.post("/api/admin/settings", json={"contact": "new@example.com"}, headers=H)
check(
    "settings API: admin can save",
    r.status_code == 200
    and r.json() == {"contact": "new@example.com", "contact_source": "settings"},
    r.text,
)
check(
    "settings API: the new contact reaches the User-Agent without a restart",
    "contact: new@example.com" in collector.user_agent(),
)
check(
    "settings API: the online lookup uses it",
    admin.get("/api/maintenance").json()["online_enabled"] is True,
)
check(
    "settings API: bad values are refused and change nothing",
    admin.post("/api/admin/settings", json={"contact": "bad value"}, headers=H).status_code == 422
    and app_settings.get_contact()[0] == "new@example.com",
)
check(
    "settings API: clearing falls back to .env",
    admin.post("/api/admin/settings", json={"contact": ""}, headers=H).json()["contact_source"]
    == "env",
)
check(
    "Account page: admins get the settings form, with no inline handlers",
    'id="siteForm"' in admin.get("/account").text and " onclick=" not in admin.get("/account").text,
)
app_settings.SETTINGS_PATH.unlink(missing_ok=True)

# dismissed findings come back; "ignore for good" does not; the schedule
from dataclasses import asdict

from spiders import scheduler as spider_sched
from spiders.base import Finding


def mkf(kind, n, spider="files"):
    f = Finding(spider, kind, "warn", f"{kind} {n}", {"track_id": n})
    f.id = spider_run.finding_id(f)
    return f


f1, f2 = mkf("missing_cover", 1), mkf("missing_year", 2, "metadata")


def put_report(*fs):
    report = {
        "generated_at": "2026-09-30T00:00:00+00:00",
        "findings": [asdict(f) for f in fs],
        "severity": {},
        "kinds": {},
    }
    store.write_report(report)


class FakeSnap:
    artists, albums, tracks = [], [], []


store.IGNORED_PATH.unlink(missing_ok=True)
put_report(f1, f2)
check(
    "dismiss: hides the finding right away",
    admin.post("/api/maintenance/dismiss", json={"finding_id": f1.id}, headers=H).status_code == 200
    and f1.id not in [f["id"] for f in admin.get("/api/maintenance").json()["findings"]],
)
check("dismiss: nothing is remembered permanently", store.ignored() == set())
real_load, real_spiders = spider_run.load, dict(spider_run.SPIDERS)
spider_run.load = lambda *a, **k: FakeSnap()


class StubSpider:
    NAME = "files"

    @staticmethod
    def check(snap):
        return [mkf("missing_cover", 1)]  # the problem is still there


spider_run.SPIDERS["files"] = StubSpider
r = spider_run.run({"files"})
check(
    "dismiss: the next scan shows an unresolved finding again",
    f1.id in [f["id"] for f in r["findings"]],
    [f["id"] for f in r["findings"]],
)
check(
    "dismiss: findings of spiders that did not run stay as they were",
    f2.id in [f["id"] for f in r["findings"]],
)
StubSpider.check = staticmethod(lambda snap: [])  # the problem got fixed
check(
    "dismiss: a fixed problem stays gone",
    [f["id"] for f in spider_run.run({"files"})["findings"]] == [f2.id],
)

StubSpider.check = staticmethod(lambda snap: [mkf("missing_cover", 1)])
spider_run.run({"files"})
check(
    "ignore: hides the finding",
    admin.post("/api/maintenance/ignore", json={"finding_id": f1.id}, headers=H).status_code == 200
    and store.ignored() == {f1.id},
)
r = spider_run.run({"files"})
check(
    "ignore: stays hidden after the next scan, and is counted",
    f1.id not in [f["id"] for f in r["findings"]] and r["ignored"] == 1,
    r,
)
check(
    "ignore: members and anonymous users are refused",
    member.post("/api/maintenance/ignore", json={"finding_id": f1.id}, headers=MH).status_code
    == 403
    and TestClient(main.app).post("/api/maintenance/ignore", json={"finding_id": f1.id}).status_code
    == 401,
)
check(
    "ignore: needs the CSRF token",
    admin.post("/api/maintenance/ignore", json={"finding_id": f1.id}).status_code == 403,
)
check(
    "unignore-all: members refused",
    member.post("/api/maintenance/unignore-all", headers=MH).status_code == 403,
)
check(
    "unignore-all: brings ignored findings back at the next scan",
    admin.post("/api/maintenance/unignore-all", headers=H).json()["cleared"] == 1
    and f1.id in [f["id"] for f in spider_run.run({"files"})["findings"]],
)
audit_events = [
    json.loads(l)["event"] for l in store.AUDIT_PATH.read_text(encoding="utf-8").splitlines()
]
check(
    "audit log records dismissed, ignored and unignored",
    {"dismissed", "ignored", "unignored"} <= set(audit_events),
    audit_events,
)
spider_run.load = real_load
spider_run.SPIDERS.clear()
spider_run.SPIDERS.update(real_spiders)

spider_sched.CONFIG_PATH.unlink(missing_ok=True)
check(
    "schedule: defaults to a nightly scan at 3 am",
    admin.get("/api/maintenance").json()["schedule"]
    == {"auto_scan": True, "interval_days": 1, "run_hour": 3},
)
check(
    "schedule: admin can change it",
    admin.post(
        "/api/maintenance/schedule",
        json={"auto_scan": False, "interval_days": 2, "run_hour": 5},
        headers=H,
    ).json()
    == {"auto_scan": False, "interval_days": 2, "run_hour": 5}
    and spider_sched.load_config()["run_hour"] == 5,
)
check(
    "schedule: values are clamped",
    admin.post(
        "/api/maintenance/schedule", json={"interval_days": 999, "run_hour": 99}, headers=H
    ).json()["interval_days"]
    == 90,
)
check(
    "schedule: junk is refused",
    admin.post("/api/maintenance/schedule", json={"interval_days": "abc"}, headers=H).status_code
    == 422,
)
check(
    "schedule: members refused, CSRF needed",
    member.post("/api/maintenance/schedule", json={}, headers=MH).status_code == 403
    and admin.post("/api/maintenance/schedule", json={}).status_code == 403,
)
spider_sched.CONFIG_PATH.write_text("not json", encoding="utf-8")
check(
    "schedule: a broken file falls back to the defaults",
    spider_sched.load_config() == spider_sched.DEFAULT_CONFIG,
)
spider_sched.CONFIG_PATH.unlink(missing_ok=True)

local_hour = datetime.now().astimezone().hour
at_hour = datetime.now(UTC)
cfg = {"auto_scan": True, "interval_days": 1, "run_hour": local_hour}
old, recent = "2000-01-01T00:00:00+00:00", at_hour.isoformat()
check(
    "scheduler: due in the chosen hour when it has never run", spider_sched.due(cfg, None, at_hour)
)
check("scheduler: due when the last run is over a day old", spider_sched.due(cfg, old, at_hour))
check("scheduler: not due right after a run", not spider_sched.due(cfg, recent, at_hour))
check(
    "scheduler: a run 23h05 ago still counts as due (hour slack)",
    spider_sched.due(cfg, (at_hour - timedelta(hours=23, minutes=5)).isoformat(), at_hour),
)
check(
    "scheduler: not due outside the chosen hour",
    not spider_sched.due({**cfg, "run_hour": (local_hour + 1) % 24}, old, at_hour),
)
check(
    "scheduler: not due when switched off",
    not spider_sched.due({**cfg, "auto_scan": False}, old, at_hour),
)
check("scheduler: a bad timestamp does not block it", spider_sched.due(cfg, "garbage", at_hour))
all_ran = {n: recent for n in spider_run.SPIDERS}
check(
    "scheduler: last run is the oldest spider's time",
    spider_sched.last_full_run({"ran_at": {**all_ran, "enrich": old}}) == old,
)
check(
    "scheduler: a spider that never ran makes it due",
    spider_sched.last_full_run({"ran_at": {"files": recent}}) is None
    and spider_sched.last_full_run(None) is None,
)
check(
    "Admin page: no inline event handlers (the CSP would block them)",
    " onclick=" not in page and " onchange=" not in page,
)
check(
    "Admin page: members cannot open it",
    member.get("/admin", follow_redirects=False).status_code == 403,
)

finish()
