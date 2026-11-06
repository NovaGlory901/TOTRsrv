"""The song-offer endpoints. A signed-in person offers and sends their own songs (everything is
limited to their own id); people who may add music (the Upload permission, gate rule in auth/web.py)
answer offers and ask for songs. Until sql/song_offers.sql has been run on PostgreSQL the answer is
a clear 503."""

import os
import pathlib
import tempfile

import fastapi
import pydantic

import db
from auth import web as auth_web

from . import MAX_ITEMS, service

router = fastapi.APIRouter()


class ItemBody(pydantic.BaseModel):
    title: str = pydantic.Field(max_length=400)
    artist: str = pydantic.Field(default="", max_length=400)
    album: str = pydantic.Field(default="", max_length=400)
    ext: str = pydantic.Field(max_length=8)
    size: int
    sha256: str = pydantic.Field(max_length=64)


class OfferBody(pydantic.BaseModel):
    note: str = pydantic.Field(default="", max_length=600)
    items: list[ItemBody] = pydantic.Field(max_length=MAX_ITEMS)


class AcceptBody(pydantic.BaseModel):
    items: list[ItemBody] = pydantic.Field(max_length=MAX_ITEMS)


class AnswerBody(pydantic.BaseModel):
    accept: list[int] = pydantic.Field(max_length=MAX_ITEMS)


class AskBody(pydantic.BaseModel):
    username: str = pydantic.Field(max_length=64)
    note: str = pydantic.Field(default="", max_length=600)


async def _run(fn, *args):
    """Like auth.web._guarded, plus the clear 503 for a database that has not had
    sql/song_offers.sql yet."""
    if not db.auth_enabled():
        return None, auth_web.json_error(service.SETUP, 503)
    data, err = await auth_web._guarded(fn, *args)
    if err is not None and err.status_code == 503:
        return None, auth_web.json_error(service.SETUP, 503)
    return data, err


def _user(request):
    return auth_web.current_user(request)


@router.post("/api/songs/offers")
async def make_offer(body: OfferBody, request: fastapi.Request):
    data, err = await _run(
        service.create_offer, _user(request), body.note, [i.model_dump() for i in body.items]
    )
    return err or data


@router.get("/api/songs/offers")
async def list_offers(request: fastapi.Request):
    data, err = await _run(service.my_offers, _user(request))
    return err or {"offers": data}


@router.post("/api/songs/offers/{offer_id}/cancel")
async def cancel_offer(offer_id: str, request: fastapi.Request):
    _, err = await _run(service.cancel_offer, _user(request), offer_id)
    return err or {"ok": True}


@router.put("/api/songs/offers/{offer_id}/items/{item_no}")
async def send_song(offer_id: str, item_no: int, request: fastapi.Request):
    """The raw bytes of one accepted song. It must be exactly the size and checksum that were offered."""
    user = _user(request)
    item, err = await _run(service.begin_upload, user, offer_id, item_no)
    if err is not None:
        return err
    tmp = None
    try:
        fd, name = tempfile.mkstemp(prefix="totr-offer-", suffix=item["ext"])
        tmp = pathlib.Path(name)
        total = 0
        with os.fdopen(fd, "wb") as out:
            async for chunk in request.stream():
                total += len(chunk)
                if total > item["size"]:
                    break
                out.write(chunk)
        if total != item["size"]:
            service.release_upload(
                offer_id, item_no, "The song did not arrive in full. Send it again."
            )
            return auth_web.json_error(
                "The song did not arrive in full, or was bigger than offered. Send it again.", 422
            )
        data, err = await _run(service.finish_upload, user, offer_id, item, tmp)
        return err or data
    except Exception:
        service.release_upload(offer_id, item_no, "The transfer failed. Send it again.")
        raise
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


@router.get("/api/songs/requests")
async def my_requests(request: fastapi.Request):
    data, err = await _run(service.my_requests, _user(request))
    return err or {"requests": data}


@router.post("/api/songs/requests/{request_id}/accept")
async def accept_request(request_id: str, body: AcceptBody, request: fastapi.Request):
    data, err = await _run(
        service.accept_request, _user(request), request_id, [i.model_dump() for i in body.items]
    )
    return err or data


@router.post("/api/songs/requests/{request_id}/decline")
async def decline_request(request_id: str, request: fastapi.Request):
    _, err = await _run(service.decline_request, _user(request), request_id)
    return err or {"ok": True}


@router.get("/api/admin/offers")
async def admin_offers():
    data, err = await _run(service.pending_offers)
    return err or {"offers": data}


@router.post("/api/admin/offers/{offer_id}/answer")
async def admin_answer(offer_id: str, body: AnswerBody, request: fastapi.Request):
    data, err = await _run(service.answer, _user(request), offer_id, body.accept)
    return err or data


@router.get("/api/admin/requests")
async def admin_requests():
    data, err = await _run(service.requests_for_admin)
    return err or {"requests": data}


@router.post("/api/admin/requests")
async def admin_ask(body: AskBody, request: fastapi.Request):
    data, err = await _run(service.ask_person, _user(request), body.username, body.note)
    return err or data


@router.post("/api/admin/requests/{request_id}/cancel")
async def admin_cancel(request_id: str, request: fastapi.Request):
    _, err = await _run(service.cancel_request, _user(request), request_id)
    return err or {"ok": True}
