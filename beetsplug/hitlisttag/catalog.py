"""Song catalog: cross-chart song identity and the durable curation store.

The catalog is one machine-written file, ``catalog.json`` in the dataset
directory, holding only the songs that carry a decision. A catalog song has
a display name, ``aliases`` (spellings that resolve to it, matched through
``match_key``) and ``links`` (the raw songs, per chart, whose placements are
its own). A link records the raw id and the raw name and source ids as they
were when the link was made; ``bind_links`` re-binds a link whose raw id is
gone after a re-acquisition, by source ids and then by name, and reports a
link it cannot re-bind rather than guessing.

The raw per-hitlist files are never the store: they are rewritten by every
``chartsacquire`` run. What survives is this file.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .dataset import HitlistData, _id_sort_key, _iter_json_files
from .lookup import LookupResult, Placement, SongLookupIndex, match_key

FORMAT_VERSION = 1
FILE_NAME = "catalog.json"


class CatalogError(Exception):
    """The catalog file is malformed or cannot be used. The message names it."""


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass
class Alias:
    """A spelling that resolves to a catalog song."""

    artist: str
    title: str

    def key(self) -> tuple[str, str]:
        return (match_key(self.artist), match_key(self.title))


@dataclass
class Link:
    """A catalog song's claim on one raw song: its placements are mine.

    ``artist``, ``title`` and ``source_ids`` are the raw song's as recorded
    when the link was made; re-binding updates them to the live values.
    """

    chart: str
    song: str
    artist: str
    title: str
    source_ids: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _is_text(self.chart) or not _is_text(self.song):
            raise ValueError("a link needs a non-empty 'chart' and 'song'")

    def key(self) -> tuple[str, str]:
        return (match_key(self.artist), match_key(self.title))


@dataclass
class CatalogSong:
    id: str
    artist: str
    title: str
    aliases: list[Alias] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.links:
            raise ValueError(f"song {self.id!r} must have at least one link")
        seen: set[tuple[str, str]] = set()
        for link in self.links:
            if (link.chart, link.song) in seen:
                raise ValueError(
                    f"song {self.id!r} links {link.chart} {link.song!r} twice"
                )
            seen.add((link.chart, link.song))


@dataclass
class Catalog:
    songs: dict[str, CatalogSong]
    source: Path
    _alias_owner: dict[tuple[str, str], str] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        owners: dict[tuple[str, str], str] = {}
        for sid, song in self.songs.items():
            for alias in song.aliases:
                key = alias.key()
                if not key[0] or not key[1]:
                    continue  # normalizes to nothing: never matches, claims nothing
                if key in owners and owners[key] != sid:
                    raise ValueError(
                        f"alias {alias.artist!r} - {alias.title!r} belongs to songs "
                        f"{owners[key]!r} and {sid!r}"
                    )
                owners[key] = sid
        self._alias_owner = owners

    def alias_owner(self, key: tuple[str, str]) -> str | None:
        """The catalog song an explicit alias key resolves to, if any."""
        return self._alias_owner.get(key)


def catalog_path(dataset_dir: Path | str) -> Path:
    return Path(dataset_dir) / FILE_NAME


def read_catalog(path: Path | str, log: logging.Logger) -> Catalog:
    """Read the catalog at ``path``; a missing file is an empty catalog.

    Raises ``CatalogError`` naming the file for anything the format does not
    allow, including unknown fields (the file is the plugin's own, so an
    unknown field is a version mismatch) and a dataset file at the reserved
    path.
    """
    path = Path(path)
    if not path.exists():
        return Catalog({}, path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        raise CatalogError(f"{path}: cannot read catalog file: {err}") from err
    if not isinstance(raw, dict):
        raise CatalogError(f"{path}: top-level value must be an object")
    if "chart" in raw:
        raise CatalogError(
            f"{path}: is a dataset file (it has 'chart'); the hitlist name "
            f"'catalog' is reserved for the song catalog"
        )
    _only_fields(raw, {"catalog", "songs"}, path, "top level")
    version = raw.get("catalog")
    if isinstance(version, bool) or version != FORMAT_VERSION:
        raise CatalogError(f"{path}: 'catalog' must be {FORMAT_VERSION}")
    raw_songs = raw.get("songs")
    if not isinstance(raw_songs, dict):
        raise CatalogError(f"{path}: 'songs' must be an object")
    songs = {sid: _parse_song(sid, val, path) for sid, val in raw_songs.items()}
    try:
        return Catalog(songs, path)
    except ValueError as err:
        raise CatalogError(f"{path}: {err}") from err


def _only_fields(obj: dict, allowed: set[str], path: Path, where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise CatalogError(f"{path}: {where} has unknown field(s) {', '.join(unknown)}")


def _parse_song(sid: str, val: object, path: Path) -> CatalogSong:
    if not isinstance(val, dict):
        raise CatalogError(f"{path}: song {sid!r} must be an object")
    _only_fields(val, {"artist", "title", "aliases", "links"}, path, f"song {sid!r}")
    artist, title = val.get("artist"), val.get("title")
    if not isinstance(artist, str) or not isinstance(title, str):
        raise CatalogError(
            f"{path}: song {sid!r} must have string 'artist' and 'title'"
        )
    raw_aliases = val.get("aliases", [])
    if not isinstance(raw_aliases, list):
        raise CatalogError(f"{path}: song {sid!r} 'aliases' must be a list")
    aliases: list[Alias] = []
    for i, item in enumerate(raw_aliases):
        where = f"song {sid!r} alias {i}"
        if not isinstance(item, dict):
            raise CatalogError(f"{path}: {where} must be an object")
        _only_fields(item, {"artist", "title"}, path, where)
        if not isinstance(item.get("artist"), str) or not isinstance(
            item.get("title"), str
        ):
            raise CatalogError(f"{path}: {where} must have string 'artist' and 'title'")
        aliases.append(Alias(item["artist"], item["title"]))
    raw_links = val.get("links")
    if not isinstance(raw_links, list):
        raise CatalogError(f"{path}: song {sid!r} 'links' must be a list")
    links: list[Link] = []
    for i, item in enumerate(raw_links):
        where = f"song {sid!r} link {i}"
        if not isinstance(item, dict):
            raise CatalogError(f"{path}: {where} must be an object")
        _only_fields(
            item, {"chart", "song", "artist", "title", "source_ids"}, path, where
        )
        fields = [item.get(k) for k in ("chart", "song", "artist", "title")]
        if not all(isinstance(f, str) for f in fields):
            raise CatalogError(
                f"{path}: {where} must have string 'chart', 'song', 'artist' "
                f"and 'title'"
            )
        source_ids = item.get("source_ids", {})
        if not isinstance(source_ids, dict) or not all(
            _is_text(k) and _is_text(v) for k, v in source_ids.items()
        ):
            raise CatalogError(
                f"{path}: {where} 'source_ids' must map non-empty strings to "
                f"non-empty strings"
            )
        try:
            links.append(Link(*fields, dict(source_ids)))
        except ValueError as err:
            raise CatalogError(f"{path}: {where}: {err}") from err
    try:
        return CatalogSong(sid, artist, title, aliases, links)
    except ValueError as err:
        raise CatalogError(f"{path}: {err}") from err


def _dump_link(link: Link) -> dict:
    out: dict = {
        "chart": link.chart,
        "song": link.song,
        "artist": link.artist,
        "title": link.title,
    }
    if link.source_ids:
        out["source_ids"] = dict(sorted(link.source_ids.items()))
    return out


def _dump_song(song: CatalogSong) -> dict:
    return {
        "artist": song.artist,
        "title": song.title,
        "aliases": [{"artist": a.artist, "title": a.title} for a in song.aliases],
        "links": [_dump_link(link) for link in song.links],
    }


def dump_catalog(catalog: Catalog) -> str:
    """Serialize in the documented shape: songs by id, 2-space indent."""
    payload = {
        "catalog": FORMAT_VERSION,
        "songs": {
            sid: _dump_song(song)
            for sid, song in sorted(
                catalog.songs.items(), key=lambda item: _id_sort_key(item[0])
            )
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _shape(catalog: Catalog) -> dict:
    return {sid: _dump_song(song) for sid, song in catalog.songs.items()}


def write_catalog_file(catalog: Catalog, path: Path, log: logging.Logger) -> None:
    """Atomically replace ``path`` with ``catalog``, or leave it untouched.

    Same pattern as ``write_dataset_file``: temp file in the same directory,
    re-read with the catalog reader, compared, atomic replace. Refuses to
    overwrite a dataset file at the reserved path (``read_catalog`` raises).
    """
    path = Path(path).resolve()
    read_catalog(path, log)  # a dataset file here is a CatalogError
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.stem}.", suffix=".json.tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(dump_catalog(catalog))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        try:
            reread = read_catalog(tmp, log)
        except CatalogError as err:
            raise CatalogError(f"internal error writing the catalog: {err}") from err
        if _shape(reread) != _shape(catalog):
            raise CatalogError(
                "internal error writing the catalog: the re-read file does not "
                "match the data written"
            )
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


@dataclass
class LinkState:
    """One link's state against the live data; ``previous`` is the raw id a
    re-bound link had before."""

    song_id: str
    link: Link
    state: str  # "bound", "rebound" or "dangling"
    previous: str | None = None


@dataclass
class BindReport:
    states: dict[str, list[LinkState]] = field(default_factory=dict)

    def all(self) -> list[LinkState]:
        return [s for states in self.states.values() for s in states]

    def with_state(self, state: str) -> list[LinkState]:
        return [s for s in self.all() if s.state == state]

    @property
    def changed(self) -> bool:
        return any(s.state == "rebound" for s in self.all())


class _LiveChart:
    """What binding needs from one chart's live data: which raw songs an
    edition cites, their source ids (when every citing entry agrees) and
    their names by match key."""

    def __init__(self, data: HitlistData):
        self.songs = data.songs
        self.cited: set[str] = set()
        ids: dict[str, dict[str, str] | None] = {}
        for edition in data.editions:
            for entry in edition.entries:
                for song in entry.songs:
                    self.cited.add(song.id)
                    if song.id not in ids:
                        ids[song.id] = dict(entry.source_ids)
                    elif ids[song.id] != entry.source_ids:
                        ids[song.id] = None
        self.source_ids: dict[str, dict[str, str]] = {
            sid: mapping for sid, mapping in ids.items() if mapping
        }
        self.by_key: dict[tuple[str, str], list[str]] = {}
        for sid in sorted(self.cited, key=_id_sort_key):
            key = (match_key(self.songs[sid].artist), match_key(self.songs[sid].title))
            if key[0] and key[1]:
                self.by_key.setdefault(key, []).append(sid)

    def by_source_ids(self, recorded: dict[str, str]) -> list[str]:
        return [
            sid
            for sid, live in self.source_ids.items()
            if all(live.get(k) == v for k, v in recorded.items())
        ]

    def is_recorded_song(self, link: Link) -> bool:
        """The live raw song at the link's id is still the song the link
        recorded: its source ids carry the recorded ones, or, with none
        recorded, its name has the recorded name's match key."""
        if link.source_ids:
            live = self.source_ids.get(link.song, {})
            return all(live.get(k) == v for k, v in link.source_ids.items())
        raw = self.songs[link.song]
        return (match_key(raw.artist), match_key(raw.title)) == link.key()


