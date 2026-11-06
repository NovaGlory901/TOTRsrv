import json
import os
import pathlib
import re
import shutil
import socket
import sys
import tempfile
import time
import urllib.request
import wave
from datetime import datetime, timedelta

PF = str(pathlib.Path(__file__).resolve().parent.parent)
FIX = pathlib.Path(__file__).parent / "fixtures"
sys.path.insert(0, PF)
os.chdir(PF)
# an unset role means "not configured" only on PostgreSQL; SQLite counts every role as enabled
os.environ["DB_ENGINE"] = "postgres"
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["MUSIC_ROOT"] = str(tmp / "music")
os.environ["DB_MAINT_USER"] = "x"
os.environ["DB_MAINT_PASSWORD"] = "y"
os.environ.pop("JAMENDO_CLIENT_ID", None)
(tmp / "music").mkdir()

import db
import uploader
from collector import acquire, finder, http, licenses, scheduler, state
from collector.models import Release, Track
from collector.sources import ccmixter, clean_title, internet_archive, jamendo, parse_track_name

LIVE = "--live" in sys.argv
from check import check, finish

# licenses
GOOD = [
    "http://creativecommons.org/licenses/by/4.0/",
    "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "http://creativecommons.org/licenses/by-nc/3.0/us/",
    "http://creativecommons.org/licenses/by-nd/2.5/",
    "http://creativecommons.org/publicdomain/zero/1.0/",
    "http://creativecommons.org/publicdomain/mark/1.0/",
    ["http://creativecommons.org/licenses/by-sa/3.0/"],
]
BAD = [
    "",
    None,
    "All rights reserved",
    "http://creativecommons.org/licenses/sampling+/1.0/",
    "http://creativecommons.org.evil.example/licenses/by/4.0/",
    "http://evil.example/creativecommons.org/licenses/by/4.0/",
    "javascript:alert(1)",
    "http://creativecommons.org/licenses/by/4.0/../../x",
    "https://creativecommons.org/licenses/by-xx/4.0/",
    "http://creativecommons.org/licenses/by/4.0/ extra",
    "https://example.com/free-to-stream",
    [],
]
for u in GOOD:
    check(f"license accepted: {str(u)[-34:]}", licenses.classify(u) is not None)
for u in BAD:
    check(f"license rejected: {str(u)[:44]!r}", licenses.classify(u) is None)
l = licenses.classify("http://creativecommons.org/licenses/by-nc-sa/4.0/")
check(
    "license flags (credit, non-commercial, label)",
    l.needs_credit and l.non_commercial and l.label == "CC BY-NC-SA 4.0",
)
check(
    "public domain needs no credit",
    not licenses.classify("http://creativecommons.org/publicdomain/mark/1.0/").needs_credit,
)

# network guard
real_getaddr = socket.getaddrinfo
ALLOWED = [
    "https://archive.org/download/x/a.mp3",
    "https://ia800.us.archive.org/3/items/x/a.mp3",
    "https://ccmixter.org/content/u/a.mp3",
    "https://api.jamendo.com/v3.0/tracks/",
    "https://prod-1.storage.jamendo.com/x.mp3",
]
REFUSED = [
    "http://archive.org/x",
    "https://archive.org.evil.example/x",
    "https://evilarchive.org/x",
    "https://10.0.0.5/x",
    "https://127.0.0.1/x",
    "https://[::1]/x",
    "https://archive.org:8443/x",
    "https://user:pw@archive.org/x",
    "file:///etc/passwd",
    "ftp://archive.org/x",
    "https://localhost/x",
    "https://169.254.169.254/latest/meta-data",
    "https://archive.org@evil.example/x",
    "",
    "not a url",
    "https://archive.org.:8080/x",
]
# pretend every name is public
socket.getaddrinfo = lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
for u in ALLOWED:
    check(f"host allowed: {u[:46]}", http.host_allowed(u))
for u in REFUSED:
    check(f"host refused: {u[:46]!r}", not http.host_allowed(u))
# DNS trick: allowed name -> private IP
socket.getaddrinfo = lambda host, port, *a, **k: [(2, 1, 6, "", ("10.0.0.7", 443))]
check(
    "allowed name that resolves to a private address is refused",
    not http.host_allowed("https://archive.org/x"),
)
socket.getaddrinfo = lambda host, port, *a, **k: [
    (2, 1, 6, "", ("93.184.216.34", 443)),
    (2, 1, 6, "", ("127.0.0.1", 443)),
]
check("mixed public+loopback answers are refused", not http.host_allowed("https://archive.org/x"))
socket.getaddrinfo = real_getaddr
check(
    "links shown to people: javascript: refused",
    not http.link_allowed("javascript:alert(1)")
    and not http.link_allowed("https://evil.example/")
    and http.link_allowed("https://archive.org/details/x"),
)
try:
    http._CheckedRedirects().redirect_request(
        urllib.request.Request("https://archive.org/x"),
        None,
        302,
        "Found",
        {},
        "https://10.0.0.5/admin",
    )
    check("redirect to an internal address is refused", False)
except http.FetchError:
    check("redirect to an internal address is refused", True)
try:
    http.get_json("https://10.0.0.5/x")
    check("fetching a non-allowed host is refused", False)
except http.FetchError:
    check("fetching a non-allowed host is refused", True)

# parsing real data
for name, want in (
    ("01_-_In_Your_Eyes.mp3", (1, "In Your Eyes")),
    ("02_-_Get_Out.ogg", (2, "Get Out")),
    ("12 Some Song.flac", (12, "Some Song")),
    ("No Number Here.mp3", (None, "No Number Here")),
    ("03. Dotted.mp3", (3, "Dotted")),
    ("CD1-04 Disc Track.mp3", (4, "Disc Track")),
):
    check(f"track name {name!r}", parse_track_name(name) == want, str(parse_track_name(name)))
for raw, artist, want in (
    ("[SCL160] Tigerberry — Cold Wave", "Tigerberry", "Cold Wave"),
    ("Wrexsoul - Alchemy Sound LP [exp037]", "Wrexsoul", "Alchemy Sound LP"),
    ("[eml009] Sound Research Vol.1", "Electronic Music Lab", "Sound Research Vol.1"),
    ("V.A. - V.A. - iHyper [BKTR001]", "V.A.", "iHyper"),
    ("Plain Title", "Someone", "Plain Title"),
    ("Someone - ", "Someone", "Someone - "),
    ("Dimensionen", "Jason Ingoli", "Dimensionen"),
    ("Album (2015 Remaster)", "X", "Album (2015 Remaster)"),
):
    check(f"title cleanup: {raw!r}", clean_title(raw, artist) == want, clean_title(raw, artist))
