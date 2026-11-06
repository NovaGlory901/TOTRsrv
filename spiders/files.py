"""Does the library on disk match the database?"""

import os
import pathlib

import mutagen

import uploader

from .base import Finding, Snapshot, is_file, propose

NAME = "files"


def check(snap: Snapshot) -> list:
    if not snap.music_root.is_dir():
        return [
            Finding(
                NAME,
                "library_unavailable",
                "error",
                f"The music folder is not available ({snap.music_root}), so the file checks were skipped. "
                "Is the drive mounted?",
            )
        ]

    out = []
    known = set()
    missing = sum(1 for t in snap.tracks if not is_file(t["file_path"]))
    if len(snap.tracks) >= 10 and missing > len(snap.tracks) / 2:
        # Losing most of the library at once is an outage, not 100 individual problems.
        return [
            Finding(
                NAME,
                "library_unavailable",
                "error",
                f"{missing} of {len(snap.tracks)} song files are missing. That looks like a drive or mount "
                "problem rather than individual songs, so no fixes are proposed. Check the storage, then "
                "run the spiders again.",
            )
        ]

    for t in snap.tracks:
        path = t["file_path"]
        known.add(os.path.normpath(path))
        if not is_file(path):
            out.append(
                Finding(
                    NAME,
                    "missing_file",
                    "error",
                    f'"{t["title"]}" points to a file that does not exist: {path}',
                    {"track_id": t["track_id"]},
                    propose(
                        "remove_missing_track",
                        "Remove this song from the library",
                        "Deletes the song's database row. No files are touched. Refused if the file has "
                        "reappeared or the music folder is not mounted.",
                        {"track_id": t["track_id"]},
                        destructive=True,
                    ),
                )
            )
            continue
        try:
            f = mutagen.File(path)
            length = getattr(getattr(f, "info", None), "length", None)
        except Exception:
            f, length = None, None
        if f is None or not length:
            out.append(
                Finding(
                    NAME,
                    "unreadable_audio",
                    "error",
                    f'"{t["title"]}" exists but cannot be read as audio (corrupt or wrong type): {path}',
                    {"track_id": t["track_id"]},
                )
            )

    if snap.music_root.is_dir():
        for dirpath, dirs, filenames in os.walk(snap.music_root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]  # skips .trash
            for name in filenames:
                full = os.path.join(dirpath, name)
                if uploader.is_audio(name) and os.path.normpath(full) not in known:
                    out.append(_orphan(snap, full))

    track_dirs = {}
    for t in snap.tracks:
        if t["album_id"] is not None and is_file(t["file_path"]):
            track_dirs.setdefault(t["album_id"], set()).add(os.path.dirname(t["file_path"]))
    for al in snap.albums:
        cover = al["cover_art_path"]
        if cover and is_file(cover):
            continue
        what = "has no cover art set" if not cover else f"cover file does not exist: {cover}"
        found = next(
            (
                c
                for d in sorted(track_dirs.get(al["album_id"], ()))
                if (c := uploader.find_cover(d))
            ),
            None,
        )
        action = (
            propose(
                "set_cover",
                "Use the cover image next to the songs",
                f"Points this album's cover at {found}. Nothing is moved or deleted.",
                {"album_id": al["album_id"], "path": str(found)},
            )
            if found
            else None
        )
        out.append(
            Finding(
                NAME,
                "missing_cover",
                "warn",
                f'Album "{al["title"]}" {what}',
                {"album_id": al["album_id"]},
                action,
            )
        )
    return out


def _orphan(snap, full):
    tags = snap.tags(full)
    names = uploader.split_artists(tags["artist"])
    title = tags["title"] or pathlib.Path(full).stem
    action = None
    if names:
        album = f' on "{tags["album"]}"' if tags["album"] else " as a loose song"
        action = propose(
            "import_track",
            "Add this song to the library",
            f'Adds "{title}" by {names[0]}{album}, using the tags inside the file. '
            "Nothing on disk changes.",
            {"path": full},
        )
    return Finding(
        NAME,
        "orphan_file",
        "info",
        f"Audio file on disk is not in the database: {full}"
        + ("" if names else " (no artist tag, so use the Upload page)"),
        {"path": full},
        action,
    )
