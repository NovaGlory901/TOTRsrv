"""Signed updates, the web-app half: the release format and signature check, safe unpacking, the
developer's release tool, and finding / downloading / filing a release (GitHub is faked: nothing
here touches the network). The installing half is tests/test_apply_update.py."""

import base64
import io
import json
import os
import pathlib
import sys
import tarfile
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
sys.path.insert(0, str(PF / "tools"))
from check import check, finish

try:
    import cryptography  # noqa: F401
# the server's virtual environment gets it from requirements.txt; on a dev machine borrow the
# system's copy for this test only
except ImportError:
    import glob

    for p in glob.glob("/usr/lib/python3*/site-packages"):
        sys.path.append(p)
    try:
        import cryptography  # noqa: F401,F811
    except ImportError:
        print(
            "skip  the 'cryptography' package is not installed here (pip install -r requirements.txt): nothing about signatures was checked"
        )
        raise SystemExit(0)

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

TMP = pathlib.Path(tempfile.mkdtemp(prefix="totr-updates-"))
os.environ["TOTR_STATE_DIR"] = str(TMP / "state")
os.environ["UPDATE_REPO"] = "someone/TOTR"
os.environ["UPDATE_PUBKEY_FILE"] = str(TMP / "pubkey.txt")
os.environ["TOTR_SIGNING_PASSPHRASE"] = "a long test passphrase"

import release_tool  # noqa: E402

import version  # noqa: E402
from collector import http  # noqa: E402
from updates import SUFFIXES, fetch, repo, signing, updates_dir  # noqa: E402

KEY = ed25519.Ed25519PrivateKey.generate()
OTHER = ed25519.Ed25519PrivateKey.generate()
raw = lambda k: k.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw
)
PUB = raw(KEY)
line = lambda k: "ed25519 " + base64.b64encode(raw(k)).decode()


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except signing.UpdateError as e:
        return str(e) or "refused"
    return None


# the public key
check("a key line is read", signing.parse_pubkey("# comment\n\n" + line(KEY) + " my key\n") == PUB)
check(
    "a missing or empty key file is 'no key', not an error",
    signing.load_pubkey(TMP / "nope.txt") is None
    and (TMP / "e.txt").write_text("# only comments\n\n") is not None
    and signing.load_pubkey(TMP / "e.txt") is None,
)
for bad in (
    "rsa AAAA",
    "ed25519",
    "ed25519 !!!notbase64",
    "ed25519 " + base64.b64encode(b"short").decode(),
):
    check(f"a malformed key line is refused ({bad[:20]!r})", raises(signing.parse_pubkey, bad))
check(
    "the shipped update_pubkey.txt is empty or holds exactly one valid public key (the developer's), never something half-made",
    (lambda k: k is None or len(k) == 32)(signing.load_pubkey(PF / "update_pubkey.txt")),
)
check(
    "the app's copy of the key (TOTRspa) is the same key as the server's",
    (PF.parent / "TOTRspa" / "shared" / "electron" / "update_pubkey.txt").exists() is False
    or signing.load_pubkey(PF.parent / "TOTRspa" / "shared" / "electron" / "update_pubkey.txt")
    == signing.load_pubkey(PF / "update_pubkey.txt"),
)

# the signature
data = b'{"hello": "world"}\n'
sig = signing.sign(data, KEY)
check("a good signature verifies", raises(signing.verify, data, sig, PUB) is None)
check("one changed byte is refused", raises(signing.verify, data + b" ", sig, PUB))
check(
    "another developer's key is refused",
    raises(signing.verify, data, signing.sign(data, OTHER), PUB),
)
check("a signature that is not base64 is refused", raises(signing.verify, data, "!!!", PUB))
check(
    "a signature of the wrong length is refused",
    raises(signing.verify, data, base64.b64encode(b"x" * 10).decode(), PUB),
)
check("an empty signature is refused", raises(signing.verify, data, "", PUB))
check(
    "an unusable public key is refused, not a crash",
    raises(signing.verify, data, sig, b"\x00" * 31),
)


