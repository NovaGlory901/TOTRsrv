"""The installer's logic that needs no root and no systemd (tools/install_wizard.py), and the
first-admin link (python -m auth.cli invite-admin). The real thing on a real machine is
deploy/lab-test.sh."""

import os
import pathlib
import re
import subprocess
import sys
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
sys.path.insert(0, str(PF / "tools"))
tmp = pathlib.Path(tempfile.mkdtemp())

import install_wizard as w
from check import check, finish

# small helpers
check(
    "the version is read from version.py",
    re.fullmatch(r"\d+\.\d+\.\d+", w.server_version()) is not None,
    w.server_version(),
)
check(
    "Python 3.11 is enough, 3.10 is not",
    w.python_ok((3, 11, 0)) and w.python_ok((3, 14, 1)) and not w.python_ok((3, 10, 9)),
)
check(
    "only private network addresses count (what the certificate authority may vouch for)",
    all(w.is_private(i) for i in ("10.0.0.5", "192.168.1.5", "172.16.0.1", "172.31.255.1"))
    and not any(w.is_private(i) for i in ("8.8.8.8", "172.32.0.1", "127.0.0.1", "100.64.0.1")),
)
import socket

s = socket.socket()
s.bind(("127.0.0.1", 0))
busy = s.getsockname()[1]
check("a port in use is reported in use", not w.port_is_free(busy, "127.0.0.1"))
s.close()
check("and free again once it is released", w.port_is_free(busy, "127.0.0.1"))

# .env: add what is missing, never change what is there
text = "DB_PASSWORD=secret-one\nSITE_NAME=Mine\n# a note\n"
merged, added = w.merge_env(
    text, {"DB_PASSWORD": "new-random", "SITE_NAME": "Other", "PORT": "8080", "HOST": "0.0.0.0"}
)
got = w.parse_env(merged)
check(
    "an existing secret is never replaced",
    got["DB_PASSWORD"] == "secret-one" and got["SITE_NAME"] == "Mine",
)
check(
    "missing settings are added, and only those",
    added == ["PORT", "HOST"] and got["PORT"] == "8080" and got["HOST"] == "0.0.0.0",
)
check("comments and the old lines are kept", "# a note" in merged and merged.startswith(text))
check("running it again adds nothing", w.merge_env(merged, {"PORT": "9", "HOST": "x"})[1] == [])
check(
    "a file without a final newline still gets clean lines",
    w.merge_env("A=1", {"B": "2"})[0] == "A=1\nB=2\n",
)
check("an empty file works", w.merge_env("", {"A": "1"})[0] == "A=1\n")

p = tmp / "state" / ".env"
w.write_private(p, "A=1\n")
if os.name != "nt":
    check(
        "a private file is readable by its owner only",
        oct(p.stat().st_mode & 0o777) == "0o600",
        oct(p.stat().st_mode),
    )
else:
    print("skip  private file mode  (Windows has no permission bits; checked on Linux)")
check(
    "and is complete, with no half-written file left beside it",
    p.read_text() == "A=1\n" and not list(p.parent.glob("*.part")),
)
w.write_private(p, "A=2\n")
check("writing again replaces it whole", p.read_text() == "A=2\n")

# database roles
names = w.role_names("totr", False)
check(
    "five roles, one per job",
    names
    == {
        "read": "totr_read",
        "write": "totr_write",
        "maint": "totr_maint",
        "auth": "totr_auth",
        "mixtape": "totr_mixtape",
    },
    names,
)
check("or one role for everything", set(w.role_names("lab", True).values()) == {"lab_app"})
pw = {n: w.new_password() for n in names.values()}
env = w.db_env(names, pw, False)
check(
    ".env gets a user and a password for each of the five jobs, with the names the server reads",
    env["DB_USER"] == "totr_read"
    and env["DB_AUTH_USER"] == "totr_auth"
    and env["DB_MIXTAPE_PASSWORD"] == pw["totr_mixtape"]
    and "DB_SINGLE_ROLE" not in env
    and len(env) == 10,
    sorted(env),
)
single = w.db_env(w.role_names("totr", True), {"totr_app": "x"}, True)
check(
    "single-role mode writes DB_SINGLE_ROLE and the one login",
    single == {"DB_SINGLE_ROLE": "true", "DB_USER": "totr_app", "DB_PASSWORD": "x"},
    single,
)
check(
    "every generated password is long and different",
    len(set(pw.values())) == 5 and all(len(v) >= 30 for v in pw.values()),
)

