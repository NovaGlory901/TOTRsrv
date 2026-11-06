"""
Runs every test suite and prints a one-line summary for each.

    python tests/run_all.py            # offline: no network, no real database writes
    python tests/run_all.py --live     # also a few read-only looks at the real Internet Archive / ccMixter

The suites only read from your real database (through the read-only role, if .env is set up) and
never write to it. Account and music-library writes are tested against throwaway SQLite files laid
out like the PostgreSQL schemas (tests/sqlite_layout.py). Checks that need a real PostgreSQL
database with the TOTR layout print "skip" until one is set up from sql/. Needs the dev packages:
pip install -r requirements-dev.txt
"""

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SUITES = [
    "test_guards",
    "test_catalog",
    "test_spiders",
    "test_actions",
    "test_auth",
    "test_http",
    "test_app_hosting",
    "test_collector",
    "test_enrich",
    "test_deezer",
    "test_settings",
    "test_library",
    "test_userdata",
    "test_tls",
    "test_downloads",
    "test_vendor",
    "test_foundations",
    "test_live_library",
    "test_radio",
    "test_visuals",
    "test_monitor",
    "test_localdb",
    "test_migrate",
    "test_paths",
    "test_installer",
    "test_media_schema",
    "test_updates",
    "test_apply_update",
    "test_updates_page",
    "test_move_to_postgres",
    "test_sql_audit",
    "test_file_serving",
    "test_fuzz",
    "test_tlsid",
    "test_join",
    "test_discovery",
    "test_offers",
    "test_hardening",
]


def main() -> int:
    extra = ["--live"] if "--live" in sys.argv else []
    failed = 0
    for name in SUITES:
        path = HERE / f"{name}.py"
        if not path.exists():
            continue
        out = subprocess.run(
            [sys.executable, str(path), *extra],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        lines = out.stdout.splitlines()
        ok, bad, skipped = (sum(l.startswith(w) for l in lines) for w in ("ok", "BAD", "skip"))
        crashed = (
            out.returncode not in (0, 1)
            or "Traceback" in out.stderr
            and bad == 0
            and out.returncode != 0
        )
        status = "PASS" if out.returncode == 0 and not bad else "FAIL"
        failed += status == "FAIL"
        print(
            f"{status}  {name:16s} {ok} passed, {bad} failed"
            + (f", {skipped} SKIPPED" if skipped else "")
            + ("  (crashed, see below)" if crashed else "")
        )
        for l in lines:
            if l.startswith("skip"):
                print("      " + l)  # a skip is not a pass: say what was not checked
        if status == "FAIL":
            print("\n".join(l for l in lines if l.startswith("BAD")) or out.stderr[-1500:])
    print("\nALL SUITES PASSED" if not failed else f"\n{failed} suite(s) FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
