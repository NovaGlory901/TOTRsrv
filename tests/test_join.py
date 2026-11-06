"""Join requests: a signed request from an app, the admin's answer, and what is (not) left behind.
Throwaway SQLite database with the real migration's table. No network."""

import base64
import hashlib
import json
import os
import pathlib
import secrets
import sys
import tempfile
import time

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
os.environ["DB_ENGINE"] = "postgres"
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["APP_SETTINGS"] = str(tmp / "reports" / "app_settings.json")
os.environ.pop("COOKIE_SECURE", None)
import sqlite_layout as layout
from check import check, finish

DB = tmp / "auth.sqlite"
layout.create(DB, layout.ACCOUNTS, layout.JOIN_REQUESTS)
for k in ("DB_AUTH_USER", "DB_AUTH_PASSWORD"):
    os.environ[k] = "x"
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

import discovery
import main
from auth import join, service

discovery.sync = lambda: "silent"  # this suite must not announce anything on the real network

service.connect = layout.shim(DB)
raw = lambda sql, params=(): [tuple(d.values()) for d in layout.raw(DB, sql, params)]
PW = "correct horse battery staple"
service.create_user("boss", PW, "admin")

anon = TestClient(main.app)
admin = TestClient(main.app)
assert admin.post("/api/auth/login", json={"username": "boss", "password": PW}).status_code == 200
AH = {"X-CSRF-Token": admin.get("/api/auth/me").json()["csrf"]}


class Install:
    """An app install: its own key, and a one-time invite token it keeps to itself."""

    def __init__(self):
        self.priv = Ed25519PrivateKey.generate()
        self.key = base64.b64encode(
            self.priv.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        ).decode()
        self.token = secrets.token_urlsafe(32)

    def body(self, name="Rae", note="hello", ts=None, **over):
        p = {
            "v": 1,
            "name": name,
            "note": note,
            "platform": "android",
            "app_version": "0.7.0",
            "history": {
                "since": "2024-01-02",
                "plays": 120,
                "mixtapes": 3,
                "songs": 900,
                "evil": "x",
            },
            "invite_hash": hashlib.sha256(self.token.encode()).hexdigest(),
            "ts": int(time.time()) if ts is None else ts,
            **over,
        }
        text = json.dumps(p)
        return {
            "payload": text,
            "key": self.key,
            "sig": base64.b64encode(self.priv.sign(join.PREFIX + text.encode())).decode(),
        }


# The numbers the server really runs with, read before any test resets them: the checks below must
# hold for THESE, not for values the test made up.
POLICY = (
    join.by_ip.limit,
    join.by_ip.window,
    join.by_device.limit,
    join.by_device.window,
    join.polls.limit,
    join.polls.window,
    join.MAX_PENDING,
    join.CLOCK_SLACK,
)


def fresh():
    join.by_ip.__init__(POLICY[0], POLICY[1])
    join.by_device.__init__(POLICY[2], POLICY[3])
    join.polls.__init__(POLICY[4], POLICY[5])


# closed by default
check(
    "the public info says closed until an admin opens it",
    anon.get("/api/join").json()["open"] is False,
)
r = anon.post("/api/join", json=Install().body())
check("a request to a closed server is refused (403)", r.status_code == 403, r.text)
check("...and leaves nothing", raw("SELECT count(*) FROM totr_join_requests")[0][0] == 0)
check("the admin endpoints need an admin", anon.get("/api/admin/join").status_code in (401, 403))
check(
    "opening needs the CSRF token",
    admin.post("/api/admin/join/open", json={"open": True}).status_code == 403,
)
r = admin.post("/api/admin/join/open", json={"open": True}, headers=AH)
check(
    "an admin opens joining",
    r.status_code == 200
    and r.json() == {"open": True, "announcing": "silent"}
    and anon.get("/api/join").json()["open"] is True,
    r.text,
)

