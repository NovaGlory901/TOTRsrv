"""The rules of song offers. Raises AuthError (a refusal that is safe to show) like the account
code; auth.web turns it into a clean answer."""

import hashlib
import logging
import pathlib
import re
import secrets
import shutil
from datetime import timedelta

import db
import uploader
from auth import service as auth_service
from auth.service import AuthError

from . import (
    AUDIO_EXTS,
    KEEP_DAYS,
    KEEP_FREE_BYTES,
    MAX_ITEMS,
    MAX_OFFER_BYTES,
    MAX_PENDING_PER_PERSON,
    MAX_PENDING_REQUESTS,
    MAX_SONG_BYTES,
)

log = logging.getLogger("totr.offers")
connect = db.get_auth_conn  # tests swap this for a throwaway database
ID = re.compile(r"^[0-9a-f]{32}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SETUP = "Offering songs needs one more database step: run sql/song_offers.sql on your PostgreSQL database."


def _now():
    return auth_service._now()


def _text(value, limit, what, required=False) -> str:
    value = " ".join(
        "".join(
            c if c.isprintable() else " " for c in str(value if value is not None else "")
        ).split()
    )
    if required and not value:
        raise AuthError(f"Every song needs {what}.", 422)
    if len(value) > limit:
        raise AuthError(f"A {what} is too long (at most {limit} characters).", 422)
    return value


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _audit(event: str, actor: str, what: str, params: dict, result: str):
    """One line in reports/actions.log, the file the maintenance fixes already use."""
    try:
        from spiders import store as spider_store

        spider_store.audit(
            event,
            {"kind": "song_offer", "message": what, "action": {"type": event, "params": params}},
            result,
            actor,
        )
    except Exception:
        log.exception("could not write the audit line")


def _purge(conn, now):
    cutoff = now - timedelta(days=KEEP_DAYS)
    conn.execute(
        "DELETE FROM totr.song_offers WHERE (status <> 'pending' AND coalesce(answered_at, created_at) < %s) OR (status = 'pending' AND created_at < %s)",
        (cutoff, cutoff),
    )
    conn.execute(
        "DELETE FROM totr.song_requests WHERE (status <> 'pending' AND coalesce(answered_at, created_at) < %s) OR (status = 'pending' AND created_at < %s)",
        (cutoff, cutoff),
    )


def _clean_items(raw) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise AuthError("Choose at least one song.", 422)
    if len(raw) > MAX_ITEMS:
        raise AuthError(f"At most {MAX_ITEMS} songs in one offer.", 422)
    out, seen = [], set()
    for it in raw:
        if not isinstance(it, dict):
            raise AuthError("Every song needs a title, a size and a checksum.", 422)
        ext = str(it.get("ext", "")).lower()
        size, sha = it.get("size"), str(it.get("sha256", "")).lower()
        if ext not in AUDIO_EXTS:
            raise AuthError("Only mp3, flac, ogg, opus, m4a and wav songs can be offered.", 422)
        if not (
            isinstance(size, int) and not isinstance(size, bool) and 1 <= size <= MAX_SONG_BYTES
        ):
            raise AuthError(f"A song can be at most {MAX_SONG_BYTES // 1048576} MB.", 422)
        if not HEX64.match(sha):
            raise AuthError("Every song needs a checksum.", 422)
        if sha in seen:
            continue  # the same file twice in one offer counts once
        seen.add(sha)
        out.append(
            {
                "title": _text(it.get("title"), 200, "title", True),
                "artist": _text(it.get("artist"), 200, "artist name"),
                "album": _text(it.get("album"), 200, "album name"),
                "ext": ext,
                "size": size,
                "sha256": sha,
            }
        )
    if sum(i["size"] for i in out) > MAX_OFFER_BYTES:
        raise AuthError(
            f"An offer can hold at most {MAX_OFFER_BYTES // 1024**3} GB of songs. Offer fewer at a time.",
            422,
        )
    return out


def _insert_offer(conn, user_id, note, items, now, request_id=None, accepted_by=None):
    offer_id = secrets.token_hex(16)
    conn.execute(
        "INSERT INTO totr.song_offers (offer_id, user_id, request_id, note, status, created_at, answered_at, answered_by, answered_by_name) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            offer_id,
            user_id,
            request_id,
            note,
            "answered" if accepted_by else "pending",
            now,
            now if accepted_by else None,
            accepted_by["user_id"] if accepted_by else None,
            accepted_by["name"] if accepted_by else "",
        ),
    )
    for n, it in enumerate(items):
        conn.execute(
            "INSERT INTO totr.song_offer_items (offer_id, item_no, title, artist, album, ext, size, sha256, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                offer_id,
                n,
                it["title"],
                it["artist"],
                it["album"],
                it["ext"],
                it["size"],
                it["sha256"],
                "accepted" if accepted_by else "offered",
            ),
        )
    return offer_id


