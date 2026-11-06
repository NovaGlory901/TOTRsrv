"""
Downloads an approved suggestion into the library and records where it came from.

Checked again at approval time, not trusted from the saved suggestion:
  * the license URL must still classify as free, and match what was suggested
  * every download URL must be on an allowed host
  * there must be room on the drive
Anything that goes wrong removes whatever this run wrote, so a failed add leaves no half-album
behind.
"""

import json
import logging
import os
import pathlib
import shutil

import mutagen

import catalog
import db
import uploader
from spiders import actions as spider_actions

from . import http, licenses, state

log = logging.getLogger("totr.collector")
MIN_FREE_BYTES = 2 * 1024**3  # always leave 2 GB free
MAX_COVER_BYTES = 8 * 1024 * 1024


class AcquireError(Exception):
    """A refusal or failure that is safe to show to the user."""


def _tag_file(path: pathlib.Path, meta: dict):
    """Writes artist/album/title/track into files that have no tags, so they stay identifiable
    anywhere."""
    try:
        f = mutagen.File(path, easy=True)
        if f is None:
            return
        if f.tags is None:
            f.add_tags()
        for key, value in meta.items():
            if value and not f.get(key):
                f[key] = [str(value)]
        f.save()
    except Exception:
        pass  # tags are a nicety; the library row holds the same information


def _playable(path: pathlib.Path) -> bool:
    try:
        f = mutagen.File(path)
        return f is not None and (getattr(f.info, "length", 0) or 0) > 1
    except Exception:
        return False


