#!/usr/bin/env python3
"""Installs a verified server update. Run by systemd as root (totr-update.service, started by
totr-update.path when the web app files a request), never by the web app itself: the web app runs as
a locked-down user that cannot change its own code, which is the point.

The web app downloads a release into <state>/updates/pending/ and writes
<state>/updates/request.json. This script then, by itself and without trusting anything the web app
says it checked:
  1. verifies the signature (with the public key of the release that is installed), the checksum,
     the version (only forward) and the disk space
  2. unpacks the bundle into <prefix>/releases/<version>/ (plain files only, nothing outside that
     folder) and builds its Python environment
  3. looks at the database: SQLite is backed up and brought forward by the new release's migrations;
     a PostgreSQL database that needs new migrations is NOT
     touched here (the admin login is never stored): the release is staged and the script stops with "needs_db_admin" until
         sudo <prefix>/current/deploy/apply-update.py --continue        (it asks for a database login that may create tables, used for that run only)
  4. swaps <prefix>/current to the new release, restarts the service, and waits for /health
  5. if the new version does not come up: swaps back, restores the SQLite backup if migrations had
     run, restarts the old version, and says so
It writes <state>/updates/status.json after every step (the admin page reads it). Only one run at a
time. The newest 3 releases are kept.

    apply-update.py [--prefix /opt/totr] [--continue]
"""

import argparse
import fcntl
import json
import os
import pathlib
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime

HERE = pathlib.Path(__file__).resolve().parent.parent  # the release this script belongs to
sys.path.insert(0, str(HERE))
from updates import signing  # noqa: E402

KEEP_RELEASES = 3
KEEP_BACKUPS = 5
HEALTH_SECONDS = 90