def create_offer(user: dict, note, items) -> dict:
    """A person offers songs. They wait for someone who may add music to accept some."""
    clean, note = _clean_items(items), _text(note, 300, "note")
    now = _now()
    with connect() as conn:
        _purge(conn, now)
        if (
            conn.execute(
                "SELECT count(*) AS n FROM totr.song_offers WHERE user_id = %s AND status = 'pending'",
                (user["user_id"],),
            ).fetchone()["n"]
            >= MAX_PENDING_PER_PERSON
        ):
            raise AuthError(
                "You already have offers waiting for an answer. Wait for them, or cancel one.", 429
            )
        offer_id = _insert_offer(conn, user["user_id"], note, clean, now)
    _audit(
        "offer_made",
        user["username"],
        f"{user['username']} offered {len(clean)} song(s)",
        {"offer": offer_id[:8], "songs": len(clean)},
        "waiting",
    )
    return {"offer_id": offer_id, "songs": len(clean)}


def _items(conn, offer_id):
    return [
        dict(r)
        for r in conn.execute(
            "SELECT item_no, title, artist, album, ext, size, status, error FROM totr.song_offer_items WHERE offer_id = %s ORDER BY item_no",
            (offer_id,),
        ).fetchall()
    ]


def _offer_view(conn, row):
    return {
        "offer_id": row["offer_id"],
        "note": row["note"],
        "status": row["status"],
        "created_at": _iso(row["created_at"]),
        "answered_at": _iso(row["answered_at"]),
        "answered_by": row["answered_by_name"] or "",
        "asked": bool(row["request_id"]),
        "items": _items(conn, row["offer_id"]),
    }


def my_offers(user: dict) -> list[dict]:
    with connect() as conn:
        _purge(conn, _now())
        rows = conn.execute(
            "SELECT offer_id, request_id, note, status, created_at, answered_at, answered_by_name FROM totr.song_offers WHERE user_id = %s AND status <> 'cancelled' ORDER BY created_at DESC",
            (user["user_id"],),
        ).fetchall()
        return [_offer_view(conn, r) for r in rows]


def _own_offer(conn, user, offer_id):
    if not ID.match(offer_id or ""):
        raise AuthError("No such offer.", 404)
    row = conn.execute(
        "SELECT offer_id, status FROM totr.song_offers WHERE offer_id = %s AND user_id = %s",
        (offer_id, user["user_id"]),
    ).fetchone()  # someone else's offer reads as missing
    if row is None:
        raise AuthError("No such offer.", 404)
    return row


def cancel_offer(user: dict, offer_id: str):
    with connect() as conn:
        _own_offer(conn, user, offer_id)
        conn.execute(
            "UPDATE totr.song_offer_items SET status = 'declined' WHERE offer_id = %s AND status IN ('offered', 'accepted')",
            (offer_id,),
        )
        conn.execute(
            "UPDATE totr.song_offers SET status = 'cancelled', answered_at = %s WHERE offer_id = %s AND status IN ('pending', 'answered')",
            (_now(), offer_id),
        )
    _audit(
        "offer_cancelled",
        user["username"],
        f"{user['username']} cancelled an offer",
        {"offer": offer_id[:8]},
        "cancelled",
    )


def _library_lookup():
    """Library songs by squashed title, from the search index (names only, no file reads). None if
    the library cannot be read right now: no flags then, offers still work."""
    try:
        import library_search
        from spiders.base import squash

        by_title = {}
        for song in library_search.get_index()["songs"]:
            by_title.setdefault(song["key"], []).append(song)
        return by_title, squash
    except Exception:
        return None


