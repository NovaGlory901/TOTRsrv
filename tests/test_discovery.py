"""The server tells apps on the network it is here (mDNS), only while joining is switched on. The
logic is tested with a stand-in for the zeroconf package; one real announce-and-find over the
network is tried at the end and says "skip" if this machine's network cannot do multicast."""

import os
import pathlib
import sys
import tempfile
import time
import types

PF = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, PF)
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["APP_SETTINGS"] = str(tmp / "app_settings.json")
# so the real certs/ of this checkout are never looked at
os.environ["TOTR_STATE_DIR"] = str(tmp / "state")
(tmp / "state").mkdir()
for k in ("TLS_CERT", "TLS_KEY", "SITE_NAME", "HOST", "PORT", "HTTP_ENABLED", "TLS_PORT"):
    os.environ.pop(k, None)
from check import check, finish

import app_settings
import discovery

# what is announced
env = {"HOST": "192.168.50.7", "PORT": "8080"}
n, port, props = discovery.announcement(env)
check(
    "plain HTTP: the port and the version, join=1, no fingerprint without an authority",
    port == 8080 and props == {"v": "1", "n": n, "join": "1", "http": "8080"},
    props,
)
check("the name falls back to the computer's name", n == discovery._name() and 0 < len(n) <= 40)
os.environ["SITE_NAME"] = "Rae's\nradio\x01" + "x" * 80
n2 = discovery.announcement(env)[0]
check(
    "SITE_NAME is flattened and cut at 40 characters",
    n2.startswith("Rae's radio ") and len(n2) <= 40 and "\n" not in n2 and "\x01" not in n2,
    n2,
)
os.environ.pop("SITE_NAME")
check(
    "a server that listens on this computer only announces nothing",
    discovery.announcement({"HOST": "127.0.0.1", "PORT": "8080"}) is None,
)
check(
    "a bad PORT announces nothing instead of crashing",
    discovery.announcement({"HOST": "192.168.50.7", "PORT": "99999"}) is None,
)
(tmp / "tls").mkdir()
(tmp / "tls" / "c.pem").write_text("x")
(tmp / "tls" / "k.pem").write_text("x")
n3, port3, p3 = discovery.announcement(
    {
        **env,
        "TLS_CERT": str(tmp / "tls" / "c.pem"),
        "TLS_KEY": str(tmp / "tls" / "k.pem"),
        "TLS_PORT": "8443",
    }
)
check(
    "with HTTPS: the first port is the HTTPS one, both are listed",
    port3 == 8443 and p3["https"] == "8443" and p3["http"] == "8080",
    p3,
)
n4, port4, p4 = discovery.announcement(
    {
        **env,
        "TLS_CERT": str(tmp / "tls" / "c.pem"),
        "TLS_KEY": str(tmp / "tls" / "k.pem"),
        "HTTP_ENABLED": "false",
    }
)
check("HTTPS only: no http port is announced", "http" not in p4 and p4["https"] == "8443", p4)

import subprocess

(tmp / "state" / "certs").mkdir()
subprocess.run(
    [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "ed25519",
        "-nodes",
        "-keyout",
        str(tmp / "ca.key"),
        "-out",
        str(tmp / "state" / "certs" / "totr-ca.pem"),
        "-subj",
        "/CN=Test CA",
        "-days",
        "30",
    ],
    capture_output=True,
    check=True,
)
import tlsid

_, _, p5 = discovery.announcement(env)
check(
    "the authority's fingerprint is announced (the certificate only, never a key)",
    p5.get("fp") == tlsid.fingerprint(tlsid.ca_pem()) and len(p5["fp"]) == 29,
    p5,
)
check(
    "nothing secret is in the record",
    all("KEY" not in str(v) for v in p5.values())
    and set(p5) <= {"v", "n", "join", "http", "https", "fp"},
    p5,
)
check(
    "every TXT entry fits mDNS's 255 bytes",
    all(len(f"{k}={v}".encode()) <= 255 for k, v in p5.items()),
)
(tmp / "state" / "certs" / "totr-ca.pem").rename(tmp / "state" / "certs" / "moved.pem")