# the manifest
def manifest(**over):
    m = {
        "format": "totr-release",
        "format_version": 1,
        "product": "TOTRsrv",
        "release": "9.9.9",
        "created": "2026-01-01T00:00:00+00:00",
        "bundle": {"name": "TOTRsrv-9.9.9.tar.gz", "sha256": "a" * 64, "size": 1234},
        "notes": "fixes",
    }
    m.update(over)
    return m


enc = lambda m: json.dumps(m).encode()
check("a well-formed manifest is accepted", raises(signing.parse_manifest, enc(manifest())) is None)
cases = {
    "not JSON": b"not json",
    "wrong format": enc(manifest(format="other")),
    "wrong format version": enc(manifest(format_version=2)),
    "another product": enc(manifest(product="Other")),
    "bad version": enc(manifest(release="1.2")),
    "version with text": enc(manifest(release="1.2.x")),
    "bundle name does not match": enc(
        manifest(bundle={"name": "TOTRsrv-1.0.0.tar.gz", "sha256": "a" * 64, "size": 5})
    ),
    "bundle name with a path": enc(
        manifest(bundle={"name": "../TOTRsrv-9.9.9.tar.gz", "sha256": "a" * 64, "size": 5})
    ),
    "bad checksum": enc(
        manifest(bundle={"name": "TOTRsrv-9.9.9.tar.gz", "sha256": "xyz", "size": 5})
    ),
    "zero size": enc(
        manifest(bundle={"name": "TOTRsrv-9.9.9.tar.gz", "sha256": "a" * 64, "size": 0})
    ),
    "huge size": enc(
        manifest(bundle={"name": "TOTRsrv-9.9.9.tar.gz", "sha256": "a" * 64, "size": 10**12})
    ),
    "size is a boolean": enc(
        manifest(bundle={"name": "TOTRsrv-9.9.9.tar.gz", "sha256": "a" * 64, "size": True})
    ),
    "no bundle": enc({k: v for k, v in manifest().items() if k != "bundle"}),
    "bad min_from": enc(manifest(min_from="x")),
    "notes not text": enc(manifest(notes=5)),
    "notes too long": enc(manifest(notes="x" * 5000)),
    "too big": b" " * (signing.MAX_MANIFEST_BYTES + 1),
    "a list": b"[]",
}
for label, blob in cases.items():
    check(f"a manifest is refused: {label}", raises(signing.parse_manifest, blob))
check(
    "verify_release looks at the signature before anything else",
    "not signed"
    in (
        raises(signing.verify_release, enc(manifest(product="Other")), signing.sign(b"x", KEY), PUB)
        or ""
    )
    or "signature"
    in (
        raises(signing.verify_release, enc(manifest(product="Other")), signing.sign(b"x", KEY), PUB)
        or ""
    ),
)
m = manifest()
check("a newer release may be installed", raises(signing.check_manifest_for, m, "0.6.3") is None)
check(
    "the same version is refused (updates only go forward)",
    raises(signing.check_manifest_for, m, "9.9.9"),
)
check(
    "an older version is refused (a genuine old release cannot be replayed)",
    raises(signing.check_manifest_for, m, "10.0.0"),
)
check(
    "min_from makes it refuse a server that is too old",
    raises(signing.check_manifest_for, manifest(min_from="9.0.0"), "8.0.0"),
)
check(
    "min_from lets an equal-or-newer server through",
    raises(signing.check_manifest_for, manifest(min_from="9.0.0"), "9.0.0") is None,
)
check(
    "versions compare as numbers, not text",
    signing.is_newer("0.10.0", "0.9.0") and not signing.is_newer("0.9.0", "0.10.0"),
)

# the bundle
b = TMP / "b.bin"
b.write_bytes(b"x" * 100)
good = manifest(
    bundle={"name": "TOTRsrv-9.9.9.tar.gz", "sha256": signing.sha256_file(b), "size": 100}
)
check(
    "a bundle that matches the signed size and checksum passes",
    raises(signing.verify_bundle, b, good) is None,
)
b.write_bytes(b"y" + b"x" * 99)
check("one changed byte in the bundle is refused", raises(signing.verify_bundle, b, good))
b.write_bytes(b"x" * 99)
check("a bundle of the wrong size is refused", raises(signing.verify_bundle, b, good))


