"""The listener app (TOTRspa, built into static/app/) is served at /app/ without sign-in, with its
own headers; the API stays locked."""

import os
import pathlib
import sys
import tempfile

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["DB_AUTH_USER"] = "x"
os.environ["DB_AUTH_PASSWORD"] = "y"

from check import check, finish
from fastapi.testclient import TestClient

import main
from auth import web

APP = pathlib.Path(PF) / "static" / "app"
(APP / "assets").mkdir(parents=True, exist_ok=True)
files = {
    APP / "_t_version.json": '{"buildId": "t"}',
    APP / "assets" / "_t_hash.js": "console.log(1)",
    APP / "_t_index.html": "<!doctype html><title>t</title>",
}
try:
    for f, text in files.items():
        f.write_text(text)
    cl = TestClient(main.app)
    r = cl.get("/app/_t_version.json", follow_redirects=False)
    check(
        "anonymous: /app/version.json is served without sign-in",
        r.status_code == 200 and r.json() == {"buildId": "t"},
        r.status_code,
    )
    check(
        "the version file is re-checked every time (no-cache), so a running app can notice a new build",
        r.headers.get("cache-control") == "no-cache",
        r.headers.get("cache-control"),
    )
    r = cl.get("/app/assets/_t_hash.js")
    check(
        "hashed asset files are cacheable for a year",
        "max-age=31536000" in r.headers.get("cache-control", "")
        and "immutable" in r.headers.get("cache-control", ""),
        r.headers.get("cache-control"),
    )
    check(
        "scripts keep a JavaScript type under nosniff",
        r.headers.get("content-type", "").startswith("text/javascript")
        and r.headers.get("x-content-type-options") == "nosniff",
        r.headers.get("content-type"),
    )
    r = cl.get("/app/_t_index.html")
    check(
        "the app page is never served from a stale cache",
        r.status_code == 200 and r.headers.get("cache-control") in ("no-cache", "no-store"),
        (r.status_code, r.headers.get("cache-control")),
    )
    csp = r.headers.get("content-security-policy", "")
    check(
        "the app gets a CSP that lets its own scripts run but nothing from elsewhere",
        "script-src 'self'" in csp
        and "'unsafe-inline'" not in csp.split("style-src")[0]
        and "connect-src 'self'" in csp
        and "font-src 'self'" in csp
        and "frame-ancestors 'none'" in csp,
        csp,
    )
    check(
        "/app redirects to /app/ rather than to the login page",
        cl.get("/app", follow_redirects=False).headers.get("location", "").endswith("/app/"),
        cl.get("/app", follow_redirects=False).headers,
    )
    check(
        "a path outside /app/ is still locked (303 to login)",
        cl.get("/archive", follow_redirects=False).status_code == 303,
    )
    check(
        "the API is still locked",
        cl.get("/api/artists").status_code == 401 and cl.get("/api/auth/me").status_code == 401,
    )
    check(
        "the downloads folder follows /static/ (public) while /api/admin stays locked",
        cl.get("/api/admin/users").status_code == 401,
    )
    check(
        "path tricks do not escape /app/",
        all(
            cl.get(p).status_code in (404, 400, 401, 403)
            for p in ("/app/%2e%2e/auth/web.py", "/app/..%2fauth%2fweb.py")
        ),
    )
    check("the portal's own pages keep their strict CSP", web.APP_CSP != web.csp("n"))
finally:
    for f in files:
        f.unlink(missing_ok=True)
    try:
        (APP / "assets").rmdir()
    except OSError:
        pass  # a real build lives there: leave it alone

finish()
