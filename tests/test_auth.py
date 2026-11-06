import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
# an unset role means "not configured" only on PostgreSQL; SQLite counts every role as enabled
os.environ["DB_ENGINE"] = "postgres"
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ.pop("COOKIE_SECURE", None)
for k in ("DB_AUTH_USER", "DB_AUTH_PASSWORD", "ADMIN_PASSWORD", "UPLOAD_PASSWORD"):
    os.environ.pop(k, None)

from dataclasses import asdict

# a throwaway SQLite database with the same tables, so the real queries run
import sqlite_layout as layout
from check import check, finish
from fastapi.testclient import TestClient

import main
from auth import passwords, service, store, web
from auth.limiter import Limiter, LoginLimits
from spiders import actions
from spiders import run as spider_run
from spiders import store as spider_store
from spiders.base import Finding, propose

DB_PATH = tmp / "auth.sqlite"
layout.create(DB_PATH, layout.ACCOUNTS)
conv, Shim = layout.conv, layout.shim(DB_PATH)
raw = lambda sql, params=(): layout.raw(DB_PATH, sql, params)
service.connect = Shim
PW = "correct horse battery staple"

# 1. fail closed when accounts are not configured
cl = TestClient(main.app)
r = cl.get("/", follow_redirects=False)
check("unconfigured: pages are locked (503), not open", r.status_code == 503, r.status_code)
check("unconfigured: /api is locked", cl.get("/api/artists").status_code == 503)
check("unconfigured: login page still loads", cl.get("/login").status_code == 200)
check("unconfigured: /health is public", cl.get("/health").status_code in (200, 503))

os.environ["DB_AUTH_USER"] = "x"
os.environ["DB_AUTH_PASSWORD"] = "y"

# 2. anonymous access
cl = TestClient(main.app)
r = cl.get("/", follow_redirects=False)
check(
    "anonymous: page redirects to login",
    r.status_code == 303 and r.headers["location"].startswith("/login?next="),
    r.headers.get("location"),
)
for path in (
    "/api/artists",
    "/api/tracks/1/audio",
    "/api/radio/next",
    "/api/albums/1/cover",
    "/api/library/stats",
    "/api/maintenance",
    "/api/health",
    "/api/admin/users",
    "/api/auth/me",
):
    check(
        f"anonymous: GET {path} is 401", cl.get(path).status_code == 401, cl.get(path).status_code
    )
check("anonymous: POST /api/upload is 401", cl.post("/api/upload", content=b"x").status_code == 401)
check("anonymous: static css is public", cl.get("/static/style.css").status_code == 200)
r = cl.get("/static/app.js")
check(
    "static files are always re-checked, so an update is never hidden by an old cached script",
    r.headers.get("cache-control") == "no-cache" and r.headers.get("etag"),
    dict(r.headers),
)
r2 = cl.get("/static/app.js", headers={"If-None-Match": r.headers["etag"]})
check(
    "...and re-checking is cheap: an unchanged file answers 304 with no body",
    r2.status_code == 304 and not r2.content,
)
check(
    "anonymous: /docs is not served",
    cl.get("/docs", follow_redirects=False).status_code in (303, 401, 404),
)
check(
    "anonymous: /openapi.json is not served",
    cl.get("/openapi.json", follow_redirects=False).status_code in (303, 401, 404),
)
h = cl.get("/health")
check("public /health exposes only the status word", set(h.json()) == {"status"}, h.text)
check("anonymous: no path leaks via /api/health", "file_path" not in cl.get("/api/health").text)

# 3. account creation rules
for bad, why in (
    ("short", "too short"),
    ("aaaaaaaaaaaaaaaa", "repetitive"),
    ("password12345", "common"),
    ("nova-nova-nova-x", "contains username"),
):
    try:
        service.create_user("nova", bad, "admin")
        check(f"rejects {why} password", False)
    except service.AuthError:
        check(f"rejects {why} password", True)
try:
    service.create_user("bad name!", PW)
    check("rejects bad username", False)
except service.AuthError:
    check("rejects bad username", True)
admin_id = service.create_user("Nova", PW, "admin")
try:
    service.create_user("nova", PW)
    check("usernames are unique ignoring case", False)
except service.AuthError as e:
    check("usernames are unique ignoring case", e.status == 409)
stored = raw("SELECT password_hash FROM totr_users")[0]["password_hash"]
check(
    "password stored only as an argon2id hash", stored.startswith("$argon2id$") and PW not in stored
)


# 4. sign in
def login(client, user="Nova", pw=PW):
    return client.post("/api/auth/login", json={"username": user, "password": pw})


service.limits = LoginLimits()
r1 = login(cl, "Nova", "wrong password here")
r2 = login(cl, "nobody-here", "wrong password here")
check(
    "wrong password and unknown user give the same message and status",
    r1.status_code == r2.status_code == 401 and r1.json() == r2.json(),
    (r1.json(), r2.json()),
)

calls = []
real_verify = passwords.verify_password
passwords.verify_password = lambda h, p: (calls.append(h), real_verify(h, p))[1]
login(cl, "ghost-user", "whatever whatever")
passwords.verify_password = real_verify
check(
    "unknown user still pays for a full hash check (no timing leak)",
    calls == [passwords.DUMMY_HASH],
)

