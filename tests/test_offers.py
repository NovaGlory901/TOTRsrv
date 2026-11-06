"""Song offers: offering, accepting, sending files, admin asks, and the checks in between. Throwaway
SQLite files (accounts + the real migration's tables, and the media ontology), real tiny WAV songs.
No network."""

import hashlib
import io
import json
import math
import os
import pathlib
import struct
import sys
import tempfile
import wave

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
os.environ["DB_ENGINE"] = "postgres"
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["APP_SETTINGS"] = str(tmp / "reports" / "app_settings.json")
os.environ.pop("COOKIE_SECURE", None)
import sqlite_layout as layout
from check import check, finish

DB, MEDIA = tmp / "auth.sqlite", tmp / "media.sqlite"
layout.create(DB, layout.ACCOUNTS, layout.SONG_OFFERS)
layout.create(MEDIA, layout.MEDIA)
for k in ("DB_AUTH_USER", "DB_AUTH_PASSWORD"):
    os.environ[k] = "x"
from fastapi.testclient import TestClient

import db
import main
import uploader
from auth import service
from offers import service as osvc


class FK(layout.Conn):
    """The real databases enforce foreign keys (and so the cascades); the stand-in connection needs
    to be told to."""

    def __init__(self, path):
        super().__init__(path)
        self.c.execute("PRAGMA foreign_keys = ON")


service.connect = lambda: FK(DB)
osvc.connect = lambda: FK(DB)
db.get_write_conn = lambda: layout.Conn(MEDIA)
uploader.MUSIC_ROOT = tmp / "music"
raw = lambda sql, params=(): [tuple(d.values()) for d in layout.raw(DB, sql, params)]
media = lambda sql, params=(): [tuple(d.values()) for d in layout.raw(MEDIA, sql, params)]
PW = "correct horse battery staple"
service.create_user("boss", PW, "admin")
service.create_user("rae", PW + "1")
service.create_user("other", PW + "2")
service.create_user("helper", PW + "3", "member", ["upload"])


def signed_in(user, pw):
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c, {"X-CSRF-Token": c.get("/api/auth/me").json()["csrf"]}


anon = TestClient(main.app)
boss, BH = signed_in("boss", PW)
rae, RH = signed_in("rae", PW + "1")
other, OH = signed_in("other", PW + "2")
helper, HH = signed_in("helper", PW + "3")


def wav(freq, seconds=0.2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / 8000)))
                for i in range(int(8000 * seconds))
            )
        )
    return buf.getvalue()


def item(title, data, ext=".wav", artist="Rae Band", album=""):
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "ext": ext,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


S1, S2, S3 = wav(220), wav(330), wav(440)
offer = lambda items, note="", c=rae, h=RH: c.post(
    "/api/songs/offers", json={"note": note, "items": items}, headers=h
)
put = lambda oid, n, data, c=rae, h=RH: c.put(
    f"/api/songs/offers/{oid}/items/{n}",
    content=data,
    headers={**h, "Content-Type": "application/octet-stream"},
)

# who may do what
for method, path in (
    ("GET", "/api/songs/offers"),
    ("POST", "/api/songs/offers"),
    ("GET", "/api/songs/requests"),
    ("GET", "/api/admin/offers"),
    ("GET", "/api/admin/requests"),
    ("PUT", "/api/songs/offers/" + "a" * 32 + "/items/0"),
):
    check(
        f"anonymous: {method} {path} is refused",
        anon.request(method, path, json={} if method == "POST" else None).status_code in (401, 403),
    )
check(
    "a plain member cannot see or answer offers or ask for songs",
    rae.get("/api/admin/offers").status_code == 403
    and rae.get("/api/admin/requests").status_code == 403
    and rae.post("/api/admin/requests", json={"username": "other"}, headers=RH).status_code == 403,
)
check(
    "a person with the Upload permission can",
    helper.get("/api/admin/offers").status_code == 200
    and boss.get("/api/admin/offers").status_code == 200,
)
check(
    "making an offer needs the CSRF token",
    rae.post("/api/songs/offers", json={"items": [item("A", S1)]}).status_code == 403,
)

