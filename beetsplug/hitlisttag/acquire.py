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
from collections.abc import Sequence
from pathlib import Path

from .dataset import Edition, Entry, HitlistData, Song, _id_sort_key
from .ingest import AcquiredEdition
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
