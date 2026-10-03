"""Chart acquisition: run an ingestor and write its editions into the dataset.

``merge_acquired`` is the pure part: it mints hitlist-scoped song ids for
acquired editions, reusing an existing id when a song's normalized artist and
title already exist in the chart's file, and merges the editions in.
``acquire_chart`` is the per-chart flow behind ``beet chartsacquire``: it
fetches only the editions the file does not hold, skips an edition that
fails (stopping after a few failures in a row), and writes what it acquired
atomically, at checkpoints and at the end, so whatever ends a run the file
holds every edition acquired up to its last successful write.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .dataset import (
    DatasetError,
    Edition,
    Entry,
    HitlistData,
    Song,
    _id_sort_key,
    _is_numeric_id,
    unknown_fields,
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
    replace: bool = False,
    keep_only: set[tuple[int, ...]] | None = None,
) -> tuple[HitlistData, int]:
    """Merge ``acquired`` editions into ``existing``; return it and the new-song count.

    With ``replace``, an acquired edition replaces an existing one with the
    same axes (``--force``); with ``keep_only``, existing editions whose axes
    are not in that set are dropped (``--prune``). Songs are never deleted,
    so ids stay valid for anything that links to them. Otherwise existing
    songs and editions are kept unchanged. A song is reused by its
    normalized key (lowest id wins among existing duplicates, with a warning);
    otherwise a new id continues the file's numeric sequence. Within one
    edition an id never repeats: a second entry resolving to the same key gets
    its own id. Editions are sorted by configured axis order, songs by id.
    Each entry keeps the source_ids its RawEntry carried; they play no part
    in reusing or minting song ids.
    """
    songs: dict[str, Song] = dict(existing.songs) if existing else {}
    editions: list[Edition] = list(existing.editions) if existing else []

    def edition_key(axes: Mapping[str, int]) -> tuple[int, ...]:
        return tuple(axes[name] for name in axis_names)

    if replace:
        incoming = {edition_key(a.ref.axes) for a in acquired}
        editions = [e for e in editions if edition_key(e.axes) not in incoming]
    if keep_only is not None:
        editions = [e for e in editions if edition_key(e.axes) in keep_only]

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

    next_id = max((int(sid) for sid in songs if _is_numeric_id(sid)), default=0) + 1
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
            entries.append(Entry(raw.position, resolved, dict(raw.source_ids)))
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


CHECKPOINT_SECONDS = 60
"""Seconds of fetching after which acquired editions are written."""

STOP_AFTER_FAILURES = 3
"""Failed editions in a row after which a chart's run stops."""


@dataclass
class ChartResult:
    """Outcome of acquiring one chart, and its printable report."""

    chart: str
    acquired: list[EditionRef] = field(default_factory=list)
    entries: int = 0
    new_songs: int = 0
    held: int = 0
    unlisted: list[tuple[int, ...]] = field(default_factory=list)
    pruned: list[tuple[int, ...]] = field(default_factory=list)
    forced: bool = False
    error: str | None = None
    failed: list[tuple[EditionRef, str]] = field(default_factory=list)
    stopped: bool = False
    not_attempted: int = 0
    written: bool = False

    def lines(self, axis_names: Sequence[str]) -> list[str]:
        def span(values: Sequence[int]) -> str:
            return f" ({_ranges(values)})" if len(axis_names) == 1 else ""

        def first_axis(refs: Sequence[EditionRef]) -> list[int]:
            return [ref.axes[axis_names[0]] for ref in refs]

        out: list[str] = []
        if self.acquired:
            out.append(
                f"{self.chart}: {'re-acquired' if self.forced else 'acquired'} "
                f"{_plural(len(self.acquired), 'edition')}"
                f"{span(first_axis(self.acquired))}, "
                f"{_plural(self.entries, 'entry', 'entries')}, "
                f"{_plural(self.new_songs, 'new song')}"
            )
        if self.failed:
            refs = [ref for ref, _reason in self.failed]
            out.append(
                f"{self.chart}: {_plural(len(self.failed), 'edition')} failed"
                f"{span(first_axis(refs))}: {self.failed[-1][1]}; "
                "a later run retries them"
            )
        if self.stopped:
            out.append(
                f"{self.chart}: stopped after {STOP_AFTER_FAILURES} failed editions "
                f"in a row; {_plural(self.not_attempted, 'edition')} not attempted"
            )
        if self.error is not None:
            kept = (
                "file keeps the editions acquired before it"
                if self.written
                else "file unchanged"
            )
            out.append(f"{self.chart}: FAILED — {self.error}; {kept}")
            return out
        if not out and not self.pruned:
            out.append(f"{self.chart}: up to date ({_plural(self.held, 'edition')})")
        if self.pruned:
            out.append(
                f"{self.chart}: dropped {_plural(len(self.pruned), 'edition')} "
                f"not listed by the source{span([key[0] for key in self.pruned])}"
            )
        if self.unlisted:
            out.append(
                f"{self.chart}: {_plural(len(self.unlisted), 'edition')} in the file "
                "are not listed by the source"
                f"{span([key[0] for key in self.unlisted])}; kept"
            )
        return out


