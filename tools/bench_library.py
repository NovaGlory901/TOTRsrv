"""Builds a throwaway library of N songs (default 100000) in the built-in database (in a temp
folder, never your real one) and times the main pages and search.

    python tools/bench_library.py [songs] [folder to keep the data in, for tools/load_test.py --data]

SQLite by default. Result on a laptop, 100,000 songs: first search 3.5 s (built in the background at
start-up), later searches 50-110 ms, every other page under 300 ms.

PostgreSQL: set BENCH_PG to a JSON object
{"host","port","dbname","admin_user","admin_password","user","password"} for an EMPTY database made
by tools/install_db.py (the real media schema, never a reduced copy); the same library is then built
in it with SQL and the same pages are timed through the server. See docs/performance.md."""

import json
import os
import pathlib
import random
import sqlite3
import sys
import tempfile
import time

PF = pathlib.Path(__file__).resolve().parent.parent
N = int(sys.argv[1]) if len(sys.argv) > 1 else 100000
sys.path.insert(0, str(PF))
os.chdir(PF)
tmp = (
    pathlib.Path(sys.argv[2])
    if len(sys.argv) > 2
    else pathlib.Path(tempfile.mkdtemp(prefix="totr-bench-"))
)
PG = json.loads(os.environ["BENCH_PG"]) if os.environ.get("BENCH_PG") else None
os.environ.update(
    DB_ENGINE="postgres" if PG else "sqlite",
    TOTR_DATA_DIR=str(tmp),
    DB_AUTH_USER="x",
    DB_AUTH_PASSWORD="y",
    MAINT_REPORT=str(tmp / "r" / "m.json"),
    COLLECTOR_DIR=str(tmp / "r"),
    APP_SETTINGS=str(tmp / "r" / "a.json"),
)
if not PG:
    import localdb

    localdb.ensure()
    rnd = random.Random(1)
    words = "dream night echo neon river glass static velvet orbit paper ghost signal winter amber hollow stone ember tide".split()
    name = lambda: (
        " ".join(rnd.choice(words).title() for _ in range(rnd.randrange(1, 4)))
        + f" {rnd.randrange(1000)}"
    )
    k = sqlite3.connect(localdb.db_path())
    tid = lambda s: k.execute(
        "SELECT type_id FROM media_item_types WHERE item_type_slug=?", (s,)
    ).fetchone()[0]
    rt = lambda s: k.execute(
        "SELECT relation_type_id FROM media_relation_types WHERE relation_slug=?", (s,)
    ).fetchone()[0]
    A, B, S_ = tid("artist"), tid("album"), tid("song")
    pb, ta, ab = rt("performed_by"), rt("track_on_album"), rt("album_by")
    t0 = time.time()
    artists = N // 100
    albums_per = 5
    songs_per = N // (artists * albums_per)
    import json

    for a in range(artists):
        aid = k.execute(
            "INSERT INTO media_items (type_id,title,metadata) VALUES (?,?,'{}')", (A, name())
        ).lastrowid
        for al in range(albums_per):
            alid = k.execute(
                "INSERT INTO media_items (type_id,title,metadata) VALUES (?,?,?)",
                (B, name(), json.dumps({"year": 1970 + rnd.randrange(50)})),
            ).lastrowid
            k.execute(
                "INSERT INTO media_item_relations (from_item_id,relation_type_id,to_item_id) VALUES (?,?,?)",
                (alid, ab, aid),
            )
            for s in range(songs_per):
                sid = k.execute(
                    "INSERT INTO media_items (type_id,title,metadata) VALUES (?,?,?)",
                    (
                        S_,
                        name(),
                        json.dumps({"file_path": f"/m/{a}/{al}/{s}.mp3", "track_number": s + 1}),
                    ),
                ).lastrowid
                k.execute(
                    "INSERT INTO media_item_relations (from_item_id,relation_type_id,to_item_id) VALUES (?,?,?)",
                    (sid, ta, alid),
                )
                k.execute(
                    "INSERT INTO media_item_relations (from_item_id,relation_type_id,to_item_id) VALUES (?,?,?)",
                    (sid, pb, aid),
                )
    k.commit()
    k.close()
    print(
        f"built {artists} artists, {artists * albums_per} albums, {artists * albums_per * songs_per} songs in {time.time() - t0:.1f}s; file {localdb.db_path().stat().st_size / 1e6:.0f} MB"
    )