def bind_links(
    catalog: Catalog, datasets: list[HitlistData], log: logging.Logger
) -> BindReport:
    """Check every link against the live data; re-bind in place where exactly
    one live raw song carries the recorded source ids, else exactly one has
    the recorded name's match key. Two candidates at a step is dangling. A
    cited id that now holds another song (ids re-minted from scratch) is
    re-bound like a missing one, never kept."""
    live = {data.chart: _LiveChart(data) for data in datasets}
    report = BindReport()
    for sid, song in catalog.songs.items():
        states = report.states.setdefault(sid, [])
        for link in song.links:
            chart = live.get(link.chart)
            if chart is None:
                states.append(LinkState(sid, link, "dangling"))
                continue
            if link.song in chart.cited and chart.is_recorded_song(link):
                states.append(LinkState(sid, link, "bound"))
                continue
            found = chart.by_source_ids(link.source_ids) if link.source_ids else []
            if not found:
                found = chart.by_key.get(link.key(), [])
            if len(found) != 1:
                states.append(LinkState(sid, link, "dangling"))
                continue
            previous, new_id = link.song, found[0]
            raw = chart.songs[new_id]
            link.song = new_id
            link.artist, link.title = raw.artist, raw.title
            link.source_ids = dict(chart.source_ids.get(new_id, {}))
            log.info(
                f"catalog: song {sid} re-bound {link.chart} {previous} -> {new_id} "
                f"({raw.artist} - {raw.title})"
            )
            states.append(LinkState(sid, link, "rebound", previous))
    return report


