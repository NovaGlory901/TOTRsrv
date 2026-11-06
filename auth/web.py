"""
Everything between the browser and the account rules: the gate every request passes through,
the sign-in/invite/account/admin endpoints, and page rendering with a per-request CSP nonce.

The gate is secure by default. A path is reachable without signing in only if it is listed in
PUBLIC_EXACT / PUBLIC_PREFIX below; everything else needs a session, and admin-only areas need
the admin role. If accounts are not set up at all, nothing but the login page is reachable.
"""

import hmac
import html
import logging
import os
import pathlib
import secrets
import urllib.parse

import fastapi
import psycopg
import pydantic
from fastapi.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import app_settings
import discovery
import moving
import tlsid

from . import COOKIE_NAME, MAX_BODY_BYTES, MAX_UPLOAD_MB, env_flag, join, permissions, service

log = logging.getLogger("totr.auth")
BASE_DIR = pathlib.Path(__file__).resolve().parent.parent

PUBLIC_EXACT = {
    "/login",
    "/health",
    "/.well-known/totr-ca.pem",
    "/app",
    "/download",
    "/api/downloads",
    "/favicon.ico",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/auth/accept-invite",
    "/api/join",
}
# /app/ is the listener app's static files (the login screen is part of it)
PUBLIC_PREFIX = (
    "/static/",
    "/invite/",
    "/app/",
    "/api/join/",
)
ADMIN_PREFIX = (
    "/upload",
    "/status",
    "/admin",
    "/discover",
    "/api/admin",
    "/api/maintenance",
    "/api/upload",
    "/api/health",
    "/api/collector",
)
# Which permission each protected area needs (first match wins; see auth/permissions.py). A path
# under ADMIN_PREFIX that is not listed here is reserved for the admin role itself, so a new admin
# route is closed by default.
PERMISSION_PREFIX = (
    ("/admin/users", "users"),
    ("/api/admin/users", "users"),
    ("/api/admin/invites", "users"),
    ("/api/admin/join", "users"),
    # whoever may add music may take offered songs and ask for them
    ("/api/admin/offers", "upload"),
    ("/api/admin/requests", "upload"),
    ("/api/admin/settings", "settings"),
    ("/admin/database", "admin"),
    ("/api/admin/database", "admin"),  # the database is the admin role's alone
    ("/admin/updates", "admin"),
    ("/api/admin/updates", "admin"),  # so are updates (they replace the running code)
    ("/upload", "upload"),
    ("/api/upload", "upload"),
    ("/status", "status"),
    ("/api/health", "status"),
    ("/api/maintenance", "maintenance"),
    ("/api/collector", "collector"),
    ("/discover", "collector"),
    ("/admin", "admin_page"),
)
# one offered song (offers/__init__.py MAX_SONG_BYTES): the file must match the size that was
# offered, which is capped there
OFFERED_SONG_BYTES = 300 * 1024 * 1024
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

router = fastapi.APIRouter()


def required_permission(path: str):
    """The permission a path needs, "admin" for admin-only paths nobody was given a permission for,
    or None."""
    for prefix, need in PERMISSION_PREFIX:
        if path.startswith(prefix):
            return need
    return "admin" if path.startswith(ADMIN_PREFIX) else None


def client_ip(request) -> str:
    trusted = os.environ.get("TRUSTED_PROXY")
    if trusted and request.client and request.client.host == trusted:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def is_secure(request) -> bool:
    mode = env_flag("COOKIE_SECURE")
    if mode in ("true", "1", "yes"):
        return True
    if mode in ("false", "0", "no"):
        return False
    trusted = os.environ.get("TRUSTED_PROXY")
    behind_proxy = bool(trusted and request.client and request.client.host == trusted)
    return request.url.scheme == "https" or (
        behind_proxy and request.headers.get("x-forwarded-proto") == "https"
    )


def json_error(message: str, status: int, retry_after: int = 0) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse({"detail": message}, status_code=status, headers=headers)


def auth_error(e: service.AuthError) -> JSONResponse:
    return json_error(e.message, e.status, e.retry_after)


def set_session_cookie(response, request, session: dict):
    response.set_cookie(
        COOKIE_NAME,
        session["token"],
        max_age=session["max_age"],
        httponly=True,
        samesite="lax",
        secure=is_secure(request),
        path="/",
    )


