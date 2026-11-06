"""The report, the permanently ignored list and the audit log, all plain files next to each other.

A plain "dismiss" needs no list at all: the finding leaves the saved report and the next scan of its
spider brings it back if the problem is still there. Only "ignore permanently" is remembered, in
ignored.json. (The old dismissed.json, whose entries never came back, is no longer read.)
"""

import json
import os
import threading
import time

from . import REPORT_PATH

LOCK = threading.RLock()
IGNORED_PATH = REPORT_PATH.with_name("ignored.json")
AUDIT_PATH = REPORT_PATH.with_name("actions.log")


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)  # readers never see a half-written file


def read_report():
    with LOCK:
        try:
            return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def write_report(report: dict):
    with LOCK:
        _write_json(REPORT_PATH, report)


def ignored() -> set:
    with LOCK:
        try:
            return set(json.loads(IGNORED_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return set()


def ignore(finding_id: str):
    with LOCK:
        _write_json(IGNORED_PATH, sorted(ignored() | {finding_id}))


def unignore_all() -> int:
    with LOCK:
        n = len(ignored())
        _write_json(IGNORED_PATH, [])
        return n


def find(finding_id: str):
    report = read_report()
    return next((f for f in (report or {}).get("findings", []) if f.get("id") == finding_id), None)


def remove_finding(finding_id: str):
    """Drops a handled finding from the saved report so the page updates without a rescan."""
    with LOCK:
        report = read_report()
        if not report:
            return
        report["findings"] = [f for f in report["findings"] if f.get("id") != finding_id]
        report["severity"], report["kinds"] = {}, {}
        for f in report["findings"]:
            report["severity"][f["severity"]] = report["severity"].get(f["severity"], 0) + 1
            report["kinds"][f["kind"]] = report["kinds"].get(f["kind"], 0) + 1
        _write_json(REPORT_PATH, report)


def audit(event: str, finding: dict, result: str, actor: str = ""):
    """One JSON line per approved or dismissed finding: what, when, and what happened."""
    line = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "by": actor,
        "event": event,
        "kind": finding.get("kind"),
        "message": finding.get("message"),
        "action": (finding.get("action") or {}).get("type"),
        "params": (finding.get("action") or {}).get("params"),
        "result": result,
    }
    with LOCK:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
