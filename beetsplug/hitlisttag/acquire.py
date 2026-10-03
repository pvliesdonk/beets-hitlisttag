"""Chart acquisition: run an ingestor and write its editions into the dataset.

``merge_acquired`` is the pure part: it mints hitlist-scoped song ids for
acquired editions, reusing an existing id when a song's normalized artist and
title already exist in the chart's file, and merges the editions in.
``acquire_chart`` is the per-chart flow behind ``beet chartsacquire``: it
fetches only the editions the file does not hold and writes the result
atomically, so a failure leaves the file as it was.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .dataset import (
    DatasetError,
    Edition,
    Entry,
    HitlistData,
    Song,
    _id_sort_key,
    write_dataset_file,
)
from .ingest import AcquiredEdition, EditionRef, IngestError, Ingestor, acquire_edition
from .lookup import normalize

SongKey = tuple[str, str]


def _key(artist: str, title: str) -> SongKey | None:
    """Normalized (artist, title), or None when either normalizes to nothing."""
    artist_key, title_key = normalize(artist), normalize(title)
    if not artist_key or not title_key:
        return None
    return (artist_key, title_key)


def merge_acquired(
    existing: HitlistData | None,
    chart: str,
    axis_names: Sequence[str],
    acquired: Sequence[AcquiredEdition],
    source: Path,
    log: logging.Logger,
) -> tuple[HitlistData, int]:
    """Merge ``acquired`` editions into ``existing``; return it and the new-song count.

    Existing songs and editions are kept unchanged. A song is reused by its
    normalized key (lowest id wins among existing duplicates, with a warning);
    otherwise a new id continues the file's numeric sequence. Within one
    edition an id never repeats: a second entry resolving to the same key gets
    its own id. Editions are sorted by configured axis order, songs by id.
    """
    songs: dict[str, Song] = dict(existing.songs) if existing else {}
    editions: list[Edition] = list(existing.editions) if existing else []

    index: dict[SongKey, str] = {}
    for sid in sorted(songs, key=_id_sort_key):
        key = _key(songs[sid].artist, songs[sid].title)
        if key is None:
            continue
        if key in index:
            log.warning(
                f"{chart}: songs {index[key]!r} and {sid!r} have the same "
                f"normalized name; reusing {index[key]!r}"
            )
            continue
        index[key] = sid

    next_id = max((int(sid) for sid in songs if sid.isdigit()), default=0) + 1
    new_songs = 0
    for edition in acquired:
        used: set[str] = set()
        entries: list[Entry] = []
        for raw in edition.entries:
            resolved: list[Song] = []
            for raw_song in raw.songs:
                key = _key(raw_song.artist, raw_song.title)
                sid = index.get(key) if key is not None else None
                if sid is None or sid in used:
                    sid = str(next_id)
                    next_id += 1
                    new_songs += 1
                    songs[sid] = Song(sid, raw_song.artist, raw_song.title)
                    if key is not None and key not in index:
                        index[key] = sid
                used.add(sid)
                resolved.append(songs[sid])
            entries.append(Entry(raw.position, resolved))
        axes = {name: edition.ref.axes[name] for name in axis_names}
        editions.append(Edition(axes, edition.size, entries))

    editions.sort(key=lambda e: tuple(e.axes[name] for name in axis_names))
    ordered = dict(sorted(songs.items(), key=lambda item: _id_sort_key(item[0])))
    return HitlistData(chart, ordered, editions, source), new_songs


def _ranges(values: Sequence[int]) -> str:
    """``[1999, 2000, 2001, 2005]`` -> ``"1999–2001, 2005"``."""
    parts: list[str] = []
    ordered = sorted(values)
    start = prev = ordered[0]
    for value in [*ordered[1:], None]:
        if value is not None and value == prev + 1:
            prev = value
            continue
        parts.append(str(start) if start == prev else f"{start}–{prev}")
        if value is not None:
            start = prev = value
    return ", ".join(parts)


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n:,} {word}" if n == 1 else f"{n:,} {plural or word + 's'}"


@dataclass
class ChartResult:
    """Outcome of acquiring one chart, and its printable report."""

    chart: str
    acquired: list[EditionRef] = field(default_factory=list)
    entries: int = 0
    new_songs: int = 0
    held: int = 0
    unlisted: list[tuple[int, ...]] = field(default_factory=list)
    error: str | None = None

    def lines(self, axis_names: Sequence[str]) -> list[str]:
        if self.error is not None:
            return [f"{self.chart}: FAILED — {self.error}; file unchanged"]
        if not self.acquired:
            out = [f"{self.chart}: up to date ({_plural(self.held, 'edition')})"]
        else:
            span = ""
            if len(axis_names) == 1:
                years = [ref.axes[axis_names[0]] for ref in self.acquired]
                span = f" ({_ranges(years)})"
            out = [
                f"{self.chart}: acquired {_plural(len(self.acquired), 'edition')}"
                f"{span}, {_plural(self.entries, 'entry', 'entries')}, "
                f"{_plural(self.new_songs, 'new song')}"
            ]
        if self.unlisted:
            span = ""
            if len(axis_names) == 1:
                span = f" ({_ranges([key[0] for key in self.unlisted])})"
            out.append(
                f"{self.chart}: {_plural(len(self.unlisted), 'edition')} in the file "
                f"are not listed by the source{span}; kept"
            )
        return out


def acquire_chart(
    ingestor: Ingestor,
    axis_names: Sequence[str],
    existing: HitlistData | None,
    path: Path,
    hitlists: Mapping[str, list[str]],
    log: logging.Logger,
) -> ChartResult:
    """Acquire the editions ``existing`` lacks and write them to ``path``.

    All-or-nothing: on any reported failure the file is left as it was. Only
    exceptions the contract does not anticipate propagate.
    """
    chart = ingestor.chart
    result = ChartResult(chart)
    if tuple(ingestor.axes) != tuple(axis_names):
        result.error = (
            f"ingestor axes {list(ingestor.axes)} differ from configured axes "
            f"{list(axis_names)}"
        )
        return result

    def key(axes: Mapping[str, int]) -> tuple[int, ...]:
        return tuple(axes.get(name, 0) for name in axis_names)

    held = {key(e.axes) for e in existing.editions} if existing else set()
    result.held = len(held)
    try:
        listed: list[EditionRef] = []
        for ref in ingestor.editions():
            if ref not in listed:
                listed.append(ref)
        result.unlisted = sorted(held - {key(ref.axes) for ref in listed})
        missing = [ref for ref in listed if key(ref.axes) not in held]
        if not missing:
            return result
        acquired = [acquire_edition(ingestor, ref) for ref in missing]
        data, new_songs = merge_acquired(
            existing, chart, axis_names, acquired, path, log
        )
        write_dataset_file(data, path, hitlists, log)
    except IngestError as err:
        result.error = str(err)
        return result
    except DatasetError as err:
        result.error = str(err)
        return result
    except ValueError as err:
        result.error = f"ingestor for {chart} broke its contract: {err}"
        return result
    except OSError as err:
        result.error = f"cannot write {path}: {err}"
        return result
    result.acquired = missing
    result.entries = sum(len(a.entries) for a in acquired)
    result.new_songs = new_songs
    return result
