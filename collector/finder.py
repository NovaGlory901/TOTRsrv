"""
Searches the sources for music and saves suggestions. Downloads nothing.

It looks for two things: music in the genres that are switched on, and the specific artists and
albums on the "wanted" list (gaps found by the enrich spider). It skips anything you already own,
said no to, were already offered, or already added.
"""

import threading

import db
from spiders.base import squash

from . import quality, state
from .http import FetchError, link_allowed
from .sources import ccmixter, internet_archive, jamendo

MODULES = {m.NAME: m for m in (internet_archive, ccmixter, jamendo)}
MB_LOOKUPS_PER_SEARCH = 40  # two requests each, one second apart: keeps a search to a few minutes
_running = threading.Lock()
progress = {"text": ""}


def is_running() -> bool:
    return _running.locked()


def sources_available() -> dict:
    return {"internet_archive": True, "ccmixter": True, "jamendo": jamendo.available()}


def library_index() -> dict:
    """What you already own, in a form that ignores case, width and punctuation."""
    with db.get_conn() as conn:
        artists = {squash(r["name"]) for r in conn.execute("SELECT name FROM totr.v_artists")}
        albums = {
            (squash(r["artist"]), squash(r["title"]))
            for r in conn.execute(
                "SELECT ar.name AS artist, al.title AS title FROM totr.v_albums al JOIN totr.v_artists ar ON ar.artist_id = al.artist_id"
            )
        }
        songs = {
            (squash(r["artist"]), squash(r["title"]))
            for r in conn.execute(
                "SELECT ar.name AS artist, t.title AS title FROM totr.v_tracks t "
                "JOIN totr.v_track_artists ta ON ta.track_id = t.track_id JOIN totr.v_artists ar ON ar.artist_id = ta.artist_id"
            )
        }
    return {"artists": artists, "albums": albums, "songs": songs}


def is_owned(release, owned: dict) -> bool:
    key = (squash(release.artist), squash(release.title))
    return key in owned["songs"] if release.single else key in owned["albums"]


def search_all() -> dict:
    if not _running.acquire(blocking=False):
        raise RuntimeError("A search is already running.")
    try:
        cfg = state.load_config()
        genres = state.enabled_genres(cfg)
        wanted = state.wanted()
        if not genres and not wanted:
            raise ValueError(
                "Switch on at least one genre first (or add something to the wanted list)."
            )
        modules = [
            m
            for name, m in MODULES.items()
            if cfg["sources"].get(name) and sources_available()[name]
        ]
        if not modules:
            raise ValueError("Turn on at least one source.")

        owned = library_index()
        hidden = state.hidden()
        data = state.read_suggestions()
        have = {s["id"] for s in data["suggestions"]}
        skip_ids = have | hidden["ids"] | {a["id"] for a in state.added() if not a.get("undone_at")}
        max_bytes = cfg["max_release_mb"] * 1024 * 1024
        per = cfg["per_genre"]

        fresh, errors = [], []
        bar = cfg["min_quality"]
        too_low = [0]
        mb_memo, mb_budget = {}, {"left": MB_LOOKUPS_PER_SEARCH, "errors": 0}

        def musicbrainz(artist):
            """Artist check, at most once per artist per search, within a budget, and never after
            repeated errors."""
            key = squash(artist)
            if key not in mb_memo:
                mb_memo[key] = None
                if mb_budget["left"] > 0 and mb_budget["errors"] < 3:
                    mb_budget["left"] -= 1
                    try:
                        mb_memo[key] = quality.musicbrainz_points(artist)
                    except FetchError:
                        mb_budget["errors"] += 1
            return mb_memo[key]

        def take(releases, reason, wanted_label=""):
            for rel in releases:
                rid = state.release_id(rel.source, rel.source_id)
                if (
                    rid in skip_ids
                    or squash(rel.artist) in hidden["artists"]
                    or is_owned(rel, owned)
                ):
                    continue
                item = rel.to_dict()
                points, why = quality.score(item)
                # an artist check could still lift it over the bar
                if points + quality.MB_MAX >= bar:
                    points, why = quality.score(item, musicbrainz(rel.artist))
                # the wanted list is the user's own choice: always shown
                if points < bar and reason != "wanted":
                    too_low[0] += 1
                    skip_ids.add(rid)
                    continue
                skip_ids.add(rid)
                if not link_allowed(rel.page_url):
                    rel.page_url = ""
                item["page_url"] = rel.page_url
                item.update(
                    id=rid,
                    found_at=state.now_iso(),
                    known_artist=squash(rel.artist) in owned["artists"],
                    reason=reason,
                    wanted_for=wanted_label,
                    quality=points,
                    quality_reasons=why,
                )
                fresh.append(item)

        # 1. specific gaps first (the wanted list), on the sources that can search by artist
        for w in wanted:
            for module in modules:
                if not hasattr(module, "search_wanted"):
                    continue
                label = w["artist"] + (f" - {w['title']}" if w.get("title") else "")
                progress["text"] = f"Looking for {label} on {module.NAME.replace('_', ' ')}..."

                def skip(source_id, m=module):
                    return state.release_id(m.NAME, source_id) in skip_ids

                try:
                    take(
                        module.search_wanted(
                            w["artist"], w.get("title", ""), min(per, 5), max_bytes, skip
                        ),
                        "wanted",
                        label,
                    )
                except FetchError as e:
                    errors.append(f"{module.NAME}: {e}")

        # 2. then the genres that are switched on
        for genre in genres:
            for module in modules:
                progress["text"] = f"Looking for {genre} on {module.NAME.replace('_', ' ')}..."

                def skip(source_id, m=module):
                    return state.release_id(m.NAME, source_id) in skip_ids

                try:
                    take(module.search(genre, per, max_bytes, skip), "genre")
                except FetchError as e:
                    errors.append(f"{module.NAME}: {e}")

        # Wanted first, then artists you already have, then the newest finds. Trim to the limit.
        everything = fresh + data["suggestions"]
        wanted_first = [s for s in everything if s.get("reason") == "wanted"]
        known = [s for s in everything if s.get("reason") != "wanted" and s.get("known_artist")]
        others = sorted(
            (s for s in everything if s.get("reason") != "wanted" and not s.get("known_artist")),
            key=lambda s: s["found_at"],
            reverse=True,
        )
        data["suggestions"] = (wanted_first + known + others)[: cfg["max_suggestions"]]
        data["last_search"] = {
            "at": state.now_iso(),
            "new": len(fresh),
            "genres": genres,
            "wanted": len(wanted),
            "errors": errors,
            "below_quality": too_low[0],
            "min_quality": bar,
        }
        state.write_suggestions(data)
        return data["last_search"]
    finally:
        progress["text"] = ""
        _running.release()
