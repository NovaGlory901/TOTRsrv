import contextlib
import logging
import mimetypes
import os
import pathlib
import platform
import shutil
import tempfile
import threading
import time
import typing
import urllib.parse

import dotenv
import fastapi
import fastapi.responses
import fastapi.staticfiles
import pydantic
from fastapi.concurrency import run_in_threadpool

import app_settings
import catalog
import db
import dbadmin
import dbmove
import discovery
import downloads
import helpers
import library_search
import monitor
import paths
import schemaver
import tlsid
import uploader
import version
from auth import web as auth_web
from collector import (
    scheduler as collector_scheduler,
)
from collector import (
    state as collector_state,
)
from collector import (
    web as collector_web,
)
from mixtapes import web as mixtapes_web
from monitor.traffic import TrafficMiddleware
from offers import service as offers_service
from offers import web as offers_web
from spiders import (
    actions as spider_actions,
)
from spiders import (
    run as spider_run,
)
from spiders import (
    scheduler as spider_scheduler,
)
from spiders import (
    store as spider_store,
)
from updates import web as updates_web
from userdata import web as userdata_web

BASE_DIR = pathlib.Path(__file__).resolve().parent


dotenv.load_dotenv(paths.env_file())

STARTED = time.time()


def use_disk_for_uploads() -> str | None:
    """An upload is written to a temporary file while it arrives, and where that file goes is the
    system's choice: on Arch and other systems /tmp is memory, so a 2 GB upload used 2 GB of RAM
    until it was saved. Use a folder next to the other things the server writes instead
    (reports/upload-tmp), and clear out what a crash left there."""
    folder = paths.home() / "reports" / "upload-tmp"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.iterdir():
            if old.is_file() and time.time() - old.stat().st_mtime > 3600:
                old.unlink(missing_ok=True)
    except OSError:
        return None  # cannot write there: keep the system's choice
    tempfile.tempdir = str(folder)
    return str(folder)


use_disk_for_uploads()
AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
}

log = logging.getLogger("totr")

# Declare these ourselves instead of trusting the operating system's table (Windows can report
# .js as text/plain, and with nosniff the browser then refuses to run the script).
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
# Some systems have no entry for .apk in their MIME table and the installer is then sent as a plain
# download. A phone's browser hands the file to Android's installer by this type, so say it
# outright.
mimetypes.add_type("application/vnd.android.package-archive", ".apk")


@contextlib.asynccontextmanager
async def lifespan(_app):
    level, note = schemaver.check_database()
    if level == "refuse":
        raise RuntimeError(note)  # never run against a database layout this version does not know
    if level == "warn":
        logging.getLogger("totr").warning("database: %s", note)

    def warm_search():
        try:
            library_search.get_index()  # so the first search does not wait for the build
        except Exception:
            pass  # no library yet, or the database is not ready: the first search tries again

    threading.Thread(target=warm_search, name="search-warmup", daemon=True).start()
    offers_service.reset_interrupted()
    threading.Thread(
        target=discovery.sync, name="announce", daemon=True
    ).start()  # tells apps on the network we are here, only if joining is switched on
    # the weekly music search (searching only, never downloading)
    stop = collector_scheduler.start()
    # the nightly spider run (reads only; fixes wait for approval)
    stop_spiders = spider_scheduler.start()
    yield
    discovery.stop()
    stop.set()
    stop_spiders.set()
    monitor.meter.flush()


app = fastapi.FastAPI(
    title="TOTR", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)
try:
    # a server that has no installers yet: without the folder every /static/downloads/ request was a
    # 500 with a traceback in the log
    downloads.DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