# the service unit
# the installer is for Linux: its paths are POSIX paths on any machine
unit = w.render_unit(
    (PF / "deploy" / "totr.service.in").read_text(),
    pathlib.PurePosixPath("/opt/totr"),
    "totr",
    ["/srv/music"],
)
check(
    "the unit runs the current release as the service user with the state folder",
    "User=totr" in unit
    and "WorkingDirectory=/opt/totr/current" in unit
    and "TOTR_STATE_DIR=/opt/totr/state" in unit
    and "ExecStart=/opt/totr/current/.venv/bin/python main.py" in unit,
    unit[:200],
)
check(
    "it may write to the state folder and the music folder, nowhere else",
    "ProtectSystem=strict" in unit and "ReadWritePaths=/opt/totr/state /srv/music" in unit,
)
lines = {l.strip() for l in unit.splitlines() if l.strip() and not l.startswith("#")}
check(
    "the sandbox: no new privileges, a private /tmp and devices, no capabilities, other users' files unreadable by default, only the socket families it uses",
    {
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "PrivateDevices=true",
        "CapabilityBoundingSet=",
        "UMask=0027",
        "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK",
        "ProtectKernelLogs=true",
        "ProtectProc=invisible",
        "RestrictNamespaces=true",
    }
    <= lines,
    sorted(lines),
)
check(
    "...system calls are filtered (privileged, resource, mount, module, clock, raw-io ones are out), and writable-executable memory is refused",
    "SystemCallFilter=@system-service" in lines
    and any(
        l.startswith("SystemCallFilter=~")
        and all(
            g in l for g in ("@privileged", "@mount", "@module", "@raw-io", "@clock", "@resources")
        )
        for l in lines
    )
    and "MemoryDenyWriteExecute=true" in lines,
)
check(
    "...many open connections are allowed for (the Status tab keeps /proc/stat readable: no ProcSubset)",
    "LimitNOFILE=65536" in lines and not any(l.startswith("ProcSubset") for l in lines),
)
check("it restarts by itself", "Restart=always" in unit and "WantedBy=multi-user.target" in unit)
check(
    "it has no leftover placeholders",
    not re.search(r"@[A-Z_]+@", unit),
    re.findall(r"@[A-Z_]+@", unit),
)

# the update service
for name in ("totr-update.service", "totr-update.path"):
    text = w.render_unit(
        (PF / "deploy" / f"{name}.in").read_text(), pathlib.PurePosixPath("/opt/totr"), "totr", []
    )
    check(
        f"{name}: no leftover placeholders",
        not re.search(r"@[A-Z_]+@", text),
        re.findall(r"@[A-Z_]+@", text),
    )
    if name.endswith(".service"):
        svc = text
    else:
        pth = text