def acquire(s: dict, actor: str, progress=lambda n: None) -> str:
    lic = licenses.classify(s.get("license_url"))
    if lic is None or lic.code != s.get("license_code"):
        raise AcquireError(
            "This release does not carry a recognised free license, so it was not downloaded."
        )
    tracks = s.get("tracks") or []
    if not tracks or any(not http.host_allowed(t.get("url", "")) for t in tracks):
        raise AcquireError(
            "This suggestion has a download link that is not allowed, so it was not downloaded."
        )
    if not db.maint_enabled():
        raise AcquireError(
            "Adding music needs the maintenance database role. See sql/maintenance_role.sql."
        )
    root = uploader.MUSIC_ROOT
    if not root.is_dir():
        raise AcquireError("The music folder is not available right now.")
    need = int(s.get("size") or 0)
    if shutil.disk_usage(root).free < need * 1.25 + MIN_FREE_BYTES:
        raise AcquireError("Not enough free disk space for this release.")

    artist, title, single = s["artist"], s["title"], bool(s.get("single"))
    folder = (
        root
        / uploader.safe_name(artist)
        / ("Singles" if single else f"Albums/{uploader.safe_name(title)}")
    )
    dests = [folder / uploader.safe_name(pathlib.Path(t["filename"]).name) for t in tracks]
    if any(d.exists() for d in dests) or len({d.name.lower() for d in dests}) != len(dests):
        raise AcquireError(
            "Files with these names already exist in that folder, so nothing was downloaded."
        )
    if any(d.suffix.lower() not in uploader.AUDIO_EXTS for d in dests):
        raise AcquireError("This release has a file type that is not supported.")

    created_folder = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    written, parsed, cover_path = [], [], None
    per_file_cap = max(int(s.get("size") or 0), 50 * 1024 * 1024) * 2
    try:
        for t, dest in zip(tracks, dests):
            part = dest.with_name(dest.name + ".part")
            written.append(part)
            http.download(t["url"], part, per_file_cap, progress, referer=s.get("page_url"))
            os.replace(part, dest)
            written[-1] = dest
            if not _playable(dest):
                raise AcquireError(f'"{t["title"]}" downloaded but is not a playable audio file.')
            _tag_file(
                dest,
                {
                    "title": t["title"],
                    "artist": artist,
                    "album": None if single else title,
                    "tracknumber": t.get("number"),
                    "date": s.get("year"),
                },
            )
            tags = uploader.read_tags(dest)
            tags["title"] = tags["title"] or t["title"]
            tags["artist"] = tags["artist"] or artist
            tags["track_number"] = tags["track_number"] or t.get("number")
            parsed.append((dest, tags))

        cover_url = s.get("cover_url") or ""
        ext = pathlib.PurePosixPath(cover_url.split("?")[0]).suffix.lower()
        if cover_url and not single and ext in uploader.COVER_EXTS and http.host_allowed(cover_url):
            try:
                cover_path = folder / f"cover{ext}"
                written.append(cover_path)
                http.download(cover_url, cover_path, MAX_COVER_BYTES, referer=s.get("page_url"))
            except (http.FetchError, OSError):
                cover_path.unlink(missing_ok=True)
                written.remove(cover_path)
                cover_path = None  # a missing cover is not worth failing the whole add

        credit = f'"{title}" by {artist}, {lic.label}, {s["page_url"]}'
        source_note = folder / f"_source_{s['id']}.json"
        written.append(source_note)
        source_note.write_text(
            json.dumps(
                {
                    "title": title,
                    "artist": artist,
                    "source": s["source"],
                    "page": s["page_url"],
                    "license": lic.label,
                    "license_url": lic.url,
                    "attribution": credit,
                    "retrieved": state.now_iso(),
                    "added_by": actor,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

        with db.get_maint_conn() as conn:
            album_id = uploader.insert_tracks(
                conn,
                artist,
                None if single else title,
                s.get("year"),
                cover_path,
                parsed,
                source="collector",
                extra={"source_url": s["page_url"], "license": lic.label},
            )
    except AcquireError:
        _cleanup(written, folder, created_folder)
        raise
    except (http.FetchError, OSError) as e:
        _cleanup(written, folder, created_folder)
        raise AcquireError(f"The download failed: {e}")
    except Exception:
        _cleanup(written, folder, created_folder)
        raise

    # The library rows are committed now, so the files must stay. Bookkeeping problems from here
    # on are logged, never turned into a failure that would delete files the library points at.
    try:
        state.append_added(
            {
                "id": s["id"],
                "at": state.now_iso(),
                "by": actor,
                "source": s["source"],
                "page_url": s["page_url"],
                "license_label": lic.label,
                "license_url": lic.url,
                "needs_credit": lic.needs_credit,
                "artist": artist,
                "title": title,
                "single": single,
                "album_id": album_id,
                "dir": str(folder),
                "attribution": credit,
                "quality": s.get("quality"),
                "quality_reasons": s.get("quality_reasons"),
                "files": [str(p) for p, _ in parsed]
                + ([str(cover_path)] if cover_path else [])
                + [str(source_note)],
                "tracks": len(parsed),
            }
        )
        state.remove_suggestion(s["id"])
    except OSError:
        log.exception("added %s but could not record it", s["id"])
    return f'Added "{title}" by {artist} ({len(parsed)} song{"s" if len(parsed) != 1 else ""}, {lic.label}).'


def _cleanup(written, folder, created_folder):
    for p in written:
        try:
            pathlib.Path(p).unlink(missing_ok=True)
        except OSError:
            pass
    if created_folder:
        # Remove the folders this add created (album, then "Albums", then the artist), stopping at
        # the first one that still has something in it.
        root = uploader.MUSIC_ROOT
        d = folder
        while d != root and root in d.parents:
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent


def undo(release_id: str, actor: str) -> str:
    record = next(
        (a for a in state.added() if a["id"] == release_id and not a.get("undone_at")), None
    )
    if record is None:
        raise AcquireError("That addition was already undone, or was never recorded.")
    if not db.maint_enabled():
        raise AcquireError(
            "Undo needs the maintenance database role. See sql/maintenance_role.sql."
        )
    if not uploader.MUSIC_ROOT.is_dir():
        raise AcquireError("The music folder is not available right now.")
    paths = [p for p in record["files"]]
    with db.get_maint_conn() as conn:  # an error inside this block rolls everything back
        ids = [r["track_id"] for r in (catalog.song_by_path(conn, p) for p in paths) if r]
        result = catalog.delete_songs(conn, ids)
        if result["kept"]:
            raise AcquireError(
                f"{len(result['kept'])} of these songs are not TOTR's to remove (something else in your database "
                "links to them, or TOTR did not add them), so nothing was undone."
            )
    existing = [p for p in paths if os.path.isfile(p) and spider_actions._inside_library(p)]
    note = spider_actions._trash(existing) if existing else "No files were left to move."
    state.mark_undone(release_id, actor)
    return f'Removed "{record["title"]}" from the library. {note}'
