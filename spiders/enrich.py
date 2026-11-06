"""
The gap-filling spider. For each artist in your library it looks them up online, compares
what exists with what you have, and proposes what it can fix:

  * an album's missing year and a song's missing track number
  * missing cover art
  * songs you are missing from albums you partly have, and releases you do not have at all. These
    can be
    added to the wanted list, so the Collector searches the legal sources for them.

It only ever proposes. Nothing changes until an admin approves it on the Admin page. Matching is
exact (artist and album names, ignoring case and punctuation), because a wrong guess would put wrong
facts in the library. Very obscure artists are simply not found, and are listed as such.

The lookup source needs nothing from you: Deezer's public catalogue (spiders/deezer.py). If a
contact is set (Account page or COLLECTOR_CONTACT) it uses MusicBrainz instead, which knows more
obscure artists but asks every program to give contact details.
"""

from collections import Counter, defaultdict

import app_settings
from collector import http

from . import deezer, musicbrainz
from .base import Finding, Snapshot, is_file, propose, squash

NAME = "enrich"
SLOW = True  # talks to the internet, so it only runs when asked (see run.py)
MAX_CONSECUTIVE_ERRORS = 5


def source():
    """MusicBrainz when a contact is set (their rule), otherwise Deezer, which needs none."""
    return musicbrainz if app_settings.contact() else deezer


def check(snap: Snapshot, progress=lambda text: None) -> list:
    mb = source()
    credited = Counter(a for ids in snap.credits.values() for a in ids)
    artists = sorted(
        (a for a in credited if a in snap.artist_names), key=lambda a: (-credited[a], a)
    )
    tracks_by_album = defaultdict(list)
    for t in snap.tracks:
        if t["album_id"] is not None:
            tracks_by_album[t["album_id"]].append(t)
    albums_of = defaultdict(list)  # artist_id -> albums holding a track they are credited on
    albums_by_id = {a["album_id"]: a for a in snap.albums}
    for album_id, tracks in tracks_by_album.items():
        if album_id not in albums_by_id:
            continue
        for artist_id in {a for t in tracks for a in snap.credits.get(t["track_id"], [])}:
            albums_of[artist_id].append(albums_by_id[album_id])
    owned_titles = defaultdict(set)  # artist_id -> squashed titles of anything they have
    for t in snap.tracks:
        for artist_id in snap.credits.get(t["track_id"], []):
            owned_titles[artist_id].add(squash(t["title"]))
    for artist_id, albums in albums_of.items():
        owned_titles[artist_id] |= {squash(a["title"]) for a in albums}

    out, unmatched, errors = [], [], 0
    for n, artist_id in enumerate(artists, 1):
        name = snap.artist_names[artist_id]
        progress(f"Looking up {name} ({n} of {len(artists)})...")
        try:
            found = mb.find_artist(name)
            if not found:
                unmatched.append(name)
                continue
            groups = mb.release_groups(found["id"])
            errors = 0
        except http.FetchError as e:
            errors += 1
            if errors >= MAX_CONSECUTIVE_ERRORS:
                out.append(
                    Finding(
                        NAME,
                        "enrich_stopped",
                        "error",
                        f"Online lookups stopped after repeated errors: {e}",
                    )
                )
                break
            continue

        by_title = {squash(g.title): g for g in groups if g.kind in mb.WANTED_TYPES}
        matched = set()
        for al in albums_of.get(artist_id, []):
            g = by_title.get(squash(al["title"]))
            if g is None:
                continue
            matched.add(g.id)
            mbref = {"album_id": al["album_id"], "mb": g.id}
            try:
                out += _album_findings(
                    mb, snap, name, al, g, tracks_by_album[al["album_id"]], mbref
                )
            except http.FetchError:
                continue

        others = [
            g
            for g in groups
            if g.kind in mb.WANTED_TYPES
            and g.id not in matched
            and not (set(g.secondary) & mb.SKIP_SECONDARY)
            and squash(g.title) not in owned_titles[artist_id]
        ]
        if others:
            others.sort(key=lambda g: (g.year or 9999, g.title))
            titles = [g.title for g in others[:15]]
            shown = ", ".join(f"{g.title} ({g.year})" if g.year else g.title for g in others[:8])
            more = f" and {len(others) - 8} more" if len(others) > 8 else ""
            out.append(
                Finding(
                    NAME,
                    "online_missing_releases",
                    "info",
                    f"{name} has {len(others)} release(s) online that you do not have: {shown}{more}",
                    {"artist_id": artist_id, "mb": found["id"]},
                    propose(
                        "add_wanted",
                        "Look for these when searching",
                        "Adds them to the wanted list. The next music search will check the free, legal sources for them. "
                        "Nothing is downloaded until you approve a suggestion.",
                        {"artist": name, "titles": titles},
                    ),
                )
            )

    if unmatched:
        shown = ", ".join(unmatched[:12]) + (
            f" and {len(unmatched) - 12} more" if len(unmatched) > 12 else ""
        )
        out.append(
            Finding(
                NAME,
                "online_unmatched",
                "info",
                f"{len(unmatched)} of {len(artists)} artists could not be found online (likely too obscure): {shown}",
                {"count": len(unmatched)},
            )
        )
    return out


