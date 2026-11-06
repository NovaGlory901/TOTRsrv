import os
import pathlib
import re
import shutil

import mutagen

import catalog
import db
import paths

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav"}
COVER_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MUSIC_ROOT = pathlib.Path(os.environ.get("MUSIC_ROOT") or paths.home() / "music")


# file systems allow 255 bytes in one name; a longer artist, album or file name made the upload fail
# with a 500 after the files were half written
MAX_NAME_BYTES = 240


def safe_name(s: str, keep_ending: str = "") -> str:
    """Folder/file-name safe: strips path separators and characters Windows/Linux dislike, and cuts
    the name to what a file system accepts (`keep_ending`, such as ".mp3", is kept when the name has
    to be cut)."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s).strip(" .")
    room = MAX_NAME_BYTES - len(keep_ending.encode("utf-8"))
    if len(s.encode("utf-8")) > MAX_NAME_BYTES:
        stem = s[: -len(keep_ending)] if keep_ending and s.endswith(keep_ending) else s
        s = stem.encode("utf-8")[:room].decode("utf-8", "ignore").strip(" .") + keep_ending
    return s or "Unknown"


class NotAudio(ValueError):
    """A file named like a song that does not read as one."""


# a title, artist or album longer than this is not a name; a file could otherwise carry megabytes of
# it into the library
MAX_TAG_TEXT = 500


def read_tags(path: pathlib.Path) -> dict:
    out = {"title": None, "artist": None, "album": None, "year": None, "track_number": None}
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        return out
    if not f or not f.tags:
        return out

    def first(key):
        v = f.tags.get(key)
        return str(v[0]).strip()[:MAX_TAG_TEXT] or None if v else None

    out["title"] = first("title")
    out["artist"] = first("artist")
    out["album"] = first("album")
    m = re.match(r"\d{4}", first("date") or "")
    out["year"] = int(m.group()) if m else None
    # a song number is 1 to 4 digits; "99999999999999999999" would not fit a database integer
    m = re.match(r"\d{1,4}(?!\d)", first("tracknumber") or "")
    out["track_number"] = int(m.group()) if m else None
    return out


def is_audio(filename: str) -> bool:
    return pathlib.Path(filename).suffix.lower() in AUDIO_EXTS


def write_file(dest: pathlib.Path, data):
    """Puts `data` in `dest`: bytes, or an open file (an upload that the web framework keeps on
    disk), copied a megabyte at a time so a big upload is never held in memory."""
    if isinstance(data, (bytes, bytearray)):
        dest.write_bytes(data)
        return
    data.seek(0)
    with open(dest, "wb") as out:
        shutil.copyfileobj(data, out, 1 << 20)


def reads_as_song(path: pathlib.Path) -> bool:
    """Does the tag reader recognise this file as audio? (The same test an offered song has to pass.)"""
    try:
        probe = mutagen.File(path)
    except Exception:
        return False
    return probe is not None and bool(getattr(probe, "info", None))


def save_album(
    artist_name, album_title, year, files, cover, source="upload", extra=None, verify_audio=False
):
    """
    files: list of (filename, bytes or open file). cover: (filename, bytes or open file) or None.
    Files are written to MUSIC_ROOT/<artist>/Albums/<album>/ and items inserted in one
    transaction. Existing artist and album items are reused.
    A blank album_title means loose songs: they go to MUSIC_ROOT/<artist>/Singles/ with
    album_id NULL, which the site shows under the artist's Discography.
    Returns a summary dict.
    """
    loose = not album_title
    album_dir = (
        MUSIC_ROOT
        / safe_name(artist_name)
        / ("Singles" if loose else f"Albums/{safe_name(album_title)}")
    )

    dests = [
        album_dir / safe_name(pathlib.Path(filename).name, pathlib.Path(filename).suffix)
        for filename, _ in files
    ]
    # on disk already, or two files in this upload with the same name
    clashes = [d.name for d in dests if d.exists()] + [d.name for d in dests if dests.count(d) > 1]
    if clashes:
        raise FileExistsError(f"{clashes[0]} already exists in that folder")
    # the folders this upload creates, so a refusal can take them away again
    made_dirs = [
        d for d in (album_dir, *album_dir.parents) if MUSIC_ROOT in d.parents and not d.exists()
    ]
    album_dir.mkdir(parents=True, exist_ok=True)

    written = []
    parsed = []
    try:
        for dest, (_, data) in zip(dests, files):
            write_file(dest, data)
            written.append(dest)
            if verify_audio and not reads_as_song(dest):
                raise NotAudio(f"{dest.name} does not read as a song.")
            parsed.append((dest, read_tags(dest)))

        cover_path = None
        if cover and not loose:
            ext = pathlib.Path(cover[0]).suffix.lower()
            if ext in COVER_EXTS:
                cover_path = album_dir / f"cover{ext}"
                # an album's existing cover is never overwritten (and so never deleted by a
                # rollback)
                if not cover_path.exists():
                    write_file(cover_path, cover[1])
                    written.append(cover_path)

        with db.get_write_conn() as conn:
            album_id = insert_tracks(
                conn,
                artist_name,
                None if loose else album_title,
                year,
                cover_path,
                parsed,
                source,
                extra,
            )
    except Exception:
        for p in written:  # DB rolled back, so don't leave orphan files behind
            p.unlink(missing_ok=True)
        # deepest first; a folder that is not empty (somebody else's file arrived meanwhile) stays
        for d in made_dirs:
            try:
                d.rmdir()
            except OSError:
                break
        raise

    return {
        "artist": artist_name,
        "album": album_title or None,
        "album_id": album_id,
        "tracks": len(parsed),
    }


def split_artists(credit) -> list:
    """'A; B' or 'A / B' in a tag means two artists. A slash with no spaces ("AC/DC") is part of a
    name."""
    return [n.strip() for n in re.split(r"\s*;\s*|\s+/\s+", credit or "") if n.strip()]


def find_cover(directory):
    """A cover image sitting next to the songs (cover.jpg, folder.png, ...), or None."""
    try:
        names = {n.lower(): n for n in os.listdir(directory)}
    except OSError:
        return None
    for stem in ("cover", "folder", "front", "album", "artwork"):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            if stem + ext in names:
                return pathlib.Path(directory) / names[stem + ext]
    return None


def insert_tracks(
    conn, artist_name, album_title, year, cover_path, parsed, source="upload", extra=None
):
    """
    Adds the album (if any) and its songs as items in the media ontology (see catalog.py), using an
    open connection. parsed is a list of (Path, tags). The caller owns the transaction. An empty
    album_title makes loose songs. An artist that already exists in the ontology is linked to, not
    duplicated. `source` is recorded as provenance. Returns the album item id, or None for loose
    songs.
    """
    owner_id = catalog.get_or_create_artist(conn, artist_name, source)
    album_id = (
        catalog.get_or_create_album(conn, owner_id, album_title, year, cover_path, source, extra)
        if album_title
        else None
    )

    for dest, tags in sorted(parsed, key=lambda p: (p[1]["track_number"] or 999, p[0].name)):
        credited = [
            catalog.get_or_create_artist(conn, name, source)
            for name in split_artists(tags["artist"]) or [artist_name]
        ]
        catalog.add_song(
            conn,
            album_id,
            credited,
            tags["title"] or dest.stem,
            tags["track_number"],
            dest,
            source,
            extra,
        )
    return album_id
