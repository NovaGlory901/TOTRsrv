"""The account rules: who may sign in, what a session is, how invites and passwords work."""

import logging
import re
import secrets
import time
from datetime import UTC, datetime, timedelta

import db

from . import (
    INVITE_DAYS,
    SESSION_ABSOLUTE_DAYS,
    SESSION_IDLE_DAYS,
    TOUCH_EVERY_SECONDS,
    passwords,
    store,
)
from . import (
    permissions as perms,
)
from .limiter import Limiter, LoginLimits

log = logging.getLogger("totr.auth")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
ROLES = ("member", "admin")

# One message for every kind of sign-in failure, so the page never reveals which usernames exist.
BAD_LOGIN = "Wrong username or password."

connect = db.get_auth_conn  # tests swap this for an in-memory database
limits = LoginLimits()
# links that did not work, per address: nobody can guess a 256-bit link, but each try costs a full
# password hash
invite_tries = Limiter(20, 15 * 60)

NEEDS_SQL = "Per-user permissions need one more database step: run sql/user_permissions.sql on your PostgreSQL database."
INVITE_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_perm_state = {"ok": None, "checked": 0.0}


def has_permission_columns() -> bool:
    """False until the owner has run sql/user_permissions.sql. Until then everything works with the
    two roles only. A "yes" is remembered; a "no" is re-checked every minute so the answer changes
    soon after the SQL is run."""
    st = _perm_state
    if st["ok"] or (st["ok"] is False and time.monotonic() - st["checked"] < 60):
        return st["ok"]
    try:
        with connect() as conn:
            conn.execute("SELECT permissions FROM totr.users LIMIT 1")
            conn.execute("SELECT permissions FROM totr.invites LIMIT 1")
        ok = True
    except Exception:
        ok = False
    st.update(ok=ok, checked=time.monotonic())
    return ok


def _require_columns():
    if not has_permission_columns():
        raise AuthError(NEEDS_SQL, 503)


def _perm_text(raw) -> str:
    try:
        return perms.to_text(raw)
    except ValueError as e:
        raise AuthError(str(e), 422)


def held_by(actor: dict) -> frozenset:
    """What this person may grant to others: everything for an admin, else their own list."""
    return (
        frozenset(perms.ALL)
        if actor.get("role") == "admin"
        else frozenset(actor.get("permissions", ()))
    )


def _check_can_change(actor: dict, before: frozenset, after: frozenset):
    """You may only add or remove permissions that you hold yourself."""
    if not (before ^ after) <= held_by(actor):
        raise AuthError("You can only grant or remove permissions that you have yourself.", 403)


def _guard_target(actor: dict, target):
    if target["role"] == "admin" and actor.get("role") != "admin":
        raise AuthError("Only an admin can do that to an admin.", 403)


class AuthError(Exception):
    """A refusal that is safe to show to the person who caused it."""

    def __init__(self, message: str, status: int = 400, retry_after: int = 0):
        super().__init__(message)
        self.message, self.status, self.retry_after = message, status, retry_after


def _now() -> datetime:
    return datetime.now(UTC)


def _new_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits from the OS random source


def _open_session(conn, user_id: int, ip: str, user_agent: str) -> dict:
    now, token, csrf = _now(), _new_token(), _new_token()
    store.create_session(
        conn,
        store.token_hash(token),
        user_id,
        csrf,
        now,
        now + timedelta(days=SESSION_ABSOLUTE_DAYS),
        ip,
        user_agent,
    )
    return {"token": token, "csrf": csrf, "max_age": SESSION_ABSOLUTE_DAYS * 86400}


def configured() -> bool:
    return db.auth_enabled()


def has_users() -> bool:
    with connect() as conn:
        return store.count_users(conn) > 0