class Stop(Exception):
    """The update cannot go on. `stage` is what the status page shows: failed, or needs_db_admin."""

    def __init__(self, message, stage="failed"):
        super().__init__(message)
        self.stage = stage


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class Updater:
    def __init__(
        self,
        prefix,
        *,
        service=True,
        skip_pip=False,
        python=None,
        health=None,
        probe=None,
        sleep=time.sleep,
        say=print,
        pubkey=None,
    ):
        self.prefix = pathlib.Path(prefix)
        self.state = self.prefix / "state"
        self.upd = self.state / "updates"
        self.releases = self.prefix / "releases"
        self.current = self.prefix / "current"
        self.service, self.skip_pip, self.sleep, self.say = service, skip_pip, sleep, say
        self.python_override = python
        self._health, self._probe = health, probe
        self.pubkey_file = pathlib.Path(pubkey) if pubkey else self.current / "update_pubkey.txt"
        self.log: list = []

    def note(self, text: str):
        self.log.append(text)
        self.log = self.log[-30:]
        self.say(text)

    def status(self, stage: str, **more):
        self.upd.mkdir(parents=True, exist_ok=True)
        tmp = self.upd / "status.json.part"
        tmp.write_text(
            json.dumps({"stage": stage, "at": now(), "log": self.log[-12:], **more}, indent=1),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.upd / "status.json")

    def env(self) -> dict:
        env = dict(os.environ)
        try:
            env.update(parse_env((self.state / ".env").read_text(encoding="utf-8")))
        except OSError:
            pass
        env["TOTR_STATE_DIR"] = str(self.state)
        return env

    def run(self, cmd, **kw):
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
        if r.returncode != 0:
            raise Stop(
                f"`{' '.join(str(c) for c in cmd[:3])} ...` failed: {(r.stderr or r.stdout).strip()[-600:]}"
            )
        return r

    def installed_version(self) -> str:
        m = re.search(
            r'^SERVER_VERSION\s*=\s*"([^"]+)"',
            (self.current / "version.py").read_text(encoding="utf-8"),
            re.M,
        )
        if not m:
            raise Stop("cannot read the installed version")
        return m.group(1)

    def new_python(self, release_dir) -> str:
        return self.python_override or str(pathlib.Path(release_dir) / ".venv" / "bin" / "python")

    # 1. verify
    def verify_pending(self, installed: str) -> dict:
        pending = self.upd / "pending"
        try:
            data = (pending / "release.json").read_bytes()
            sig = (pending / "release.json.sig").read_text(encoding="ascii")
        except OSError:
            raise Stop("no release is waiting (the download is missing)")
        try:
            key = signing.load_pubkey(self.pubkey_file)
            if key is None:
                raise Stop("this server has no developer key, so it cannot install updates")
            manifest = signing.verify_release(data, sig, key)
            signing.check_manifest_for(manifest, installed)
            bundle = pending / manifest["bundle"]["name"]
            signing.verify_bundle(bundle, manifest)
        except signing.UpdateError as e:
            raise Stop(str(e))
        free = shutil.disk_usage(self.prefix).free
        need = manifest["bundle"]["size"] * 4 + 300 * 1024 * 1024
        if free < need:
            raise Stop(
                f"not enough free disk space ({free // 2**20} MB free, about {need // 2**20} MB needed)"
            )
        return manifest

    # 2. unpack and prepare
    def stage_release(self, manifest: dict) -> pathlib.Path:
        version = manifest["release"]
        dest = self.releases / version
        marker = dest / ".staged"
        if marker.exists() and marker.read_text().strip() == manifest["bundle"]["sha256"]:
            self.note(f"release {version} is already unpacked")
            return dest
        if dest.exists():
            if dest.resolve() == self.current.resolve():
                raise Stop(f"{version} is the installed release")
            shutil.rmtree(dest)
        self.note(f"unpacking {manifest['bundle']['name']}")
        try:
            signing.safe_unpack(self.upd / "pending" / manifest["bundle"]["name"], dest)
        except signing.UpdateError as e:
            raise Stop(str(e))
        if not (dest / "main.py").is_file() or not (dest / "version.py").is_file():
            shutil.rmtree(dest, ignore_errors=True)
            raise Stop("the bundle is not a TOTR server (main.py or version.py is missing)")
        if (dest / "version.py").read_text(encoding="utf-8").count(f'"{version}"') < 1:
            shutil.rmtree(dest, ignore_errors=True)
            raise Stop("the version inside the bundle is not the version the signed release names")
        if not self.skip_pip:
            self.note("making the Python environment (needs the internet; a minute)")
            self.run([sys.executable, "-m", "venv", dest / ".venv"])
            self.run(
                [
                    dest / ".venv" / "bin" / "python",
                    "-m",
                    "pip",
                    "install",
                    "--quiet",
                    "--disable-pip-version-check",
                    "-r",
                    dest / "requirements.txt",
                ]
            )
        marker.write_text(manifest["bundle"]["sha256"], encoding="utf-8")
        return dest

    # 3. the database
    def probe_db(self, release_dir) -> dict:
        if self._probe:
            return self._probe(release_dir)
        r = self.run(
            [self.new_python(release_dir), release_dir / "deploy" / "db-state.py"],
            env=self.env(),
            cwd=release_dir,
        )
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise Stop(
                "could not read the database version: " + (r.stderr or r.stdout).strip()[-300:]
            )

    def sqlite_path(self) -> pathlib.Path:
        env = self.env()
        return pathlib.Path(env.get("TOTR_DATA_DIR") or self.state / "data") / "totr.db"

    def backup_sqlite(self, old_version: str):
        src = self.sqlite_path()
        if not src.exists():
            return None
        dest = self.upd / "backups" / f"totr-{old_version}-{time.strftime('%Y%m%d-%H%M%S')}.db"
        dest.parent.mkdir(parents=True, exist_ok=True)
        a, b = sqlite3.connect(f"file:{src}?mode=ro", uri=True), sqlite3.connect(dest)
        try:
            a.backup(b)
        finally:
            b.close()
            a.close()
        for old in sorted(dest.parent.glob("totr-*.db"))[:-KEEP_BACKUPS]:
            old.unlink(missing_ok=True)
        self.note(f"backed up the database to {dest.name}")
        return dest

    def restore_sqlite(self, backup):
        dst = self.sqlite_path()
        for suffix in ("-wal", "-shm"):
            pathlib.Path(str(dst) + suffix).unlink(missing_ok=True)
        shutil.copyfile(backup, dst)
        try:
            shutil.chown(dst, *self.owner_of(dst.parent))
        except (OSError, LookupError, PermissionError):
            pass

    @staticmethod
    def owner_of(path):
        st = os.stat(path)
        return st.st_uid, st.st_gid

    # 4. swap, restart, look
    def point_current_at(self, release_dir):
        tmp = self.prefix / "current.new"
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(pathlib.Path(release_dir))
        os.replace(tmp, self.current)

    def systemctl(self, *args):
        if self.service:
            self.run(["systemctl", *args])

    def health(self) -> bool:
        if self._health:
            return self._health()
        env = self.env()
        if env.get("TLS_CERT"):
            url = f"https://127.0.0.1:{env.get('TLS_PORT') or '8443'}/health"
            ca = self.state / "certs" / "totr-ca.pem"
            ctx = (
                ssl.create_default_context(cafile=str(ca))
                if ca.exists()
                else ssl.create_default_context()
            )
            ctx.check_hostname = False
        else:
            url, ctx = f"http://127.0.0.1:{env.get('PORT') or '8080'}/health", None
        try:
            with urllib.request.urlopen(url, context=ctx, timeout=3) as r:
                return r.status == 200 and b"ok" in r.read()
        except Exception:
            return False

    def wait_healthy(self) -> bool:
        deadline = time.monotonic() + HEALTH_SECONDS
        while True:
            if self.health():
                return True
            if time.monotonic() >= deadline:
                return False
            self.sleep(1)

    def apply(self, resume: bool = False) -> str:
        """Returns the final stage. Never raises for an expected problem: it is written to status.json."""
        request = self.upd / "request.json"
        installed = self.installed_version()
        try:
            self.log = []
            if not resume:
                if not request.exists():
                    return "idle"
                request.unlink()
            self.status("working", step="checking", **{"from": installed})
            self.note(f"checking the downloaded release (installed: {installed})")
            manifest = self.verify_pending(installed)
            version = manifest["release"]
            self.status("working", step="unpacking", release=version, **{"from": installed})
            new_dir = self.stage_release(manifest)

            self.status("working", step="database", release=version, **{"from": installed})
            db = self.probe_db(new_dir)
            self.note(
                f"database: {db.get('engine')} is {db.get('state')} (version {db.get('current')}, the new release needs {db.get('latest')})"
            )
            if db.get("state") == "newer":
                raise Stop("the database is newer than this release; refusing to install it")
            backup, migrated = None, False
            if db.get("state") == "old":
                if db.get("engine") == "postgres" and not resume:
                    cmd = f"sudo {self.current}/deploy/apply-update.py --prefix {self.prefix} --continue"
                    self.status(
                        "needs_db_admin",
                        release=version,
                        command=cmd,
                        message="This update changes the database layout. Run the command below as an administrator: it asks for a database login that may create tables (used once, never stored).",
                        **{"from": installed},
                    )
                    self.note("waiting for a database administrator")
                    return "needs_db_admin"
                if db.get("engine") == "sqlite":
                    backup = self.backup_sqlite(installed)

            previous = self.current.resolve()
            if db.get("state") == "old":
                self.status("working", step="migrating", release=version, **{"from": installed})
                self.note("bringing the database forward")
                self.run(
                    [self.new_python(new_dir), new_dir / "migrate.py", "up"],
                    env=self.env(),
                    cwd=new_dir,
                    stdin=None if resume else subprocess.DEVNULL,
                )
                migrated = True

            self.status("working", step="switching", release=version, **{"from": installed})
            self.point_current_at(new_dir)
            self.note(f"switched to {version}; restarting")
            try:
                self.systemctl("restart", "totr")
                healthy = self.wait_healthy()
            except Stop as e:
                healthy, why = False, str(e)
            else:
                why = "the new version did not answer /health in time"
            if not healthy:
                return self.roll_back(
                    previous, new_dir, version, installed, backup if migrated else None, why
                )

            self.note(f"{version} is running")
            shutil.rmtree(self.upd / "pending", ignore_errors=True)
            self.prune(keep=(new_dir.name, previous.name))
            self.status("done", release=version, **{"from": installed})
            return "done"
        except Stop as e:
            self.note(str(e))
            self.status(e.stage, message=str(e), **{"from": installed})
            return e.stage
        except (
            Exception
        ) as e:  # nothing unexpected may leave the server half-updated without a word
            self.note(f"unexpected: {type(e).__name__}: {e}")
            self.status(
                "failed",
                message=f"unexpected error: {type(e).__name__}: {e}",
                **{"from": installed},
            )
            return "failed"

    def roll_back(self, previous, failed_dir, version, installed, backup, why) -> str:
        self.note(f"{version} did not start ({why}); going back to {installed}")
        try:
            self.systemctl("stop", "totr")
        except Stop:
            pass
        if backup:
            self.restore_sqlite(backup)
            self.note("restored the database from the backup")
        self.point_current_at(previous)
        broken = failed_dir.with_name(f"{failed_dir.name}.failed-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            failed_dir.rename(broken)
        except OSError:
            pass
        try:
            self.systemctl("restart", "totr")
            back = self.wait_healthy()
        except Stop:
            back = False
        msg = f"{version} did not start: {why}. " + (
            f"Back on {installed}, which is running."
            if back
            else f"WARNING: {installed} is not answering either. Look at: journalctl -u totr -n 50"
        )
        self.status("rolled_back", release=version, message=msg, **{"from": installed})
        return "rolled_back"

    def prune(self, keep):
        # releases that failed are named x.y.z.failed-...: left alone
        dirs = sorted(
            (
                d
                for d in self.releases.iterdir()
                if d.is_dir() and re.fullmatch(r"\d+\.\d+\.\d+", d.name)
            ),
            key=lambda d: signing.version_tuple(d.name),
            reverse=True,
        )
        for d in dirs[KEEP_RELEASES:]:
            if d.name not in keep:
                shutil.rmtree(d, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--prefix",
        default=str(HERE.parent.parent),
        help="where TOTR is installed (default: found from this script)",
    )
    ap.add_argument(
        "--continue",
        dest="resume",
        action="store_true",
        help="finish an update that stopped at needs_db_admin",
    )
    ap.add_argument(
        "--no-service",
        action="store_true",
        help="do not stop or restart the systemd service (testing)",
    )
    a = ap.parse_args(argv)
    if os.geteuid() != 0 and not a.no_service:
        print("Run this as root (sudo): it replaces the installed code and restarts the service.")
        return 2
    u = Updater(a.prefix, service=not a.no_service)
    u.upd.mkdir(parents=True, exist_ok=True)
    lock = open(u.upd / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another update run is in progress")
        return 0
    stage = u.apply(resume=a.resume)
    return 0 if stage in ("done", "idle", "needs_db_admin") else 1


if __name__ == "__main__":
    sys.exit(main())
