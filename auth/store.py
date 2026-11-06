"""
Every account query lives here. Rules this file keeps:

  * Every value reaches SQL as a bound parameter (%s). Nothing is ever formatted into a query
    string, so there is nothing for an attacker's input to break out of.
  * Session and invite tokens arrive here already hashed (see token_hash).
  * Times are passed in from Python rather than using the database clock, which keeps the SQL
    plain and lets the same queries be tested on an in-memory database.
  * The `permissions` columns come from sql/user_permissions.sql. Queries that touch them take
    `perm`: False means the
    owner has not run that script yet, so the older query (no such column, empty permissions) is used and the app
    keeps working with just the two roles. Each query is written out in full in both forms, never assembled.
"""

import hashlib
import sqlite3

import psycopg

INTEGRITY_ERRORS = (psycopg.errors.UniqueViolation, sqlite3.IntegrityError)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def count_users(conn) -> int:
    return conn.execute("SELECT count(*) AS n FROM totr.users").fetchone()["n"]


def get_user_by_name(conn, username: str):
    return conn.execute(
        "SELECT user_id, username, password_hash, role, disabled FROM totr.users WHERE lower(username) = %s",
        (username.lower(),),
    ).fetchone()


def get_user(conn, user_id: int):
    return conn.execute(
        "SELECT user_id, username, password_hash, role, disabled FROM totr.users WHERE user_id = %s",
        (user_id,),
    ).fetchone()


def create_user(
    conn,
    username: str,
    password_hash: str,
    role: str,
    now,
    permissions: str = "",
    perm: bool = False,
) -> int:
    if perm:
        return conn.execute(
            "INSERT INTO totr.users (username, password_hash, role, permissions, created_at) VALUES (%s, %s, %s, %s, %s) "
            "RETURNING user_id",
            (username, password_hash, role, permissions, now),
        ).fetchone()["user_id"]
    return conn.execute(
        "INSERT INTO totr.users (username, password_hash, role, created_at) VALUES (%s, %s, %s, %s) RETURNING user_id",
        (username, password_hash, role, now),
    ).fetchone()["user_id"]


def get_user_full(conn, user_id: int):
    """Needs the permissions column."""
    return conn.execute(
        "SELECT user_id, username, role, disabled, permissions FROM totr.users WHERE user_id = %s",
        (user_id,),
    ).fetchone()


def set_permissions(conn, user_id: int, permissions: str):
    conn.execute(
        "UPDATE totr.users SET permissions = %s WHERE user_id = %s", (permissions, user_id)
    )


def delete_user(conn, user_id: int):
    """Sessions are removed first (the database also cascades, but a plain test database does not)."""
    conn.execute("DELETE FROM totr.sessions WHERE user_id = %s", (user_id,))
    conn.execute("DELETE FROM totr.users WHERE user_id = %s", (user_id,))


def count_other_active_admins(conn, user_id: int) -> int:
    return conn.execute(
        "SELECT count(*) AS n FROM totr.users WHERE role = 'admin' AND disabled = false AND user_id <> %s",
        (user_id,),
    ).fetchone()["n"]


def set_password_hash(conn, user_id: int, password_hash: str):
    conn.execute(
        "UPDATE totr.users SET password_hash = %s WHERE user_id = %s", (password_hash, user_id)
    )


def touch_login(conn, user_id: int, now):
    conn.execute("UPDATE totr.users SET last_login_at = %s WHERE user_id = %s", (now, user_id))


def set_disabled(conn, user_id: int, disabled: bool) -> bool:
    return (
        conn.execute(
            "UPDATE totr.users SET disabled = %s WHERE user_id = %s", (disabled, user_id)
        ).rowcount
        == 1
    )


