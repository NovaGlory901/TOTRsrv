"""The lookup source that needs no email: Deezer's public catalogue API, against real recorded
answers (tests/fixtures/dz_*.json), and the enrich spider using it when no contact is set. No
network: http.get_json is replaced."""

import json
import os
import pathlib
import sys
import tempfile

PF = str(pathlib.Path(__file__).resolve().parent.parent)
FIX = pathlib.Path(__file__).parent / "fixtures"
sys.path.insert(0, PF)
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["APP_SETTINGS"] = str(tmp / "reports" / "app_settings.json")
os.environ.pop("COLLECTOR_CONTACT", None)

from check import check, finish

import app_settings
import collector
from collector import http
from spiders import actions, deezer, enrich
from spiders import musicbrainz as mb
from spiders.base import Snapshot

load = lambda n: json.loads((FIX / n).read_text(encoding="utf-8"))
SEARCH, ALBUMS, TRACKS = (
    load("dz_artist_search.json"),
    load("dz_albums.json"),
    load("dz_tracks.json"),
)
calls = []


def fake(url, params=None):
    calls.append((url, dict(params or {})))
    if "search/artist" in url:
        return SEARCH
    if url.endswith("/artist/27/albums"):
        return ALBUMS if not (params or {}).get("index") else {"data": []}
    if url.endswith("/album/302127/tracks"):
        return TRACKS
    return {"error": {"type": "DataException", "message": "no data", "code": 800}}


http.get_json = fake
http.MIN_SECONDS_BETWEEN_REQUESTS = 0
mb.CACHE_PATH = tmp / "cache.json"
mb._cache = {}

# nothing here needs a contact
check(
    "no contact is set, and the lookup source is still Deezer",
    not app_settings.contact() and enrich.source() is deezer,
)
check(
    "the User-Agent carries no contact when none is set", "contact:" not in collector.user_agent()
)
check(
    "the allow-list has the two Deezer domains",
    {"deezer.com", "dzcdn.net"} <= http.ALLOWED_SUFFIXES,
)
http.CHECK_DNS = False
check(
    "api.deezer.com and the cover host pass the host check; a lookalike and plain http do not",
    http.host_allowed("https://api.deezer.com/x")
    and http.host_allowed("https://cdn-images.dzcdn.net/images/cover/a/1000x1000.jpg")
    and not http.host_allowed("https://deezer.com.evil.example/x")
    and not http.host_allowed("http://api.deezer.com/x"),
)

# the client
a = deezer.find_artist("Daft Punk")
check(
    "finds the artist by exact name, taking the real one over a same-named empty page",
    a == {"id": "27", "name": "Daft Punk"},
    a,
)
check("finds it ignoring case and punctuation", deezer.find_artist("DAFT  punk!") == a)
check(
    "does NOT guess: a near-miss name is not matched",
    deezer.find_artist("Daft Pun") is None
    and deezer.find_artist("Daft Punk Experience Live") is None,
)
close = json.loads(json.dumps(SEARCH))
close["data"][3]["nb_fan"] = 4000000
SEARCH_BACKUP, SEARCH = SEARCH, close
mb._cache = {}
check("two same-named artists of similar size are refused", deezer.find_artist("Daft Punk") is None)
SEARCH = SEARCH_BACKUP
mb._cache = {}

groups = deezer.release_groups("27")
titles = [g.title for g in groups]
check(
    "album list: albums, EPs and singles only, with year, kind and a cover address",
    groups
    and all(g.kind in ("Album", "EP", "Single") for g in groups)
    and all(g.year for g in groups)
    and all(g.cover.startswith("https://cdn-images.dzcdn.net/") for g in groups),
    groups[:1],
)
check(
    "album list: live, remix, demo and karaoke releases are left out",
    not any(deezer.SKIP_TITLE.search(t) for t in titles)
    and "Alive 2007" in [x["title"] for x in ALBUMS["data"]]
    and "Alive 2007" in titles,
    titles,
)
check("album list: each album appears once", len(titles) == len({g.id for g in groups}))
tl = deezer.tracklist("302127")
check(
    "track list: disc, position and title, in order",
    [(t.disc, t.number, t.title) for t in tl[:3]]
    == [(1, 1, "One More Time"), (1, 2, "Aerodynamic"), (1, 3, "Digital Love")],
    tl[:3],
)
check("an unknown album is an empty list, not an error", deezer.tracklist("1") == [])
deezer.find_artist("Daft Punk")
n = len(calls)
deezer.find_artist("Daft Punk")
deezer.release_groups("27")
deezer.tracklist("302127")
check("repeat lookups come from the cache", len(calls) == n, len(calls) - n)


