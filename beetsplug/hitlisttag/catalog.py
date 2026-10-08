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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from .dataset import HitlistData, _id_sort_key, _is_numeric_id, _iter_json_files
from .lookup import LookupResult, Placement, SongLookupIndex, match_key

FORMAT_VERSION = 1
FILE_NAME = "catalog.json"


class CatalogError(Exception):
    """The catalog file is malformed or cannot be used. The message names it."""


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
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

    The one mutable part of a catalog: ``bind_links`` updates a link in place
    when it re-binds, which touches no alias and never duplicates a link
    within a song. Every other change goes through ``Catalog.with_song``,
    ``Catalog.new_song`` and ``Catalog.without_song``.
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


@dataclass(frozen=True)
class CatalogSong:
    """A song's identity in the catalog. Its name, aliases and set of links
    can't change; ``aliases`` and ``links`` are stored as tuples (lists are
    accepted and converted). A ``Link`` itself is the one in-place update."""

    id: str
    artist: str
    title: str
    aliases: tuple[Alias, ...] = ()
    links: tuple[Link, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "aliases", tuple(self.aliases))
        object.__setattr__(self, "links", tuple(self.links))
        if not self.links:
            raise ValueError(f"song {self.id!r} must have at least one link")
        seen: set[tuple[str, str]] = set()
        for link in self.links:
            if (link.chart, link.song) in seen:
                raise ValueError(
                    f"song {self.id!r} links {link.chart} {link.song!r} twice"
                )
            seen.add((link.chart, link.song))