def _already_here(item, lookup):
    """How much an offered song looks like one the library already holds: {level: 'likely', ...}
    when the title and the artist match (case, spacing and punctuation ignored), {level: 'title',
    ...} when only the title does, or None. A hint for the person deciding, never a block: they may
    want a different recording or file."""
    by_title, squash = lookup
    found = by_title.get(squash(item["title"]))
    if not found:
        return None
    artist = squash(item["artist"])
    pick, level = found[0], "title"
    if artist:
        for song in found:
            names = [squash(n) for n in str(song.get("artists") or "").split(", ")] + [
                squash(song.get("artists") or "")
            ]
            if artist in names:
                pick, level = song, "likely"
                break
    return {
        "level": level,
        "track_id": pick["track_id"],
        "title": pick["title"],
        "artists": pick.get("artists") or "",
        "album": pick.get("album") or "",
    }


def pending_offers() -> list[dict]:
    """Offers waiting for an answer, oldest first, and answered ones from the last days (so a person
    sees what happened). Songs in a waiting offer that look like ones the library already has carry
    `library` (see _already_here)."""
    with connect() as conn:
        _purge(conn, _now())
        rows = conn.execute(
            "SELECT o.offer_id, o.request_id, o.note, o.status, o.created_at, o.answered_at, o.answered_by_name, u.username FROM totr.song_offers o "
            "JOIN totr.users u ON u.user_id = o.user_id WHERE o.status <> 'cancelled' ORDER BY (o.status = 'pending') DESC, o.created_at"
        ).fetchall()
        out = [{**_offer_view(conn, r), "from": r["username"]} for r in rows]
    lookup = _library_lookup() if any(o["status"] == "pending" for o in out) else None
    if lookup:
        for o in out:
            if o["status"] == "pending":
                for it in o["items"]:
                    hint = _already_here(it, lookup)
                    if hint:
                        it["library"] = hint
    return out


def answer(actor: dict, offer_id: str, accept) -> dict:
    """Takes the songs in `accept` (item numbers) and declines the rest. Only a waiting offer can be
    answered, once."""
    if not ID.match(offer_id or ""):
        raise AuthError("No such offer.", 404)
    if not isinstance(accept, list) or not all(
        isinstance(n, int) and not isinstance(n, bool) for n in accept
    ):
        raise AuthError("Choose which songs to take.", 422)
    now = _now()
    with connect() as conn:
        row = conn.execute(
            "SELECT o.offer_id, u.username FROM totr.song_offers o JOIN totr.users u ON u.user_id = o.user_id WHERE o.offer_id = %s AND o.status = 'pending'",
            (offer_id,),
        ).fetchone()
        if row is None:
            raise AuthError("That offer is gone or was already answered.", 404)
        have = {
            r["item_no"]
            for r in conn.execute(
                "SELECT item_no FROM totr.song_offer_items WHERE offer_id = %s", (offer_id,)
            ).fetchall()
        }
        if not set(accept) <= have:
            raise AuthError("Some of those songs are not in the offer.", 422)
        for n in have:
            conn.execute(
                "UPDATE totr.song_offer_items SET status = %s WHERE offer_id = %s AND item_no = %s",
                ("accepted" if n in accept else "declined", offer_id, n),
            )
        conn.execute(
            "UPDATE totr.song_offers SET status = 'answered', answered_at = %s, answered_by = %s, answered_by_name = %s WHERE offer_id = %s",
            (now, actor["user_id"], actor["username"], offer_id),
        )
    _audit(
        "offer_answered",
        actor["username"],
        f"{actor['username']} took {len(accept)} of {len(have)} song(s) offered by {row['username']}",
        {
            "offer": offer_id[:8],
            "from": row["username"],
            "taken": len(accept),
            "offered": len(have),
        },
        "accepted" if accept else "declined",
    )
    return {"taken": len(accept), "declined": len(have) - len(accept)}


def ask_person(actor: dict, username: str, note) -> dict:
    note = _text(note, 300, "note")
    now = _now()
    with connect() as conn:
        _purge(conn, now)
        target = conn.execute(
            "SELECT user_id, username FROM totr.users WHERE lower(username) = lower(%s) AND disabled = false",
            (str(username or "").strip(),),
        ).fetchone()
        if target is None:
            raise AuthError("There is no such person here.", 404)
        if target["user_id"] == actor["user_id"]:
            raise AuthError("You cannot ask yourself.", 409)
        if (
            conn.execute(
                "SELECT count(*) AS n FROM totr.song_requests WHERE user_id = %s AND status = 'pending'",
                (target["user_id"],),
            ).fetchone()["n"]
            >= MAX_PENDING_REQUESTS
        ):
            raise AuthError("That person already has requests waiting.", 429)
        request_id = secrets.token_hex(16)
        conn.execute(
            "INSERT INTO totr.song_requests (request_id, user_id, asked_by, asked_by_name, note, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (request_id, target["user_id"], actor["user_id"], actor["username"], note, now),
        )
    _audit(
        "songs_asked",
        actor["username"],
        f"{actor['username']} asked {target['username']} for their saved songs",
        {"request": request_id[:8], "person": target["username"]},
        "waiting",
    )
    return {"request_id": request_id}


