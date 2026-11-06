"""Join requests: someone finds this server from their TOTR app and asks to join; an admin answers.

Nothing is created until an admin approves. The asking app makes up a one-time invite token itself and sends only its SHA-256, so the server
never stores or sends a secret: approving writes that hash as an ordinary invite, and the app (which still holds the token) opens /invite/<token>
to pick a username and password. Each request is signed with the app install's own key: Ed25519 (desktop) or ECDSA on the P-256 curve (Android, whose key store has no Ed25519). That proves "the same install as before" and stops
casual fakes; it cannot prove the app is unmodified, and the admin page says so.

The request body is {"payload": "<JSON text>", "key": "<base64 public key>", "sig": "<base64 signature>"}; the signature covers
b"totr-join-request\\n" + the exact payload text. The payload is {"v":1,"name","note","platform","app_version","history","invite_hash","ts"}.
"""

import base64
import binascii
import hashlib
import json
import math
import re
import secrets
from datetime import timedelta

import app_settings

from . import INVITE_DAYS, service, store
from .limiter import Limiter
from .service import AuthError

PREFIX = b"totr-join-request\n"
MAX_PAYLOAD = 4000
CLOCK_SLACK = 600  # seconds the app's clock may differ from ours
ANSWERED_KEEP_DAYS = (
    7  # answered requests are kept this long (so the app can read the answer), then purged
)
PENDING_KEEP_DAYS = 14
MAX_PENDING = 200  # a flood stops here instead of filling the database
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HISTORY_COUNTS = ("plays", "mixtapes", "songs")

by_ip = Limiter(5, 3600)  # requests sent, per address per hour
by_device = Limiter(3, 3600)  # ... and per app install
polls = Limiter(120, 600)  # status checks per address

CLOSED = "This server is not taking join requests right now."


def is_open() -> bool:
    return app_settings.get_join_open()


def _text(value, limit, what, required=False) -> str:
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    value = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    if required and not value:
        raise AuthError(f"Please give {what}.", 422)
    if len(value) > limit:
        raise AuthError(
            f"{what[0].upper() + what[1:]} is too long (at most {limit} characters).", 422
        )
    return value


def _history(raw) -> str:
    """Counts the app reported about the person's home server. Only whitelisted keys, only plain
    numbers and a date; anything else is dropped."""
    if not isinstance(raw, dict):
        return ""
    out = {}
    if isinstance(raw.get("since"), str) and DAY.match(raw["since"]):
        out["since"] = raw["since"]
    for k in HISTORY_COUNTS:
        v = raw.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10**9:
            out[k] = v
    return json.dumps(out, separators=(",", ":")) if out else ""


def fingerprint(key_b64: str) -> str:
    """A short readable name for an install's key: 4 groups of 4 hex characters."""
    h = hashlib.sha256(base64.b64decode(key_b64)).hexdigest()[:16]
    return "-".join(h[i : i + 4] for i in range(0, 16, 4))