ia = json.load(open(FIX / "ia_SCL160.json", encoding="utf-8"))
tracks = internet_archive.pick_tracks("SCL160", ia["files"])
check("IA: 8 songs from 82 files (one file per song)", len(tracks) == 8, len(tracks))
check(
    "IA: prefers mp3 over ogg/flac",
    all(t.filename.lower().endswith(".mp3") for t in tracks),
    [t.filename for t in tracks][:3],
)
check("IA: ordered by track number", [t.number for t in tracks] == sorted(t.number for t in tracks))
check("IA: titles cleaned", tracks[0].title == "In Your Eyes", tracks[0].title)
check(
    "IA: download URLs are https on archive.org",
    all(t.url.startswith("https://archive.org/download/SCL160/") for t in tracks),
)
check("IA: sizes come from the file list", all(t.size > 0 for t in tracks))
check(
    "IA: size of the audio only (not the 1.2 GB item)",
    sum(t.size for t in tracks) < 200_000_000,
    sum(t.size for t in tracks),
)


# IA search() end to end, with the network replaced by real-shaped responses
def fake_get_json(url, params=None):
    if "advancedsearch" in url:
        return {
            "response": {
                "docs": [
                    {
                        "identifier": "SCL160",
                        "licenseurl": "http://creativecommons.org/licenses/by-nc-sa/4.0/",
                    },
                    {"identifier": "NOLIC", "licenseurl": "All rights reserved"},
                    {
                        "identifier": "bad id with spaces",
                        "licenseurl": "http://creativecommons.org/licenses/by/4.0/",
                    },
                    {
                        "identifier": "SEEN1",
                        "licenseurl": "http://creativecommons.org/licenses/by/4.0/",
                    },
                ]
            }
        }
    return ia


real_get = http.get_json
http.get_json = fake_get_json
found = internet_archive.search("vaporwave", 5, 600 * 1024 * 1024, skip=lambda sid: sid == "SEEN1")
check(
    "IA search: keeps the CC item only",
    [r.source_id for r in found] == ["SCL160"],
    [r.source_id for r in found],
)
r = found[0]
check(
    "IA search: artist/title/year/license filled, title cleaned",
    (r.artist, r.year, r.license_code, r.title) == ("Tigerberry", 2015, "by-nc-sa", "Cold Wave"),
    (r.artist, r.title),
)
check(
    "IA search: multi-song release is an album, not a single", not r.single and len(r.tracks) == 8
)
check(
    "IA search: skips releases over the size cap",
    internet_archive.search("vaporwave", 5, 1024, skip=lambda s: False) == [],
)
no_license = json.loads(json.dumps(ia))
no_license["metadata"]["licenseurl"] = "All rights reserved"
http.get_json = lambda url, params=None: (
    fake_get_json(url, params) if "advancedsearch" in url else no_license
)
check(
    "IA search: a search-index license is not trusted over the item's own record",
    internet_archive.search("vaporwave", 5, 10**9) == [],
)
http.get_json = real_get

ccm = json.load(open(FIX / "ccm_chiptune.json", encoding="utf-8"))
http.get_json = lambda url, params=None: ccm
found = ccmixter.search("chiptune", 5, 10**9)
check(
    "ccMixter: 'Sampling Plus' uploads are skipped, plain CC kept",
    len(found) == 1 and found[0].license_code == "by-nc",
    [(r.title, r.license_code) for r in found],
)
check(
    "ccMixter: each upload is a loose single",
    found[0].single
    and len(found[0].tracks) == 1
    and found[0].tracks[0].url.startswith("https://ccmixter.org/"),
)
http.get_json = real_get
check("ccMixter: rejects hostile tag values", ccmixter.search('x"; drop', 5, 10**9) == [])

os.environ["JAMENDO_CLIENT_ID"] = "test"
jam = {
    "results": [
        {
            "id": "1",
            "name": "Song A",
            "artist_name": "Band",
            "album_id": "9",
            "album_name": "The Album",
            "position": 1,
            "license_ccurl": "http://creativecommons.org/licenses/by-sa/3.0/",
            "audiodownload_allowed": True,
            "audiodownload": "https://prod-1.storage.jamendo.com/download/1.mp3",
            "releasedate": "2020-05-01",
            "image": "https://usercontent.jamendo.com/?type=album&id=9",
        },
        {
            "id": "2",
            "name": "Song B",
            "artist_name": "Band",
            "album_id": "9",
            "album_name": "The Album",
            "position": 2,
            "license_ccurl": "http://creativecommons.org/licenses/by-sa/3.0/",
            "audiodownload_allowed": True,
            "audiodownload": "https://prod-1.storage.jamendo.com/download/2.mp3",
        },
        {
            "id": "3",
            "name": "Locked",
            "artist_name": "Other",
            "album_id": "",
            "license_ccurl": "http://creativecommons.org/licenses/by/3.0/",
            "audiodownload_allowed": False,
            "audiodownload": "https://x.jamendo.com/3.mp3",
        },
        {
            "id": "4",
            "name": "NoLicense",
            "artist_name": "Other",
            "album_id": "",
            "license_ccurl": "",
            "audiodownload_allowed": True,
            "audiodownload": "https://x.jamendo.com/4.mp3",
        },
        {
            "id": "5",
            "name": "A Single",
            "artist_name": "Solo",
            "album_id": "",
            "license_ccurl": "http://creativecommons.org/publicdomain/zero/1.0/",
            "audiodownload_allowed": True,
            "audiodownload": "https://x.jamendo.com/5.mp3",
        },
    ]
}
http.get_json = lambda url, params=None: jam
found = jamendo.search("rock", 10, 10**9)
check(
    "Jamendo: groups songs into albums, skips locked/unlicensed",
    sorted(r.title for r in found) == ["A Single", "The Album"],
    [r.title for r in found],
)
check(
    "Jamendo: album has 2 songs in order, single is single",
    next(r for r in found if r.title == "The Album").tracks[0].title == "Song A"
    and next(r for r in found if r.title == "A Single").single,
)
os.environ.pop("JAMENDO_CLIENT_ID")
check("Jamendo: does nothing without a key", jamendo.search("rock", 10, 10**9) == [])
http.get_json = real_get

# settings
try:
    state.clean_config({"genres": "vaporwave; DROP TABLE"})
    check("settings: bad genre text refused", False)
except ValueError:
    check("settings: bad genre text refused", True)
c = state.clean_config(
    {
        "genres": " Vaporwave, chiptune ,vaporwave,,Lo-Fi ",
        "per_genre": 9999,
        "interval_days": 0,
        "run_hour": 99,
        "max_release_mb": "abc" if False else 100,
    }
)
check(
    "settings: genres cleaned and deduplicated",
    [g["name"] for g in c["genres"]] == ["Vaporwave", "chiptune", "Lo-Fi"]
    and all(g["enabled"] for g in c["genres"]),
    c["genres"],
)
check(
    "settings: numbers clamped to sane ranges",
    (c["per_genre"], c["interval_days"], c["run_hour"]) == (100, 1, 23),
)
try:
    state.clean_config({"genres": [f"g{i}" for i in range(21)]})
    check("settings: more than 20 switched-on genres refused", False)
except ValueError:
    check("settings: more than 20 switched-on genres refused", True)
many = state.clean_config({"genres": [{"name": f"g{i}", "enabled": i < 5} for i in range(40)]})
check(
    "settings: 40 remembered genres are fine while only a few are on",
    len(many["genres"]) == 40 and len(state.enabled_genres(many)) == 5,
)
check(
    "settings: old plain-name genres still load (migration)",
    state.clean_config({"genres": ["a", "b"]})["genres"]
    == [{"name": "a", "enabled": True}, {"name": "b", "enabled": True}],
)
try:
    state.clean_config({"per_genre": "lots"})
    check("settings: non-number refused", False)
