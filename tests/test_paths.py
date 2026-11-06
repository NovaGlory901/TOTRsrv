"""Where TOTR keeps what is not code (paths.py): next to the code as always, or in one state folder
when TOTR_STATE_DIR is set (an installed server). Each case runs in a fresh Python so the modules
read the environment the way the service would."""

import json
import os
import pathlib
import subprocess
import sys
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
from check import check, finish

PROBE = r"""
import json, os, sys
sys.path.insert(0, %r)
os.environ["DB_AUTH_USER"] = "x"; os.environ["DB_AUTH_PASSWORD"] = "y"
import paths, localdb, downloads, tlsconf, app_settings, spiders, collector
out = {"home": str(paths.home()), "env": str(paths.env_file()), "data": str(localdb.data_dir()), "downloads": str(downloads.DIR),
       "report": str(spiders.REPORT_PATH), "collector": str(collector.DATA_DIR), "settings": str(app_settings.SETTINGS_PATH)}
try:
    out["tls"] = tlsconf.settings({"TLS_CERT": "certs/server.pem", "TLS_KEY": "certs/server.key", "TLS_PORT": "9443"})
except tlsconf.TlsError as e:
    out["tls_error"] = str(e)
print(json.dumps(out))
""" % str(PF)


def probe(**env):
    base = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("TOTR_", "MAINT_REPORT", "COLLECTOR_DIR", "APP_SETTINGS", "DB_"))
    }
    r = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env={**base, **env},
        cwd=tempfile.gettempdir(),
    )
    return (
        json.loads(r.stdout.strip().splitlines()[-1])
        if r.returncode == 0
        else {"crash": r.stderr[-400:]}
    )


# a checkout, as always
d = probe()
check(
    "no state folder: everything sits next to the code, as before",
    d.get("home") == str(PF)
    and d["env"] == str(PF / ".env")
    and d["data"] == str(PF / "data")
    and d["report"] == str(PF / "reports" / "maintenance.json")
    and d["collector"] == str(PF / "reports")
    and d["settings"] == str(PF / "reports" / "app_settings.json")
    and d["downloads"] == str(PF / "static" / "downloads"),
    d,
)

# an installed server
state = pathlib.Path(tempfile.mkdtemp()) / "state"
(state / "certs").mkdir(parents=True)
(state / "certs" / "server.pem").write_text("x")
(state / "certs" / "server.key").write_text("x")
s = str(state)
P = pathlib.Path  # compared as paths, so the separators of the platform do not matter
d = probe(TOTR_STATE_DIR=s)
check(
    "TOTR_STATE_DIR moves .env, the database, reports, settings and downloads into it",
    P(d.get("env", "")) == state / ".env"
    and P(d["data"]) == state / "data"
    and P(d["report"]) == state / "reports" / "maintenance.json"
    and P(d["collector"]) == state / "reports"
    and P(d["settings"]) == state / "reports" / "app_settings.json"
    and P(d["downloads"]) == state / "downloads",
    d,
)
check(
    "relative certificate paths are found in the state folder (a local variable once shadowed the module and crashed the start)",
    P(d.get("tls", {}).get("cert", "")) == state / "certs" / "server.pem"
    and P(d["tls"]["key"]) == state / "certs" / "server.key"
    and d["tls"]["port"] == 9443,
    d,
)
d = probe(
    TOTR_STATE_DIR=s,
    TOTR_ENV_FILE="/elsewhere/.env",
    TOTR_DATA_DIR="/elsewhere/db",
    TOTR_DOWNLOADS_DIR="/elsewhere/dl",
    MAINT_REPORT="/elsewhere/m.json",
)
check(
    "a more specific setting still wins",
    P(d["env"]) == P("/elsewhere/.env")
    and P(d["data"]) == P("/elsewhere/db")
    and P(d["downloads"]) == P("/elsewhere/dl")
    and P(d["report"]) == P("/elsewhere/m.json"),
    d,
)
d = probe(TOTR_STATE_DIR="  ")
check("a blank state folder counts as not set", d.get("home") == str(PF), d)

finish()
