"""The download page: the newest installer of each app is found, and the page and its list are open
to everyone (a new device has no account yet)."""

import os
import pathlib
import sys
import tempfile

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
# never look at this checkout's real certs/
os.environ["TOTR_STATE_DIR"] = str(pathlib.Path(tempfile.mkdtemp()))
os.environ["DB_AUTH_USER"] = "x"
os.environ["DB_AUTH_PASSWORD"] = "y"
os.environ["MAINT_REPORT"] = str(pathlib.Path(tempfile.mkdtemp()) / "reports" / "maintenance.json")

from check import check, finish
from fastapi.testclient import TestClient

import downloads
import main

d = pathlib.Path(tempfile.mkdtemp())
for n in [
    "TOTR-0.5.1-android.apk",
    "TOTR-0.10.0-android.apk",
    "TOTR-0.9.9-android.apk",
    "TOTR.apk",
    "TOTR-0.5.2-linux.AppImage",
    "TOTR-0.5.2-linux.AppImage.blockmap",
    "latest-linux.yml",
    "TOTR-0.5.2-linux.AppImage.part",
    "TOTR-x-win.exe",
]:
    (d / n).write_bytes(b"x" * 10)
rows = {r["platform"]: r for r in downloads.latest(d)}
check(
    "one row per platform, in order",
    [r["platform"] for r in downloads.latest(d)] == ["android", "linux", "win"],
)
check(
    "0.10.0 counts as newer than 0.9.9 (numbers, not text)",
    rows["android"]["version"] == "0.10.0" and rows["android"]["file"] == "TOTR-0.10.0-android.apk",
    rows["android"],
)
check(
    "the link, size and date are filled in",
    rows["android"]["url"] == "/static/downloads/TOTR-0.10.0-android.apk"
    and rows["android"]["size"] == 10
    and rows["android"]["updated"],
    rows["android"],
)
check(
    "blockmap, .part, yml and the fixed-name copy are not mistaken for installers",
    rows["linux"]["file"] == "TOTR-0.5.2-linux.AppImage",
    rows["linux"],
)
check(
    "a platform with nothing uploaded says so",
    rows["win"]["version"] is None and rows["win"]["url"] is None,
    rows["win"],
)
check(
    "a missing folder is not an error",
    all(r["version"] is None for r in downloads.latest(d / "nope")),
)
cl = TestClient(main.app)
r = cl.get("/download", follow_redirects=False)
page = r.text
check(
    "anonymous: the download page opens without signing in",
    r.status_code == 200 and "Get the app" in r.text,
    r.status_code,
)
check(
    "the page keeps the strict script policy",
    "nonce-" in r.headers.get("content-security-policy", "") and "<script nonce=" in r.text,
    r.headers.get("content-security-policy"),
)
r = cl.get("/api/downloads", follow_redirects=False)
check(
    "anonymous: the list opens without signing in",
    r.status_code == 200
    and [a["platform"] for a in r.json()["apps"]] == ["android", "linux", "win"],
    r.status_code,
)
check(
    "the old admin-only route is gone",
    cl.get("/api/admin/downloads", follow_redirects=False).status_code in (401, 403, 404, 503),
)

check(
    "no authority published: the list says there is no certificate step",
    r.json()["certificate"] is None,
    r.json(),
)
import subprocess

import tlsid

state = pathlib.Path(os.environ["TOTR_STATE_DIR"])
(state / "certs").mkdir()
subprocess.run(
    [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "ed25519",
        "-nodes",
        "-keyout",
        str(state / "ca.key"),
        "-out",
        str(state / "certs" / "totr-ca.pem"),
        "-subj",
        "/CN=Test CA",
        "-days",
        "30",
    ],
    capture_output=True,
    check=True,
)
c = cl.get("/api/downloads").json()["certificate"]
check(
    "with an authority: its address and fingerprint (the certificate only)",
    c == {"url": "/.well-known/totr-ca.pem", "fingerprint": tlsid.fingerprint(tlsid.ca_pem())}
    and cl.get(c["url"]).status_code == 200,
    c,
)
check(
    "the page shows the steps for the visitor's device, from text only (no markup built from the network)",
    all(w in page for w in ("function stepsFor", "'android'", "'win'", "'linux'", "ios"))
    and ".innerHTML" not in page,
)
check(
    ".apk goes out as an Android package, not a plain download",
    'add_type("application/vnd.android.package-archive", ".apk")'
    in pathlib.Path("main.py").read_text(encoding="utf-8"),
)
import mimetypes

check(
    "...and the table the server actually uses answers that",
    mimetypes.guess_type("TOTR.apk")[0] == "application/vnd.android.package-archive",
    mimetypes.guess_type("TOTR.apk"),
)

finish()
