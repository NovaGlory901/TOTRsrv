"""The admin Updates page and its API through the real sign-in gate: who may open them, what they
report, and that the page draws everything as text. Sessions are faked (no database); nothing here
touches the network (the update host calls are not reached: nothing is set up, or the call is
refused first)."""

import base64
import json
import os
import pathlib
import re
import sys
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
os.chdir(PF)
TMP = pathlib.Path(tempfile.mkdtemp(prefix="totr-updpage-"))
os.environ["TOTR_STATE_DIR"] = str(TMP / "state")
os.environ["UPDATE_PUBKEY_FILE"] = str(TMP / "pubkey.txt")
os.environ.pop("UPDATE_REPO", None)
os.environ["DB_AUTH_USER"] = "x"
os.environ["DB_AUTH_PASSWORD"] = "y"
os.environ["MAINT_REPORT"] = str(TMP / "reports" / "maintenance.json")

from check import check, finish
from fastapi.testclient import TestClient

import main
from auth import permissions, service


def user(name, role, perms=()):
    return {
        "user_id": 1,
        "username": name,
        "role": role,
        "permissions": permissions.effective(role, ",".join(perms)),
        "csrf": "tok-" + name,
        "token_hash": "h-" + name,
    }


SESSIONS = {
    "admin": user("admin", "admin"),
    "member": user("member", "member"),
    "status": user("status", "member", ("status",)),
    "maint": user("maint", "member", ("maintenance", "collector", "users", "settings", "upload")),
}
service.configured = lambda: True
service.validate_session = lambda token: SESSIONS.get(token)
client = TestClient(main.app)
as_ = lambda who: {"cookie": f"totr_session={who}"}
post = lambda path, who: client.post(
    path,
    headers={**as_(who), "X-CSRF-Token": SESSIONS[who]["csrf"], "Content-Length": "0"},
    content=b"",
)

# who may open it
r = client.get("/admin/updates", follow_redirects=False)
check(
    "anonymous: the page sends you to sign in",
    r.status_code == 303 and "/login" in r.headers["location"],
    r.status_code,
)
check("anonymous: the API is locked", client.get("/api/admin/updates").status_code == 401)
for who in ("member", "status", "maint"):
    check(
        f"{who} (not the admin role, even with other permissions): the page is refused",
        client.get("/admin/updates", headers=as_(who)).status_code == 403,
    )
    check(
        f"{who}: the API is refused, and so are check and install",
        client.get("/api/admin/updates", headers=as_(who)).status_code == 403
        and post("/api/admin/updates/check", who).status_code == 403
        and post("/api/admin/updates/install", who).status_code == 403,
    )
r = client.get("/admin/updates", headers=as_("admin"))
check("the admin gets the page", r.status_code == 200 and "<h1>Updates</h1>" in r.text)
check(
    "the admin menu has the new link, and only the admin sees it",
    'href="/admin/updates"' in r.text
    and 'href="/admin/updates"' not in client.get("/admin/users", headers=as_("maint")).text,
)
check(
    "the menu marks it as the current page",
    re.search(r'<a href="/admin/updates" aria-current="page">updates</a>', r.text) is not None,
)
check(
    "the script gets the page's nonce, and there are no inline event handlers",
    re.search(r'<script nonce="[^"]+">', r.text) is not None
    and not re.search(r"\son(click|load|error)=", r.text),
)
check(
    "the page's CSP has no unsafe-inline for scripts",
    "script-src 'self' 'nonce-" in r.headers["content-security-policy"]
    and "unsafe-inline" not in r.headers["content-security-policy"].split("style-src")[0],
)

# what the API reports
o = client.get("/api/admin/updates", headers=as_("admin")).json()
check(
    "with nothing set up it says so and why",
    o["configured"] is False
    and o["repo"] == ""
    and o["key"] is None
    and "repository" in o["problem"]
    and o["current"],
    o,
)
check(
    "it says which database engine is in use (the page warns PostgreSQL admins to back up)",
    o["engine"] in ("sqlite", "postgres"),
)
r = post("/api/admin/updates/install", "admin")
check(
    "installing when nothing is set up is refused with a reason, not started",
    r.status_code == 409 and "repository" in r.json()["detail"],
    r.text,
)
r = post("/api/admin/updates/check", "admin")
check(
    "a check when nothing is set up answers without touching the network",
    r.status_code == 200 and r.json()["configured"] is False and "problem" in r.json(),
)

(TMP / "pubkey.txt").write_text(
    "ed25519 " + base64.b64encode(bytes(range(32))).decode() + " test\n"
)
os.environ["UPDATE_REPO"] = "someone/TOTR"
o = client.get("/api/admin/updates", headers=as_("admin")).json()
check(
    "with a repository and a key it is set up and shows the key's fingerprint",
    o["configured"] is True
    and o["repo"] == "someone/TOTR"
    and re.fullmatch(r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}", o["key"] or ""),
    o,
)
import hashlib

digits = hashlib.sha256(bytes(range(32))).hexdigest()[:12]
check(
    "the fingerprint is the first 12 hex digits of the key's SHA-256, in groups of four",
    o["key"] == "-".join(digits[i : i + 4] for i in range(0, 12, 4)),
    (o["key"], digits),
)

upd = TMP / "state" / "updates"
upd.mkdir(parents=True)
(upd / "status.json").write_text(
    json.dumps(
        {
            "stage": "rolled_back",
            "release": "0.7.0",
            "from": "0.6.3",
            "message": "0.7.0 did not start",
            "log": ["a", "b"],
        }
    )
)
(upd / "app-status.json").write_text(json.dumps({"stage": "waiting", "release": "0.7.0"}))
o = client.get("/api/admin/updates", headers=as_("admin")).json()
check(
    "what the update service said is passed on",
    o["service"]["stage"] == "rolled_back"
    and o["service"]["log"] == ["a", "b"]
    and o["fetching"]["stage"] == "waiting",
    o,
)

# the page draws text, never HTML
page = (PF / "templates" / "updates.html").read_text(encoding="utf-8")
script = page.split("<script>", 1)[1]
check(
    "the page never puts server text into the page as HTML (no innerHTML, no document.write, no eval)",
    not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(", script),
)
check(
    "the page uses the shared helpers (apiFetch carries the CSRF token on POST)",
    "apiFetch('/api/admin/updates" in script and "method: 'POST'" in script,
)
check("installing asks first (it restarts the server)", "confirm(" in script)

import shutil

shutil.rmtree(TMP, ignore_errors=True)
finish()