# before /static, which would otherwise catch it
app.mount(
    "/static/downloads",
    fastapi.staticfiles.StaticFiles(directory=downloads.DIR, check_dir=False),
    name="downloads",
)
app.mount("/static", fastapi.staticfiles.StaticFiles(directory=BASE_DIR / "static"), name="static")
# The listener app (React, built by TOTRspa's `npm run publish:web`). Files only; the API stays
# behind the sign-in gate.
# The folder is empty until the first publish; a missing one would make every /app/ request a 500.
(BASE_DIR / "static" / "app").mkdir(parents=True, exist_ok=True)
app.mount(
    "/app",
    fastapi.staticfiles.StaticFiles(directory=BASE_DIR / "static" / "app", html=True),
    name="listener-app",
)
auth_web.install(app)  # sign-in gate, CSRF, security headers, account endpoints
# added last = outermost: it counts audio/media bytes as they leave
app.add_middleware(TrafficMiddleware, traffic=monitor.meter)
monitor.errors.install()  # remember recent warnings and errors for the Status tab
app.include_router(collector_web.router)
app.include_router(mixtapes_web.router)
app.include_router(userdata_web.router)
app.include_router(offers_web.router)
app.include_router(updates_web.router)
app.include_router(dbmove.router)
app.include_router(dbadmin.router)


@app.get("/")
def root(request: fastapi.Request):
    return auth_web.render_page(request, "index.html", "home")


@app.get("/archive")
def archive_page(request: fastapi.Request):
    return auth_web.render_page(request, "archive.html", "archive")


@app.get("/music")
def music_redirect():
    return fastapi.responses.RedirectResponse("/archive")


@app.get("/radio")
def radio_page(request: fastapi.Request):
    return auth_web.render_page(request, "radio.html", "radio")


@app.get("/.well-known/totr-ca.pem")
def totr_ca():
    """This server's certificate authority (public part only), for an app that is meeting the server
    for the first time and will ask its person to check the fingerprint."""
    pem = tlsid.ca_pem()
    if pem is None:
        raise fastapi.HTTPException(status_code=404, detail="Not found")
    return fastapi.Response(
        pem, media_type="application/x-pem-file", headers={"Cache-Control": "no-cache"}
    )


@app.get("/admin/updates")
def updates_page(request: fastapi.Request):
    return auth_web.render_page(request, "updates.html", "updates")


@app.get("/admin")
def admin_page(request: fastapi.Request):
    return auth_web.render_page(request, "admin.html", "admin")


@app.get("/status")
def status_redirect():
    return fastapi.responses.RedirectResponse("/admin#status")


@app.get("/discover")
def discover_redirect():
    return fastapi.responses.RedirectResponse("/admin#collector")


@app.get("/upload")
def upload_page(request: fastapi.Request):
    return auth_web.render_page(request, "upload.html", "upload")


@app.get("/api/library/stats")
def library_stats():
    with db.get_conn() as conn:
        return library_counts(conn)


@app.get("/api/me/summary")
def my_summary(request: fastapi.Request):
    """A few counts about the signed-in person, for the one thing they may choose to share: when
    they ask another TOTR server to let them in. The app puts these in the join request only if the
    person ticks the box. Only what this server really records: the day the account was made, how
    many mixtapes they have, and how many songs this library holds (play counts are not kept per
    person, so there are none)."""
    user = auth_web.current_user(request)
    out = {}
    try:
        made = auth_web.service.account_overview(user)["created_at"]
        out["since"] = str(made.isoformat() if hasattr(made, "isoformat") else made)[:10]
    except Exception:
        pass
    try:
        with mixtapes_web.session() as conn:
            out["mixtapes"] = mixtapes_web.store.count_mixtapes(conn, user["user_id"])
    except Exception:
        pass  # the mixtape feature is not set up here: simply not reported
    try:
        with db.get_conn() as conn:
            out["songs"] = library_counts(conn)["tracks"]
    except Exception:
        pass
    return out


@app.get("/health")
def health_public():
    """For uptime monitors. Deliberately tiny and cheap: no file paths, no counts, no file scan."""
    try:
        with db.get_conn() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}
    except Exception:
        return fastapi.responses.JSONResponse({"status": "degraded"}, status_code=503)