def requests_for_admin() -> list[dict]:
    with connect() as conn:
        _purge(conn, _now())
        rows = conn.execute(
            "SELECT r.request_id, r.note, r.status, r.asked_by_name, r.created_at, r.answered_at, u.username FROM totr.song_requests r JOIN totr.users u ON u.user_id = r.user_id "
            "WHERE r.status <> 'cancelled' ORDER BY r.created_at DESC"
        ).fetchall()
        return [
            {
                "request_id": r["request_id"],
                "person": r["username"],
                "note": r["note"],
                "status": r["status"],
                "asked_by": r["asked_by_name"],
                "created_at": _iso(r["created_at"]),
                "answered_at": _iso(r["answered_at"]),
            }
            for r in rows
        ]


def cancel_request(actor: dict, request_id: str):
    if not ID.match(request_id or ""):
        raise AuthError("No such request.", 404)
    with connect() as conn:
        if (
            conn.execute(
                "UPDATE totr.song_requests SET status = 'cancelled', answered_at = %s WHERE request_id = %s AND status = 'pending'",
                (_now(), request_id),
            ).rowcount
            != 1
        ):
            raise AuthError("That request is gone or was already answered.", 404)
    _audit(
        "songs_ask_cancelled",
        actor["username"],
        f"{actor['username']} withdrew a request for songs",
        {"request": request_id[:8]},
        "cancelled",
    )


