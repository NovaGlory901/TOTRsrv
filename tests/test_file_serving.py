"""Path traversal on the route that serves files by name (/api/radio/media/file/...): only pictures
and videos inside the media folder come out, whatever the path says, and a link inside the folder
that points outside it does not open the door either."""

import os
import pathlib
import sys
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
os.chdir(PF)
TMP = pathlib.Path(tempfile.mkdtemp(prefix="totr-serve-"))
os.environ.update(DB_AUTH_USER="x", DB_AUTH_PASSWORD="y", MAINT_REPORT=str(TMP / "r" / "m.json"))

from check import check, finish
from fastapi.testclient import TestClient

import main
from auth import permissions, service

media, outside = TMP / "media", TMP / "outside"
(media / "sub").mkdir(parents=True)
outside.mkdir()
(media / "pic.gif").write_bytes(b"GIF89a-inside")
(media / "sub" / "clip.mp4").write_bytes(b"mp4-inside")
(media / "notes.txt").write_text("not a picture")
(outside / "secret.gif").write_bytes(b"GIF89a-SECRET")
(TMP / "secret.gif").write_bytes(b"GIF89a-SECRET")
os.symlink(outside / "secret.gif", media / "link.gif")
os.symlink(outside, media / "linkdir")
main.MEDIA_ROOT = media

user = {
    "user_id": 1,
    "username": "m",
    "role": "member",
    "permissions": permissions.effective("member", ""),
    "csrf": "t",
    "token_hash": "h",
}
service.configured = lambda: True
service.validate_session = lambda token: user
client = TestClient(main.app)
get = lambda path: client.get("/api/radio/media/file/" + path, headers={"cookie": "totr_session=x"})

r = get("pic.gif")
check(
    "a picture inside the folder is served", r.status_code == 200 and r.content == b"GIF89a-inside"
)
check("so is a video in a sub folder", get("sub/clip.mp4").content == b"mp4-inside")
check("a file of another kind is refused", get("notes.txt").status_code == 404)
for label, path in {
    "dot-dot": "../secret.gif",
    "dot-dot twice": "sub/../../secret.gif",
    "encoded dot-dot": "%2e%2e/secret.gif",
    "encoded slash": "..%2fsecret.gif",
    "double encoded": "%252e%252e/secret.gif",
    "absolute path": "/etc/passwd",
    "backslashes": "..\\secret.gif",
    "a link to a file outside": "link.gif",
    "a link to a folder outside": "linkdir/secret.gif",
    "a NUL byte": "pic.gif%00.txt",
}.items():
    r = get(path)
    check(
        f"refused: {label}",
        r.status_code in (404, 400, 422)
        and b"SECRET" not in r.content
        and b"root:" not in r.content,
        (r.status_code, r.content[:30]),
    )
check(
    "the folder itself is not served",
    get("").status_code in (404, 405) and get("sub").status_code == 404,
)
anon = client.get("/api/radio/media/file/pic.gif")
check(
    "without signing in there is nothing to read",
    anon.status_code in (401, 303) and b"GIF" not in anon.content,
)

import shutil

shutil.rmtree(TMP, ignore_errors=True)
finish()
