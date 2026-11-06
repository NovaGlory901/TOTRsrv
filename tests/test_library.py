"""Tests for archive search and mixtapes. Search ranking runs on a hand-built index; one check reads
the real library (read-only role) to prove the index SQL is valid. Mixtapes run against a throwaway
SQLite file with the same tables."""

import os
import pathlib
import re
import sys
import tempfile

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["APP_SETTINGS"] = str(tmp / "reports" / "app_settings.json")

from check import check, finish

import library_search as ls


# search ranking (no database)
def artist(i, name):
    return ls._entry(name, artist_id=i, name=name, track_count=3)


def album(i, title, owner="Someone"):
    return ls._entry(title, album_id=i, title=title, year=2020, owner=owner, tracks=5)


def song(i, title, artists="", album_title=None):
    e = ls._entry(title, track_id=i, title=title, artists=artists, album_id=None, album=album_title)
    e["context"] = ls.squash(f"{title} {artists} {album_title or ''}")
    return e


INDEX = {
    "artists": [
        artist(1, "Kanal Vier"),
        artist(2, "ＫＡＮＡＬ"),
        artist(3, "Lo-Fi Girl"),
        artist(4, "Slowerpace 音楽"),
        artist(5, "The Kanal Collective"),
    ],
    "albums": [album(1, "Cold Wave"), album(2, "Lofi Beats to Sleep To"), album(3, "Waves")],
    "songs": [
        song(1, "In Your Eyes", "Tigerberry", "Cold Wave"),
        song(2, "Dreams", "Slowerpace 音楽"),
        song(3, "Dream Machine", "Kanal Vier"),
        song(4, "Kanal", "Someone Else"),
        song(5, "Lo fi dreams"),
        song(6, "Wave Rider", "Tigerberry", "Waves"),
    ],
}
names = lambda r, g: [e["name" if g == "artists" else "title"] for e in r[g]]
S = lambda q: ls.search(INDEX, q)

check(
    "search: case does not matter",
    names(S("KANAL VIER"), "artists") == ["Kanal Vier"]
    and names(S("kanal vier"), "artists") == ["Kanal Vier"],
)
check(
    "search: punctuation and spaces do not matter (lo-fi, lo fi, lofi, LOFI)",
    all(
        "Lo-Fi Girl" in names(S(q), "artists")
        for q in ("lo-fi", "lo fi", "lofi", "LOFI", "lo_fi", "l.o.f.i")
    ),
)
check(
    "search: full-width letters match the same as normal ones",
    "ＫＡＮＡＬ" in names(S("kanal"), "artists")
    and "Kanal Vier" in names(S("ｋａｎａｌ"), "artists"),
)
check(
    "search: non-Latin names are found",
    names(S("音楽"), "artists") == ["Slowerpace 音楽"]
    and names(S("slowerpace"), "artists") == ["Slowerpace 音楽"],
)
check(
    "search: every word must match, in any order",
    names(S("vier kanal"), "artists") == ["Kanal Vier"] and names(S("kanal zzz"), "artists") == [],
)
check(
    "search: exact match first, then starts-with, then word-start, then anywhere",
    names(S("kanal"), "artists") == ["ＫＡＮＡＬ", "Kanal Vier", "The Kanal Collective"]
    and names(S("nal"), "artists")[0] in ("ＫＡＮＡＬ", "Kanal Vier", "The Kanal Collective"),
)
check(
    "search: finds albums and songs too, grouped by type",
    names(S("wave"), "albums") == ["Waves", "Cold Wave"]
    and names(S("wave"), "songs") == ["Wave Rider"],
)
check("search: a song is found by its title", names(S("eyes"), "songs") == ["In Your Eyes"])
check(
    "search: one word that is only an artist's name does not flood the songs",
    names(S("tigerberry"), "songs") == [],
)
check(
    "search: two or more words can match across title and artist, ranked after real title matches",
    names(S("dreams slowerpace"), "songs") == ["Dreams"]
    and names(S("dream"), "songs")[:2]
    in (["Dreams", "Dream Machine"], ["Dream Machine", "Dreams"]),
)
check(
    "search: results carry what the page needs and nothing internal",
    set(S("kanal vier")["artists"][0]) == {"artist_id", "name", "track_count"}
    and set(S("eyes")["songs"][0]) == {"track_id", "title", "artists", "album_id", "album"}
    and set(S("cold")["albums"][0]) == {"album_id", "title", "year", "owner", "tracks"},
)
for junk in ("", "   ", "!!!", "---", "...", None):
    r = S(junk)
    check(
        f"search: {junk!r} finds nothing and does not fail",
        r["artists"] == r["albums"] == r["songs"] == []
        and r["totals"] == {"artists": 0, "albums": 0, "songs": 0},
    )