check(
    "the update service runs as root (the web app's user must not be able to replace the code), once, from the installed release",
    "User=" not in svc
    and "Type=oneshot" in svc
    and "ExecStart=/opt/totr/current/.venv/bin/python /opt/totr/current/deploy/apply-update.py --prefix /opt/totr"
    in svc,
)
check(
    "and may write only to the TOTR folder",
    "ProtectSystem=strict" in svc
    and "ReadWritePaths=/opt/totr" in svc
    and "ProtectHome=true" in svc,
)
ulines = {l.strip() for l in svc.splitlines() if l.strip() and not l.startswith("#")}
check(
    "the update service, though root, is boxed in: only the capabilities its work needs (change owners, override file permissions), no devices, kernel log, clock or namespaces, no writable-executable memory",
    "CapabilityBoundingSet=CAP_CHOWN CAP_FOWNER CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH" in ulines
    and {
        "PrivateDevices=true",
        "ProtectKernelLogs=true",
        "ProtectClock=true",
        "ProtectHostname=true",
        "RestrictNamespaces=true",
        "RestrictRealtime=true",
        "MemoryDenyWriteExecute=true",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "RemoveIPC=true",
        "ProtectProc=invisible",
    }
    <= ulines,
    sorted(ulines),
)
check(
    "...it reaches only internet, local and netlink sockets",
    "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK" in ulines,
)
check(
    "...mount, swap, reboot, raw-io, module, debug and clock system calls are out, ownership changes stay in, and a refused call fails with an error instead of ending the process mid-swap",
    "SystemCallFilter=@system-service @chown" in ulines
    and any(
        l.startswith("SystemCallFilter=~")
        and all(
            g in l for g in ("@mount", "@swap", "@reboot", "@raw-io", "@module", "@debug", "@clock")
        )
        for l in ulines
    )
    and "SystemCallErrorNumber=EPERM" in ulines,
)
check(
    "...and it still has no User= and no network namespace (root is the point; it must reach the internet and systemd)",
    not any(l.startswith(("User=", "PrivateNetwork", "PrivateUsers")) for l in ulines),
)
check(
    "it is started by the request file the web app writes in the state folder",
    "PathExists=/opt/totr/state/updates/request.json" in pth and "Unit=totr-update.service" in pth,
)
check(
    "the web app's service can still write the update folder (inside the state folder) but not the code",
    "ReadWritePaths=/opt/totr/state" in unit
    and "/opt/totr/releases" not in unit
    and "/opt/totr/current" not in unit.split("ReadWritePaths")[1],
)
check(
    "--update-repo is an option and is written to .env only when given",
    w.parser().parse_args(["--update-repo", "me/TOTR"]).update_repo == "me/TOTR"
    and w.parser().parse_args([]).update_repo == "",
)

# the argument parser
a = w.parser().parse_args([])
check(
    "the defaults: SQLite, HTTPS from this server's own certificate on 8443, listen everywhere",
    a.db == "sqlite"
    and a.tls == "self"
    and a.tls_port == 8443
    and a.host == "0.0.0.0"
    and a.prefix == "/opt/totr",
)
check(
    "a bad choice is refused",
    subprocess.run(
        [sys.executable, str(PF / "tools" / "install_wizard.py"), "--db", "mysql"],
        capture_output=True,
    ).returncode
    == 2,
)

# preflight (no root needed with --no-service)
a = w.parser().parse_args(["--prefix", str(tmp / "t"), "--no-service", "--tls", "none", "--yes"])
inst = w.Install(a, say=lambda *_: None)
check(
    "a plain setup with nothing in the way has no problems",
    inst.preflight() == [],
    inst.preflight(),
)
a = w.parser().parse_args(
    ["--prefix", str(tmp / "t"), "--no-service", "--tls", "none", "--yes", "--port", str(busy)]
)
s = socket.socket()
s.bind(("0.0.0.0", busy))
s.listen()
a.no_service = False
inst = w.Install(a, say=lambda *_: None)
# (on a machine that already runs TOTR, a busy port is its own and is not a problem)
inst.service_active = lambda: False
msgs = " ".join(inst.preflight())
s.close()
check("a port in use is named as a problem", f"Port {busy}" in msgs, msgs)
check(
    "without root, the service steps say to use sudo",
    os.name == "nt" or inst.root or "sudo" in msgs,
    msgs,
)

# the first-admin link (a real run of the server's own CLI against a temp SQLite database)
data = tmp / "data"
env = {k: v for k, v in os.environ.items() if not k.startswith(("DB_", "TOTR_"))}
env.update(TOTR_DATA_DIR=str(data), DB_ENGINE="sqlite")
run = lambda *args: subprocess.run(
    [sys.executable, *args], capture_output=True, text=True, cwd=PF, env=env
)
r = run("migrate.py", "up")
check("the local database is built by migrate.py up", r.returncode == 0, r.stdout + r.stderr)
r = run("-m", "auth.cli", "invite-admin")
link = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
check(
    "invite-admin prints a one-time sign-in path and nothing secret besides it",
    r.returncode == 0 and re.fullmatch(r"/invite/[A-Za-z0-9_-]{30,}", link) is not None,
    r.stdout + r.stderr,
)
check("it asks for no password", "password" not in (r.stdout + r.stderr).lower())
r2 = run("-m", "auth.cli", "list")
check(
    "no account exists yet, so the installer would show the link",
    r2.returncode == 0
    and "0 account(s)" in r2.stdout
    and not re.search(r"^\S+\s+admin\s", r2.stdout, re.M),
    r2.stdout,
)

finish()