def acquire_chart(
    ingestor: Ingestor,
    axis_names: Sequence[str],
    existing: HitlistData | None,
    path: Path,
    hitlists: Mapping[str, list[str]],
    log: logging.Logger,
    force: bool = False,
    prune: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> ChartResult:
    """Acquire the editions ``existing`` lacks and write them to ``path``.

    With ``force``, every edition the source lists is re-acquired, replacing
    same-axes editions; with ``prune`` (meaningful only with ``force``),
    editions the source no longer lists are dropped. An edition that fails
    is recorded in ``failed`` and skipped; after ``STOP_AFTER_FAILURES`` in a
    row the run stops. Acquired editions are written atomically every
    ``CHECKPOINT_SECONDS`` (by ``clock``), at the end, and before an
    unexpected exception from the ingestor (Ctrl-C, a bug) propagates; a
    failed write ends the run. Whatever ends it, the file holds what it held
    plus every edition acquired up to the last successful write.
    """
    chart = ingestor.chart
    result = ChartResult(chart, forced=force)
    if tuple(ingestor.axes) != tuple(axis_names):
        result.error = (
            f"ingestor axes {list(ingestor.axes)} differ from configured axes "
            f"{list(axis_names)}"
        )
        return result

    def key(axes: Mapping[str, int]) -> tuple[int, ...]:
        # The reader guarantees every configured axis on a held edition. A ref
        # from the ingestor may lack one; .get keeps that from raising here,
        # and acquire_edition then rejects the ref as an IngestError.
        return tuple(axes.get(name, 0) for name in axis_names)

    if existing is None and path.exists():
        result.error = (
            f"{path} exists but does not hold {chart}; move it aside or fix "
            "its 'chart' field"
        )
        return result

    held = {key(e.axes) for e in existing.editions} if existing else set()
    result.held = len(held)

    # Listing phase: a failure here ends the chart before anything is
    # fetched or written. Only the contract's own failures are reported;
    # any other exception from the ingestor propagates (#99).
    try:
        listed: list[EditionRef] = []
        seen: set[EditionRef] = set()
        for ref in ingestor.editions():
            if ref not in seen:
                seen.add(ref)
                listed.append(ref)
        listed_keys = {key(ref.axes) for ref in listed}
        if prune and not listed:
            result.error = (
                "the source lists no editions; refusing to prune every edition"
            )
            return result
        if prune:
            result.pruned = sorted(held - listed_keys)
        else:
            result.unlisted = sorted(held - listed_keys)
        if force:
            missing = listed
        else:
            missing = [ref for ref in listed if key(ref.axes) not in held]
        if not missing and not result.pruned:
            return result
        if existing is not None:
            dropped = unknown_fields(existing.source)
            if dropped:
                result.error = (
                    f"{existing.source} holds fields chartsacquire would drop "
                    f"({', '.join(dropped)}); remove them, or keep this chart "
                    "hand-maintained"
                )
                return result
    except IngestError as err:
        result.error = str(err)
        return result
    except ValueError as err:
        result.error = f"ingestor for {chart} broke its contract: {err}"
        return result

    state = existing
    pending: list[AcquiredEdition] = []
    last_write = clock()

    def write() -> bool:
        """Merge ``pending`` into the file; False, with ``error`` set, if that fails."""
        nonlocal state, last_write
        data, new_songs = merge_acquired(
            state,
            chart,
            axis_names,
            pending,
            path,
            log,
            replace=force,
            keep_only=listed_keys if prune else None,
        )
        try:
            write_dataset_file(data, path, hitlists, log)
        except DatasetError as err:
            result.error = str(err)
        except OSError as err:
            result.error = f"cannot write {path}: {err}"
        if result.error is not None:
            if not result.written:
                result.pruned = []  # the prune never reached the file
            return False
        state = data
        result.acquired.extend(edition.ref for edition in pending)
        result.entries += sum(len(edition.entries) for edition in pending)
        result.new_songs += new_songs
        result.written = True
        pending.clear()
        last_write = clock()
        return True

    in_a_row = 0
    for index, ref in enumerate(missing):
        try:
            edition = acquire_edition(ingestor, ref)
        except IngestError as err:
            reason = str(err)
        except ValueError as err:
            reason = f"ingestor for {chart} broke its contract: {err}"
        except BaseException:
            # Ctrl-C, or a bug in the ingestor: keep what was acquired, then
            # let the exception through unchanged.
            if pending:
                try:
                    if not write():
                        log.error(f"{chart}: {result.error}")
                except Exception as err:
                    log.error(f"{chart}: could not save acquired editions: {err!r}")
            raise
        else:
            pending.append(edition)
            in_a_row = 0
            if clock() - last_write >= CHECKPOINT_SECONDS and not write():
                return result
            continue
        result.failed.append((ref, reason))
        in_a_row += 1
        if in_a_row >= STOP_AFTER_FAILURES:
            result.stopped = True
            result.not_attempted = len(missing) - index - 1
            break

    if pending or (result.pruned and not result.written):
        write()
    return result
