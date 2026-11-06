"""ccMixter (ccmixter.org): a community site for Creative Commons music. Each upload is a loose song."""

import re

from .. import http
from ..licenses import classify
from ..models import Release, Track
from . import text, year_of

NAME = "ccmixter"
API = "https://ccmixter.org/api/query"
TAG_OK = re.compile(r"^[A-Za-z0-9_+-]{1,40}$")


def search(genre: str, limit: int, max_bytes: int, skip=lambda source_id: False) -> list:
    tag = re.sub(r"\s+", "_", genre.strip().lower())
    if not TAG_OK.match(tag):
        return []
    uploads = http.get_json(
        API, {"f": "json", "tags": tag, "limit": limit * 3, "sort": "rank", "dataview": "default"}
    )
    out = []
    for u in uploads if isinstance(uploads, list) else []:
        if len(out) >= limit:
            break
        source_id = text(u.get("upload_id"))
        lic = classify(u.get("license_url"))
        mp3 = next(
            (
                f
                for f in u.get("files", [])
                if (f.get("file_format_info") or {}).get("default-ext") == "mp3"
                and f.get("download_url")
            ),
            None,
        )
        artist = text(u.get("user_real_name")) or text(u.get("user_name"))
        title = text(u.get("upload_name"))
        if lic is None or mp3 is None or not (source_id and artist and title) or skip(source_id):
            continue
        size = int(mp3.get("file_rawsize") or 0)
        if size > max_bytes:
            continue
        out.append(
            Release(
                NAME,
                source_id,
                title,
                artist,
                year_of(u.get("upload_date")),
                lic.code,
                lic.label,
                lic.url,
                lic.needs_credit,
                text(u.get("file_page_url")),
                genre,
                [
                    Track(
                        title,
                        None,
                        mp3["download_url"],
                        mp3.get("file_name") or f"{title}.mp3",
                        size,
                    )
                ],
                single=True,
            )
        )
    return out
