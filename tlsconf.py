"""HTTPS for the server itself (no proxy needed). Certificates come from tools/make_certs.py.

    TLS_CERT=certs/server.pem   TLS_KEY=certs/server.key     both set = HTTPS is on (neither set = plain HTTP only, as before)
    TLS_PORT=8443                                             the HTTPS port (default 8443)
    HTTP_ENABLED=true                                         keep the plain HTTP port (8080) too; `false` turns it off once every app uses https

While moving over, both run in one process (same app, same sessions): the installed apps can only
update through the address they already know, so HTTP stays until they have been updated, then
HTTP_ENABLED=false closes it. Relative paths are taken from this folder.
"""

import pathlib
import threading

import paths

HERE = pathlib.Path(__file__).resolve().parent
OFF = ("0", "false", "no", "off")


class TlsError(Exception):
    """Something in the TLS settings is wrong; the message says what to fix."""


def settings(env) -> dict | None:
    cert, key = (env.get("TLS_CERT") or "").strip(), (env.get("TLS_KEY") or "").strip()
    if not cert and not key:
        return None
    if not (cert and key):
        raise TlsError("Set both TLS_CERT and TLS_KEY in .env, or neither.")
    found = []
    for label, value in (("TLS_CERT", cert), ("TLS_KEY", key)):
        p = pathlib.Path(value)
        p = p if p.is_absolute() else paths.home() / p
        if not p.is_file():
            raise TlsError(
                f"{label} points at {p}, which is not a file. Copy the certificate and key there (see docs/setup.md)."
            )
        found.append(str(p))
    try:
        port = int((env.get("TLS_PORT") or "8443").strip())
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise TlsError("TLS_PORT must be a port number such as 8443.")
    return {
        "cert": found[0],
        "key": found[1],
        "port": port,
        "http": (env.get("HTTP_ENABLED") or "true").strip().lower() not in OFF,
    }


def run(app, host: str, http_port: int, tls: dict | None):
    """Serves `app` over HTTP, HTTPS, or both. Blocks until the server is stopped (Ctrl+C)."""
    import uvicorn

    import logredact

    # the request log without the secrets that travel in an address (invite links)
    log_config = logredact.log_config()
    if tls is None:
        uvicorn.run(
            app, host=host, port=http_port, reload=False, server_header=False, log_config=log_config
        )
        return
    https = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=tls["port"],
            ssl_certfile=tls["cert"],
            ssl_keyfile=tls["key"],
            reload=False,
            server_header=False,
            log_config=log_config,
        )
    )
    if not tls["http"]:
        https.run()
        return
    # uvicorn only handles signals on the main thread: Ctrl+C stops both
    worker = threading.Thread(target=https.run, name="https", daemon=True)
    worker.start()
    try:
        uvicorn.run(
            app, host=host, port=http_port, reload=False, server_header=False, log_config=log_config
        )
    finally:
        https.should_exit = True
        worker.join(10)
