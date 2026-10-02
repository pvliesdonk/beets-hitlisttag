"""Ingestor contract and discovery.

An *ingestor* is the per-chart piece that obtains chart data from its public
source -- scraper, API fetcher, file downloader. It belongs to one chart and
provides two things: which editions exist at the source, and, for one
edition, its declared size and ranked entries with artist and title as
published. Minting song ids and writing dataset files are the acquisition
tool's job, not the ingestor's.

Ingestors are plain synchronous Python objects satisfying the ``Ingestor``
protocol. One ingestor per module, exposed as a module-level ``INGESTOR``
object; the same convention holds for modules bundled in
``beetsplug.hitlisttag.ingestors`` and for scripts dropped into the
configured ``ingestor_dir``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class IngestError(Exception):
    """An ingestor could not obtain data from its source.

    Raised by ingestors for anything the source does wrong -- unreachable,
    changed layout, an edition it listed but cannot parse -- and by the
    framework when an ingestor breaks its own contract. Any other exception
    out of an ingestor is a bug in the ingestor and propagates.
    """


class DiscoveryError(Exception):
    """Two ingestors at the same tier claim the same chart."""


def _is_int(value: object) -> bool:
    """True for an integer. Excludes bool, which subclasses int."""
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class RawSong:
    """One credited song, exactly as published."""

    artist: str
    title: str

    def __post_init__(self) -> None:
        if not isinstance(self.artist, str) or not self.artist.strip():
            raise ValueError("artist must be a non-empty string")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title must be a non-empty string")


@dataclass(frozen=True)
class RawEntry:
    """One release at one rank.

    More than one song only when the source itself lists them separately;
    an ingestor never splits an "A / B" title -- song resolution is the
    ontology's job.
    """

    position: int
    songs: tuple[RawSong, ...]

    def __post_init__(self) -> None:
        if not _is_int(self.position) or self.position < 1:
            raise ValueError("position must be an integer >= 1")
        if not self.songs:
            raise ValueError(f"entry at position {self.position} must have songs")


@dataclass(frozen=True, eq=False)
class EditionRef:
    """The identity of one edition at the source: its axis values.

    Keyed by the chart's axis names, e.g. ``{"year": 2023}`` or
    ``{"year": 2024, "week": 7}``. Hashable and comparable by value, so a
    refresh can diff refs against what the dataset file holds.
    """

    axes: Mapping[str, int]

    def __post_init__(self) -> None:
        if not isinstance(self.axes, Mapping) or not self.axes:
            raise ValueError("axes must be a non-empty mapping")
        for name, value in self.axes.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"axis name {name!r} must be a non-empty string")
            if not _is_int(value) or value < 1:
                raise ValueError(f"axis {name!r} must be an integer >= 1")
        # Copy so a caller mutating its dict afterwards cannot change the ref.
        object.__setattr__(self, "axes", dict(self.axes))

    def _key(self) -> tuple[tuple[str, int], ...]:
        return tuple(sorted(self.axes.items()))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EditionRef):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())


@dataclass(frozen=True)
class AcquiredEdition:
    """One edition as obtained from the source.

    Carries the dataset ``Edition``'s invariants -- ``size >= 1``, positions
    within ``1..size`` and unique -- re-checked here so a violation is
    attributed to the ingestor rather than to the file about to be written.
    Partial editions are allowed; ``size`` must still be the real size.
    """

    ref: EditionRef
    size: int
    entries: tuple[RawEntry, ...]

    def __post_init__(self) -> None:
        if not _is_int(self.size) or self.size < 1:
            raise ValueError("size must be an integer >= 1")
        seen: set[int] = set()
        for entry in self.entries:
            if not 1 <= entry.position <= self.size:
                raise ValueError(
                    f"entry position {entry.position} not in 1..{self.size}"
                )
            if entry.position in seen:
                raise ValueError(f"duplicate position {entry.position}")
            seen.add(entry.position)
