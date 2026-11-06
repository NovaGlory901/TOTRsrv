"""Each source has search(genre, limit, max_bytes, skip) -> list[Release] and a NAME."""

import re


def text(value) -> str:
    """API fields are sometimes a string and sometimes a list of strings."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def year_of(*values):
    for v in values:
        m = re.match(r"\s*(\d{4})", text(v))
        if m and 1800 <= int(m.group(1)) <= 2100:
            return int(m.group(1))
    return None


_TRACK = re.compile(r"^\s*(?:(?:cd|disc)\s*\d+\s*[-_ ]\s*)?(\d{1,3})\s*[-_. )]+\s*(.+)$", re.I)


def parse_track_name(filename: str):
    """'01_-_In_Your_Eyes.mp3' -> (1, 'In Your Eyes'). No leading number -> (None, title)."""
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", filename)
    m = _TRACK.match(stem)
    number, rest = (int(m.group(1)), m.group(2)) if m else (None, stem)
    title = re.sub(r"_+", " ", rest)
    title = re.sub(r"^[\s\-]+|[\s\-]+$", "", re.sub(r"\s+", " ", title))
    return number, title or stem


def clean_title(title: str, artist: str) -> str:
    """Netlabel uploads often title an album '[cat123] Artist - Album [cat123]'. Keep just 'Album'."""
    t = title
    t = re.sub(r"^\s*[\[(][^\])]{1,24}[\])]\s*", "", t)  # leading [catalog]
    for _ in range(2):  # leading 'Artist - '
        m = re.match(r"^\s*" + re.escape(artist) + r"\s*[-–—:]+\s*(.+)$", t, re.I)
        if not m:
            break
        t = m.group(1)
    t = re.sub(r"\s*[\[(][A-Za-z]{1,8}[-_ ]?\d{1,5}[\])]\s*$", "", t)  # trailing [catalog]
    return t.strip() or title