def login(username: str, password: str, ip: str, user_agent: str) -> dict:
    username = (username or "").strip()
    password = password or ""

    wait = limits.retry_after(ip, username)
    if wait:
        raise AuthError(
            f"Too many failed attempts. Try again in {max(1, wait // 60)} minute(s).", 429, wait
        )

    with connect() as conn:
        user = store.get_user_by_name(conn, username) if USERNAME_RE.match(username) else None

    usable = user is not None and not user["disabled"]
    # Always do one password check, against a dummy hash when there is no usable account,
    # so the response time does not reveal whether the username exists.
    ok = (
        passwords.verify_password(
            user["password_hash"] if usable else passwords.DUMMY_HASH, password[:1024]
        )
        and usable
        and len(password) <= 1024
    )
    if not ok:
        limits.failure(ip, username)
        raise AuthError(BAD_LOGIN, 401)

    limits.success(ip, username)
    now = _now()
    with connect() as conn:
        if passwords.needs_rehash(user["password_hash"]):
            store.set_password_hash(conn, user["user_id"], passwords.hash_password(password))
        store.touch_login(conn, user["user_id"], now)
        store.purge_sessions(conn, now, now - timedelta(days=SESSION_IDLE_DAYS))
        session = _open_session(conn, user["user_id"], ip, user_agent)
    return {**session, "username": user["username"], "role": user["role"]}


def validate_session(token: str):
    """The user behind a cookie value, or None. Never raises for a bad cookie."""
    if not token or len(token) > 100:
        return None
    now = _now()
    th = store.token_hash(token)
    perm = has_permission_columns()
    with connect() as conn:
        row = store.get_session(
            conn,
            th,
            now,
            now - timedelta(days=SESSION_IDLE_DAYS),
            now - timedelta(seconds=TOUCH_EVERY_SECONDS),
            perm,
        )
        if row is None:
            return None
        if row["stale"]:
            store.touch_session(conn, th, now)
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "role": row["role"],
        "permissions": perms.effective(row["role"], row["permissions"]),
        "csrf": row["csrf_token"],
        "token_hash": th,
    }


def logout(token: str):
    if token:
        with connect() as conn:
            store.delete_session(conn, store.token_hash(token))


def account_overview(user: dict) -> dict:
    """What the Settings page shows about the signed-in person and where they are signed in."""
    with connect() as conn:
        info = store.account_info(conn, user["user_id"])
        rows = store.list_sessions(conn, user["user_id"], _now())
    if info is None:
        raise AuthError("Account not found.", 404)
    return {
        "username": info["username"],
        "role": info["role"],
        "created_at": info["created_at"],
        "last_login_at": info["last_login_at"],
        "sessions": [
            {
                "id": r["token_hash"][:16],
                "current": r["token_hash"] == user["token_hash"],
                "created_at": r["created_at"],
                "last_seen": r["last_seen"],
                "ip": r["ip"],
                "user_agent": r["user_agent"],
            }
            for r in rows
        ],
    }


def revoke_own_session(user: dict, session_id: str):
    """Sign out one other device. The current one is not offered here (that is "sign out")."""
    if not re.fullmatch(r"[0-9a-f]{16}", session_id or ""):
        raise AuthError("Unknown session.", 404)
    with connect() as conn:
        if not store.delete_own_session(conn, user["user_id"], session_id, user["token_hash"]):
            raise AuthError("That device is not signed in any more.", 404)


def change_password(user: dict, current: str, new: str):
    wait = limits.by_pair.retry_after(f"pw|{user['user_id']}")
    if wait:
        raise AuthError("Too many attempts. Try again later.", 429, wait)
    with connect() as conn:
        row = store.get_user(conn, user["user_id"])
    if row is None or not passwords.verify_password(row["password_hash"], (current or "")[:1024]):
        limits.by_pair.record(f"pw|{user['user_id']}")
        raise AuthError("Your current password is wrong.", 403)
    problem = passwords.check_new_password(new or "", row["username"])
    if problem:
        raise AuthError(problem, 422)
    if new == current:
        raise AuthError("Choose a different password from the current one.", 422)
    new_hash = passwords.hash_password(new)
    with connect() as conn:
        store.set_password_hash(conn, row["user_id"], new_hash)
        # Anyone else signed in as this user (a stolen session, an old phone) is signed out.
        store.delete_user_sessions(conn, row["user_id"], keep_token_h=user["token_hash"])
    limits.by_pair.clear(f"pw|{user['user_id']}")