@dataclass(frozen=True)
class Catalog:
    """The catalog's songs by id. Its songs and their aliases can't change:
    ``songs`` is a read-only view of the catalog's own copy, and a change
    builds a new catalog through ``with_song``, ``new_song`` or
    ``without_song``, which re-run every check here, so the alias index can
    never go stale. The new catalog shares its song objects with the old one,
    so a ``Link`` updated in place (see ``Link``) is updated in both.

    ``next_id`` is the id the next new song takes; it only grows, so an id
    once dropped never names another song. A raw song is linked by at most
    one catalog song: a published entry crediting several songs is split when
    the chart is acquired (#183), never in the catalog."""

    songs: Mapping[str, CatalogSong]
    source: Path
    next_id: int | None = None
    _alias_owner: dict[tuple[str, str], str] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        songs = dict(self.songs)
        for key, song in songs.items():
            if song.id != key:
                raise ValueError(f"key {key!r} holds song {song.id!r}")
        highest = max((int(sid) for sid in songs if _is_numeric_id(sid)), default=0)
        next_id = highest + 1 if self.next_id is None else self.next_id
        if (
            isinstance(next_id, bool)
            or not isinstance(next_id, int)
            or next_id <= highest
            or next_id < 1
        ):
            raise ValueError(
                f"next_id {self.next_id!r} must be a whole number above every "
                f"numeric song id ({highest})"
            )
        linkers: dict[tuple[str, str], str] = {}
        for sid, song in songs.items():
            for link in song.links:
                raw = (link.chart, link.song)
                if raw in linkers and linkers[raw] != sid:
                    raise ValueError(
                        f"{link.chart} {link.song!r} is linked by songs "
                        f"{linkers[raw]!r} and {sid!r}"
                    )
                linkers[raw] = sid
        owners: dict[tuple[str, str], str] = {}
        for sid, song in songs.items():
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
        object.__setattr__(self, "songs", MappingProxyType(songs))
        object.__setattr__(self, "next_id", next_id)
        object.__setattr__(self, "_alias_owner", owners)

    def alias_owner(self, key: tuple[str, str]) -> str | None:
        """The catalog song an explicit alias key resolves to, if any."""
        return self._alias_owner.get(key)

    def link_owner(self, chart: str, song: str) -> str | None:
        """The catalog song linking raw song ``song`` of ``chart``, if any.
        Reads the links as they are now, so it sees re-binds."""
        for sid, catalog_song in self.songs.items():
            if any((ln.chart, ln.song) == (chart, song) for ln in catalog_song.links):
                return sid
        return None

    def with_song(self, song: CatalogSong) -> Catalog:
        """A new catalog with ``song`` replacing the one with its id; raises
        ``ValueError`` (and changes nothing) if it breaks a rule or names no
        song here (a new song takes its id from ``new_song``)."""
        if song.id not in self.songs:
            raise ValueError(
                f"no song {song.id!r} in the catalog; a new song takes its id "
                f"from new_song"
            )
        return Catalog({**self.songs, song.id: song}, self.source, self.next_id)

    def new_song(
        self,
        artist: str,
        title: str,
        aliases: Iterable[Alias] = (),
        links: Iterable[Link] = (),
    ) -> tuple[Catalog, str]:
        """A new catalog holding a new song under ``next_id``, and its id;
        raises ``ValueError`` (and changes nothing) if it breaks a rule."""
        sid = str(self.next_id)
        song = CatalogSong(sid, artist, title, tuple(aliases), tuple(links))
        return Catalog({**self.songs, sid: song}, self.source, self.next_id + 1), sid

    def without_song(self, song_id: str) -> Catalog:
        """A new catalog without song ``song_id``; its id is not reused."""
        if song_id not in self.songs:
            raise ValueError(f"no song {song_id!r} in the catalog")
        songs = {sid: s for sid, s in self.songs.items() if sid != song_id}
        return Catalog(songs, self.source, self.next_id)


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
    _only_fields(raw, {"catalog", "next_id", "songs"}, path, "top level")
    version = raw.get("catalog")
    if isinstance(version, bool) or version != FORMAT_VERSION:
        raise CatalogError(f"{path}: 'catalog' must be {FORMAT_VERSION}")
    raw_songs = raw.get("songs")
    if not isinstance(raw_songs, dict):
        raise CatalogError(f"{path}: 'songs' must be an object")
    songs = {sid: _parse_song(sid, val, path) for sid, val in raw_songs.items()}
    if "next_id" in raw and raw["next_id"] is None:
        raise CatalogError(f"{path}: 'next_id' must be a whole number, not null")
    try:
        return Catalog(songs, path, raw.get("next_id"))
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
        return CatalogSong(sid, artist, title, tuple(aliases), tuple(links))
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
        "next_id": catalog.next_id,
        "songs": {
            sid: _dump_song(song)
            for sid, song in sorted(
                catalog.songs.items(), key=lambda item: _id_sort_key(item[0])
            )
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _shape(catalog: Catalog) -> dict:
    return {
        "next_id": catalog.next_id,
        "songs": {sid: _dump_song(song) for sid, song in catalog.songs.items()},
    }


def write_catalog_file(catalog: Catalog, path: Path, log: logging.Logger) -> None:
    """Atomically replace ``path`` with ``catalog``, or leave it untouched.

    Same pattern as ``write_dataset_file``: temp file in the same directory,
    re-read with the catalog reader, compared, atomic replace. First reads
    the existing file, so it refuses to overwrite a dataset file at the
    reserved path or any other malformed catalog (``read_catalog`` raises
    ``CatalogError``). A filesystem failure is a ``CatalogError`` naming the
    path, and the existing file is left as it was.
    """
    path = Path(path).resolve()
    read_catalog(path, log)  # a dataset file or a malformed catalog is refused
    tmp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.stem}.", suffix=".json.tmp"
        )
        tmp = Path(tmp_name)
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
    except OSError as err:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        raise CatalogError(f"cannot write {path}: {err}") from err
    except BaseException:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        raise


@dataclass
class LinkState:
    """One link's state against the live data; ``previous`` is the raw id a
    re-bound link had before; for a link left dangling because its only
    candidate is another catalog song's raw song (#173), ``candidate`` is
    that raw id and ``held_by`` the song linking it."""

    song_id: str
    link: Link
    state: str  # "bound", "rebound" or "dangling"
    previous: str | None = None
    held_by: str | None = None
    candidate: str | None = None


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