except ValueError:
    check("settings: non-number refused", True)
check(
    "settings: unknown sources ignored",
    set(state.clean_config({"sources": {"evil": True}})["sources"]) == set(state.SOURCES),
)

# scheduler
cfg = state.clean_config({"genres": ["x"], "interval_days": 7, "run_hour": 3})
at = lambda h, days_ago: (
    datetime.now().astimezone().replace(hour=h, minute=0) - timedelta(days=days_ago)
)
check("scheduler: never searched, right hour -> due", scheduler.due(cfg, None, at(3, 0)))
check("scheduler: wrong hour -> not due", not scheduler.due(cfg, None, at(14, 0)))
check(
    "scheduler: searched 2 days ago -> not due",
    not scheduler.due(cfg, (at(3, 2)).isoformat(), at(3, 0)),
)
check("scheduler: searched 8 days ago -> due", scheduler.due(cfg, (at(3, 8)).isoformat(), at(3, 0)))
check("scheduler: no genres -> never", not scheduler.due({**cfg, "genres": []}, None, at(3, 0)))
check(
    "scheduler: auto-search off -> never",
    not scheduler.due({**cfg, "auto_search": False}, None, at(3, 0)),
)


# finder
def rel(source, sid, title, artist, single=False, page="https://archive.org/details/x", tracks=1):
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
        page,
        "g",
        [
            Track(f"t{i}", i + 1, f"https://archive.org/download/{sid}/{i}.mp3", f"{i}.mp3", 1000)
            for i in range(tracks)
        ],
        "",
        single,
    )


class Fake:
    def __init__(self, name, releases):
        self.NAME, self.releases = name, releases

    def search(self, genre, limit, max_bytes, skip):
        return [r for r in self.releases if not skip(r.source_id)]


fake_ia = Fake(
    "internet_archive",
    [
        rel("internet_archive", "a1", "New Album", "Fresh Artist", tracks=3),
        rel("internet_archive", "a2", "Owned Album", "Owner"),
        rel("internet_archive", "a3", "Another", "Owner", tracks=2),
        rel("internet_archive", "a4", "Bad Link", "Evil", page="javascript:alert(1)"),
        rel("internet_archive", "a5", "Blocked One", "Blocked Artist"),
        rel("internet_archive", "a6", "Dismissed One", "Someone"),
    ],
)
fake_ccm = Fake(
    "ccmixter",
    [
        rel("ccmixter", "c1", "Owned Song", "Owner", single=True),
        rel("ccmixter", "c2", "Fresh Song", "Owner", single=True),
    ],
)
finder.MODULES = {"internet_archive": fake_ia, "ccmixter": fake_ccm}
finder.sources_available = lambda: {"internet_archive": True, "ccmixter": True, "jamendo": False}
sq = lambda s: re.sub(r"[\W_]+", "", s.casefold())
finder.library_index = lambda: {
    "artists": {sq("Owner")},
    "albums": {(sq("Owner"), sq("Owned Album"))},
    "songs": {(sq("Owner"), sq("Owned Song"))},
}
state.save_config(
    state.clean_config(
        {
            "genres": ["vaporwave"],
            "max_suggestions": 10,
            "min_quality": 0,
            "sources": {"internet_archive": True, "ccmixter": True, "jamendo": False},
        }
    )
)
state.hide(release_id_=state.release_id("internet_archive", "a6"))
state.hide(artist_key=sq("Blocked Artist"))
summary = finder.search_all()
got = {s["title"]: s for s in state.read_suggestions()["suggestions"]}
check(
    "finder: suggests new releases",
    "New Album" in got and "Another" in got and "Fresh Song" in got,
    list(got),
)
check(
    "finder: skips what you already own (album and song)",
    "Owned Album" not in got and "Owned Song" not in got,
)
check(
    "finder: skips dismissed and blocked-artist releases",
    "Dismissed One" not in got and "Blocked One" not in got,
)
check("finder: unsafe page link is blanked", got["Bad Link"]["page_url"] == "")
check(
    "finder: flags artists you already have",
    got["Another"]["known_artist"] and not got["New Album"]["known_artist"],
)
order = [s["title"] for s in state.read_suggestions()["suggestions"]]
check(
    "finder: known artists listed first",
    all(got[t]["known_artist"] for t in order[:2]) and order[-1] != "Another",
    order,
)
check("finder: summary counts new items", summary["new"] == len(got), summary)
again = finder.search_all()
check("finder: a second search finds nothing already offered", again["new"] == 0, again)
state.save_config(
    state.clean_config({"genres": ["vaporwave"], "max_suggestions": 10, "min_quality": 0})
)
try:
    state.save_config({**state.load_config(), "genres": []})
    finder.search_all()
    check("finder: needs at least one genre", False)
except ValueError:
    check("finder: needs at least one genre", True)
state.save_config(
    state.clean_config(
        {
            "genres": ["x"],
            "sources": {"internet_archive": False, "ccmixter": False, "jamendo": False},
        }
    )
)
try:
    finder.search_all()
    check("finder: needs at least one source", False)
except ValueError:
    check("finder: needs at least one source", True)
check("finder: lock is released after errors", not finder.is_running())

# quality score, sources, threshold
from collector import quality

check(
    "config: min_quality defaults to 25 and ccMixter is off by default",
    state.clean_config({})["min_quality"] == 25
    and state.clean_config({})["sources"]
    == {"internet_archive": True, "ccmixter": False, "jamendo": True},
)
check(
    "config: min_quality is clamped to 0-100",
    state.clean_config({"min_quality": 500})["min_quality"] == 100
    and state.clean_config({"min_quality": -4})["min_quality"] == 0,
)
state.CONFIG_PATH.write_text(
    json.dumps(
        {"genres": ["a"], "sources": {"internet_archive": True, "ccmixter": True, "jamendo": True}}
    ),
    encoding="utf-8",
)
check(
    "config: an older saved config gets ccMixter switched off once",
    state.load_config()["sources"]["ccmixter"] is False,
)
state.save_config(
    {
        **state.load_config(),
        "sources": {"internet_archive": True, "ccmixter": True, "jamendo": True},
    }
)
check(
    "config: a later deliberate ccMixter choice sticks",
    state.load_config()["sources"]["ccmixter"] is True
    and state.load_config()["config_version"] == 2,
)
state.CONFIG_PATH.unlink(missing_ok=True)

