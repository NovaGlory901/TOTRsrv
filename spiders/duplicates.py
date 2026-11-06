"""Is anything in the library there twice?"""

import hashlib
import os
import re
from collections import defaultdict

from .base import Finding, Snapshot, is_file, propose, squash

NAME = "duplicates"

# Credits that may really be several artists stuck together. Informational only, since
# names like "M00NofS0rr0W x Figure" can be genuine collaborations.
COMBINED = re.compile(r"\b(feat\.?|featuring|ft\.?|with)\b|\s[x&]\s|,", re.I)


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check(snap: Snapshot) -> list:
    out = []
    title_of = {t["track_id"]: t["title"] for t in snap.tracks}

    by_path = defaultdict(list)
    for t in snap.tracks:
        by_path[os.path.normpath(t["file_path"])].append(t["track_id"])
    for path, ids in by_path.items():
        if len(ids) > 1:
            ids = sorted(ids)
            out.append(
                Finding(
                    NAME,
                    "duplicate_path",
                    "error",
                    f"{len(ids)} tracks use the same file: {path}",
                    {"track_ids": ids},
                    propose(
                        "remove_tracks",
                        "Keep one, remove the extra entries",
                        f"Deletes {len(ids) - 1} extra database row(s) and keeps song #{ids[0]}. "
                        "The audio file itself is kept.",
                        {"keep": ids[0], "remove": ids[1:], "trash_files": False},
                        destructive=True,
                    ),
                )
            )

    # Same bytes under different names. Only hash files whose sizes already match.
    by_size = defaultdict(list)
    for path, ids in by_path.items():
        if is_file(path):
            by_size[os.path.getsize(path)].append((path, ids[0]))
    for group in by_size.values():
        if len(group) < 2:
            continue
        by_hash = defaultdict(list)
        for path, tid in group:
            by_hash[_sha1(path)].append((path, tid))
        for same in by_hash.values():
            if len(same) > 1:
                names = "; ".join(f'"{title_of[tid]}"' for _, tid in same)
                ids = sorted(tid for _, tid in same)
                out.append(
                    Finding(
                        NAME,
                        "duplicate_content",
                        "warn",
                        f"Identical audio file stored {len(same)} times: {names}",
                        {"track_ids": ids},
                        propose(
                            "remove_tracks",
                            "Keep one copy, trash the others",
                            f"Keeps song #{ids[0]}. Removes the other {len(ids) - 1} database row(s) and moves "
                            "their identical files to the music folder's .trash (recoverable).",
                            {"keep": ids[0], "remove": ids[1:], "trash_files": True},
                            destructive=True,
                        ),
                    )
                )

    # Same song entered twice (same title, same artists).
    by_song = defaultdict(list)
    for t in snap.tracks:
        key = (squash(t["title"]), frozenset(snap.credits.get(t["track_id"], [])))
        if key[0]:
            by_song[key].append(t["track_id"])
    for (_title, _artists), ids in by_song.items():
        if len(ids) > 1:
            ids = sorted(ids)
            out.append(
                Finding(
                    NAME,
                    "duplicate_song",
                    "warn",
                    f'"{title_of[ids[0]]}" appears {len(ids)} times with the same artists',
                    {"track_ids": ids},
                    propose(
                        "remove_tracks",
                        "Keep the oldest, trash the others",
                        f"Keeps song #{ids[0]}. Removes the other {len(ids) - 1} row(s) and moves their files to "
                        ".trash (recoverable). Check first: these may be different versions of the song.",
                        {"keep": ids[0], "remove": ids[1:], "trash_files": True},
                        destructive=True,
                    ),
                )
            )

    song_count = defaultdict(int)
    for ids in snap.credits.values():
        for aid in ids:
            song_count[aid] += 1

    # Artists whose names only differ by case, width, spacing or punctuation.
    by_name = defaultdict(list)
    for a in snap.artists:
        key = squash(a["name"])
        if key:
            by_name[key].append(a)
    for group in by_name.values():
        if len(group) > 1:
            names = " / ".join(f'"{a["name"]}"' for a in group)
            ids = [a["artist_id"] for a in group]
            keep = max(group, key=lambda a: (song_count[a["artist_id"]], -a["artist_id"]))
            rest = [a["artist_id"] for a in group if a is not keep]
            out.append(
                Finding(
                    NAME,
                    "similar_artist",
                    "warn",
                    f"These artists may be the same person: {names}",
                    {"artist_ids": ids},
                    propose(
                        "merge_artists",
                        f'Merge into "{keep["name"]}"',
                        f'Moves every song and album to "{keep["name"]}" ({song_count[keep["artist_id"]]} songs) '
                        f"and deletes the other {len(rest)} artist entr{'y' if len(rest) == 1 else 'ies'}.",
                        {"keep": keep["artist_id"], "merge": rest},
                        destructive=True,
                    ),
                )
            )

    for a in snap.artists:
        if COMBINED.search(a["name"]):
            out.append(
                Finding(
                    NAME,
                    "combined_credit",
                    "info",
                    f'"{a["name"]}" may be several artists credited as one',
                    {"artist_id": a["artist_id"]},
                )
            )
    return out
