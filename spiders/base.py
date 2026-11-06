import functools
import os
import pathlib
from dataclasses import dataclass, field

import uploader
from textnorm import norm, squash  # noqa: F401  (re-exported: the spiders import them from here)


@dataclass
class Finding:
    spider: str
    kind: str
    severity: str  # "error" (broken), "warn" (probably wrong), "info" (worth a look)
    message: str
    refs: dict = field(default_factory=dict)
    action: dict = (
        None  # proposed fix, carried out only after the user approves it (see actions.py)
    )
    id: str = ""  # stable across runs, so an ignored finding stays ignored


def propose(type_: str, label: str, detail: str, params: dict, destructive: bool = False) -> dict:
    """A fix the user can approve on the Status page. Only spiders/actions.py knows how to run it."""
    return {
        "type": type_,
        "label": label,
        "detail": detail,
        "params": params,
        "destructive": destructive,
    }


@dataclass
class Snapshot:
    """Everything a spider needs, loaded once, so spiders are plain functions that are easy to test."""

    artists: list
    albums: list
    tracks: list
    credits: dict  # track_id -> [artist_id, ...]
    music_root: pathlib.Path
    _tags: dict = field(default_factory=dict)

    @functools.cached_property
    def artist_names(self) -> dict:
        return {a["artist_id"]: a["name"] for a in self.artists}

    def tags(self, path: str) -> dict:
        if path not in self._tags:
            self._tags[path] = uploader.read_tags(pathlib.Path(path))
        return self._tags[path]


def load(db_module, music_root: pathlib.Path) -> Snapshot:
    with db_module.get_conn() as conn:
        # The library's artists: ones TOTR made, or credited on / owning something the site lists.
        # Other artist items (people and groups from the rest of the database) are not
        # the spiders' business.
        artists = conn.execute(
            "SELECT a.artist_id, a.name FROM totr.v_artists a WHERE a.totr_created "
            "OR EXISTS (SELECT 1 FROM totr.v_track_artists ta WHERE ta.artist_id = a.artist_id) "
            "OR EXISTS (SELECT 1 FROM totr.v_albums al WHERE al.artist_id = a.artist_id) ORDER BY a.artist_id"
        ).fetchall()
        albums = conn.execute(
            "SELECT album_id, title, year, cover_art_path, artist_id FROM totr.v_albums ORDER BY album_id"
        ).fetchall()
        tracks = conn.execute(
            "SELECT track_id, album_id, track_number, title, file_path FROM totr.v_tracks ORDER BY track_id"
        ).fetchall()
        credits: dict = {}
        for r in conn.execute("SELECT track_id, artist_id FROM totr.v_track_artists"):
            credits.setdefault(r["track_id"], []).append(r["artist_id"])
    return Snapshot(artists, albums, tracks, credits, music_root)


def is_file(path: str) -> bool:
    return os.path.isfile(path)