@app.get("/api/health")
def health():
    """Full detail, admins only (enforced by the gate). Includes file paths."""
    checks = {}

    t0 = time.perf_counter()
    try:
        with db.get_conn() as conn:
            counts = library_counts(conn)
            paths = [
                r["file_path"]
                for r in conn.execute("SELECT file_path FROM totr.v_tracks").fetchall()
            ]
        checks["database"] = {
            "ok": True,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "counts": counts,
        }
    except Exception as e:
        checks["database"] = {"ok": False, "error": type(e).__name__}
        paths = []

    missing = [p for p in paths if not os.path.isfile(p)]
    checks["audio_files"] = {
        "ok": not missing,
        "checked": len(paths),
        "missing": len(missing),
        "missing_examples": missing[:5],
    }

    root = uploader.MUSIC_ROOT
    checks["music_root"] = {
        "ok": root.is_dir(),
        "path": str(root),
        "writable": os.access(root, os.W_OK) if root.is_dir() else False,
    }

    # Informational: uploads are optional, so these never mark the server unhealthy.
    checks["uploads"] = {"ok": True, "db_write_role": db.write_enabled()}
    checks["accounts"] = {"ok": db.auth_enabled(), "configured": db.auth_enabled()}

    checks["maintenance"] = {
        "ok": True,
        "actions_enabled": db.maint_enabled(),
        "last_scan": (spider_store.read_report() or {}).get("generated_at"),
    }

    checks["radio_media"] = {
        "ok": True,
        "path": str(MEDIA_ROOT),
        "exists": MEDIA_ROOT.is_dir(),
        "files": sum(
            1 for p in MEDIA_ROOT.rglob("*") if p.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS
        )
        if MEDIA_ROOT.is_dir()
        else 0,
    }

    healthy = all(c["ok"] for c in checks.values())
    body = {
        "status": "ok" if healthy else "degraded",
        "version": version.SERVER_VERSION,
        "uptime_s": int(time.time() - STARTED),
        "host": platform.node(),
        "checks": checks,
    }
    return fastapi.responses.JSONResponse(body, status_code=200 if healthy else 503)


@app.get("/api/health/live")
def health_live():
    """The Status tab's fast-moving numbers (admin or `status` permission): listeners, data
    streamed, system, library, jobs, problems."""

    def counts():
        with db.get_conn() as conn:
            return library_counts(conn)

    def paths():
        with db.get_conn() as conn:
            return [
                r["file_path"]
                for r in conn.execute("SELECT file_path FROM totr.v_tracks").fetchall()
            ]

    places = {"music library": uploader.MUSIC_ROOT, "dream folder": MEDIA_ROOT, "app": BASE_DIR}
    return monitor.live_snapshot(mixtapes_web.lookup_tracks, places, counts, paths)


class PingBody(pydantic.BaseModel):
    track_id: int | None = pydantic.Field(default=None, ge=1, le=2_147_483_647)
    state: typing.Literal["playing", "paused", "stopped"]
    source: typing.Literal["radio", "archive"]


@app.post("/api/listening/ping")
def listening_ping(body: PingBody, request: fastapi.Request):
    """The pages say "still playing this song" every ~30 seconds. Any signed-in person; the Status
    tab shows it to admins only."""
    user = auth_web.current_user(request)
    monitor.audience.ping(
        user["token_hash"],
        user["username"],
        body.track_id,
        body.state,
        body.source,
        auth_web.client_ip(request),
        request.headers.get("user-agent", "")[:200],
    )
    return {"ok": True}


# constants, never user input
LIBRARY_VIEWS = {
    "artists": "totr.v_artists",
    "albums": "totr.v_albums",
    "tracks": "totr.v_tracks",
}


def library_counts(conn) -> dict:
    return {
        name: conn.execute(f"SELECT count(*) AS n FROM {view}").fetchone()["n"]
        for name, view in LIBRARY_VIEWS.items()
    }


