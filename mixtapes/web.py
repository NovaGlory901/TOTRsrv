"""The mixtape API. Any signed-in person may use it, on their own mixtapes only (the gate already
requires a session; every query here is also limited to the signed-in user's id, so ids from the
browser are never trusted)."""

import contextlib
import re
import typing
from datetime import UTC, datetime

import fastapi
import psycopg
import pydantic

import db
from auth import web as auth_web

from . import MAX_ENTRIES, MAX_MIXTAPES, MAX_NAME, store

router = fastapi.APIRouter()
connect = db.get_mixtape_conn  # tests swap this for a throwaway database
SETUP = "Mixtapes need setting up: run sql/mixtapes.sql on your PostgreSQL database and add DB_MIXTAPE_USER / DB_MIXTAPE_PASSWORD to .env."

TrackId = typing.Annotated[int, pydantic.Field(ge=1, le=2_147_483_647)]


class CreateBody(pydantic.BaseModel):
    name: str = pydantic.Field(max_length=200)
    track_ids: list[TrackId] = pydantic.Field(default_factory=list, max_length=200)


class NameBody(pydantic.BaseModel):
    name: str = pydantic.Field(max_length=200)


class TracksBody(pydantic.BaseModel):
    track_ids: list[TrackId] = pydantic.Field(min_length=1, max_length=200)


class MoveBody(pydantic.BaseModel):
    direction: typing.Literal["up", "down"]


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _clean_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", raw or "").strip()
    if not name or len(name) > MAX_NAME or any(ord(c) < 32 for c in name):
        raise fastapi.HTTPException(
            status_code=422, detail=f"Give the mixtape a name of 1 to {MAX_NAME} characters."
        )
    return name


def _uid(request) -> int:
    return auth_web.current_user(request)["user_id"]


@contextlib.contextmanager
def session():
    """A connection as the mixtape role, or a clear 503 if the owner has not set the feature up yet."""
    if not db.mixtape_enabled():
        raise fastapi.HTTPException(status_code=503, detail=SETUP)
    try:
        with connect() as conn:
            yield conn
    except psycopg.errors.UndefinedTable:
        raise fastapi.HTTPException(status_code=503, detail=SETUP)


def lookup_tracks(ids) -> dict:
    """Titles and artists for these track ids, read with the read-only music role. Unknown ids are
    simply absent."""
    if not ids:
        return {}
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT t.track_id, t.title, t.album_id, string_agg(DISTINCT ar.name, ', ') AS artists FROM totr.v_tracks t "
            "LEFT JOIN totr.v_track_artists ta ON ta.track_id = t.track_id LEFT JOIN totr.v_artists ar ON ar.artist_id = ta.artist_id "
            "WHERE t.track_id = ANY(%s::int[]) GROUP BY t.track_id, t.title, t.album_id",
            (list(ids),),
        ).fetchall()
    return {r["track_id"]: r for r in rows}


def _own(conn, request, mixtape_id: int):
    mixtape = store.get_mixtape(conn, _uid(request), mixtape_id)
    if mixtape is None:
        raise fastapi.HTTPException(status_code=404, detail="That mixtape does not exist.")
    return mixtape


def _append(conn, mixtape_id: int, track_ids: list) -> dict:
    """Adds the songs that exist, at the end, in the order given. Returns how many went in."""
    ids = list(dict.fromkeys(track_ids))
    found = lookup_tracks(ids)
    wanted = [i for i in ids if i in found]
    if len(store.entries(conn, mixtape_id)) + len(wanted) > MAX_ENTRIES:
        raise fastapi.HTTPException(
            status_code=409, detail=f"A mixtape holds at most {MAX_ENTRIES} songs."
        )
    now, position = _now(), store.next_position(conn, mixtape_id)
    for track_id in wanted:
        store.add_entry(conn, mixtape_id, track_id, position, now)
        position += 1
    if wanted:
        store.touch(conn, mixtape_id, now)
    return {"added": len(wanted), "skipped": len(ids) - len(wanted)}


@router.get("/api/mixtapes")
def list_mixtapes(request: fastapi.Request):
    if not db.mixtape_enabled():
        return {"enabled": False, "setup": SETUP, "mixtapes": []}
    with session() as conn:
        rows = store.list_mixtapes(conn, _uid(request))
    return {"enabled": True, "mixtapes": [{**r, "updated_at": _iso(r["updated_at"])} for r in rows]}