def my_requests(user: dict) -> list[dict]:
    with connect() as conn:
        _purge(conn, _now())
        rows = conn.execute(
            "SELECT request_id, note, asked_by_name, created_at FROM totr.song_requests WHERE user_id = %s AND status = 'pending' ORDER BY created_at",
            (user["user_id"],),
        ).fetchall()
        return [
            {
                "request_id": r["request_id"],
                "note": r["note"],
                "asked_by": r["asked_by_name"],
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ]


def _own_request(conn, user, request_id):
    if not ID.match(request_id or ""):
        raise AuthError("No such request.", 404)
    row = conn.execute(
        "SELECT request_id, asked_by, asked_by_name FROM totr.song_requests WHERE request_id = %s AND user_id = %s AND status = 'pending'",
        (request_id, user["user_id"]),
    ).fetchone()
    if row is None:
        raise AuthError("That request is gone or was already answered.", 404)
    return row


def accept_request(user: dict, request_id: str, items) -> dict:
    """The person says yes to an admin's ask and lists what they will share. The admin asked, so
    these songs are accepted at once (the person's tap is the consent)."""
    clean = _clean_items(items)
    now = _now()
    with connect() as conn:
        req = _own_request(conn, user, request_id)
        asker = {"user_id": req["asked_by"], "name": req["asked_by_name"]}
        offer_id = _insert_offer(
            conn, user["user_id"], "", clean, now, request_id=request_id, accepted_by=asker
        )
        conn.execute(
            "UPDATE totr.song_requests SET status = 'accepted', answered_at = %s, offer_id = %s WHERE request_id = %s",
            (now, offer_id, request_id),
        )
    _audit(
        "songs_ask_accepted",
        user["username"],
        f"{user['username']} agreed to share {len(clean)} song(s) asked for by {asker['name']}",
        {
            "request": request_id[:8],
            "offer": offer_id[:8],
            "songs": len(clean),
            "asked_by": asker["name"],
        },
        "accepted",
    )
    return {"offer_id": offer_id, "songs": len(clean)}


def decline_request(user: dict, request_id: str):
    with connect() as conn:
        req = _own_request(conn, user, request_id)
        conn.execute(
            "UPDATE totr.song_requests SET status = 'declined', answered_at = %s WHERE request_id = %s",
            (_now(), request_id),
        )
    _audit(
        "songs_ask_declined",
        user["username"],
        f"{user['username']} declined to share songs asked for by {req['asked_by_name']}",
        {"request": request_id[:8]},
        "declined",
    )


def _free_bytes() -> int:
    """Free space on the disk the music goes to (looked up from the nearest folder that exists)."""
    folder = pathlib.Path(uploader.MUSIC_ROOT)
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    return shutil.disk_usage(folder).free


def begin_upload(user: dict, offer_id: str, item_no: int) -> dict:
    """Checks that this person may send this file now, and says how big it must be. The file is
    taken only for a song someone accepted."""
    with connect() as conn:
        _own_offer(conn, user, offer_id)
        item = conn.execute(
            "SELECT item_no, title, artist, album, ext, size, sha256, status FROM totr.song_offer_items WHERE offer_id = %s AND item_no = %s",
            (offer_id, item_no),
        ).fetchone()
        if item is None:
            raise AuthError("No such song in that offer.", 404)
        if (
            item["status"] in ("accepted", "failed")
            and _free_bytes() - item["size"] < KEEP_FREE_BYTES
        ):
            raise AuthError(
                "The server is almost out of disk space, so it cannot take this song now. Tell whoever runs the server.",
                507,
            )
        if item["status"] not in ("accepted", "failed"):
            raise AuthError(
                {
                    "offered": "Nobody has accepted that song yet.",
                    "declined": "That song was not accepted.",
                    "uploaded": "That song is already here.",
                    "receiving": "That song is being received right now.",
                }.get(item["status"], "That song cannot be sent."),
                409,
            )
        if (
            conn.execute(
                "UPDATE totr.song_offer_items SET status = 'receiving', error = '' WHERE offer_id = %s AND item_no = %s AND status IN ('accepted', 'failed')",
                (offer_id, item_no),
            ).rowcount
            != 1
        ):
            raise AuthError("That song is being received right now.", 409)
        return dict(item)


def _mark(offer_id, item_no, status, error=""):
    with connect() as conn:
        conn.execute(
            "UPDATE totr.song_offer_items SET status = %s, error = %s WHERE offer_id = %s AND item_no = %s",
            (status, error[:300], offer_id, item_no),
        )


def release_upload(offer_id: str, item_no: int, why: str):
    """The transfer did not complete (cut off, wrong size): the song goes back to 'failed' so the
    owner can try again."""
    _mark(offer_id, item_no, "failed", why)


def finish_upload(user: dict, offer_id: str, item: dict, path: pathlib.Path) -> dict:
    """The file has arrived at `path` with the right size. Checks the checksum and that it reads as
    audio, then puts it in the library."""
    item_no = item["item_no"]

    def fail(message):
        _mark(offer_id, item_no, "failed", message)
        _audit(
            "offer_song_failed",
            user["username"],
            f"{user['username']}'s song '{item['title']}' was not added: {message}",
            {"offer": offer_id[:8], "song": item_no},
            "failed",
        )
        raise AuthError(message, 422)

    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != item["sha256"]:
        fail("The file that arrived is not the one that was offered (the checksum differs).")
    import mutagen

    try:
        probe = mutagen.File(path)
    except Exception:
        probe = None
    if probe is None or not getattr(probe, "info", None):
        fail("That file does not read as a song.")
    tags = uploader.read_tags(path)
    artist = tags["artist"] or item["artist"] or "Unknown"
    album = tags["album"] or item["album"] or ""
    filename = uploader.safe_name(f"{item['title']}") + item["ext"]
    try:
        with open(
            path, "rb"
        ) as song:  # the open file, not its bytes: a 300 MB song is copied in pieces
            uploader.save_album(
                artist,
                album,
                tags["year"],
                [(filename, song)],
                None,
                source="offer",
                extra={"offered_by": user["username"]},
            )
    except FileExistsError:
        fail("A song with that name is already in the library.")
    except Exception:
        log.exception("saving an offered song failed")
        fail("The song could not be added. The server logged why.")
    _mark(offer_id, item_no, "uploaded")
    _audit(
        "offer_song_added",
        user["username"],
        f"'{item['title']}' from {user['username']} was added to the library",
        {"offer": offer_id[:8], "song": item_no, "artist": artist},
        "added",
    )
    return {"ok": True}


def reset_interrupted():
    """At start-up: a song that was 'being received' when the server stopped was cut off. It can be
    sent again."""
    try:
        with connect() as conn:
            conn.execute(
                "UPDATE totr.song_offer_items SET status = 'failed', error = 'The transfer was interrupted. Send it again.' WHERE status = 'receiving'"
            )
    except Exception:
        pass  # the tables may not exist yet (sql/song_offers.sql not run)