# a good request
a = Install()
r = anon.post("/api/join", json=a.body())
check(
    "a signed request is accepted",
    r.status_code == 200 and len(r.json()["request_id"]) == 32,
    r.text,
)
rid = r.json()["request_id"]
check("the asker sees 'pending'", anon.get(f"/api/join/{rid}").json() == {"status": "pending"})
check(
    "no account and no invite exist yet",
    len(raw("SELECT user_id FROM totr_users")) == 1
    and len(raw("SELECT token_hash FROM totr_invites")) == 0,
)
q = admin.get("/api/admin/join").json()["requests"]
check(
    "the admin sees it with name, note, platform, version",
    len(q) == 1
    and q[0]["name"] == "Rae"
    and q[0]["note"] == "hello"
    and q[0]["platform"] == "android"
    and q[0]["app_version"] == "0.7.0",
    q,
)
check(
    "the reported history keeps only known counts",
    q[0]["history"] == {"since": "2024-01-02", "plays": 120, "mixtapes": 3, "songs": 900},
    q[0]["history"],
)
check(
    "the install is named by a short fingerprint of its key",
    q[0]["install"] == join.fingerprint(a.key) and len(q[0]["install"]) == 19,
)
check(
    "the page response never contains the invite hash or the key",
    a.key not in json.dumps(q)
    and hashlib.sha256(a.token.encode()).hexdigest() not in json.dumps(q),
)
check("one waiting request per install", anon.post("/api/join", json=a.body()).status_code == 409)

# forged and broken requests
fresh()
b = Install()
good = b.body()


def tamper(**kw):
    x = dict(good)
    x.update(kw)
    return x


check(
    "a changed payload fails the signature",
    anon.post("/api/join", json=tamper(payload=good["payload"].replace("Rae", "Eve"))).status_code
    == 400,
)
other = Install()
check(
    "someone else's key fails the signature",
    anon.post("/api/join", json=tamper(key=other.key)).status_code == 400,
)
check(
    "a short signature is refused",
    anon.post("/api/join", json=tamper(sig=base64.b64encode(b"x" * 10).decode())).status_code
    == 400,
)
check(
    "garbage in the key field is refused",
    anon.post("/api/join", json=tamper(key="!!!not base64!!!")).status_code == 400,
)
check(
    "a payload that is not JSON is refused",
    anon.post(
        "/api/join",
        json=(
            lambda t: {
                "payload": t,
                "key": b.key,
                "sig": base64.b64encode(b.priv.sign(join.PREFIX + t.encode())).decode(),
            }
        )("not json"),
    ).status_code
    == 400,
)
check(
    "a payload signed without our prefix is refused",
    anon.post(
        "/api/join",
        json=(
            lambda t: {
                "payload": t,
                "key": b.key,
                "sig": base64.b64encode(b.priv.sign(t.encode())).decode(),
            }
        )(json.dumps({"v": 1})),
    ).status_code
    == 400,
)
check(
    "an old request (replay) is refused",
    anon.post("/api/join", json=b.body(ts=int(time.time()) - 3600)).status_code == 400,
)
check(
    "a request from the far future is refused",
    anon.post("/api/join", json=b.body(ts=int(time.time()) + 3600)).status_code == 400,
)
check("an empty name is refused", anon.post("/api/join", json=b.body(name="  ")).status_code == 422)
check(
    "a 41-character name is refused",
    anon.post("/api/join", json=b.body(name="x" * 41)).status_code == 422,
)
check(
    "a note over 300 characters is refused",
    anon.post("/api/join", json=b.body(note="x" * 301)).status_code == 422,
)
for bad_ts in ("NaN", "Infinity", "-Infinity", "true", '"now"', "null"):
    t = b.body()
    p_ = t["payload"].replace(f'"ts": {json.loads(t["payload"])["ts"]}', f'"ts": {bad_ts}')
    forged = {
        "payload": p_,
        "key": b.key,
        "sig": base64.b64encode(b.priv.sign(join.PREFIX + p_.encode())).decode(),
    }
    check(
        f"a properly signed request with ts = {bad_ts} is refused (no clock check to skip)",
        anon.post("/api/join", json=forged).status_code == 400,
    )
