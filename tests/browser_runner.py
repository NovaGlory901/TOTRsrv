"""Shared by the suites that check pages in a real browser (headless Edge/Chromium). Nothing here
needs a server: a page's template is turned into a stand-alone file, the server is replaced by a
small script, and a driver script clicks through the page and writes what it saw into <pre id="out">
as JSON, which `run` reads back."""

import html
import json
import os
import pathlib
import re
import shutil
import subprocess

PF = pathlib.Path(__file__).resolve().parent.parent
CANDIDATES = [
    os.environ.get("EDGE_PATH", ""),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    shutil.which("msedge") or "",
    shutil.which("chromium") or "",
    shutil.which("google-chrome") or "",
]
# software WebGL when there is no GPU
WEBGL_FLAGS = [
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
]


def find_browser():
    if os.environ.get("NO_BROWSER"):  # CI: the pages are checked on a developer's machine
        return None
    return next((p for p in CANDIDATES if p and os.path.isfile(p)), None)


def uri(*parts) -> str:
    return PF.joinpath(*parts).resolve().as_uri()


def page_from_template(
    tmp: pathlib.Path, template: str, stubs: str, driver: str, swaps=()
) -> pathlib.Path:
    """The page with its header/footer removed, fonts and absolute /static paths pointed at local
    files, the stubs loaded first and the driver last. `swaps` are extra (old, new) text
    replacements, for pages that load their own scripts."""
    text = (
        (PF / "templates" / template)
        .read_text(encoding="utf-8")
        .replace("{{HEADER}}", "")
        .replace("{{FOOTER}}", "")
    )
    text = re.sub(r'<link rel="stylesheet" href="https://[^"]+">', "", text)
    text = text.replace('href="/static/style.css"', f'href="{uri("static", "style.css")}"')
    for old, new in swaps:
        assert old in text, old
        text = text.replace(old, new, 1)
    # the shared app.js first (the pages call trackListening from it), then the stubs, which replace
    # its apiFetch with canned answers
    stub_tag = f'<script src="{uri("static", "app.js")}"></script><script src="{uri("tests", "browser", stubs)}"></script>'
    # the stubs go before the page's first script: after the head's last stylesheet, or before the
    # shared scripts
    text = text.replace("</head>", stub_tag + "</head>", 1)
    text = text.replace(
        "</body>",
        f'<pre id="out">pending</pre><script src="{uri("tests", "browser", driver)}"></script></body>',
        1,
    )
    out = tmp / f"page_{template}"
    out.write_text(text, encoding="utf-8")
    return out


def run(browser: str, page: pathlib.Path, tmp: pathlib.Path, flags=(), budget=20000, timeout=150):
    """Opens the page headless and returns the JSON the driver wrote, or None if there was none."""
    proc = subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--autoplay-policy=no-user-gesture-required",
            *flags,
            f"--virtual-time-budget={budget}",
            f"--user-data-dir={tmp / ('profile-' + page.stem)}",
            "--dump-dom",
            page.as_uri(),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    found = re.search(r"RESULT (\{.*?\})\s*</pre>", proc.stdout, re.S)
    return json.loads(html.unescape(found.group(1))) if found else None
