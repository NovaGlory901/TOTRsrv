"""
Carries out a fix a spider proposed, after the user approved it on the Status page.

- Only the action types in HANDLERS exist. The browser sends just a finding id; the type and
  parameters come from the report the spider saved, never from the request.
- Every handler re-checks its preconditions at approval time, because the library may have
  changed since the scan. If anything looks wrong it refuses and changes nothing.
- Database changes run in one transaction on the maintenance role (sql/maintenance_role.sql),
  through catalog.py. TOTR changes
  only items it created and never what something else in the database links to (docs/context/schema-cleanup-2026-10-05.md).
- Files are never deleted: they are moved to MUSIC_ROOT/.trash/<timestamp>/ so they can be put back.
"""

import functools
import os
import pathlib
import shutil
import time

import catalog
import db
import uploader
from collector import http as net
from collector import state as collector_state


class ActionError(Exception):
    """A refusal or failure that is safe to show to the user."""


# The statements live in catalog.py (the music catalog is the existing media ontology). Kept under
# this name so the tests can still syntax-check every one of them against the real schema.
SQL = catalog.SQL


def _inside_library(path) -> bool:
    try:
        pathlib.Path(path).resolve().relative_to(uploader.MUSIC_ROOT.resolve())
        return True
    except (ValueError, OSError):
        return False


def _require_library_mounted():
    """Guards against wiping rows just because the drive is unmounted right now."""
    if not uploader.MUSIC_ROOT.is_dir():
        raise ActionError(
            "The music folder is not available right now (is the drive mounted?), "
            "so nothing was changed."
        )


def _trash(paths) -> str:
    root = uploader.MUSIC_ROOT.resolve()
    dest_root = root / ".trash" / time.strftime("%Y%m%d-%H%M%S")
    moved, failed = 0, 0
    for p in paths:
        try:
            dest = dest_root / pathlib.Path(p).resolve().relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(p, dest)
            moved += 1
        except (OSError, ValueError):
            failed += 1
    msg = f"{moved} file(s) moved to {dest_root} (recoverable)."
    return msg + (f" {failed} could not be moved." if failed else "")


# handlers: (conn, params, after_commit_hooks) -> message


def _refusals(fn):
    """A catalog refusal (not TOTR's to change, something else links to it ...) reaches the user as
    an ActionError."""

    @functools.wraps(fn)
    def wrapper(conn, p, hooks):
        try:
            return fn(conn, p, hooks)
        except catalog.CatalogError as e:
            raise ActionError(str(e))

    return wrapper


def _kept_message(kept: dict) -> str:
    reasons = sorted(set(kept.values()))
    return f" {len(kept)} left alone ({'; '.join(reasons)})." if kept else ""


@_refusals
def remove_missing_track(conn, p, hooks):
    _require_library_mounted()
    track_id = int(p["track_id"])
    row = catalog.song(conn, track_id)
    if row is None:
        raise ActionError("That song is already gone from the library.")
    if os.path.isfile(row["file_path"]):
        raise ActionError("The file exists now, so the song was kept.")
    result = catalog.delete_songs(conn, [track_id])
    if track_id in result["kept"]:
        raise ActionError(f"The song was left in place: {result['kept'][track_id]}.")
    return "Removed the song with the missing file from the library."


@_refusals
def remove_tracks(conn, p, hooks):
    _require_library_mounted()
    keep = int(p["keep"])
    remove = [int(x) for x in p["remove"] if int(x) != keep]
    if catalog.song(conn, keep) is None:
        raise ActionError("The song to keep no longer exists, so nothing was removed.")
    rows = [r for r in (catalog.song(conn, t) for t in remove) if r is not None]
    if not rows:
        raise ActionError("Those duplicates are already gone.")
    result = catalog.delete_songs(conn, [r["track_id"] for r in rows])
    gone = [r for r in rows if r["track_id"] in result["deleted"]]
    if not gone:
        raise ActionError("Nothing was removed." + _kept_message(result["kept"]))

    trash, seen = [], set()
    if p.get("trash_files"):
        for r in gone:
            fp, key = r["file_path"], os.path.normpath(r["file_path"])
            if key in seen or catalog.song_by_path(conn, fp):
                continue  # a song still in the library uses this file: keep it
            seen.add(key)
            if os.path.isfile(fp) and _inside_library(fp):
                trash.append(fp)
    if trash:
        hooks.append(lambda: _trash(trash))
    return f"Removed {len(gone)} duplicate song(s) from the library." + _kept_message(
        result["kept"]
    )


