"""What the machine is doing: CPU, memory, uptime, temperatures and disk space.

Standard library only. Linux reads /proc and /sys; Windows uses the Win32
calls through ctypes. Anything a platform cannot tell us is None, and the page shows "n/a", so
nothing here can break the Status tab. The parsers take text or a folder so tests can feed them
sample data.
"""

import ctypes
import os
import pathlib
import shutil
import sys
import threading
import time

_lock = threading.Lock()
_cpu = {"sample": None, "at": 0.0, "percent": None}  # CPU load is a difference between two readings


def parse_proc_stat(text: str):
    """(busy, total) jiffies from the first line of /proc/stat, or None."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            parts = [int(x) for x in line.split()[1:9] if x.isdigit()]
            if len(parts) >= 4:
                idle = parts[3] + (parts[4] if len(parts) > 4 else 0)  # idle + iowait
                total = sum(parts)
                return total - idle, total
    return None


def cpu_percent(prev, cur):
    """How busy the CPUs were between two (busy, total) readings, 0-100, or None if nothing elapsed."""
    if not prev or not cur or cur[1] <= prev[1]:
        return None
    return round(max(0.0, min(100.0, 100.0 * (cur[0] - prev[0]) / (cur[1] - prev[1]))), 1)


def parse_meminfo(text: str):
    """{total, available} in bytes from /proc/meminfo, or None."""
    values = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        bits = rest.split()
        if bits and bits[0].isdigit():
            values[name] = int(bits[0]) * 1024
    if "MemTotal" not in values:
        return None
    available = values.get(
        "MemAvailable",
        values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0),
    )
    return {"total": values["MemTotal"], "available": min(available, values["MemTotal"])}


def read_temps(root="/sys/class") -> list:
    """Temperatures in Celsius from the kernel's thermal zones and hardware monitors. Sensible
    values only, at most 12."""
    root = pathlib.Path(root)
    found = []

    def add(label, raw):
        try:
            c = int(raw.strip()) / 1000.0
        except (ValueError, AttributeError):
            return
        if -30 < c < 150:
            found.append({"label": label, "celsius": round(c, 1)})

    def read(path):
        try:
            return path.read_text().strip()
        except OSError:
            return None

    for zone in sorted((root / "thermal").glob("thermal_zone*")):
        add(read(zone / "type") or zone.name, read(zone / "temp"))
    for mon in sorted((root / "hwmon").glob("hwmon*")):
        name = read(mon / "name") or mon.name
        for temp in sorted(mon.glob("temp*_input")):
            label = read(mon / temp.name.replace("_input", "_label")) or temp.name.replace(
                "_input", ""
            )
            add(f"{name} {label}", read(temp))
    return found[:12]


def _windows_cpu():
    class FILETIME(ctypes.Structure):
        _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]

    idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    ):
        return None

    def val(f):
        return (f.hi << 32) | f.lo

    total = val(kernel) + val(user)  # kernel time already includes idle time
    return total - val(idle), total


def _windows_memory():
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint32),
            ("load", ctypes.c_uint32),
            ("total", ctypes.c_uint64),
            ("avail", ctypes.c_uint64),
            ("tpage", ctypes.c_uint64),
            ("apage", ctypes.c_uint64),
            ("tvirt", ctypes.c_uint64),
            ("avirt", ctypes.c_uint64),
            ("ext", ctypes.c_uint64),
        ]

    m = MEMORYSTATUSEX()
    m.length = ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return None
    return {"total": m.total, "available": m.avail}


def _windows_uptime():
    get = ctypes.windll.kernel32.GetTickCount64
    get.restype = ctypes.c_uint64
    return int(get() // 1000)


def _read(path):
    try:
        return pathlib.Path(path).read_text()
    except OSError:
        return None


def _cpu_reading():
    if sys.platform.startswith("linux"):
        text = _read("/proc/stat")
        return parse_proc_stat(text) if text else None
    if sys.platform == "win32":
        try:
            return _windows_cpu()
        except Exception:
            return None
    return None


def current_cpu_percent(min_gap: float = 0.5):
    """CPU use since the previous call (the first call waits a moment to have something to compare
    with)."""
    with _lock:
        now, cur = time.monotonic(), _cpu_reading()
        if cur is None:
            return None
        prev = _cpu["sample"]
        if prev is None:
            time.sleep(0.2)
            now, prev, cur = time.monotonic(), cur, _cpu_reading()
            _cpu["sample"], _cpu["at"] = prev, now - 0.2
        if now - _cpu["at"] >= min_gap:
            pct = cpu_percent(_cpu["sample"], cur)
            _cpu.update(sample=cur, at=now, percent=pct if pct is not None else _cpu["percent"])
        return _cpu["percent"]


def memory():
    info = None
    if sys.platform.startswith("linux"):
        text = _read("/proc/meminfo")
        info = parse_meminfo(text) if text else None
    elif sys.platform == "win32":
        try:
            info = _windows_memory()
        except Exception:
            info = None
    if not info:
        return None
    used = info["total"] - info["available"]
    return {
        "total": info["total"],
        "used": used,
        "available": info["available"],
        "percent": round(100.0 * used / info["total"], 1) if info["total"] else None,
    }


def uptime_seconds():
    if sys.platform.startswith("linux"):
        text = _read("/proc/uptime")
        try:
            return int(float(text.split()[0])) if text else None
        except (ValueError, IndexError):
            return None
    if sys.platform == "win32":
        try:
            return _windows_uptime()
        except Exception:
            return None
    return None


def load_average():
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except (AttributeError, OSError):
        return None


def disks(places: dict) -> list:
    """Space used on the drive behind each named folder. Folders on the same drive are listed once,
    with both names."""
    seen, out = {}, []
    for label, path in places.items():
        path = pathlib.Path(path)
        try:
            device = os.stat(path).st_dev
            usage = shutil.disk_usage(path)
        except OSError:
            out.append({"label": label, "path": str(path), "ok": False})
            continue
        if device in seen:
            seen[device]["label"] += f" + {label}"
            continue
        entry = {
            "label": label,
            "path": str(path),
            "ok": True,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round(100.0 * usage.used / usage.total, 1) if usage.total else None,
        }
        seen[device] = entry
        out.append(entry)
    return out


def snapshot(places: dict) -> dict:
    return {
        "platform": "linux" if sys.platform.startswith("linux") else sys.platform,
        "cores": os.cpu_count(),
        "cpu_percent": current_cpu_percent(),
        "load": load_average(),
        "memory": memory(),
        "uptime_s": uptime_seconds(),
        "temps": read_temps() if sys.platform.startswith("linux") else [],
        "disks": disks(places),
    }
