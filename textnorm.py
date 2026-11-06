"""Name normalisation shared by the spiders, the search index and the catalog (kept in one place so
they always agree)."""

import re
import unicodedata


def norm(s: str) -> str:
    """Case, width and whitespace insensitive form of a name."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "").casefold()).strip()


def squash(s: str) -> str:
    """Like norm() but also drops spaces and punctuation, so 'A  B!' and 'ab' match."""
    return re.sub(r"[\W_]+", "", norm(s))
