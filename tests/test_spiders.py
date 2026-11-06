import pathlib
import sys
import tempfile
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from spiders import duplicates, files, metadata
from spiders.base import Snapshot


def make_wav(path, frames=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x01\x02" * frames)


root = pathlib.Path(tempfile.mkdtemp())
a, b, c, bad = root / "a.wav", root / "b.wav", root / "orphan.wav", root / "bad.mp3"
make_wav(a)
make_wav(b)
make_wav(c, frames=4000)  # a and b are byte-identical
bad.write_bytes(b"this is not audio at all")
missing = root / "gone.wav"

artists = [
    {"artist_id": 1, "name": "Kanal Vier"},
    {"artist_id": 2, "name": "\uff2b\uff41\uff4e\uff41\uff4c \uff36\uff49\uff45\uff52"},
    {"artist_id": 3, "name": "Solo feat. Friend"},
    {"artist_id": 4, "name": "Nobody"},
]
albums = [
    {"album_id": 1, "title": "Album", "year": None, "cover_art_path": None, "artist_id": 1},
    {"album_id": 2, "title": "Has Cover", "year": 1999, "cover_art_path": str(a), "artist_id": 1},
]
tracks = [
    {"track_id": 1, "album_id": 1, "track_number": 1, "title": "Song One", "file_path": str(a)},
    {"track_id": 2, "album_id": 1, "track_number": None, "title": "Song Two", "file_path": str(b)},
    {"track_id": 3, "album_id": 2, "track_number": 1, "title": "Gone", "file_path": str(missing)},
    {"track_id": 4, "album_id": 2, "track_number": 2, "title": "Garbage", "file_path": str(bad)},
    {
        "track_id": 5,
        "album_id": None,
        "track_number": None,
        "title": "song  one!",
        "file_path": str(a),
    },
    {
        "track_id": 6,
        "album_id": None,
        "track_number": None,
        "title": "No Credit",
        "file_path": str(c),
    },
]
credits = {1: [1], 2: [1], 3: [1], 4: [1], 5: [1]}  # track 6 has no credit
snap = Snapshot(artists, albums, tracks, credits, root)

found = {}
for mod in (files, duplicates, metadata):
    for f in mod.check(snap):
        found.setdefault(f.kind, []).append(f)

expect = {
    "missing_file": 1,  # gone.wav
    "unreadable_audio": 1,  # bad.mp3
    "orphan_file": 0,  # c.wav IS referenced by track 6, so not an orphan
    "missing_cover": 1,  # album 1 has none
    "duplicate_path": 1,  # tracks 1 and 5 share a.wav
    "duplicate_content": 1,  # a.wav == b.wav
    "duplicate_song": 1,  # "Song One" vs "song  one!" with same artist
    "similar_artist": 1,  # Kanal Vier vs full-width
    "combined_credit": 1,  # "Solo feat. Friend"
    "track_without_artist": 1,  # track 6
    "missing_track_number": 1,  # track 2
    "missing_year": 1,  # album 1
    "artist_without_songs": 3,  # artists 2, 3, 4
}
bad_count = 0
for kind, n in expect.items():
    got = len(found.get(kind, []))
    status = "ok " if got == n else "BAD"
    bad_count += got != n
    print(f"{status} {kind}: expected {n}, got {got}")

# an orphan: drop a file nobody references
(root / "stray.wav").write_bytes(a.read_bytes())
orph = [f for f in files.check(snap) if f.kind == "orphan_file"]
print("ok " if len(orph) == 1 else "BAD", "orphan_file after adding stray.wav:", len(orph))
bad_count += len(orph) != 1
print("FAILED" if bad_count else "ALL PASSED")

print("--- proposals")
from spiders import duplicates as D
from spiders import files as F
from spiders import metadata as M

allf = F.check(snap) + D.check(snap) + M.check(snap)
for f in allf:
    if f.action:
        print(f"{f.kind:22s} -> {f.action['type']:20s} {f.action['params']}")

print("--- outage handling")

from spiders.base import Snapshot as Snap

gone = Snap(artists, albums, tracks, credits, root / "not-mounted")
r = files.check(gone)
print(
    "ok "
    if [f.kind for f in r] == ["library_unavailable"] and not any(f.action for f in r)
    else "BAD",
    "unmounted folder gives one finding, no fixes",
)
many = [
    {
        "track_id": i,
        "album_id": None,
        "track_number": None,
        "title": f"t{i}",
        "file_path": str(root / f"no{i}.wav"),
    }
    for i in range(20)
]
r = files.check(Snap(artists, albums, many, {}, root))
print(
    "ok " if [f.kind for f in r] == ["library_unavailable"] else "BAD",
    "most files missing gives one finding, no per-song deletes",
)
r = files.check(snap)
print(
    "ok " if any(f.kind == "missing_file" and f.action for f in r) else "BAD",
    "a single missing song still gets a fix",
)
