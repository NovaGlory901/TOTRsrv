"""
Internet Archive (archive.org). Uses its public search and metadata APIs.
Only items whose own 'licenseurl' field is a Creative Commons / public-domain URL are kept.
Netlabels and independent artists who chose a free license are well represented here.
"""

import os
import re
import urllib.parse

from .. import http
from ..licenses import classify
from ..models import Release, Track
from . import clean_title, parse_track_name, text, year_of

NAME = "internet_archive"
SEARCH = "https://archive.org/advancedsearch.php"
META = "https://archive.org/metadata/"
ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# MP3 first: smaller, and what the library mostly holds
EXT_RANK = {
    ".mp3": 0,
    ".ogg": 1,
    ".flac": 2,
}
COVER_NAME = re.compile(r"(cover|folder|front|art|album)", re.I)


def _quote(term: str) -> str:
    return '"' + term.replace("\\", " ").replace('"', " ") + '"'


def _every(value) -> str:
    """All of a field that may be a string or a list (text() would keep only the first entry)."""
    return " ".join(text(v) for v in (value if isinstance(value, (list, tuple)) else [value]))


def pick_tracks(item_id: str, files: list) -> list:
    """One file per song: the best available format, ordered by track number."""
    best = {}
    for f in files:
        name = f.get("name", "")
        ext = os.path.splitext(name)[1].lower()
        if ext not in EXT_RANK or "/" in name:
            continue
        stem = os.path.splitext(f.get("original") or name)[0]
        rank = (EXT_RANK[ext], 0 if f.get("source") == "original" else 1)
        if stem not in best or rank < best[stem][0]:
            best[stem] = (rank, f)
    tracks = []
    for _, f in best.values():
        number, title = parse_track_name(f["name"])
        track_tag = str(f.get("track") or "").split("/")[0]
        if track_tag.isdigit():
            number = int(track_tag)
        tracks.append(
            Track(
                text(f.get("title")) or title,
                number,
                f"https://archive.org/download/{item_id}/{urllib.parse.quote(f['name'])}",
                f["name"],
                int(f.get("size") or 0),
            )
        )
    tracks.sort(key=lambda t: (t.number if t.number is not None else 9999, t.filename.lower()))
    return tracks


def pick_cover(item_id: str, files: list) -> str:
    candidates = [
        f
        for f in files
        if f.get("format") in ("JPEG", "PNG")
        and f.get("source") == "original"
        and int(f.get("size") or 0) < 8_000_000
        and "/" not in f.get("name", "")
    ]
    candidates.sort(key=lambda f: (not COVER_NAME.search(f["name"]), f["name"].lower()))
    if not candidates:
        return ""
    return f"https://archive.org/download/{item_id}/{urllib.parse.quote(candidates[0]['name'])}"


def _search(query: str, label: str, limit: int, max_bytes: int, skip) -> list:
    found = http.get_json(
        SEARCH,
        {
            "q": query,
            "fl[]": ["identifier", "licenseurl", "downloads", "num_reviews", "avg_rating"],
            "rows": limit * 3,
            "sort[]": "downloads desc",
            "output": "json",
        },
    )["response"]["docs"]
    out = []
    for doc in found:
        if len(out) >= limit:
            break
        item_id = text(doc.get("identifier"))
        if not ID_OK.match(item_id) or classify(doc.get("licenseurl")) is None or skip(item_id):
            continue
        try:
            meta = http.get_json(META + urllib.parse.quote(item_id))
        except http.FetchError:
            continue
        m = meta.get("metadata", {})
        lic = classify(m.get("licenseurl"))  # trust the item's own record over the search index
        artist = text(m.get("creator"))
        title = clean_title(text(m.get("title")), artist)
        tracks = pick_tracks(item_id, meta.get("files", []))
        if lic is None or not artist or not title or not tracks:
            continue
        collections = m.get("collection") or []
        release = Release(
            NAME,
            item_id,
            title,
            artist,
            year_of(m.get("year"), m.get("date")),
            lic.code,
            lic.label,
            lic.url,
            lic.needs_credit,
            f"https://archive.org/details/{item_id}",
            label,
            tracks,
            pick_cover(item_id, meta.get("files", [])),
            single=len(tracks) == 1,
            signals={
                "downloads": doc.get("downloads"),
                "reviews": doc.get("num_reviews"),
                "rating": doc.get("avg_rating"),
                "collections": [
                    text(c)
                    for c in (collections if isinstance(collections, list) else [collections])
                ][:20],
                # only used to spot stock/filler wording; never shown
                "text": " ".join(_every(m.get(k)) for k in ("subject", "description"))[:600],
            },
        )
        if release.size <= max_bytes:
            out.append(release)
    return out


def search(genre: str, limit: int, max_bytes: int, skip=lambda source_id: False) -> list:
    """Netlabel releases first (real artists, curated), then anything else with a free license to
    fill the quota."""
    base = f"mediatype:audio AND subject:{_quote(genre)} AND licenseurl:(*creativecommons.org*)"
    out = _search(base + " AND collection:netlabels", genre, limit, max_bytes, skip)
    if len(out) < limit:
        got = {r.source_id for r in out}
        out += _search(
            base, genre, limit - len(out), max_bytes, lambda sid: sid in got or skip(sid)
        )
    return out


def search_wanted(
    artist: str, title: str, limit: int, max_bytes: int, skip=lambda source_id: False
) -> list:
    """Looks for a specific artist (and album, if given) that the library is missing."""
    query = f"mediatype:audio AND creator:{_quote(artist)} AND licenseurl:(*creativecommons.org*)"
    if title:
        query += f" AND title:{_quote(title)}"
    return _search(query, "wanted", limit, max_bytes, skip)
