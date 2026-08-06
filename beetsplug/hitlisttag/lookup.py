"""Normalized exact song lookup over the chart dataset.

`normalize` is the single caseless-matching normalizer, shared by
index-building and per-track lookup so both sides agree by construction.
`SongLookupIndex` maps a normalized (artist, title) key to the chart positions
of the matching song, and reports a key as ambiguous when two distinct song ids
in one chart collapse onto it.
"""

from __future__ import annotations

import unicodedata

# Latin letters NFKD does not decompose; casefold already handles ß -> ss.
_EXPANSIONS = str.maketrans(
    {
        "æ": "ae",
        "œ": "oe",
        "ø": "o",
        "ł": "l",
        "đ": "d",
        "þ": "th",
        "ð": "d",
    }
)


def normalize(text: str) -> str:
    """Normalize an artist or title for exact caseless matching.

    Casefold, expand the Latin letters NFKD leaves alone (æ, œ, …), fold
    diacritics via NFKD, drop combining marks, map every non-alphanumeric
    character to a space, and collapse whitespace.
    """
    text = text.casefold()
    text = text.translate(_EXPANSIONS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = "".join(c if c.isalnum() else " " for c in text)
    return " ".join(text.split())
