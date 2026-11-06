"""Runs the maintenance spiders and writes a report of problems and proposed fixes.

    python -m spiders.run                  # the quick spiders (files, duplicates, metadata)
    python -m spiders.run --only enrich    # the slow online one (needs COLLECTOR_CONTACT)
    python -m spiders.run --only files,duplicates

Scanning is read-only: it uses the robots database role and only writes the report file.
Proposed fixes happen only when an admin approves them on the Admin page.
Running some spiders keeps the earlier findings of the ones that did not run.
"""

import argparse
import hashlib
import json
import threading
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime

import db
import uploader

from . import REPORT_PATH, duplicates, enrich, files, metadata, store
from .base import Finding, load

SPIDERS = {m.NAME: m for m in (files, duplicates, metadata, enrich)}
SLOW = {name for name, m in SPIDERS.items() if getattr(m, "SLOW", False)}  # only run when asked for
_running = threading.Lock()
progress = {"text": ""}


def is_running() -> bool:
    return _running.locked()


def finding_id(f: Finding) -> str:
    """Same problem, same id on every run, so an ignored finding stays ignored."""
    key = json.dumps([f.spider, f.kind, f.refs], sort_keys=True, ensure_ascii=False)
    return f"{f.kind}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _say(text: str):
    progress["text"] = text


def run(only=None) -> dict:
    if not _running.acquire(blocking=False):
        raise RuntimeError("The spiders are already running.")
    try:
        started = time.time()
        names = set(only) if only else set(SPIDERS) - SLOW
        snap = load(db, uploader.MUSIC_ROOT)
        findings = []
        for name, mod in SPIDERS.items():
            if name not in names:
                continue
            _say(f"Running the {name} spider...")
            try:
                findings += mod.check(snap, progress=_say) if name in SLOW else mod.check(snap)
            except Exception as e:  # one broken spider must not hide the others' results
                findings.append(Finding(name, "spider_failed", "error", f"{type(e).__name__}: {e}"))

        skip = store.ignored()
        for f in findings:
            f.id = finding_id(f)
        fresh = [asdict(f) for f in findings if f.id not in skip]

        previous = store.read_report() or {}
        # spiders that did not run now
        kept = [f for f in previous.get("findings", []) if f.get("spider") not in names]
        merged = fresh + kept
        now = datetime.now(UTC).isoformat(timespec="seconds")
        report = {
            "generated_at": now,
            "duration_s": round(time.time() - started, 1),
            "library": {
                "artists": len(snap.artists),
                "albums": len(snap.albums),
                "tracks": len(snap.tracks),
            },
            "spiders": sorted(names),
            "ran_at": {**previous.get("ran_at", {}), **{n: now for n in names}},
            "ignored": len(findings) - len(fresh),
            "severity": dict(Counter(f["severity"] for f in merged)),
            "kinds": dict(Counter(f["kind"] for f in merged)),
            "findings": merged,
        }
        store.write_report(report)
        return report
    finally:
        progress["text"] = ""
        _running.release()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--only", help="comma separated spider names: " + ", ".join(SPIDERS))
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None
    unknown = (only or set()) - set(SPIDERS)
    if unknown:
        ap.error("unknown spider(s): " + ", ".join(sorted(unknown)))

    r = run(only)
    print(f"Library: {r['library']}  ({r['duration_s']}s)  ran: {', '.join(r['spiders'])}")
    print("Findings by severity:", r["severity"] or "none", f"({r['ignored']} ignored permanently)")
    proposals = sum(1 for f in r["findings"] if f["action"])
    print(f"{proposals} have a proposed fix waiting for your approval on the Admin page.")
    for kind, n in sorted(r["kinds"].items(), key=lambda kv: -kv[1]):
        print(f"  {n:4d}  {kind}")
    print("Report written to", REPORT_PATH)


if __name__ == "__main__":
    main()
