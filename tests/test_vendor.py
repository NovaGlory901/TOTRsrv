"""The DejaVuAmp and FalseHorizons scripts in static/ are copies (the originals live with those
programs). The lock file written by tools/sync-vendor.cjs says what each copy must contain, so a
hand edit made here instead of at the source is caught."""

import hashlib
import json
import pathlib

PF = pathlib.Path(__file__).resolve().parent.parent
from check import check, finish


def sha(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


lock_path = PF / "vendor.lock.json"
check("the lock file exists", lock_path.exists())
files = (
    json.loads(lock_path.read_text(encoding="utf-8")).get("files", {}) if lock_path.exists() else {}
)
check(
    "it lists the DejaVuAmp and FalseHorizons scripts",
    len(files) >= 11 and "falsehorizon-core.js" in files and "dejavuamp.js" in files,
    sorted(files),
)
for name, info in sorted(files.items()):
    p = PF / "static" / name
    check(
        f"static/{name} is the copy the lock file describes (change it at {info['from']}, then run tools/sync-vendor.cjs)",
        p.exists() and sha(p) == info["sha256"],
        "missing" if not p.exists() else "differs",
    )

finish()
