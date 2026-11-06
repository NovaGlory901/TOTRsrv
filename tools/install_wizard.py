#!/usr/bin/env python3
"""Installs (or repairs) a TOTR server. Start it with deploy/install.sh. Standard library only: it
runs before the server's own Python environment exists.

    sudo ./deploy/install.sh            asks plain questions, then does everything below
    sudo ./deploy/install.sh --yes      no questions: SQLite, the defaults for everything else
    sudo ./deploy/install.sh --help

What it does, in order (each step can be run again; it never changes a setting or secret that
already exists):
  1. checks the machine: Python version, free disk space, openssl, systemd, that the port is free
  2. lays out the install:   <prefix>/releases/<version>/   the code (read-only for the service)
                             <prefix>/current              a link to the release in use (an update swaps this link)
                             <prefix>/state/               settings (.env), database, reports, certificates, app installers: survives updates
  3. makes the Python environment and installs the requirements for this release
  4. writes state/.env (mode 600): database, folders, ports, HTTPS
  5. database: the built-in SQLite file (default) or PostgreSQL (migrations + one least-privilege
     role per job with random passwords; the admin login you
     give is used for this run and is never stored)
  6. HTTPS: a certificate authority of this server's own and a server certificate for its address
     (or your own certificate, or plain HTTP)
  7. the first admin: prints a one-time sign-in link (no password is typed here)
  8. the service: a 'totr' user and a systemd unit (restarts by itself, starts at boot), then checks
     that it answers
"""

import argparse
import getpass
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request

SRC = pathlib.Path(__file__).resolve().parent.parent  # the TOTRsrv folder this script came with
MIN_PYTHON = (3, 11)
MIN_FREE_MB = 600
ROLE_PURPOSES = ("read", "write", "maint", "auth", "mixtape")
ROLE_VARS = {
    "read": ("DB_USER", "DB_PASSWORD"),
    "write": ("DB_WRITE_USER", "DB_WRITE_PASSWORD"),
    "maint": ("DB_MAINT_USER", "DB_MAINT_PASSWORD"),
    "auth": ("DB_AUTH_USER", "DB_AUTH_PASSWORD"),
    "mixtape": ("DB_MIXTAPE_USER", "DB_MIXTAPE_PASSWORD"),
}
# Never copied into a release: per-machine state, build leftovers, the tests.
COPY_SKIP_NAMES = {
    ".venv",
    "__pycache__",
    "tests",
    ".env",
    "data",
    "reports",
    "certs",
    "downloads",
    "node_modules",
    ".git",
    ".pytest_cache",
}


class InstallError(Exception):
    """Something stopped the install; the message says what to do."""


def server_version(src: pathlib.Path = SRC) -> str:
    m = re.search(
        r'^SERVER_VERSION\s*=\s*"([^"]+)"', (src / "version.py").read_text(encoding="utf-8"), re.M
    )
    if not m:
        raise InstallError(f"no SERVER_VERSION in {src / 'version.py'}")
    return m.group(1)


def python_ok(version=sys.version_info) -> bool:
    return tuple(version[:2]) >= MIN_PYTHON


