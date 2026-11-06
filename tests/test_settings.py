"""The Settings page (account, appearance, site), the themes behind it, and the account API
(profile, devices, sign out one device). The theme engine itself is tested in Node
(tests/theme_test.js, run from here). Pages and the API run on the built-in local database in a
temporary folder. Nothing real is touched."""

import os
import pathlib
import re
import subprocess
import sys
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["DB_ENGINE"] = "sqlite"
os.environ["TOTR_DATA_DIR"] = str(tmp / "data")
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["APP_SETTINGS"] = str(tmp / "reports" / "app_settings.json")

# 1. the theme engine (Node)
import shutil

from check import check, finish, relay

node = shutil.which("node")
if node:
    out = subprocess.run(
        [node, str(PF / "tests" / "theme_test.js")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines = out.stdout.splitlines()
    relay("theme engine", lines)
    check(
        "theme engine: the Node test ran to the end",
        any("ALL PASSED" in l or "FAILED" in l for l in lines),
        out.stderr[-500:],
    )
    r = subprocess.run(
        [node, "--check", str(PF / "static" / "theme.js")], capture_output=True, text=True
    )
    check("theme.js is valid JavaScript", r.returncode == 0, r.stderr[:300])
else:
    print("skip  theme engine tests  (Node is not installed)")

# 2. the stylesheet has no colors a theme cannot reach
css = (PF / "static" / "style.css").read_text(encoding="utf-8")
body = css[re.search(r"\*\s*\{\s*box-sizing", css).start() :]
left = [m for m in re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", body) if "var(" not in m]
check(
    "style.css: every color outside :root is a variable, so a theme restyles everything",
    not left,
    left,
)
used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
defined = set(re.findall(r"^\s*(--[a-z0-9-]+):", css, re.M))
check(
    "style.css: every variable it uses is defined",
    used <= defined | {"--iris"},
    sorted(used - defined),
)
for page in ("radio", "admin"):
    t = (PF / "templates" / f"{page}.html").read_text(encoding="utf-8")
    left = [m for m in re.findall(r"#[0-9a-fA-F]{6}\b|rgba?\(\d[^)]*\)", t)]
    check(f"{page}.html: no fixed colors left, the theme reaches it", not left, left)

# an element id that the shared stylesheet already styles (like the archive's #now player bar)
# restyles the new page by accident
css_ids = set(
    re.findall(r"#([A-Za-z][A-Za-z0-9_-]*)\s*[{,.:\[> ]", re.sub(r"#[0-9a-fA-F]{3,8}", "", css))
)
for page in ("settings", "database"):
    ids = set(
        re.findall(r'id="([^"]+)"', (PF / "templates" / f"{page}.html").read_text(encoding="utf-8"))
    )
    check(
        f"{page}.html: none of its element ids collide with ids style.css already styles",
        not (ids & css_ids - {"logout"}),
        sorted(ids & css_ids),
    )

# 3. pages and the account API
from fastapi.testclient import TestClient

import main
from auth import service
from auth.limiter import LoginLimits

PW = "correct horse battery staple"
service.limits = LoginLimits()
uid = service.create_user("Nova", PW, "member")
service.create_user("Gast", PW, "member")
service.create_user("Chef", PW, "admin")


def client(user):
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": user, "password": PW})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = c.get("/api/auth/me").json()["csrf"]
    return c


nova, gast, chef = client("Nova"), client("Gast"), client("Chef")
anon = TestClient(main.app)

page = nova.get("/settings")
check("/settings opens for a member", page.status_code == 200 and "<h1>Settings</h1>" in page.text)
check(
    "/account (the old address) still opens the same page",
    nova.get("/account").status_code == 200 and "<h1>Settings</h1>" in nova.get("/account").text,
)
check(
    "the settings page is locked for anonymous visitors",
    anon.get("/settings", follow_redirects=False).status_code in (302, 303),
)
head = page.text[: page.text.index("</head>")]
check(
    "every page loads the theme before the page paints (theme.js in <head>, before app.js)",
    head.index("/static/theme.js") < head.index("/static/app.js"),
)
check(
    "the page names the signed-in person for per-person theme storage",
    '<meta name="totr-user" content="Nova">' in page.text,
)
login = anon.get("/login")
check(
    "the sign-in page gets the theme too, with no person name",
    "/static/theme.js" in login.text and "totr-user" not in login.text,
)
check(
    "the header has the logo mark element (hidden unless a theme asks for it)",
    'class="logo-mark"' in page.text,
)
check(
    "the navigation says Settings and points at /settings",
    'href="/settings"' in page.text
    and ">settings<" in page.text
    and 'href="/account"' not in page.text,
)
check(
    "the page has no inline handlers and never uses innerHTML (CSP, and no data becomes markup)",
    " onclick=" not in page.text
    and "innerHTML" not in page.text
    and "on" + "error=" not in page.text,
)
check(
    "the Site tab (contact setting) is in the page for admins to unlock, and stays hidden by default",
    'data-tab="site" hidden' in page.text
    and 'id="siteForm"' in page.text
    and "includes('settings')" in page.text,
)
check(
    "theme.js is served as JavaScript to anyone (the sign-in page needs it)",
    anon.get("/static/theme.js").status_code == 200
    and "javascript" in anon.get("/static/theme.js").headers["content-type"],
)
check(
    "the radio and admin pages also load the theme",
    "/static/theme.js" in chef.get("/radio").text and "/static/theme.js" in chef.get("/admin").text,
)

acct = nova.get("/api/account").json()
check(
    "account API: username, role, member since, last sign-in",
    acct["username"] == "Nova"
    and acct["role"] == "member"
    and acct["created_at"]
    and acct["last_login_at"],
    acct,
)
check(
    "account API: lists this device as current",
    len(acct["sessions"]) == 1
    and acct["sessions"][0]["current"] is True
    and len(acct["sessions"][0]["id"]) == 16,
    acct["sessions"],
)
check(
    "account API: never returns token hashes or cookies, only the 16-character id",
    all(
        set(s) == {"id", "current", "created_at", "last_seen", "ip", "user_agent"}
        for s in acct["sessions"]
    ),
)
check("account API: needs sign-in", anon.get("/api/account").status_code == 401)

second = TestClient(main.app)
second.post(
    "/api/auth/login",
    json={"username": "Nova", "password": PW},
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537"},
)
sessions = nova.get("/api/account").json()["sessions"]
other = next(s for s in sessions if not s["current"])
check(
    "a second sign-in shows up as another device",
    len(sessions) == 2 and "Chrome" in other["user_agent"],
    sessions,
)
check(
    "signing out another device needs the CSRF token",
    TestClient(main.app).post(f"/api/account/sessions/{other['id']}/revoke").status_code
    in (401, 403),
)
nocsrf = TestClient(main.app)
nocsrf.cookies.update(nova.cookies)
check(
    "...and a cookie alone is not enough",
    nocsrf.post(f"/api/account/sessions/{other['id']}/revoke").status_code == 403,
)
cur = next(s for s in sessions if s["current"])
check(
    "the current device cannot be removed here",
    nova.post(f"/api/account/sessions/{cur['id']}/revoke").status_code == 404
    and nova.get("/api/account").status_code == 200,
)
check(
    "someone else cannot sign out my device",
    gast.post(f"/api/account/sessions/{other['id']}/revoke").status_code == 404
    and second.get("/api/account").status_code == 200,
)
check(
    "junk ids are refused",
    all(
        nova.post(f"/api/account/sessions/{x}/revoke").status_code in (404, 422)
        for x in ("zz", "0" * 16, "../../etc", "a" * 17, "%27%20OR%201%3D1")
    ),
)
r = nova.post(f"/api/account/sessions/{other['id']}/revoke")
check(
    "signing out another device works, and that device is really signed out",
    r.status_code == 200 and second.get("/api/account").status_code == 401,
    r.text,
)
check(
    "my own device still works",
    nova.get("/api/account").status_code == 200
    and len(nova.get("/api/account").json()["sessions"]) == 1,
)
check(
    "the same device cannot be removed twice",
    nova.post(f"/api/account/sessions/{other['id']}/revoke").status_code == 404,
)
check(
    "every route under /api/account needs no permission, only sign-in",
    main.auth_web.required_permission("/api/account") is None
    and main.auth_web.required_permission("/api/account/sessions/x/revoke") is None
    and main.auth_web.required_permission("/settings") is None,
)

finish()