admin = TestClient(main.app)
r = login(admin)
check("correct login works", r.status_code == 200 and r.json()["role"] == "admin")
sc = r.headers["set-cookie"].lower()
check("cookie is HttpOnly", "httponly" in sc)
check("cookie is SameSite=lax", "samesite=lax" in sc)
check("cookie has a limited lifetime", "max-age=" in sc)
check("no Secure flag over plain http (auto)", "secure" not in sc)
os.environ["COOKIE_SECURE"] = "true"
check(
    "Secure flag when COOKIE_SECURE=true",
    "secure" in login(TestClient(main.app)).headers["set-cookie"].lower(),
)
os.environ.pop("COOKIE_SECURE")
cookie = admin.cookies.get("totr_session")
rows = raw(
    "SELECT token_hash FROM totr_sessions WHERE token_hash = ?",
    (hashlib.sha256(cookie.encode()).hexdigest(),),
)
check(
    "DB holds only the sha256 of the session token",
    bool(cookie)
    and len(rows) == 1
    and cookie not in json.dumps(raw("SELECT * FROM totr_sessions")),
)

# 5. signed-in pages: headers, nonce, CSRF token
r = admin.get("/")
csp = r.headers["content-security-policy"]
nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
check("page CSP carries a nonce", bool(nonce))
check(
    "every inline <script> has that nonce",
    r.text.count("<script>") == 0 and f'<script nonce="{nonce}">' in r.text,
)
check(
    "CSP blocks everything by default",
    "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp,
)
check(
    "CSP does not allow unsafe-inline scripts", not re.search(r"script-src[^;]*unsafe-inline", csp)
)
check("page embeds the CSRF token", 'name="csrf-token" content="' in r.text)
check(
    "nonce differs per request",
    re.search(r"'nonce-([^']+)'", admin.get("/").headers["content-security-policy"]).group(1)
    != nonce,
)
for hname in (
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
):
    check(f"header {hname} present", hname in r.headers)
check("pages are not cached", r.headers.get("cache-control") == "no-store")
check(
    "even signed-in, /docs and /openapi.json are 404",
    admin.get("/docs").status_code == 404 and admin.get("/openapi.json").status_code == 404,
)
me = admin.get("/api/auth/me")
check(
    "/api/auth/me returns csrf for this session",
    me.status_code == 200 and len(me.json()["csrf"]) > 20,
)
check(
    "JSON responses get a locked-down CSP",
    admin.get("/api/auth/me").headers["content-security-policy"].startswith("default-src 'none'"),
)
csrf = me.json()["csrf"]
H = {"X-CSRF-Token": csrf}
if layout.live_ready():
    check(
        "member APIs work when signed in (real DB read)",
        admin.get("/api/library/stats").status_code == 200,
    )
else:
    print(
        "skip  member APIs work when signed in (real DB read)  (no database with the TOTR layout is set up)"
    )

# 6. CSRF, Origin, size
check("POST without CSRF token is refused", admin.post("/api/auth/signout-all").status_code == 403)
check(
    "POST with wrong CSRF token is refused",
    admin.post("/api/auth/signout-all", headers={"X-CSRF-Token": "nope"}).status_code == 403,
)
check(
    "cross-site Origin is refused even with a valid token",
    admin.post(
        "/api/admin/invites",
        json={"role": "member"},
        headers={**H, "Origin": "https://evil.example"},
    ).status_code
    == 403,
)
check(
    "same-site Origin is accepted",
    admin.post(
        "/api/admin/invites", json={"role": "member"}, headers={**H, "Origin": "http://testserver"}
    ).status_code
    == 200,
)
check(
    "oversized non-upload body is refused (413)",
    admin.post(
        "/api/auth/change-password",
        content=b"x" * 70000,
        headers={**H, "Content-Type": "application/json"},
    ).status_code
    == 413,
)
check("GET never needs a CSRF token", admin.get("/api/auth/me").status_code == 200)
check(
    "a non-ASCII CSRF header is a 403, not a crash",
    admin.post("/api/auth/signout-all", headers={"X-CSRF-Token": "é".encode("latin-1")}).status_code
    == 403,
)
check(
    "the upload size limit covers the tag-inspect route too (not the 64 KB body limit)",
    admin.post(
        "/api/upload/inspect",
        content=b"x" * 70000,
        headers={**H, "Content-Type": "application/octet-stream"},
    ).status_code
    != 413,
)

# 7. invites and roles
inv = admin.post("/api/admin/invites", json={"role": "member"}, headers=H).json()
token = inv["token"]
check("invite token is long and random", len(token) >= 40)
check(
    "invite stored only as a hash",
    raw("SELECT count(*) n FROM totr_invites WHERE token_hash = ?", (token,))[0]["n"] == 0
    and raw(
        "SELECT count(*) n FROM totr_invites WHERE token_hash = ?",
        (hashlib.sha256(token.encode()).hexdigest(),),
    )[0]["n"]
    == 1,
)
guest = TestClient(main.app)
check("invite page is public", guest.get(f"/invite/{token}").status_code == 200)
check(
    "invite with weak password refused",
    guest.post(
        "/api/auth/accept-invite", json={"token": token, "username": "friend", "password": "short"}
    ).status_code
    == 422,
)
check(
    "invite with bad username refused",
    guest.post(
        "/api/auth/accept-invite", json={"token": token, "username": "a b", "password": PW}
    ).status_code
    == 422,
)
check(
    "bad invite token refused with a generic message",
    guest.post(
        "/api/auth/accept-invite", json={"token": "nope", "username": "friend", "password": PW}
    ).status_code
    == 400,
)
r = guest.post(
    "/api/auth/accept-invite", json={"token": token, "username": "friend", "password": PW + "!"}
)
check(
    "valid invite creates the account and signs in",
    r.status_code == 200 and r.json()["role"] == "member",
)
check(
    "invite can be used only once",
    TestClient(main.app)
    .post("/api/auth/accept-invite", json={"token": token, "username": "second", "password": PW})
    .status_code
    == 400,
)
# a taken username must not burn the invite
inv2 = admin.post("/api/admin/invites", json={"role": "member"}, headers=H).json()["token"]
check(
    "taken username refused (case-insensitive)",
    TestClient(main.app)
    .post("/api/auth/accept-invite", json={"token": inv2, "username": "FRIEND", "password": PW})
    .status_code
    == 409,
)
check(
    "...and the invite still works afterwards",
    TestClient(main.app)
    .post("/api/auth/accept-invite", json={"token": inv2, "username": "friend2", "password": PW})
    .status_code
    == 200,
)
inv3 = admin.post("/api/admin/invites", json={"role": "member"}, headers=H).json()["token"]
raw(
    "UPDATE totr_invites SET expires_at = ? WHERE token_hash = ?",
    (
        conv(datetime.now(UTC) - timedelta(days=1)),
        hashlib.sha256(inv3.encode()).hexdigest(),
    ),
)
check(
    "expired invite refused",
    TestClient(main.app)
    .post("/api/auth/accept-invite", json={"token": inv3, "username": "late", "password": PW})
    .status_code
    == 400,
)
check(
    "unknown role cannot be invited",
    admin.post("/api/admin/invites", json={"role": "root"}, headers=H).status_code == 422,
)

