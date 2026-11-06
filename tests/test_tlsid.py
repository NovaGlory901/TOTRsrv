"""The server's network identity: the public certificate authority an app is shown the fingerprint
of (tlsid.py, GET /.well-known/totr-ca.pem)."""

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
os.chdir(PF)
TMP = pathlib.Path(tempfile.mkdtemp(prefix="totr-tlsid-"))
(TMP / "certs").mkdir()
os.environ.update(
    TOTR_CERTS_DIR=str(TMP / "certs"),
    DB_AUTH_USER="x",
    DB_AUTH_PASSWORD="y",
    MAINT_REPORT=str(TMP / "r" / "m.json"),
    TOTR_STATE_DIR=str(TMP / "state"),
    UPDATE_PUBKEY_FILE=str(TMP / "none.txt"),
)
os.environ.pop("TLS_CERT", None)

from check import check, finish
from fastapi.testclient import TestClient

import main
import tlsid
from auth import permissions, service


def openssl(*args):
    return subprocess.run(["openssl", *args], capture_output=True, text=True, check=True).stdout


CA = TMP / "certs" / "totr-ca.pem"
KEY = TMP / "ca.key"
openssl(
    "req",
    "-x509",
    "-newkey",
    "ec",
    "-pkeyopt",
    "ec_paramgen_curve:prime256v1",
    "-nodes",
    "-keyout",
    str(KEY),
    "-out",
    str(CA),
    "-days",
    "30",
    "-subj",
    "/CN=Test TOTR CA",
)
good = CA.read_bytes()

pem = tlsid.ca_pem()
check(
    "the authority's certificate is read as one clean PEM certificate",
    pem is not None
    and pem.startswith(b"-----BEGIN CERTIFICATE-----\n")
    and pem.count(b"BEGIN") == 1,
)
fp = tlsid.fingerprint(pem)
check(
    "the fingerprint is six groups of four hex digits",
    re.fullmatch(r"(?:[0-9a-f]{4}-){5}[0-9a-f]{4}", fp or "") is not None,
    fp,
)
ref = (
    openssl("x509", "-in", str(CA), "-noout", "-fingerprint", "-sha256")
    .split("=", 1)[1]
    .strip()
    .replace(":", "")
    .lower()
)
check(
    "and it is the start of the certificate's SHA-256 (as openssl computes it)",
    fp.replace("-", "") == ref[:24],
    (fp, ref[:24]),
)
check(
    "the same certificate always gives the same code, another gives another",
    tlsid.fingerprint(pem) == fp and tlsid.fingerprint(pem.replace(b"A", b"B", 1)) != fp,
)

# what must never be read or served
CA.write_bytes(KEY.read_bytes())
check("a file that holds a private key is refused, whatever its name", tlsid.ca_pem() is None)
CA.write_bytes(good + good)
check("a file with two certificates is refused (exactly one)", tlsid.ca_pem() is None)
CA.write_bytes(good + KEY.read_bytes())
check("a certificate with a key beside it is refused", tlsid.ca_pem() is None)
CA.write_bytes(b"not a certificate")
check(
    "text that is not a certificate is refused",
    tlsid.ca_pem() is None and tlsid.fingerprint(b"junk") is None,
)
CA.write_bytes(b"-----BEGIN CERTIFICATE-----\n!!!notbase64!!!\n-----END CERTIFICATE-----\n")
check("a damaged certificate has no fingerprint", tlsid.fingerprint(tlsid.ca_pem() or b"") is None)
CA.unlink()
check("no file: nothing", tlsid.ca_pem() is None and tlsid.fingerprint(b"") is None)
(TMP / "certs" / "totr-ca.key").write_bytes(KEY.read_bytes())
check("the authority's key file next to it is never looked at", tlsid.ca_pem() is None)

# the public route and the admin row
SESSIONS = {
    "admin": {
        "user_id": 1,
        "username": "a",
        "role": "admin",
        "permissions": permissions.effective("admin", ""),
        "csrf": "t",
        "token_hash": "h",
    }
}
service.configured = lambda: True
service.validate_session = lambda t: SESSIONS.get(t)
client = TestClient(main.app)
r = client.get("/.well-known/totr-ca.pem")
check(
    "no authority file: the route answers 404 (and does not ask anyone to sign in)",
    r.status_code == 404,
    r.status_code,
)
CA.write_bytes(good)
r = client.get("/.well-known/totr-ca.pem")
check(
    "anyone may fetch the public certificate: a PEM, nothing else",
    r.status_code == 200
    and r.content == tlsid.ca_pem()
    and b"PRIVATE" not in r.content
    and r.headers["content-type"].startswith("application/x-pem-file"),
    (r.status_code, r.headers.get("content-type")),
)
check(
    "it is not cached (a renewed authority shows at once)",
    r.headers.get("cache-control") == "no-cache",
)
check(
    "only that exact path is public",
    client.get("/.well-known/", follow_redirects=False).status_code in (303, 401, 404)
    and client.get("/.well-known/totr-ca.key", follow_redirects=False).status_code
    in (303, 401, 404),
)
check(
    "the key file cannot be fetched through /static or anywhere else",
    client.get("/static/../certs/totr-ca.key", follow_redirects=False).status_code
    in (303, 400, 401, 404)
    and client.get("/certs/totr-ca.key", follow_redirects=False).status_code in (303, 401, 404),
)
o = client.get("/api/admin/updates", headers={"cookie": "totr_session=admin"}).json()
check("the admin Updates page API carries the same fingerprint", o["ca"] == fp, o.get("ca"))
service.create_invite = lambda *a, **k: "T" * 43
mk = lambda: client.post(
    "/api/admin/invites",
    json={"role": "member"},
    headers={"cookie": "totr_session=admin", "X-CSRF-Token": "t"},
)
r = mk()
check(
    "an invite made by an admin carries the authority's code next to its path",
    r.status_code == 200 and r.json()["code"] == fp and r.json()["path"] == "/invite/" + "T" * 43,
    r.text,
)
check(
    "the code is the public fingerprint only, never the certificate or a key",
    set(r.json()) == {"token", "path", "code", "days", "invite_id"} and "BEGIN" not in r.text,
)
check("invite_code() is the fingerprint", tlsid.invite_code() == fp)
users_page = (PF / "templates" / "users.html").read_text(encoding="utf-8")
check(
    "the admin page puts the code after a # in the link it shows", "'#fp=' + j.code" in users_page
)
CA.write_bytes(b"not a certificate")
r = mk()
check(
    "with no usable authority the code is empty and the invite still works",
    r.status_code == 200 and r.json()["code"] == "" and tlsid.invite_code() == "",
    r.text,
)
CA.write_bytes(good)
page = (PF / "templates" / "updates.html").read_text(encoding="utf-8")
check(
    "and the page shows it as text (the row is built with textContent helpers)",
    "o.ca" in page and "innerHTML" not in page,
)

shutil.rmtree(TMP, ignore_errors=True)
finish()
