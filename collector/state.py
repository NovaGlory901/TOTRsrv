"""Settings, suggestions, the wanted list, things you said no to, and the log of what was added and
from where."""

import hashlib
import json
import os
import re
import threading
from datetime import UTC, datetime

from . import DATA_DIR

LOCK = threading.RLock()
CONFIG_PATH = DATA_DIR / "collector_config.json"
SUGGESTIONS_PATH = DATA_DIR / "collector.json"
HIDDEN_PATH = DATA_DIR / "collector_hidden.json"
ADDED_PATH = DATA_DIR / "collector_added.jsonl"
WANTED_PATH = DATA_DIR / "collector_wanted.json"

SOURCES = ("internet_archive", "ccmixter", "jamendo")
GENRE_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &+'.-]{0,38}$")
MAX_GENRES, MAX_ENABLED = 60, 20

CONFIG_VERSION = 2  # 2: ccMixter is off by default (mostly stock-style single tracks)

# releases scoring below this (0-100) are not suggested; see collector/quality.py
DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "genres": [],  # [{"name": "vaporwave", "enabled": true}]: remembered even while switched off
    "sources": {s: s != "ccmixter" for s in SOURCES},
    "min_quality": 25,
    "auto_search": True,  # search by itself on the schedule below
    "interval_days": 7,
    "run_hour": 3,  # local hour of day the scheduled search may start
    "per_genre": 12,  # releases looked for per genre per source
    "max_suggestions": 120,  # suggestions kept at once
    "max_release_mb": 800,  # bigger releases are not suggested
}

# Older installs kept these under the "discover" name. Move them once, so nothing is lost.
_LEGACY = {
    "discover_config.json": CONFIG_PATH,
    "discover.json": SUGGESTIONS_PATH,
    "discover_hidden.json": HIDDEN_PATH,
    "discover_added.jsonl": ADDED_PATH,
}


def _migrate_legacy():
    for old_name, new_path in _LEGACY.items():
        old = DATA_DIR / old_name
        try:
            if old.exists() and not new_path.exists():
                os.replace(old, new_path)
        except OSError:
            pass


_migrate_legacy()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def release_id(source: str, source_id: str) -> str:
    return hashlib.sha1(f"{source}:{source_id}".encode()).hexdigest()[:16]


def clean_genres(raw) -> list:
    """Accepts a comma/newline string, a list of names, or a list of {name, enabled}. Returns the
    cleaned list."""
    if isinstance(raw, str):
        raw = re.split(r"[,\n]", raw)
    cleaned, seen = [], set()
    for item in raw or []:
        name, enabled = (
            (item.get("name", ""), bool(item.get("enabled", True)))
            if isinstance(item, dict)
            else (item, True)
        )
        name = re.sub(r"\s+", " ", str(name)).strip()
        if not name:
            continue
        if not GENRE_OK.match(name):
            raise ValueError(
                f'"{name[:30]}" is not a usable genre. Use letters, numbers, spaces, & + . - only.'
            )
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        cleaned.append({"name": name, "enabled": enabled})
    if len(cleaned) > MAX_GENRES:
        raise ValueError(f"Keep at most {MAX_GENRES} genres in the list.")
    if sum(g["enabled"] for g in cleaned) > MAX_ENABLED:
        raise ValueError(f"Switch on at most {MAX_ENABLED} genres at once.")
    return cleaned


