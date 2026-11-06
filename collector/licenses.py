"""
Decides whether a license URL means "free to download". Deliberately strict: the URL must be
one of the standard Creative Commons / public-domain URLs, in the release's own structured data.
Anything else (missing, unknown, deprecated like Sampling Plus, free text) is NOT accepted.
"""

import re
from dataclasses import dataclass

_CC = re.compile(
    r"^https?://(?:www\.)?creativecommons\.org/licenses/(by(?:-nc)?(?:-sa|-nd)?)/(\d\.\d)(?:/[a-z]{2,3})?/?$",
    re.I,
)
_ZERO = re.compile(r"^https?://(?:www\.)?creativecommons\.org/publicdomain/zero/1\.0/?$", re.I)
_MARK = re.compile(r"^https?://(?:www\.)?creativecommons\.org/publicdomain/mark/1\.0/?$", re.I)


@dataclass(frozen=True)
class License:
    code: str  # "by-nc-sa", "cc0", "pdm"
    label: str  # "CC BY-NC-SA 4.0"
    url: str
    needs_credit: bool
    non_commercial: bool


def classify(url) -> License | None:
    if isinstance(url, (list, tuple)):
        url = url[0] if url else ""
    url = str(url or "").strip()
    m = _CC.match(url)
    if m:
        code, version = m.group(1).lower(), m.group(2)
        return License(code, f"CC {code.upper()} {version}", url, True, "nc" in code.split("-"))
    if _ZERO.match(url):
        return License("cc0", "CC0 1.0 (public domain dedication)", url, False, False)
    if _MARK.match(url):
        return License("pdm", "Public Domain", url, False, False)
    return None
