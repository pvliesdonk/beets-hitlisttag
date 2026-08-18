"""Chart edition dataset: on-disk format and reader.

The dataset is the library-independent store of chart history. It holds one
JSON file per hitlist. Each file declares:

- ``chart``: the hitlist name, matching a configured hitlist.
- ``songs``: an object mapping a file-local song id to ``{artist, title}``.
  Ids are scoped to the file; the same song across editions is referenced by
  one id, so its strings are stored once.
- ``editions``: a list of editions. Each edition declares ``axes`` (an object
  keyed by the chart's configured axis names, values positive integers),
  ``size`` (the number of ranks, a positive integer), and ``entries``. Each
  entry is one release at a rank: ``position`` (an integer in ``1..size``) and
  ``songs`` (a non-empty list of song ids). A release crediting more than one
  song -- a double A-side or an early multi-song single -- lists them all;
  positions stay unique within an edition, and so do songs -- a song id may
  appear at most once per edition.

Files hold raw acquired data only: disposable and re-acquirable. Discovery is
recursive; only lowercase ``*.json`` files are read; unreadable directories are
warned about, and symlinked directories are not followed.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard


class DatasetError(Exception):
    """A dataset file is malformed. The message names the offending file."""


@dataclass
class Song:
    id: str
    artist: str
    title: str


@dataclass
class Entry:
    position: int
    songs: list[Song]

    def __post_init__(self) -> None:
        if not self.songs:
            raise ValueError(
                f"entry at position {self.position} must have a non-empty 'songs' list"
            )


@dataclass
class Edition:
    axes: dict[str, int]
    size: int
    entries: list[Entry]

    def __post_init__(self) -> None:
        if self.size < 1:
            raise ValueError("'size' must be an integer >= 1")
        seen_positions: set[int] = set()
        seen_songs: set[str] = set()
        for entry in self.entries:
            if not 1 <= entry.position <= self.size:
                raise ValueError(
                    f"entry position {entry.position} not in 1..{self.size}"
                )
            if entry.position in seen_positions:
                raise ValueError(f"duplicate position {entry.position}")
            seen_positions.add(entry.position)
            for song in entry.songs:
                if song.id in seen_songs:
                    raise ValueError(f"references song {song.id!r} more than once")
                seen_songs.add(song.id)


@dataclass
class HitlistData:
    chart: str
    songs: dict[str, Song]
    editions: list[Edition]
    source: Path


def _is_int(value: object) -> TypeGuard[int]:
    """True for a JSON integer. Excludes bool, which subclasses int."""
    return isinstance(value, int) and not isinstance(value, bool)


def read_dataset(
    root: Path | str, hitlists: Mapping[str, list[str]], log: logging.Logger
) -> list[HitlistData]:
    """Read every edition file under ``root``.

    Returns one ``HitlistData`` per file whose ``chart`` is a configured
    hitlist. Files for unconfigured charts are skipped with a warning. Raises
    ``DatasetError`` (naming the file) on malformed content or on two files
    declaring the same chart.
    """
    root = Path(root)
    results: list[HitlistData] = []
    seen: dict[str, Path] = {}
    for path in _iter_json_files(root, log):
        data = _read_file(path, hitlists, log)
        if data is None:
            continue
        if data.chart in seen:
            raise DatasetError(
                f"chart {data.chart!r} is declared in two files: "
                f"{seen[data.chart]} and {path}"
            )
        seen[data.chart] = path
        results.append(data)
    return results


def _iter_json_files(root: Path, log: logging.Logger):
    def onerror(err: OSError) -> None:
        log.warning(f"cannot read dataset directory {err.filename!r}: {err}")

    for dirpath, _dirnames, filenames in os.walk(root, onerror=onerror):
        for name in sorted(filenames):
            if name.endswith(".json"):  # case-sensitive: NOTES.JSON is ignored
                yield Path(dirpath) / name


def _read_file(
    path: Path, hitlists: Mapping[str, list[str]], log: logging.Logger
) -> HitlistData | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        # UnicodeDecodeError is a ValueError, not an OSError: a non-UTF-8 file
        # must still surface as a DatasetError naming the path, like every
        # other malformed-content case.
        raise DatasetError(f"{path}: cannot read dataset file: {err}") from err
    if not isinstance(raw, dict):
        raise DatasetError(f"{path}: top-level value must be an object")

    chart = raw.get("chart")
    if not isinstance(chart, str):
        raise DatasetError(f"{path}: 'chart' must be a string")
    if chart not in hitlists:
        log.warning(
            f"{path}: chart {chart!r} is not a configured hitlist; skipping file"
        )
        return None

    songs = _parse_songs(raw, path)
    editions = _parse_editions(raw, path, hitlists[chart], songs)
    return HitlistData(chart=chart, songs=songs, editions=editions, source=path)


def _parse_songs(raw: dict, path: Path) -> dict[str, Song]:
    raw_songs = raw.get("songs")
    if not isinstance(raw_songs, dict):
        raise DatasetError(f"{path}: 'songs' must be an object")
    songs: dict[str, Song] = {}
    for sid, val in raw_songs.items():
        if not isinstance(val, dict):
            raise DatasetError(f"{path}: song {sid!r} must be an object")
        artist, title = val.get("artist"), val.get("title")
        if not isinstance(artist, str) or not isinstance(title, str):
            raise DatasetError(
                f"{path}: song {sid!r} must have string 'artist' and 'title'"
            )
        songs[sid] = Song(id=sid, artist=artist, title=title)
    return songs


def _parse_editions(
    raw: dict, path: Path, axis_names: list[str], songs: dict[str, Song]
) -> list[Edition]:
    raw_editions = raw.get("editions")
    if not isinstance(raw_editions, list):
        raise DatasetError(f"{path}: 'editions' must be a list")
    editions: list[Edition] = []
    seen_axes: set[tuple[int, ...]] = set()
    for i, ed in enumerate(raw_editions):
        if not isinstance(ed, dict):
            raise DatasetError(f"{path}: edition {i} must be an object")
        axes = _parse_axes(ed, path, axis_names, i)
        key = tuple(axes[name] for name in axis_names)
        if key in seen_axes:
            raise DatasetError(f"{path}: duplicate edition for axes {axes}")
        seen_axes.add(key)
        size = ed.get("size")
        if not _is_int(size) or size < 1:
            raise DatasetError(f"{path}: edition {axes} 'size' must be an integer >= 1")
        try:
            entries = _parse_entries(ed, path, size, songs, axes)
            editions.append(Edition(axes=axes, size=size, entries=entries))
        except ValueError as err:
            raise DatasetError(f"{path}: edition {axes}: {err}") from err
    return editions


def _parse_axes(ed: dict, path: Path, axis_names: list[str], i: int) -> dict[str, int]:
    axes = ed.get("axes")
    if not isinstance(axes, dict):
        raise DatasetError(f"{path}: edition {i} 'axes' must be an object")
    if set(axes) != set(axis_names):
        raise DatasetError(
            f"{path}: edition {i} axes {sorted(axes)} do not match the "
            f"configured axes {sorted(axis_names)} for this chart"
        )
    result: dict[str, int] = {}
    for name in axis_names:
        val = axes[name]
        if not _is_int(val) or val < 1:
            raise DatasetError(
                f"{path}: edition {i} axis {name!r} must be an integer >= 1"
            )
        result[name] = val
    return result


def _parse_entries(
    ed: dict, path: Path, size: int, songs: dict[str, Song], axes: dict[str, int]
) -> list[Entry]:
    raw_entries = ed.get("entries")
    if not isinstance(raw_entries, list):
        raise DatasetError(f"{path}: edition {axes} 'entries' must be a list")
    entries: list[Entry] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            raise DatasetError(f"{path}: edition {axes} has a non-object entry")
        pos = entry.get("position")
        if not _is_int(pos):
            raise DatasetError(
                f"{path}: edition {axes} entry 'position' must be an integer"
            )
        song_ids = entry.get("songs")
        if not isinstance(song_ids, list):
            raise DatasetError(
                f"{path}: edition {axes} entry at position {pos} must have a "
                f"non-empty 'songs' list"
            )
        resolved: list[Song] = []
        for sid in song_ids:
            if sid not in songs:
                raise DatasetError(
                    f"{path}: edition {axes} entry at position {pos} references "
                    f"unknown song {sid!r}"
                )
            resolved.append(songs[sid])
        entries.append(Entry(position=pos, songs=resolved))
    return entries
