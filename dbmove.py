"""The admin API behind the Database page's "Move to PostgreSQL" flow (admin role only: the gate
lists /api/admin/database as admin-only).

The server never touches the database layout itself, so the work is done by a one-time helper
process (tools/move_to_postgres.py). This file starts it, hands it the form over its standard input
(so the database administrator's password is in no argument, no environment variable, no file and no
log here), and turns the helper's progress lines into the status the page polls. The password is
held only in the request and in the helper.

    GET  /api/admin/database/move           where things stand: engine, counts to be copied, progress, the last result
    POST /api/admin/database/move/test      connects as the administrator and reports what it found (changes nothing)
    POST /api/admin/database/move/start     mode "rehearse" (copies, counts, undoes) or "move" (copies, counts, switches TOTR over, restarts)
    POST /api/admin/database/move/dismiss   hides the "moved" report
"""

import json
import logging
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime

import fastapi
import pydantic

import db
import localdb
import moving
from auth import web as auth_web

log = logging.getLogger("totr.dbmove")
router = fastapi.APIRouter()
HELPER = pathlib.Path(__file__).resolve().parent / "tools" / "move_to_postgres.py"
TEST_SECONDS, MOVE_SECONDS = 60, 30 * 60
RESTART_AFTER = 4  # seconds: long enough for the page to read the result once

_lock = threading.Lock()
_state = {
    "stage": "idle",
    "mode": None,
    "steps": [],
    "percent": 0,
    "message": "",
    "result": None,
    "restart": "",
}


class Form(pydantic.BaseModel):
    host: str = pydantic.Field(max_length=200)
    port: str = pydantic.Field("", max_length=5)
    dbname: str = pydantic.Field(max_length=41)
    sslmode: str = pydantic.Field("", max_length=12)
    roles: str = pydantic.Field("several", max_length=10)
    admin_user: str = pydantic.Field(max_length=63)
    admin_password: str = pydantic.Field(max_length=500)


class StartForm(Form):
    mode: str = pydantic.Field(max_length=10)


def _result_file() -> pathlib.Path:
    return localdb.data_dir() / "move-result.json"


