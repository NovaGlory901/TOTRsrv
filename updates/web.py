"""The admin API for updates. The gate in auth/web.py keeps it to the admin role (every /api/admin
path not listed with a permission is admin-only)."""

import logging
import threading

import fastapi

import db
import tlsid
import version
from auth import web as auth_web

from . import fetch, repo, signing

log = logging.getLogger("totr.updates")
router = fastapi.APIRouter()


def _ca():
    pem = tlsid.ca_pem()
    return tlsid.fingerprint(pem) if pem else None


def overview() -> dict:
    ok, why = fetch.configured()
    return {
        "current": version.SERVER_VERSION,
        "repo": repo(),
        "engine": db.engine(),
        "key": fetch.key_fingerprint(),
        "ca": _ca(),
        "configured": ok,
        "problem": why,
        "checked": fetch.last_check() or None,
        "fetching": fetch.app_status(),
        "service": fetch.service_status(),
    }


@router.get("/api/admin/updates")
def get_updates():
    return overview()


@router.post("/api/admin/updates/check")
def check_now():
    return fetch.check()


@router.post("/api/admin/updates/install")
def install(request: fastapi.Request):
    """Fetches and verifies the newest release in the background, then files the request; the update
    service installs it. Poll GET /api/admin/updates."""
    ok, why = fetch.configured()
    if not ok:
        raise fastapi.HTTPException(status_code=409, detail=why)
    actor = (auth_web.current_user(request) or {}).get("username", "")

    def work():
        try:
            fetch.stage(actor)
        except signing.UpdateError as e:
            log.warning("update not started: %s", e)
        except Exception:
            log.exception("update fetch failed")

    threading.Thread(target=work, name="update-fetch", daemon=True).start()
    return {"started": True}