member = guest  # signed in as "friend"
mh = {"X-CSRF-Token": member.get("/api/auth/me").json()["csrf"]}
check(
    "member can browse",
    member.get("/archive").status_code == 200
    and (not layout.live_ready() or member.get("/api/artists").status_code == 200),
)
for path in (
    "/status",
    "/upload",
    "/admin/users",
    "/api/admin/users",
    "/api/maintenance",
    "/api/health",
):
    check(
        f"member is refused {path}",
        member.get(path, follow_redirects=False).status_code == 403,
        member.get(path).status_code,
    )
check(
    "member cannot upload", member.post("/api/upload", content=b"x", headers=mh).status_code == 403
)
check(
    "member cannot approve fixes",
    member.post("/api/maintenance/approve", json={"finding_id": "x"}, headers=mh).status_code
    == 403,
)
check(
    "member cannot create invites",
    member.post("/api/admin/invites", json={"role": "admin"}, headers=mh).status_code == 403,
)
check(
    "member page has no admin links",
    "/admin/users" not in member.get("/").text
    and "/status" not in member.get("/").text.split("<main>")[0],
)
check("admin page has admin links", "/admin/users" in admin.get("/").text)

# 8. brute force limits
service.limits = LoginLimits()
bf = TestClient(main.app)
codes = [login(bf, "Nova", f"guess number {i} wrong").status_code for i in range(6)]
check("5 wrong guesses allowed, 6th is blocked with 429", codes == [401] * 5 + [429], codes)
r = login(bf, "Nova", PW)
check(
    "a locked pair refuses even the right password",
    r.status_code == 429 and int(r.headers["retry-after"]) > 0,
)
check(
    "a different account is unaffected",
    login(TestClient(main.app), "friend", PW + "!").status_code == 200,
)
lim = Limiter(3, 60)
for _ in range(3):
    lim.record("k", now=100)
check(
    "limiter blocks then frees after the window",
    lim.retry_after("k", now=110) > 0 and lim.retry_after("k", now=200) == 0,
)
service.limits = LoginLimits()

# 9. SQL injection and hostile input
inj = [
    "admin' OR '1'='1",
    "x'; DROP TABLE users;--",
    "' UNION SELECT password_hash FROM totr_users--",
    "Nova' --",
    "nova\x00",
    "%",
    "\\",
]
for name in inj:
    r = login(TestClient(main.app), name, "whatever whatever")
    check(
        f"login with injection-style username is a plain 401 ({name[:20]!r})", r.status_code == 401
    )
check("users table survived", raw("SELECT count(*) n FROM totr_users")[0]["n"] >= 2)
with Shim() as conn:
    check(
        "store: hostile username matches nothing",
        store.get_user_by_name(conn, "' OR 1=1 --") is None,
    )
    check(
        "store: hostile token matches nothing",
        store.get_session(
            conn,
            "' OR 1=1 --",
            datetime.now(UTC),
            datetime.now(UTC),
            datetime.now(UTC),
        )
        is None,
    )
try:
    Shim().execute("SELECT 1 WHERE 'a' = 'b'", ("oops",))
    check("test shim rejects unbound parameters", False)
except AssertionError:
    check("test shim rejects unbound parameters", True)
src = "".join(
    open(f, encoding="utf-8").read() for f in ("auth/store.py", "auth/service.py", "auth/web.py")
)
check(
    "no f-string or % formatting builds SQL in the auth code",
    not re.search(r'execute\(\s*f["\']', src)
    and not re.search(r'execute\([^)]*["\']\s*%\s*\(', src),
)
for bad_session in ("' OR 1=1 --", "a" * 500, "\x00", ""):
    cc = TestClient(main.app)
    cc.cookies.set("totr_session", bad_session)
    check(
        f"forged/garbage session cookie is refused ({bad_session[:12]!r})",
        cc.get("/api/auth/me").status_code == 401,
    )

