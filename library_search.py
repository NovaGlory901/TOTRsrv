"""Search over artists, albums and songs.

Matching is the same as the spiders use for names (`spiders.base.squash`): case, width, spaces and
punctuation are ignored, so "lo-fi", "Lo Fi" and "ＬＯＦＩ" all find "lofi". Every word you type must
appear in the name, in any order. Results are grouped by type and ranked: exact, then starts-with,
then word-start, then anywhere.

The names live in an in-memory index (one short string per song: about 100,000 at the largest
libraries tested), rebuilt when the library's counts change (looked at every 15 seconds) or every
few minutes, in the background, so a search never waits for a rebuild and most cost no query at all.
"""

import threading
import time

import db
from spiders.base import norm, squash

MAX_QUERY = 100
GROUP_LIMIT = 40
INDEX_TTL = 300  # seconds; catches renames, which do not change the counts
# seconds between looks at the library's counts (counting 100,000 songs costs about a tenth of a
# second)
CHECK_EVERY = 15

_lock = threading.Lock()
_cache = {"index": None, "signature": None, "at": 0.0, "checked": 0.0, "busy": False}


def _entry(label, **fields) -> dict:
    return {
        "key": squash(label),
        "words": [squash(w) for w in norm(label).split() if squash(w)],
        **fields,
    }


def build_index(conn) -> dict:
    artists = [
        _entry(r["name"], artist_id=r["artist_id"], name=r["name"], track_count=r["track_count"])
        for r in conn.execute(
            "SELECT ar.artist_id, ar.name, count(DISTINCT ta.track_id) AS track_count FROM totr.v_artists ar "
            "JOIN totr.v_track_artists ta ON ta.artist_id = ar.artist_id GROUP BY ar.artist_id, ar.name"
        ).fetchall()
    ]
    albums = [
        _entry(
            r["title"],
            album_id=r["album_id"],
            title=r["title"],
            year=r["year"],
            owner=r["owner"],
            tracks=r["tracks"],
        )
        for r in conn.execute(
            "SELECT al.album_id, al.title, al.year, owner.name AS owner, count(t.track_id) AS tracks FROM totr.v_albums al "
            "JOIN totr.v_artists owner ON owner.artist_id = al.artist_id JOIN totr.v_tracks t ON t.album_id = al.album_id "
            "GROUP BY al.album_id, al.title, al.year, owner.name"
        ).fetchall()
    ]
    # The credits come in their own query and are joined here: one four-way join with an aggregate
    # took 16 seconds on 100,000 songs, this takes under one.
    credits = {}
    for r in conn.execute(
        "SELECT ta.track_id, ar.name FROM totr.v_track_artists ta JOIN totr.v_artists ar ON ar.artist_id = ta.artist_id"
    ).fetchall():
        credits.setdefault(r["track_id"], set()).add(r["name"])
    songs = []
    for r in conn.execute(
        "SELECT t.track_id, t.title, t.album_id, al.title AS album FROM totr.v_tracks t LEFT JOIN totr.v_albums al ON al.album_id = t.album_id"
    ).fetchall():
        names = ", ".join(sorted(credits.get(r["track_id"], ()), key=str.casefold))
        song = _entry(
            r["title"],
            track_id=r["track_id"],
            title=r["title"],
            artists=names,
            album_id=r["album_id"],
            album=r["album"],
        )
        song["context"] = squash(f"{r['title']} {names} {r['album'] or ''}")
        songs.append(song)
    return {"artists": artists, "albums": albums, "songs": songs}


def _signature(conn):
    row = conn.execute(
        "SELECT (SELECT count(*) FROM totr.v_tracks) AS t, (SELECT max(track_id) FROM totr.v_tracks) AS tm, "
        "(SELECT count(*) FROM totr.v_albums) AS al, (SELECT count(*) FROM totr.v_artists) AS ar"
    ).fetchone()
    return tuple(row.values())


def _refresh():
    """Looks at the library's counts and rebuilds the index if they changed (or it is old). Runs in
    the background, so searches keep using the old index meanwhile: rebuilding takes seconds on a
    big library."""
    try:
        with db.get_conn() as conn:
            sig = _signature(conn)
            if sig != _cache["signature"] or time.monotonic() - _cache["at"] > INDEX_TTL:
                index = build_index(conn)
                with _lock:
                    _cache.update(index=index, signature=sig, at=time.monotonic())
    except Exception:
        pass  # keep serving the old index; the next look tries again
    finally:
        with _lock:
            _cache["checked"] = time.monotonic()
            _cache["busy"] = False


def get_index() -> dict:
    with _lock:
        if _cache["index"] is not None:
            if time.monotonic() - _cache["checked"] >= CHECK_EVERY and not _cache["busy"]:
                _cache["busy"] = True
                threading.Thread(target=_refresh, name="search-index", daemon=True).start()
            return _cache["index"]
        with db.get_conn() as conn:  # the very first search waits for the first build
            _cache.update(
                index=build_index(conn),
                signature=_signature(conn),
                at=time.monotonic(),
                checked=time.monotonic(),
            )
        return _cache["index"]


def tokens_of(query: str) -> list:
    return [t for t in (squash(w) for w in norm((query or "")[:MAX_QUERY]).split()) if t]


def _score(key: str, words: list, tokens: list):
    """How well every typed word matches this name, or None if one of them is missing."""
    if not key or not all(t in key for t in tokens):
        return None
    joined = "".join(tokens)
    if key == joined:
        return 100
    if key.startswith(joined):
        return 85
    if all(any(w.startswith(t) for w in words) for t in tokens):
        return 65
    return 40


def _top(scored: list, name_of) -> tuple:
    scored.sort(key=lambda p: (-p[0], name_of(p[1]).casefold()))
    return [
        {k: v for k, v in e.items() if k not in ("key", "words", "context")}
        for _, e in scored[:GROUP_LIMIT]
    ], len(scored)


def search(index: dict, query: str) -> dict:
    tokens = tokens_of(query)
    out = {
        "query": (query or "")[:MAX_QUERY].strip(),
        "artists": [],
        "albums": [],
        "songs": [],
        "totals": {"artists": 0, "albums": 0, "songs": 0},
    }
    if not tokens:
        return out
    for group, name_of in (("artists", lambda e: e["name"]), ("albums", lambda e: e["title"])):
        scored = [
            (s, e) for e in index[group] if (s := _score(e["key"], e["words"], tokens)) is not None
        ]
        out[group], out["totals"][group] = _top(scored, name_of)
    songs = []
    for e in index["songs"]:
        s = _score(e["key"], e["words"], tokens)
        # With two or more words, also match across title + artist + album ("dreams slowerpace"),
        # ranked below title matches.
        if s is None and len(tokens) > 1 and all(t in e["context"] for t in tokens):
            s = 25
        if s is not None:
            songs.append((s, e))
    out["songs"], out["totals"]["songs"] = _top(songs, lambda e: e["title"])
    return out
