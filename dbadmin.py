"""The admin Database page: which database TOTR runs on, and (for PostgreSQL) what each configured
role can really do. Read-only: it runs only SELECTs, shows no passwords and changes no setting.
Settings live in `.env` (docs/setup.md)."""

import fastapi

import db
import localdb

router = fastapi.APIRouter()
MIN_POSTGRES = 14

# What each purpose must be able to do, as (what it is for, a read-only probe). Nothing here changes
# data.
PROBES = {
    "read": [("read the music views", "SELECT 1 FROM totr.v_tracks LIMIT 1")],
    "auth": [("read and change accounts", "SELECT 1 FROM totr.users LIMIT 1")],
    "mixtape": [("read and change mixtapes", "SELECT 1 FROM totr.mixtapes LIMIT 1")],
    "write": [
        (
            "add music (insert into items)",
            "SELECT has_table_privilege(current_user, 'media.items', 'INSERT') AS ok",
        ),
        (
            "add relations",
            "SELECT has_table_privilege(current_user, 'media.item_relations', 'INSERT') AS ok",
        ),
    ],
    "maint": [
        (
            "fix and remove music (update and delete items)",
            "SELECT (has_table_privilege(current_user, 'media.items', 'UPDATE') AND has_table_privilege(current_user, 'media.items', 'DELETE')) AS ok",
        )
    ],
}
REQUIRED = {"read", "auth"}  # without these the site cannot show music or let anyone sign in
PURPOSE_LABEL = {
    "read": "browse and listen (required)",
    "auth": "sign-in and accounts (required)",
    "mixtape": "mixtapes",
    "write": "uploads",
    "maint": "approved maintenance and collector adds",
}


def _probe(purpose: str) -> dict:
    """Connects as the purpose's role and tries each probe. Returns what was found, in plain words."""
    out = {
        "purpose": purpose,
        "label": PURPOSE_LABEL[purpose],
        "configured": db.credentials(purpose) is not None,
        "connects": False,
        "checks": [],
    }
    if not out["configured"]:
        return out
    try:
        with db._connect(purpose) as conn:
            out["connects"] = True
            out["user"] = db.credentials(purpose)[0]
            if purpose == "read":
                out["server_version"] = conn.info.server_version
            for what, sql in PROBES[purpose]:
                try:
                    row = conn.execute(sql).fetchone()
                    ok = row.get("ok", True) if row else True
                    out["checks"].append({"what": what, "ok": bool(ok)})
                except Exception as e:  # a missing table or right: report it, do not fail the page
                    conn.rollback()
                    out["checks"].append({"what": what, "ok": False, "why": type(e).__name__})
    except Exception as e:
        out["error"] = type(e).__name__
    return out


def report() -> dict:
    if db.engine() == "sqlite":
        info = localdb.info()
        info["problems"] = (
            []
            if info["sqlite_ok"]
            else ["This Python's SQLite is too old for TOTR (needs 3.38 or newer)."]
        ) + (
            []
            if info["schema_version"] in (None, info["expected_version"])
            else ["The database file has a different layout version than this TOTR."]
        )
        return info
    import os

    roles = [_probe(p) for p in db.ROLES]
    version = next((r.get("server_version") for r in roles if r["purpose"] == "read"), None)
    problems = []
    if not roles[0]["connects"]:
        problems.append(
            "The read-only role cannot connect. Check DB_HOST, DB_PORT, DB_NAME, DB_SSLMODE, DB_USER and DB_PASSWORD in .env."
        )
    elif version and version // 10000 < MIN_POSTGRES:
        problems.append(
            f"PostgreSQL {version // 10000} is older than the {MIN_POSTGRES} TOTR is tested with."
        )
    for r in roles:
        if r["purpose"] in REQUIRED and not r["configured"]:
            problems.append(
                f"The {r['label']} role is not set in .env, so that part of TOTR is off or locked."
            )
        if r["configured"] and r["connects"] and not all(c["ok"] for c in r["checks"]):
            problems.append(
                f"The {r['label']} role is missing rights; run the matching script in sql/ (docs/setup.md)."
            )
    return {
        "engine": "postgres",
        "host": os.environ.get("DB_HOST"),
        "port": os.environ.get("DB_PORT") or "5432 (default)",
        "database": os.environ.get("DB_NAME"),
        "sslmode": os.environ.get("DB_SSLMODE")
        or ("disable" if (os.environ.get("DB_HOST") or "").startswith("/") else "require"),
        "single_role": db.single_role(),
        "server_version": version,
        "roles": roles,
        "problems": problems,
    }


@router.get("/api/admin/database")
def database_report():
    return report()