# 10. session lifecycle
check("logout works", admin.post("/api/auth/logout", headers=H).status_code == 200)
check("session is gone server-side after logout", admin.get("/api/auth/me").status_code == 401)
admin = TestClient(main.app)
login(admin)
H = {"X-CSRF-Token": admin.get("/api/auth/me").json()["csrf"]}
th = hashlib.sha256(admin.cookies.get("totr_session").encode()).hexdigest()
raw(
    "UPDATE totr_sessions SET last_seen = ? WHERE token_hash = ?",
    (conv(datetime.now(UTC) - timedelta(days=8)), th),
)
check("idle session (8 days) expires", admin.get("/api/auth/me").status_code == 401)
admin = TestClient(main.app)
login(admin)
H = {"X-CSRF-Token": admin.get("/api/auth/me").json()["csrf"]}
th = hashlib.sha256(admin.cookies.get("totr_session").encode()).hexdigest()
raw(
    "UPDATE totr_sessions SET expires_at = ? WHERE token_hash = ?",
    (conv(datetime.now(UTC) - timedelta(minutes=1)), th),
)
check("absolute-lifetime session expires", admin.get("/api/auth/me").status_code == 401)
admin = TestClient(main.app)
login(admin)
H = {"X-CSRF-Token": admin.get("/api/auth/me").json()["csrf"]}
check(
    "two logins get different session tokens",
    login(TestClient(main.app)).headers["set-cookie"].split(";")[0]
    != login(TestClient(main.app)).headers["set-cookie"].split(";")[0],
)

# 11. password change
other = TestClient(main.app)
login(other)
check(
    "wrong current password refused",
    admin.post(
        "/api/auth/change-password",
        json={"current": "nope nope nope", "new": "brand new passphrase 1"},
        headers=H,
    ).status_code
    == 403,
)
check(
    "weak new password refused",
    admin.post(
        "/api/auth/change-password", json={"current": PW, "new": "short"}, headers=H
    ).status_code
    == 422,
)
check(
    "same password refused",
    admin.post("/api/auth/change-password", json={"current": PW, "new": PW}, headers=H).status_code
    == 422,
)
r = admin.post(
    "/api/auth/change-password", json={"current": PW, "new": "brand new passphrase 1"}, headers=H
)
check("password change works", r.status_code == 200)
check("...this session survives", admin.get("/api/auth/me").status_code == 200)
check("...every other session is signed out", other.get("/api/auth/me").status_code == 401)
check("old password no longer works", login(TestClient(main.app), "Nova", PW).status_code == 401)
check(
    "new password works",
    login(TestClient(main.app), "Nova", "brand new passphrase 1").status_code == 200,
)
NEWPW = "brand new passphrase 1"
check(
    "sign out everywhere works",
    admin.post("/api/auth/signout-all", headers=H).status_code == 200
    and admin.get("/api/auth/me").status_code == 401,
)

# 12. admin controls
admin = TestClient(main.app)
login(admin, "Nova", NEWPW)
H = {"X-CSRF-Token": admin.get("/api/auth/me").json()["csrf"]}
users = admin.get("/api/admin/users").json()["users"]
ids = {u["username"]: u["user_id"] for u in users}
check(
    "user list never includes password hashes",
    "argon2" not in admin.get("/api/admin/users").text
    and "password" not in admin.get("/api/admin/users").text,
)
check(
    "admin cannot disable themselves",
    admin.post(f"/api/admin/users/{ids['Nova']}/disable", headers=H).status_code == 409,
)
member = TestClient(main.app)
login(member, "friend", PW + "!")
check("member is signed in before being disabled", member.get("/api/auth/me").status_code == 200)
check(
    "admin can disable a user",
    admin.post(f"/api/admin/users/{ids['friend']}/disable", headers=H).status_code == 200,
)
check("disabled user is signed out immediately", member.get("/api/auth/me").status_code == 401)
check(
    "disabled user cannot sign in (generic message)",
    login(TestClient(main.app), "friend", PW + "!").json()["detail"] == service.BAD_LOGIN,
)
check(
    "admin can re-enable",
    admin.post(f"/api/admin/users/{ids['friend']}/enable", headers=H).status_code == 200,
)
check(
    "re-enabled user can sign in",
    login(TestClient(main.app), "friend", PW + "!").status_code == 200,
)
try:
    service.set_disabled({"user_id": 999, "role": "admin"}, ids["Nova"], True)
    check("last admin protected", False)
except service.AuthError as e:
    check("last admin protected", e.status == 409)
check(
    "unknown user id gives 404",
    admin.post("/api/admin/users/99999/disable", headers=H).status_code == 404,
)

# 13. open redirect
for evil in (
    "//evil.example",
    "https://evil.example",
    "/\\evil.example",
    "javascript:alert(1)",
    "",
):
    check(f"safe_next rejects {evil!r}", web.safe_next(evil) == "/")
check(
    "safe_next allows same-site paths", web.safe_next("/archive#/artist/3") == "/archive#/artist/3"
)
r = admin.get("/login?next=//evil.example", follow_redirects=False)
check(
    "signed-in /login?next=//evil redirects home, not away",
    r.status_code == 303 and r.headers["location"] == "/",
)

# 14. maintenance runs as the signed-in admin
act = lambda n: propose("set_title", "Rename", "detail", {"track_id": n, "title": "x"})


def mk(kind, tid, action=None):
    f = Finding("files", kind, "warn", f"{kind} {tid}", {"track_id": tid}, action)
    f.id = spider_run.finding_id(f)
    return f


