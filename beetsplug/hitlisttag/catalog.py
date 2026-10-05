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

from .dataset import _id_sort_key
from .lookup import match_key

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