check(
    "search: hostile text is only ever text (no SQL involved)",
    S("'; DROP TABLE tracks; --")["songs"] == []
    and S("%")["songs"] == []
    and S("a" * 5000)["query"] == "a" * ls.MAX_QUERY,
)
big = {"artists": [], "albums": [], "songs": [song(i, f"Test {i}") for i in range(100)]}
r = ls.search(big, "test")
check(
    "search: a group is capped but the total is honest",
    len(r["songs"]) == ls.GROUP_LIMIT and r["totals"]["songs"] == 100,
)
check(
    "search: ties are listed alphabetically",
    [
        e["title"]
        for e in ls.search(
            {
                "artists": [],
                "albums": [],
                "songs": [song(1, "b track"), song(2, "A track"), song(3, "c track")],
            },
            "track",
        )["songs"]
    ]
    == ["A track", "b track", "c track"],
)
check(
    "search: tokens use the same squash as the spiders",
    ls.tokens_of("Lo-Fi  ＧＩＲＬ!") == ["lofi", "girl"],
)

# the index SQL against the real library (read-only)
import sqlite_layout as layout

import db

if layout.live_ready():
    real = ls.get_index()
    check(
        "index: built from the real library with artists, albums and songs",
        all(isinstance(real[g], list) for g in ("artists", "albums", "songs")) and real["songs"],
        {g: len(real[g]) for g in real},
    )
    check("index: a second search reuses it (nothing changed)", ls.get_index() is real)
    first = real["artists"][0]["name"] if real["artists"] else None
    if first:
        found = ls.search(real, first)
        check(
            "index: searching an artist's own name finds them",
            any(a["name"] == first for a in found["artists"]),
            first,
        )
    first_song = real["songs"][0]
    check(
        "index: searching a song's own title finds it",
        any(
            s["track_id"] == first_song["track_id"]
            for s in ls.search(real, first_song["title"])["songs"]
        ),
        first_song["title"],
    )
else:
    print(
        "skip  the index SQL against the real library  (no database with the TOTR layout is set up)"
    )

# mixtapes: a throwaway database with the same tables
import sqlite_layout as layout

MIX_DB = tmp / "mixtapes.sqlite"
layout.create(MIX_DB, layout.MIXTAPES)
AUTH_DB = tmp / "auth.sqlite"
layout.create(AUTH_DB, layout.ACCOUNTS)
make_shim = layout.shim
raw = lambda sql, params=(): layout.raw(MIX_DB, sql, params)


for k in ("DB_AUTH_USER", "DB_AUTH_PASSWORD"):
    os.environ[k] = "x"
os.environ.pop("DB_MIXTAPE_USER", None)
os.environ.pop("DB_MIXTAPE_PASSWORD", None)
from fastapi.testclient import TestClient

import main
from auth import service
from mixtapes import web as mx

service.connect = make_shim(AUTH_DB)
mx.connect = make_shim(MIX_DB)
PW = "correct horse battery staple"
service.create_user("alice", PW)
service.create_user("bob", PW + "!")


def signed_in(user, pw):
    cl = TestClient(main.app)
    assert cl.post("/api/auth/login", json={"username": user, "password": pw}).status_code == 200
    return cl, {"X-CSRF-Token": cl.get("/api/auth/me").json()["csrf"]}


alice, AH = signed_in("alice", PW)
bob, BH = signed_in("bob", PW + "!")
anon = TestClient(main.app)

