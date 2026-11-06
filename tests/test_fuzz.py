"""Junk in, clean refusal out. Feeds thousands of random and mangled inputs (fixed seed, so a
failure repeats) to the parsers that take untrusted input and checks that each either works or
refuses with its own error, never with a crash. Covers the release manifest and signature, the
public key file, version numbers, the update bundle unpacker, the Move to PostgreSQL request, the
search box and text normalising."""

import base64
import io
import json
import os
import pathlib
import random
import sys
import tarfile
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
sys.path.insert(0, str(PF / "tools"))
os.chdir(PF)
TMP = pathlib.Path(tempfile.mkdtemp(prefix="totr-fuzz-"))
os.environ.update(DB_ENGINE="sqlite", TOTR_DATA_DIR=str(TMP / "data"))

import move_to_postgres as mv

import library_search
import textnorm
from updates import signing

rnd = random.Random(20261011)
fails = 0


def check(name, bad):
    global fails
    fails += bool(bad)
    print(("ok  " if not bad else "BAD ") + name + (f"   <- {bad[:3]}" if bad else ""))


NASTY = [
    "",
    " ",
    "\x00",
    "\n",
    "'",
    '"',
    "\\",
    "../",
    "%s",
    "{}",
    "${x}",
    "<script>",
    "é",
    "日本語",
    "\U0001f600",
    "\ud800".encode("utf-8", "surrogatepass").decode("utf-8", "replace"),
    "9" * 400,
    "-1",
    "0",
    "1e999",
    "null",
    "[]",
    "{}",
    "true",
    "ed25519",
    "# c",
    "A" * 1000,
]


def junk_text(n=40):
    return "".join(
        rnd.choice(NASTY) if rnd.random() < 0.3 else chr(rnd.randrange(32, 0x2FFF))
        for _ in range(rnd.randrange(0, n))
    )


def junk_json():
    pool = [
        None,
        True,
        False,
        0,
        -1,
        2**70,
        1.5,
        "x",
        "0.7.1",
        "TOTRsrv",
        [],
        {},
        [1],
        {"a": 1},
        "9" * 50,
    ]
    return rnd.choice(pool)


def attempt(fn, expected, calls):
    """Runs fn on each input; returns the (input, error) pairs where something other than `expected`
    came out."""
    bad = []
    for arg in calls:
        try:
            fn(arg)
        except expected:
            pass
        except Exception as e:  # a crash, not a refusal
            bad.append((repr(arg)[:60], type(e).__name__ + ": " + str(e)[:50]))
    return bad


# manifest, signature, key, versions
good = {
    "format": signing.FORMAT,
    "format_version": 1,
    "product": signing.PRODUCT,
    "release": "0.7.1",
    "created": "2026-10-11T00:00:00+00:00",
    "bundle": {"name": "TOTRsrv-0.7.1.tar.gz", "sha256": "a" * 64, "size": 123},
    "notes": "x",
}
signing.parse_manifest(json.dumps(good).encode())
mutants = []
for _ in range(3000):
    m = json.loads(json.dumps(good))
    for _ in range(rnd.randrange(1, 4)):
        target = m if (rnd.random() < 0.6 or not isinstance(m.get("bundle"), dict)) else m["bundle"]
        if not target:
            continue
        key = rnd.choice(list(target))
        action = rnd.random()
        if action < 0.5:
            target[key] = junk_json()
        elif action < 0.8:
            del target[key]
        else:
            target[junk_text(8)] = junk_json()
    mutants.append(json.dumps(m).encode())
raw = json.dumps(good).encode()
for _ in range(2000):
    b = bytearray(raw)
    for _ in range(rnd.randrange(1, 6)):
        b[rnd.randrange(len(b))] = rnd.randrange(256)
    mutants.append(bytes(b))