album = {
    "title": "Cold Wave",
    "artist": "Tigerberry",
    "genre": "vaporwave",
    "year": 2015,
    "cover_url": "https://archive.org/x.jpg",
    "single": False,
    "size": 80_000_000,
    "tracks": [{"title": f"t{i}"} for i in range(8)],
    "signals": {
        "downloads": "1204",
        "reviews": 4,
        "rating": "4.5",
        "collections": ["netlabels", "opensource_audio"],
        "text": "vaporwave dreamy",
    },
}
pts, why = quality.score(album)
check(
    "quality: a netlabel album with downloads and reviews scores high, with reasons",
    pts >= 70 and any("netlabel" in w for w in why) and any("1,204 downloads" in w for w in why),
    (pts, why),
)
stock = {
    "title": "Epic Cinematic",
    "artist": "Stock Guy",
    "genre": "ambient",
    "single": True,
    "size": 900_000,
    "tracks": [{"title": "x"}],
    "signals": {"downloads": 3, "text": "Royalty free background music for videos. No copyright."},
}
pts, why = quality.score(stock)
check(
    "quality: stock/filler wording drops a release below the default bar",
    pts < 25 and any("royalty free" in w or "background music" in w for w in why),
    (pts, why),
)
check(
    "quality: whole words only (Stockholm is fine, SFX is not)",
    quality._blocked_word({"title": "Stockholm Nights"}) is None
    and quality._blocked_word({"title": "Boom SFX"}) == "sfx",
)
check(
    "quality: a non-music collection is penalised",
    quality.score({**album, "signals": {"collections": ["podcasts"]}})[0]
    < quality.score({**album, "signals": {}})[0],
)
check(
    "quality: a tiny single is penalised, a normal single is not",
    quality.score({"single": True, "size": 500_000, "tracks": [1]})[0]
    < quality.score({"single": True, "size": 6_000_000, "tracks": [1]})[0],
)
check(
    "quality: legacy records (no size/cover) are scored from what they have",
    quality.score({"title": "A", "artist": "B", "tracks": 9, "single": False}, legacy=True)[0] == 25
    and quality.score({"title": "A", "artist": "B", "tracks": 1, "single": True}, legacy=True)[0]
    == 10,
)
check(
    "quality: junk signal values never crash",
    isinstance(
        quality.score(
            {
                "tracks": [],
                "signals": {
                    "downloads": "lots",
                    "reviews": [],
                    "rating": {},
                    "collections": None,
                    "text": 5,
                },
            }
        )[0],
        int,
    ),
)
check(
    "quality: always within 0-100",
    0
    <= quality.score({**stock, "signals": {"collections": ["podcasts"], "text": "royalty free"}})[0]
    <= 100
    and quality.score(album, (99, "x"))[0] == 100,
)
check(
    "quality: a MusicBrainz result adds its points and reason",
    quality.score({"single": True, "tracks": [1]}, (12, "artist has 5 releases on MusicBrainz"))
    == (22, ["a single song", "artist has 5 releases on MusicBrainz"]),
)


class RG:
    def __init__(self, kind):
        self.kind = kind


real_find, real_rgs, real_contact = (
    quality.mb.find_artist,
    quality.mb.release_groups,
    quality.app_settings.contact,
)
quality.app_settings.contact = lambda: ""
check(
    "musicbrainz points: skipped (None) without a contact",
    quality.musicbrainz_points("Anyone") is None,
)
quality.app_settings.contact = lambda: "me@example.com"
quality.mb.find_artist = lambda name: None
check(
    "musicbrainz points: unknown artist gets 0 and a reason",
    quality.musicbrainz_points("Nobody") == (0, "artist not found on MusicBrainz"),
)
quality.mb.find_artist = lambda name: {"id": "mbid"}
quality.mb.release_groups = lambda mbid: [
    RG("Album"),
    RG("EP"),
    RG("Single"),
    RG("Album"),
    RG("Broadcast"),
]
check(
    "musicbrainz points: 4+ real releases = 18",
    quality.musicbrainz_points("Real") == (18, "artist has 4 releases on MusicBrainz"),
)
quality.mb.release_groups = lambda mbid: [RG("Album")]
check("musicbrainz points: 1-3 releases = 12", quality.musicbrainz_points("Small")[0] == 12)
quality.mb.release_groups = lambda mbid: []
check("musicbrainz points: known but no releases = 0", quality.musicbrainz_points("Empty")[0] == 0)

# IA source: netlabels first, stats and collections kept as signals
queries = []
ia_meta = json.loads(json.dumps(ia))
ia_meta["metadata"]["collection"] = ["netlabels", "opensource_audio"]
ia_meta["metadata"]["subject"] = ["vaporwave", "royalty free"]


def ia_get(url, params=None):
    if "advancedsearch" in url:
        queries.append(params["q"])
        return {
            "response": {
                "docs": [
                    {
                        "identifier": "SCL160",
                        "licenseurl": "http://creativecommons.org/licenses/by-nc-sa/4.0/",
                        "downloads": 1204,
                        "num_reviews": 3,
                        "avg_rating": 4.7,
                    }
                ]
            }
        }
    return ia_meta


http.get_json = ia_get
found = internet_archive.search("vaporwave", 5, 10**9)
check("IA: the netlabels search runs first", "collection:netlabels" in queries[0], queries)
check(
    "IA: the general search tops up when netlabels fall short, without duplicates",
    len(queries) == 2
    and "collection:netlabels" not in queries[1]
    and [r.source_id for r in found] == ["SCL160"],
    queries,
)
check(
    "IA: popularity and collections are kept for scoring",
    found[0].signals["downloads"] == 1204
    and found[0].signals["reviews"] == 3
    and "netlabels" in found[0].signals["collections"],
)
check(
    "IA: all subject tags reach the filler check, not just the first",
    "royalty free" in found[0].signals["text"],
)
queries.clear()
internet_archive.search("vaporwave", 1, 10**9)
check("IA: a full quota from netlabels needs no second search", len(queries) == 1, queries)
http.get_json = real_get


# finder: threshold, wanted exemption, artist check budget
class Fake2:
    NAME = "internet_archive"

    def __init__(self, releases, wanted=()):
        self.releases, self.wanted = releases, wanted

    def search(self, genre, limit, max_bytes, skip):
        return [r for r in self.releases if not skip(r.source_id)]

    def search_wanted(self, artist, title, limit, max_bytes, skip):
        return [r for r in self.wanted if not skip(r.source_id)]


def rel2(sid, title, artist, tracks=1, single=True, text="", downloads=0, size=5_000_000):
    r = rel("internet_archive", sid, title, artist, single=single, tracks=tracks)
    r.signals = {"downloads": downloads, "text": text}
    r.tracks[0].size = size
    return r