def port_is_free(port: int, host: str = "0.0.0.0") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # sends nothing; makes the system pick the interface it would use
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def is_private(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    # what the certificate authority may vouch for
    return any(
        a in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12")
    )


def parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def merge_env(existing_text: str, wanted: dict) -> tuple[str, list]:
    """The .env text with every wanted setting added that is not there yet. A setting that is
    already there is NEVER changed (it may be an admin's choice or a secret other things depend on).
    Returns (new text, names added)."""
    have = parse_env(existing_text)
    added = [k for k in wanted if k not in have]
    text = (
        existing_text if existing_text.endswith("\n") or not existing_text else existing_text + "\n"
    )
    text += "".join(f"{k}={wanted[k]}\n" for k in added)
    return text, added


def write_private(path: pathlib.Path, text: str):
    """Write a file only its owner can read, atomically (nobody ever sees it half written or
    world-readable)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def render_unit(template: str, prefix: pathlib.Path, user: str, extra_rw: list) -> str:
    extra = "".join(f" {p}" for p in extra_rw)
    return (
        template.replace("@PREFIX@", str(prefix))
        .replace("@USER@", user)
        .replace("@EXTRA_PATHS@", extra)
    )


def new_password() -> str:
    return secrets.token_urlsafe(24)


def role_names(prefix: str, single: bool) -> dict:
    """purpose -> role name. One role for everything (single) or one per job."""
    if single:
        return {p: f"{prefix}_app" for p in ROLE_PURPOSES}
    return {p: f"{prefix}_{p}" for p in ROLE_PURPOSES}


def db_env(names: dict, passwords: dict, single: bool) -> dict:
    """The .env lines for the database roles."""
    out = {"DB_SINGLE_ROLE": "true"} if single else {}
    for purpose in ("read",) if single else ROLE_PURPOSES:
        user_var, pass_var = ROLE_VARS[purpose]
        out[user_var], out[pass_var] = names[purpose], passwords[names[purpose]]
    return out


def ca_fingerprint(pem: pathlib.Path) -> str:
    out = subprocess.run(
        ["openssl", "x509", "-in", str(pem), "-noout", "-fingerprint", "-sha256"],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise InstallError(
            "openssl could not read the certificate authority: " + out.stderr.strip()
        )
    return out.stdout.strip().split("=", 1)[1]


class Install:
    def __init__(self, a, say=print):
        self.a, self.say = a, say
        self.prefix = pathlib.Path(a.prefix)
        self.state = self.prefix / "state"
        self.version = server_version()
        self.release = self.prefix / "releases" / self.version
        self.current = self.prefix / "current"
        self.root = os.geteuid() == 0 if hasattr(os, "geteuid") else False
        self.env_path = self.state / ".env"
        self.existing_env = (
            self.env_path.read_text(encoding="utf-8") if self.env_path.exists() else ""
        )
        self.have = parse_env(self.existing_env)
        self.interactive = sys.stdin.isatty() and not a.yes

    def ask(self, question: str, default: str = "", secret: bool = False) -> str:
        if not self.interactive:
            return default
        shown = f" [{default}]" if default and not secret else ""
        answer = (getpass.getpass if secret else input)(f"{question}{shown}: ").strip()
        return answer or default

    def yes(self, question: str, default: bool = True) -> bool:
        if not self.interactive:
            return default
        answer = input(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not answer else answer.startswith("y")

    def run(self, *cmd, check=True, **kw):
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
        if check and r.returncode != 0:
            raise InstallError(
                f"`{' '.join(str(c) for c in cmd[:3])} ...` failed:\n{(r.stderr or r.stdout).strip()[-800:]}"
            )
        return r

    def py(self) -> pathlib.Path:
        return self.release / ".venv" / "bin" / "python"

    def server_env(self) -> dict:
        env = dict(os.environ)
        env["TOTR_STATE_DIR"] = str(self.state)
        for k in (
            "DB_HOST",
            "DB_ENGINE",
            "DB_USER",
            "DB_PASSWORD",
            "TOTR_ENV_FILE",
            "TOTR_PG_ADMIN_PASSWORD",
        ):
            # the .env of this install decides, not whatever the installing shell happens to have
            # set
            env.pop(k, None)
        return env

    # 1. checks
    def preflight(self) -> list:
        problems, a = [], self.a
        if not python_ok():
            problems.append(
                f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is needed; this is {sys.version.split()[0]}."
            )
        free = (
            shutil.disk_usage(
                self.prefix
                if self.prefix.exists()
                else self.prefix.parent
                if self.prefix.parent.exists()
                else pathlib.Path("/")
            ).free
            // 2**20
        )
        if free < MIN_FREE_MB:
            problems.append(
                f"Only {free} MB free where TOTR is going; it needs about {MIN_FREE_MB} MB."
            )
        if a.tls == "self" and not shutil.which("openssl"):
            problems.append(
                "openssl is needed to make the HTTPS certificate (Debian / Ubuntu: sudo apt install openssl; Arch: sudo pacman -S openssl), or use --tls none."
            )
        if not a.no_service:
            if not self.root:
                problems.append(
                    "Run with sudo: the service user and the systemd unit need it (or add --no-service)."
                )
            if not shutil.which("systemctl"):
                problems.append(
                    "systemd was not found. Add --no-service to set up the files only and start the server yourself."
                )
        # our own running service holds the port when we repair
        busy_ok = a.no_service or self.service_active()
        port = a.port if a.tls == "none" else a.tls_port
        if not busy_ok and not port_is_free(port):
            problems.append(
                f"Port {port} is already in use by something else. Use --port / --tls-port to pick another."
            )
        return problems

    def service_active(self) -> bool:
        return (
            shutil.which("systemctl") is not None
            and subprocess.run(["systemctl", "is-active", "--quiet", "totr"]).returncode == 0
        )

    # 2. layout
    def layout(self):
        for d in (
            self.prefix / "releases",
            self.state,
            self.state / "data",
            self.state / "reports",
            self.state / "certs",
            self.state / "downloads",
            self.state / "updates",
        ):
            d.mkdir(parents=True, exist_ok=True)
        for name in ("music", "media"):
            if not getattr(self.a, name + "_dir"):
                (self.state / name).mkdir(exist_ok=True)  # the defaults live in the state folder
        self.state.chmod(0o750)
        (self.state / "certs").chmod(0o700)
        self.say(f"  folders under {self.prefix}")

    def copy_release(self):
        def skip(folder, names):
            hidden = {n for n in names if n in COPY_SKIP_NAMES}
            if pathlib.Path(folder) == SRC / "static":
                hidden |= {"downloads"}
            return hidden | {n for n in names if n.endswith(".pyc")}

        shutil.copytree(SRC, self.release, ignore=skip, dirs_exist_ok=True)
        tmp = self.prefix / "current.new"
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(self.release)
        os.replace(tmp, self.current)
        self.say(f"  code in {self.release}  (current -> {self.version})")

    # 3. python environment
    def make_venv(self):
        req = self.release / "requirements.txt"
        stamp = self.release / ".venv" / ".requirements.sha256"
        digest = hashlib.sha256(req.read_bytes()).hexdigest()
        if self.py().exists() and stamp.exists() and stamp.read_text().strip() == digest:
            self.say("  python environment already complete")
            return
        r = self.run(sys.executable, "-m", "venv", self.release / ".venv", check=False)
        if r.returncode != 0:
            raise InstallError(
                "Could not make the Python environment:\n"
                + (r.stderr or r.stdout).strip()[-400:]
                + "\nOn Debian / Ubuntu: sudo apt install python3-venv"
            )
        self.say("  installing requirements (needs the internet; a minute)")
        self.run(
            self.py(), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", req
        )
        stamp.write_text(digest)

    # 4. settings
    def wanted_env(self) -> dict:
        a, w = self.a, {}
        w["SITE_NAME"] = a.site_name
        w["MUSIC_ROOT"] = a.music_dir or str(self.state / "music")
        w["MEDIA_ROOT"] = a.media_dir or str(self.state / "media")
        w["HOST"] = a.host
        w["PORT"] = str(a.port)
        if a.update_repo:
            w["UPDATE_REPO"] = a.update_repo
        if a.tls == "none":
            w["HTTP_ENABLED"] = "true"
        else:
            w.update(
                {
                    "TLS_CERT": "certs/server.pem" if a.tls == "self" else a.tls_cert,
                    "TLS_KEY": "certs/server.key" if a.tls == "self" else a.tls_key,
                    "TLS_PORT": str(a.tls_port),
                    "HTTP_ENABLED": "false",
                }
            )
        return {k: v for k, v in w.items() if v != ""}

    def write_env(self, extra: dict):
        text, added = merge_env(self.existing_env, {**self.wanted_env(), **extra})
        write_private(self.env_path, text)
        self.existing_env, self.have = text, parse_env(text)
        self.say(
            f"  settings in {self.env_path} (mode 600); "
            + (f"added {len(added)}" if added else "nothing new to add")
        )

    # 5. database
    def database(self):
        a = self.a
        engine = self.have.get("DB_ENGINE") or a.db
        if engine == "sqlite":
            self.write_env({"DB_ENGINE": "sqlite"})
            out = self.run(self.py(), "migrate.py", "up", cwd=self.release, env=self.server_env())
            self.say(
                "  database: the built-in SQLite file in "
                + str(self.state / "data")
                + " ("
                + out.stdout.strip().splitlines()[-1]
                + ")"
            )
            return
        host = self.have.get("DB_HOST") or a.pg_host
        port = self.have.get("DB_PORT") or str(a.pg_port)
        name = self.have.get("DB_NAME") or a.pg_name
        single = (
            (self.have.get("DB_SINGLE_ROLE", "").lower() in ("1", "true", "yes"))
            if self.have.get("DB_ENGINE") == "postgres"
            else a.pg_single_role
        )
        names = role_names(a.pg_role_prefix, single)
        if self.have.get("DB_USER"):  # a repair: use the roles that are already in .env
            names = {
                p: self.have.get(ROLE_VARS[p][0], names[p]) if not single else self.have["DB_USER"]
                for p in ROLE_PURPOSES
            }
        passwords = {}
        for purpose in ROLE_PURPOSES:
            n = names[purpose]
            var = ROLE_VARS["read" if single else purpose][1]
            passwords[n] = self.have.get(var) or passwords.get(n) or new_password()
        admin_user = a.pg_admin_user or self.ask(
            "PostgreSQL account that may create databases and roles", "postgres"
        )
        admin_pass = os.environ.get("TOTR_PG_ADMIN_PASSWORD") or self.ask(
            f"Password for {admin_user} (used now, never stored; empty if it needs none)",
            "",
            secret=True,
        )
        sslmode = a.pg_sslmode or (
            "disable"
            if host.startswith("/") or host in ("localhost", "127.0.0.1", "::1")
            else "require"
        )
        request = {
            "admin": {
                "host": host,
                "port": port,
                "dbname": name,
                "user": admin_user,
                "password": admin_pass,
                "sslmode": sslmode,
            },
            "roles": {n: p for n, p in passwords.items() if n},
            "purposes": {p: names[p] for p in ROLE_PURPOSES},
        }
        self.say(
            f"  PostgreSQL {host}:{port}/{name}: building the layout and the {'role' if single else 'five roles'}"
        )
        r = self.run(
            self.py(),
            "tools/install_db.py",
            input=json.dumps(request),
            cwd=self.release,
            env=self.server_env(),
            check=False,
        )
        del request  # the admin login is not kept around
        try:
            result = json.loads(r.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise InstallError(
                "The database step gave no answer:\n" + (r.stderr or r.stdout).strip()[-500:]
            )
        if not result.get("ok"):
            raise InstallError("PostgreSQL: " + result.get("error", "failed"))
        self.write_env(
            {
                "DB_ENGINE": "postgres",
                "DB_HOST": host,
                "DB_PORT": port,
                "DB_NAME": name,
                "DB_SSLMODE": sslmode,
                **db_env(names, passwords, single),
            }
        )
        self.say(
            "  database ready"
            + (" (made the database)" if result["created_database"] else "")
            + (
                f"; applied {', '.join(result['applied'])}"
                if result["applied"]
                else "; already up to date"
            )
        )

    # 6. HTTPS
    def certificates(self) -> str | None:
        """Returns the fingerprint of the certificate authority to show, or None."""
        a, certs = self.a, self.state / "certs"
        if a.tls == "none":
            self.say(
                "  HTTPS: off (plain HTTP). Fine on a closed network or behind a proxy that does HTTPS; passwords then cross the network unprotected."
            )
            return None
        if a.tls == "own":
            for p in (a.tls_cert, a.tls_key):
                if not p or not pathlib.Path(p).is_file():
                    raise InstallError(
                        f"--tls own needs --tls-cert and --tls-key to be files; {p!r} is not."
                    )
            self.say("  HTTPS: your own certificate")
            return None
        ip = a.address or lan_ip()
        names = [n for n in (a.name or []) if n.endswith((".local", ".lan"))]
        host = socket.gethostname().split(".")[0]
        if host and not names:
            names = [f"{host}.local"]
        if not is_private(ip):
            raise InstallError(
                f"{ip} is not a private network address, and this server's certificate authority only vouches for private ones (10.x, 192.168.x, 172.16-31.x, "
                "*.local, *.lan). Use --address with this server's address on your network, or --tls own with a certificate you have, or --tls none."
            )
        env = dict(os.environ, TOTR_CERTS_DIR=str(certs))
        tool = self.release / "tools" / "make_certs.py"
        if not (certs / "totr-ca.pem").exists():
            passphrase = new_password()
            # next to the key it protects: this CA lives on the server so renewals can run by
            # themselves
            write_private(certs / "ca.pass", passphrase + "\n")
            self.run(sys.executable, tool, "ca", env=dict(env, TOTR_CA_PASSPHRASE=passphrase))
        passphrase = (certs / "ca.pass").read_text().strip()
        if not (certs / "server.pem").exists():
            cmd = [
                sys.executable,
                tool,
                "server",
                "--ip",
                ip,
                *[x for n in names for x in ("--name", n)],
            ]
            self.run(*cmd, env=dict(env, TOTR_CA_PASSPHRASE=passphrase))
            self.say(f"  HTTPS: made the server certificate for {ip} and {', '.join(names)}")
        else:
            self.say("  HTTPS: certificate already there")
        return ca_fingerprint(certs / "totr-ca.pem")

    # 7. first admin
    def first_admin(self) -> str | None:
        listing = self.run(
            self.py(), "-m", "auth.cli", "list", cwd=self.release, env=self.server_env()
        ).stdout
        if re.search(r"^\S+\s+admin\s", listing, re.M):
            self.say("  an admin account already exists")
            return None
        out = (
            self.run(
                self.py(), "-m", "auth.cli", "invite-admin", cwd=self.release, env=self.server_env()
            )
            .stdout.strip()
            .splitlines()[-1]
        )
        if not out.startswith("/invite/"):
            raise InstallError("could not make the admin sign-in link: " + out)
        return out

    # 8. service
    def ensure_user(self):
        if subprocess.run(["id", "-u", self.a.user], capture_output=True).returncode != 0:
            nologin = shutil.which("nologin") or "/usr/sbin/nologin"
            self.run(
                "useradd",
                "--system",
                "--home-dir",
                self.prefix,
                "--no-create-home",
                "--shell",
                nologin,
                self.a.user,
            )
            self.say(f"  made the service user '{self.a.user}'")

    def fix_owners(self):
        if self.root and not self.a.no_service:
            self.run("chown", "-R", f"{self.a.user}:{self.a.user}", self.state)
            for d in (self.state / "music", self.state / "media"):
                if d.exists():
                    self.run("chown", "-R", f"{self.a.user}:{self.a.user}", d)

    def service(self):
        a = self.a
        extra = sorted(
            {
                p
                for p in (self.have.get("MUSIC_ROOT"), self.have.get("MEDIA_ROOT"))
                if p and not pathlib.Path(p).is_relative_to(self.state)
            }
        )
        unit = render_unit(
            (SRC / "deploy" / "totr.service.in").read_text(encoding="utf-8"),
            self.prefix,
            a.user,
            extra,
        )
        unit_path = pathlib.Path("/etc/systemd/system/totr.service")
        unit_path.write_text(unit, encoding="utf-8")
        # the update service: started by systemd (as root) when the web app files a request, so the
        # web app never replaces its own code (deploy/apply-update.py)
        for name in ("totr-update.service", "totr-update.path"):
            pathlib.Path("/etc/systemd/system", name).write_text(
                render_unit(
                    (SRC / "deploy" / f"{name}.in").read_text(encoding="utf-8"),
                    self.prefix,
                    a.user,
                    [],
                ),
                encoding="utf-8",
            )
        self.run("systemctl", "daemon-reload")
        self.run("systemctl", "enable", "totr")
        self.run("systemctl", "enable", "--now", "totr-update.path")
        self.run("systemctl", "restart", "totr")
        self.say(
            "  service 'totr' enabled and started (restarts by itself; starts at boot; log: journalctl -u totr)"
        )
        self.say(
            "  update service ready (totr-update.path; an admin starts an update from the web app or `python -m updates install`; log: journalctl -u totr-update)"
        )
        for p in extra:
            if subprocess.run(["runuser", "-u", a.user, "--", "test", "-w", p]).returncode != 0:
                self.say(
                    f"  NOTE: the service user cannot write to {p}. Reading and playing work; uploads and the collector need: sudo chown -R {a.user} {p}  (or a shared group)."
                )

    def wait_for_health(self) -> bool:
        a = self.a
        if a.tls == "none":
            url, ctx = f"http://127.0.0.1:{a.port}/health", None
        else:
            url = f"https://127.0.0.1:{a.tls_port}/health"
            ctx = (
                ssl.create_default_context(cafile=str(self.state / "certs" / "totr-ca.pem"))
                if a.tls == "self"
                else ssl.create_default_context()
            )
            ctx.check_hostname = (
                False  # we are asking by 127.0.0.1, which the certificate (rightly) does not list
            )
        for _ in range(40):
            try:
                with urllib.request.urlopen(url, context=ctx, timeout=3) as r:
                    if r.status == 200 and b"ok" in r.read():
                        return True
            except Exception:
                time.sleep(1)
        return False

    def go(self) -> int:
        a = self.a
        repair = self.env_path.exists()
        self.say(
            f"TOTR {self.version}: "
            + ("repairing the install in " if repair else "installing into ")
            + str(self.prefix)
        )
        problems = self.preflight()
        if problems:
            self.say("\nCannot go on:\n  - " + "\n  - ".join(problems))
            return 1
        if self.root and not a.no_service:
            self.ensure_user()
        self.layout()
        self.copy_release()
        self.make_venv()
        self.write_env({})
        self.database()
        fingerprint = self.certificates()
        invite = self.first_admin()
        scheme, port = ("http", a.port) if a.tls == "none" else ("https", a.tls_port)
        address = f"{scheme}://{a.address or lan_ip()}:{port}"
        if not a.no_service:
            self.fix_owners()
            self.service()
            healthy = self.wait_for_health()
            self.say(
                "  the server answers /health"
                if healthy
                else "  WARNING: the server did not answer /health in 40 s. See: journalctl -u totr -n 50"
            )
        else:
            self.fix_owners()
            self.say(
                f"  no service made. Start it with:\n    TOTR_STATE_DIR={self.state} {self.py()} {self.current}/main.py"
            )
        self.say("\n=== TOTR is installed ===")
        self.say(f"Address:      {address}/")
        if fingerprint:
            self.say(
                f"Certificate:  this server made its own HTTPS certificate. Its fingerprint (SHA-256) is\n              {fingerprint}\n"
                f"              A phone or computer that has never seen this server shows this same code the first time it connects: check they match.\n"
                f"              (To stop browser warnings, import {self.state / 'certs' / 'totr-ca.pem'} into the browser.)"
            )
        if invite:
            self.say(
                f"First admin:  open  {address}{invite}\n              (one time, valid 7 days; you choose your own name and password there)"
            )
        self.say(
            f"Settings:     {self.env_path}\nUpdate later: more in docs/setup.md. Run this installer again any time to repair."
        )
        return 0 if (a.no_service or healthy) else 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--prefix", default="/opt/totr", help="where TOTR is installed (default /opt/totr)"
    )
    p.add_argument("--user", default="totr", help="the service user (default totr)")
    p.add_argument(
        "--no-service",
        action="store_true",
        help="set up the files only: no service user, no systemd unit (you start it yourself)",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="ask nothing; use the defaults and what is given on the command line",
    )
    p.add_argument("--site-name", default="", help="shown in the page footer")
    p.add_argument(
        "--music-dir", default="", help="the folder with your music (default <prefix>/state/music)"
    )
    p.add_argument(
        "--media-dir",
        default="",
        help="pictures and videos for the radio (default <prefix>/state/media)",
    )
    p.add_argument("--host", default="0.0.0.0", help="address to listen on (default every address)")
    p.add_argument(
        "--address",
        default="",
        help="this server's address on your network, for the certificate and the printed links (default: found by itself)",
    )
    p.add_argument(
        "--name",
        action="append",
        default=[],
        help="a name ending in .local or .lan that reaches this server (default <hostname>.local)",
    )
    p.add_argument(
        "--port", type=int, default=8080, help="the plain HTTP port (used only with --tls none)"
    )
    p.add_argument(
        "--tls",
        choices=("self", "own", "none"),
        default="self",
        help="self: this server makes its own certificate (default); own: use --tls-cert/--tls-key; none: plain HTTP",
    )
    p.add_argument("--tls-port", type=int, default=8443)
    p.add_argument("--tls-cert", default="")
    p.add_argument("--tls-key", default="")
    p.add_argument(
        "--db",
        choices=("sqlite", "postgres"),
        default="sqlite",
        help="sqlite: the built-in file, nothing to set up (default)",
    )
    p.add_argument("--pg-host", default="localhost")
    p.add_argument("--pg-port", type=int, default=5432)
    p.add_argument("--pg-name", default="totr", help="the database (made if it does not exist)")
    p.add_argument(
        "--pg-admin-user",
        default="",
        help="a PostgreSQL account that may create databases and roles (its password: TOTR_PG_ADMIN_PASSWORD or asked)",
    )
    p.add_argument("--pg-sslmode", default="")
    p.add_argument(
        "--pg-role-prefix",
        default="totr",
        help="role names start with this (totr_read, totr_auth ...)",
    )
    p.add_argument(
        "--update-repo",
        default="",
        help="the GitHub repository (owner/name) whose releases carry server updates; without it the server does not look for updates",
    )
    p.add_argument(
        "--pg-single-role",
        action="store_true",
        help="one login for everything instead of five least-privilege roles",
    )
    return p


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    inst = Install(a)
    if inst.interactive and not inst.have.get("DB_ENGINE"):
        a.db = (
            "postgres"
            if inst.yes(
                "Do you have a PostgreSQL database you want TOTR to use? (No = use the built-in one, nothing to set up)",
                False,
            )
            else "sqlite"
        )
        a.site_name = a.site_name or inst.ask(
            "Name of this server, shown at the bottom of pages (optional)", ""
        )
        a.music_dir = a.music_dir or inst.ask("Folder with your music", str(inst.state / "music"))
        if a.db == "postgres":
            a.pg_host = inst.ask("PostgreSQL host", a.pg_host)
            a.pg_port = int(inst.ask("PostgreSQL port", str(a.pg_port)))
            a.pg_name = inst.ask("Database name", a.pg_name)
    try:
        return inst.go()
    except InstallError as e:
        print(f"\nStopped: {e}\nNothing is half-installed that running this again will not fix.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