# the switch drives it (a stand-in for the package)
log = []


class FakeInfo:
    def __init__(self, type_, name, addresses=None, port=None, properties=None, server=None):
        self.name, self.port, self.properties, self.addresses = name, port, properties, addresses


class FakeZc:
    fail = False

    def __init__(self):
        if FakeZc.fail:
            raise OSError("no multicast here")
        log.append("open")

    def register_service(self, info, allow_name_change=False):
        log.append(("register", info.name, info.port, dict(info.properties)))

    def unregister_service(self, info):
        log.append("unregister")

    def close(self):
        log.append("close")


sys.modules["zeroconf"] = types.SimpleNamespace(ServiceInfo=FakeInfo, Zeroconf=FakeZc)
os.environ.update(HOST="192.168.50.7", PORT="8080")
discovery._addresses = lambda: [b"\xc0\xa8\x32\x07"]
check(
    "switch off (the default): silent, nothing opened", discovery.sync() == "silent" and log == []
)
app_settings.set_join_open(True)
check(
    "switch on: announcing",
    discovery.sync() == "announcing"
    and log[0] == "open"
    and log[1][0] == "register"
    and log[1][2] == 8080
    and log[1][1].endswith("._totr._tcp.local."),
    log,
)
check("the record says join=1", log[1][3]["join"] == "1")
n_log = len(log)
check("syncing again changes nothing", discovery.sync() == "announcing" and len(log) == n_log)
os.environ["PORT"] = "9090"
check(
    "a changed setting re-announces",
    discovery.sync() == "announcing" and "unregister" in log and log[-1][2] == 9090,
    log,
)
app_settings.set_join_open(False)
log.clear()
check(
    "switch off: withdrawn and closed",
    discovery.sync() == "silent" and log == ["unregister", "close"],
    log,
)
check(
    "off twice does nothing more", discovery.sync() == "silent" and log == ["unregister", "close"]
)
app_settings.set_join_open(True)
FakeZc.fail = True
r = discovery.sync()
check(
    "no multicast: a plain reason, no crash",
    r.startswith("not announcing") and "no multicast" in r,
    r,
)
FakeZc.fail = False
os.environ["HOST"] = "127.0.0.1"
check(
    "on but listening on this computer only: says so", discovery.sync().startswith("not announcing")
)
os.environ["HOST"] = "192.168.50.7"
check("and recovers when fixed", discovery.sync() == "announcing")
discovery.stop()
check("stop() withdraws", log[-2:] == ["unregister", "close"] and discovery._state["zc"] is None)
del sys.modules["zeroconf"]

# the real network, if this machine allows it
import helpers

try:
    import socket

    import zeroconf as zc_real

    lan = helpers.get_local_ip()
    os.environ["HOST"], os.environ["PORT"] = lan, "8765"
    discovery._addresses = lambda ip=lan: [socket.inet_aton(ip)]
    found = []

    class Listener(zc_real.ServiceListener):
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, 2000)
            if info and info.port == 8765:
                found.append(info)

        update_service = remove_service = lambda *a: None

    app_settings.set_join_open(True)
    browser_zc = zc_real.Zeroconf()
    browser = zc_real.ServiceBrowser(browser_zc, discovery.SERVICE, Listener())
    state = discovery.sync()
    end = time.time() + 6
    while not found and time.time() < end:
        time.sleep(0.2)
    if found:
        props = {k.decode(): v.decode() for k, v in found[0].properties.items()}
        check(
            "a real browser on the network finds the server and reads the record",
            state == "announcing" and props.get("join") == "1" and props.get("http") == "8765",
            (state, props),
        )
    else:
        print(
            "skip real network discovery: nothing found on this machine's network within 6 s ("
            + state
            + ")"
        )
    discovery.stop()
    browser.cancel()
    browser_zc.close()
except Exception as e:
    print(f"skip real network discovery: {e}")

finish()
