"""Normalized exact song lookup over the chart dataset.

`normalize` is the single caseless-matching normalizer, shared by
index-building and per-track lookup so both sides agree by construction.
`SongLookupIndex` maps a normalized (artist, title) key to the chart positions
of the matching song, and reports a key as ambiguous when two distinct song ids
in one chart collapse onto it.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass

from .dataset import HitlistData, Song

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


@dataclass
class Placement:
    """One appearance of a song in one edition."""

    axes: dict[str, int]
    position: int
    size: int


@dataclass
class LookupResult:
    normalized: tuple[str, str] | None
    placements: dict[str, list[Placement]]
    ambiguous_charts: set[str]

    @property
    def unnormalizable(self) -> bool:
        return self.normalized is None

    @property
    def is_miss(self) -> bool:
        return (
            self.normalized is not None
            and not self.placements
            and not self.ambiguous_charts
        )


class SongLookupIndex:
    def __init__(self, index: dict):
        # (norm_artist, norm_title) -> chart -> {song_id: [Placement]}
        self._index = index

    @classmethod
    def from_datasets(
        cls, datasets: list[HitlistData], log: logging.Logger
    ) -> SongLookupIndex:
        index: dict = {}
        for data in datasets:
            placements: dict[str, list[Placement]] = {}
            songs: dict[str, Song] = {}
            for edition in data.editions:
                for entry in edition.entries:
                    for song in entry.songs:
                        songs[song.id] = song
                        placements.setdefault(song.id, []).append(
                            Placement(
                                axes=edition.axes,
                                position=entry.position,
                                size=edition.size,
                            )
                        )
            for song_id, plist in placements.items():
                song = songs[song_id]
                na, nt = normalize(song.artist), normalize(song.title)
                if not na or not nt:
                    log.warning(
                        f"{data.source}: song {song_id!r} "
                        f"({song.artist!r} - {song.title!r}) normalizes to an "
                        f"empty key; skipping"
                    )
                    continue
                by_chart = index.setdefault((na, nt), {})
                by_chart.setdefault(data.chart, {})[song_id] = plist
        return cls(index)

    def lookup(self, artist: str, title: str) -> LookupResult:
        na, nt = normalize(artist), normalize(title)
        if not na or not nt:
            return LookupResult(normalized=None, placements={}, ambiguous_charts=set())
        by_chart = self._index.get((na, nt))
        if by_chart is None:
            return LookupResult(
                normalized=(na, nt), placements={}, ambiguous_charts=set()
            )
        placements: dict[str, list[Placement]] = {}
        ambiguous: set[str] = set()
        for chart, songmap in by_chart.items():
            if len(songmap) == 1:
                (plist,) = songmap.values()
                placements[chart] = plist
            else:
                ambiguous.add(chart)
        return LookupResult(
            normalized=(na, nt), placements=placements, ambiguous_charts=ambiguous
        )