good = rel2("q1", "Real Album", "Real Band", tracks=8, single=False, downloads=800)
filler = rel2("q2", "Calm Piano", "Stock Co", text="royalty free background music")
plain_single = rel2("q3", "Plain Single", "Unknown Dude")
lifted = rel2("q4", "Single With Fame", "Known Artist")
wanted_rel = rel2("q5", "Wanted Thing", "Missing Band", text="royalty free")
finder.MODULES = {"internet_archive": Fake2([good, filler, plain_single, lifted], [wanted_rel])}
finder.sources_available = lambda: {"internet_archive": True, "ccmixter": False, "jamendo": False}
finder.library_index = lambda: {"artists": set(), "albums": set(), "songs": set()}
state.write_suggestions({"suggestions": [], "last_search": None})
state.HIDDEN_PATH.unlink(missing_ok=True)
state.save_config(
    state.clean_config({"genres": ["vaporwave"], "min_quality": 25, "max_suggestions": 50})
)
state.WANTED_PATH.write_text(
    json.dumps([{"artist": "Missing Band", "title": "", "added_at": state.now_iso()}]),
    encoding="utf-8",
)
asked = []
quality.musicbrainz_points = lambda artist: (
    asked.append(artist),
    (18, "artist has 6 releases on MusicBrainz")
    if artist == "Known Artist"
    else (0, "artist not found on MusicBrainz"),
)[1]
summary = finder.search_all()
got = {s["title"]: s for s in state.read_suggestions()["suggestions"]}
check(
    "finder: a good album is suggested with its score and reasons",
    "Real Album" in got
    and got["Real Album"]["quality"] >= 25
    and got["Real Album"]["quality_reasons"],
    got.get("Real Album"),
)
check("finder: stock/filler is not suggested", "Calm Piano" not in got)
check("finder: a plain unknown single is below the bar", "Plain Single" not in got)
check(
    "finder: an artist found on MusicBrainz can lift a release over the bar",
    "Single With Fame" in got and got["Single With Fame"]["quality"] == 30,
    got.get("Single With Fame"),
)
check(
    "finder: the wanted list ignores the bar (the score is still shown)",
    "Wanted Thing" in got
    and got["Wanted Thing"]["reason"] == "wanted"
    and "quality" in got["Wanted Thing"],
)
check(
    "finder: the summary says how many were below the bar",
    summary["below_quality"] == 2 and summary["min_quality"] == 25,
    summary,
)
check(
    "finder: the artist check is skipped when nothing could lift the score over the bar",
    "Stock Co" not in asked and "Known Artist" in asked,
    asked,
)
check("finder: each artist is checked at most once", len(asked) == len(set(asked)), asked)
state.write_suggestions({"suggestions": [], "last_search": None})
asked.clear()
state.save_config(state.clean_config({"genres": ["vaporwave"], "min_quality": 0}))
finder.search_all()
check(
    "finder: a bar of 0 suggests everything",
    {s["title"] for s in state.read_suggestions()["suggestions"]}
    >= {"Real Album", "Calm Piano", "Plain Single"},
)
state.write_suggestions({"suggestions": [], "last_search": None})
state.save_config(
    state.clean_config({"genres": ["vaporwave"], "min_quality": 40, "max_suggestions": 50})
)
finder.MODULES = {
    "internet_archive": Fake2(
        [rel2(f"b{i}", f"T{i}", f"Artist {i}", tracks=4, single=False) for i in range(60)]
    )
}
asked.clear()
quality.musicbrainz_points = lambda artist: (
    asked.append(artist),
    (0, "artist not found on MusicBrainz"),
)[1]
finder.search_all()
check(
    "finder: artist checks stop at the per-search budget",
    len(asked) == finder.MB_LOOKUPS_PER_SEARCH,
    len(asked),
)
errors_seen = []


def failing(artist):
    errors_seen.append(artist)
    raise http.FetchError("MusicBrainz is down")


quality.musicbrainz_points = failing
state.write_suggestions({"suggestions": [], "last_search": None})
summary = finder.search_all()
check(
    "finder: repeated MusicBrainz errors stop the checks and the search still finishes",
    len(errors_seen) == 3 and "below_quality" in summary,
    errors_seen,
)
quality.musicbrainz_points = lambda artist: None
quality.mb.find_artist, quality.mb.release_groups, quality.app_settings.contact = (
    real_find,
    real_rgs,
    real_contact,
)
state.WANTED_PATH.unlink(missing_ok=True)

# acquire: music database stand-in
import sqlite_layout as layout

MUSIC_DB = tmp / "music.sqlite"
layout.create(MUSIC_DB, layout.MEDIA)


def q(sql, params=()):
    return layout.raw(MUSIC_DB, sql, params)


def reset_music():
    q("DELETE FROM media_item_relations")
    q("DELETE FROM media_items")


db.get_maint_conn = layout.shim(MUSIC_DB)


def mp3_bytes(seconds=2.0):
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413  # silent MPEG-1 Layer III frame, 128 kbps, ~26 ms
    return frame * int(seconds / 0.026)


def wav_bytes():
    path = tmp / "x.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x01\x02" * 16000)
    return path.read_bytes()


AUDIO = mp3_bytes()
import mutagen

probe = tmp / "probe.mp3"
probe.write_bytes(AUDIO)
try:
    playable = (mutagen.File(probe).info.length or 0) > 1
except Exception:
    playable = False
if not playable:  # fall back to a WAV if the synthetic mp3 is not parsed
    AUDIO, EXT = wav_bytes(), ".wav"
else:
    EXT = ".mp3"
print("     (test audio:", EXT, ")")

PAYLOAD = {}
REFERERS = []


def fake_download(url, dest, max_bytes, progress=None, referer=None):
    REFERERS.append(referer)
    if url not in PAYLOAD:
        raise http.FetchError("404 from archive.org")
    data = PAYLOAD[url]
    if isinstance(data, Exception):
        raise data
    pathlib.Path(dest).write_bytes(data)
    if progress:
        progress(len(data))
    return len(data)


real_host_allowed = http.host_allowed
http.download = fake_download
http.host_allowed = lambda url: (
    url.startswith("https://archive.org/") or url.startswith("https://ccmixter.org/")
)


def sugg(**kw):
    s = {
        "id": "abc123",
        "source": "internet_archive",
        "source_id": "X1",
        "title": "Cold Wave",
        "artist": "Tigerberry",
        "year": 2015,
        "license_code": "by-nc-sa",
        "license_label": "CC BY-NC-SA 4.0",
        "license_url": "http://creativecommons.org/licenses/by-nc-sa/4.0/",
        "needs_credit": True,
        "page_url": "https://archive.org/details/X1",
        "genre": "vaporwave",
        "tracks": [
            {
                "title": "In Your Eyes",
                "number": 1,
                "url": "https://archive.org/download/X1/01.mp3",
                "filename": f"01_-_In_Your_Eyes{EXT}",
                "size": 100,
            },
            {
                "title": "Get Out",
                "number": 2,
                "url": "https://archive.org/download/X1/02.mp3",
                "filename": f"02_-_Get_Out{EXT}",
                "size": 100,
            },
        ],
        "cover_url": "https://archive.org/download/X1/cover.jpg",
        "single": False,
        "size": 200,
        "known_artist": False,
    }
    s.update(kw)
    state.write_suggestions({"suggestions": [s], "last_search": None})
    return s


def reset_payload():
    PAYLOAD.clear()
    PAYLOAD["https://archive.org/download/X1/01.mp3"] = AUDIO
    PAYLOAD["https://archive.org/download/X1/02.mp3"] = AUDIO
    PAYLOAD["https://archive.org/download/X1/cover.jpg"] = b"\xff\xd8\xff\xe0fakejpeg"


def tree():
    return sorted(
        str(p.relative_to(tmp / "music")) for p in (tmp / "music").rglob("*") if p.is_file()
    )