REAL_LOOKUP = mx.lookup_tracks
KNOWN = set(range(1, 5001))
mx.lookup_tracks = lambda ids: {
    i: {"track_id": i, "title": f"Song {i}", "artists": f"Artist {i}", "album_id": None}
    for i in ids
    if i in KNOWN
}

# not set up yet
check(
    "mixtapes: before setup the list says so instead of failing",
    alice.get("/api/mixtapes").json()["enabled"] is False
    and "sql/mixtapes.sql" in alice.get("/api/mixtapes").json()["setup"],
)
check(
    "mixtapes: before setup changes give a clear 503",
    alice.post("/api/mixtapes", json={"name": "x"}, headers=AH).status_code == 503
    and "mixtapes.sql"
    in alice.post("/api/mixtapes", json={"name": "x"}, headers=AH).json()["detail"],
)
os.environ["DB_MIXTAPE_USER"] = "x"
os.environ["DB_MIXTAPE_PASSWORD"] = "y"

ROUTES = [
    ("GET", "/api/mixtapes"),
    ("POST", "/api/mixtapes"),
    ("GET", "/api/mixtapes/1"),
    ("POST", "/api/mixtapes/1/rename"),
    ("POST", "/api/mixtapes/1/delete"),
    ("POST", "/api/mixtapes/1/tracks"),
    ("POST", "/api/mixtapes/1/tracks/1/remove"),
    ("POST", "/api/mixtapes/1/tracks/1/move"),
    ("GET", "/api/search?q=x"),
]
for method, path in ROUTES:
    check(
        f"anonymous: {method} {path} is refused",
        anon.request(method, path, json={} if method == "POST" else None).status_code == 401,
    )
for method, path in ROUTES:
    if method == "POST":
        check(
            f"CSRF token required: {path}", alice.post(path, json={"name": "x"}).status_code == 403
        )

# create / list / validation
r = alice.post("/api/mixtapes", json={"name": "  Road   Trip  "}, headers=AH)
mid = r.json()["mixtape_id"]
check(
    "create: name is tidied and the mixtape is made",
    r.status_code == 200 and r.json()["name"] == "Road Trip",
)
for bad in ("", "   ", "x" * 81, chr(7) + "name"):
    check(
        f"create: refuses name {bad[:12]!r}",
        alice.post("/api/mixtapes", json={"name": bad}, headers=AH).status_code == 422,
    )
check(
    "create: refuses a missing name or one that is far too long",
    alice.post("/api/mixtapes", json={}, headers=AH).status_code == 422
    and alice.post("/api/mixtapes", json={"name": "x" * 500}, headers=AH).status_code == 422,
)
check(
    "create: the same name (any case) is refused for the same person",
    alice.post("/api/mixtapes", json={"name": "road trip"}, headers=AH).status_code == 409,
)
check(
    "create: but another person may use it",
    bob.post("/api/mixtapes", json={"name": "Road Trip"}, headers=BH).status_code == 200,
)
r = alice.post(
    "/api/mixtapes", json={"name": "Night Drive", "track_ids": [3, 1, 3, 999999]}, headers=AH
)
check(
    "create: starting songs are added in order, once each, unknown ones skipped",
    r.json()["added"] == 2 and r.json()["skipped"] == 1,
)
nd = r.json()["mixtape_id"]
lst = alice.get("/api/mixtapes").json()["mixtapes"]
check(
    "list: only my own, sorted by name, with counts",
    [m["name"] for m in lst] == ["Night Drive", "Road Trip"]
    and [m["track_count"] for m in lst] == [2, 0],
)
check(
    "list: never includes another person's",
    [m["name"] for m in bob.get("/api/mixtapes").json()["mixtapes"]] == ["Road Trip"],
)