def list_users(conn, now, perm: bool = False):
    if perm:
        return conn.execute(
            "SELECT u.user_id, u.username, u.role, u.disabled, u.permissions, u.created_at, u.last_login_at, "
            "(SELECT count(*) FROM totr.sessions s WHERE s.user_id = u.user_id AND s.expires_at > %s) AS sessions "
            "FROM totr.users u ORDER BY lower(u.username)",
            (now,),
        ).fetchall()
    return conn.execute(
        "SELECT u.user_id, u.username, u.role, u.disabled, '' AS permissions, u.created_at, u.last_login_at, "
        "(SELECT count(*) FROM totr.sessions s WHERE s.user_id = u.user_id AND s.expires_at > %s) AS sessions "
        "FROM totr.users u ORDER BY lower(u.username)",
        (now,),
    ).fetchall()


def create_session(conn, token_h: str, user_id: int, csrf: str, now, expires, ip, user_agent):
    conn.execute(
        "INSERT INTO totr.sessions (token_hash, user_id, csrf_token, created_at, last_seen, expires_at, ip, user_agent) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (token_h, user_id, csrf, now, now, expires, (ip or "")[:64], (user_agent or "")[:200]),
    )


def get_session(conn, token_h: str, now, idle_cutoff, stale_before, perm: bool = False):
    """The signed-in user for a session, or None if it is unknown, expired, idle, or the user is
    disabled. 'stale' says last_seen is old enough to be worth refreshing."""
    if perm:
        return conn.execute(
            "SELECT s.user_id, s.csrf_token, u.username, u.role, u.permissions, "
            "CASE WHEN s.last_seen < %s THEN 1 ELSE 0 END AS stale "
            "FROM totr.sessions s JOIN totr.users u ON u.user_id = s.user_id "
            "WHERE s.token_hash = %s AND s.expires_at > %s AND s.last_seen > %s AND u.disabled = false",
            (stale_before, token_h, now, idle_cutoff),
        ).fetchone()
    return conn.execute(
        "SELECT s.user_id, s.csrf_token, u.username, u.role, '' AS permissions, "
        "CASE WHEN s.last_seen < %s THEN 1 ELSE 0 END AS stale "
        "FROM totr.sessions s JOIN totr.users u ON u.user_id = s.user_id "
        "WHERE s.token_hash = %s AND s.expires_at > %s AND s.last_seen > %s AND u.disabled = false",
        (stale_before, token_h, now, idle_cutoff),
    ).fetchone()


def touch_session(conn, token_h: str, now):
    conn.execute("UPDATE totr.sessions SET last_seen = %s WHERE token_hash = %s", (now, token_h))


def delete_session(conn, token_h: str):
    conn.execute("DELETE FROM totr.sessions WHERE token_hash = %s", (token_h,))


def delete_user_sessions(conn, user_id: int, keep_token_h: str = ""):
    conn.execute(
        "DELETE FROM totr.sessions WHERE user_id = %s AND token_hash <> %s", (user_id, keep_token_h)
    )


def account_info(conn, user_id: int):
    return conn.execute(
        "SELECT username, role, created_at, last_login_at FROM totr.users WHERE user_id = %s",
        (user_id,),
    ).fetchone()


def list_sessions(conn, user_id: int, now):
    """One person's live sessions, newest activity first. Only the hash is read, never the cookie
    value."""
    return conn.execute(
        "SELECT token_hash, created_at, last_seen, ip, user_agent FROM totr.sessions WHERE user_id = %s AND expires_at > %s "
        "ORDER BY last_seen DESC",
        (user_id, now),
    ).fetchall()


def delete_own_session(conn, user_id: int, prefix: str, keep_token_h: str) -> int:
    """Signs out one of this person's other sessions, named by the first 16 characters of its hash.
    Returns how many went."""
    return conn.execute(
        "DELETE FROM totr.sessions WHERE user_id = %s AND substr(token_hash, 1, 16) = %s AND token_hash <> %s",
        (user_id, prefix, keep_token_h),
    ).rowcount


def purge_sessions(conn, now, idle_cutoff):
    conn.execute(
        "DELETE FROM totr.sessions WHERE expires_at <= %s OR last_seen <= %s", (now, idle_cutoff)
    )


