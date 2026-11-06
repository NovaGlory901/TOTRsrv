"""The per-person data API. Any signed-in person may use it, on their own data only (the gate
requires a session, every query is limited to the signed-in person's id). Until sql/user_data.sql
has been run the answer is a clear 503 and the app simply keeps its settings on the device."""

import contextlib
import re
import sqlite3
from datetime import UTC, datetime

import fastapi
import psycopg
import pydantic

import db
from auth import web as auth_web

from . import MAX_ITEMS, MAX_VALUE, NAME_PATTERN, store

router = fastapi.APIRouter()
connect = db.get_auth_conn  # the accounts role (tests swap this for a throwaway database)
SETUP = "Syncing needs setting up: run sql/user_data.sql on your PostgreSQL database. Until then settings stay on each device."
NAME = re.compile(NAME_PATTERN)
MAX_MS = 4_102_444_800_000  # the year 2100: anything later is not a clock


class SaveBody(pydantic.BaseModel):
    value: str = pydantic.Field(max_length=MAX_VALUE)
    updated_ms: int = pydantic.Field(ge=0, le=MAX_MS)


@contextlib.contextmanager
def session():
    """A connection as the accounts role, or a clear 503 when the owner has not created the table yet."""
    if not db.auth_enabled():
        raise fastapi.HTTPException(status_code=503, detail=SETUP)
    try:
        with connect() as conn:
            yield conn
    except psycopg.errors.UndefinedTable:
        raise fastapi.HTTPException(status_code=503, detail=SETUP)
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            raise fastapi.HTTPException(status_code=503, detail=SETUP)
        raise


def _uid(request) -> int:
    return auth_web.current_user(request)["user_id"]


def _name(raw: str) -> str:
    if not NAME.match(raw or ""):
        raise fastapi.HTTPException(status_code=422, detail="That name is not allowed.")
    return raw


@router.get("/api/me/data")
def get_all(request: fastapi.Request):
    uid = _uid(request)
    with session() as conn:
        rows = store.list_items(conn, uid)
    return {
        "items": {
            r["name"]: {"value": r["value"], "updated_ms": int(r["updated_ms"])} for r in rows
        }
    }


@router.put("/api/me/data/{name}")
def put_one(name: str, body: SaveBody, request: fastapi.Request):
    uid, name = _uid(request), _name(name)
    with session() as conn:
        if store.get_item(conn, uid, name) is None and store.count_items(conn, uid) >= MAX_ITEMS:
            raise fastapi.HTTPException(
                status_code=409, detail=f"At most {MAX_ITEMS} items can be kept."
            )
        kept = store.save_item(conn, uid, name, body.value, body.updated_ms, datetime.now(UTC))
        stored = store.get_item(conn, uid, name)
    return {"kept": kept, "updated_ms": int(stored["updated_ms"])}


# POST like the other deletes: the gate wants a body with every change
@router.post("/api/me/data/{name}/delete")
def delete_one(name: str, request: fastapi.Request):
    uid, name = _uid(request), _name(name)
    with session() as conn:
        store.delete_item(conn, uid, name)
    return {"ok": True}
