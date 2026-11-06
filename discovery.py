"""Lets TOTR apps on the same network find this server: mDNS / "Bonjour" service `_totr._tcp`.

The server announces itself ONLY while an admin has switched on "Let people ask to join this server"
(auth/join.py is_open); with the switch off it is silent. The announcement is public to everyone on
the network, so it holds nothing private: the server's name, the ports it serves on, the fingerprint
of its certificate authority (so an app can show it and later check it, see docs/security.md
"Meeting a new server") and join=1. An app that finds it still has to be let in by an admin.

TXT record: v=1, n=<name>, https=<port> and/or http=<port>, fp=<authority fingerprint, if there is
one>, join=1. Nothing here can stop the server from starting: if multicast is unavailable (a
container, a locked-down network) it logs once and carries on.
"""

import logging
import os
import socket
import threading

import app_settings
import helpers
import tlsconf
import tlsid

log = logging.getLogger("totr.discovery")
SERVICE = "_totr._tcp.local."
_lock = threading.Lock()
_state = {"zc": None, "info": None, "props": None}


def _name() -> str:
    raw = os.environ.get("SITE_NAME", "").strip() or socket.gethostname().split(".")[0] or "TOTR"
    return " ".join("".join(c if c.isprintable() else " " for c in raw).split())[:40] or "TOTR"


def announcement(env=None):
    """(name, port, properties) to announce, or None if there is nothing useful to announce
    (listening on this computer only, or a bad setting)."""
    env = os.environ if env is None else env
    try:
        host, http_port = helpers.listen_address(env)
        tls = tlsconf.settings(env)
    except (ValueError, tlsconf.TlsError):
        return None
    if host.startswith("127.") or host == "::1" or host == "localhost":
        return None
    props = {"v": "1", "n": _name(), "join": "1"}
    ports = []
    if tls:
        props["https"] = str(tls["port"])
        ports.append(tls["port"])
    if not tls or tls["http"]:
        props["http"] = str(http_port)
        ports.append(http_port)
    pem = tlsid.ca_pem()
    fp = tlsid.fingerprint(pem) if pem else None
    if fp:
        props["fp"] = fp
    return props["n"], ports[0], props


def _addresses() -> list[bytes]:
    import ifaddr

    found = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if ip.is_IPv4 and not str(ip.ip).startswith(("127.", "169.254.")):
                found.append(socket.inet_aton(str(ip.ip)))
    return found


def _stop_locked():
    zc, info = _state["zc"], _state["info"]
    _state.update(zc=None, info=None, props=None)
    if zc is not None:
        try:
            if info is not None:
                zc.unregister_service(info)
        except Exception:
            pass
        try:
            zc.close()
        except Exception:
            pass


def sync() -> str:
    """Makes the announcement match the switch. Returns "announcing", "silent" (switch off), or a
    short reason it could not announce."""
    with _lock:
        want = app_settings.get_join_open()
        ann = announcement() if want else None
        if not want:
            _stop_locked()
            return "silent"
        if ann is None:
            _stop_locked()
            return "not announcing: the server only listens on this computer, or its listen settings are wrong"
        name, port, props = ann
        if _state["zc"] is not None and _state["props"] == (name, port, props):
            return "announcing"
        _stop_locked()
        try:
            from zeroconf import ServiceInfo, Zeroconf

            addrs = _addresses()
            if not addrs:
                return "not announcing: no network address found"
            info = ServiceInfo(
                SERVICE,
                f"{name}.{SERVICE}",
                addresses=addrs,
                port=port,
                properties=props,
                server=f"{socket.gethostname().split('.')[0]}.local.",
            )
            zc = Zeroconf()
            zc.register_service(info, allow_name_change=True)
        except (
            Exception
        ) as e:  # no multicast, no permission, a missing package: say so once and move on
            log.warning("could not announce this server on the network: %s", e)
            return f"not announcing: {e}"
        _state.update(zc=zc, info=info, props=(name, port, props))
        log.info(
            "announcing %s on the network (%s)",
            name,
            ", ".join(f"{k}={v}" for k, v in props.items() if k in ("http", "https")),
        )
        return "announcing"


def stop():
    with _lock:
        _stop_locked()