def quota(url, params=None):
    return {"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}}


mb._cache = {}
http.get_json = quota
try:
    deezer.find_artist("Someone")
    check("a quota error is raised as a fetch error", False)
except http.FetchError as e:
    check("a quota error is raised as a fetch error, retryable", e.retryable and "Quota" in str(e))
http.get_json = fake
check("...and was not cached as 'not found'", "Someone" not in json.dumps(mb._cache))


# the spider with no contact
def lib():
    artists = [{"artist_id": 1, "name": "Daft Punk"}, {"artist_id": 2, "name": "Nobody Known"}]
    albums = [
        {"album_id": 1, "title": "Discovery", "year": None, "cover_art_path": None, "artist_id": 1}
    ]
    tracks = [
        {
            "track_id": 1,
            "album_id": 1,
            "track_number": None,
            "title": "one more time",
            "file_path": "/x/1.mp3",
        },
        {
            "track_id": 2,
            "album_id": 1,
            "track_number": None,
            "title": "Aerodynamic",
            "file_path": "/x/2.mp3",
        },
        {
            "track_id": 3,
            "album_id": None,
            "track_number": None,
            "title": "Loose",
            "file_path": "/x/3.mp3",
        },
    ]
    return Snapshot(artists, albums, tracks, {1: [1], 2: [1], 3: [2]}, tmp / "music")


# the real album list names "Discovery" with the id 302127; give the fixture that album so the
# exact-title match works
extra = {
    "id": 302127,
    "title": "Discovery",
    "cover_xl": "https://cdn-images.dzcdn.net/images/cover/5718f7c81c27e0b2417e2a4c45224f8a/1000x1000-000000-80-0-0.jpg",
    "release_date": "2001-03-07",
    "record_type": "album",
}
ALBUMS = {"data": ALBUMS["data"] + [extra]}
mb._cache = {}
out = enrich.check(lib())
kinds = {}
for f in out:
    kinds.setdefault(f.kind, []).append(f)
check(
    "spider: runs with no contact (no 'needs a contact' finding)",
    "enrich_needs_contact" not in kinds,
    list(kinds),
)
check(
    "spider: proposes the album year from Deezer",
    [f.action["params"] for f in kinds.get("online_album_year", [])]
    == [{"album_id": 1, "year": 2001}],
    list(kinds),
)
check("...worded with its real source", "Deezer says" in kinds["online_album_year"][0].message)
nums = sorted(
    (f.action["params"]["track_id"], f.action["params"]["track_number"])
    for f in kinds.get("online_track_number", [])
)
check("spider: proposes track numbers", nums == [(1, 1), (2, 2)], nums)
check(
    "spider: reports songs missing from a partly-owned album",
    len(kinds.get("online_missing_tracks", [])) == 1
    and "2 of 14" in kinds["online_missing_tracks"][0].message,
    [m.message for m in kinds.get("online_missing_tracks", [])],
)
cov = kinds.get("online_cover", [])
check(
    "spider: proposes the cover from the album list, a dzcdn.net picture, with no extra lookup",
    len(cov) == 1
    and cov[0].action["params"]["url"].startswith("https://cdn-images.dzcdn.net/")
    and "Deezer" in cov[0].message,
    [c.message for c in cov],
)
check(
    "spider: lists the artist Deezer does not know",
    "Nobody Known" in kinds["online_unmatched"][0].message,
)
check(
    "every proposal is a known, approval-gated action",
    all(f.action is None or f.action["type"] in actions.HANDLERS for f in out),
)
check(
    "the cover download is allowed to reach the picture host",
    http.host_allowed(cov[0].action["params"]["url"]),
)

finish()
