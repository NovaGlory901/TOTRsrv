"""
Jamendo (jamendo.com): independent artists, Creative Commons licensed, with an official API.
Needs a free client id from developer.jamendo.com in .env as JAMENDO_CLIENT_ID; skipped without one.
Only tracks Jamendo marks as downloadable are used.
"""

import os
import re

from .. import http
from ..licenses import classify
from ..models import Release, Track
from . import text, year_of


def _key(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.casefold())


NAME = "jamendo"
API = "https://api.jamendo.com/v3.0/tracks/"
TAG_OK = re.compile(r"^[A-Za-z0-9_+-]{1,40}$")


def available() -> bool:
    return bool(os.environ.get("JAMENDO_CLIENT_ID"))


def _group(data: dict, genre: str, limit: int, skip, keep=lambda track: True) -> list:
    albums = {}
    for t in data.get("results", []):
        if not keep(t):
            continue
        lic = classify(t.get("license_ccurl"))
        if lic is None or not t.get("audiodownload_allowed") or not t.get("audiodownload"):
            continue
        key = text(t.get("album_id")) or f"track-{t.get('id')}"
        rel = albums.get(key)
        if rel is None:
            single = not t.get("album_id")
            page = f"https://www.jamendo.com/{'track' if single else 'album'}/{t.get('id') if single else t.get('album_id')}"
            rel = albums[key] = Release(
                NAME,
                key,
                text(t.get("name")) if single else text(t.get("album_name")),
                text(t.get("artist_name")),
                year_of(t.get("releasedate")),
                lic.code,
                lic.label,
                lic.url,
                lic.needs_credit,
                page,
                genre,
                [],
                text(t.get("image")),
                single,
            )
        position = str(t.get("position", ""))
        rel.tracks.append(
            Track(
                text(t.get("name")),
                int(position) if position.isdigit() else None,
                t["audiodownload"],
                f"{text(t.get('name'))}.mp3",
                0,
            )
        )
    out = []
    for rel in albums.values():
        if rel.artist and rel.title and not skip(rel.source_id):
            rel.tracks.sort(
                key=lambda x: (x.number if x.number is not None else 9999, x.title.lower())
            )
            out.append(rel)
        if len(out) >= limit:
            break
    return out


def search(genre: str, limit: int, max_bytes: int, skip=lambda source_id: False) -> list:
    tag = re.sub(r"\s+", "", genre.strip().lower())
    if not available() or not TAG_OK.match(tag):
        return []
    data = http.get_json(
        API,
        {
            "client_id": os.environ["JAMENDO_CLIENT_ID"],
            "format": "json",
            "limit": limit * 12,
            "tags": tag,
            "order": "popularity_total",
            "audiodownload_allowed": "true",
        },
    )
    return _group(data, genre, limit, skip)


def search_wanted(
    artist: str, title: str, limit: int, max_bytes: int, skip=lambda source_id: False
) -> list:
    """Looks for a specific artist (and album or song, if given) that the library is missing."""
    if not available() or not artist.strip():
        return []
    data = http.get_json(
        API,
        {
            "client_id": os.environ["JAMENDO_CLIENT_ID"],
            "format": "json",
            "limit": 100,
            "artist_name": artist,
            "audiodownload_allowed": "true",
        },
    )
    want_title = _key(title)

    def keep(t):
        return _key(text(t.get("artist_name"))) == _key(artist) and (
            not want_title
            or want_title in (_key(text(t.get("album_name"))), _key(text(t.get("name"))))
        )

    return _group(data, "wanted", limit, skip, keep)
