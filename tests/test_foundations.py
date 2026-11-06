"""Small foundations: the server has a version, listen settings come from HOST/PORT, and the page
footer names the admin's site, not the author's."""

import os
import pathlib
import re
import sys
import tempfile

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
os.environ["DB_AUTH_USER"] = "x"
os.environ["DB_AUTH_PASSWORD"] = "y"
os.environ["MAINT_REPORT"] = str(pathlib.Path(tempfile.mkdtemp()) / "reports" / "maintenance.json")
os.environ.pop("SITE_NAME", None)

from check import check, finish
from fastapi.testclient import TestClient

import helpers
import main
import version

check(
    "the server has a version like 1.2.3",
    re.fullmatch(r"\d+\.\d+\.\d+", version.SERVER_VERSION) is not None,
    version.SERVER_VERSION,
)
check(
    "HOST and PORT are used",
    helpers.listen_address({"HOST": "0.0.0.0", "PORT": "9000"}) == ("0.0.0.0", 9000),
)
check(
    "no PORT means 8080, and the host is found by itself",
    helpers.listen_address({"HOST": "127.0.0.1"}) == ("127.0.0.1", 8080)
    and helpers.listen_address({})[1] == 8080,
)
check(
    "blank settings count as not set",
    helpers.listen_address({"HOST": "  ", "PORT": " "})[1] == 8080,
)
for bad in ("0", "65536", "abc", "-1", "80 80", "8080.5"):
    try:
        helpers.listen_address({"HOST": "127.0.0.1", "PORT": bad})
        ok = False
    except ValueError:
        ok = True
    check(f"PORT={bad!r} is refused", ok)

cl = TestClient(main.app)
page = cl.get("/login").text
check(
    "the footer names no author by default", "self-hosted" in page and "self-hosted by" not in page
)
os.environ["SITE_NAME"] = "Maker <b>Space</b> & Co"
page = cl.get("/login").text
check(
    "SITE_NAME is shown in the footer, escaped",
    "self-hosted by Maker &lt;b&gt;Space&lt;/b&gt; &amp; Co" in page and "<b>Space</b>" not in page,
    re.findall(r"self-hosted[^<]*", page),
)
os.environ["SITE_NAME"] = "x" * 200
check("a very long SITE_NAME is cut", "self-hosted by " + "x" * 60 + " " in cl.get("/login").text)

finish()
