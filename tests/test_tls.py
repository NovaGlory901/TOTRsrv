"""Tests for HTTPS: the TLS settings the server reads, the certificate tool (tools/make_certs.py: a
name-constrained private authority), and a real TLS handshake against a server using those
certificates. Uses the `openssl` command; everything goes in a throwaway folder."""

import os
import pathlib
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
os.chdir(PF)
from check import check, finish

if not shutil.which("openssl"):
    print("skip  TLS tests (the openssl command is not installed)")
    sys.exit(0)

import tlsconf

tmp = pathlib.Path(tempfile.mkdtemp())
PASS = "correct horse battery staple"
ENV = {**os.environ, "TOTR_CERTS_DIR": str(tmp), "TOTR_CA_PASSPHRASE": PASS}


def tool(*args, env=None):
    r = subprocess.run(
        [sys.executable, str(PF / "tools" / "make_certs.py"), *args],
        env=env or ENV,
        capture_output=True,
        text=True,
    )
    return r.returncode, r.stdout + r.stderr


# the settings
check(
    "settings: no TLS variables means plain HTTP as before",
    tlsconf.settings({}) is None and tlsconf.settings({"TLS_CERT": "", "TLS_KEY": " "}) is None,
)
for label, env in (
    ("only the certificate", {"TLS_CERT": "x.pem"}),
    ("only the key", {"TLS_KEY": "x.key"}),
):
    try:
        tlsconf.settings(env)
        ok = False
    except tlsconf.TlsError as e:
        ok = "both" in str(e)
    check(f"settings: {label} is refused with a clear message", ok)
try:
    tlsconf.settings({"TLS_CERT": str(tmp / "nope.pem"), "TLS_KEY": str(tmp / "nope.key")})
    ok = False
except tlsconf.TlsError as e:
    ok = "not a file" in str(e)
check("settings: a missing file is named", ok)


def private(path):
    """Only the owner can read the file. Windows has no such permission bits, so there it is not
    checked (and the run says so)."""
    return os.name == "nt" or path.stat().st_mode & 0o077 == 0


if os.name == "nt":
    print(
        "skip  key files private to the owner  (Windows has no permission bits; checked on Linux)"
    )

# the certificate tool
code, out = tool("ca")
check(
    "tool: makes a certificate authority",
    code == 0 and (tmp / "totr-ca.pem").exists() and (tmp / "totr-ca.key").exists(),
    out,
)
check(
    "tool: the CA key is encrypted and private to the owner",
    b"ENCRYPTED PRIVATE KEY" in (tmp / "totr-ca.key").read_bytes() and private(tmp / "totr-ca.key"),
)
code, out = tool("ca")
check("tool: refuses to replace an existing authority", code != 0 and "already exists" in out)
code, out = tool("server", "--ip", "10.0.0.5", "--name", "server.local")
check(
    "tool: issues a server certificate",
    code == 0 and (tmp / "server.pem").exists() and (tmp / "server.key").exists(),
    out,
)
check(
    "tool: the server key is not password-protected (the server starts on its own) but private to the owner",
    b"ENCRYPTED" not in (tmp / "server.key").read_bytes() and private(tmp / "server.key"),
)
v = lambda *a: subprocess.run(
    [
        "openssl",
        "verify",
        "-CAfile",
        str(tmp / "totr-ca.pem"),
        "-purpose",
        "sslserver",
        *a,
        str(tmp / "server.pem"),
    ],
    capture_output=True,
    text=True,
)
check(
    "verify: the certificate is good for its own address and name",
    "OK" in v("-verify_ip", "10.0.0.5").stdout
    and "OK" in v("-verify_hostname", "server.local").stdout,
)
check(
    "verify: and not for any other",
    v("-verify_ip", "10.0.0.99").returncode != 0
    and v("-verify_hostname", "other.local").returncode != 0,
)
cat = subprocess.run(
    [
        "openssl",
        "x509",
        "-in",
        str(tmp / "totr-ca.pem"),
        "-noout",
        "-ext",
        "nameConstraints,basicConstraints",
    ],
    capture_output=True,
    text=True,
).stdout
check(
    "the authority is name-constrained to private addresses and .local/.lan, and cannot make further authorities",
    "critical" in cat
    and "IP:10.0.0.0/255.0.0.0" in cat
    and "DNS:.local" in cat
    and "pathlen:0" in cat,
    cat,
)
other = tmp / "other"
other.mkdir()
shutil.copy(tmp / "totr-ca.pem", other)
shutil.copy(tmp / "totr-ca.key", other)
env2 = {**ENV, "TOTR_CERTS_DIR": str(other)}
for what, args in (
    ("a public address", ("--ip", "8.8.8.8")),
    ("a public name", ("--ip", "10.0.0.5", "--name", "bank.example.com")),
):
    code, out = tool("server", *args, env=env2)
    check(
        f"constraint: the authority will not vouch for {what}",
        code != 0 and "permitted subtree" in out and not (other / "server.pem").exists(),
        out[-200:],
    )