def clear_session_cookie(response, request):
    response.delete_cookie(
        COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=is_secure(request)
    )


def safe_next(target: str) -> str:
    """Only same-site paths. Stops 'log in, then get sent to evil.example'."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


# The listener app (TOTRspa, built into static/app/) is plain files with no inline script, so it
# needs no nonce; blob: lets it play audio it fetched.
APP_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def csp(nonce: str = "") -> str:
    if not nonce:  # JSON and other non-page responses
        return "default-src 'none'; frame-ancestors 'none'"
    return (
        "default-src 'none'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data:; media-src 'self'; connect-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )


def security_headers(response, request):
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    path = request.url.path
    h.setdefault("Content-Security-Policy", APP_CSP if path.startswith("/app/") else csp())
    if path.startswith("/api/") or h.get("content-type", "").startswith("text/html"):
        h.setdefault("Cache-Control", "no-store")  # account data must not linger in caches
    if path.startswith("/static/"):
        # Re-check with the server every time (cheap: the file's ETag says "unchanged"), so a
        # browser can never keep running an old app.js against new pages. Without this, browsers
        # cache scripts for days after an update.
        h.setdefault("Cache-Control", "no-cache")
    if path.startswith("/app/"):
        # Hashed files under assets/ never change, so they may be cached; the page and version.json
        # are re-checked every time, which is what lets a running app notice a newer build.
        h.setdefault(
            "Cache-Control",
            "public, max-age=31536000, immutable"
            if path.startswith("/app/assets/")
            else "no-cache",
        )
    if is_secure(request):
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


PAGE_NAV_MEMBER = [
    ("/", "home"),
    ("/archive", "archive"),
    ("/radio", "radio"),
    ("/download", "get the app"),
]
PAGE_NAV_ADMIN = [
    ("/upload", "upload", "upload"),
    ("/admin", "admin", "admin_page"),
    ("/admin/users", "users", "users"),
    ("/admin/database", "database", "admin"),
    ("/admin/updates", "updates", "admin"),
]


def render_page(request, name: str, current: str = ""):
    """Fills the shared header/footer into a template, adds the CSRF token and the script nonce."""
    user = getattr(request.state, "user", None)
    nonce = secrets.token_urlsafe(16)
    links = []
    if user:
        nav = (
            PAGE_NAV_MEMBER
            + [
                (href, label)
                for href, label, need in PAGE_NAV_ADMIN
                if permissions.allows(user, need)
            ]
            + [("/settings", "settings")]
        )
        links = [
            f'<a href="{href}"{' aria-current="page"' if label == current else ""}>{label}</a>'
            for href, label in nav
        ]
    who = (
        (
            f'<span class="who">{html.escape(user["username"])}</span>'
            '<button id="logout" type="button">sign out</button>'
        )
        if user
        else ""
    )
    header = (
        '<header class="site"><div class="site-title"><a href="/"><span class="logo-mark" aria-hidden="true"></span>TOTR<span class="cursor">_</span></a>'
        '<span class="tag">tv on the radio</span></div>'
        f'<nav aria-label="Main">{"".join(links)}<span class="grow"></span>{who}</nav></header>'
    )
    site = os.environ.get("SITE_NAME", "").strip()[:60]
    footer = (
        '<footer class="site">user@dreamspace:~$ TV on the Radio &middot; '
        f"self-hosted{' by ' + html.escape(site) if site else ''} &#10022;</footer>"
    )
    page = (BASE_DIR / "templates" / name).read_text(encoding="utf-8")
    meta = (
        f'<meta name="csrf-token" content="{html.escape(user["csrf"])}"><meta name="totr-user" content="{html.escape(user["username"])}">'
        if user
        else ""
    )
    page = (
        page.replace("{{HEADER}}", header)
        .replace("{{FOOTER}}", footer)
        .replace(
            "</head>",
            f'{meta}<script src="/static/theme.js"></script><script src="/static/accounts.js"></script><script src="/static/app.js"></script></head>',
            1,
        )
        .replace("<script>", f'<script nonce="{nonce}">')
    )
    return HTMLResponse(page, headers={"Content-Security-Policy": csp(nonce)})


def install(app):
    @app.middleware("http")
    async def gate(request, call_next):
        path, method = request.url.path, request.method

        if "transfer-encoding" in request.headers:
            # A body sent in chunks has no length to check. With a small Content-Length beside it
            # the server still read the chunks (150 MB in the lab), so the size limits below could
            # be walked around by anyone, signed in or not. Browsers and the apps always send a
            # Content-Length with a body.
            return security_headers(
                json_error("Send a Content-Length; chunked requests are not accepted.", 411),
                request,
            )

        if method in UNSAFE:
            origin = request.headers.get("origin")
            if origin and urllib.parse.urlparse(origin).netloc != request.headers.get("host", ""):
                return security_headers(json_error("Cross-site request refused.", 403), request)
            length = request.headers.get("content-length")
            limit = (
                MAX_UPLOAD_MB * 1024 * 1024
                if path.startswith("/api/upload")
                else OFFERED_SONG_BYTES
                if (method == "PUT" and path.startswith("/api/songs/offers/"))
                else MAX_BODY_BYTES
            )
            if length is None:
                return security_headers(
                    json_error("A Content-Length header is required.", 411), request
                )
            if not length.isdigit() or int(length) > limit:
                return security_headers(json_error("That request is too large.", 413), request)

        if moving.active() and method in UNSAFE and not path.startswith("/api/admin/database/move"):
            return security_headers(
                json_error("TOTR is moving to a new database. Try again in a few minutes.", 503),
                request,
            )

        public = path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIX)
        user, token = None, request.cookies.get(COOKIE_NAME)
        if token and service.configured():
            try:
                user = await run_in_threadpool(service.validate_session, token)
            except Exception:
                log.exception("session lookup failed")
                if not public:
                    return security_headers(
                        json_error("Accounts are temporarily unavailable.", 503), request
                    )
        request.state.user = user

        if not public:
            if not service.configured():
                return security_headers(
                    json_error("Accounts are not set up yet. See sql/auth_schema.sql.", 503),
                    request,
                )
            if user is None:
                if path.startswith("/api/") or method != "GET":
                    resp = json_error("Not signed in.", 401)
                else:
                    resp = RedirectResponse(
                        "/login?next=" + urllib.parse.quote(path), status_code=303
                    )
                if token:
                    clear_session_cookie(resp, request)
                return security_headers(resp, request)
            need = required_permission(path)
            if need and not permissions.allows(user, need):
                if path.startswith("/api/") or method != "GET":
                    return security_headers(
                        json_error("You do not have permission for that.", 403), request
                    )
                return security_headers(
                    Response("You do not have permission for that.", 403, media_type="text/plain"),
                    request,
                )
            if method in UNSAFE and not hmac.compare_digest(
                request.headers.get("x-csrf-token", "").encode(), user["csrf"].encode()
            ):
                return security_headers(
                    json_error("CSRF check failed. Reload the page.", 403), request
                )

        try:
            response = await call_next(request)
        except (OverflowError, psycopg.errors.DataError):
            # a number too big for the database column (an id of 20 digits): the same answer as any
            # other number that is not valid
            return security_headers(json_error("That number is out of range.", 422), request)
        except Exception:
            # Without this the bare "Internal Server Error" of the framework goes out with none of
            # the security headers.
            log.exception("unhandled error on %s %s", method, path)
            return security_headers(json_error("Something went wrong on the server.", 500), request)
        return security_headers(response, request)

    app.include_router(router)


def current_user(request):
    return getattr(request.state, "user", None)


@router.get("/login")
def login_page(request: fastapi.Request):
    if current_user(request):
        return RedirectResponse(safe_next(request.query_params.get("next", "/")), status_code=303)
    return render_page(request, "login.html")


@router.get("/invite/{token}")
def invite_page(request: fastapi.Request, token: str):
    return render_page(request, "invite.html")


@router.get("/settings")
@router.get("/account")  # the old address keeps working (links, bookmarks)
def settings_page(request: fastapi.Request):
    return render_page(request, "settings.html", "settings")


@router.get("/admin/users")
def users_page(request: fastapi.Request):
    return render_page(request, "users.html", "users")


@router.get("/admin/database")
def database_page(request: fastapi.Request):
    return render_page(request, "database.html", "database")


class LoginBody(pydantic.BaseModel):
    username: str = pydantic.Field(max_length=64)
    password: str = pydantic.Field(max_length=1024)


class InviteBody(pydantic.BaseModel):
    token: str = pydantic.Field(max_length=100)
    username: str = pydantic.Field(max_length=64)
    password: str = pydantic.Field(max_length=1024)


class PasswordBody(pydantic.BaseModel):
    current: str = pydantic.Field(max_length=1024)
    new: str = pydantic.Field(max_length=1024)


class RoleBody(pydantic.BaseModel):
    role: str = pydantic.Field(max_length=16)
    # names are checked in service.py
    permissions: list[str] = pydantic.Field(default_factory=list, max_length=20)


class PermissionsBody(pydantic.BaseModel):
    permissions: list[str] = pydantic.Field(max_length=20)


class DeleteBody(pydantic.BaseModel):
    confirm_username: str = pydantic.Field(max_length=64)


async def _guarded(fn, *args):
    """Runs blocking account code off the event loop. Account problems become clean JSON errors;
    anything unexpected is logged, and the browser only ever sees a generic message."""
    try:
        return await run_in_threadpool(fn, *args), None
    except service.AuthError as e:
        return None, auth_error(e)
    except (OverflowError, psycopg.errors.DataError):
        return None, json_error("That number is out of range.", 422)
    except Exception:
        log.exception("account operation failed")
        return None, json_error("That did not work. Try again in a moment.", 503)


@router.get("/api/auth/setup")
async def auth_setup():
    if not service.configured():
        return {"configured": False, "has_users": False}
    has_users, err = await _guarded(service.has_users)
    return {"configured": True, "has_users": bool(has_users) if not err else True}


@router.post("/api/auth/login")
async def api_login(body: LoginBody, request: fastapi.Request):
    if not service.configured():
        return json_error("Accounts are not set up yet. See sql/auth_schema.sql.", 503)
    session, err = await _guarded(
        service.login,
        body.username,
        body.password,
        client_ip(request),
        request.headers.get("user-agent", ""),
    )
    if err:
        return err
    resp = JSONResponse({"ok": True, "username": session["username"], "role": session["role"]})
    set_session_cookie(resp, request, session)
    return resp


@router.post("/api/auth/accept-invite")
async def api_accept_invite(body: InviteBody, request: fastapi.Request):
    if not service.configured():
        return json_error("Accounts are not set up yet.", 503)
    session, err = await _guarded(
        service.accept_invite,
        body.token,
        body.username,
        body.password,
        client_ip(request),
        request.headers.get("user-agent", ""),
    )
    if err:
        return err
    resp = JSONResponse({"ok": True, "username": session["username"], "role": session["role"]})
    set_session_cookie(resp, request, session)
    return resp


@router.get("/api/auth/me")
def api_me(request: fastapi.Request):
    u = current_user(request)
    return {
        "username": u["username"],
        "role": u["role"],
        "csrf": u["csrf"],
        "permissions": [p for p in permissions.ALL if p in u["permissions"]],
        "admin_page": permissions.can_open_admin_page(u),
    }


@router.get("/api/account")
async def api_account(request: fastapi.Request):
    data, err = await _guarded(service.account_overview, current_user(request))
    return err or data


@router.post("/api/account/sessions/{session_id}/revoke")
async def api_revoke_session(session_id: str, request: fastapi.Request):
    _, err = await _guarded(service.revoke_own_session, current_user(request), session_id)
    return err or {"ok": True}


@router.post("/api/auth/logout")
async def api_logout(request: fastapi.Request):
    await _guarded(service.logout, request.cookies.get(COOKIE_NAME))
    resp = JSONResponse({"ok": True})
    clear_session_cookie(resp, request)
    return resp


@router.post("/api/auth/signout-all")
async def api_signout_all(request: fastapi.Request):
    _, err = await _guarded(service.sign_out_everywhere, current_user(request)["user_id"])
    if err:
        return err
    resp = JSONResponse({"ok": True})
    clear_session_cookie(resp, request)
    return resp


@router.post("/api/auth/change-password")
async def api_change_password(body: PasswordBody, request: fastapi.Request):
    _, err = await _guarded(service.change_password, current_user(request), body.current, body.new)
    return err or {"ok": True}


@router.get("/api/admin/users")
async def api_admin_users():
    data, err = await _guarded(service.admin_overview)
    return err or data


@router.post("/api/admin/invites")
async def api_admin_invite(body: RoleBody, request: fastapi.Request):
    token, err = await _guarded(
        service.create_invite, current_user(request), body.role, body.permissions
    )
    return err or {
        "token": token,
        "path": f"/invite/{token}",
        "code": tlsid.invite_code(),
        "days": 7,
        "invite_id": service.store.token_hash(token)[:16],
    }


@router.post("/api/admin/invites/{invite_id}/permissions")
async def api_admin_invite_permissions(
    invite_id: str, body: PermissionsBody, request: fastapi.Request
):
    _, err = await _guarded(
        service.set_invite_permissions, current_user(request), invite_id, body.permissions
    )
    return err or {"ok": True}


@router.post("/api/admin/invites/{invite_id}/revoke")
async def api_admin_invite_revoke(invite_id: str, request: fastapi.Request):
    _, err = await _guarded(service.revoke_invite, current_user(request), invite_id)
    return err or {"ok": True}


@router.post("/api/admin/users/{user_id}/disable")
async def api_admin_disable(user_id: int, request: fastapi.Request):
    _, err = await _guarded(service.set_disabled, current_user(request), user_id, True)
    return err or {"ok": True}


@router.post("/api/admin/users/{user_id}/enable")
async def api_admin_enable(user_id: int, request: fastapi.Request):
    _, err = await _guarded(service.set_disabled, current_user(request), user_id, False)
    return err or {"ok": True}


@router.post("/api/admin/users/{user_id}/signout")
async def api_admin_signout(user_id: int, request: fastapi.Request):
    _, err = await _guarded(service.admin_sign_out, current_user(request), user_id)
    return err or {"ok": True}


@router.post("/api/admin/users/{user_id}/permissions")
async def api_admin_permissions(user_id: int, body: PermissionsBody, request: fastapi.Request):
    _, err = await _guarded(
        service.set_permissions, current_user(request), user_id, body.permissions
    )
    return err or {"ok": True}


@router.post("/api/admin/users/{user_id}/delete")
async def api_admin_delete(user_id: int, body: DeleteBody, request: fastapi.Request):
    _, err = await _guarded(
        service.delete_user, current_user(request), user_id, body.confirm_username
    )
    return err or {"ok": True}


class JoinBody(pydantic.BaseModel):
    payload: str = pydantic.Field(max_length=join.MAX_PAYLOAD)
    # 44 for an Ed25519 key, 88 for an uncompressed P-256 point
    key: str = pydantic.Field(max_length=88)
    sig: str = pydantic.Field(max_length=128)


class JoinOpenBody(pydantic.BaseModel):
    open: bool


@router.get("/api/join")
async def api_join_info():
    """Public: does this server take join requests, and what does it call itself. Nothing else."""
    return {"open": join.is_open(), "name": os.environ.get("SITE_NAME", "").strip()[:60]}


@router.post("/api/join")
async def api_join(body: JoinBody, request: fastapi.Request):
    data, err = await _guarded(join.submit, client_ip(request), body.model_dump())
    return err or data


@router.get("/api/join/{request_id}")
async def api_join_status(request_id: str, request: fastapi.Request):
    data, err = await _guarded(join.status, client_ip(request), request_id)
    return err or data


@router.post("/api/join/{request_id}/withdraw")
async def api_join_withdraw(request_id: str):
    _, err = await _guarded(join.withdraw, request_id)
    return err or {"ok": True}


@router.get("/api/admin/join")
async def api_admin_join():
    data, err = await _guarded(join.pending)
    return err or {"open": join.is_open(), "requests": data}


@router.post("/api/admin/join/open")
async def api_admin_join_open(body: JoinOpenBody):
    app_settings.set_join_open(body.open)
    # start or stop telling apps on the network we are here
    announced = await run_in_threadpool(discovery.sync)
    return {"open": join.is_open(), "announcing": announced}


@router.post("/api/admin/join/{request_id}/approve")
async def api_admin_join_approve(request_id: str, body: PermissionsBody, request: fastapi.Request):
    _, err = await _guarded(join.approve, current_user(request), request_id, body.permissions)
    return err or {"ok": True}


@router.post("/api/admin/join/{request_id}/decline")
async def api_admin_join_decline(request_id: str, request: fastapi.Request):
    _, err = await _guarded(join.decline, current_user(request), request_id)
    return err or {"ok": True}