def _carries(live: Mapping[str, str], recorded: Mapping[str, str]) -> bool:
    """Live ids carry recorded ones when they share a key and every shared
    key has the same value. No shared key is not agreement (#175)."""
    shared = recorded.keys() & live.keys()
    return bool(shared) and all(live[key] == recorded[key] for key in shared)


class LiveChart:
    """What binding needs from one chart's live data: which raw songs an
    edition cites, their source ids (the keys, with their values, on which
    every citing entry agrees) and their names by match key."""

    def __init__(self, data: HitlistData):
        self.chart = data.chart
        self.songs = data.songs
        self.cited: set[str] = set()
        entry_ids: dict[str, list[dict[str, str]]] = {}
        # raw id -> (axes, position) per citing entry, for find and show
        self.placements: dict[str, list[tuple[dict[str, int], int]]] = {}
        for edition in data.editions:
            for entry in edition.entries:
                for song in entry.songs:
                    self.cited.add(song.id)
                    entry_ids.setdefault(song.id, []).append(entry.source_ids)
                    self.placements.setdefault(song.id, []).append(
                        (dict(edition.axes), entry.position)
                    )
        self.source_ids: dict[str, dict[str, str]] = {}
        for sid, mappings in entry_ids.items():
            agreed = {
                key: value
                for key, value in mappings[0].items()
                if all(other.get(key) == value for other in mappings[1:])
            }
            if agreed:
                self.source_ids[sid] = agreed
        self.by_key: dict[tuple[str, str], list[str]] = {}
        for sid in sorted(self.cited, key=_id_sort_key):
            key = self.name_key(sid)
            if key[0] and key[1]:
                self.by_key.setdefault(key, []).append(sid)

    def by_source_ids(self, recorded: dict[str, str]) -> list[str]:
        return [
            sid for sid, live in self.source_ids.items() if _carries(live, recorded)
        ]

    def is_recorded_song(self, link: Link) -> bool:
        """The live raw song at the link's id is still the song the link
        recorded. Where the link's ids and the live ones share a key, they
        must agree on every shared key (#175: a subtitle id that changed with
        a re-entry no longer counts), and where another live cited raw song
        carries them too (a title id shared by cover versions), its name has
        the recorded name's match key as well. With no shared key, the name
        alone decides."""
        raw = self.songs[link.song]
        name_matches = (match_key(raw.artist), match_key(raw.title)) == link.key()
        live = self.source_ids.get(link.song, {})
        if not link.source_ids.keys() & live.keys():
            return name_matches
        if not _carries(live, link.source_ids):
            return False
        return name_matches or len(self.by_source_ids(link.source_ids)) == 1

    def link(self, sid: str) -> Link:
        """A link to raw song ``sid`` as it is now: its published name and the
        source ids every entry citing it agrees on."""
        raw = self.songs[sid]
        return Link(
            self.chart, sid, raw.artist, raw.title, dict(self.source_ids.get(sid, {}))
        )

    def name_key(self, sid: str) -> tuple[str, str]:
        raw = self.songs[sid]
        return (match_key(raw.artist), match_key(raw.title))


def live_charts(datasets: list[HitlistData]) -> dict[str, LiveChart]:
    """Each chart's live data, by chart name."""
    return {data.chart: LiveChart(data) for data in datasets}


@dataclass
class _Move:
    """One link's outcome while binding: ``bound``, ``dangling``, or a
    ``move`` to ``target``; a refused move is ``dangling`` with its
    ``target`` kept and the song whose link holds it in ``holder``."""

    sid: str
    link: Link
    state: str
    target: str | None = None
    holder: str | None = None