def make_tar(path, members):
    """members: list of (name, kind, content); kind: file | dir | symlink | hardlink | fifo | exec"""
    with tarfile.open(path, "w:gz") as tar:
        for name, kind, content in members:
            ti = tarfile.TarInfo(name)
            if kind in ("file", "exec"):
                ti.size, ti.mode = len(content), 0o755 if kind == "exec" else 0o644
                tar.addfile(ti, io.BytesIO(content))
            elif kind == "dir":
                ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(ti)
            elif kind == "symlink":
                ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(ti)
            elif kind == "hardlink":
                ti.type, ti.linkname = tarfile.LNKTYPE, "a.txt"
                tar.addfile(ti)
            elif kind == "fifo":
                ti.type = tarfile.FIFOTYPE
                tar.addfile(ti)


n = [0]


def unpack(members):
    n[0] += 1
    tar_path, dest = TMP / f"t{n[0]}.tar.gz", TMP / f"out{n[0]}"
    make_tar(tar_path, members)
    return raises(signing.safe_unpack, tar_path, dest), dest


err, dest = unpack(
    [
        ("a.txt", "file", b"hi"),
        ("sub", "dir", b""),
        ("sub/run.sh", "exec", b"#!/bin/sh\n"),
        ("./x/y.txt", "file", b"y"),
    ]
)
check(
    "a normal bundle unpacks (files, folders, a leading ./)",
    err is None and (dest / "a.txt").read_bytes() == b"hi" and (dest / "x" / "y.txt").exists(),
)
check(
    "file permissions are normalised (executable stays executable, the rest are not)",
    os.access(dest / "sub" / "run.sh", os.X_OK) and not os.access(dest / "a.txt", os.X_OK),
)
for label, members in {
    "an absolute path": [("/etc/evil", "file", b"x")],
    "a path that climbs out with ..": [("a/../../evil", "file", b"x")],
    "a leading ..": [("../evil", "file", b"x")],
    "a symbolic link": [("a.txt", "file", b"x"), ("link", "symlink", b"")],
    "a hard link": [("a.txt", "file", b"x"), ("hl", "hardlink", b"")],
    "a device-like entry": [("f", "fifo", b"")],
    "a backslash path": [("a\\b", "file", b"x")],
}.items():
    err, dest = unpack(members)
    check(
        f"unpacking refuses {label}, and leaves nothing behind",
        err is not None and not dest.exists(),
        err,
    )
signing.MAX_FILES, saved = 3, signing.MAX_FILES
err, dest = unpack([(f"f{i}", "file", b"x") for i in range(5)])
signing.MAX_FILES = saved
check("unpacking refuses too many files", err is not None and not dest.exists())
signing.MAX_UNPACKED_BYTES, saved = 10, signing.MAX_UNPACKED_BYTES
err, dest = unpack([("big", "file", b"x" * 50)])
signing.MAX_UNPACKED_BYTES = saved
check(
    "unpacking refuses a bundle that is too big once unpacked",
    err is not None and not dest.exists(),
)
(TMP / "junk.tar.gz").write_bytes(b"this is not a tar")
check(
    "a file that is not a tar is refused cleanly",
    raises(signing.safe_unpack, TMP / "junk.tar.gz", TMP / "junk-out") is not None
    and not (TMP / "junk-out").exists(),
)
(TMP / "exists").mkdir()
check(
    "unpacking into a folder that already exists is refused",
    raises(signing.safe_unpack, TMP / "t1.tar.gz", TMP / "exists"),
)

# the developer's release tool
src = TMP / "src"
for rel, text in {
    "main.py": "x",
    "version.py": 'SERVER_VERSION = "9.9.9"\n',
    "sub/mod.py": "m",
    ".env": "SECRET=1",
    "data/totr.db": "db",
    "reports/r.json": "{}",
    "certs/k.pem": "k",
    "tests/t.py": "t",
    "__pycache__/m.pyc": "c",
    "static/downloads/app.apk": "a",
    "static/app/index.html": "<html>",
    "mod.pyc": "c",
}.items():
    f = src / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