code, out = tool(
    "server", "--ip", "10.0.0.5", env={**env2, "TOTR_CA_PASSPHRASE": "not the passphrase at all"}
)
check("tool: a wrong passphrase says so", code != 0 and "not the CA passphrase" in out, out[-200:])

# the settings with real files
s = tlsconf.settings({"TLS_CERT": str(tmp / "server.pem"), "TLS_KEY": str(tmp / "server.key")})
check(
    "settings: with both files, HTTPS is on at 8443 and HTTP stays",
    s and s["port"] == 8443 and s["http"] is True and s["cert"].endswith("server.pem"),
    s,
)
check(
    "settings: HTTP_ENABLED=false closes the HTTP port",
    tlsconf.settings(
        {
            "TLS_CERT": str(tmp / "server.pem"),
            "TLS_KEY": str(tmp / "server.key"),
            "HTTP_ENABLED": "false",
        }
    )["http"]
    is False,
)
check(
    "settings: a custom port is read",
    tlsconf.settings(
        {
            "TLS_CERT": str(tmp / "server.pem"),
            "TLS_KEY": str(tmp / "server.key"),
            "TLS_PORT": "9443",
        }
    )["port"]
    == 9443,
)
for bad in ("0", "99999", "abc"):
    try:
        tlsconf.settings(
            {
                "TLS_CERT": str(tmp / "server.pem"),
                "TLS_KEY": str(tmp / "server.key"),
                "TLS_PORT": bad,
            }
        )
        ok = False
    except tlsconf.TlsError:
        ok = True
    check(f"settings: TLS_PORT={bad} is refused", ok)

# a real handshake
import uvicorn


async def hello(scope, receive, send):
    if scope["type"] == "http":
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"hello over tls"})


with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
server = uvicorn.Server(
    uvicorn.Config(
        hello,
        host="127.0.0.1",
        port=port,
        ssl_certfile=s["cert"],
        ssl_keyfile=s["key"],
        log_level="critical",
    )
)
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.05)


def fetch(context, hostname):
    with (
        socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
        context.wrap_socket(raw, server_hostname=hostname) as tls,
    ):
        tls.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        data = b""
        while chunk := tls.recv(4096):
            data += chunk
        return data


trusting = ssl.create_default_context(cafile=str(tmp / "totr-ca.pem"))
check(
    "handshake: a client that trusts the TOTR authority connects and gets the page",
    b"hello over tls" in fetch(trusting, "10.0.0.5"),
)
try:
    fetch(ssl.create_default_context(), "10.0.0.5")
    ok = False
except ssl.SSLCertVerificationError:
    ok = True
check("handshake: a client that does not trust the authority refuses the server", ok)
try:
    fetch(trusting, "10.0.0.99")
    ok = False
except ssl.SSLCertVerificationError:
    ok = True
check("handshake: even a trusting client refuses it when the address is not the certificate's", ok)
server.should_exit = True
thread.join(5)
shutil.rmtree(tmp, ignore_errors=True)
finish()