def _verify(body: dict) -> tuple[dict, str]:
    """The signed payload and the install's identity (an Ed25519 key as sent; a P-256 key as the
    base64 of its SHA-256), or an AuthError. Wrong shape, wrong key size and a bad signature all
    read the same to the sender."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    bad = AuthError("This request could not be checked. Update the app and try again.", 400)
    payload, key, sig = body.get("payload"), body.get("key"), body.get("sig")
    if (
        not (isinstance(payload, str) and isinstance(key, str) and isinstance(sig, str))
        or len(payload) > MAX_PAYLOAD
    ):
        raise bad
    try:
        raw_key, raw_sig = (
            base64.b64decode(key, validate=True),
            base64.b64decode(sig, validate=True),
        )
        message = PREFIX + payload.encode("utf-8")
        # the algorithm follows the key's size, never a field the sender picks
        if len(raw_key) == 32 and len(raw_sig) == 64:
            Ed25519PublicKey.from_public_bytes(raw_key).verify(raw_sig, message)
            ident = key
        # an uncompressed P-256 point and a DER signature (what Android's key store makes)
        elif len(raw_key) == 65:
            # the library refuses a point off the curve or not starting 0x04, and any signature that
            # is not strict DER or not this key's
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw_key).verify(
                raw_sig, message, ec.ECDSA(hashes.SHA256())
            )
            # stored as its SHA-256 (also 44 characters): only ever compared and shown as a short
            # code
            ident = base64.b64encode(hashlib.sha256(raw_key).digest()).decode()
        else:
            raise bad

        def no_constants(name):  # NaN and Infinity are not numbers a clock can be checked against
            raise ValueError(name)

        data = json.loads(payload, parse_constant=no_constants)
    except (binascii.Error, ValueError, RecursionError, InvalidSignature):
        raise bad
    if not isinstance(data, dict) or data.get("v") != 1:
        raise bad
    return data, ident


def _purge(conn, now):
    conn.execute(
        "DELETE FROM totr.join_requests WHERE status <> 'pending' AND answered_at < %s",
        (now - timedelta(days=ANSWERED_KEEP_DAYS),),
    )
    conn.execute(
        "DELETE FROM totr.join_requests WHERE status = 'pending' AND created_at < %s",
        (now - timedelta(days=PENDING_KEEP_DAYS),),
    )


def submit(ip: str, body) -> dict:
    """Takes a signed request. Returns {"request_id"}; the asking app then polls status()."""
    if not is_open():
        raise AuthError(CLOSED, 403)
    if not isinstance(body, dict):
        raise AuthError("This request could not be checked. Update the app and try again.", 400)
    wait = max(by_ip.retry_after(ip), 0)
    if wait:
        raise AuthError("Too many requests from here. Try again later.", 429, wait)
    data, key = _verify(body)
    wait = by_device.retry_after(key)
    if wait:
        raise AuthError("This install has asked too often. Try again later.", 429, wait)
    now = service._now()
    ts = data.get("ts")
    if (
        not (isinstance(ts, (int, float)) and not isinstance(ts, bool) and math.isfinite(ts))
        or abs(now.timestamp() - ts) > CLOCK_SLACK
    ):
        raise AuthError("Your device's clock is off. Fix the time and try again.", 400)
    invite_hash = data.get("invite_hash")
    if not (isinstance(invite_hash, str) and HEX64.match(invite_hash)):
        raise AuthError("This request could not be checked. Update the app and try again.", 400)
    name = _text(data.get("name"), 40, "a name", required=True)
    note = _text(data.get("note"), 300, "the note")
    platform, version = (
        _text(data.get("platform"), 40, "the platform"),
        _text(data.get("app_version"), 20, "the app version"),
    )
    by_ip.record(ip)
    by_device.record(key)
    request_id = secrets.token_hex(16)
    with service.connect() as conn:
        _purge(conn, now)
        if (
            conn.execute(
                "SELECT count(*) AS n FROM totr.join_requests WHERE status = 'pending'"
            ).fetchone()["n"]
            >= MAX_PENDING
        ):
            raise AuthError("This server has too many waiting requests. Try again later.", 503)
        if conn.execute(
            "SELECT 1 FROM totr.join_requests WHERE device_key = %s AND status = 'pending'", (key,)
        ).fetchone():
            raise AuthError("This install already has a request waiting.", 409)
        if (
            conn.execute(
                "SELECT 1 FROM totr.join_requests WHERE invite_hash = %s", (invite_hash,)
            ).fetchone()
            or conn.execute(
                "SELECT 1 FROM totr.invites WHERE token_hash = %s", (invite_hash,)
            ).fetchone()
        ):
            raise AuthError("This request could not be checked. Update the app and try again.", 400)
        conn.execute(
            "INSERT INTO totr.join_requests (request_id, display_name, note, device_key, platform, app_version, history, invite_hash, ip, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                request_id,
                name,
                note,
                key,
                platform,
                version,
                _history(data.get("history")),
                invite_hash,
                ip,
                now,
            ),
        )
    return {"request_id": request_id}


def status(ip: str, request_id: str) -> dict:
    """pending / approved / declined. Declined says nothing more; the row disappears a week later
    and the answer then reads as 404."""
    wait = polls.retry_after(ip)
    if wait:
        raise AuthError("Too many checks. Try again in a minute.", 429, wait)
    polls.record(ip)
    if not ID_RE.match(request_id or ""):
        raise AuthError("No such request.", 404)
    with service.connect() as conn:
        row = conn.execute(
            "SELECT status FROM totr.join_requests WHERE request_id = %s", (request_id,)
        ).fetchone()
    if row is None:
        raise AuthError("No such request. It may have been answered a while ago.", 404)
    return {"status": row["status"]}


def withdraw(request_id: str):
    """The asking person changed their mind. Only a pending request can be withdrawn; anyone who
    knows the (secret, 128-bit) id may."""
    if not ID_RE.match(request_id or ""):
        raise AuthError("No such request.", 404)
    with service.connect() as conn:
        conn.execute(
            "DELETE FROM totr.join_requests WHERE request_id = %s AND status = 'pending'",
            (request_id,),
        )


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def pending() -> list[dict]:
    """Waiting requests for the admin, oldest first, with the signals an admin needs to decide: whether
    this install asked before, and what it reported."""
    now = service._now()
    with service.connect() as conn:
        _purge(conn, now)
        rows = conn.execute(
            "SELECT request_id, display_name, note, device_key, platform, app_version, history, ip, created_at "
            "FROM totr.join_requests WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
        out = []
        for r in rows:
            before = conn.execute(
                "SELECT status, count(*) AS n FROM totr.join_requests WHERE device_key = %s AND request_id <> %s AND status <> 'pending' "
                "GROUP BY status",
                (r["device_key"], r["request_id"]),
            ).fetchall()
            try:
                history = json.loads(r["history"]) if r["history"] else None
            except ValueError:
                history = None
            out.append(
                {
                    "request_id": r["request_id"],
                    "name": r["display_name"],
                    "note": r["note"],
                    "install": fingerprint(r["device_key"]),
                    "platform": r["platform"],
                    "app_version": r["app_version"],
                    "history": history,
                    "address": r["ip"],
                    "asked_before": {x["status"]: x["n"] for x in before},
                    "created_at": _iso(r["created_at"]),
                }
            )
    return out


def _pending_row(conn, request_id):
    if not ID_RE.match(request_id or ""):
        raise AuthError("That request is gone.", 404)
    row = conn.execute(
        "SELECT request_id, invite_hash FROM totr.join_requests WHERE request_id = %s AND status = 'pending'",
        (request_id,),
    ).fetchone()
    if row is None:
        raise AuthError("That request is gone or was already answered.", 404)
    return row


def approve(actor: dict, request_id: str, granted=()):
    """Turns the request into a member invite carrying the chosen permissions. A join request can
    never make an admin."""
    text = service._perm_text(granted)
    if text:
        service._require_columns()
        service._check_can_change(actor, frozenset(), service.perms.from_text(text))
    now = service._now()
    with service.connect() as conn:
        row = _pending_row(conn, request_id)
        try:
            store.create_invite(
                conn,
                row["invite_hash"],
                "member",
                actor["user_id"],
                now,
                now + timedelta(days=INVITE_DAYS),
                text,
                service.has_permission_columns(),
            )
        except store.INTEGRITY_ERRORS:
            raise AuthError("That request cannot be approved (its invite already exists).", 409)
        conn.execute(
            "UPDATE totr.join_requests SET status = 'approved', answered_at = %s, answered_by = %s WHERE request_id = %s",
            (now, actor["user_id"], request_id),
        )
    service.log.info("join request %s approved by %s", request_id[:8], actor.get("username"))


def decline(actor: dict, request_id: str):
    now = service._now()
    with service.connect() as conn:
        _pending_row(conn, request_id)
        conn.execute(
            "UPDATE totr.join_requests SET status = 'declined', answered_at = %s, answered_by = %s WHERE request_id = %s",
            (now, actor["user_id"], request_id),
        )
    service.log.info("join request %s declined by %s", request_id[:8], actor.get("username"))