packed = release_tool.files_to_pack(src)
check(
    "the bundle holds the code and the built web app",
    {"main.py", "version.py", "sub/mod.py", "static/app/index.html"} <= set(packed),
    packed,
)
check(
    "the bundle leaves out everything that belongs to one machine (.env, data, reports, certs, downloads, tests, caches)",
    not any(
        p.startswith(("data/", "reports/", "certs/", "tests/", "__pycache__/", "static/downloads/"))
        or p in (".env", "mod.pyc")
        for p in packed
    ),
    packed,
)
out1, out2 = TMP / "rel1", TMP / "rel2"
m1 = release_tool.make_release(src, out1, KEY, "first")
m2 = release_tool.make_release(src, out2, KEY, "first")
check(
    "the same code gives the same bundle every time (reproducible)",
    m1["bundle"]["sha256"] == m2["bundle"]["sha256"],
)
check(
    "release.json and its signature check out with the public key",
    raises(
        signing.verify_release,
        (out1 / "release.json").read_bytes(),
        (out1 / "release.json.sig").read_text(),
        PUB,
    )
    is None,
)
check(
    "the manifest names the version in version.py",
    m1["release"] == "9.9.9"
    and m1["bundle"]["name"] == "TOTRsrv-9.9.9.tar.gz"
    and m1["notes"] == "first",
)

app = TMP / "builtapp"
for rel, text in {"index.html": "<html>new", "version.json": "{}", "assets/a.js": "js"}.items():
    (app / rel).parent.mkdir(parents=True, exist_ok=True)
    (app / rel).write_text(text)
out3 = TMP / "rel3"
m3 = release_tool.make_release(src, out3, KEY, "with app", app=app)
with tarfile.open(out3 / m3["bundle"]["name"]) as tf:
    names = tf.getnames()
    index_text = tf.extractfile("static/app/index.html").read()
check(
    "a release can carry a built web app: it becomes static/app and replaces the folder's own copy",
    "static/app/assets/a.js" in names
    and "static/app/version.json" in names
    and index_text == b"<html>new"
    and names.count("static/app/index.html") == 1,
    names,
)
check(
    "a release made with an app still leaves out everything that belongs to one machine and keeps the code",
    "main.py" in names and not any(n.startswith(("data/", "certs/", ".env")) for n in names),
    names,
)
check(
    "the same code and app give the same bundle every time",
    release_tool.make_release(src, TMP / "rel4", KEY, "with app", app=app)["bundle"]["sha256"]
    == m3["bundle"]["sha256"],
)
try:
    release_tool.make_release(src, TMP / "rel5", KEY, "x", app=TMP / "src" / "sub")
    refused = False
except SystemExit:
    refused = True
check("a folder that is not a built web app is refused", refused)
(TMP / "pubkey.txt").write_text(line(KEY) + "\n")
try:
    release_tool.verify(out1, TMP / "pubkey.txt")
    verified = True
except SystemExit:
    verified = False
check("the tool's own verify accepts a good release", verified)
(
    out1 / release_tool.signing.PRODUCT + "-9.9.9.tar.gz"
    if False
    else out1 / "TOTRsrv-9.9.9.tar.gz"
).write_bytes(b"tampered")
try:
    release_tool.verify(out1, TMP / "pubkey.txt")
    verified = True
except signing.UpdateError:
    verified = False
check("and refuses a bundle changed after signing", not verified)
unpacked = TMP / "unpacked"
signing.safe_unpack(out2 / "TOTRsrv-9.9.9.tar.gz", unpacked)
check(
    "a built bundle unpacks and holds what was packed",
    (unpacked / "main.py").read_text() == "x" and not (unpacked / ".env").exists(),
)
kdir = TMP / "keys"
release_tool.keygen(kdir)
kf = kdir / "signing.key"
check(
    "keygen makes an encrypted private key readable only by its owner, and a public line",
    oct(kf.stat().st_mode & 0o777) == "0o600"
    and b"ENCRYPTED" in kf.read_bytes()
    and (kdir / "update_pubkey.txt").read_text().startswith("ed25519 "),
)
loaded = release_tool.load_key(kf)
pub_line = (kdir / "update_pubkey.txt").read_text()
check(
    "the key loads with the passphrase, and signs what the public line verifies",
    raises(signing.verify, b"abc", signing.sign(b"abc", loaded), signing.parse_pubkey(pub_line))
    is None,
)
try:
    release_tool.keygen(kdir)
    replaced = True
