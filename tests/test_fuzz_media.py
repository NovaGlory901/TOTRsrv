"""Damaged songs in, a clean answer out, for the server. Everything here is a place where the server
hands the bytes of a file somebody gave it to mutagen: reading the tags of an upload
(uploader.read_tags), writing tags into a downloaded song (collector.acquire._tag_file), checking a
downloaded song plays (_playable) and the library scan (spiders.files). Real small songs are made on
the spot (WAV, MP3 with ID3, FLAC with a comment block; plus the sample songs of DejaVuAmp when this
is the full repository: Ogg, Opus, M4A, WMA) and damaged in many ways, then each function is called.
They must answer (read_tags: a dict of sane values), never raise, never hang (5 s each) and never
use unreasonable memory. DURATION=seconds (default 15) sets how long; SEED changes the seed. Run:
python tests/test_fuzz_media.py"""

import io
import os
import pathlib
import random
import resource
import shutil
import signal
import struct
import sys
import tempfile
import time
import wave

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
os.chdir(PF)
TMP = pathlib.Path(tempfile.mkdtemp(prefix="totr-fuzz-media-"))
os.environ.update(DB_ENGINE="sqlite", TOTR_DATA_DIR=str(TMP / "data"))

import mutagen
from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, TRCK

import uploader
from collector import acquire

DURATION = float(os.environ.get("DURATION", "15"))
rnd = random.Random(int(os.environ.get("SEED", "20261012")))
fails = 0
problems = []


def check(name, bad):
    global fails
    fails += bool(bad)
    print(("ok  " if not bad else "BAD ") + name + (f"   <- {bad[:3]}" if bad else ""))


# songs made on the spot
def make_wav() -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x01" * 4000)
    return b.getvalue()


def make_mp3(path: pathlib.Path) -> bytes:
    # MPEG-1 layer 3, 128 kbit/s, 44.1 kHz: 417 bytes
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)
    path.write_bytes(frame * 40)
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Title"))
    tags.add(TPE1(encoding=3, text="Artist"))
    tags.add(TALB(encoding=3, text="Album"))
    tags.add(TRCK(encoding=3, text="3/12"))
    tags.add(TDRC(encoding=3, text="2020"))
    tags.save(path)
    return path.read_bytes()


def make_flac(path: pathlib.Path) -> bytes:
    packed = (44100 << 44) | (1 << 41) | (15 << 36) | 44100
    info = struct.pack(">HH", 4096, 4096) + bytes(6) + packed.to_bytes(8, "big") + bytes(16)
    path.write_bytes(b"fLaC" + bytes([0x80, 0, 0, 34]) + info)
    f = mutagen.File(path)
    f.add_tags()
    f["title"] = ["Title"]
    f["artist"] = ["Artist"]
    f["album"] = ["Album"]
    f["tracknumber"] = ["3"]
    f["date"] = ["2020"]
    f.save()
    return path.read_bytes()


seeds = []  # (extension, bytes)
seeds.append((".wav", make_wav()))
seeds.append((".mp3", make_mp3(TMP / "seed.mp3")))
seeds.append((".flac", make_flac(TMP / "seed.flac")))
extra = PF.parent / "DejaVuAmp" / "tests" / "data"
if extra.is_dir():
    for p in sorted(extra.iterdir()):
        if (
            p.suffix.lower() in (".ogg", ".opus", ".m4a", ".wma", ".aac", ".mp3", ".flac", ".wav")
            and p.stat().st_size < 200_000
        ):
            seeds.append((p.suffix.lower(), p.read_bytes()))
print(f"{len(seeds)} seed songs, {DURATION:.0f} s")


# damage
def pos(b):
    return rnd.randrange(len(b)) if rnd.random() < 0.3 else rnd.randrange(min(len(b), 400))


def damage(b: bytes) -> bytes:
    b = bytearray(b)
    for _ in range(rnd.randrange(1, 7)):
        if not b:
            break
        k = rnd.randrange(10)
        if k == 0:
            b[pos(b)] = rnd.randrange(256)
        elif k == 1:
            del b[rnd.randrange(len(b) + 1) :]
        elif k == 2:
            at = pos(b)
            b[at : at + rnd.randrange(1, 40)] = b"\xff" * rnd.randrange(1, 40)
        elif k == 3:
            at = pos(b)
            b[at : at + rnd.randrange(1, 40)] = b"\x00" * rnd.randrange(1, 40)
        elif k == 4:
            at = pos(b)
            b[at:at] = bytes([rnd.randrange(256)]) * rnd.randrange(1, 9)
        elif k == 5:
            at = pos(b)
            del b[at : at + rnd.randrange(1, 65)]
        elif k == 6 and len(b) >= 8:  # a 32-bit field set to something huge (sizes, counts)
            at = rnd.randrange(min(len(b) - 4, 200))
            b[at : at + 4] = rnd.choice(
                [
                    b"\xff\xff\xff\x7f",
                    b"\x7f\xff\xff\xff",
                    b"\xff\xff\xff\xff",
                    b"\x80\x00\x00\x00",
                    b"\x00\x00\x00\x00",
                ]
            )
        elif k == 7:
            at = rnd.randrange(len(b))
            piece = b[at : at + rnd.randrange(1, 513)]
            b[rnd.randrange(len(b) + 1) : 0] = piece
        elif k == 8:
            b[rnd.randrange(len(b))] ^= 1 << rnd.randrange(8)
        else:
            i, j = pos(b), rnd.randrange(len(b))
            b[i], b[j] = b[j], b[i]
    return bytes(b)