fs = [mk("tag_mismatch", 1, act(1)), mk("tag_mismatch", 2, act(2))]
spider_store.write_report(
    {
        "generated_at": "2026-09-30T00:00:00+00:00",
        "findings": [asdict(f) for f in fs],
        "severity": {},
        "kinds": {},
    }
)
ran = []
actions.execute = lambda a: (ran.append(a), "Title updated.")[1]
check(
    "approve needs no password field any more",
    admin.post("/api/maintenance/approve", json={"finding_id": fs[0].id}, headers=H).status_code
    == 200
    and len(ran) == 1,
)
check(
    "approve without CSRF token refused",
    admin.post("/api/maintenance/approve", json={"finding_id": fs[1].id}).status_code == 403,
)
check(
    "anonymous approve refused",
    TestClient(main.app).post("/api/maintenance/approve", json={"finding_id": fs[1].id}).status_code
    == 401,
)
log = [json.loads(l) for l in spider_store.AUDIT_PATH.read_text(encoding="utf-8").splitlines()]
check(
    "audit log records who approved",
    log[-1]["by"] == "Nova" and log[-1]["event"] == "approved",
    log[-1],
)
check(
    "finding_id is length limited",
    admin.post("/api/maintenance/approve", json={"finding_id": "x" * 500}, headers=H).status_code
    == 422,
)


# 15. per-user permissions
member = TestClient(main.app)
login(member, "friend", PW + "!")
mh = {"X-CSRF-Token": member.get("/api/auth/me").json()["csrf"]}
admin = TestClient(main.app)
login(admin, "Nova", NEWPW)
H = {"X-CSRF-Token": admin.get("/api/auth/me").json()["csrf"]}
import time

from auth import permissions

check(
    "permissions: names are fixed and validated",
    permissions.clean(["users", "upload", "upload"]) == ("upload", "users")
    and permissions.clean("collector, status") == ("collector", "status")
    and permissions.clean(None) == (),
)
try:
    permissions.clean(["upload", "root"])
    check("permissions: unknown names refused", False)
except ValueError:
    check("permissions: unknown names refused", True)
check(
    "permissions: admins hold everything whatever is stored; members only what is stored",
    permissions.effective("admin", "") == frozenset(permissions.ALL)
    and permissions.effective("member", "upload,bogus") == frozenset({"upload"}),
)

PROBES = {  # one harmless request per area: (method, path, permission it needs)
    "upload": [("GET", "/upload"), ("POST", "/api/upload")],
    "collector": [("GET", "/api/collector"), ("GET", "/discover")],
    "maintenance": [("GET", "/api/maintenance"), ("POST", "/api/maintenance/schedule")],
    "status": [("GET", "/api/health"), ("GET", "/status")],
    "settings": [("GET", "/api/admin/settings")],
    "users": [("GET", "/api/admin/users"), ("GET", "/admin/users")],
}


def member_with(name, granted, role="member"):
    service.create_user(name, PW, role, granted)
    cl_ = TestClient(main.app)
    assert login(cl_, name, PW).status_code == 200
    return cl_, {"X-CSRF-Token": cl_.get("/api/auth/me").json()["csrf"]}


def probe(cl_, hdrs, method, path):
    kw = {"headers": hdrs, "follow_redirects": False}
    if method == "POST":
        kw["json"] = {}
    return cl_.request(method, path, **kw).status_code


service.limits = LoginLimits()
holders = {}
for perm in permissions.ALL:
    holders[perm] = member_with(f"only_{perm}", [perm])
    me_ = holders[perm][0].get("/api/auth/me").json()
    check(f"{perm}: /me lists exactly that permission", me_["permissions"] == [perm], me_)
for perm, (cl_, hdrs) in holders.items():
    for other, routes in PROBES.items():
        for method, path in routes:
            code = probe(cl_, hdrs, method, path)
            if other == perm:
                check(f"only_{perm} may {method} {path}", code not in (401, 403), code)
            else:
                check(f"only_{perm} is refused {method} {path}", code == 403, code)

for perm, (cl_, _hdrs) in holders.items():
    page = cl_.get("/admin", follow_redirects=False).status_code
    wanted_ = 200 if perm in permissions.ADMIN_PAGE_TABS else 403
    check(f"the Admin page opens for {perm}: {wanted_ == 200}", page == wanted_, page)
check(
    "a member with no permissions reaches nothing admin",
    all(probe(member, mh, m, p) == 403 for routes in PROBES.values() for m, p in routes)
    and member.get("/admin", follow_redirects=False).status_code == 403,
)
check(
    "admins reach every area",
    all(probe(admin, H, m, p) not in (401, 403) for routes in PROBES.values() for m, p in routes)
    and admin.get("/admin").status_code == 200,
)
me_admin = admin.get("/api/auth/me").json()
check(
    "admin /me lists every permission and the admin page",
    me_admin["permissions"] == list(permissions.ALL) and me_admin["admin_page"] is True,
)
check(
    "member /me lists none",
    member.get("/api/auth/me").json()["permissions"] == []
    and member.get("/api/auth/me").json()["admin_page"] is False,
)

known = set(permissions.ALL) | {"admin_page", "admin"}
unlisted = [
    r.path
    for r in main.app.routes
    if getattr(r, "path", "").startswith(web.ADMIN_PREFIX)
    and web.required_permission(r.path) not in known
]
check("every admin route names a permission (or the admin role)", not unlisted, unlisted)
check(
    "a new admin route nobody listed is admin-role only",
    web.required_permission("/api/admin/something-new") == "admin"
    and not permissions.allows(
        {"role": "member", "permissions": frozenset(permissions.ALL)}, "admin"
    ),
)
check(
    "member routes need no permission",
    all(
        web.required_permission(p) is None
        for p in ("/", "/archive", "/radio", "/api/artists", "/api/auth/me", "/account")
    ),
)