def clean_config(raw: dict) -> dict:
    """Validates settings from the browser. Raises ValueError with a message meant for the user."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["genres"] = clean_genres(raw.get("genres", []))
    cfg["sources"] = {
        s: bool((raw.get("sources") or {}).get(s, cfg["sources"][s])) for s in SOURCES
    }
    cfg["auto_search"] = bool(raw.get("auto_search", True))
    for key, lo, hi in (
        ("interval_days", 1, 90),
        ("run_hour", 0, 23),
        ("per_genre", 1, 100),
        ("min_quality", 0, 100),
        ("max_suggestions", 10, 500),
        ("max_release_mb", 20, 10000),
    ):
        try:
            value = int(raw.get(key, cfg[key]))
        except (TypeError, ValueError):
            raise ValueError(f"{key.replace('_', ' ')} must be a number.")
        cfg[key] = min(max(value, lo), hi)
    return cfg


def enabled_genres(cfg: dict) -> list:
    return [g["name"] for g in cfg["genres"] if g["enabled"]]


def load_config() -> dict:
    raw = _read(CONFIG_PATH, {})
    if raw and raw.get("config_version", 1) < 2:
        raw.setdefault("sources", {})["ccmixter"] = (
            False  # one-time: the owner turned ccMixter off; saving again keeps any later choice
        )
    try:
        return clean_config(raw)
    except ValueError:
        return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: dict):
    with LOCK:
        _write(CONFIG_PATH, cfg)


def read_suggestions() -> dict:
    with LOCK:
        d = _read(SUGGESTIONS_PATH, {})
    d.setdefault("suggestions", [])
    d.setdefault("last_search", None)
    return d


def write_suggestions(data: dict):
    with LOCK:
        _write(SUGGESTIONS_PATH, data)


def find_suggestion(release_id_: str):
    return next((s for s in read_suggestions()["suggestions"] if s["id"] == release_id_), None)


def remove_suggestion(release_id_: str):
    with LOCK:
        d = read_suggestions()
        d["suggestions"] = [s for s in d["suggestions"] if s["id"] != release_id_]
        write_suggestions(d)


def wanted() -> list:
    with LOCK:
        return _read(WANTED_PATH, [])


def add_wanted(artist: str, titles=None) -> int:
    """Remembers that these releases are missing, so every search also looks for them.
    titles=None (or empty) means "anything by this artist". Returns how many were new."""
    artist = re.sub(r"\s+", " ", str(artist)).strip()[:120]
    if not artist:
        return 0
    wanted_titles = [re.sub(r"\s+", " ", str(t)).strip()[:160] for t in (titles or [""])]
    with LOCK:
        items = wanted()
        have = {(i["artist"].casefold(), (i.get("title") or "").casefold()) for i in items}
        added = 0
        for title in wanted_titles:
            key = (artist.casefold(), title.casefold())
            if key in have:
                continue
            items.append({"artist": artist, "title": title, "added_at": now_iso()})
            have.add(key)
            added += 1
        _write(WANTED_PATH, items[-300:])
        return added


def remove_wanted(artist: str, title: str = ""):
    with LOCK:
        _write(
            WANTED_PATH,
            [
                i
                for i in wanted()
                if not (
                    i["artist"].casefold() == artist.casefold()
                    and (i.get("title") or "").casefold() == title.casefold()
                )
            ],
        )


def hidden() -> dict:
    d = _read(HIDDEN_PATH, {})
    return {"ids": set(d.get("ids", [])), "artists": set(d.get("artists", []))}


def hide(release_id_: str = "", artist_key: str = ""):
    with LOCK:
        h = hidden()
        if release_id_:
            h["ids"].add(release_id_)
        if artist_key:
            h["artists"].add(artist_key)
        _write(HIDDEN_PATH, {"ids": sorted(h["ids"]), "artists": sorted(h["artists"])})


def added() -> list:
    with LOCK:
        try:
            lines = ADDED_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def credit_for_album(album_id) -> dict | None:
    """Where an album the collector added came from and under which license, for the credit line on
    its page. None for any other album (uploads, the user's own library), for singles (they have no
    album) and for an addition that was undone. Links are http(s) only."""

    def link(u):
        return u if isinstance(u, str) and u.startswith(("http://", "https://")) else ""

    for r in reversed(added()):
        if r.get("album_id") == album_id and not r.get("undone_at"):
            return {
                "license": r.get("license_label") or "",
                "license_url": link(r.get("license_url")),
                "page_url": link(r.get("page_url")),
                "source": r.get("source") or "",
                "needs_credit": bool(r.get("needs_credit")),
                "attribution": r.get("attribution") or "",
            }
    return None


def append_added(record: dict):
    with LOCK:
        ADDED_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ADDED_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def mark_undone(release_id_: str, by: str):
    with LOCK:
        records = added()
        for r in records:
            if r.get("id") == release_id_ and not r.get("undone_at"):
                r["undone_at"], r["undone_by"] = now_iso(), by
        ADDED_PATH.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
        )