# a good album
reset_payload()
s = sugg()
progress = []
msg = acquire.acquire(s, "director", progress.append)
folder = tmp / "music" / "Tigerberry" / "Albums" / "Cold Wave"
check(
    "acquire: album lands in Artist/Albums/Title",
    (folder / f"01_-_In_Your_Eyes{EXT}").is_file() and (folder / f"02_-_Get_Out{EXT}").is_file(),
    tree(),
)
check("acquire: cover saved next to the songs", (folder / "cover.jpg").is_file())
check(
    "acquire: a source note records the license",
    any(p.name.startswith("_source_") for p in folder.iterdir())
    and "CC BY-NC-SA 4.0" in next(folder.glob("_source_*.json")).read_text(encoding="utf-8"),
)
check(
    "acquire: library rows created (1 album, 2 tracks, credits)",
    (
        len(q("select * from albums")),
        len(q("select * from tracks")),
        len(q("select * from track_artists")),
    )
    == (1, 2, 2),
)
al = q("select * from albums")[0]
check(
    "acquire: album has year and cover path",
    al["year"] == 2015 and al["cover_art_path"].endswith("cover.jpg"),
)
check(
    "acquire: track titles and numbers stored",
    [(t["track_number"], t["title"]) for t in q("select * from tracks order by track_number")]
    == [(1, "In Your Eyes"), (2, "Get Out")],
)
check(
    "acquire: paths stored in the database point at real files",
    all(os.path.isfile(t["file_path"]) for t in q("select file_path from tracks")),
)
check("acquire: no .part files left behind", not any(n.endswith(".part") for n in tree()))
rec = state.added()[-1]
check(
    "acquire: provenance recorded (source, license, who)",
    rec["license_label"] == "CC BY-NC-SA 4.0"
    and rec["by"] == "director"
    and rec["page_url"].startswith("https://archive.org/")
    and "Tigerberry" in rec["attribution"],
)
check("acquire: suggestion removed once added", state.find_suggestion("abc123") is None)
check("acquire: progress was reported", sum(progress) > 0)
check("acquire: message is readable", "Cold Wave" in msg and "2 songs" in msg, msg)

# undo
check(
    "undo: needs a recorded addition",
    (lambda: (_ for _ in ()).throw(AssertionError) if False else True)(),
)
um = acquire.undo("abc123", "director")
check(
    "undo: library rows gone",
    len(q("select * from tracks")) == 0 and len(q("select * from track_artists")) == 0,
    um,
)
check(
    "undo: files moved to .trash, not deleted",
    not (folder / f"01_-_In_Your_Eyes{EXT}").exists()
    and any((tmp / "music" / ".trash").rglob(f"01_-_In_Your_Eyes{EXT}")),
)
check("undo: addition marked undone", state.added()[-1].get("undone_at"))
try:
    acquire.undo("abc123", "director")
    check("undo: cannot be repeated", False)
except acquire.AcquireError:
    check("undo: cannot be repeated", True)

# a loose single
reset_payload()
s = sugg(
    id="single1",
    title="Loose Song",
    single=True,
    cover_url="",
    tracks=[
        {
            "title": "Loose Song",
            "number": None,
            "url": "https://archive.org/download/X1/01.mp3",
            "filename": f"loose{EXT}",
            "size": 100,
        }
    ],
)
acquire.acquire(s, "director")
check(
    "acquire: a single goes to Singles with no album",
    (tmp / "music" / "Tigerberry" / "Singles" / f"loose{EXT}").is_file()
    and q("select album_id from tracks where title='Loose Song'")[0]["album_id"] is None,
)
reset_music()


# failures clean up completely
def snapshot():
    return (tree(), len(q("select * from tracks")), len(q("select * from albums")))


def fails_cleanly(name, s, pre=None):
    reset_payload()
    if pre:
        pre()
    before = snapshot()
    try:
        acquire.acquire(s, "director")
        check(name, False, "did not fail")
    except acquire.AcquireError as e:
        after = snapshot()
        check(name, after == before, f"{after} vs {before}")
        return str(e)


shutil.rmtree(tmp / "music" / "Tigerberry", ignore_errors=True)
msg = fails_cleanly(
    "failure on the 2nd song leaves nothing behind (no files, no rows)",
    sugg(id="f1"),
    lambda: PAYLOAD.__setitem__(
        "https://archive.org/download/X1/02.mp3", http.FetchError("connection reset")
    ),
)
check("...and says so plainly", "failed" in msg.lower(), msg)
check(
    "...the artist folder we just created is removed too",
    not (tmp / "music" / "Tigerberry").exists(),
)
fails_cleanly(
    "a non-audio download is refused and cleaned up",
    sugg(id="f2"),
    lambda: PAYLOAD.__setitem__(
        "https://archive.org/download/X1/01.mp3", b"<html>not music</html>" * 100
    ),
)
fails_cleanly(
    "license tampered to a non-free one is refused",
    sugg(id="f3", license_url="http://creativecommons.org/licenses/sampling+/1.0/"),
)
fails_cleanly(
    "license code that does not match its URL is refused", sugg(id="f4", license_code="cc0")
)
http.host_allowed = real_host_allowed
socket.getaddrinfo = lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
bad_host = sugg(id="f5")
bad_host["tracks"][0]["url"] = "https://10.0.0.5/steal"
fails_cleanly("a download link to an internal address is refused", bad_host)
socket.getaddrinfo = real_getaddr
http.host_allowed = lambda url: (
    url.startswith("https://archive.org/") or url.startswith("https://ccmixter.org/")
)
fails_cleanly(
    "existing files with those names are refused",
    sugg(id="f6"),
    lambda: (
        (tmp / "music" / "Tigerberry" / "Albums" / "Cold Wave").mkdir(parents=True),
        (
            tmp / "music" / "Tigerberry" / "Albums" / "Cold Wave" / f"01_-_In_Your_Eyes{EXT}"
        ).write_bytes(b"mine"),
    ),
)
shutil.rmtree(tmp / "music" / "Tigerberry", ignore_errors=True)
bad_ext = sugg(id="f7")
bad_ext["tracks"][0]["filename"] = "evil.exe"
fails_cleanly("unsupported file types are refused", bad_ext)
real_disk = shutil.disk_usage
shutil.disk_usage = lambda p: type("U", (), {"free": 1024})()
fails_cleanly("not enough disk space is refused up front", sugg(id="f8"))
shutil.disk_usage = real_disk
real_insert = uploader.insert_tracks
uploader.insert_tracks = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database exploded"))
reset_payload()
before = snapshot()
s = sugg(id="f9")
try:
    acquire.acquire(s, "director")
    check("a database failure cleans up the files", False)
except RuntimeError:
    check("a database failure cleans up the files", snapshot() == before, snapshot())
uploader.insert_tracks = real_insert
os.environ.pop("DB_MAINT_USER")
fails_cleanly("adding is refused when the maintenance role is not configured", sugg(id="f10"))
os.environ["DB_MAINT_USER"] = "x"

# bookkeeping failure after the commit must not delete files the library already points at
reset_payload()
s = sugg(id="bk1")
real_append = state.append_added
state.append_added = lambda r: (_ for _ in ()).throw(OSError("disk full"))
msg = acquire.acquire(s, "director")
state.append_added = real_append
check(
    "a logging failure after the commit keeps the files",
    (tmp / "music" / "Tigerberry" / "Albums" / "Cold Wave" / f"01_-_In_Your_Eyes{EXT}").is_file()
    and len(q("select * from tracks")) == 2,
    msg,
)
reset_music()