hdr_of = lambda cl_: cl_.get("/").text.split("<main>")[0]
check(
    "nav: upload link only for people who may upload",
    'href="/upload"' in hdr_of(holders["upload"][0])
    and 'href="/upload"' not in hdr_of(member)
    and 'href="/admin"' not in hdr_of(holders["upload"][0]),
)
check(
    "nav: users link only for people who manage users",
    'href="/admin/users"' in hdr_of(holders["users"][0])
    and 'href="/admin/users"' not in hdr_of(holders["status"][0])
    and 'href="/admin"' in hdr_of(holders["status"][0]),
)

check(
    "pages: the users page has the permission boxes and no inline handlers",
    all(
        x in admin.get("/admin/users").text
        for x in ('id="invitePerms"', "Revoke invite", "Delete user")
    )
    and " onclick=" not in admin.get("/admin/users").text,
)
check(
    "pages: account page offers site settings only to people with that permission",
    'id="siteForm"' in holders["settings"][0].get("/account").text
    and "includes('settings')" in holders["settings"][0].get("/account").text,
)
check(
    "pages: the admin page hides tabs the person lacks (tab list is filled from /me)",
    "allowedTabs" in holders["status"][0].get("/admin").text,
)

# granting rules
service.limits = LoginLimits()
mgr, mgr_h = member_with("manager", ["users", "upload"])
target, target_h = member_with("target_user", [])
tid = {u["username"]: u["user_id"] for u in admin.get("/api/admin/users").json()["users"]}
users_by_name = lambda: {u["username"]: u for u in admin.get("/api/admin/users").json()["users"]}
check(
    "grant: a manager can give a permission they hold",
    mgr.post(
        f"/api/admin/users/{tid['target_user']}/permissions",
        json={"permissions": ["upload"]},
        headers=mgr_h,
    ).status_code
    == 200
    and users_by_name()["target_user"]["permissions"] == ["upload"],
)
check(
    "grant: ...and it applies to the person's very next request",
    probe(target, target_h, "GET", "/upload") not in (401, 403),
)
check(
    "grant: a manager cannot give a permission they lack",
    mgr.post(
        f"/api/admin/users/{tid['target_user']}/permissions",
        json={"permissions": ["upload", "collector"]},
        headers=mgr_h,
    ).status_code
    == 403
    and users_by_name()["target_user"]["permissions"] == ["upload"],
)
raw(
    "UPDATE totr_users SET permissions = 'upload,collector' WHERE user_id = ?",
    (tid["target_user"],),
)
check(
    "grant: a manager cannot remove a permission they do not hold",
    mgr.post(
        f"/api/admin/users/{tid['target_user']}/permissions",
        json={"permissions": ["upload"]},
        headers=mgr_h,
    ).status_code
    == 403,
)
check(
    "grant: ...but can remove one they hold, leaving the rest",
    mgr.post(
        f"/api/admin/users/{tid['target_user']}/permissions",
        json={"permissions": ["collector"]},
        headers=mgr_h,
    ).status_code
    == 200
    and users_by_name()["target_user"]["permissions"] == ["collector"],
)
check(
    "grant: nobody can change their own permissions",
    mgr.post(
        f"/api/admin/users/{tid['manager']}/permissions",
        json={"permissions": ["users", "upload", "status"]},
        headers=mgr_h,
    ).status_code
    == 409
    and admin.post(
        f"/api/admin/users/{tid['Nova']}/permissions", json={"permissions": []}, headers=H
    ).status_code
    == 409,
)
check(
    "grant: admins' permissions cannot be edited",
    admin.post(
        f"/api/admin/users/{tid['Nova']}/permissions", json={"permissions": ["upload"]}, headers=H
    ).status_code
    in (409,)
    and mgr.post(
        f"/api/admin/users/{tid['Nova']}/permissions",
        json={"permissions": ["upload"]},
        headers=mgr_h,
    ).status_code
    in (409, 403),
)
check(
    "grant: an admin can grant anything",
    admin.post(
        f"/api/admin/users/{tid['target_user']}/permissions",
        json={"permissions": list(permissions.ALL)},
        headers=H,
    ).status_code
    == 200
    and users_by_name()["target_user"]["permissions"] == list(permissions.ALL),
)
check(
    "grant: unknown names and bad payloads are refused",
    admin.post(
        f"/api/admin/users/{tid['target_user']}/permissions",
        json={"permissions": ["root"]},
        headers=H,
    ).status_code
    == 422
    and admin.post(
        f"/api/admin/users/{tid['target_user']}/permissions",
        json={"permissions": "upload"},
        headers=H,
    ).status_code
    == 422
    and admin.post(
        f"/api/admin/users/{tid['target_user']}/permissions", json={}, headers=H
    ).status_code
    == 422
    and admin.post(
        "/api/admin/users/99999/permissions", json={"permissions": []}, headers=H
    ).status_code
    == 404,
)
check(
    "grant: stored in a fixed order, and only valid names",
    raw("SELECT permissions FROM totr_users WHERE user_id = ?", (tid["target_user"],))[0][
        "permissions"
    ]
    == ",".join(permissions.ALL),
)
check(
    "grant: needs the CSRF token, and members cannot",
    admin.post(
        f"/api/admin/users/{tid['target_user']}/permissions", json={"permissions": []}
    ).status_code
    == 403
    and member.post(
        f"/api/admin/users/{tid['target_user']}/permissions", json={"permissions": []}, headers=mh
    ).status_code
    == 403,
)
check(
    "manager cannot touch an admin (disable, sign out, delete, invite)",
    mgr.post(f"/api/admin/users/{tid['Nova']}/disable", headers=mgr_h).status_code == 403
    and mgr.post(f"/api/admin/users/{tid['Nova']}/signout", headers=mgr_h).status_code == 403
    and mgr.post(
        f"/api/admin/users/{tid['Nova']}/delete", json={"confirm_username": "Nova"}, headers=mgr_h
    ).status_code
    == 403
    and mgr.post("/api/admin/invites", json={"role": "admin"}, headers=mgr_h).status_code == 403,
)
check(
    "manager can still disable a member",
    mgr.post(f"/api/admin/users/{tid['target_user']}/disable", headers=mgr_h).status_code == 200
    and mgr.post(f"/api/admin/users/{tid['target_user']}/enable", headers=mgr_h).status_code == 200,
)