mutants += [rnd.randbytes(rnd.randrange(0, 200)) for _ in range(500)] + [
    b"[" * 5000,
    b"{" * 5000,
    b"\xff\xfe",
    b"",
]
check(
    f"release.json: {len(mutants)} mangled manifests are refused cleanly",
    attempt(signing.parse_manifest, signing.UpdateError, mutants),
)
check(
    "public key file: junk is refused cleanly",
    attempt(
        signing.parse_pubkey,
        signing.UpdateError,
        [junk_text(60) for _ in range(3000)]
        + [
            "ed25519 " + base64.b64encode(rnd.randbytes(rnd.randrange(0, 80))).decode()
            for _ in range(500)
        ],
    ),
)
check(
    "signature: junk signatures are refused cleanly (or the library is absent)",
    attempt(
        lambda s: signing.verify(b"data", s, bytes(range(32))),
        signing.UpdateError,
        [junk_text(100) for _ in range(500)]
        + [base64.b64encode(rnd.randbytes(rnd.randrange(0, 100))).decode() for _ in range(500)],
    ),
)
check(
    "version numbers: junk is refused cleanly",
    attempt(
        signing.version_tuple,
        signing.UpdateError,
        [junk_text(12) for _ in range(3000)] + [junk_json() for _ in range(200)],
    ),
)
check(
    "is_newer never crashes on text it was told is a version",
    attempt(
        lambda p: signing.is_newer(*p),
        signing.UpdateError,
        [(junk_text(8), junk_text(8)) for _ in range(1000)],
    ),
)


# bundles
def make_bundle(path):
    with tarfile.open(path, "w:gz") as tar:
        for _ in range(rnd.randrange(0, 6)):
            info = tarfile.TarInfo(
                rnd.choice(
                    [
                        "a.txt",
                        "../evil",
                        "/abs",
                        "d/e/f.py",
                        "x\\y",
                        "ok/" + junk_text(5),
                        "./",
                        "",
                        "a\x00b",
                    ]
                )
            )
            info.type = rnd.choice(
                [
                    tarfile.REGTYPE,
                    tarfile.DIRTYPE,
                    tarfile.SYMTYPE,
                    tarfile.LNKTYPE,
                    tarfile.CHRTYPE,
                    tarfile.FIFOTYPE,
                ]
            )
            info.linkname = rnd.choice(["/etc/passwd", "../x", ""])
            data = rnd.randbytes(rnd.randrange(0, 50))
            info.size = len(data) if info.type == tarfile.REGTYPE else 0
            tar.addfile(info, io.BytesIO(data) if info.size else None)


bad, escaped = [], []
for n in range(150):
    bundle, dest = TMP / f"b{n}.tar.gz", TMP / f"dest{n}"
    make_bundle(bundle)
    if rnd.random() < 0.3:  # damage it
        b = bytearray(bundle.read_bytes())
        for _ in range(rnd.randrange(1, 4)):
            if b:
                b[rnd.randrange(len(b))] = rnd.randrange(256)
        bundle.write_bytes(bytes(b))
    try:
        signing.safe_unpack(bundle, dest)
    except signing.UpdateError:
        if dest.exists():
            bad.append(("left a half-unpacked folder", n))
    except Exception as e:
        bad.append((type(e).__name__, str(e)[:50]))
    for f in TMP.rglob("evil"):
        escaped.append(str(f))
check(
    "update bundles: 150 hostile or damaged bundles are refused cleanly and leave nothing behind",
    bad,
)
check("and none of them wrote outside its folder", escaped)

# the Move to PostgreSQL request
base = {
    "mode": "move",
    "host": "db.example.org",
    "port": "5432",
    "dbname": "totr",
    "sslmode": "require",
    "roles": "several",
    "admin_user": "postgres",
    "admin_password": "pw",
}
reqs = []
for _ in range(3000):
    r = dict(base)
    for _ in range(rnd.randrange(1, 4)):
        k = rnd.choice(list(r))
        r[k] = junk_text(20) if rnd.random() < 0.7 else junk_json()
    reqs.append(r)
reqs += [junk_json() for _ in range(100)] + [{}]
check(
    f"move request: {len(reqs)} junk requests are refused cleanly",
    attempt(mv.validate, mv.MoveError, reqs),
)
passed = []
for r in reqs:
    try:
        v = mv.validate(r)
    except mv.MoveError:
        continue
    passed.append(v)
check(
    "anything it accepts has a clean host and database name",
    [v for v in passed if not mv.HOST.match(v["host"]) or not mv.IDENT.match(v["dbname"])],
)

# search and text
check(
    "text normalising never crashes",
    attempt(
        lambda s: (textnorm.norm(s), textnorm.squash(s)), (), [junk_text(80) for _ in range(5000)]
    ),
)
check(
    "search words never crash",
    attempt(library_search.tokens_of, (), [junk_text(80) for _ in range(5000)]),
)

import shutil

shutil.rmtree(TMP, ignore_errors=True)
print("\nALL PASSED" if not fails else f"\nFAILED ({fails} failing)")
raise SystemExit(1 if fails else 0)