def _saved_result():
    try:
        return json.loads(_result_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def local_counts() -> dict | None:
    """What a move would copy, by the names the page shows. None when TOTR is not on the built-in
    database."""
    if db.engine() != "sqlite":
        return None
    try:
        with localdb.connect() as c:
            kinds = {
                r["slug"]: r["n"]
                for r in c.execute(
                    "SELECT t.item_type_slug AS slug, count(*) AS n FROM media.items i JOIN media.item_types t ON t.type_id = i.type_id GROUP BY 1"
                ).fetchall()
            }
            return {
                "artists": kinds.get("artist", 0),
                "albums": kinds.get("album", 0),
                "songs": kinds.get("song", 0),
                "accounts": c.execute("SELECT count(*) AS n FROM totr.users").fetchone()["n"],
                "playlists": c.execute("SELECT count(*) AS n FROM totr.mixtapes").fetchone()["n"],
            }
    except Exception:
        log.exception("could not count the local database")
        return None


def _transport_ok(request) -> bool:
    """The administrator's password must not cross a network in the clear: https, or this machine
    itself."""
    return auth_web.is_secure(request) or (
        request.client is not None and request.client.host in ("127.0.0.1", "::1")
    )


def _require_private_channel(request):
    if not _transport_ok(request):
        raise fastapi.HTTPException(
            status_code=400,
            detail="The database administrator's password must not cross the network unencrypted. "
            "Open TOTR over https (or on this server itself) for this step.",
        )


def _public_state() -> dict:
    with _lock:
        s = {k: (list(v) if isinstance(v, list) else v) for k, v in _state.items()}
    s["engine"] = db.engine()
    s["counts"] = local_counts() if s["stage"] in ("idle", "failed", "done") else None
    if s["result"] is None and s["engine"] == "postgres":
        s["result"] = _saved_result()
    s["moving"] = moving.active()
    s["private_channel"] = None
    return s


@router.get("/api/admin/database/move")
def status(request: fastapi.Request):
    s = _public_state()
    s["private_channel"] = _transport_ok(request)
    return s


def _run_helper(payload: dict, seconds: int, on_event):
    """Runs the helper with `payload` on its standard input. Calls on_event for each json line.
    Returns the final result event (or a failure one)."""
    secret = payload.get("admin_password", "")
    proc = subprocess.Popen(
        [sys.executable, str(HELPER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(HELPER.parent.parent),
    )
    timer = threading.Timer(seconds, proc.kill)
    timer.start()
    errors = []
    threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True).start()
    result = None
    try:
        proc.stdin.write(json.dumps(payload))
        proc.stdin.close()
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event") == "result":
                result = event
            else:
                on_event(event)
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
    if result is None:
        tail = (
            (errors[0] if errors else "").replace(secret, "***")[-500:]
            if secret
            else (errors[0] if errors else "")[-500:]
        )
        log.error("the move helper ended without a result: %s", tail.strip())
        result = {
            "event": "result",
            "ok": False,
            "message": "The helper stopped unexpectedly (or took too long). Nothing was switched. Details: journalctl -u totr.",
        }
    return result


def _note(event: dict):
    with _lock:
        if event.get("event") == "progress":
            _state["percent"] = max(0, min(100, int(event.get("percent", 0))))
        elif event.get("event") == "step":
            for s in _state["steps"]:
                if s["name"] == event["name"]:
                    s["state"] = event["state"]
                    break
            else:
                _state["steps"].append({"name": event["name"], "state": event["state"]})


def _restart_soon():
    time.sleep(RESTART_AFTER)
    # the service manager starts it again, now reading the new settings
    os.kill(os.getpid(), signal.SIGTERM)


def _work(payload: dict, mode: str):
    result = {"ok": False, "message": "It did not finish."}
    try:
        result = _run_helper(payload, MOVE_SECONDS, _note)
    except Exception:
        log.exception("the move failed")
        result = {
            "ok": False,
            "message": "It did not finish. Nothing was switched. Details: journalctl -u totr.",
        }
    finally:
        payload["admin_password"] = ""
    done_ok = bool(result.get("ok"))
    with _lock:
        _state["stage"] = "done" if done_ok else "failed"
        _state["message"] = "" if done_ok else str(result.get("message", ""))[:500]
        _state["percent"] = 100 if done_ok else _state["percent"]
        if done_ok:
            result = {k: v for k, v in result.items() if k != "event"}
            result["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            _state["result"] = result
    if done_ok and mode == "move":
        try:
            _result_file().write_text(json.dumps(result), encoding="utf-8")
        except OSError:
            log.warning("could not save the move report")
        if os.environ.get("INVOCATION_ID"):  # set by systemd for a service: it will restart us
            threading.Thread(target=_restart_soon, name="restart-after-move", daemon=True).start()
            _state["restart"] = "automatic"
        else:
            # moving stays up: TOTR must not take writes into the file that was set aside
            _state["restart"] = "manual"
        return
    moving.set_active(False)


@router.post("/api/admin/database/move/test")
def test(form: Form, request: fastapi.Request):
    _require_private_channel(request)
    if db.engine() != "sqlite":
        raise fastapi.HTTPException(status_code=409, detail="TOTR is not on the built-in database.")
    with _lock:
        if _state["stage"] == "running":
            raise fastapi.HTTPException(status_code=409, detail="A move is already running.")
    payload = {**form.model_dump(), "mode": "test"}
    result = _run_helper(payload, TEST_SECONDS, lambda event: None)
    payload["admin_password"] = ""
    return {
        "ok": bool(result.get("ok")),
        "checks": result.get("checks", []),
        "message": result.get("message", ""),
    }


@router.post("/api/admin/database/move/start")
def start(form: StartForm, request: fastapi.Request):
    _require_private_channel(request)
    if form.mode not in ("rehearse", "move"):
        raise fastapi.HTTPException(status_code=400, detail="Unknown mode.")
    if db.engine() != "sqlite":
        raise fastapi.HTTPException(status_code=409, detail="TOTR is not on the built-in database.")
    with _lock:
        if _state["stage"] == "running" or moving.active():
            raise fastapi.HTTPException(status_code=409, detail="A move is already running.")
        _state.update(
            stage="running",
            mode=form.mode,
            steps=[],
            percent=0,
            message="",
            result=None,
            restart="",
        )
        moving.set_active(True)
    payload = form.model_dump()
    threading.Thread(
        target=_work, args=(payload, form.mode), name="move-to-postgres", daemon=True
    ).start()
    return {"started": True}


@router.post("/api/admin/database/move/dismiss")
def dismiss():
    with _lock:
        if _state["stage"] == "running":
            raise fastapi.HTTPException(status_code=409, detail="A move is running.")
        _state.update(stage="idle", mode=None, steps=[], percent=0, message="", result=None)
    try:
        _result_file().unlink()
    except OSError:
        pass
    return {"ok": True}
