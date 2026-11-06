"""The update service (deploy/apply-update.py): installing a verified release, bringing the database
forward, switching versions, and going back when the new version does not come up. Everything runs
in a throwaway folder that looks like an installed server (releases/, current, state/), with no
root, no systemd and no network: the service calls and the health check are stand-ins. The release
trees are built from the real code (migrations, db.py, migrate.py ...)."""

import base64
import importlib.util
import io
import json
import pathlib
import shutil
import sqlite3
import sys
import tarfile
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
sys.path.insert(0, str(PF / "tools"))
from check import check, finish

try:
    import cryptography  # noqa: F401
except ImportError:
    import glob

    for p in glob.glob("/usr/lib/python3*/site-packages"):
        sys.path.append(p)
    try:
        import cryptography  # noqa: F401,F811
    except ImportError:
        print(
            "skip  the 'cryptography' package is not installed here (pip install -r requirements.txt): the update service was not checked"
        )
        raise SystemExit(0)

import release_tool  # noqa: E402
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from updates import signing  # noqa: E402

spec = importlib.util.spec_from_file_location("apply_update", PF / "deploy" / "apply-update.py")
au = importlib.util.module_from_spec(spec)
spec.loader.exec_module(au)
au.HEALTH_SECONDS = 0

KEY, OTHER = ed25519.Ed25519PrivateKey.generate(), ed25519.Ed25519PrivateKey.generate()
pub_line = lambda k: (
    "ed25519 "
    + base64.b64encode(
        k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    + "\n"
)
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="totr-apply-"))
COPY_FILES = ["paths.py", "db.py", "localdb.py", "schemaver.py", "migrate.py", "requirements.txt"]
quiet = lambda *_: None


