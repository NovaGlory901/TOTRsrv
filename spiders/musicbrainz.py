"""
A small, polite MusicBrainz client (musicbrainz.org, open music data) plus Cover Art Archive
lookups.

MusicBrainz asks every program to identify itself with contact details, so this source is only used
when a contact is set (Account page or COLLECTOR_CONTACT); without one the enrich spider uses
spiders/deezer.py. Requests are spaced one second apart by collector/http.py, and every answer is
cached on disk for 30 days, so repeat runs are nearly free.
"""

import json
import os
import re
import threading
import time
from dataclasses import dataclass

from collector import DATA_DIR, http

from .base import squash

SOURCE = "MusicBrainz"
API = "https://musicbrainz.org/ws/2/"
COVERS = "https://coverartarchive.org/release-group/"
CACHE_PATH = DATA_DIR / "musicbrainz_cache.json"
TTL_SECONDS = 30 * 24 * 3600
MAX_ENTRIES = 4000
WANTED_TYPES = {"Album", "EP", "Single"}
SKIP_SECONDARY = {"Live", "Interview", "Audiobook", "Spokenword", "Demo"}

_lock = threading.Lock()
_cache = None


@dataclass
class ReleaseGroup:
    id: str
    title: str
    kind: str  # Album / EP / Single
    secondary: tuple
    year: int | None
    cover: str = ""  # a ready cover address, when the source gives one with the album list


@dataclass
class OnlineTrack:
    disc: int
    number: int
    title: str


def _load():
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save():
    cache = _load()
    if len(cache) > MAX_ENTRIES:  # forget the oldest quarter
        for key in sorted(cache, key=lambda k: cache[k]["at"])[: MAX_ENTRIES // 4]:
            del cache[key]
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CACHE_PATH)


def cached_get(url: str, params: dict | None = None, valid=None):
    """get_json with a 30-day disk cache. A 404 is cached too (as None), so missing things are not
    re-asked. `valid(data)` may raise, or return False to say the answer is an error body that must
    not be cached."""
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    with _lock:
        entry = _load().get(key)
        if entry and time.time() - entry["at"] < TTL_SECONDS:
            return entry["data"]
    try:
        data = http.get_json(url, params)
    except http.FetchError as e:
        if "404" not in str(e):
            raise
        data = None
    if valid is not None and not valid(data):
        return data
    with _lock:
        _load()[key] = {"at": time.time(), "data": data}
        _save()
    return data


def _clean(s: str) -> str:
    return s.replace('"', " ").replace("\\", " ")


def find_artist(name: str):
    """The MusicBrainz artist whose name (or alias) is the same as this one, or None. No guessing."""
    data = (
        cached_get(
            API + "artist/", {"query": f'artist:"{_clean(name)}"', "fmt": "json", "limit": 5}
        )
        or {}
    )
    want = squash(name)
    for a in data.get("artists", []):
        names = {squash(a.get("name", "")), squash(a.get("sort-name", ""))} | {
            squash(x.get("name", "")) for x in a.get("aliases", [])
        }
        if a.get("score", 0) >= 90 and want in names:
            return a
    return None


def release_groups(artist_id: str) -> list:
    data = (
        cached_get(API + "release-group", {"artist": artist_id, "fmt": "json", "limit": 100}) or {}
    )
    out = []
    for g in data.get("release-groups", []):
        year = re.match(r"\d{4}", g.get("first-release-date") or "")
        out.append(
            ReleaseGroup(
                g["id"],
                g.get("title", ""),
                g.get("primary-type") or "",
                tuple(g.get("secondary-types") or ()),
                int(year.group()) if year else None,
            )
        )
    return out


def tracklist(release_group_id: str) -> list:
    data = (
        cached_get(
            API + "release",
            {"release-group": release_group_id, "inc": "recordings", "fmt": "json", "limit": 1},
        )
        or {}
    )
    releases = data.get("releases") or []
    tracks = []
    for medium in releases[0].get("media", []) if releases else []:
        for t in medium.get("tracks", []):
            tracks.append(
                OnlineTrack(
                    int(medium.get("position") or 1),
                    int(t.get("position") or 0),
                    t.get("title", ""),
                )
            )
    return tracks


def cover_url(release_group_id: str) -> str:
    """A front-cover image address if the Cover Art Archive has one for this release, else ''."""
    data = cached_get(COVERS + release_group_id, {})
    if data and any(img.get("front") for img in data.get("images", [])):
        return COVERS + release_group_id + "/front-500"
    return ""