def track_order() -> str:
    """Chronological by release date (songs without one last), then insertion order."""
    return "t.released NULLS LAST, t.track_id"


@app.get("/api/search")
def search_library(q: str = ""):
    """Artists, albums and songs matching what was typed, grouped by type (see library_search.py)."""
    return library_search.search(library_search.get_index(), q)


@app.get("/api/artists")
def list_artists():
    """Artists credited on at least one track. Album 'owners' with no credited tracks (e.g.
    MixTapes) are hidden."""
    with db.get_conn() as conn:
        return conn.execute(
            """
            WITH cover AS (SELECT ta2.artist_id, min(t2.album_id) AS album_id      -- the first album with a cover, for every artist in one pass (a per-artist subquery walked all their tracks)
                             FROM totr.v_tracks t2 JOIN totr.v_track_artists ta2 ON ta2.track_id = t2.track_id
                             JOIN totr.v_albums al2 ON al2.album_id = t2.album_id
                            WHERE al2.cover_art_path IS NOT NULL
                            GROUP BY ta2.artist_id)
            SELECT ar.artist_id, ar.name,
                   count(DISTINCT ta.track_id) AS track_count,
                   cv.album_id AS cover_album_id
            FROM totr.v_artists ar
            JOIN totr.v_track_artists ta ON ta.artist_id = ar.artist_id
            LEFT JOIN cover cv ON cv.artist_id = ar.artist_id
            GROUP BY ar.artist_id, ar.name, cv.album_id
            ORDER BY ar.name
            """
        ).fetchall()


@app.get("/api/artists/{artist_id}")
def get_artist(artist_id: int):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT artist_id, name FROM totr.v_artists WHERE artist_id = %s", (artist_id,)
        ).fetchone()
    if row is None:
        raise fastapi.HTTPException(status_code=404, detail="Artist not found")
    return row


@app.get("/api/artists/{artist_id}/albums")
def list_albums(artist_id: int):
    """Every album holding at least one track credited to this artist (includes compilations)."""
    with db.get_conn() as conn:
        # Starts from this artist's own tracks (the albums come second): the other way round SQLite
        # looked at every album in the library.
        return conn.execute(
            """
            WITH mine AS (
                SELECT t.album_id, count(DISTINCT t.track_id) AS n
                FROM totr.v_track_artists ta JOIN totr.v_tracks t ON t.track_id = ta.track_id
                WHERE ta.artist_id = %s GROUP BY t.album_id
            )
            SELECT al.album_id, al.title, al.year, owner.name AS owner, mine.n AS their_tracks,
                   (SELECT count(*) FROM totr.v_tracks tt WHERE tt.album_id = al.album_id) AS total_tracks
            FROM mine
            JOIN totr.v_albums al ON al.album_id = mine.album_id
            JOIN totr.v_artists owner ON owner.artist_id = al.artist_id
            ORDER BY al.title
            """,
            (artist_id,),
        ).fetchall()


@app.get("/api/artists/{artist_id}/discography")
def list_discography(artist_id: int):
    """Loose songs (no album) credited to this artist, oldest first."""
    with db.get_conn() as conn:
        return conn.execute(
            f"""
            SELECT t.track_id, t.track_number, t.title,
                   string_agg(DISTINCT ar.name, ', ') AS artists
            FROM totr.v_tracks t
            JOIN totr.v_track_artists me ON me.track_id = t.track_id AND me.artist_id = %s
            JOIN totr.v_track_artists ta ON ta.track_id = t.track_id
            JOIN totr.v_artists ar ON ar.artist_id = ta.artist_id
            WHERE t.album_id IS NULL
            GROUP BY t.track_id, t.track_number, t.title, t.released   -- a view has no primary key to infer the rest from
            ORDER BY {track_order()}
            """,
            (artist_id,),
        ).fetchall()