@dataclass
class ImplicitPair:
    """Two singly-linked raw songs with one match key under different catalog
    songs: an unmerged pair. Neither implies an alias; ``check`` reports it."""

    key: tuple[str, str]
    raws: list[tuple[str, str]]
    songs: list[str]
    artist: str
    title: str


def placements_by_song(
    datasets: list[HitlistData],
) -> dict[tuple[str, str], list[Placement]]:
    out: dict[tuple[str, str], list[Placement]] = {}
    for data in datasets:
        for edition in data.editions:
            for entry in edition.entries:
                for song in entry.songs:
                    out.setdefault((data.chart, song.id), []).append(
                        Placement(
                            axes=edition.axes,
                            position=entry.position,
                            size=edition.size,
                        )
                    )
    return out


class CatalogIndex:
    """Resolution through the catalog, with ``SongLookupIndex``'s contract.

    Order for a track's match key: an explicit alias; else the live name of
    a raw song that links to exactly one catalog song (an implicit alias);
    else the raw lookup.
    """

    def __init__(
        self,
        raw: SongLookupIndex,
        catalog: Catalog,
        placements: dict[tuple[str, str], list[Placement]],
        names: dict[tuple[str, str], tuple[str, str]],
        report: BindReport,
    ):
        self._raw = raw
        self._catalog = catalog
        self._placements = placements
        self.bind_report = report
        linkers: dict[tuple[str, str], set[str]] = {}
        for sid, states in report.states.items():
            for state in states:
                if state.state != "dangling":
                    linkers.setdefault((state.link.chart, state.link.song), set()).add(
                        sid
                    )
        implicit: dict[tuple[str, str], tuple[set[str], list[tuple[str, str]]]] = {}
        for raw_id, sids in linkers.items():
            if len(sids) != 1 or raw_id not in names:
                continue
            artist, title = names[raw_id]
            key = (match_key(artist), match_key(title))
            if not key[0] or not key[1]:
                continue
            owners, raws = implicit.setdefault(key, (set(), []))
            owners.update(sids)
            raws.append(raw_id)
        self._implicit = {
            k: next(iter(v[0])) for k, v in implicit.items() if len(v[0]) == 1
        }
        pairs = []
        for key, (owners, unsorted) in implicit.items():
            if len(owners) > 1:
                raws = sorted(unsorted)
                pairs.append(ImplicitPair(key, raws, sorted(owners), *names[raws[0]]))
        self.implicit_pairs = sorted(pairs, key=lambda pair: pair.key)

    @classmethod
    def from_datasets(
        cls, datasets: list[HitlistData], catalog: Catalog, log: logging.Logger
    ) -> CatalogIndex:
        raw = SongLookupIndex.from_datasets(datasets, log)
        report = bind_links(catalog, datasets, log)
        names = {
            (data.chart, song.id): (song.artist, song.title)
            for data in datasets
            for song in data.songs.values()
        }
        return cls(raw, catalog, placements_by_song(datasets), names, report)

    def lookup(self, artist: str, title: str) -> LookupResult:
        key = (match_key(artist), match_key(title))
        if not key[0] or not key[1]:
            return LookupResult(normalized=None, placements={}, ambiguous_charts=set())
        sid = self._catalog.alias_owner(key)
        if sid is None:
            sid = self._implicit.get(key)
        if sid is None:
            return self._raw.lookup(artist, title)
        placements: dict[str, list[Placement]] = {}
        unbound: set[str] = set()
        for state in self.bind_report.states.get(sid, []):
            link = state.link
            if state.state == "dangling":
                unbound.add(link.chart)
                continue
            placements.setdefault(link.chart, []).extend(
                self._placements.get((link.chart, link.song), [])
            )
        return LookupResult(
            normalized=key,
            placements=placements,
            ambiguous_charts=set(),
            unbound_charts=unbound,
        )