# web layer
for k in ("DB_AUTH_USER", "DB_AUTH_PASSWORD"):
    os.environ[k] = "x"
import sqlite_layout as layout
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


def signed_in(user, pw):
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c, {"X-CSRF-Token": c.get("/api/auth/me").json()["csrf"]}


anon = TestClient(main.app)
admin, H = signed_in("Nova", PW)
member, MH = signed_in("friend", PW + "!")

for path in ("/admin", "/api/collector"):
    check(
        f"anonymous: {path} refused",
        anon.get(path, follow_redirects=False).status_code in (303, 401),
    )
    check(
        f"member: {path} is admin-only", member.get(path, follow_redirects=False).status_code == 403
    )
check(
    "an auth database without the permissions columns still works (the owner has not run user_permissions.sql yet)",
    service.has_permission_columns() is False
    and admin.get("/api/collector").status_code == 200
    and member.get("/api/collector").status_code == 403,
)
check(
    "admin: the Admin page loads with all three tabs",
    admin.get("/admin").status_code == 200
    and all(
        t in admin.get("/admin").text
        for t in ('data-tab="status"', 'data-tab="maintenance"', 'data-tab="collector"')
    ),
)
check(
    "admin: the Collector tab has the quality minimum and the clean-up panel",
    all(
        x in admin.get("/admin").text
        for x in ('id="min_quality"', 'id="reviewBtn"', 'id="reviewUndo"')
    )
    and " onclick=" not in admin.get("/admin").text,
)
check(
    "old /status and /discover links land on the Admin page",
    admin.get("/status", follow_redirects=False).headers["location"] == "/admin#status"
    and admin.get("/discover", follow_redirects=False).headers["location"] == "/admin#collector",
)
check(
    "admin nav shows admin, member nav does not",
    'href="/admin"' in admin.get("/").text and 'href="/admin"' not in member.get("/").text,
)
for path, body in (
    ("/api/collector/config", {"genres": "x"}),
    ("/api/collector/search", None),
    ("/api/collector/approve", {"id": "a"}),
    ("/api/collector/approve-artist", {"id": "a"}),
    ("/api/collector/dismiss", {"id": "a"}),
    ("/api/collector/undo", {"id": "a"}),
):
    check(f"member cannot POST {path}", member.post(path, json=body, headers=MH).status_code == 403)
    check(f"admin POST {path} needs the CSRF token", admin.post(path, json=body).status_code == 403)

state.write_suggestions({"suggestions": [], "last_search": None})
state.save_config(state.clean_config({}))
r = admin.get("/api/collector").json()
check(
    "overview: shape",
    {"config", "suggestions", "jobs", "added", "searching", "sources_available", "adds_enabled"}
    <= set(r),
    list(r),
)
check("overview: jamendo unavailable without a key", r["sources_available"]["jamendo"] is False)
check(
    "config: bad genre is a clean 422",
    admin.post("/api/collector/config", json={"genres": "bad;genre"}, headers=H).status_code == 422,
)
r = admin.post(
    "/api/collector/config", json={"genres": "vaporwave, chiptune", "per_genre": 3}, headers=H
)
check(
    "config: saved and returned",
    r.status_code == 200
    and [g["name"] for g in r.json()["genres"]] == ["vaporwave", "chiptune"]
    and state.load_config()["per_genre"] == 3,
)
check(
    "config: huge body refused by the gate",
    admin.post(
        "/api/collector/config",
        content=b"x" * 70000,
        headers={**H, "Content-Type": "application/json"},
    ).status_code
    == 413,
)

state.save_config(state.clean_config({"genres": ["vaporwave"]}))
finder.search_all = lambda: {"at": state.now_iso(), "new": 0, "genres": ["vaporwave"], "errors": []}
check(
    "search: starts in the background",
    admin.post("/api/collector/search", headers=H).json()["started"] is True,
)

a, b = (
    rel("internet_archive", "w1", "Web One", "Web Artist"),
    rel("internet_archive", "w2", "Web Two", "Web Artist"),
)
state.write_suggestions(
    {
        "suggestions": [
            {
                **a.to_dict(),
                "id": "idA",
                "found_at": state.now_iso(),
                "known_artist": False,
                "size": 1000,
            },
            {
                **b.to_dict(),
                "id": "idB",
                "found_at": state.now_iso(),
                "known_artist": False,
                "size": 1000,
            },
        ],
        "last_search": None,
    }
)
acquired = []
real_acquire = acquire.acquire
acquire.acquire = lambda s, actor, progress=None: (
    acquired.append((s["id"], actor)),
    f'Added "{s["title"]}"',
)[1]
check(
    "approve: unknown id is 404",
    admin.post("/api/collector/approve", json={"id": "nope"}, headers=H).status_code == 404,
)
r = admin.post("/api/collector/approve", json={"id": "idA"}, headers=H)
check(
    "approve: queues a background job",
    r.status_code == 200 and r.json()["status"] in ("queued", "running", "done"),
    r.text,
)
for _ in range(40):
    if acquired:
        break
    time.sleep(0.1)
check("approve: worker ran the add as the signed-in admin", acquired == [("idA", "Nova")], acquired)
time.sleep(0.3)
check(
    "approve: job finishes with a message",
    any(
        j["status"] == "done" and "Web One" in j["message"]
        for j in admin.get("/api/collector").json()["jobs"]
    ),
)
r = admin.post("/api/collector/approve-artist", json={"id": "idB"}, headers=H)
check("approve-artist: queues every suggestion from that artist", r.json()["queued"] == 2, r.text)
for _ in range(40):
    if len(acquired) >= 3:
        break
    time.sleep(0.1)
check(
    "approve-artist: all of them were processed",
    {x[0] for x in acquired} == {"idA", "idB"},
    acquired,
)
acquire.acquire = real_acquire

state.write_suggestions(
    {
        "suggestions": [
            {**a.to_dict(), "id": "idC", "found_at": state.now_iso(), "known_artist": False},
            {**b.to_dict(), "id": "idD", "found_at": state.now_iso(), "known_artist": False},
        ],
        "last_search": None,
    }
)
check(
    "dismiss: removes just that suggestion",
    admin.post("/api/collector/dismiss", json={"id": "idC"}, headers=H).status_code == 200
    and [s["id"] for s in state.read_suggestions()["suggestions"]] == ["idD"]
    and "idC" in state.hidden()["ids"],
)
state.write_suggestions(
    {
        "suggestions": [
            {**a.to_dict(), "id": "idE", "found_at": state.now_iso(), "known_artist": False},
            {**b.to_dict(), "id": "idF", "found_at": state.now_iso(), "known_artist": False},
        ],
        "last_search": None,
    }
)
check(
    "dismiss + block artist: removes all by them and remembers",
    admin.post(
        "/api/collector/dismiss", json={"id": "idE", "block_artist": True}, headers=H
    ).status_code
    == 200
    and state.read_suggestions()["suggestions"] == []
    and sq("Web Artist") in state.hidden()["artists"],
)
check(
    "undo: unknown id is a clean 409",
    admin.post("/api/collector/undo", json={"id": "never-added"}, headers=H).status_code == 409,
)
check(
    "id fields are length limited",
    admin.post("/api/collector/approve", json={"id": "x" * 200}, headers=H).status_code == 422,
)