@app.get("/api/artists/{artist_id}/songs")
def list_artist_songs(artist_id: int):
    """Every song credited to this artist (on albums or loose), A to Z, with the album it is on."""
    with db.get_conn() as conn:
        return conn.execute(
            """
            SELECT t.track_id, t.title, al.title AS album,
                   string_agg(DISTINCT ar.name, ', ') AS artists
            FROM totr.v_tracks t
            JOIN totr.v_track_artists me ON me.track_id = t.track_id AND me.artist_id = %s
            JOIN totr.v_track_artists ta ON ta.track_id = t.track_id
            JOIN totr.v_artists ar ON ar.artist_id = ta.artist_id
            LEFT JOIN totr.v_albums al ON al.album_id = t.album_id
            GROUP BY t.track_id, t.title, al.title
            ORDER BY lower(t.title), t.track_id
            """,
            (artist_id,),
        ).fetchall()


@app.get("/api/compilations")
def list_compilations():
    """Albums credited to several artists (mixtapes, samplers)."""
    with db.get_conn() as conn:
        return conn.execute(
            """
            SELECT al.album_id, al.title, al.year, owner.name AS owner,
                   count(DISTINCT t.track_id) AS total_tracks,
                   count(DISTINCT ta.artist_id) AS artist_count
            FROM totr.v_albums al
            JOIN totr.v_artists owner ON owner.artist_id = al.artist_id
            JOIN totr.v_tracks t ON t.album_id = al.album_id
            JOIN totr.v_track_artists ta ON ta.track_id = t.track_id
            GROUP BY al.album_id, al.title, al.year, owner.name
            HAVING count(DISTINCT ta.artist_id) >= 4
            ORDER BY al.title
            """
        ).fetchall()


@app.get("/api/albums/{album_id}")
def get_album(album_id: int):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT al.album_id, al.title, al.year, owner.name AS owner "
            "FROM totr.v_albums al JOIN totr.v_artists owner ON owner.artist_id = al.artist_id WHERE al.album_id = %s",
            (album_id,),
        ).fetchone()
    if row is None:
        raise fastapi.HTTPException(status_code=404, detail="Album not found")
    # credit: set only for albums the collector added
    return {
        **row,
        "credit": collector_state.credit_for_album(album_id),
    }


MEDIA_ROOT = pathlib.Path(os.environ.get("MEDIA_ROOT") or paths.home() / "media")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTS = {".mp4", ".webm"}


@app.get("/api/radio/next")
def radio_next(exclude: str = ""):
    """One random track for the radio, skipping recently played ids."""
    ids = [
        int(x)
        for x in exclude.split(",")
        if x.strip().isascii() and x.strip().isdigit() and len(x.strip()) <= 9
    ][:200]
    sql = """
        SELECT t.track_id, t.title, t.album_id, string_agg(DISTINCT ar.name, ', ') AS artists
        FROM totr.v_tracks t
        JOIN totr.v_track_artists ta ON ta.track_id = t.track_id
        JOIN totr.v_artists ar ON ar.artist_id = ta.artist_id
        WHERE NOT (t.track_id = ANY(%s::int[]))
        GROUP BY t.track_id, t.title, t.album_id
        ORDER BY random() LIMIT 1
    """
    with db.get_conn() as conn:
        row = conn.execute(sql, (ids,)).fetchone() or conn.execute(sql, ([],)).fetchone()
    if row is None:
        raise fastapi.HTTPException(status_code=404, detail="No tracks in the library yet")
    return row