# under the 4000-character limit, over what the JSON reader will nest
deep = "[" * 1900 + "]" * 1900
p_ = '{"v": 1, "x": ' + deep + "}"
check(
    "a signed payload nested thousands deep is refused cleanly, not a crash",
    anon.post(
        "/api/join",
        json={
            "payload": p_,
            "key": b.key,
            "sig": base64.b64encode(b.priv.sign(join.PREFIX + p_.encode())).decode(),
        },
    ).status_code
    == 400,
)
check(
    "a bad invite hash is refused",
    anon.post("/api/join", json=b.body(invite_hash="abc")).status_code == 400,
)
check("a missing body is refused", anon.post("/api/join", json={}).status_code == 422)
check(
    "a huge payload is refused",
    anon.post("/api/join", json={"payload": "x" * 5000, "key": b.key, "sig": "x"}).status_code
    == 422,
)
check(
    "control characters in the name are flattened",
    (
        lambda r: (
            r.status_code == 200
            and admin.get("/api/admin/join").json()["requests"][-1]["name"] == "Eve X"
        )
    )(anon.post("/api/join", json=b.body(name="Eve\n\x00X"))),
)
check(
    "a cross-site browser post is refused",
    anon.post(
        "/api/join", json=Install().body(), headers={"Origin": "https://evil.example"}
    ).status_code
    == 403,
)
check(
    "the same invite hash cannot be used twice",
    anon.post(
        "/api/join", json=(lambda i: (setattr(i, "token", b.token), i.body())[1])(Install())
    ).status_code
    == 400,
)

# declining leaves nothing
rid_b = [
    x["request_id"] for x in admin.get("/api/admin/join").json()["requests"] if x["name"] == "Eve X"
][0]
check(
    "declining needs the CSRF token",
    admin.post(f"/api/admin/join/{rid_b}/decline").status_code == 403,
)
r = admin.post(f"/api/admin/join/{rid_b}/decline", headers=AH)
check("an admin declines", r.status_code == 200, r.text)
check("the asker sees 'declined'", anon.get(f"/api/join/{rid_b}").json() == {"status": "declined"})
check(
    "a declined request makes no account and no invite",
    len(raw("SELECT user_id FROM totr_users")) == 1
    and len(raw("SELECT token_hash FROM totr_invites")) == 0,
)
check(
    "it cannot then be approved",
    admin.post(f"/api/admin/join/{rid_b}/approve", json={"permissions": []}, headers=AH).status_code
    == 404,
)

# approving
r = admin.post(f"/api/admin/join/{rid}/approve", json={"permissions": ["upload"]}, headers=AH)
check("an admin approves with a permission", r.status_code == 200, r.text)
check("the asker sees 'approved'", anon.get(f"/api/join/{rid}").json() == {"status": "approved"})
inv = raw("SELECT token_hash, role, permissions FROM totr_invites")
check(
    "approval wrote one member invite under the app's own hash",
    len(inv) == 1
    and inv[0][0] == hashlib.sha256(a.token.encode()).hexdigest()
    and inv[0][1] == "member"
    and inv[0][2] == "upload",
    inv,
)
r = TestClient(main.app).post(
    "/api/auth/accept-invite", json={"token": a.token, "username": "rae", "password": PW + "!"}
)
check("the app opens its own token and makes the account", r.status_code == 200, r.text)
check(
    "the new person is a member with the granted permission",
    raw("SELECT role, permissions FROM totr_users WHERE username = 'rae'")[0]
    == ("member", "upload"),
)
check(
    "an answered request is not in the waiting list",
    all(x["request_id"] != rid for x in admin.get("/api/admin/join").json()["requests"]),
)
check(
    "it cannot be approved twice",
    admin.post(f"/api/admin/join/{rid}/approve", json={"permissions": []}, headers=AH).status_code
    == 404,
)
check(
    "the id must look right",
    anon.get("/api/join/nope").status_code == 404
    and anon.get("/api/join/" + "0" * 32).status_code == 404,
)
check(
    "an unknown permission is refused",
    admin.post(
        f"/api/admin/join/{rid}/approve", json={"permissions": ["fly"]}, headers=AH
    ).status_code
    in (404, 422),
)

# a member cannot answer requests; a user-manager can
member = TestClient(main.app)
member.post("/api/auth/login", json={"username": "rae", "password": PW + "!"})
MH = {"X-CSRF-Token": member.get("/api/auth/me").json()["csrf"]}
check(
    "a plain member cannot list or open joining",
    member.get("/api/admin/join").status_code == 403
    and member.post("/api/admin/join/open", json={"open": False}, headers=MH).status_code == 403,
)