@_refusals
def import_track(conn, p, hooks):
    path = p["path"]
    if not os.path.isfile(path) or not _inside_library(path):
        raise ActionError("That file is not in the music folder any more.")
    if catalog.song_by_path(conn, path):
        raise ActionError("That file is already in the library.")
    tags = uploader.read_tags(pathlib.Path(path))
    names = uploader.split_artists(tags["artist"])
    if not names:
        raise ActionError("The file has no artist tag. Add it with the Upload page instead.")
    uploader.insert_tracks(
        conn,
        names[0],
        tags["album"] or "",
        tags["year"],
        uploader.find_cover(os.path.dirname(path)),
        [(pathlib.Path(path), tags)],
        source="library-import",
    )
    return f'Added "{tags["title"] or pathlib.Path(path).stem}" by {names[0]} to the library.'


@_refusals
def set_cover(conn, p, hooks):
    path = p["path"]
    if not os.path.isfile(path) or not _inside_library(path):
        raise ActionError("That cover image is not in the music folder any more.")
    catalog.set_album_cover(conn, int(p["album_id"]), path)
    return "Cover art set."


@_refusals
def merge_artists(conn, p, hooks):
    keep = int(p["keep"])
    merge = [int(x) for x in p["merge"] if int(x) != keep]
    result = catalog.merge_artists(conn, keep, merge)
    if not result["merged"]:
        raise ActionError(
            "Nothing was merged."
            + (_kept_message(result["kept"]) or " Those artists are already merged.")
        )
    return f"Merged {len(result['merged'])} artist(s) into one." + _kept_message(result["kept"])


@_refusals
def set_title(conn, p, hooks):
    catalog.set_title(conn, int(p["track_id"]), str(p["title"]))
    return "Title updated."


@_refusals
def set_year(conn, p, hooks):
    catalog.set_album_year(conn, int(p["album_id"]), int(p["year"]))
    return "Year set."


@_refusals
def set_track_number(conn, p, hooks):
    catalog.set_song_number(conn, int(p["track_id"]), int(p["track_number"]))
    return "Track number set."


def add_wanted(conn, p, hooks):
    """No database change: remembers missing releases so the Collector searches for them."""
    titles = [str(t) for t in (p.get("titles") or [])]
    added = collector_state.add_wanted(str(p["artist"]), titles or None)
    return (
        f"Added {added} item(s) to the wanted list."
        if added
        else "Those were already on the wanted list."
    ) + " The next music search will look for them."


add_wanted.needs_db = False


JPEG_MAGIC = bytes([0xFF, 0xD8, 0xFF])
PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def _image_ext(head: bytes):
    """The right file extension for a JPEG or PNG, judged by the file's own first bytes. None for
    anything else."""
    if head[:3] == JPEG_MAGIC:
        return ".jpg"
    if head[:8] == PNG_MAGIC:
        return ".png"
    return None


@_refusals
def fetch_cover(conn, p, hooks):
    album_id, url = int(p["album_id"]), str(p["url"])
    row = conn.execute(SQL["sel_album_song_path"], (album_id,)).fetchone()
    if row is None:
        raise ActionError("That album has no songs, so there is nowhere to save a cover.")
    folder = pathlib.Path(row["file_path"]).parent
    if not folder.is_dir() or not _inside_library(folder):
        raise ActionError("The album's folder is not in the music folder right now.")
    part = folder / ".cover.part"
    try:
        net.download(url, part, 8 * 1024 * 1024)
        head = part.read_bytes()[:8]
        ext = _image_ext(head)
        if ext is None:
            raise ActionError("What was downloaded is not an image, so nothing was saved.")
        dest = folder / f"cover{ext}"
        if dest.exists():
            raise ActionError("There is already a cover file in that folder. Nothing was replaced.")
        os.replace(part, dest)
    except net.FetchError as e:
        raise ActionError(f"The cover could not be downloaded: {e}")
    finally:
        part.unlink(missing_ok=True)
    try:
        catalog.set_album_cover(conn, album_id, str(dest))
    except catalog.CatalogError:
        dest.unlink(missing_ok=True)
        raise
    return "Cover art saved."


HANDLERS = {
    f.__name__: f
    for f in (
        remove_missing_track,
        remove_tracks,
        import_track,
        set_cover,
        merge_artists,
        set_title,
        set_year,
        set_track_number,
        add_wanted,
        fetch_cover,
    )
}


def execute(action: dict) -> str:
    fn = HANDLERS.get((action or {}).get("type"))
    if fn is None:
        raise ActionError("Unknown action.")
    needs_db = getattr(fn, "needs_db", True)
    if needs_db and not db.maint_enabled():
        raise ActionError(
            "Fix actions are not set up yet. Run sql/maintenance_role.sql on your PostgreSQL database and add "
            "DB_MAINT_USER and DB_MAINT_PASSWORD to .env."
        )
    hooks = []
    try:
        if needs_db:
            with db.get_maint_conn() as conn:  # commits on success, rolls back on any error
                message = fn(conn, action.get("params") or {}, hooks)
        else:
            message = fn(None, action.get("params") or {}, hooks)
    except (KeyError, TypeError, ValueError):
        raise ActionError("This proposal is malformed. Run the spiders again.")
    return " ".join([message] + [h() for h in hooks])
