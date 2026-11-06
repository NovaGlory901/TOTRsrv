"""The newest installer of each app, read from static/downloads/ (where
TOTRspa/shared/tools/deploy.cjs puts them) for the Admin page's Downloads tab."""

import datetime
import pathlib
import re

import paths

DIR = paths.downloads_dir()
# platform -> (label, file ending, how to install it)
PLATFORMS = {
    "android": (
        "Android",
        "android.apk",
        "Open the file on the phone and allow installs from this source. After that the app updates itself.",
    ),
    "linux": (
        "Linux",
        "linux.AppImage",
        "chmod +x the file and run it. After that the app updates itself.",
    ),
    "win": (
        "Windows",
        "win.exe",
        "Copy the installer over yourself (not through a browser) to avoid the SmartScreen warning. After that the app updates itself.",
    ),
}
NAME = re.compile(r"^TOTR-(\d+(?:\.\d+)*)-(android\.apk|linux\.AppImage|win\.exe)$")


def latest(folder: pathlib.Path = DIR) -> list:
    """One row per platform, in display order: the newest version found, or `version: None` when
    none has been uploaded."""
    found: dict = {}
    try:
        names = [p for p in folder.iterdir() if p.is_file()]
    except OSError:
        names = []
    for p in names:
        m = NAME.match(p.name)
        if not m:
            continue
        version = tuple(int(n) for n in m.group(1).split("."))
        key = next(k for k, v in PLATFORMS.items() if v[1] == m.group(2))
        if key not in found or version > found[key][0]:
            found[key] = (version, p)
    rows = []
    for key, (label, _ext, note) in PLATFORMS.items():
        row = {
            "platform": key,
            "label": label,
            "note": note,
            "version": None,
            "file": None,
            "url": None,
            "size": None,
            "updated": None,
        }
        if key in found:
            version, p = found[key]
            st = p.stat()
            row.update(
                version=".".join(map(str, version)),
                file=p.name,
                url=f"/static/downloads/{p.name}",
                size=st.st_size,
                updated=datetime.datetime.fromtimestamp(st.st_mtime, datetime.UTC).isoformat(
                    timespec="seconds"
                ),
            )
        rows.append(row)
    return rows
