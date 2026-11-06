"""The server's network identity: its own certificate authority (certs/totr-ca.pem, next to the
server certificate; made by tools/make_certs.py).

An app that meets this server for the first time shows its person a short code (the fingerprint) and
asks them to compare it with the one on this server's admin page (Updates > This server). If they
match, the app remembers this authority for this server only. Only the PUBLIC certificate is ever
read or served here: the authority's key (totr-ca.key) is never opened by the web app.
"""

import base64
import hashlib
import os
import pathlib
import re

import paths

PEM = re.compile(rb"-----BEGIN CERTIFICATE-----\s*([A-Za-z0-9+/=\s]+?)\s*-----END CERTIFICATE-----")
MAX_PEM = 16 * 1024


def ca_file() -> pathlib.Path | None:
    """certs/totr-ca.pem next to the server certificate (TLS_CERT), or in TOTR_CERTS_DIR / the home
    certs folder. None when there is none."""
    candidates = []
    cert = (os.environ.get("TLS_CERT") or "").strip()
    if cert:
        p = pathlib.Path(cert)
        candidates.append((p if p.is_absolute() else paths.home() / p).parent / "totr-ca.pem")
    if os.environ.get("TOTR_CERTS_DIR"):
        candidates.append(pathlib.Path(os.environ["TOTR_CERTS_DIR"]) / "totr-ca.pem")
    candidates.append(paths.home() / "certs" / "totr-ca.pem")
    return next((c for c in candidates if c.is_file()), None)


def ca_pem() -> bytes | None:
    """The authority's certificate as PEM text, exactly one certificate, or None (no file, not a
    certificate, or a private key by mistake)."""
    f = ca_file()
    if f is None:
        return None
    try:
        data = f.read_bytes()[:MAX_PEM]
    except OSError:
        return None
    if b"PRIVATE KEY" in data:
        return None  # a key must never go out, whatever the file is called
    found = PEM.findall(data)
    if len(found) != 1:
        return None
    return (
        b"-----BEGIN CERTIFICATE-----\n"
        + b"\n".join(re.findall(rb".{1,64}", re.sub(rb"\s+", b"", found[0])))
        + b"\n-----END CERTIFICATE-----\n"
    )


def der_of(pem: bytes) -> bytes | None:
    m = PEM.search(pem)
    if not m:
        return None
    try:
        return base64.b64decode(re.sub(rb"\s+", b"", m.group(1)), validate=True)
    except ValueError:
        return None


def invite_code() -> str:
    """The code an invite link carries after a '#' so the phone can trust this server's authority
    without comparing anything by eye: the authority's fingerprint, or '' when this server has no
    authority."""
    pem = ca_pem()
    return (fingerprint(pem) if pem else None) or ""


def fingerprint(pem: bytes) -> str | None:
    """The first 24 hex digits of the certificate's SHA-256, in six groups of four:
    9f3a-77c1-0be2-44d9-c05e-a1b7 (96 bits: enough to compare by eye and out of reach to forge)."""
    der = der_of(pem)
    if not der:
        return None
    h = hashlib.sha256(der).hexdigest()[:24]
    return "-".join(h[i : i + 4] for i in range(0, 24, 4))