# invites show up as users, carrying their permissions
r = admin.post(
    "/api/admin/invites", json={"role": "member", "permissions": ["collector", "upload"]}, headers=H
).json()
inv_id, link = r["invite_id"], r["path"]
check(
    "invite: the response names the invite without exposing the secret beyond the link",
    re.fullmatch(r"[0-9a-f]{16}", inv_id) and link.startswith("/invite/") and inv_id not in link,
)
pend = admin.get("/api/admin/users").json()["pending_invites"]
mine = next(i for i in pend if i["invite_id"] == inv_id)
check(
    "invite: appears in the user list at once with its permissions, role and creator",
    mine["role"] == "member"
    and mine["permissions"] == ["upload", "collector"]
    and mine["created_by"] == "Nova",
    mine,
)
check(
    "invite: the list never contains the link or token",
    "token" not in json.dumps(pend) and link.split("/")[-1] not in json.dumps(pend),
)
check(
    "invite: its permissions can be edited before the person signs up",
    admin.post(
        f"/api/admin/invites/{inv_id}/permissions", json={"permissions": ["status"]}, headers=H
    ).status_code
    == 200
    and next(
        i
        for i in admin.get("/api/admin/users").json()["pending_invites"]
        if i["invite_id"] == inv_id
    )["permissions"]
    == ["status"],
)
newbie = TestClient(main.app)
check(
    "invite: accepting it creates a user who starts with those permissions",
    newbie.post(
        "/api/auth/accept-invite",
        json={"token": link.split("/")[-1], "username": "newbie", "password": PW},
    ).status_code
    == 200
    and newbie.get("/api/auth/me").json()["permissions"] == ["status"],
)
check(
    "invite: once used it is no longer listed as pending",
    all(i["invite_id"] != inv_id for i in admin.get("/api/admin/users").json()["pending_invites"])
    and "newbie" in users_by_name(),
)
check(
    "invite: a used invite cannot be edited or revoked",
    admin.post(
        f"/api/admin/invites/{inv_id}/permissions", json={"permissions": []}, headers=H
    ).status_code
    == 404
    and admin.post(f"/api/admin/invites/{inv_id}/revoke", headers=H).status_code == 404,
)
r = admin.post(
    "/api/admin/invites", json={"role": "member", "permissions": ["upload"]}, headers=H
).json()
check(
    "invite: an admin can revoke one, and its link stops working",
    admin.post(f"/api/admin/invites/{r['invite_id']}/revoke", headers=H).status_code == 200
    and TestClient(main.app)
    .post(
        "/api/auth/accept-invite",
        json={"token": r["path"].split("/")[-1], "username": "ghosty", "password": PW},
    )
    .status_code
    == 400
    and all(
        i["invite_id"] != r["invite_id"]
        for i in admin.get("/api/admin/users").json()["pending_invites"]
    ),
)
check(
    "invite: bad ids are a clean 404",
    all(
        admin.post(f"/api/admin/invites/{bad}/revoke", headers=H).status_code == 404
        for bad in ("nope", "0" * 16, "G" * 16, "%" * 16)
    ),
)
check(
    "invite: unknown permission names are refused",
    admin.post(
        "/api/admin/invites", json={"role": "member", "permissions": ["root"]}, headers=H
    ).status_code
    == 422,
)
r = admin.post(
    "/api/admin/invites", json={"role": "admin", "permissions": ["upload"]}, headers=H
).json()
check(
    "invite: an admin invite carries no stored list (admins hold everything) and its permissions are not editable",
    raw(
        "SELECT permissions FROM totr_invites WHERE substr(token_hash, 1, 16) = ?",
        (r["invite_id"],),
    )[0]["permissions"]
    == ""
    and admin.post(
        f"/api/admin/invites/{r['invite_id']}/permissions",
        json={"permissions": ["upload"]},
        headers=H,
    ).status_code
    == 409
    and next(
        i
        for i in admin.get("/api/admin/users").json()["pending_invites"]
        if i["invite_id"] == r["invite_id"]
    )["permissions"]
    == list(permissions.ALL),
)
admin.post(f"/api/admin/invites/{r['invite_id']}/revoke", headers=H)
check(
    "invite: a manager can invite with permissions they hold, not ones they lack",
    mgr.post(
        "/api/admin/invites", json={"role": "member", "permissions": ["upload"]}, headers=mgr_h
    ).status_code
    == 200
    and mgr.post(
        "/api/admin/invites",
        json={"role": "member", "permissions": ["upload", "settings"]},
        headers=mgr_h,
    ).status_code
    == 403,
)
r = admin.post("/api/admin/invites", json={"role": "admin"}, headers=H).json()
check(
    "invite: a manager cannot revoke an admin's invite",
    mgr.post(f"/api/admin/invites/{r['invite_id']}/revoke", headers=mgr_h).status_code == 403,
)
admin.post(f"/api/admin/invites/{r['invite_id']}/revoke", headers=H)
check(
    "invite: members without the users permission cannot invite",
    member.post("/api/admin/invites", json={"role": "member"}, headers=mh).status_code == 403,
)
check(
    "invite: an old-style request (role only) still works",
    admin.post("/api/admin/invites", json={"role": "member"}, headers=H).status_code == 200,
)