# an offer is checked
good = item("First", S1)
bad = lambda **o: offer([{**good, **o}]).status_code
check("an empty offer is refused", offer([]).status_code == 422)
check(
    "101 songs are refused",
    offer([{**good, "sha256": f"{i:064x}"} for i in range(101)]).status_code == 422,
)
check("a bad extension is refused", bad(ext=".exe") == 422 and bad(ext=".mp3x") == 422)
check(
    "a bad size is refused (0, huge, not a number)",
    bad(size=0) == 422 and bad(size=300 * 1024 * 1024 + 1) == 422 and bad(size=-5) == 422,
)
check("a bad checksum is refused", bad(sha256="abc") == 422 and bad(sha256="z" * 64) == 422)
check("an empty title is refused", bad(title="   ") == 422)
check(
    "a missing field is refused by the shape check",
    rae.post("/api/songs/offers", json={"items": [{"title": "x"}]}, headers=RH).status_code == 422,
)
check(
    "nothing was stored by the refused ones",
    raw("SELECT count(*) FROM totr_song_offers")[0][0] == 0,
)

big = lambda n, i: {
    "title": f"Big {i}",
    "artist": "A",
    "album": "",
    "ext": ".flac",
    "size": n,
    "sha256": f"{i:064x}",
}
check(
    "an offer of more than 4 GB in total is refused (each song is under the 300 MB limit)",
    offer([big(299 * 1024 * 1024, i) for i in range(1, 16)]).status_code == 422,
)
check(
    "...and 4 GB or less is fine",
    offer([big(299 * 1024 * 1024, i) for i in range(20, 33)], note="13 big ones").status_code
    == 200,
)
raw("DELETE FROM totr_song_offers")

# a good offer
r = offer(
    [
        item("First", S1),
        item("Second", S2),
        item("First again", S1),
        item("Third", S3, album="Live"),
    ],
    note="my saved songs",
)
check(
    "an offer is accepted; the same file twice counts once",
    r.status_code == 200 and r.json()["songs"] == 3,
    r.text,
)
oid = r.json()["offer_id"]
mine = rae.get("/api/songs/offers").json()["offers"]
check(
    "the person sees it with every song 'offered'",
    len(mine) == 1
    and [i["status"] for i in mine[0]["items"]] == ["offered"] * 3
    and mine[0]["status"] == "pending",
)
check("another person sees none of it", other.get("/api/songs/offers").json()["offers"] == [])
check(
    "another person cannot cancel it or send into it",
    other.post(f"/api/songs/offers/{oid}/cancel", headers=OH).status_code == 404
    and put(oid, 0, S1, other, OH).status_code == 404,
)
check("a song nobody accepted yet is refused", put(oid, 0, S1).status_code == 409)
check(
    "a pending limit of 3 stops a flood",
    [offer([item(f"x{i}", wav(500 + i))]).status_code for i in range(4)] == [200, 200, 429, 429],
)

# the person who adds music answers
q = helper.get("/api/admin/offers").json()["offers"]
mineq = [o for o in q if o["offer_id"] == oid][0]
check(
    "they see who offered what",
    mineq["from"] == "rae"
    and mineq["note"] == "my saved songs"
    and [i["title"] for i in mineq["items"]] == ["First", "Second", "Third"],
)
check(
    "answering needs the CSRF token",
    helper.post(f"/api/admin/offers/{oid}/answer", json={"accept": [0]}).status_code == 403,
)
check(
    "songs that are not in the offer are refused",
    helper.post(f"/api/admin/offers/{oid}/answer", json={"accept": [0, 9]}, headers=HH).status_code
    == 422,
)
check(
    "the offer is still waiting after that",
    [o for o in helper.get("/api/admin/offers").json()["offers"] if o["offer_id"] == oid][0][
        "status"
    ]
    == "pending",
)
r = helper.post(f"/api/admin/offers/{oid}/answer", json={"accept": [0, 2]}, headers=HH)
check(
    "they take two of three",
    r.status_code == 200 and r.json() == {"taken": 2, "declined": 1},
    r.text,
)
st = lambda: (
    {i["item_no"]: i["status"] for i in rae.get("/api/songs/offers").json()["offers"][-1]["items"]}
    if False
    else {
        i["item_no"]: i["status"]
        for o in rae.get("/api/songs/offers").json()["offers"]
        if o["offer_id"] == oid
        for i in o["items"]
    }
)
check(
    "the owner sees what was accepted and who decided",
    st() == {0: "accepted", 1: "declined", 2: "accepted"}
    and [o for o in rae.get("/api/songs/offers").json()["offers"] if o["offer_id"] == oid][0][
        "answered_by"
    ]
    == "helper",
)
check(
    "an offer can be answered only once",
    helper.post(f"/api/admin/offers/{oid}/answer", json={"accept": [1]}, headers=HH).status_code
    == 404,
)