# songs: add / get / move / remove
check(
    "add: refuses an empty list, bad ids and too many",
    alice.post(f"/api/mixtapes/{mid}/tracks", json={"track_ids": []}, headers=AH).status_code == 422
    and all(
        alice.post(f"/api/mixtapes/{mid}/tracks", json={"track_ids": bad}, headers=AH).status_code
        == 422
        for bad in ([0], [-5], [2**40], ["abc"], [1.5], list(range(1, 202)))
    )
    and alice.post(f"/api/mixtapes/{mid}/tracks", json={}, headers=AH).status_code == 422,
)
r = alice.post(
    f"/api/mixtapes/{mid}/tracks", json={"track_ids": [10, 11, 12, 11, 888888]}, headers=AH
).json()
check("add: appends existing songs once, reports skipped", r == {"added": 3, "skipped": 1}, r)
check(
    "add: nothing exists -> nothing added",
    alice.post(f"/api/mixtapes/{mid}/tracks", json={"track_ids": [888888]}, headers=AH).json()
    == {"added": 0, "skipped": 1},
)
got = alice.get(f"/api/mixtapes/{mid}").json()
check(
    "get: songs come back in order with titles and artists",
    [t["track_id"] for t in got["tracks"]] == [10, 11, 12]
    and got["tracks"][0]["title"] == "Song 10"
    and got["tracks"][0]["artists"] == "Artist 10"
    and got["name"] == "Road Trip",
)
eids = [t["entry_id"] for t in got["tracks"]]
move = lambda eid, d: alice.post(
    f"/api/mixtapes/{mid}/tracks/{eid}/move", json={"direction": d}, headers=AH
)
order = lambda: [t["track_id"] for t in alice.get(f"/api/mixtapes/{mid}").json()["tracks"]]
check(
    "move: a song can go later",
    move(eids[0], "down").status_code == 200 and order() == [11, 10, 12],
)
check("move: ...and earlier", move(eids[2], "up").status_code == 200 and order() == [11, 12, 10])
before = order()
top = [t["entry_id"] for t in alice.get(f"/api/mixtapes/{mid}").json()["tracks"]]
move(top[0], "up")
move(top[-1], "down")
check("move: moving the first up or the last down changes nothing", order() == before)
check(
    "move: bad direction and unknown song are refused",
    alice.post(
        f"/api/mixtapes/{mid}/tracks/{top[0]}/move", json={"direction": "sideways"}, headers=AH
    ).status_code
    == 422
    and move(99999, "up").status_code == 404,
)
raw("UPDATE totr_mixtape_tracks SET position = 7 WHERE mixtape_id = ?", (mid,))
tie_order = [t["entry_id"] for t in alice.get(f"/api/mixtapes/{mid}").json()["tracks"]]
move(tie_order[1], "up")
check(
    "move: equal positions are tidied first, so a move still works",
    [t["entry_id"] for t in alice.get(f"/api/mixtapes/{mid}").json()["tracks"]][:2]
    == [tie_order[1], tie_order[0]],
)
remove = lambda m, e, h=AH, c=alice: c.post(f"/api/mixtapes/{m}/tracks/{e}/remove", headers=h)
check(
    "remove: takes one entry out and keeps the rest",
    remove(mid, top[0]).status_code == 200 and len(order()) == 2,
)
check(
    "remove: again, or a song from another mixtape, is a 404",
    remove(mid, top[0]).status_code == 404
    and remove(
        mid,
        raw("SELECT entry_id FROM totr_mixtape_tracks WHERE mixtape_id = ?", (nd,))[0]["entry_id"],
    ).status_code
    == 404,
)
check(
    "remove: the other mixtape is untouched",
    len(alice.get(f"/api/mixtapes/{nd}").json()["tracks"]) == 2,
)
mx.lookup_tracks = lambda ids: {
    i: {"track_id": i, "title": f"Song {i}", "artists": "", "album_id": None}
    for i in ids
    if i in KNOWN and i != 1
}
check(
    "get: a song that no longer exists in the library is hidden, not an error",
    [t["track_id"] for t in alice.get(f"/api/mixtapes/{nd}").json()["tracks"]] == [3],
)
mx.lookup_tracks = lambda ids: {
    i: {"track_id": i, "title": f"Song {i}", "artists": f"Artist {i}", "album_id": None}
    for i in ids
    if i in KNOWN
}

