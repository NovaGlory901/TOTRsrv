"""The collector's API. Everything here is admin-only (enforced by the gate in auth/web.py)."""

import logging
import threading
import typing

import fastapi
import pydantic
from starlette.responses import JSONResponse

import db
from auth import web as auth_web
from spiders.base import squash

from . import acquire, finder, jobs, quality, state

log = logging.getLogger("totr.collector")
router = fastapi.APIRouter()


class IdBody(pydantic.BaseModel):
    id: str = pydantic.Field(max_length=40)


class DismissBody(IdBody):
    block_artist: bool = False


class IdsBody(pydantic.BaseModel):
    ids: list[typing.Annotated[str, pydantic.Field(max_length=40)]] = pydantic.Field(
        min_length=1, max_length=100
    )


class WantedBody(pydantic.BaseModel):
    artist: str = pydantic.Field(min_length=1, max_length=120)
    title: str = pydantic.Field(default="", max_length=160)


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


def _actor(request) -> str:
    return auth_web.current_user(request)["username"]


@router.get("/api/collector")
def overview():
    data = state.read_suggestions()
    return {
        "config": state.load_config(),
        "sources_available": finder.sources_available(),
        "adds_enabled": db.maint_enabled(),
        "searching": finder.is_running(),
        "search_status": finder.progress["text"],
        "last_search": data["last_search"],
        "wanted": state.wanted(),
        "suggestions": data["suggestions"],
        "jobs": jobs.listing(),
        "added": [a for a in reversed(state.added()) if not a.get("undone_at")][:25],
    }


@router.post("/api/collector/config")
def save_config(raw: dict = fastapi.Body(...)):
    try:
        cfg = state.clean_config(raw)
    except ValueError as e:
        return _error(str(e), 422)
    state.save_config(cfg)
    return cfg


@router.post("/api/collector/wanted/add")
def wanted_add(body: WantedBody):
    return {"added": state.add_wanted(body.artist, [body.title] if body.title.strip() else None)}


@router.post("/api/collector/wanted/remove")
def wanted_remove(body: WantedBody):
    state.remove_wanted(body.artist, body.title)
    return {"ok": True}


@router.post("/api/collector/search")
def search_now():
    if finder.is_running():
        return {"started": False, "detail": "Already searching"}

    def work():
        try:
            finder.search_all()
        except ValueError as e:
            finder.progress["text"] = ""
            state.write_suggestions(
                {
                    **state.read_suggestions(),
                    "last_search": {
                        "at": state.now_iso(),
                        "new": 0,
                        "genres": [],
                        "wanted": 0,
                        "errors": [str(e)],
                    },
                }
            )
        except Exception:
            log.exception("music search failed")

    threading.Thread(target=work, name="collector-search", daemon=True).start()
    return {"started": True}


def _queue_add(suggestion: dict, actor: str):
    """Queues one add, or returns None if that suggestion is already waiting or running."""
    if jobs.active(suggestion["id"]):
        return None
    label = f"{suggestion['title']} by {suggestion['artist']}"
    return jobs.submit(
        label,
        lambda progress: acquire.acquire(suggestion, actor, progress),
        int(suggestion.get("size") or 0),
        key=suggestion["id"],
    )


@router.post("/api/collector/approve")
def approve(body: IdBody, request: fastapi.Request):
    s = state.find_suggestion(body.id)
    if s is None:
        return _error("That suggestion is gone. Search again.", 404)
    job = _queue_add(s, _actor(request))
    return job if job is not None else _error("That one is already being added.", 409)


@router.post("/api/collector/approve-artist")
def approve_artist(body: IdBody, request: fastapi.Request):
    s = state.find_suggestion(body.id)
    if s is None:
        return _error("That suggestion is gone. Search again.", 404)
    key = squash(s["artist"])
    mine = [x for x in state.read_suggestions()["suggestions"] if squash(x["artist"]) == key]
    queued = [j for j in (_queue_add(x, _actor(request)) for x in mine) if j is not None]
    return {"queued": len(queued), "already_queued": len(mine) - len(queued)}


@router.post("/api/collector/dismiss")
def dismiss(body: DismissBody):
    s = state.find_suggestion(body.id)
    if s is None:
        return _error("That suggestion is gone.", 404)
    state.hide(release_id_=body.id, artist_key=squash(s["artist"]) if body.block_artist else "")
    if body.block_artist:
        key = squash(s["artist"])
        data = state.read_suggestions()
        data["suggestions"] = [x for x in data["suggestions"] if squash(x["artist"]) != key]
        state.write_suggestions(data)
    else:
        state.remove_suggestion(body.id)
    return {"ok": True}


@router.get("/api/collector/review-added")
def review_added():
    """Collector adds that score below the bar, for a bulk undo. Older adds were not scored when
    they were made, so they are scored from what was recorded (title, artist, song count, source);
    online checks are not repeated."""
    bar = state.load_config()["min_quality"]
    items = []
    for a in reversed(state.added()):
        if a.get("undone_at"):
            continue
        points, why, scored_then = a.get("quality"), a.get("quality_reasons") or [], True
        if not isinstance(points, int):
            scored_then = False
            points, why = quality.score(a, legacy=True)
        if points < bar:
            items.append(
                {
                    "id": a["id"],
                    "title": a["title"],
                    "artist": a["artist"],
                    "source": a["source"],
                    "tracks": a.get("tracks"),
                    "single": a.get("single"),
                    "at": a.get("at"),
                    "quality": points,
                    "reasons": why,
                    "scored_when_added": scored_then,
                }
            )
    return {"min_quality": bar, "items": items}


@router.post("/api/collector/undo-many")
def undo_many(body: IdsBody, request: fastapi.Request):
    """Undoes several adds one by one (files go to .trash). One failing does not stop the rest."""
    actor, results = _actor(request), []
    for release_id in dict.fromkeys(body.ids):
        try:
            results.append(
                {"id": release_id, "ok": True, "message": acquire.undo(release_id, actor)}
            )
        except acquire.AcquireError as e:
            results.append({"id": release_id, "ok": False, "message": str(e)})
        except Exception:
            log.exception("undo failed")
            results.append(
                {
                    "id": release_id,
                    "ok": False,
                    "message": "That did not work and nothing was changed. See the server log.",
                }
            )
    return {"results": results, "undone": sum(r["ok"] for r in results)}


@router.post("/api/collector/undo")
def undo(body: IdBody, request: fastapi.Request):
    try:
        return {"ok": True, "message": acquire.undo(body.id, _actor(request))}
    except acquire.AcquireError as e:
        return _error(str(e), 409)
    except Exception:
        log.exception("undo failed")
        return _error("That did not work and nothing was changed. See the server log.", 500)