except SystemExit:
    replaced = False
check("keygen refuses to replace an existing key", not replaced)
os.environ["TOTR_SIGNING_PASSPHRASE"] = "the wrong passphrase!"
try:
    release_tool.load_key(kf)
    opened = True
except SystemExit:
    opened = False
os.environ["TOTR_SIGNING_PASSPHRASE"] = "a long test passphrase"
check("the wrong passphrase does not open the key", not opened)

# finding and fetching a release (GitHub faked)
check("repo() accepts owner/name and nothing else", repo() == "someone/TOTR")
for bad in ("", "no-slash", "a/b/c", "../x", "a b/c", "x;rm/y"):
    os.environ["UPDATE_REPO"] = bad
    check(f"a bad UPDATE_REPO is ignored ({bad!r})", repo() == "")
os.environ["UPDATE_REPO"] = "someone/TOTR"
for url, want in {
    "https://github.com/o/r/releases/download/v1/x": True,
    "https://objects.githubusercontent.com/x": True,
    "https://release-assets.githubusercontent.com/x": True,
    "https://api.github.com/repos/o/r/releases": True,
    "https://github.com.evil.example/x": False,
    "https://evil.example/github.com": False,
    "http://github.com/x": False,
    "https://githubusercontent.com.evil.test/x": False,
    "https://10.0.0.5/x": False,
}.items():
    http.CHECK_DNS = False
    check(
        f"the update host list: {url} is {'allowed' if want else 'refused'}",
        http.host_allowed(url, SUFFIXES) == want,
    )
http.CHECK_DNS = True
check(
    "the collector's own host list is unchanged by the updater",
    not http.host_allowed("https://github.com/x") and "github.com" not in http.ALLOWED_SUFFIXES,
)

(TMP / "pubkey.txt").unlink(missing_ok=True)
check(
    "with no developer key, updates are 'not set up' and say why",
    fetch.configured()[0] is False and "developer key" in fetch.configured()[1],
)
(TMP / "pubkey.txt").write_text(line(KEY) + "\n")
check("with a key and a repository they are set up", fetch.configured() == (True, ""))

version.SERVER_VERSION = "0.6.3"
fake_dir = TMP / "gh"
rel_ok = TMP / "relgood"
src2 = TMP / "src2"
signing.safe_unpack(out2 / "TOTRsrv-9.9.9.tar.gz", src2)
(src2 / "version.py").write_text('SERVER_VERSION = "9.9.9"\n')
mf = release_tool.make_release(src2, rel_ok, KEY, "the notes")
files = {
    "release.json": (rel_ok / "release.json").read_bytes(),
    "release.json.sig": (rel_ok / "release.json.sig").read_bytes(),
    mf["bundle"]["name"]: (rel_ok / mf["bundle"]["name"]).read_bytes(),
}


def listing(*releases):
    return list(releases)


def gh_release(assets=("release.json", "release.json.sig", mf["bundle"]["name"]), **flags):
    return {
        "tag_name": "server-v9.9.9",
        **flags,
        "assets": [
            {
                "name": n,
                "browser_download_url": f"https://github.com/someone/TOTR/releases/download/x/{n}",
                "size": 1,
            }
            for n in assets
        ],
    }


class FakeNet:
    def __init__(self, releases, blobs):
        self.releases, self.blobs, self.calls = releases, blobs, []

    def get_json(self, url, params=None, suffixes=None, headers=None):
        self.calls.append(url)
        assert suffixes == SUFFIXES, "the updater must use its own host list"
        return self.releases

    def download(self, url, dest, max_bytes, progress=None, referer=None, suffixes=None):
        assert suffixes == SUFFIXES
        name = url.rsplit("/", 1)[1]
        data = self.blobs[name]
        if len(data) > max_bytes:
            raise http.FetchError("file is larger than the size limit")
        pathlib.Path(dest).write_bytes(data)
        return len(data)


def with_net(releases, blobs=None):
    net = FakeNet(releases, files if blobs is None else blobs)
    http.get_json, http.download = net.get_json, net.download
    return net