def bind_links(
    catalog: Catalog, datasets: list[HitlistData], log: logging.Logger
) -> BindReport:
    """Check every link against the live data; re-bind in place where exactly
    one live raw song carries the recorded source ids (several are narrowed by
    the recorded name's match key), else exactly one has the recorded name's
    match key. Two candidates at a step is dangling. A cited id that now holds
    another song (ids re-minted from scratch) is re-bound like a missing one,
    never kept.

    Moves are decided together, so catalog order never matters: a move is
    refused (the link dangles where it is) when its target is held by a link
    that stays, bound or dangling, or is claimed by an earlier move, of this
    catalog song or another (#173); refusing one can refuse another, so this
    repeats until nothing changes. Then the moves are applied at once, so two
    links trading ids (a from-scratch re-acquisition listing songs in another
    order) both re-bind."""
    live = live_charts(datasets)
    moves: list[_Move] = []
    for sid, song in catalog.songs.items():
        for link in song.links:
            chart = live.get(link.chart)
            if chart is None:
                moves.append(_Move(sid, link, "dangling"))
                continue
            if link.song in chart.cited and chart.is_recorded_song(link):
                moves.append(_Move(sid, link, "bound"))
                continue
            found = chart.by_source_ids(link.source_ids) if link.source_ids else []
            if len(found) > 1:
                found = [c for c in found if chart.name_key(c) == link.key()]
            elif not found:
                found = chart.by_key.get(link.key(), [])
            if len(found) != 1:
                moves.append(_Move(sid, link, "dangling"))
                continue
            moves.append(_Move(sid, link, "move", found[0]))
    refused = True
    while refused:
        refused = False
        staying = {(m.link.chart, m.link.song): m for m in moves if m.state != "move"}
        claimed: dict[tuple[str, str], _Move] = {}
        for move in moves:
            if move.state != "move":
                continue
            key = (move.link.chart, move.target)
            other = staying.get(key) or claimed.get(key)
            if other is not None:
                move.state, move.holder = "dangling", other.sid
                refused = True
                continue
            claimed[key] = move
    report = BindReport()
    for move in moves:
        sid, link = move.sid, move.link
        states = report.states.setdefault(sid, [])
        if move.state == "bound":
            states.append(LinkState(sid, link, "bound"))
        elif move.state == "move":
            chart = live[link.chart]
            previous, new_id = link.song, move.target
            raw = chart.songs[new_id]
            link.song = new_id
            link.artist, link.title = raw.artist, raw.title
            link.source_ids = dict(chart.source_ids.get(new_id, {}))
            log.info(
                f"catalog: song {sid} re-bound {link.chart} {previous} -> {new_id} "
                f"({raw.artist} - {raw.title})"
            )
            states.append(LinkState(sid, link, "rebound", previous))
        elif move.holder == sid:
            log.info(
                f"catalog: song {sid} link {link.chart} {link.song} left "
                f"dangling: {move.target} is already linked by this song"
            )
            states.append(LinkState(sid, link, "dangling"))
        elif move.holder is not None:
            log.info(
                f"catalog: song {sid} link {link.chart} {link.song} left "
                f"dangling: {move.target} is linked by song {move.holder}"
            )
            states.append(
                LinkState(
                    sid, link, "dangling", held_by=move.holder, candidate=move.target
                )
            )
        else:
            states.append(LinkState(sid, link, "dangling"))
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
    a raw song a catalog song links (an implicit alias);
    else the raw lookup.

    A resolved catalog song's links decide the charts they name, a dangling
    link included. Every other chart resolves by name over every name the
    song knows (the track's spelling, the display name, the aliases, the
    links' recorded names and their raw songs' live names), skipping raw
    songs any catalog song links; several distinct raw songs matching in one
    chart make that chart ambiguous.
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
        self._names = names
        self.bind_report = report
        linkers: dict[tuple[str, str], str] = {}
        for sid, states in report.states.items():
            for state in states:
                if state.state != "dangling":
                    linkers[(state.link.chart, state.link.song)] = sid
        self._claimed = set(linkers)
        implicit: dict[tuple[str, str], tuple[set[str], list[tuple[str, str]]]] = {}
        for raw_id, sid in linkers.items():
            if raw_id not in names:
                continue
            artist, title = names[raw_id]
            key = (match_key(artist), match_key(title))
            if not key[0] or not key[1]:
                continue
            owners, raws = implicit.setdefault(key, (set(), []))
            owners.add(sid)
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
        found = self._fallback(sid, {key})
        ambiguous: set[str] = set()
        for chart, songs in found.items():
            if len(songs) == 1:
                (chart_placements,) = songs.values()
                placements[chart] = list(chart_placements)
            else:
                ambiguous.add(chart)
        return LookupResult(
            normalized=key,
            placements=placements,
            ambiguous_charts=ambiguous,
            unbound_charts=unbound,
        )

    def _known_keys(self, sid: str) -> set[tuple[str, str]]:
        """Every name catalog song ``sid`` knows, as match keys."""
        song = self._catalog.songs[sid]
        names = [(song.artist, song.title)]
        names += [(alias.artist, alias.title) for alias in song.aliases]
        for state in self.bind_report.states.get(sid, []):
            names.append((state.link.artist, state.link.title))
            live = self._names.get((state.link.chart, state.link.song))
            if state.state != "dangling" and live is not None:
                names.append(live)
        keys = {(match_key(artist), match_key(title)) for artist, title in names}
        return {key for key in keys if key[0] and key[1]}

    def _fallback(
        self, sid: str, extra: set[tuple[str, str]]
    ) -> dict[str, dict[str, list[Placement]]]:
        """Raw songs matching catalog song ``sid`` by name in the charts it
        does not link: chart -> raw song id -> placements. Raw songs any
        catalog song links are skipped."""
        linked = {state.link.chart for state in self.bind_report.states.get(sid, [])}
        found: dict[str, dict[str, list[Placement]]] = {}
        for key in self._known_keys(sid) | extra:
            for chart, songs in self._raw.songs_for(key).items():
                if chart in linked:
                    continue
                for raw_id, chart_placements in songs.items():
                    if (chart, raw_id) not in self._claimed:
                        found.setdefault(chart, {})[raw_id] = chart_placements
        return found

    def ambiguous_fallbacks(self) -> list[tuple[str, str, list[tuple[str, str, str]]]]:
        """Catalog songs whose own names match several raw songs in a chart
        they do not link: (song id, chart, [(raw id, artist, title)]), by song
        id, then chart, then raw id."""
        out = []
        for sid in sorted(self._catalog.songs, key=_id_sort_key):
            for chart, songs in sorted(self._fallback(sid, set()).items()):
                if len(songs) > 1:
                    raws = [
                        (raw_id, *self._names[(chart, raw_id)])
                        for raw_id in sorted(songs, key=_id_sort_key)
                    ]
                    out.append((sid, chart, raws))
        return out


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
        held = (
            f"; {state.link.chart} {state.candidate} is linked by @{state.held_by}"
            if state.held_by is not None
            else ""
        )
        lines.append(
            f"dangling: song {state.song_id} ({song.artist} - {song.title}) "
            f"{state.link.chart} {state.link.song} "
            f"(recorded: {state.link.artist} - {state.link.title}{held})"
        )
    for pair in index.implicit_pairs:
        raws = " and ".join(f"{chart} {rid}" for chart, rid in pair.raws)
        lines.append(
            f"implicit alias pair: {raws} ({pair.artist} - {pair.title}) "
            f"link different songs"
        )
    # Information only: these resolve as ambiguous, as the plain lookup would.
    for sid, chart, raws in index.ambiguous_fallbacks():
        song = catalog.songs[sid]
        found = ", ".join(f"{rid} ({artist} - {title})" for rid, artist, title in raws)
        lines.append(
            f"ambiguous fallback: song {sid} ({song.artist} - {song.title}) "
            f"{chart}: {found}"
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
