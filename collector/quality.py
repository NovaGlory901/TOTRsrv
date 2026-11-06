"""A 0-100 quality score for a release, with the reasons in plain words.

The collector's goal is real music by real artists; the legal free-music sources also hold a lot of
stock "royalty free" filler. The score is a transparent sum of signals, so the owner can see why
something ranked low and set the bar in the Collector tab. It judges worth, never legality: the
license check is separate.

Everything here is pure (no network) except `musicbrainz_points`, which the finder calls sparingly.
Works on suggestion dicts (`Release.to_dict()`), and on older "added" records, which carry less
information.
"""

import re

import app_settings
from spiders import musicbrainz as mb

BASE = 10
MB_MAX = 18  # the most the MusicBrainz artist check can add

# Wording that marks stock / filler uploads. Matched as whole words in the title, artist, tags and
# description.
BLOCKLIST = (
    "royalty free",
    "royalty-free",
    "background music",
    "no copyright",
    "copyright free",
    "copyright-free",
    "stock music",
    "stock audio",
    "for youtube",
    "for videos",
    "sound effect",
    "sound effects",
    "sfx",
    "sample pack",
    "loop pack",
    "ringtone",
    "jingle",
    "production music",
)
_BLOCK_RE = re.compile(
    r"(?<![a-z0-9])(" + "|".join(re.escape(w) for w in BLOCKLIST) + r")(?![a-z0-9])"
)

# Internet Archive collections that are not music at all (our search is by genre tag, so some slip
# in).
NOT_MUSIC_COLLECTIONS = {
    "audio_bookspoetry",
    "librivoxaudio",
    "podcasts",
    "radioprograms",
    "audio_religion",
    "audio_news",
    "audio_tech",
}
SHORT_SINGLE_BYTES = 1_500_000  # a "single" this small is usually a loop or a sound clip


def _int(value) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _blocked_word(item: dict):
    signals = item.get("signals") or {}
    text = " ".join(
        str(x)
        for x in (item.get("title"), item.get("artist"), item.get("genre"), signals.get("text"))
        if x
    ).casefold()
    m = _BLOCK_RE.search(text)
    return m.group(1) if m else None


def score(item: dict, mb_result=None, legacy: bool = False) -> tuple:
    """Returns (score 0-100, [reasons]). `mb_result` is (points, reason) from `musicbrainz_points`,
    or None. `legacy` is for old add records, which do not carry cover/size/popularity, so those
    rules are skipped."""
    signals = item.get("signals") or {}
    points, why = BASE, []

    downloads = _int(signals.get("downloads"))
    for floor, pts in ((10000, 20), (1000, 14), (200, 8), (50, 4)):
        if downloads >= floor:
            points += pts
            why.append(f"{downloads:,} downloads at the source")
            break
    reviews = _int(signals.get("reviews"))
    if reviews:
        points += min(10, 5 * reviews)
        why.append(f"{reviews} review{'s' if reviews != 1 else ''}")
        try:
            if float(signals.get("rating") or 0) >= 4:
                points += 5
                why.append("rated 4 stars or more")
        except (TypeError, ValueError):
            pass

    collections = {str(c).casefold() for c in (signals.get("collections") or [])}
    if "netlabels" in collections:
        points += 15
        why.append("released by a netlabel")
    if collections & NOT_MUSIC_COLLECTIONS:
        points -= 30
        why.append("filed under a spoken-word or non-music collection")

    n_tracks = (
        item["tracks"] if isinstance(item.get("tracks"), int) else len(item.get("tracks") or [])
    )
    if item.get("single") or n_tracks <= 1:
        why.append("a single song")
        if not legacy and 0 < _int(item.get("size")) < SHORT_SINGLE_BYTES:
            points -= 10
            why.append("a very short file (maybe a loop or clip)")
    elif n_tracks >= 4:
        points += 15
        why.append(f"a full release ({n_tracks} songs)")
    else:
        points += 8
        why.append(f"a short release ({n_tracks} songs)")

    if not legacy:
        if item.get("cover_url"):
            points += 3
            why.append("has cover art")
        if item.get("year"):
            points += 2

    word = _blocked_word(item)
    if word:
        points -= 40
        why.append(f'looks like stock or filler music (mentions "{word}")')

    if mb_result:
        pts, reason = mb_result
        points += pts
        why.append(reason)

    return max(0, min(100, points)), why


def musicbrainz_points(artist: str):
    """(points, reason) from whether MusicBrainz knows this artist with real releases, or None if it
    could not be checked. Two lookups per new artist (cached for 30 days). Needs the contact, which
    MusicBrainz requires."""
    if not app_settings.contact():
        return None
    found = mb.find_artist(artist)
    if not found:
        return 0, "artist not found on MusicBrainz"
    releases = [g for g in mb.release_groups(found["id"]) if g.kind in mb.WANTED_TYPES]
    if not releases:
        return 0, "on MusicBrainz, but with no releases listed"
    pts = 18 if len(releases) >= 4 else 12
    return (
        pts,
        f"artist has {len(releases)} release{'s' if len(releases) != 1 else ''} on MusicBrainz",
    )