@app.get("/api/radio/media")
def radio_media():
    """Pictures and videos for the radio collage: album covers plus anything dropped in MEDIA_ROOT."""
    items = []
    with db.get_conn() as conn:
        for r in conn.execute(
            "SELECT album_id, cover_art_path FROM totr.v_albums WHERE cover_art_path IS NOT NULL"
        ):
            if os.path.isfile(r["cover_art_path"]):
                items.append({"type": "image", "url": f"/api/albums/{r['album_id']}/cover"})
    if MEDIA_ROOT.is_dir():
        for p in MEDIA_ROOT.rglob("*"):
            ext = p.suffix.lower()
            if p.is_file() and (ext in IMAGE_EXTS or ext in VIDEO_EXTS):
                rel = urllib.parse.quote(p.relative_to(MEDIA_ROOT).as_posix())
                items.append(
                    {
                        "type": "video" if ext in VIDEO_EXTS else "image",
                        "url": f"/api/radio/media/file/{rel}",
                    }
                )
            if len(items) >= 500:
                break
    return items


@app.get("/api/radio/media/file/{rel:path}")
def radio_media_file(rel: str):
    root = MEDIA_ROOT.resolve()
    try:
        target = (MEDIA_ROOT / rel).resolve()
    except (ValueError, OSError):  # a NUL byte or a name the system refuses
        raise fastapi.HTTPException(status_code=404, detail="Not found")
    if (
        root not in target.parents
        or target.suffix.lower() not in IMAGE_EXTS | VIDEO_EXTS
        or not target.is_file()
    ):
        raise fastapi.HTTPException(status_code=404, detail="Not found")
    return fastapi.responses.FileResponse(target)


@app.get("/api/albums/{album_id}/tracks")
def list_tracks(album_id: int):
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT t.track_id, t.track_number, t.title,
                   string_agg(ar.name, ', ') AS artists
            FROM totr.v_tracks t
            JOIN totr.v_track_artists ta ON ta.track_id = t.track_id
            JOIN totr.v_artists ar ON ar.artist_id = ta.artist_id
            WHERE t.album_id = %s
            GROUP BY t.track_id, t.track_number, t.title
            ORDER BY t.track_number
            """,
            (album_id,),
        ).fetchall()
    return rows


@app.get("/api/tracks/{track_id}/audio")
def stream_track(track_id: int):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT file_path FROM totr.v_tracks WHERE track_id = %s",
            (track_id,),
        ).fetchone()
    if row is None:
        raise fastapi.HTTPException(status_code=404, detail="Track not found")
    path = pathlib.Path(row["file_path"])
    if not path.is_file():
        raise fastapi.HTTPException(status_code=404, detail="Audio file missing on disk")
    return fastapi.responses.FileResponse(
        path, media_type=AUDIO_TYPES.get(path.suffix.lower(), "audio/mpeg")
    )


@app.get("/api/albums/{album_id}/cover")
def album_cover(album_id: int):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT cover_art_path FROM totr.v_albums WHERE album_id = %s", (album_id,)
        ).fetchone()
    if row is None or not row["cover_art_path"] or not os.path.isfile(row["cover_art_path"]):
        raise fastapi.HTTPException(status_code=404, detail="No cover art")
    return fastapi.responses.FileResponse(row["cover_art_path"])


def require_upload_ready():
    if not db.write_enabled():
        raise fastapi.HTTPException(
            status_code=503,
            detail="Uploads not configured: set DB_WRITE_USER and DB_WRITE_PASSWORD in .env",
        )


@app.post("/api/upload/inspect")
async def inspect_upload(files: list[fastapi.UploadFile] = fastapi.File(...)):
    """Reads tags from the chosen files so the form can prefill artist/album/year. Saves nothing."""
    tags = []
    for f in files:
        if not uploader.is_audio(f.filename):
            continue
        with tempfile.NamedTemporaryFile(suffix=pathlib.Path(f.filename).suffix) as tmp:
            f.file.seek(0)
            await run_in_threadpool(shutil.copyfileobj, f.file, tmp, 1 << 20)
            tmp.flush()
            tags.append(await run_in_threadpool(uploader.read_tags, pathlib.Path(tmp.name)))

    def pick(k):
        return next((t[k] for t in tags if t[k]), None)

    return {
        "artist": pick("artist"),
        "album": pick("album"),
        "year": pick("year"),
        "tracks": len(tags),
    }


@app.post("/api/upload")
async def upload(
    artist: str = fastapi.Form(..., max_length=300),
    album: str = fastapi.Form("", max_length=300),
    year: int | None = fastapi.Form(None),
    files: list[fastapi.UploadFile] = fastapi.File(...),
    cover: fastapi.UploadFile | None = fastapi.File(None),
):
    """A blank album means loose songs: they land in the artist's Discography instead of an album."""
    require_upload_ready()
    artist, album = artist.strip(), album.strip()
    if not artist:
        raise fastapi.HTTPException(status_code=422, detail="Artist is required")
    # the open files, not their bytes: a 2 GB upload used to be read into memory first
    audio = [(f.filename, f.file) for f in files if uploader.is_audio(f.filename)]
    if not audio:
        raise fastapi.HTTPException(
            status_code=422, detail="No supported audio files (mp3, flac, ogg, opus, m4a, wav)"
        )
    cover_data = (cover.filename, cover.file) if cover and cover.filename else None
    try:
        # off the event loop: writing gigabytes must not stop every other request
        return await run_in_threadpool(
            uploader.save_album, artist, album, year, audio, cover_data, verify_audio=True
        )
    except uploader.NotAudio as e:
        raise fastapi.HTTPException(status_code=422, detail=str(e))
    except (FileExistsError, catalog.CatalogError) as e:
        raise fastapi.HTTPException(status_code=409, detail=str(e))


