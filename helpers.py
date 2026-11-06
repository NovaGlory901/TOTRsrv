import socket


def listen_address(env) -> tuple[str, int]:
    """(host, port) to listen on: HOST (default: this computer's LAN address) and PORT (default
    8080). Raises ValueError on a bad PORT."""
    host = (env.get("HOST") or "").strip() or get_local_ip()
    raw = (env.get("PORT") or "").strip() or "8080"
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise ValueError("PORT must be a number from 1 to 65535")
    return host, int(raw)


def get_local_ip() -> str:
    """The computer's LAN address, or 127.0.0.1 when it has no network."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # a UDP "connect" sends nothing, but makes the system pick the interface it would use
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "127.0.0.1"
    finally:
        s.close()
    return local_ip