# sending the files
import shutil

real_usage = shutil.disk_usage
# nearly full
shutil.disk_usage = lambda p: shutil._ntuple_diskusage(10 * 1024**3, 10 * 1024**3 - 50, 50)
r = put(oid, 0, S1)
check(
    "a nearly full disk refuses the song with a plain sentence, and it can be sent later",
    r.status_code == 507 and "disk space" in r.json()["detail"] and st()[0] == "accepted",
    r.text,
)
shutil.disk_usage = real_usage
check("a declined song is refused", put(oid, 1, S2).status_code == 409)
r = put(oid, 0, S1[:-100])
check(
    "a file that is too short is refused and can be sent again",
    r.status_code == 422 and st()[0] == "failed",
    r.text,
)
r = put(oid, 0, S1 + b"x")
check("a file that is too long is refused", r.status_code == 422)
r = put(oid, 0, bytes(len(S1)))
check(
    "a file of the right size but other content is refused (checksum)",
    r.status_code == 422 and "checksum" in r.json()["detail"] and st()[0] == "failed",
    r.text,
)
check(
    "nothing reached the library yet",
    media(
        "SELECT count(*) FROM media_items WHERE type_id = (SELECT type_id FROM media_item_types WHERE item_type_slug = 'song')"
    )[0][0]
    == 0,
)
r = put(oid, 0, S1)
check("the right file is added", r.status_code == 200 and st()[0] == "uploaded", r.text)
songs = media(
    "SELECT title, metadata FROM media_items WHERE type_id = (SELECT type_id FROM media_item_types WHERE item_type_slug = 'song')"
)
meta = json.loads(songs[0][1]) if songs else {}
check(
    "the song is in the library with the offered title, from 'offer', credited to who offered it",
    len(songs) == 1
    and songs[0][0] == "First"
    and meta.get("source") == "offer"
    and meta.get("offered_by") == "rae",
    songs,
)
check(
    "the file is on disk under the artist",
    any(p.suffix == ".wav" for p in (tmp / "music" / "Rae Band").rglob("*")),
)
check("sending it again is refused", put(oid, 0, S1).status_code == 409)
r = put(oid, 2, S3)
check(
    "the second accepted song goes into its album",
    r.status_code == 200 and (tmp / "music" / "Rae Band" / "Albums" / "Live").exists(),
    r.text,
)
# a file that is not audio, even with a matching checksum
junk = b"this is not a song" * 20
o2 = offer([item("Junk", junk)], c=other, h=OH).json()["offer_id"]
helper.post(f"/api/admin/offers/{o2}/answer", json={"accept": [0]}, headers=HH)
r = put(o2, 0, junk, other, OH)
check(
    "a file that does not read as a song is refused",
    r.status_code == 422 and "does not read as a song" in r.json()["detail"],
    r.text,
)
# a name clash
o3 = offer([item("First", S1, artist="Rae Band")], c=other, h=OH).json()["offer_id"]
helper.post(f"/api/admin/offers/{o3}/answer", json={"accept": [0]}, headers=HH)
r = put(o3, 0, S1, other, OH)
check(
    "a song already in the library under that name is refused, nothing overwritten",
    r.status_code == 422 and "already in the library" in r.json()["detail"],
    r.text,
)

# "this may already be in the library"
import library_search
from spiders.base import squash