class FindingRequest(pydantic.BaseModel):
    finding_id: str = pydantic.Field(max_length=80)


@app.get("/api/maintenance")
def maintenance_report():
    report = spider_store.read_report() or {
        "generated_at": None,
        "findings": [],
        "severity": {},
        "kinds": {},
    }
    return {
        **report,
        "running": spider_run.is_running(),
        "progress": spider_run.progress["text"],
        "actions_enabled": db.maint_enabled(),
        "online_enabled": True,
        "online_source": "MusicBrainz" if collector_contact() else "Deezer",
        "schedule": spider_scheduler.load_config(),
    }


@app.post("/api/maintenance/schedule")
def maintenance_schedule(raw: dict = fastapi.Body(...)):
    try:
        cfg = spider_scheduler.clean_config(raw)
    except ValueError as e:
        raise fastapi.HTTPException(status_code=422, detail=str(e))
    spider_scheduler.save_config(cfg)
    return cfg


def collector_contact() -> str:
    return app_settings.contact()


# site settings (non-secret, edited on the Account page by admins; /api/admin is admin-only)


def _settings_view() -> dict:
    contact, source = app_settings.get_contact()
    return {"contact": contact, "contact_source": source}


@app.get("/api/admin/settings")
def get_site_settings():
    return _settings_view()


@app.post("/api/admin/settings")
def save_site_settings(raw: dict = fastapi.Body(...)):
    try:
        app_settings.set_contact(raw.get("contact", ""))
    except ValueError as e:
        raise fastapi.HTTPException(status_code=422, detail=str(e))
    return _settings_view()


@app.get("/api/downloads")
def get_downloads():
    """The newest installer of each app. Public on purpose: a new device needs the app before it can
    sign in (the files themselves are public too)."""
    pem = tlsid.ca_pem()
    # the certificate step of the install instructions, only when this server publishes its
    # authority (a server on a certificate every device already trusts has none)
    return {
        "apps": downloads.latest(),
        "certificate": {"url": "/.well-known/totr-ca.pem", "fingerprint": tlsid.fingerprint(pem)}
        if pem
        else None,
    }


@app.get("/download")
def download_page(request: fastapi.Request):
    return auth_web.render_page(request, "download.html", "download")


