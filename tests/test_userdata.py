"""Tests for the per-person data API (settings and listening position that follow a person between
devices): privacy between people, sign-in and CSRF rules, size and name limits, newer-wins, and the
clear answer before sql/user_data.sql has been run. Runs against a throwaway SQLite file with the
same table."""

import os
import pathlib
import sys
import tempfile

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
os.environ["APP_SETTINGS"] = str(tmp / "reports" / "app_settings.json")
import sqlite_layout as layout
from check import check, finish

AUTH_DB = tmp / "auth.sqlite"
layout.create(AUTH_DB, layout.ACCOUNTS)  # accounts only: the user_data table does not exist yet
for k in ("DB_AUTH_USER", "DB_AUTH_PASSWORD"):
    os.environ[k] = "x"
from fastapi.testclient import TestClient

import main
from auth import service
from userdata import MAX_ITEMS, MAX_VALUE
from userdata import web as ud

service.connect = layout.shim(AUTH_DB)
ud.connect = layout.shim(AUTH_DB)
PW = "correct horse battery staple"
service.create_user("alice", PW)
service.create_user("bob", PW + "!")


def signed_in(user, pw):
    cl = TestClient(main.app)
    assert cl.post("/api/auth/login", json={"username": user, "password": pw}).status_code == 200
    return cl, {"X-CSRF-Token": cl.get("/api/auth/me").json()["csrf"]}


alice, AH = signed_in("alice", PW)
bob, BH = signed_in("bob", PW + "!")
anon = TestClient(main.app)
put = lambda cl, h, name, value, ms: cl.put(
    f"/api/me/data/{name}", json={"value": value, "updated_ms": ms}, headers=h
)

# before setup
r = alice.get("/api/me/data")
check(
    "before setup: reading says what to run instead of failing",
    r.status_code == 503 and "sql/user_data.sql" in r.json()["detail"],
    r.text,
)
check(
    "before setup: saving gives the same clear 503",
    put(alice, AH, "recent", "[]", 1).status_code == 503,
)
layout.create(AUTH_DB, layout.USER_DATA)

# sign-in and CSRF
for method, path in (
    ("GET", "/api/me/data"),
    ("PUT", "/api/me/data/recent"),
    ("POST", "/api/me/data/recent/delete"),
):
    check(
        f"anonymous: {method} {path} is refused",
        anon.request(
            method, path, json={"value": "x", "updated_ms": 1} if method == "PUT" else {}
        ).status_code
        == 401,
    )
check(
    "CSRF token required to save",
    alice.put("/api/me/data/recent", json={"value": "x", "updated_ms": 1}).status_code == 403,
)
check(
    "CSRF token required to delete",
    alice.post("/api/me/data/recent/delete", json={}).status_code == 403,
)

# save and read
check("empty at first", alice.get("/api/me/data").json() == {"items": {}})
r = put(alice, AH, "recent", '[{"track_id":1}]', 1000)
check(
    "save: kept, with its time",
    r.status_code == 200 and r.json() == {"kept": True, "updated_ms": 1000},
    r.text,
)
check(
    "read: the value comes back exactly",
    alice.get("/api/me/data").json()["items"]["recent"]
    == {"value": '[{"track_id":1}]', "updated_ms": 1000},
)
r = put(alice, AH, "recent", "[]", 900)
check(
    "an older save is ignored and the stored time is reported",
    r.json() == {"kept": False, "updated_ms": 1000}
    and alice.get("/api/me/data").json()["items"]["recent"]["value"] == '[{"track_id":1}]',
    r.text,
)
check(
    "an equally old save is ignored too",
    put(alice, AH, "recent", "[]", 1000).json()["kept"] is False,
)
r = put(alice, AH, "recent", "[2]", 2000)
check(
    "a newer save replaces it",
    r.json() == {"kept": True, "updated_ms": 2000}
    and alice.get("/api/me/data").json()["items"]["recent"]["value"] == "[2]",
)
check(
    "another kind of item sits beside it",
    put(alice, AH, "last", '{"position":12}', 5).status_code == 200
    and sorted(alice.get("/api/me/data").json()["items"]) == ["last", "recent"],
)
check(
    "text that looks like SQL or HTML is only stored as text",
    put(alice, AH, "themes", "'; DROP TABLE totr_user_data; --<script>x</script>", 7).status_code
    == 200
    and alice.get("/api/me/data").json()["items"]["themes"]["value"].startswith("'; DROP"),
)

# privacy
check("another person sees none of it", bob.get("/api/me/data").json() == {"items": {}})
check(
    "another person's save does not touch it",
    put(bob, BH, "recent", "[99]", 9999).json()["kept"] is True
    and alice.get("/api/me/data").json()["items"]["recent"]["value"] == "[2]",
)
check(
    "deleting is limited to your own",
    bob.post("/api/me/data/last/delete", json={}, headers=BH).status_code == 200
    and "last" in alice.get("/api/me/data").json()["items"],
)
check(
    "delete removes it",
    alice.post("/api/me/data/last/delete", json={}, headers=AH).status_code == 200
    and "last" not in alice.get("/api/me/data").json()["items"],
)

# limits
for bad in ("Recent", "-x", "a b", "a/b", "x" * 41, "UPPER", ".dot"):
    check(f"name {bad!r} is refused", put(alice, AH, bad, "x", 1).status_code in (404, 405, 422))
check("a name with dots and dashes is fine", put(alice, AH, "a.b-c", "x", 1).status_code == 200)
check(
    "a value over the limit is refused",
    put(alice, AH, "big", "x" * (MAX_VALUE + 1), 1).status_code == 422,
)
check("a value at the limit is kept", put(alice, AH, "big", "x" * MAX_VALUE, 1).status_code == 200)
check(
    "a missing or odd body is refused",
    alice.put("/api/me/data/x", json={}, headers=AH).status_code == 422
    and alice.put("/api/me/data/x", json={"value": 5, "updated_ms": "now"}, headers=AH).status_code
    == 422
    and alice.put("/api/me/data/x", json={"value": "x", "updated_ms": -1}, headers=AH).status_code
    == 422
    and alice.put(
        "/api/me/data/x", json={"value": "x", "updated_ms": 10**15}, headers=AH
    ).status_code
    == 422,
)
n = len(alice.get("/api/me/data").json()["items"])
for i in range(MAX_ITEMS - n):
    put(alice, AH, f"fill-{i}", "x", 1)
check(
    "the number of items is capped",
    put(alice, AH, "one-too-many", "x", 1).status_code == 409
    and len(alice.get("/api/me/data").json()["items"]) == MAX_ITEMS,
)
check(
    "an existing item can still be updated at the cap",
    put(alice, AH, "recent", "[3]", 3000).json()["kept"] is True,
)
check("another person has their own allowance", put(bob, BH, "fill-0", "x", 1).status_code == 200)

finish()