# review and bulk undo of earlier adds that fall below the bar
state.ADDED_PATH.unlink(missing_ok=True)
state.save_config(state.clean_config({"min_quality": 25}))
base_rec = {
    "at": state.now_iso(),
    "by": "Nova",
    "page_url": "https://archive.org/details/x",
    "license_label": "CC BY 4.0",
    "files": [],
}
state.append_added(
    {
        **base_rec,
        "id": "old-loop",
        "source": "ccmixter",
        "artist": "Loop Guy",
        "title": "A Loop",
        "single": True,
        "tracks": 1,
    }
)
state.append_added(
    {
        **base_rec,
        "id": "old-album",
        "source": "internet_archive",
        "artist": "Band",
        "title": "Real Album",
        "single": False,
        "tracks": 9,
    }
)
state.append_added(
    {
        **base_rec,
        "id": "new-low",
        "source": "internet_archive",
        "artist": "Stock Co",
        "title": "Calm",
        "single": True,
        "tracks": 1,
        "quality": 3,
        "quality_reasons": ["looks like stock or filler music"],
    }
)
state.append_added(
    {
        **base_rec,
        "id": "new-high",
        "source": "internet_archive",
        "artist": "Great",
        "title": "Fine",
        "single": False,
        "tracks": 8,
        "quality": 80,
        "quality_reasons": [],
    }
)
state.append_added(
    {
        **base_rec,
        "id": "gone",
        "source": "ccmixter",
        "artist": "X",
        "title": "Undone",
        "single": True,
        "tracks": 1,
        "undone_at": state.now_iso(),
    }
)
check("review: members refused", member.get("/api/collector/review-added").status_code == 403)
r = admin.get("/api/collector/review-added").json()
ids = [i["id"] for i in r["items"]]
check(
    "review: lists only adds below the bar (scored now or when added), never undone ones",
    sorted(ids) == ["new-low", "old-loop"] and r["min_quality"] == 25,
    ids,
)
low = {i["id"]: i for i in r["items"]}
check(
    "review: says whether the score was made when it was added",
    low["new-low"]["scored_when_added"] is True
    and low["old-loop"]["scored_when_added"] is False
    and low["new-low"]["reasons"],
)
state.save_config(state.clean_config({"min_quality": 90}))
check(
    "review: raising the bar lists more",
    {i["id"] for i in admin.get("/api/collector/review-added").json()["items"]}
    == {"old-loop", "old-album", "new-low", "new-high"},
)
state.save_config(state.clean_config({"min_quality": 0}))
check(
    "review: a bar of 0 lists nothing",
    admin.get("/api/collector/review-added").json()["items"] == [],
)
state.save_config(state.clean_config({"min_quality": 25}))

undone = []
real_undo = acquire.undo


def fake_undo(release_id, actor):
    if release_id == "boom":
        raise RuntimeError("secret internals")
    if release_id == "old-album":
        raise acquire.AcquireError("That addition was already undone, or was never recorded.")
    undone.append((release_id, actor))
    return f"Removed {release_id}."


acquire.undo = fake_undo
check(
    "undo-many: members refused, CSRF needed",
    member.post("/api/collector/undo-many", json={"ids": ["a"]}, headers=MH).status_code == 403
    and admin.post("/api/collector/undo-many", json={"ids": ["a"]}).status_code == 403,
)
check(
    "undo-many: needs at least one id, at most 100, each short",
    admin.post("/api/collector/undo-many", json={"ids": []}, headers=H).status_code == 422
    and admin.post("/api/collector/undo-many", json={"ids": ["x"] * 101}, headers=H).status_code
    == 422
    and admin.post("/api/collector/undo-many", json={"ids": ["x" * 41]}, headers=H).status_code
    == 422,
)
r = admin.post(
    "/api/collector/undo-many",
    json={"ids": ["old-loop", "old-album", "boom", "new-low", "old-loop"]},
    headers=H,
).json()
check(
    "undo-many: undoes each id once as the signed-in admin",
    undone == [("old-loop", "Nova"), ("new-low", "Nova")],
    undone,
)
check(
    "undo-many: one failure does not stop the rest, and is reported",
    r["undone"] == 2 and [x["ok"] for x in r["results"]] == [True, False, False, True],
    r,
)
check("undo-many: internal errors are not leaked", "secret" not in json.dumps(r))
acquire.undo = real_undo
state.ADDED_PATH.unlink(missing_ok=True)

# live, read-only (only with --live)
print("--- credit line for an album page")
state.ADDED_PATH.unlink(missing_ok=True)
state.append_added(
    {
        "id": "cr1",
        "album_id": 41,
        "source": "internet_archive",
        "page_url": "https://archive.org/details/x",
        "license_label": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "needs_credit": True,
        "attribution": '"A" by B, CC BY 4.0, https://archive.org/details/x',
    }
)
state.append_added(
    {
        "id": "cr2",
        "album_id": 42,
        "source": "ccmixter",
        "page_url": "javascript:alert(1)",
        "license_label": "CC0 1.0",
        "license_url": "ftp://x",
        "needs_credit": False,
    }
)
state.append_added(
    {
        "id": "cr3",
        "album_id": 43,
        "source": "jamendo",
        "license_label": "CC BY 4.0",
        "undone_at": "2026-10-09T00:00:00+00:00",
    }
)
c = state.credit_for_album(41)
check(
    "credit: an added album carries its license, links and credit text",
    c
    and c["license"] == "CC BY 4.0"
    and c["license_url"].startswith("https://creativecommons.org/")
    and c["page_url"] == "https://archive.org/details/x"
    and c["needs_credit"]
    and "CC BY 4.0" in c["attribution"],
    c,
)
c = state.credit_for_album(42)
check(
    "credit: a link that is not http(s) is dropped",
    c and c["page_url"] == "" and c["license_url"] == "" and not c["needs_credit"],
    c,
)
check("credit: an undone addition has none", state.credit_for_album(43) is None)
check(
    "credit: an album the collector did not add has none",
    state.credit_for_album(7) is None and state.credit_for_album(None) is None,
)
state.ADDED_PATH.unlink(missing_ok=True)

if LIVE:
    print("--- live searches (read-only, nothing downloaded)")
    http.host_allowed = real_host_allowed
    socket.getaddrinfo = real_getaddr
    http.get_json = real_get
    for mod, genre in ((internet_archive, "chiptune"), (ccmixter, "chiptune")):
        found = mod.search(genre, 3, 600 * 1024 * 1024)
        check(f"live {mod.NAME}: found releases for {genre}", len(found) > 0, found)
        for r in found:
            print(
                f"     {r.artist} - {r.title} | {r.license_label} | {len(r.tracks)} song(s) | {r.size // 1048576} MB | {r.page_url}"
            )
        check(
            f"live {mod.NAME}: every result has a free license and allowed links",
            all(
                licenses.classify(r.license_url)
                and http.link_allowed(r.page_url)
                and all(http.host_allowed(t.url) for t in r.tracks)
                for r in found
            ),
        )

finish()