@app.post("/api/maintenance/run")
def maintenance_run(only: str = ""):
    """Scan in the background (read-only). `only` picks spiders, e.g. `enrich` for the online lookup.
    The page polls /api/maintenance until it finishes."""
    names = {n for n in only.split(",") if n} or None
    if names and not names <= set(spider_run.SPIDERS):
        return fastapi.responses.JSONResponse({"detail": "Unknown spider."}, status_code=422)
    if spider_run.is_running():
        return {"started": False, "detail": "Already running"}
    threading.Thread(target=lambda: spider_run.run(names), daemon=True).start()
    return {"started": True}


@app.post("/api/maintenance/approve")
def maintenance_approve(req: FindingRequest, request: fastapi.Request):
    actor = auth_web.current_user(request)["username"]
    finding = spider_store.find(req.finding_id)
    if finding is None:
        raise fastapi.HTTPException(
            status_code=404, detail="That finding is gone. Run the spiders again."
        )
    if not finding.get("action"):
        raise fastapi.HTTPException(status_code=400, detail="This finding has no proposed fix")
    try:
        message = spider_actions.execute(finding["action"])
    except spider_actions.ActionError as e:
        spider_store.audit("refused", finding, str(e), actor)
        raise fastapi.HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        log.exception("maintenance action failed")
        spider_store.audit("failed", finding, f"{type(e).__name__}: {e}", actor)
        raise fastapi.HTTPException(
            status_code=500, detail="The fix failed and nothing was changed. See the server log."
        )
    spider_store.audit("approved", finding, message, actor)
    spider_store.remove_finding(req.finding_id)
    return {"ok": True, "message": message}


@app.post("/api/maintenance/dismiss")
def maintenance_dismiss(req: FindingRequest, request: fastapi.Request):
    actor = auth_web.current_user(request)["username"]
    finding = spider_store.find(req.finding_id)
    if finding is None:
        raise fastapi.HTTPException(
            status_code=404, detail="That finding is gone. Run the spiders again."
        )
    # Hidden only until the next scan of its spider: if the problem is still there, it comes back.
    spider_store.audit("dismissed", finding, "hidden until the next scan", actor)
    spider_store.remove_finding(req.finding_id)
    return {"ok": True}


@app.post("/api/maintenance/ignore")
def maintenance_ignore(req: FindingRequest, request: fastapi.Request):
    actor = auth_web.current_user(request)["username"]
    finding = spider_store.find(req.finding_id)
    if finding is None:
        raise fastapi.HTTPException(
            status_code=404, detail="That finding is gone. Run the spiders again."
        )
    spider_store.ignore(req.finding_id)
    spider_store.audit("ignored", finding, "ignored permanently", actor)
    spider_store.remove_finding(req.finding_id)
    return {"ok": True}


@app.post("/api/maintenance/unignore-all")
def maintenance_unignore_all(request: fastapi.Request):
    actor = auth_web.current_user(request)["username"]
    n = spider_store.unignore_all()
    spider_store.audit(
        "unignored",
        {"kind": "all", "message": f"{n} ignored finding(s) will show again"},
        "cleared",
        actor,
    )
    return {"ok": True, "cleared": n}


if __name__ == "__main__":
    import os

    import tlsconf

    try:
        host_ip, port_num = helpers.listen_address(os.environ)
    except ValueError as e:
        raise SystemExit(f"Listen settings: {e}")
    try:
        tls = tlsconf.settings(os.environ)
    except tlsconf.TlsError as e:
        raise SystemExit(f"HTTPS settings: {e}")

    shown = helpers.get_local_ip() if host_ip in ("0.0.0.0", "::") else host_ip
    print("\n=================================================================")
    print("|        TOTR Server is launching on your local network!          |")
    if tls:
        print(f"|  Others on your Wi-Fi can join at: https://{shown}:{tls['port']}/")
    if not tls or tls["http"]:
        print(f"|  Others on your Wi-Fi can join at: http://{shown}:{port_num}/ |")
    print("===================================================================\n")

    tlsconf.run("main:app", host_ip, port_num, tls)
