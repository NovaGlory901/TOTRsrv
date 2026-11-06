"""
The lookup source that needs no contact, key or account: Deezer's public catalogue API
(api.deezer.com), used read-only for metadata only (the exact spelling of an artist, an album's
year, its song list and track numbers, a cover picture). It has the same functions as
musicbrainz.py, so the enrich spider uses whichever is available. No audio is touched.

Same safety as every outbound fetch: collector/http.py (allow-list, spacing between requests,
redirects re-checked) and the 30-day disk cache shared with musicbrainz.py. Matching stays exact
(case and punctuation ignored): a wrong guess would put wrong facts in the library, so artists that
are not found are simply listed as not found.
"""

import re

from collector import http

from . import musicbrainz as mb
from .base import squash
from .musicbrainz import OnlineTrack, ReleaseGroup

SOURCE = "Deezer"
API = "https://api.deezer.com/"
# Deezer's other kinds (compilations) are left alone
WANTED = {
    "album": "Album",
    "ep": "EP",
    "single": "Single",
}
# the names enrich.py uses on either source
WANTED_TYPES, SKIP_SECONDARY = (
    set(WANTED.values()),
    set(),
)
# Deezer has no secondary types
SKIP_TITLE = re.compile(r"\b(live|remix(es)?|karaoke|instrumentals?|demo)\b", re.I)
PAGE, MAX_PAGES = 100, 3


def _get(path: str, params: dict | None = None):
    """Deezer answers errors with HTTP 200 and an "error" body: a missing thing is None, anything
    else (quota) is an error and is never cached."""

    def valid(data):
        err = data.get("error") if isinstance(data, dict) else None
        if err and err.get("code") != 800:  # 800 = "no data"
            raise http.FetchError(
                f"Deezer: {err.get('message') or err.get('type') or 'error'}",
                retryable=err.get("code") == 4,
            )
        return not err

    data = mb.cached_get(API + path, params or {}, valid=valid)
    return data if data and not data.get("error") else None


def find_artist(name: str):
    """{"id", "name"} of the Deezer artist whose name is the same as this one, or None."""
    data = _get("search/artist", {"q": name, "limit": 5}) or {}
    want = squash(name)
    same = sorted(
        (a for a in data.get("data", []) if squash(a.get("name", "")) == want),
        key=lambda a: -(a.get("nb_fan") or 0),
    )
    # Deezer can list two different artists with one name. Take the first only when it is clearly
    # the real one (10x the fans of the next); otherwise say "not found" rather than guess.
    if same and (
        len(same) == 1 or (same[0].get("nb_fan") or 0) >= 10 * max(1, same[1].get("nb_fan") or 0)
    ):
        return {"id": str(same[0]["id"]), "name": same[0]["name"]}
    return None


def release_groups(artist_id: str) -> list:
    out, seen = [], set()
    for page in range(MAX_PAGES):
        data = _get(f"artist/{artist_id}/albums", {"limit": PAGE, "index": page * PAGE}) or {}
        rows = data.get("data", [])
        for g in rows:
            kind = WANTED.get(g.get("record_type"))
            title = g.get("title", "")
            if kind is None or g["id"] in seen or SKIP_TITLE.search(title):
                continue
            seen.add(g["id"])
            year = re.match(r"\d{4}", g.get("release_date") or "")
            out.append(
                ReleaseGroup(
                    str(g["id"]),
                    title,
                    kind,
                    (),
                    int(year.group()) if year else None,
                    cover=g.get("cover_xl") or g.get("cover_big") or "",
                )
            )
        if len(rows) < PAGE:
            break
    return out


def tracklist(album_id: str) -> list:
    data = _get(f"album/{album_id}/tracks", {"limit": PAGE}) or {}
    return [
        OnlineTrack(
            int(t.get("disk_number") or 1), int(t.get("track_position") or 0), t.get("title", "")
        )
        for t in data.get("data", [])
    ]


def cover_url(album_id: str) -> str:
    # the cover address comes with the album list (ReleaseGroup.cover), so there is no second lookup
    return ""