# privacy: nobody reaches anyone else's mixtape
snapshot = raw("SELECT * FROM totr_mixtapes ORDER BY mixtape_id") + raw(
    "SELECT * FROM totr_mixtape_tracks ORDER BY entry_id"
)
some_entry = raw("SELECT entry_id FROM totr_mixtape_tracks WHERE mixtape_id = ?", (nd,))[0][
    "entry_id"
]
attempts = [
    bob.get(f"/api/mixtapes/{nd}"),
    bob.post(f"/api/mixtapes/{nd}/rename", json={"name": "mine now"}, headers=BH),
    bob.post(f"/api/mixtapes/{nd}/delete", headers=BH),
    bob.post(f"/api/mixtapes/{nd}/tracks", json={"track_ids": [5]}, headers=BH),
    remove(nd, some_entry, BH, bob),
    bob.post(
        f"/api/mixtapes/{nd}/tracks/{some_entry}/move", json={"direction": "down"}, headers=BH
    ),
]
check(
    "privacy: every way of reaching someone else's mixtape is a 404",
    [a.status_code for a in attempts] == [404] * 6,
    [a.status_code for a in attempts],
)
check(
    "privacy: and nothing changed",
    snapshot
    == raw("SELECT * FROM totr_mixtapes ORDER BY mixtape_id")
    + raw("SELECT * FROM totr_mixtape_tracks ORDER BY entry_id"),
)
check(
    "privacy: a mixtape id that does not exist looks the same",
    bob.get("/api/mixtapes/987654").status_code == 404
    and bob.get("/api/mixtapes/0").status_code == 404,
)

# rename / delete / limits
check(
    "rename: works, and same name in another case is fine for the same mixtape",
    alice.post(f"/api/mixtapes/{mid}/rename", json={"name": "road TRIP"}, headers=AH).status_code
    == 200
    and alice.get(f"/api/mixtapes/{mid}").json()["name"] == "road TRIP",
)
check(
    "rename: onto another of my mixtapes' names is refused; bad names too",
    alice.post(f"/api/mixtapes/{mid}/rename", json={"name": "night drive"}, headers=AH).status_code
    == 409
    and alice.post(f"/api/mixtapes/{mid}/rename", json={"name": ""}, headers=AH).status_code == 422,
)
check(
    "delete: removes the mixtape and its entries, not anyone else's",
    alice.post(f"/api/mixtapes/{mid}/delete", headers=AH).status_code == 200
    and alice.get(f"/api/mixtapes/{mid}").status_code == 404
    and not raw("SELECT 1 FROM totr_mixtape_tracks WHERE mixtape_id = ?", (mid,))
    and len(alice.get(f"/api/mixtapes/{nd}").json()["tracks"]) == 2
    and bob.get("/api/mixtapes").json()["mixtapes"][0]["name"] == "Road Trip",
)
check(
    "delete: a second time is a 404",
    alice.post(f"/api/mixtapes/{mid}/delete", headers=AH).status_code == 404,
)
big_id = alice.post("/api/mixtapes", json={"name": "Big"}, headers=AH).json()["mixtape_id"]
for start in range(1, 1001, 200):
    alice.post(
        f"/api/mixtapes/{big_id}/tracks",
        json={"track_ids": list(range(start, start + 200))},
        headers=AH,
    )
check(
    "limits: a mixtape holds 1000 songs",
    len(raw("SELECT 1 FROM totr_mixtape_tracks WHERE mixtape_id = ?", (big_id,))) == 1000,
)
check(
    "limits: the 1001st is refused and nothing is added",
    alice.post(f"/api/mixtapes/{big_id}/tracks", json={"track_ids": [2000]}, headers=AH).status_code
    == 409
    and len(raw("SELECT 1 FROM totr_mixtape_tracks WHERE mixtape_id = ?", (big_id,))) == 1000,
)
check(
    "create: up to 200 starting songs at once",
    alice.post(
        "/api/mixtapes", json={"name": "Two Hundred", "track_ids": list(range(1, 201))}, headers=AH
    ).json()["added"]
    == 200,
)
for n in range(100):
    bob.post("/api/mixtapes", json={"name": f"bob mix {n}"}, headers=BH)
check(
    "limits: at most 100 mixtapes each",
    bob.post("/api/mixtapes", json={"name": "one too many"}, headers=BH).status_code == 409,
)