# a person who may manage users can only grant what they hold
service.create_user("helper", PW + "?", "member", ["users"])
helper = TestClient(main.app)
helper.post("/api/auth/login", json={"username": "helper", "password": PW + "?"})
HH = {"X-CSRF-Token": helper.get("/api/auth/me").json()["csrf"]}
fresh()
h = Install()
rid_h = anon.post("/api/join", json=h.body(name="Hal")).json()["request_id"]
check(
    "a user-manager sees the requests", len(helper.get("/api/admin/join").json()["requests"]) >= 1
)
check(
    "...but cannot hand out a permission they do not hold",
    helper.post(
        f"/api/admin/join/{rid_h}/approve", json={"permissions": ["upload"]}, headers=HH
    ).status_code
    == 403,
)
check(
    "...and the request is still waiting after that",
    any(x["request_id"] == rid_h for x in admin.get("/api/admin/join").json()["requests"]),
)
check(
    "...they can approve with what they hold",
    helper.post(
        f"/api/admin/join/{rid_h}/approve", json={"permissions": ["users"]}, headers=HH
    ).status_code
    == 200,
)
check(
    "approval never makes an admin",
    raw(
        "SELECT role FROM totr_invites WHERE token_hash = ?",
        (hashlib.sha256(h.token.encode()).hexdigest(),),
    )[0][0]
    == "member",
)

# 'asked before'
fresh()
b.token = secrets.token_urlsafe(32)  # a new request always carries a new one-time token
r = anon.post("/api/join", json=b.body(name="Eve again"))
check("the same install can ask again after an answer", r.status_code == 200, r.text)
q = [x for x in admin.get("/api/admin/join").json()["requests"] if x["name"] == "Eve again"][0]
check("the admin is told this install was declined before", q["asked_before"] == {"declined": 1}, q)

# the counts a person may share
import db
from mixtapes import web as mixtapes_web

check("the summary needs a sign-in", anon.get("/api/me/summary").status_code == 401)
summ = member.get("/api/me/summary").json()
check(
    "the summary has the account's start day and nothing invented (no play counts)",
    set(summ) <= {"since", "mixtapes", "songs"}
    and len(summ["since"]) == 10
    and "plays" not in summ,
    summ,
)
layout.create(DB, layout.MIXTAPES)
mixtapes_web.connect = layout.shim(DB)
db.mixtape_enabled = lambda: True
raw(
    "INSERT INTO totr_mixtapes (user_id, name) VALUES ((SELECT user_id FROM totr_users WHERE username = 'rae'), 'one'), ((SELECT user_id FROM totr_users WHERE username = 'rae'), 'two'), (1, 'boss mix')"
)
check(
    "it counts only that person's own mixtapes",
    member.get("/api/me/summary").json().get("mixtapes") == 2,
)

# withdraw
rid_c = r.json()["request_id"]
check(
    "the asker can withdraw a waiting request",
    anon.post(f"/api/join/{rid_c}/withdraw", json={}).status_code == 200
    and anon.get(f"/api/join/{rid_c}").status_code == 404,
)

# P-256 installs (Android: its key store has no Ed25519)
fresh()
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)


class P256Install(Install):
    """An Android install: an EC P-256 key, an uncompressed public point, and DER signatures like
    the platform key store makes."""

    def __init__(self, curve=None):
        self.priv = ec.generate_private_key(curve or ec.SECP256R1())
        self.key = base64.b64encode(
            self.priv.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        ).decode()
        self.token = secrets.token_urlsafe(32)

    def body(self, name="Pia", note="hello", ts=None, **over):
        p = {
            "v": 1,
            "name": name,
            "note": note,
            "platform": "android",
            "app_version": "0.8.0",
            "history": {},
            "invite_hash": hashlib.sha256(self.token.encode()).hexdigest(),
            "ts": int(time.time()) if ts is None else ts,
            **over,
        }
        text = json.dumps(p)
        return {
            "payload": text,
            "key": self.key,
            "sig": base64.b64encode(
                self.priv.sign(join.PREFIX + text.encode(), ec.ECDSA(hashes.SHA256()))
            ).decode(),
        }