def _count(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


@dataclass
class CheckResult:
    lines: list[str]
    problems: int
    changed: bool


def check_catalog(index: CatalogIndex, catalog: Catalog) -> CheckResult:
    """The ``chartscatalog check`` report over an index built from ``catalog``.

    ``problems`` counts dangling links and implicit alias pairs; ``changed``
    says a re-bind altered a link, so the caller writes the catalog back.
    """
    report = index.bind_report
    bound = report.with_state("bound")
    rebound = report.with_state("rebound")
    dangling = report.with_state("dangling")
    songs = catalog.songs.values()
    aliases = sum(len(s.aliases) for s in songs)
    links = sum(len(s.links) for s in songs)
    lines = [
        f"catalog: {_count(len(catalog.songs), 'song')}, "
        f"{_count(aliases, 'alias', 'aliases')}, {_count(links, 'link')}",
        f"links: {len(bound)} bound, {len(rebound)} re-bound, {len(dangling)} dangling",
    ]
    for state in rebound:
        song = catalog.songs[state.song_id]
        lines.append(
            f"re-bound: song {state.song_id} ({song.artist} - {song.title}) "
            f"{state.link.chart} {state.previous} -> {state.link.song}"
        )
    for state in dangling:
        song = catalog.songs[state.song_id]
        lines.append(
            f"dangling: song {state.song_id} ({song.artist} - {song.title}) "
            f"{state.link.chart} {state.link.song} "
            f"(recorded: {state.link.artist} - {state.link.title})"
        )
    for pair in index.implicit_pairs:
        raws = " and ".join(f"{chart} {rid}" for chart, rid in pair.raws)
        lines.append(
            f"implicit alias pair: {raws} ({pair.artist} - {pair.title}) "
            f"link different songs"
        )
    return CheckResult(lines, len(dangling) + len(index.implicit_pairs), bool(rebound))


def stray_catalog_files(dataset_dir: Path | str, log: logging.Logger) -> list[Path]:
    """Catalog-shaped ``*.json`` files under ``dataset_dir`` other than the one
    at the reserved path; the reader skips them, so they are never used."""
    dataset_dir = Path(dataset_dir)
    reserved = catalog_path(dataset_dir).resolve()
    strays: list[Path] = []
    for path in _iter_json_files(dataset_dir, log):
        if path.resolve() == reserved:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue  # the dataset reader reports unreadable files
        if isinstance(raw, dict) and "catalog" in raw and "chart" not in raw:
            strays.append(path)
    return strays