@router.post("/api/mixtapes")
def create_mixtape(body: CreateBody, request: fastapi.Request):
    name = _clean_name(body.name)
    with session() as conn:
        uid = _uid(request)
        if store.count_mixtapes(conn, uid) >= MAX_MIXTAPES:
            raise fastapi.HTTPException(
                status_code=409, detail=f"You can have at most {MAX_MIXTAPES} mixtapes."
            )
        try:
            mixtape_id = store.create_mixtape(conn, uid, name, _now())
        except store.INTEGRITY_ERRORS:
            raise fastapi.HTTPException(
                status_code=409, detail="You already have a mixtape with that name."
            )
        added = (
            _append(conn, mixtape_id, body.track_ids)
            if body.track_ids
            else {"added": 0, "skipped": 0}
        )
    return {"mixtape_id": mixtape_id, "name": name, **added}


@router.get("/api/mixtapes/{mixtape_id}")
def get_mixtape(mixtape_id: int, request: fastapi.Request):
    with session() as conn:
        mixtape = _own(conn, request, mixtape_id)
        entries = store.entries(conn, mixtape_id)
    details = lookup_tracks([e["track_id"] for e in entries])
    tracks = [
        {
            "entry_id": e["entry_id"],
            "track_id": e["track_id"],
            "title": details[e["track_id"]]["title"],
            "artists": details[e["track_id"]]["artists"] or "",
            "album_id": details[e["track_id"]]["album_id"],
        }
        for e in entries
        if e["track_id"] in details
    ]
    return {"mixtape_id": mixtape["mixtape_id"], "name": mixtape["name"], "tracks": tracks}


@router.post("/api/mixtapes/{mixtape_id}/rename")
def rename_mixtape(mixtape_id: int, body: NameBody, request: fastapi.Request):
    name = _clean_name(body.name)
    with session() as conn:
        _own(conn, request, mixtape_id)
        try:
            store.rename_mixtape(conn, _uid(request), mixtape_id, name, _now())
        except store.INTEGRITY_ERRORS:
            raise fastapi.HTTPException(
                status_code=409, detail="You already have a mixtape with that name."
            )
    return {"ok": True, "name": name}


@router.post("/api/mixtapes/{mixtape_id}/delete")
def delete_mixtape(mixtape_id: int, request: fastapi.Request):
    with session() as conn:
        _own(conn, request, mixtape_id)
        store.delete_mixtape(conn, _uid(request), mixtape_id)
    return {"ok": True}


@router.post("/api/mixtapes/{mixtape_id}/tracks")
def add_tracks(mixtape_id: int, body: TracksBody, request: fastapi.Request):
    with session() as conn:
        _own(conn, request, mixtape_id)
        return _append(conn, mixtape_id, body.track_ids)


@router.post("/api/mixtapes/{mixtape_id}/tracks/{entry_id}/remove")
def remove_track(mixtape_id: int, entry_id: int, request: fastapi.Request):
    with session() as conn:
        _own(conn, request, mixtape_id)
        if not store.remove_entry(conn, mixtape_id, entry_id):
            raise fastapi.HTTPException(status_code=404, detail="That song is not in this mixtape.")
        store.touch(conn, mixtape_id, _now())
    return {"ok": True}


@router.post("/api/mixtapes/{mixtape_id}/tracks/{entry_id}/move")
def move_track(mixtape_id: int, entry_id: int, body: MoveBody, request: fastapi.Request):
    """Moves one song a step earlier or later by swapping places with its neighbour."""
    with session() as conn:
        _own(conn, request, mixtape_id)
        entries = store.entries(conn, mixtape_id)
        order = [e["entry_id"] for e in entries]
        if entry_id not in order:
            raise fastapi.HTTPException(status_code=404, detail="That song is not in this mixtape.")
        if len({e["position"] for e in entries}) != len(entries):  # tidy up any tie before swapping
            for n, eid in enumerate(order, 1):
                store.set_position(conn, mixtape_id, eid, n)
            entries = store.entries(conn, mixtape_id)
        i = order.index(entry_id)
        j = i - 1 if body.direction == "up" else i + 1
        if 0 <= j < len(entries):
            a, b = entries[i], entries[j]
            store.set_position(conn, mixtape_id, a["entry_id"], b["position"])
            store.set_position(conn, mixtape_id, b["entry_id"], a["position"])
            store.touch(conn, mixtape_id, _now())
    return {"ok": True}