def make_tree(
    dest: pathlib.Path,
    version: str,
    *,
    extra_migration=False,
    main=True,
    key=KEY,
    version_in_file=None,
):
    """A server code folder with the real migrations / database code, at `version`; optionally with
    one more upgrade (0005) for both databases."""
    dest.mkdir(parents=True)
    for f in COPY_FILES:
        shutil.copy(PF / f, dest / f)
    (dest / "version.py").write_text(f'SERVER_VERSION = "{version_in_file or version}"\n')
    (dest / "update_pubkey.txt").write_text(pub_line(key))
    if main:
        (dest / "main.py").write_text("# stand-in\n")
    for sub in ("deploy", "migrations/sqlite", "migrations/postgres", "sql/sqlite"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(PF / "deploy" / "db-state.py", dest / "deploy" / "db-state.py")
    for engine in ("sqlite", "postgres"):
        for f in (PF / "migrations" / engine).glob("*.sql"):
            shutil.copy(f, dest / "migrations" / engine / f.name)
    shutil.copy(
        PF / "sql" / "sqlite" / "totr_template.db", dest / "sql" / "sqlite" / "totr_template.db"
    )
    if extra_migration:
        (dest / "migrations" / "sqlite" / "0005_update_marker.sql").write_text(
            "CREATE TABLE totr_update_marker (n INTEGER);\nINSERT INTO totr_update_marker VALUES (1);\n"
        )
        (dest / "migrations" / "postgres" / "0005_update_marker.sql").write_text(
            "CREATE TABLE IF NOT EXISTS totr.update_marker (n integer);\n"
        )
    return dest


class Install:
    """A fake installed server at version 0.6.3 with a SQLite database, plus helpers to file a
    release the way the web app would."""

    count = 0

    def __init__(self):
        Install.count += 1
        self.base = ROOT / f"case{Install.count}"
        self.prefix = self.base / "opt"
        make_tree(self.prefix / "releases" / "0.6.3", "0.6.3")
        (self.prefix / "current").symlink_to(self.prefix / "releases" / "0.6.3")
        self.state = self.prefix / "state"
        (self.state / "data").mkdir(parents=True)
        (self.state / ".env").write_text("DB_ENGINE=sqlite\nPORT=8080\n")
        shutil.copy(PF / "sql" / "sqlite" / "totr_template.db", self.state / "data" / "totr.db")
        self.upd = self.state / "updates"
        self.upd.mkdir()
        self.healthy = True

    def release(self, version, *, extra_migration=False, key=KEY, **tree):
        src = make_tree(
            self.base / f"src-{version}-{Install.count}",
            version,
            extra_migration=extra_migration,
            key=key,
            **tree,
        )
        out = self.base / f"rel-{version}"
        shutil.rmtree(out, ignore_errors=True)
        release_tool.make_release(src, out, key, f"notes for {version}")
        return out

    def file(self, rel_dir: pathlib.Path, name=None):
        shutil.rmtree(self.upd / "pending", ignore_errors=True)
        shutil.copytree(rel_dir, self.upd / "pending")
        (self.upd / "request.json").write_text(
            json.dumps({"release": name or "x", "requested_by": "test"})
        )

    def updater(self, **kw):
        kw.setdefault("health", lambda: self.healthy)
        return au.Updater(
            self.prefix,
            service=False,
            skip_pip=True,
            python=sys.executable,
            sleep=lambda s: None,
            say=quiet,
            **kw,
        )

    def status(self):
        return json.loads((self.upd / "status.json").read_text())

    def current(self):
        return (self.prefix / "current").resolve().name

    def db(self):
        return sqlite3.connect(self.state / "data" / "totr.db")


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# the happy path, no database change
i = Install()
i.file(i.release("0.7.0"))
res = i.updater().apply()
st = i.status()
check(
    "an update with no database change installs and switches to the new release",
    res == "done"
    and i.current() == "0.7.0"
    and st["stage"] == "done"
    and st["from"] == "0.6.3"
    and st["release"] == "0.7.0",
    st,
)
check(
    "the old release is kept (to go back to), and the downloaded copy is cleared",
    (i.prefix / "releases" / "0.6.3").is_dir()
    and not (i.upd / "pending").exists()
    and not (i.upd / "request.json").exists(),
)
check(
    "the new release is a real unpacked tree",
    (i.prefix / "releases" / "0.7.0" / "main.py").is_file()
    and (i.prefix / "releases" / "0.7.0" / "version.py").is_file(),
)
check(
    "the state (settings, database) was not touched",
    (i.state / ".env").read_text().startswith("DB_ENGINE=sqlite")
    and not (i.upd / "backups").exists(),
)
check("with nothing waiting, the service does nothing", i.updater().apply() == "idle")

# with a database upgrade
i = Install()
i.file(i.release("0.7.1", extra_migration=True))
res = i.updater().apply()
c = i.db()
check(
    "an update with a database upgrade backs up, upgrades, and switches",
    res == "done" and i.current() == "0.7.1" and "totr_update_marker" in tables(c),
    i.status(),
)
check(
    "the upgrade is recorded in the database's own version list",
    [r[0] for r in c.execute("SELECT version FROM totr_schema_version ORDER BY version")]
    == [1, 2, 3, 4, 5],
)
check(
    "a backup of the database from before the upgrade exists and does not have it",
    len(list((i.upd / "backups").glob("totr-0.6.3-*.db"))) == 1
    and "totr_update_marker"
    not in tables(sqlite3.connect(next((i.upd / "backups").glob("totr-0.6.3-*.db")))),
)
c.close()

# the new version does not come up: go back
i = Install()
i.healthy = False
i.file(i.release("0.7.1", extra_migration=True))
res = i.updater().apply()
c = i.db()
check(
    "if the new version does not answer, the old one is put back",
    res == "rolled_back" and i.current() == "0.6.3",
    i.status(),
)
check(
    "the database is restored too (an old version refuses a database that is newer than itself)",
    "totr_update_marker" not in tables(c)
    and [r[0] for r in c.execute("SELECT version FROM totr_schema_version ORDER BY version")]
    == [1, 2, 3, 4],
)
check(
    "the failed release is set aside, not deleted (for finding out why)",
    not (i.prefix / "releases" / "0.7.1").exists()
    and list((i.prefix / "releases").glob("0.7.1.failed-*")),
)
check(
    "the status tells the admin what happened",
    i.status()["stage"] == "rolled_back"
    and "0.6.3" in i.status()["message"]
    and "0.7.1" in i.status()["message"],
    i.status(),
)
c.close()

i = Install()
i.healthy = False
i.file(i.release("0.7.0"))
check(
    "a rollback with no database change needs no restore and still goes back",
    i.updater().apply() == "rolled_back" and i.current() == "0.6.3",
)


# things that must be refused
def refused(label, setup, expect=None):
    i = Install()
    setup(i)
    res = i.updater().apply()
    st = i.status()
    ok = (
        res == "failed"
        and i.current() == "0.6.3"
        and st["stage"] == "failed"
        and (expect is None or expect in st["message"])
    )
    check(
        f"refused: {label}",
        ok and not (i.prefix / "releases" / "0.7.0").exists(),
        (res, st.get("message")),
    )
    return i


def tamper(path, fn):
    path.write_bytes(fn(path.read_bytes()))


def s_manifest(i):
    r = i.release("0.7.0")
    tamper(r / "release.json", lambda b: b.replace(b"notes for 0.7.0", b"notes for 0.7.1"))
    i.file(r)


def s_bundle(i):
    r = i.release("0.7.0")
    tamper(r / "TOTRsrv-0.7.0.tar.gz", lambda b: b[:-1] + bytes([b[-1] ^ 1]))
    i.file(r)


refused("a manifest changed after signing", s_manifest, "signature")
refused("a bundle changed after signing", s_bundle)
refused(
    "a release signed by somebody else's key",
    lambda i: i.file(i.release("0.7.0", key=OTHER)),
    "signature",
)
refused("the same version again", lambda i: i.file(i.release("0.6.3")), "forward")
refused("an older genuine release (replay)", lambda i: i.file(i.release("0.5.0")), "forward")
refused(
    "a request with nothing downloaded",
    lambda i: (i.upd / "request.json").write_text("{}"),
    "download is missing",
)
refused(
    "a bundle that is not a server (no main.py)",
    lambda i: i.file(i.release("0.7.0", main=False)),
    "not a TOTR server",
)


def s_mismatch(i):
    """Signed as 0.7.0, but the code inside says 0.7.5 (a signed manifest pasted onto a different
    bundle)."""
    r = i.release("0.7.5")
    bundle = r / "TOTRsrv-0.7.0.tar.gz"
    (r / "TOTRsrv-0.7.5.tar.gz").rename(bundle)
    manifest = {
        "format": "totr-release",
        "format_version": 1,
        "product": "TOTRsrv",
        "release": "0.7.0",
        "created": "now",
        "bundle": {
            "name": bundle.name,
            "sha256": signing.sha256_file(bundle),
            "size": bundle.stat().st_size,
        },
    }
    data = json.dumps(manifest).encode()
    (r / "release.json").write_bytes(data)
    (r / "release.json.sig").write_text(signing.sign(data, KEY))
    i.file(r)


refused(
    "a bundle whose version file disagrees with the signed release", s_mismatch, "version inside"
)


def s_nokey(i):
    (i.prefix / "releases" / "0.6.3" / "update_pubkey.txt").write_text("# no key\n")
    i.file(i.release("0.7.0"))


refused("any update on a server with no developer key", s_nokey, "no developer key")


def s_evil(i):
    """Genuinely signed, but the archive tries to escape: the unpacking rules are a second wall."""
    out = i.base / "evil"
    out.mkdir()
    bundle = out / "TOTRsrv-0.7.0.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        ti = tarfile.TarInfo("../../escaped.txt")
        ti.size = 1
        tar.addfile(ti, io.BytesIO(b"x"))
    manifest = {
        "format": "totr-release",
        "format_version": 1,
        "product": "TOTRsrv",
        "release": "0.7.0",
        "created": "now",
        "bundle": {
            "name": bundle.name,
            "sha256": signing.sha256_file(bundle),
            "size": bundle.stat().st_size,
        },
    }
    data = json.dumps(manifest).encode()
    (out / "release.json").write_bytes(data)
    (out / "release.json.sig").write_text(signing.sign(data, KEY))
    i.file(out)


i = refused(
    "a genuinely signed bundle that tries to unpack outside its folder", s_evil, "unsafe path"
)
check(
    "and nothing was written outside",
    not (i.prefix / "escaped.txt").exists() and not (i.prefix.parent / "escaped.txt").exists(),
)

i = Install()
i.file(i.release("0.7.0"))
real = shutil.disk_usage
shutil.disk_usage = lambda p: type("U", (), {"free": 1024})()
res = i.updater().apply()
shutil.disk_usage = real
check(
    "refused: not enough disk space",
    res == "failed" and "disk space" in i.status()["message"] and i.current() == "0.6.3",
)

# the database decides
i = Install()
i.file(i.release("0.7.1", extra_migration=True))
res = i.updater(
    probe=lambda d: {"engine": "postgres", "state": "old", "current": 4, "latest": 5}
).apply()
st = i.status()
check(
    "PostgreSQL that needs new upgrades is NOT touched: the release is staged and the service stops, asking for a database administrator",
    res == "needs_db_admin"
    and i.current() == "0.6.3"
    and st["stage"] == "needs_db_admin"
    and "--continue" in st["command"],
    st,
)
check(
    "the release stays staged and the download is kept for the second step",
    (i.prefix / "releases" / "0.7.1" / ".staged").exists() and (i.upd / "pending").exists(),
)
res = i.updater(
    probe=lambda d: {"engine": "postgres", "state": "old", "current": 4, "latest": 5}
).apply(resume=True)
check(
    "--continue finishes it (here the stand-in database is SQLite, so the upgrade runs for real)",
    res == "done" and i.current() == "0.7.1" and "totr_update_marker" in tables(i.db()),
    i.status(),
)

i = Install()
i.file(i.release("0.7.0"))
res = i.updater(
    probe=lambda d: {"engine": "sqlite", "state": "newer", "current": 9, "latest": 4}
).apply()
check(
    "a database newer than the release is refused",
    res == "failed" and i.current() == "0.6.3" and "newer" in i.status()["message"],
)

i = Install()
i.file(i.release("0.7.0"))
res = i.updater(
    probe=lambda d: {"engine": "postgres", "state": "unversioned", "current": 0, "latest": 4}
).apply()
check(
    "a hand-made PostgreSQL database that has not been adopted still updates (the server only warns)",
    res == "done" and i.current() == "0.7.0",
)

# housekeeping
i = Install()
for v in ("0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0"):
    (i.prefix / "releases" / v).mkdir()
(i.prefix / "releases" / "0.6.9.failed-20260101-000000").mkdir()
i.file(i.release("0.7.0"))
i.updater().apply()
left = sorted(d.name for d in (i.prefix / "releases").iterdir())
check(
    "only the newest releases are kept, the one before this update is always kept, and failed ones are left alone",
    left == ["0.5.0", "0.6.3", "0.6.9.failed-20260101-000000", "0.7.0"],
    left,
)

# one at a time
i = Install()
i.file(i.release("0.7.0"))
import fcntl

lock = open(i.upd / ".lock", "w")
fcntl.flock(lock, fcntl.LOCK_EX)
rc = au.main(["--prefix", str(i.prefix), "--no-service"])
fcntl.flock(lock, fcntl.LOCK_UN)
check(
    "a second run while one is in progress does nothing",
    rc == 0 and i.current() == "0.6.3" and (i.upd / "request.json").exists(),
)

# the status file
check(
    "the status file is readable by the web app's user",
    oct((i.upd / "status.json").stat().st_mode & 0o777) in ("0o644",)
    if (i.upd / "status.json").exists()
    else True,
)
i = Install()
i.file(i.release("0.7.0"))
i.updater().apply()
check(
    "a finished update's status file is world-readable and has no secrets",
    oct((i.upd / "status.json").stat().st_mode & 0o777) == "0o644"
    and "password" not in (i.upd / "status.json").read_text().lower(),
)

shutil.rmtree(ROOT, ignore_errors=True)
finish()