fake = lambda title, artists, tid, album="": {
    "key": squash(title),
    "track_id": tid,
    "title": title,
    "artists": artists,
    "album": album,
}
LIB = {
    "songs": [
        fake("Sunrise", "Rae Band", 11, "Dawn"),
        fake("Moonlight", "Someone Else", 12),
        fake("Duet", "Ann, Bob", 13),
    ]
}
library_search.get_index = lambda: LIB
dups = [
    item("SUNRISE!", wav(910), artist="rae  band"),
    item("Moonlight", wav(911), artist="Rae Band"),
    item("Nowhere", wav(912), artist="Rae Band"),
    item("Duet", wav(913), artist="Bob"),
    item("Sunrise", wav(914), artist=""),
]
od = offer(dups, c=other, h=OH).json()["offer_id"]
shown = {
    i["title"] + "|" + i["artist"]: i.get("library")
    for o in helper.get("/api/admin/offers").json()["offers"]
    if o["offer_id"] == od
    for i in o["items"]
}
check(
    "same title and artist (case, spacing, punctuation ignored): 'likely', and which song it is",
    shown["SUNRISE!|rae band"]
    and shown["SUNRISE!|rae band"]["level"] == "likely"
    and shown["SUNRISE!|rae band"]["track_id"] == 11
    and shown["SUNRISE!|rae band"]["album"] == "Dawn",
    shown,
)
check(
    "same title, another artist: only a title match",
    shown["Moonlight|Rae Band"]
    and shown["Moonlight|Rae Band"]["level"] == "title"
    and shown["Moonlight|Rae Band"]["artists"] == "Someone Else",
    shown,
)
check("no match: no flag", shown["Nowhere|Rae Band"] is None)
check(
    "one of several credited artists counts",
    shown["Duet|Bob"] and shown["Duet|Bob"]["level"] == "likely",
    shown,
)
check(
    "no artist given: a title match is as far as it goes",
    shown["Sunrise|"] and shown["Sunrise|"]["level"] == "title",
    shown,
)
check(
    "the person who offered never sees the flags (they are for the one deciding)",
    all(
        "library" not in i
        for o in other.get("/api/songs/offers").json()["offers"]
        for i in o["items"]
    ),
)


def broken():
    raise RuntimeError("the library cannot be read")


library_search.get_index = broken
r = helper.get("/api/admin/offers")
check(
    "if the library cannot be read, offers still list, just without flags",
    r.status_code == 200
    and all("library" not in i for o in r.json()["offers"] for i in o["items"]),
)
library_search.get_index = lambda: LIB
helper.post(f"/api/admin/offers/{od}/answer", json={"accept": [2]}, headers=HH)
check(
    "an answered offer carries no flags",
    all(
        "library" not in i
        for o in helper.get("/api/admin/offers").json()["offers"]
        if o["offer_id"] == od
        for i in o["items"]
    ),
)

# cancel
o4 = offer([item("Cancel me", wav(600))], c=other, h=OH).json()["offer_id"]
check(
    "the owner can cancel",
    other.post(f"/api/songs/offers/{o4}/cancel", headers=OH).status_code == 200
    and all(o["offer_id"] != o4 for o in other.get("/api/songs/offers").json()["offers"]),
)
check(
    "a cancelled offer cannot be answered",
    helper.post(f"/api/admin/offers/{o4}/answer", json={"accept": [0]}, headers=HH).status_code
    == 404,
)

# an admin asks
check(
    "asking a person needs the right to add music",
    rae.post("/api/admin/requests", json={"username": "other"}, headers=RH).status_code == 403,
)
check(
    "asking someone who does not exist, or yourself, is refused",
    helper.post("/api/admin/requests", json={"username": "ghost"}, headers=HH).status_code == 404
    and helper.post("/api/admin/requests", json={"username": "helper"}, headers=HH).status_code
    == 409,
)
r = helper.post(
    "/api/admin/requests",
    json={"username": "RAE", "note": "please share your saved songs"},
    headers=HH,
)
check("a person can be asked (name ignoring case)", r.status_code == 200, r.text)
rid = r.json()["request_id"]
mr = rae.get("/api/songs/requests").json()["requests"]
check(
    "they see who asked and why; nobody else sees it",
    len(mr) == 1
    and mr[0]["asked_by"] == "helper"
    and mr[0]["note"] == "please share your saved songs"
    and other.get("/api/songs/requests").json()["requests"] == [],
)
check(
    "another person cannot answer it",
    other.post(f"/api/songs/requests/{rid}/decline", headers=OH).status_code == 404
    and other.post(
        f"/api/songs/requests/{rid}/accept", json={"items": [item("z", S1)]}, headers=OH
    ).status_code
    == 404,
)
check(
    "the admin sees it waiting",
    [x for x in helper.get("/api/admin/requests").json()["requests"] if x["request_id"] == rid][0][
        "status"
    ]
    == "pending",
)
S4 = wav(700)
r = rae.post(
    f"/api/songs/requests/{rid}/accept", json={"items": [item("Asked for", S4)]}, headers=RH
)
check(
    "saying yes lists the songs and makes an offer that is already accepted",
    r.status_code == 200,
    r.text,
)
ao = r.json()["offer_id"]
view = [o for o in rae.get("/api/songs/offers").json()["offers"] if o["offer_id"] == ao][0]
check(
    "it names the person who asked as the one who approved",
    view["answered_by"] == "helper"
    and view["asked"] is True
    and view["items"][0]["status"] == "accepted",
    view,
)
check(
    "the ask is no longer waiting",
    rae.get("/api/songs/requests").json()["requests"] == []
    and [x for x in helper.get("/api/admin/requests").json()["requests"] if x["request_id"] == rid][
        0
    ]["status"]
    == "accepted",
)
check("the file can now be sent", put(ao, 0, S4).status_code == 200)
r2 = helper.post("/api/admin/requests", json={"username": "other"}, headers=HH).json()["request_id"]
check(
    "a person can decline",
    other.post(f"/api/songs/requests/{r2}/decline", headers=OH).status_code == 200
    and other.get("/api/songs/requests").json()["requests"] == [],
)
r3 = helper.post("/api/admin/requests", json={"username": "other"}, headers=HH).json()["request_id"]
check(
    "the admin can withdraw a request",
    helper.post(f"/api/admin/requests/{r3}/cancel", headers=HH).status_code == 200
    and other.get("/api/songs/requests").json()["requests"] == []
    and helper.post(f"/api/admin/requests/{r3}/cancel", headers=HH).status_code == 404,
)
check(
    "at most 5 waiting asks per person",
    [
        helper.post("/api/admin/requests", json={"username": "other"}, headers=HH).status_code
        for _ in range(6)
    ]
    == [200] * 5 + [429],
)