# deleting users
service.limits = LoginLimits()
victim, victim_h = member_with("victim", ["upload"])
vid = {u["username"]: u["user_id"] for u in admin.get("/api/admin/users").json()["users"]}["victim"]
check(
    "delete: refused without the right name typed",
    admin.post(
        f"/api/admin/users/{vid}/delete", json={"confirm_username": "nope"}, headers=H
    ).status_code
    == 422
    and admin.post(
        f"/api/admin/users/{vid}/delete", json={"confirm_username": "VICTIM"}, headers=H
    ).status_code
    == 422
    and admin.post(f"/api/admin/users/{vid}/delete", json={}, headers=H).status_code == 422
    and "victim" in users_by_name(),
)
check(
    "delete: members and the CSRF check apply",
    member.post(
        f"/api/admin/users/{vid}/delete", json={"confirm_username": "victim"}, headers=mh
    ).status_code
    == 403
    and admin.post(
        f"/api/admin/users/{vid}/delete", json={"confirm_username": "victim"}
    ).status_code
    == 403
    and "victim" in users_by_name(),
)
check(
    "delete: you cannot delete yourself",
    admin.post(
        f"/api/admin/users/{tid['Nova']}/delete", json={"confirm_username": "Nova"}, headers=H
    ).status_code
    == 409,
)
check(
    "delete: unknown id is a 404",
    admin.post(
        "/api/admin/users/99999/delete", json={"confirm_username": "x"}, headers=H
    ).status_code
    == 404,
)
check(
    "delete: an admin can delete a member once the name is typed",
    admin.post(
        f"/api/admin/users/{vid}/delete", json={"confirm_username": "victim"}, headers=H
    ).status_code
    == 200
    and "victim" not in users_by_name(),
)
check(
    "delete: the person is signed out at once and cannot sign in again",
    victim.get("/api/auth/me").status_code == 401
    and login(TestClient(main.app), "victim", PW).status_code == 401
    and raw("SELECT count(*) AS n FROM totr_sessions WHERE user_id = ?", (vid,))[0]["n"] == 0,
)
check("delete: the name can be used again", service.create_user("victim", PW) > 0)
tmp_mem, _h = member_with("manager_target", [])
mid = {u["username"]: u["user_id"] for u in admin.get("/api/admin/users").json()["users"]}[
    "manager_target"
]
check(
    "delete: a manager (users permission) can delete a member",
    mgr.post(
        f"/api/admin/users/{mid}/delete", json={"confirm_username": "manager_target"}, headers=mgr_h
    ).status_code
    == 200,
)
boss2 = service.create_user("Boss2", PW, "admin")
check(
    "delete: an admin can delete another admin while one other active admin remains",
    admin.post(
        f"/api/admin/users/{boss2}/delete", json={"confirm_username": "Boss2"}, headers=H
    ).status_code
    == 200,
)
try:
    service.delete_user({"user_id": 999, "role": "admin", "username": "ghost"}, tid["Nova"], "Nova")
    check("delete: the last active admin cannot be deleted", False)
except service.AuthError as e:
    check(
        "delete: the last active admin cannot be deleted",
        e.status == 409 and "Nova" in users_by_name(),
    )

# before sql/user_permissions.sql has been run, the app keeps working with the two roles
service._perm_state.update(ok=False, checked=time.monotonic())
legacy_admin = TestClient(main.app)
login(legacy_admin, "Nova", NEWPW)
LH = {"X-CSRF-Token": legacy_admin.get("/api/auth/me").json()["csrf"]}
check(
    "no permissions column: admins still sign in and reach every area",
    all(
        probe(legacy_admin, LH, m, p) not in (401, 403)
        for routes in PROBES.values()
        for m, p in routes
    ),
)
check(
    "no permissions column: members still sign in and are limited to member pages",
    (not layout.live_ready() or member.get("/api/artists").status_code == 200)
    and probe(member, mh, "GET", "/api/collector") == 403,
)
ov = legacy_admin.get("/api/admin/users").json()
check(
    "no permissions column: the user list works and says permissions are not set up",
    ov["permissions_enabled"] is False
    and all(isinstance(u["permissions"], list) for u in ov["users"]),
)
check(
    "no permissions column: editing permissions says what to run",
    legacy_admin.post(
        f"/api/admin/users/{tid['target_user']}/permissions",
        json={"permissions": ["upload"]},
        headers=LH,
    ).status_code
    == 503
    and "user_permissions.sql"
    in legacy_admin.post(
        f"/api/admin/users/{tid['target_user']}/permissions",
        json={"permissions": ["upload"]},
        headers=LH,
    ).json()["detail"],
)
check(
    "no permissions column: invites without permissions still work, with permissions say what to run",
    legacy_admin.post("/api/admin/invites", json={"role": "member"}, headers=LH).status_code == 200
    and legacy_admin.post(
        "/api/admin/invites", json={"role": "member", "permissions": ["upload"]}, headers=LH
    ).status_code
    == 503,
)
r = legacy_admin.post("/api/admin/invites", json={"role": "member"}, headers=LH).json()
check(
    "no permissions column: accepting an invite works",
    TestClient(main.app)
    .post(
        "/api/auth/accept-invite",
        json={"token": r["path"].split("/")[-1], "username": "legacyfriend", "password": PW},
    )
    .status_code
    == 200,
)
service._perm_state.update(ok=None, checked=0.0)
check(
    "permissions column present again: checked again and used",
    service.has_permission_columns() is True,
)

finish()