def create_invite(
    conn,
    token_h: str,
    role: str,
    created_by,
    now,
    expires,
    permissions: str = "",
    perm: bool = False,
):
    if perm:
        conn.execute(
            "INSERT INTO totr.invites (token_hash, role, permissions, created_by, created_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (token_h, role, permissions, created_by, now, expires),
        )
        return
    conn.execute(
        "INSERT INTO totr.invites (token_hash, role, created_by, created_at, expires_at) VALUES (%s, %s, %s, %s, %s)",
        (token_h, role, created_by, now, expires),
    )


def invite_usable(conn, token_h: str, now) -> bool:
    """Is there an unused, unexpired invite with this hash? A cheap look, so a made-up link costs
    nothing (the password hash is the expensive part)."""
    return (
        conn.execute(
            "SELECT 1 FROM totr.invites WHERE token_hash = %s AND used_at IS NULL AND expires_at > %s",
            (token_h, now),
        ).fetchone()
        is not None
    )


def consume_invite(conn, token_h: str, now, perm: bool = False):
    """Marks an unused, unexpired invite as used and returns (role, permissions), in one atomic
    statement, so two people cannot both redeem the same link. None if the link is not usable."""
    if perm:
        row = conn.execute(
            "UPDATE totr.invites SET used_at = %s WHERE token_hash = %s AND used_at IS NULL AND expires_at > %s "
            "RETURNING role, permissions",
            (now, token_h, now),
        ).fetchone()
        return (row["role"], row["permissions"]) if row else None
    row = conn.execute(
        "UPDATE totr.invites SET used_at = %s WHERE token_hash = %s AND used_at IS NULL AND expires_at > %s "
        "RETURNING role",
        (now, token_h, now),
    ).fetchone()
    return (row["role"], "") if row else None


def set_invite_user(conn, token_h: str, user_id: int):
    conn.execute("UPDATE totr.invites SET used_by = %s WHERE token_hash = %s", (user_id, token_h))


# An invite is named by the first 16 characters of its token hash: enough to tell invites apart,
# useless for working out the link (which is never stored).
def pending_invites(conn, now, perm: bool = False):
    if perm:
        return conn.execute(
            "SELECT substr(i.token_hash, 1, 16) AS invite_id, i.role, i.permissions, i.created_at, i.expires_at, "
            "u.username AS created_by FROM totr.invites i LEFT JOIN totr.users u ON u.user_id = i.created_by "
            "WHERE i.used_at IS NULL AND i.expires_at > %s ORDER BY i.created_at DESC",
            (now,),
        ).fetchall()
    return conn.execute(
        "SELECT substr(i.token_hash, 1, 16) AS invite_id, i.role, '' AS permissions, i.created_at, i.expires_at, "
        "u.username AS created_by FROM totr.invites i LEFT JOIN totr.users u ON u.user_id = i.created_by "
        "WHERE i.used_at IS NULL AND i.expires_at > %s ORDER BY i.created_at DESC",
        (now,),
    ).fetchall()


def get_pending_invite(conn, invite_id: str, now, perm: bool = False):
    if perm:
        return conn.execute(
            "SELECT substr(token_hash, 1, 16) AS invite_id, role, permissions FROM totr.invites "
            "WHERE substr(token_hash, 1, 16) = %s AND used_at IS NULL AND expires_at > %s",
            (invite_id, now),
        ).fetchone()
    return conn.execute(
        "SELECT substr(token_hash, 1, 16) AS invite_id, role, '' AS permissions FROM totr.invites "
        "WHERE substr(token_hash, 1, 16) = %s AND used_at IS NULL AND expires_at > %s",
        (invite_id, now),
    ).fetchone()


def set_invite_permissions(conn, invite_id: str, permissions: str):
    conn.execute(
        "UPDATE totr.invites SET permissions = %s WHERE substr(token_hash, 1, 16) = %s AND used_at IS NULL",
        (permissions, invite_id),
    )


def revoke_invite(conn, invite_id: str) -> bool:
    return (
        conn.execute(
            "DELETE FROM totr.invites WHERE substr(token_hash, 1, 16) = %s AND used_at IS NULL",
            (invite_id,),
        ).rowcount
        == 1
    )