with_net([gh_release()])
res = fetch.check()
check(
    "check() finds the newer signed release and reports it",
    res["configured"]
    and res["latest"] == "9.9.9"
    and res["newer"] is True
    and res["notes"] == "the notes",
    res,
)
version.SERVER_VERSION = "9.9.9"
res = fetch.check()
check(
    "check() says nothing is newer when this server is current",
    res["latest"] == "9.9.9" and res["newer"] is False and "problem" not in res,
    res,
)
version.SERVER_VERSION = "10.0.0"
check("check() does not offer an older release", fetch.check()["newer"] is False)
version.SERVER_VERSION = "0.6.3"
with_net([gh_release(draft=True), gh_release(prerelease=True)])
check("drafts and pre-releases are skipped", fetch.check()["latest"] is None)
with_net([gh_release(assets=("release.json",))])
check("a release without a signature file is skipped", fetch.check()["latest"] is None)
with_net([])
check(
    "no releases at all is not an error",
    fetch.check()["latest"] is None and "problem" not in fetch.check(),
)
bad_sig = dict(
    files, **{"release.json.sig": (signing.sign(b"something else", KEY) + "\n").encode()}
)
with_net([gh_release()], bad_sig)
res = fetch.check()
check(
    "a release whose signature does not match is reported as a problem, never as an update",
    res.get("newer") is not True and "signature" in res.get("problem", ""),
    res,
)
other_sig = dict(
    files, **{"release.json.sig": (signing.sign(files["release.json"], OTHER) + "\n").encode()}
)
with_net([gh_release()], other_sig)
res = fetch.check()
check(
    "a release signed by somebody else is refused",
    res.get("newer") is not True and "developer's key" in res.get("problem", ""),
    res,
)

(updates_dir() / "pending").mkdir(parents=True, exist_ok=True)
import shutil

shutil.rmtree(updates_dir() / "pending")
with_net([gh_release()])
st = fetch.stage("tester")
pend = updates_dir() / "pending"
check(
    "stage() downloads, verifies and files the request",
    st == {"release": "9.9.9", "state": "waiting"}
    and (pend / "release.json").exists()
    and (pend / "release.json.sig").exists()
    and (pend / mf["bundle"]["name"]).exists(),
)
req = json.loads((updates_dir() / "request.json").read_text())
check(
    "the request names the release and who asked",
    req["release"] == "9.9.9" and req["requested_by"] == "tester",
)
check(
    "what was filed verifies again from the files alone (as the update service will)",
    raises(
        signing.verify_bundle,
        pend / mf["bundle"]["name"],
        signing.verify_release(
            (pend / "release.json").read_bytes(), (pend / "release.json.sig").read_text(), PUB
        ),
    )
    is None,
)
check("a second request while one is waiting is refused", raises(fetch.stage, "again") is not None)
check("the app says it is waiting", fetch.app_status()["stage"] == "waiting")
(updates_dir() / "request.json").unlink()
shutil.rmtree(pend)
corrupt = dict(files, **{mf["bundle"]["name"]: files[mf["bundle"]["name"]][:-1] + b"X"})
with_net([gh_release()], corrupt)
err = raises(fetch.stage, "t")
check(
    "a corrupted download is refused and nothing is filed",
    err is not None
    and not (updates_dir() / "request.json").exists()
    and not (updates_dir() / "pending").exists()
    and not list(updates_dir().glob("incoming-*")),
    err,
)
check("the failure is shown to the admin", fetch.app_status()["stage"] == "failed")
with_net([gh_release(assets=("release.json", "release.json.sig"))])
check(
    "a release that lists a bundle but does not attach it is refused",
    raises(fetch.stage, "t") is not None,
)
version.SERVER_VERSION = "9.9.9"
with_net([gh_release()])
check(
    "stage() will not fetch a release that is not newer",
    raises(fetch.stage, "t") is not None and not (updates_dir() / "request.json").exists(),
)
version.SERVER_VERSION = "0.6.3"

# who may use the API
from auth import web as auth_web  # noqa: E402

check(
    "the update API is for the admin role only (the gate's default for any /api/admin path with no permission listed)",
    all(
        auth_web.required_permission(p) == "admin"
        for p in ("/api/admin/updates", "/api/admin/updates/check", "/api/admin/updates/install")
    ),
)

shutil.rmtree(TMP, ignore_errors=True)
finish()