# the store keeps its own rules
src = (
    open("mixtapes/store.py", encoding="utf-8").read()
    + open("mixtapes/web.py", encoding="utf-8").read()
)
check(
    "mixtape code builds no SQL from strings",
    not re.search(r'execute\(\s*f["\']', src) and "% (" not in src,
)

# the real music lookup (read-only, real library)
if layout.live_ready():
    mx.lookup_tracks = REAL_LOOKUP
    with db.get_conn() as conn:
        ids = [
            r["track_id"]
            for r in conn.execute(
                "SELECT track_id FROM totr.v_tracks ORDER BY track_id LIMIT 3"
            ).fetchall()
        ]
    real_rows = REAL_LOOKUP(ids + [2_000_000_000])
    check(
        "lookup: real titles and artists come back for real ids, and unknown ids are absent",
        set(real_rows) == set(ids) and all(r["title"] for r in real_rows.values()),
        real_rows,
    )
    check("lookup: no ids, no query", REAL_LOOKUP([]) == {})
else:
    print("skip  the real music lookup  (no database with the TOTR layout is set up)")

# the search endpoint and the page (on the hand-built index until the real library is on the new
# layout)
if not layout.live_ready():
    real = INDEX
    ls.get_index = lambda: INDEX
r = alice.get("/api/search", params={"q": real["artists"][0]["name"] if real["artists"] else "a"})
check(
    "search API: any signed-in person can search; the shape is right",
    r.status_code == 200 and set(r.json()) == {"query", "artists", "albums", "songs", "totals"},
)
check(
    "search API: empty and very long queries are fine",
    alice.get("/api/search").status_code == 200
    and alice.get("/api/search", params={"q": "x" * 3000}).status_code == 200,
)
page = alice.get("/archive").text
check(
    "archive page: search box, mixtapes and the add-to-mixtape control are there, with no inline handlers",
    all(x in page for x in ('id="search"', 'id="mixDialog"', "#/mixtapes", 'id="addNow"'))
    and " onclick=" not in page
    and " onchange=" not in page
    and " oninput=" not in page,
)


# the archive page itself, clicked through in a browser (server replaced by a small in-memory
# stand-in)
sys.path.insert(0, str(pathlib.Path(PF) / "tests"))
import browser_runner as br

edge = br.find_browser()
if not edge:
    print("skip archive page in a browser: no Edge/Chromium found (set EDGE_PATH to run it)")