def reset_password(username: str, new: str):
    """Command-line recovery. Signs the user out everywhere."""
    with connect() as conn:
        row = store.get_user_by_name(conn, username)
        if row is None:
            raise AuthError("No such user.", 404)
        problem = passwords.check_new_password(new, row["username"])
        if problem:
            raise AuthError(problem, 422)
        store.set_password_hash(conn, row["user_id"], passwords.hash_password(new))
        store.delete_user_sessions(conn, row["user_id"])


def create_user(username: str, password: str, role: str = "member", granted=()) -> int:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise AuthError(
            "Usernames are 3 to 32 characters: letters, numbers, dot, dash, underscore.", 422
        )
    if role not in ROLES:
        raise AuthError("Unknown role.", 422)
    problem = passwords.check_new_password(password or "", username)
    if problem:
        raise AuthError(problem, 422)
    text = "" if role == "admin" else _perm_text(granted)
    if text:
        _require_columns()
    password_hash = passwords.hash_password(password)
    with connect() as conn:
        try:
            return store.create_user(
                conn, username, password_hash, role, _now(), text, has_permission_columns()
            )
        except store.INTEGRITY_ERRORS:
            raise AuthError("That username is taken.", 409)


def create_invite(actor: dict, role: str, granted=()) -> str:
    """Returns the one-time token. Only its hash is stored, so this is the only time it can be shown.
    The invite already carries the permissions the new person will start with."""
    if role not in ROLES:
        raise AuthError("Unknown role.", 422)
    if role == "admin" and actor.get("role") != "admin":
        raise AuthError("Only an admin can invite another admin.", 403)
    text = "" if role == "admin" else _perm_text(granted)  # an admin holds everything anyway
    if text:
        _require_columns()
        _check_can_change(actor, frozenset(), perms.from_text(text))
    now, token = _now(), _new_token()
    with connect() as conn:
        store.create_invite(
            conn,
            store.token_hash(token),
            role,
            actor["user_id"],
            now,
            now + timedelta(days=INVITE_DAYS),
            text,
            has_permission_columns(),
        )
    return token


def accept_invite(token: str, username: str, password: str, ip: str, user_agent: str) -> dict:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise AuthError(
            "Usernames are 3 to 32 characters: letters, numbers, dot, dash, underscore.", 422
        )
    wait = invite_tries.retry_after(ip)
    if wait:
        raise AuthError(f"Too many tries. Try again in {max(1, wait // 60)} minute(s).", 429, wait)
    # The link is looked at before anything expensive happens: the password hash (about 0.1 s of
    # processor time) must not be made for every request, or anyone could keep the server busy
    # by posting made-up links.
    th, now = store.token_hash(token or ""), _now()
    if not token or len(token) > 100:
        invite_tries.record(ip)
        raise AuthError("This invite link is invalid or has expired.", 400)
    with connect() as conn:
        usable = store.invite_usable(conn, th, now)
    if not usable:
        invite_tries.record(ip)
        raise AuthError("This invite link is invalid or has expired.", 400)
    problem = passwords.check_new_password(password or "", username)
    if problem:
        raise AuthError(problem, 422)
    password_hash = passwords.hash_password(password)
    perm = has_permission_columns()
    with connect() as conn:
        consumed = store.consume_invite(conn, th, now, perm)
        if consumed is None:
            raise AuthError("This invite link is invalid or has expired.", 400)
        role, text = consumed
        try:
            user_id = store.create_user(
                conn, username, password_hash, role, now, "" if role == "admin" else text, perm
            )
        except store.INTEGRITY_ERRORS:
            conn.rollback()  # undoes consume_invite too, so the link still works
            raise AuthError("That username is taken. Pick another.", 409)
        store.set_invite_user(conn, th, user_id)
        store.touch_login(conn, user_id, now)
        session = _open_session(conn, user_id, ip, user_agent)
    return {**session, "username": username, "role": role}


def _ordered(granted) -> list:
    return [p for p in perms.ALL if p in granted]