else:
    import psycopg

    os.environ.update(
        DB_HOST=PG["host"],
        DB_PORT=str(PG["port"]),
        DB_NAME=PG["dbname"],
        DB_SSLMODE="disable",
        DB_USER=PG["user"],
        DB_PASSWORD=PG["password"],
        DB_WRITE_USER=PG["user"],
        DB_WRITE_PASSWORD=PG["password"],
        DB_MAINT_USER=PG["user"],
        DB_MAINT_PASSWORD=PG["password"],
        DB_AUTH_USER=PG["user"],
        DB_AUTH_PASSWORD=PG["password"],
        DB_MIXTAPE_USER=PG["user"],
        DB_MIXTAPE_PASSWORD=PG["password"],
        DB_SINGLE_ROLE="true",
    )
    artists = N // 100
    albums_per = 5
    songs_per = N // (artists * albums_per)
    n_albums = artists * albums_per
    n_songs = n_albums * songs_per
    words_sql = (
        "array["
        + ",".join(
            f"'{w}'"
            for w in "dream night echo neon river glass static velvet orbit paper ghost signal winter amber hollow stone ember tide".split()
        )
        + "]"
    )
    nm = f"(initcap(({words_sql})[1+floor(random()*18)::int]) || ' ' || initcap(({words_sql})[1+floor(random()*18)::int]) || ' ' || floor(random()*1000)::int)"
    t0 = time.time()
    k = psycopg.connect(
        host=PG["host"],
        port=PG["port"],
        dbname=PG["dbname"],
        user=PG["admin_user"],
        password=PG["admin_password"],
        autocommit=True,
    )
    ids = lambda q, a=(): k.execute(q, a).fetchone()[0]
    A = ids("SELECT type_id FROM media.item_types WHERE item_type_slug='artist'")
    B = ids("SELECT type_id FROM media.item_types WHERE item_type_slug='album'")
    S_ = ids("SELECT type_id FROM media.item_types WHERE item_type_slug='song'")
    pb = ids("SELECT relation_type_id FROM media.relation_types WHERE relation_slug='performed_by'")
    ta = ids(
        "SELECT relation_type_id FROM media.relation_types WHERE relation_slug='track_on_album'"
    )
    ab = ids("SELECT relation_type_id FROM media.relation_types WHERE relation_slug='album_by'")
    first = ids("SELECT coalesce(max(item_id), 0) FROM media.items")
    assert first == 0, "BENCH_PG must point at an empty library"
    k.execute(
        f"INSERT INTO media.items (type_id,title,metadata) SELECT {A}, {nm}, '{{}}'::jsonb FROM generate_series(1,{artists}) g ORDER BY g"
    )
    k.execute(
        f"INSERT INTO media.items (type_id,title,metadata) SELECT {B}, {nm}, jsonb_build_object('year', 1970+floor(random()*50)::int) FROM generate_series(1,{n_albums}) g ORDER BY g"
    )
    k.execute(
        f"INSERT INTO media.items (type_id,title,metadata) SELECT {S_}, {nm}, jsonb_build_object('file_path', '/m/' || g || '.mp3', 'track_number', 1+(g-1)%{songs_per}) FROM generate_series(1,{n_songs}) g ORDER BY g"
    )
    k.execute(
        f"INSERT INTO media.item_relations (from_item_id,relation_type_id,to_item_id) SELECT {artists}+g, {ab}, ((g-1)/{albums_per})+1 FROM generate_series(1,{n_albums}) g"
    )
    k.execute(
        f"INSERT INTO media.item_relations (from_item_id,relation_type_id,to_item_id) SELECT {artists + n_albums}+g, {ta}, {artists}+((g-1)/{songs_per})+1 FROM generate_series(1,{n_songs}) g"
    )
    k.execute(
        f"INSERT INTO media.item_relations (from_item_id,relation_type_id,to_item_id) SELECT {artists + n_albums}+g, {pb}, ((((g-1)/{songs_per}))/{albums_per})+1 FROM generate_series(1,{n_songs}) g"
    )
    k.execute("ANALYZE")
    print(
        f"built {artists} artists, {n_albums} albums, {n_songs} songs in PostgreSQL in {time.time() - t0:.1f}s; database {ids('SELECT pg_database_size(current_database())') / 1e6:.0f} MB"
    )
    k.close()
from fastapi.testclient import TestClient

import main
from auth import permissions, service

user = {
    "user_id": 1,
    "username": "m",
    "role": "member",
    "permissions": permissions.effective("member", ""),
    "csrf": "t",
    "token_hash": "h",
}
service.configured = lambda: True
service.validate_session = lambda t: user
c = TestClient(main.app)
H = {"cookie": "totr_session=x"}


def timed(label, path, repeat=3):
    best = None
    for _ in range(repeat):
        t = time.time()
        r = c.get(path, headers=H)
        d = time.time() - t
        best = d if best is None else min(best, d)
    print(f"{label:34s} {r.status_code}  {best * 1000:8.0f} ms   {len(r.content) / 1024:7.0f} KB")


timed("search (first: builds the index)", "/api/search?q=dream", 1)
timed("search again", "/api/search?q=dream night")
timed("search no match", "/api/search?q=zzzzqq")
timed("artists list", "/api/artists")
timed("one artist", "/api/artists/1")
timed("artist's albums", "/api/artists/1/albums")
timed("an album", f"/api/albums/{artists + 2}")
timed("album tracks", f"/api/albums/{artists + 2}/tracks")
timed("artist's songs", "/api/artists/1/songs")
