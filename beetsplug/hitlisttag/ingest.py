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

import importlib
import importlib.util
import logging
import os
import pkgutil
import stat
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable


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


class _ReadOnlyDict(dict):
    """A dict that refuses changes.

    Used for ``RawEntry.source_ids`` instead of a ``MappingProxyType``, which
    cannot be pickled or deep-copied: an ingestor may parse pages in a
    process pool or cache its entries, and a ``RawEntry`` must survive that.
    """

    def _refuse(self, *args, **kwargs):
        raise TypeError("source_ids is read-only")

    __setitem__ = __delitem__ = __ior__ = _refuse
    clear = pop = popitem = setdefault = update = _refuse

    def __reduce__(self):
        return (_ReadOnlyDict, (dict(self),))


def _is_text(value: object) -> bool:
    """True for a string with something other than whitespace in it."""
    return isinstance(value, str) and bool(value.strip())


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

    ``source_ids`` holds the identifiers the source publishes for this
    entry, raw, keyed ``<source>/<kind>`` (``{"top40.nl/title": "8522"}``).
    Optional; the framework stores them and never interprets them, so they
    never change which song an entry resolves to.
    """

    position: int
    songs: tuple[RawSong, ...]
    source_ids: Mapping[str, str] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        if not _is_int(self.position) or self.position < 1:
            raise ValueError("position must be an integer >= 1")
        if isinstance(self.songs, str) or not all(
            isinstance(song, RawSong) for song in self.songs
        ):
            raise ValueError("songs must be a tuple of RawSong")
        # Normalise so a list passed in cannot be mutated behind the frozen entry.
        object.__setattr__(self, "songs", tuple(self.songs))
        if not self.songs:
            raise ValueError(f"entry at position {self.position} must have songs")
        # Copy first, then validate the copy, so a mapping that changes
        # between reads cannot slip past the check; the copy is read-only.
        ids = (
            _ReadOnlyDict(self.source_ids)
            if isinstance(self.source_ids, Mapping)
            else None
        )
        if ids is None or not all(
            _is_text(key) and _is_text(value) for key, value in ids.items()
        ):
            raise ValueError(
                "source_ids must map non-empty strings to non-empty strings"
            )
        object.__setattr__(self, "source_ids", ids)


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
        # Copy behind a read-only view: a ref is hashable, so neither the
        # caller's dict nor ``ref.axes`` itself may change it afterwards.
        object.__setattr__(self, "axes", MappingProxyType(dict(self.axes)))

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
        if not isinstance(self.ref, EditionRef):
            raise ValueError("ref must be an EditionRef")
        if not _is_int(self.size) or self.size < 1:
            raise ValueError("size must be an integer >= 1")
        if isinstance(self.entries, str) or not all(
            isinstance(entry, RawEntry) for entry in self.entries
        ):
            raise ValueError("entries must be a tuple of RawEntry")
        object.__setattr__(self, "entries", tuple(self.entries))
        seen: set[int] = set()
        for entry in self.entries:
            if not 1 <= entry.position <= self.size:
                raise ValueError(
                    f"entry position {entry.position} not in 1..{self.size}"
                )
            if entry.position in seen:
                raise ValueError(f"duplicate position {entry.position}")
            seen.add(entry.position)


@runtime_checkable
class Ingestor(Protocol):
    """What an ingestor provides. Structural: no base class to inherit."""

    chart: str
    """Hitlist name; must match a ``hitlists`` config entry."""

    axes: tuple[str, ...]
    """Axis names in order, e.g. ``("year",)`` or ``("year", "week")``."""

    def editions(self) -> Iterable[EditionRef]:
        """Every edition that exists at the source. May be expensive;
        the tool calls it once per run."""
        ...

    def fetch(self, ref: EditionRef) -> AcquiredEdition:
        """One edition, whose ``ref`` equals the one asked for."""
        ...


def acquire_edition(ingestor: Ingestor, ref: EditionRef) -> AcquiredEdition:
    """Fetch ``ref`` through ``ingestor``, enforcing the contract.

    Callers use this rather than ``fetch`` directly so the checks cannot be
    forgotten: the ref's axis names must be the ingestor's, and the edition
    returned must be the one asked for. Either violation is an
    ``IngestError``; an ``IngestError`` raised by ``fetch`` passes through,
    and anything else propagates as a bug in the ingestor.
    """
    if set(ref.axes) != set(ingestor.axes):
        raise IngestError(
            f"ingestor for {ingestor.chart!r} declares axes {ingestor.axes!r} "
            f"but edition ref has {sorted(ref.axes)!r}"
        )
    edition = ingestor.fetch(ref)
    if not isinstance(edition, AcquiredEdition):
        raise IngestError(
            f"ingestor for {ingestor.chart!r} returned {type(edition).__name__} "
            f"instead of an AcquiredEdition for {ref.axes!r}"
        )
    if edition.ref != ref:
        raise IngestError(
            f"ingestor for {ingestor.chart!r} returned edition {edition.ref.axes!r} "
            f"when asked for {ref.axes!r}"
        )
    return edition


BUNDLED_PACKAGE = "beetsplug.hitlisttag.ingestors"


def _check_ingestor(obj: object) -> str | None:
    """Why ``obj`` is not a usable ingestor, or None when it is."""
    if obj is None:
        return "no INGESTOR defined"
    if isinstance(obj, type):
        return "INGESTOR must be an instance, not a class"
    chart = getattr(obj, "chart", None)
    if not isinstance(chart, str) or not chart:
        return "chart must be a non-empty string"
    axes = getattr(obj, "axes", None)
    if (
        not isinstance(axes, tuple)
        or not axes
        or not all(isinstance(a, str) and a for a in axes)
    ):
        return "axes must be a non-empty tuple of non-empty strings"
    if len(set(axes)) != len(axes):
        return f"axes {axes!r} contain a duplicate name"
    for method in ("editions", "fetch"):
        if not callable(getattr(obj, method, None)):
            return f"{method} must be a callable"
    return None


def _validated(module: object, origin: str, log: logging.Logger) -> Ingestor | None:
    ingestor = getattr(module, "INGESTOR", None)
    problem = _check_ingestor(ingestor)
    if problem is not None:
        log.warning(f"ignoring ingestor {origin}: {problem}")
        return None
    return ingestor


def _load_bundled(module_name: str, log: logging.Logger) -> Ingestor | None:
    try:
        module = importlib.import_module(module_name)
    except Exception as err:  # a bundled module is this package's bug
        log.warning(f"cannot load bundled ingestor {module_name}: {err!r}")
        return None
    return _validated(module, module_name, log)


def _bundled_module_names() -> list[str]:
    package = importlib.import_module(BUNDLED_PACKAGE)
    return sorted(
        f"{BUNDLED_PACKAGE}.{info.name}"
        for info in pkgutil.iter_modules(package.__path__)
    )


def _claim(
    found: dict[str, tuple[str, Ingestor]],
    ingestor: Ingestor,
    origin: str,
    tier: str,
) -> None:
    """Register ``ingestor`` under its chart; a second claim at the same tier
    is a DiscoveryError naming both origins."""
    if ingestor.chart in found:
        raise DiscoveryError(
            f"chart {ingestor.chart!r} is claimed by two {tier} ingestors: "
            f"{found[ingestor.chart][0]} and {origin}"
        )
    found[ingestor.chart] = (origin, ingestor)


_DROP_IN_PREFIX = "hitlisttag_ingestor_"


def _iter_drop_in_files(directory: Path, log: logging.Logger) -> list[Path]:
    """Top-level ``*.py`` files in ``directory``, sorted by name.

    No recursion, so subdirectories (symlinked or not) are never entered;
    ``_``-prefixed files are skipped; the suffix is case-sensitive. A missing
    directory yields nothing; an unreadable directory, or a file that cannot
    be examined, is warned about. ``os`` calls are used directly because
    ``Path.is_dir``/``is_file`` raise ``PermissionError`` before Python 3.14
    and swallow it from 3.14 on -- neither is the warning the user needs.
    """
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return []
    except OSError as err:
        log.warning(f"cannot read ingestor directory {str(directory)!r}: {err}")
        return []
    files: list[Path] = []
    for name in names:
        if not name.endswith(".py") or name.startswith("_"):
            continue
        path = directory / name
        try:
            mode = os.stat(path).st_mode
        except OSError as err:
            log.warning(f"cannot read ingestor {path}: {err}")
            continue
        if stat.S_ISREG(mode):
            files.append(path)
    return files


def _load_drop_in(path: Path, log: logging.Logger) -> Ingestor | None:
    """Import ``path`` under a synthetic module name and validate its INGESTOR.

    The synthetic name keeps a script called ``json.py`` from shadowing the
    real module, and the file's directory is never added to ``sys.path``, so
    a script cannot import a sibling file. Any failure to import is warned
    about by path and the script is skipped.
    """
    name = f"{_DROP_IN_PREFIX}{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        log.warning(f"cannot load ingestor {path}: not importable")
        return None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution on purpose: a dataclass defined in the
    # script resolves its annotations through sys.modules, and the entry is
    # kept for a usable ingestor so that lookup keeps working afterwards. A
    # script that fails to load or validate is unregistered again.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except (Exception, SystemExit) as err:  # a script may raise or exit at import
        sys.modules.pop(name, None)
        log.warning(f"cannot load ingestor {path}: {err!r}")
        return None
    ingestor = _validated(module, str(path), log)
    if ingestor is None:
        sys.modules.pop(name, None)
    return ingestor


def discover_ingestors(
    drop_in_dir: Path | None, *, log: logging.Logger
) -> dict[str, Ingestor]:
    """Every usable ingestor, keyed by chart name.

    Bundled modules first, then scripts in ``drop_in_dir``; a drop-in
    claiming a chart a bundled module also claims overrides it with a
    logged notice. Unusable modules are warned about and skipped; two
    claims at the same tier raise ``DiscoveryError``.
    """
    bundled: dict[str, tuple[str, Ingestor]] = {}
    for module_name in _bundled_module_names():
        ingestor = _load_bundled(module_name, log)
        if ingestor is not None:
            _claim(bundled, ingestor, module_name, "bundled")

    drop_ins: dict[str, tuple[str, Ingestor]] = {}
    if drop_in_dir is not None:
        for path in _iter_drop_in_files(drop_in_dir, log):
            ingestor = _load_drop_in(path, log)
            if ingestor is not None:
                _claim(drop_ins, ingestor, str(path), "drop-in")

    found = {chart: ingestor for chart, (_origin, ingestor) in bundled.items()}
    for chart, (origin, ingestor) in drop_ins.items():
        if chart in bundled:
            log.info(
                f"drop-in ingestor {origin} overrides bundled "
                f"{bundled[chart][0]} for chart {chart!r}"
            )
        found[chart] = ingestor
    return found