# the audit trail
log = [
    json.loads(l)
    for l in (tmp / "reports" / "actions.log").read_text(encoding="utf-8").splitlines()
]
events = [(l["event"], l["by"]) for l in log if l["kind"] == "song_offer"]
check(
    "the audit log says who took songs",
    ("offer_answered", "helper") in events and ("offer_made", "rae") in events,
    events,
)
check(
    "...who asked and who agreed",
    ("songs_asked", "helper") in events
    and ("songs_ask_accepted", "rae") in events
    and ("songs_ask_declined", "other") in events,
)
check(
    "...and what was added or refused",
    ("offer_song_added", "rae") in events and ("offer_song_failed", "rae") in events,
)
took = [l for l in log if l["event"] == "offer_answered"][0]
check(
    "a line names the songs taken and from whom",
    took["params"]["taken"] == 2 and took["params"]["from"] == "rae",
    took,
)

# forgetting
raw(
    "UPDATE totr_song_offers SET status = 'answered', answered_at = '2020-01-01T00:00:00+00:00' WHERE offer_id = ?",
    (oid,),
)
helper.get("/api/admin/offers")
check(
    "old answered offers and their songs are purged",
    raw("SELECT count(*) FROM totr_song_offers WHERE offer_id = ?", (oid,))[0][0] == 0
    and raw("SELECT count(*) FROM totr_song_offer_items WHERE offer_id = ?", (oid,))[0][0] == 0,
)
raw("UPDATE totr_song_offer_items SET status = 'receiving' WHERE offer_id = ?", (ao,))
osvc.reset_interrupted()
check(
    "a song cut off by a restart can be sent again",
    raw("SELECT status FROM totr_song_offer_items WHERE offer_id = ?", (ao,))[0][0] == "failed",
)

# deleting a person
service.create_user("gone", PW + "4")
g, GH = signed_in("gone", PW + "4")
go = g.post("/api/songs/offers", json={"items": [item("Bye", wav(800))]}, headers=GH).json()[
    "offer_id"
]
helper.post(f"/api/admin/offers/{go}/answer", json={"accept": [0]}, headers=HH)
gid = raw("SELECT user_id FROM totr_users WHERE username = 'gone'")[0][0]
raw("PRAGMA foreign_keys = ON")
import sqlite3

c = sqlite3.connect(DB)
c.execute("PRAGMA foreign_keys = ON")
c.execute("DELETE FROM totr_users WHERE user_id = ?", (gid,))
c.commit()
c.close()
check(
    "deleting a person removes their offers; who decided is kept as text where it matters",
    raw("SELECT count(*) FROM totr_song_offers WHERE offer_id = ?", (go,))[0][0] == 0,
)

# the database before sql/song_offers.sql
for t in ("totr_song_offer_items", "totr_song_offers", "totr_song_requests"):
    raw(f"DROP TABLE {t}")
r = rae.get("/api/songs/offers")
check(
    "before the upgrade: a clear 503 that says what to run",
    r.status_code == 503 and "song_offers.sql" in r.json()["detail"],
    r.text,
)
check(
    "...for every endpoint",
    helper.get("/api/admin/offers").status_code == 503
    and rae.get("/api/songs/requests").status_code == 503,
)

finish()