else:
    try:
        res = br.run(
            edge,
            br.page_from_template(tmp, "archive.html", "archive_stubs.js", "archive_driver.js"),
            tmp,
            budget=30000,
        )
    except Exception as e:
        res = None
        print("note: archive page run failed:", repr(e)[:200])
    check("archive page in a browser: produced a result", res is not None)
    if res is not None:
        st = res["steps"]
        check(
            "archive page in a browser: no script errors anywhere", not res["errors"], res["errors"]
        )
        check(
            "archive page in a browser: the Mixtapes tab sits beside Artists and Compilations",
            st["home"]["tabs"][:3] == ["Artists", "Compilations", "Mixtapes"]
            and st["home"]["cards"] == ["Kanal Vier"],
            st["home"],
        )
        check(
            "archive page in a browser: typing in the search box shows artists, albums and songs, and keeps what was typed",
            st["search"]["hash"] == "#/search/kanal%20vi"
            and st["search"]["headings"] == ['Results for "kanal vi"', "Artists", "Albums", "Songs"]
            and st["search"]["cards"] == ["Kanal Vier", "Cold Wave"]
            and st["search"]["songs"] == ["In Your Eyes", "Dreams"]
            and st["search"]["box"] == "kanal vi",
            st["search"],
        )
        check(
            "archive page in a browser: a search with no hits says so kindly",
            st["searchNothing"]["hash"] == "#/search/zzz"
            and "Nothing found" in st["searchNothing"]["text"][0],
            st["searchNothing"],
        )
        check(
            "archive page in a browser: clearing the box goes back to the artists",
            st["searchCleared"]["hash"] == "#/",
        )
        check(
            "archive page in a browser: an artist shows Albums, no Singles when there are none, and All songs (every song, with its album)",
            st["artist"]["headings"] == ["Kanal Vier", "Albums", "All songs"]
            and st["artist"]["rows"] == ["In Your Eyes", "Dreams"]
            and st["artist"]["artists"] == ["Kanal Vier / Cold Wave", "Kanal Vier / Cold Wave"]
            and st["artist"]["numbers"] == ["1", "2"],
            st["artist"],
        )
        check(
            "archive page in a browser: an album has Play all, Add all to a mixtape and a + on every song",
            st["album"]["rows"] == ["In Your Eyes", "Dreams"]
            and st["album"]["buttons"] == ["> Play all", "+ Add all to a mixtape", "+", "+"],
            st["album"],
        )
        check(
            "archive page in a browser: + opens a chooser that asks for a name when there are no mixtapes",
            st["dialogOpen"]["open"]
            and st["dialogOpen"]["title"] == "Add 1 song to a mixtape"
            and "no mixtapes yet" in st["dialogOpen"]["message"],
            st["dialogOpen"],
        )
        check(
            "archive page in a browser: naming a new mixtape creates it with the song and lists it",
            st["created"]
            == {
                "message": 'Added 1 song to "Road".',
                "buttons": ["Road (1)"],
                "mixes": [["Road", 1]],
            },
            st["created"],
        )
        check(
            "archive page in a browser: a duplicate name shows the server's message",
            st["duplicate"] == "You already have a mixtape with that name.",
            st["duplicate"],
        )
        check("archive page in a browser: the chooser closes", st["closed"] is True)
        check(
            "archive page in a browser: clicking a song plays it",
            st["playing"].startswith("> Dreams"),
            st["playing"],
        )
        check(
            "archive page in a browser: the player bar's + adds the song that is playing",
            st["addNow"]["mixes"] == [["Road", [11, 12]]]
            and 'Added 1 song to "Road"' in st["addNow"]["message"],
            st["addNow"],
        )
        check(
            "archive page in a browser: playing a song sends the listening heartbeat with that song",
            any(
                p["state"] == "playing" and p["source"] == "archive" and p["track_id"] == 12
                for p in st["pings"]
            ),
            st["pings"],
        )
        check(
            "archive page in a browser: pressing + on a song does not start playing it",
            st["plusDoesNotPlay"] == st["playing"],
            st["plusDoesNotPlay"],
        )
        check(
            "archive page in a browser: Add all puts every song of the album in",
            st["addAll"] == [11, 12, 11, 12],
            st["addAll"],
        )
        check(
            "archive page in a browser: the mixtapes page lists mine with song counts",
            st["mixtapes"]["cards"] == ["Road"]
            and st["mixtapes"]["subs"] == ["4 songs"]
            and st["mixtapes"]["heading"] == ["Your mixtapes"],
            st["mixtapes"],
        )
        check(
            "archive page in a browser: a mixtape shows its songs in order with a breadcrumb",
            st["mixtape"]["title"] == ["Road"]
            and st["mixtape"]["rows"] == ["In Your Eyes", "Dreams", "In Your Eyes", "Dreams"]
            and st["mixtape"]["crumbs"] == "Music Archive / Mixtapes / Road",
            st["mixtape"],
        )
        check(
            "archive page in a browser: moving a song later reorders, and the first row cannot go up",
            st["moved"] == {"was": "In Your Eyes", "now": "Dreams", "upDisabledOnFirst": True},
            st["moved"],
        )
        check(
            "archive page in a browser: removing a song takes it out",
            st["removed"] == {"before": 4, "after": 3},
            st["removed"],
        )
        check(
            "archive page in a browser: renaming shows the new name",
            st["renamed"] == ["Road Two"],
            st["renamed"],
        )
        check(
            "archive page in a browser: Play mixtape plays it",
            st["playsMixtape"].startswith("> "),
            st["playsMixtape"],
        )
        check(
            "archive page in a browser: deleting goes back to the list and the mixtape is gone",
            st["deleted"] == {"hash": "#/mixtapes", "cards": [], "mixes": 0},
            st["deleted"],
        )

finish()