class Slow(Exception):
    pass


def on_alarm(*_):
    raise Slow()


signal.signal(signal.SIGALRM, on_alarm)


def guarded(label, fn, *args):
    """Runs fn with a 5-second limit. Returns (result, problem or None)."""
    signal.alarm(5)
    try:
        return fn(*args), None
    except Slow:
        return None, f"{label}: took more than 5 s"
    except MemoryError:
        return None, f"{label}: MemoryError"
    except BaseException as e:  # the functions promise not to raise at all
        return None, f"{label}: raised {type(e).__name__}: {str(e)[:80]}"
    finally:
        signal.alarm(0)


def scan_like_spider(path):
    """The same lines as spiders/files.py: read it, and take any ordinary exception as 'cannot be
    read'."""
    try:
        f = mutagen.File(path)
        return getattr(getattr(f, "info", None), "length", None)
    except Exception:
        return None


def sane(tags):
    bad = []
    if set(tags) != {"title", "artist", "album", "year", "track_number"}:
        return ["wrong keys"]
    for k in ("title", "artist", "album"):
        v = tags[k]
        if v is not None and (not isinstance(v, str) or len(v) > 100_000):
            bad.append(
                f"{k} is {type(v).__name__} of length {len(v) if hasattr(v, '__len__') else '?'}"
            )
    if tags["year"] is not None and not (
        isinstance(tags["year"], int) and 0 <= tags["year"] <= 9999
    ):
        bad.append(f"year {tags['year']!r}")
    if tags["track_number"] is not None and not (
        isinstance(tags["track_number"], int) and 0 <= tags["track_number"] < 2**31
    ):
        bad.append(f"track_number {tags['track_number']!r} does not fit a database integer")
    return bad


# tags that are well-formed but silly
silly = []
for label, track, date, title in [
    ("a track number with 30 digits", "9" * 30, "2020", "t"),
    ("a track number just over a database integer", str(2**31), "2020", "t"),
    ("a title of two million characters", "1", "2020", "x" * 2_000_000),
    ("a year of 0000", "1", "0000", "t"),
    ("a negative-looking track", "-5", "20x", "t"),
]:
    mp3 = TMP / "silly.mp3"
    mp3.write_bytes((bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)) * 40)
    t = ID3()
    t.add(TIT2(encoding=3, text=title))
    t.add(TRCK(encoding=3, text=track))
    t.add(TDRC(encoding=3, text=date))
    t.save(mp3)
    got, p = guarded("read_tags", uploader.read_tags, mp3)
    silly += [f"{label}: {p}"] if p else [f"{label}: {m}" for m in sane(got)]
check("well-formed tags with silly values give values a database can hold", silly)

cases = 0
start = time.time()
peak0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
work = TMP / "work"
work.mkdir()
keep = TMP / "failures"
keep.mkdir()
while time.time() - start < DURATION:
    ext, data = rnd.choice(seeds)
    blob = (
        damage(data)
        if rnd.random() > 0.05
        else bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 3000)))
    )
    path = work / f"song{ext}"
    path.write_bytes(blob)
    cases += 1
    found = []
    tags, p = guarded("read_tags", uploader.read_tags, path)
    if p:
        found.append(p)
    elif tags is None:
        found.append("read_tags returned None")
    else:
        found += ["read_tags: " + s for s in sane(tags)]
    _, p = guarded("_playable", acquire._playable, path)
    found += [p] if p else []
    _, p = guarded("spider scan", scan_like_spider, path)
    found += [p] if p else []
    copy = work / f"copy{ext}"
    shutil.copy(path, copy)
    _, p = guarded(
        "_tag_file",
        acquire._tag_file,
        copy,
        {"artist": "A", "album": "B", "title": "C", "tracknumber": "1"},
    )
    found += [p] if p else []
    if found:
        saved = keep / f"fail-{len(problems) + 1}{ext}"
        path.replace(saved)
        problems.append((saved.name, found))
    else:
        path.unlink(missing_ok=True)
    copy.unlink(missing_ok=True)

grew = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - peak0) / 1024
print(f"{cases} damaged songs, peak memory grew {grew:.0f} MB")
check(
    f"{cases} damaged songs went through read_tags, _playable, the library scan and _tag_file without an exception, a hang or an unusable value",
    [f"{n}: {'; '.join(f)}" for n, f in problems[:6]],
)
check("memory did not run away (under 300 MB growth)", [f"{grew:.0f} MB"] if grew > 300 else [])
if problems:
    print("inputs kept in", keep)
else:
    shutil.rmtree(TMP, ignore_errors=True)
print("\nALL PASSED" if not fails else f"\nFAILED ({fails} failing)")
raise SystemExit(1 if fails else 0)