def p256_with(sig_bytes=None, key_bytes=None, body=None):
    x = dict(body)
    if sig_bytes is not None:
        x["sig"] = base64.b64encode(sig_bytes).decode()
    if key_bytes is not None:
        x["key"] = base64.b64encode(key_bytes).decode()
    return x


pa = P256Install()
pgood = pa.body()
r = anon.post("/api/join", json=pgood)
check(
    "a P-256 signed request is accepted",
    r.status_code == 200 and len(r.json()["request_id"]) == 32,
    r.text,
)
q = [x for x in admin.get("/api/admin/join").json()["requests"] if x["name"] == "Pia"]
check(
    "the admin sees it, named by a fingerprint of the key bytes",
    len(q) == 1
    and q[0]["install"]
    == join.fingerprint(
        base64.b64encode(hashlib.sha256(base64.b64decode(pa.key)).digest()).decode()
    )
    and len(q[0]["install"]) == 19,
    q,
)
check("the page response never contains the P-256 key", pa.key not in json.dumps(q))
anon.post(f"/api/join/{r.json()['request_id']}/withdraw", json={})
fresh()
pb = P256Install()
good = pb.body()
raw_sig = base64.b64decode(good["sig"])
rr, ss = decode_dss_signature(raw_sig)
raw_key = base64.b64decode(good["key"])
check("the test signature is the canonical DER form", encode_dss_signature(rr, ss) == raw_sig)
check(
    "a changed P-256 payload fails the signature",
    anon.post(
        "/api/join", json={**good, "payload": good["payload"].replace("Pia", "Eve")}
    ).status_code
    == 400,
)
check(
    "someone else's P-256 key fails the signature",
    anon.post(
        "/api/join", json=p256_with(key_bytes=base64.b64decode(P256Install().key), body=good)
    ).status_code
    == 400,
)
off = bytes([4]) + (1).to_bytes(32, "big") + (1).to_bytes(32, "big")
check(
    "a point that is not on the curve is refused",
    anon.post("/api/join", json=p256_with(key_bytes=off, body=good)).status_code == 400,
)
check(
    "a 65-byte key that does not start with 0x04 is refused",
    anon.post(
        "/api/join", json=p256_with(key_bytes=bytes([2]) + raw_key[1:], body=good)
    ).status_code
    == 400,
)
check(
    "a compressed P-256 point (33 bytes) is refused",
    anon.post(
        "/api/join",
        json=p256_with(
            key_bytes=pb.priv.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint
            ),
            body=good,
        ),
    ).status_code
    == 400,
)
check(
    "a 66-byte key is refused",
    anon.post("/api/join", json=p256_with(key_bytes=raw_key + b"\x00", body=good)).status_code
    == 400,
)
check(
    "a P-384 key is refused (too long for the request, and not an accepted size)",
    anon.post("/api/join", json=P256Install(ec.SECP384R1()).body()).status_code in (400, 422),
)
check(
    "a raw r||s signature (not DER) is refused",
    anon.post(
        "/api/join",
        json=p256_with(sig_bytes=rr.to_bytes(32, "big") + ss.to_bytes(32, "big"), body=good),
    ).status_code
    == 400,
)
long_form = b"\x30\x81" + bytes([raw_sig[1]]) + raw_sig[2:]
check(
    "a DER signature in a non-canonical (long-form length) encoding is refused",
    anon.post("/api/join", json=p256_with(sig_bytes=long_form, body=good)).status_code == 400,
)
check(
    "a signature over 72 bytes is refused",
    anon.post("/api/join", json=p256_with(sig_bytes=raw_sig + b"\x00" * 8, body=good)).status_code
    == 400,
)
check(
    "an empty signature is refused",
    anon.post("/api/join", json=p256_with(sig_bytes=b"", body=good)).status_code == 400,
)
check(
    "a P-256 key with a 64-byte Ed25519-sized signature is refused (the key's size picks the algorithm)",
    anon.post("/api/join", json=p256_with(sig_bytes=b"\x01" * 64, body=good)).status_code == 400,
)
ed = Install()
check(
    "an Ed25519 key with a DER-sized signature is refused",
    anon.post("/api/join", json={**ed.body(), "sig": good["sig"]}).status_code == 400,
)
check(
    "a P-256 signature made over the payload without our prefix is refused",
    anon.post(
        "/api/join",
        json=(
            lambda t: {
                "payload": t,
                "key": pb.key,
                "sig": base64.b64encode(
                    pb.priv.sign(t.encode(), ec.ECDSA(hashes.SHA256()))
                ).decode(),
            }
        )(good["payload"]),
    ).status_code
    == 400,
)
check(
    "a P-256 signature made with SHA-384 is refused",
    anon.post(
        "/api/join",
        json=(
            lambda t: {
                "payload": t,
                "key": pb.key,
                "sig": base64.b64encode(
                    pb.priv.sign(join.PREFIX + t.encode(), ec.ECDSA(hashes.SHA384()))
                ).decode(),
            }
        )(good["payload"]),
    ).status_code
    == 400,
)
check(
    "an old P-256 request (replay) is refused",
    anon.post("/api/join", json=pb.body(ts=int(time.time()) - 3600)).status_code == 400,
)
check(
    "the Ed25519 path still works next to it",
    anon.post("/api/join", json=Install().body(name="Ed")).status_code == 200,
)
raw("DELETE FROM totr_join_requests")