def _album_findings(mb, snap, artist_name, al, g, lib_tracks, mbref) -> list:
    out = []
    if al["year"] is None and g.year:
        out.append(
            Finding(
                NAME,
                "online_album_year",
                "info",
                f'{mb.SOURCE} says "{al["title"]}" by {artist_name} came out in {g.year}',
                mbref,
                propose(
                    "set_year",
                    f"Set year to {g.year}",
                    "Updates only this album's year.",
                    {"album_id": al["album_id"], "year": g.year},
                ),
            )
        )

    needs_numbers = any(t["track_number"] is None for t in lib_tracks)
    online = mb.tracklist(g.id) if (needs_numbers or lib_tracks) else []
    online_by_title = {squash(t.title): t for t in online}
    lib_titles = {squash(t["title"]) for t in lib_tracks}

    if len({t.disc for t in online}) <= 1:  # track numbers only make sense within one disc
        for t in lib_tracks:
            hit = online_by_title.get(squash(t["title"]))
            if t["track_number"] is None and hit and hit.number:
                out.append(
                    Finding(
                        NAME,
                        "online_track_number",
                        "info",
                        f'"{t["title"]}" on "{al["title"]}" is track {hit.number} according to {mb.SOURCE}',
                        {"track_id": t["track_id"], "mb": g.id},
                        propose(
                            "set_track_number",
                            f"Set track number to {hit.number}",
                            "Updates only this song's track number.",
                            {"track_id": t["track_id"], "track_number": hit.number},
                        ),
                    )
                )

    missing = [t for t in online if squash(t.title) not in lib_titles]
    if missing and (lib_titles & online_by_title.keys()):
        shown = ", ".join(t.title for t in missing[:8]) + (
            f" and {len(missing) - 8} more" if len(missing) > 8 else ""
        )
        out.append(
            Finding(
                NAME,
                "online_missing_tracks",
                "info",
                f'You have {len(lib_titles & online_by_title.keys())} of {len(online)} songs on "{al["title"]}" by {artist_name}. Missing: {shown}',
                mbref,
                propose(
                    "add_wanted",
                    "Look for this album when searching",
                    "Adds the album to the wanted list. The next music search will check the free, legal sources for it.",
                    {"artist": artist_name, "titles": [al["title"]]},
                ),
            )
        )

    cover = al["cover_art_path"]
    if not (cover and is_file(cover)):
        url = g.cover or mb.cover_url(g.id)
        if url:
            out.append(
                Finding(
                    NAME,
                    "online_cover",
                    "info",
                    f'Cover art for "{al["title"]}" by {artist_name} is available from {"the Cover Art Archive" if mb is musicbrainz else mb.SOURCE}',
                    mbref,
                    propose(
                        "fetch_cover",
                        "Download the cover",
                        "Saves the front cover next to the songs and points the album at it.",
                        {"album_id": al["album_id"], "url": url},
                    ),
                )
            )
    return out
