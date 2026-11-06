"""Is the information about the music complete and consistent?"""

from .base import Finding, Snapshot, is_file, propose, squash

NAME = "metadata"


def check(snap: Snapshot) -> list:
    out = []
    album_title = {a["album_id"]: a["title"] for a in snap.albums}
    credited = {aid for ids in snap.credits.values() for aid in ids}

    for t in snap.tracks:
        tid = t["track_id"]
        if not snap.credits.get(tid):
            out.append(
                Finding(
                    NAME,
                    "track_without_artist",
                    "error",
                    f'"{t["title"]}" has no artist credited',
                    {"track_id": tid},
                )
            )
        if t["album_id"] is not None and t["track_number"] is None:
            number = snap.tags(t["file_path"])["track_number"] if is_file(t["file_path"]) else None
            out.append(
                Finding(
                    NAME,
                    "missing_track_number",
                    "info",
                    f'"{t["title"]}" on "{album_title.get(t["album_id"], "?")}" has no track number',
                    {"track_id": tid},
                    propose(
                        "set_track_number",
                        f"Set track number to {number} (from the file)",
                        "Updates only this song's track number.",
                        {"track_id": tid, "track_number": number},
                    )
                    if number
                    else None,
                )
            )
        if is_file(t["file_path"]):
            tag_title = snap.tags(t["file_path"])["title"]
            if tag_title and squash(tag_title) != squash(t["title"]):
                out.append(
                    Finding(
                        NAME,
                        "tag_mismatch",
                        "info",
                        f'Title in the file is "{tag_title}" but the database says "{t["title"]}"',
                        {"track_id": tid},
                        propose(
                            "set_title",
                            f'Rename to "{tag_title}" (from the file)',
                            f'Changes the title in the database from "{t["title"]}" to "{tag_title}".',
                            {"track_id": tid, "title": tag_title},
                        ),
                    )
                )

    years = {}
    for t in snap.tracks:
        if t["album_id"] is not None and t["album_id"] not in years and is_file(t["file_path"]):
            y = snap.tags(t["file_path"])["year"]
            if y:
                years[t["album_id"]] = y
    for al in snap.albums:
        if al["year"] is None:
            y = years.get(al["album_id"])
            out.append(
                Finding(
                    NAME,
                    "missing_year",
                    "info",
                    f'Album "{al["title"]}" has no year',
                    {"album_id": al["album_id"]},
                    propose(
                        "set_year",
                        f"Set year to {y} (from the file tags)",
                        "Updates only this album's year.",
                        {"album_id": al["album_id"], "year": y},
                    )
                    if y
                    else None,
                )
            )

    owners = {al["artist_id"] for al in snap.albums}
    for a in snap.artists:
        if a["artist_id"] not in credited:
            note = (
                " (owns an album, so it is probably a compilation label)"
                if a["artist_id"] in owners
                else ""
            )
            out.append(
                Finding(
                    NAME,
                    "artist_without_songs",
                    "info",
                    f'Artist "{a["name"]}" is not credited on any song{note}',
                    {"artist_id": a["artist_id"]},
                )
            )
    return out