# limits
check(
    "the built-in limits: 5 requests per address and 3 per install per hour, 120 status checks per 10 minutes, 200 waiting at most, a clock within 10 minutes",
    POLICY == (5, 3600, 3, 3600, 120, 600, 200, 600),
    POLICY,
)
fresh()
codes = [anon.post("/api/join", json=Install().body()).status_code for _ in range(7)]
check(
    "one address can send only 5 requests an hour",
    codes[:5] == [200] * 5 and codes[5:] == [429, 429],
    codes,
)
fresh()
d = Install()
codes = []
for _ in range(4):
    d.token = secrets.token_urlsafe(32)
    resp = anon.post("/api/join", json=d.body())
    codes.append(resp.status_code)
    if resp.status_code == 200:
        anon.post(f"/api/join/{resp.json()['request_id']}/withdraw", json={})
check("one install can send only 3 requests an hour", codes == [200, 200, 200, 429], codes)
fresh()
raw("DELETE FROM totr_join_requests")
for _ in range(min(join.MAX_PENDING, 300)):
    raw(
        "INSERT INTO totr_join_requests (request_id, display_name, device_key, invite_hash, created_at) VALUES (?, 'x', ?, ?, ?)",
        (secrets.token_hex(16), "k" * 44, secrets.token_hex(32), "2999-01-01T00:00:00+00:00"),
    )
check(
    "a flood stops at the pending cap",
    anon.post("/api/join", json=Install().body()).status_code == 503,
)
fresh()
polls_codes = {anon.get("/api/join/" + "1" * 32).status_code for _ in range(125)}
check("status checks are rate limited", 429 in polls_codes)

# purge
fresh()
raw("DELETE FROM totr_join_requests")
raw(
    "INSERT INTO totr_join_requests (request_id, display_name, device_key, invite_hash, status, created_at, answered_at) VALUES (?, 'old', ?, ?, 'declined', '2020-01-01T00:00:00+00:00', '2020-01-02T00:00:00+00:00')",
    ("a" * 32, "k" * 44, "b" * 64),
)
raw(
    "INSERT INTO totr_join_requests (request_id, display_name, device_key, invite_hash, status, created_at) VALUES (?, 'stale', ?, ?, 'pending', '2020-01-01T00:00:00+00:00')",
    ("c" * 32, "j" * 44, "d" * 64),
)
admin.get("/api/admin/join")
check(
    "old answered and stale pending requests are purged",
    raw("SELECT count(*) FROM totr_join_requests")[0][0] == 0,
)

# the old database: no table yet
raw("DROP TABLE totr_join_requests")
check(
    "before the upgrade, listing says it cannot (a clean 503, no crash)",
    admin.get("/api/admin/join").status_code == 503,
)
check(
    "before the upgrade, an app request gets a clean 503",
    anon.post("/api/join", json=Install().body()).status_code == 503,
)

finish()