def admin_overview() -> dict:
    now, perm = _now(), has_permission_columns()
    with connect() as conn:
        users = store.list_users(conn, now, perm)
        invites = store.pending_invites(conn, now, perm)

    def iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    return {
        "permissions_enabled": perm,
        "permission_names": list(perms.ALL),
        "permission_labels": perms.LABELS,
        "users": [
            {
                **u,
                "permissions": _ordered(perms.effective(u["role"], u["permissions"])),
                "created_at": iso(u["created_at"]),
                "last_login_at": iso(u["last_login_at"]),
            }
            for u in users
        ],
        # An invite is listed as a user who has not signed up yet, with the permissions it already
        # carries.
        "pending_invites": [
            {
                **i,
                "permissions": _ordered(perms.effective(i["role"], i["permissions"])),
                "created_at": iso(i["created_at"]),
                "expires_at": iso(i["expires_at"]),
            }
            for i in invites
        ],
    }


def set_disabled(actor: dict, user_id: int, disabled: bool):
    if disabled and user_id == actor["user_id"]:
        raise AuthError("You cannot disable your own account.", 409)
    with connect() as conn:
        target = store.get_user(conn, user_id)
        if target is None:
            raise AuthError("No such user.", 404)
        _guard_target(actor, target)
        if (
            disabled
            and target["role"] == "admin"
            and not target["disabled"]
            and store.count_other_active_admins(conn, user_id) == 0
        ):
            raise AuthError("That is the last active admin.", 409)
        store.set_disabled(conn, user_id, disabled)
        if disabled:
            store.delete_user_sessions(conn, user_id)


def sign_out_everywhere(user_id: int):
    with connect() as conn:
        store.delete_user_sessions(conn, user_id)


def admin_sign_out(actor: dict, user_id: int):
    with connect() as conn:
        target = store.get_user(conn, user_id)
        if target is None:
            raise AuthError("No such user.", 404)
        _guard_target(actor, target)
        store.delete_user_sessions(conn, user_id)


def set_permissions(actor: dict, user_id: int, granted):
    """Replaces a member's permissions. Takes effect on their next request (they are looked up per
    request)."""
    _require_columns()
    after = perms.from_text(_perm_text(granted))
    with connect() as conn:
        target = store.get_user_full(conn, user_id)
        if target is None:
            raise AuthError("No such user.", 404)
        if user_id == actor["user_id"]:
            raise AuthError("You cannot change your own permissions.", 409)
        if target["role"] == "admin":
            raise AuthError("Admins always have every permission.", 409)
        _check_can_change(actor, perms.from_text(target["permissions"]), after)
        store.set_permissions(conn, user_id, perms.to_text(after))


def _pending_invite(conn, invite_id: str):
    invite = (
        store.get_pending_invite(conn, invite_id, _now(), has_permission_columns())
        if INVITE_ID_RE.match(invite_id or "")
        else None
    )
    if invite is None:
        raise AuthError("That invite is gone or has expired.", 404)
    return invite


def set_invite_permissions(actor: dict, invite_id: str, granted):
    _require_columns()
    after = perms.from_text(_perm_text(granted))
    with connect() as conn:
        invite = _pending_invite(conn, invite_id)
        if invite["role"] == "admin":
            raise AuthError("Admins always have every permission.", 409)
        _check_can_change(actor, perms.from_text(invite["permissions"]), after)
        store.set_invite_permissions(conn, invite_id, perms.to_text(after))


def revoke_invite(actor: dict, invite_id: str):
    with connect() as conn:
        invite = _pending_invite(conn, invite_id)
        _guard_target(actor, invite)
        store.revoke_invite(conn, invite_id)


def delete_user(actor: dict, user_id: int, confirm_username: str):
    """Removes the account for good. The person must be named in `confirm_username`, so a stray
    click cannot do it. Their invite links' "created by" and the spider and collector logs keep the
    username as text."""
    if user_id == actor["user_id"]:
        raise AuthError("You cannot delete your own account.", 409)
    with connect() as conn:
        target = store.get_user(conn, user_id)
        if target is None:
            raise AuthError("No such user.", 404)
        _guard_target(actor, target)
        if (confirm_username or "") != target["username"]:
            raise AuthError("Type the person's username exactly to confirm.", 422)
        if target["role"] == "admin" and store.count_other_active_admins(conn, user_id) == 0:
            raise AuthError("That is the last active admin.", 409)
        store.delete_user(conn, user_id)
    log.info(
        "user %s (id %s) was deleted by %s", target["username"], user_id, actor.get("username", "?")
    )
