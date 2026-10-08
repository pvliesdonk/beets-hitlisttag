"""Curation operations on the song catalog (#162).

Each operation takes the catalog (already bound against the live data) and
returns a ``Change``: the new catalog, the lines a confirmation prompt shows,
and whether anything changed. Nothing here writes or prompts; the
``chartscatalog`` command does both, once. A rule the new catalog would
break raises ``CurationError`` before anything is written.

The policies they serve are the owner's (``docs/roadmap.md``, *What counts
as the same song*, *Variant policy*): ``merge`` makes raw songs one song
(spellings, a re-release, across charts) and, with a name, folds a remix
bundle into its base song; ``alias`` adds a spelling; ``unlink``,
``unalias`` and ``drop`` revert a decision, after which the song resolves as
it did before. Splitting a multi-song entry is acquisition's (#183).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace

from .catalog import (
    Alias,
    BindReport,
    Catalog,
    CatalogSong,
    Link,
    LiveChart,
    dump_catalog,
)
from .dataset import _id_sort_key
from .lookup import match_key


class CurationError(Exception):
    """An operation can't be applied; the message says why."""


@dataclass(frozen=True)
class Change:
    catalog: Catalog
    lines: list[str]
    changed: bool


def parse_name(text: str) -> tuple[str, str]:
    """``"Artist - Title"`` split at the first ``" - "`` (titles often hold
    one, ``Cheer - Remix``; artists rarely do)."""
    artist, sep, title = text.partition(" - ")
    if not sep or not artist.strip() or not title.strip():
        raise CurationError(f"a name is written 'Artist - Title', got {text!r}")
    return artist.strip(), title.strip()


def _count(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _label(song: CatalogSong) -> str:
    return f"@{song.id} ({song.artist} - {song.title})"


def _song(catalog: Catalog, sid: str) -> CatalogSong:
    if sid not in catalog.songs:
        raise CurationError(f"no catalog song @{sid}")
    return catalog.songs[sid]


def _raw_link(live: Mapping[str, LiveChart], chart: str, raw_id: str) -> Link:
    data = live.get(chart)
    if data is None or raw_id not in data.cited:
        raise CurationError(f"{chart}:{raw_id} is not in the current data")
    return data.link(raw_id)


@contextmanager
def _rules():
    """A catalog rule the change would break becomes a CurationError."""
    try:
        yield
    except ValueError as err:
        raise CurationError(str(err)) from err


def _change(before: Catalog, after: Catalog, lines: list[str]) -> Change:
    return Change(after, lines, dump_catalog(after) != dump_catalog(before))


def _new_alias(name: tuple[str, str]) -> Alias:
    new = Alias(*name)
    if not all(new.key()):
        raise CurationError(
            f"the name {name[0]} - {name[1]} matches nothing (it has no letters "
            f"or digits)"
        )
    return new


def _no_takeover(
    catalog: Catalog,
    live: Mapping[str, LiveChart],
    sid: str,
    new_aliases: list[Alias],
    new_links: list[Link],
) -> None:
    """An explicit alias wins over a linked raw song's name (resolution's
    order), so a new alias of song ``sid`` spelled like a raw song another
    catalog song links, or a new link of ``sid`` spelled like another song's
    alias, would take that song's tracks: refuse it."""
    keys = {alias.key(): alias for alias in new_aliases}
    for other_id, other in catalog.songs.items():
        if other_id == sid:
            continue
        for ln in other.links:
            chart = live.get(ln.chart)
            if chart is None or ln.song not in chart.cited:
                continue
            clash = keys.get(chart.name_key(ln.song))
            if clash is not None:
                raise CurationError(
                    f"{clash.artist} - {clash.title} is how {ln.chart}:{ln.song} "
                    f"is spelled, which @{other_id} links"
                )
    for ln in new_links:
        owner = catalog.alias_owner(live[ln.chart].name_key(ln.song))
        if owner is not None and owner != sid:
            raise CurationError(
                f"{ln.chart}:{ln.song} ({ln.artist} - {ln.title}) is spelled like "
                f"@{owner}'s alias"
            )


def merge(
    catalog: Catalog,
    live: Mapping[str, LiveChart],
    raws: list[tuple[str, str]],
    songs: list[str],
    name: tuple[str, str] | None = None,
) -> Change:
    """Make the named raw songs and catalog songs one catalog song.

    The first catalog song named is the target; with none, the catalog song
    already linking the first named raw song that has one, so running a
    merge again changes nothing; with none of those, a new song is minted,
    displayed as ``name`` or else as its first raw song. Other catalog songs
    named are absorbed (their links and aliases move, they are dropped). A
    raw song another catalog song links moves; a song left with no links is
    dropped and its aliases come along. ``name`` sets the display name and
    adds it as an alias: the fold of a remix bundle into its base song. A
    new alias spelled like another song's raw song, or a new link spelled
    like another song's alias, is refused (it would take that song's
    tracks).
    """
    raws = list(dict.fromkeys(raws))
    if len(raws) + len(songs) < (1 if name else 2):
        raise CurationError("merge needs two references, or one and --name")
    if len(set(songs)) != len(songs):
        raise CurationError("a catalog song is named twice")
    for sid in songs:
        _song(catalog, sid)
    links = [_raw_link(live, chart, raw_id) for chart, raw_id in raws]
    new_alias = _new_alias(name) if name else None

    if not songs:
        held = (catalog.link_owner(chart, raw_id) for chart, raw_id in raws)
        songs = [next((sid for sid in held if sid is not None), None)] if raws else []
        songs = [sid for sid in songs if sid is not None]
    target = catalog.songs[songs[0]] if songs else None
    aliases = list(target.aliases) if target else []
    target_links = list(target.links) if target else []
    work = catalog
    lines: list[str] = []
    for sid in songs[1:]:
        absorbed = work.songs[sid]
        lines.append(
            f"  absorb {_label(absorbed)}: {_count(len(absorbed.links), 'link')}, "
            f"{_count(len(absorbed.aliases), 'alias', 'aliases')}"
        )
        aliases += absorbed.aliases
        target_links += absorbed.links
        work = work.without_song(sid)
    for link in links:
        ref = f"{link.chart}:{link.song}"
        if any((ln.chart, ln.song) == (link.chart, link.song) for ln in target_links):
            lines.append(f"  {ref} already linked")
            continue
        suffix = ""
        holder = work.link_owner(link.chart, link.song)
        if holder is not None:
            held = work.songs[holder]
            remaining = tuple(
                ln
                for ln in held.links
                if (ln.chart, ln.song) != (link.chart, link.song)
            )
            if remaining:
                with _rules():
                    work = work.with_song(replace(held, links=remaining))
                suffix = f"  (moves from @{holder})"
            else:
                work = work.without_song(holder)
                aliases += held.aliases
                kept = (
                    f", its {_count(len(held.aliases), 'alias', 'aliases')} kept"
                    if held.aliases
                    else ""
                )
                suffix = f"  (moves from @{holder}; @{holder} is dropped{kept})"
        target_links.append(link)
        lines.append(f"  link {ref:<12} {link.artist} - {link.title}{suffix}")
    if new_alias is not None:
        aliases.append(new_alias)
        lines.append(f"  name {name[0]} - {name[1]} (and alias)")
    seen: set[tuple[str, str]] = set()
    distinct = []
    for a in aliases:
        if a.key() not in seen:
            seen.add(a.key())
            distinct.append(a)

    if target is None:
        display = name or (links[0].artist, links[0].title)
        with _rules():
            work, sid = work.new_song(display[0], display[1], distinct, target_links)
        header = f"merge into new song @{sid} ({display[0]} - {display[1]}):"
        target_id = sid
    else:
        display = name or (target.artist, target.title)
        song = CatalogSong(
            target.id, display[0], display[1], tuple(distinct), tuple(target_links)
        )
        with _rules():
            work = work.with_song(song)
        header = f"merge into {_label(target)}:"
        target_id = target.id
    _no_takeover(work, live, target_id, [new_alias] if new_alias else [], links)
    return _change(catalog, work, [header, *lines])


def alias(
    catalog: Catalog,
    live: Mapping[str, LiveChart],
    name: tuple[str, str],
    song: str | None = None,
    raw: tuple[str, str] | None = None,
) -> Change:
    """Add a spelling to a catalog song, or to the song a raw song belongs
    to; a raw song no catalog song links gets a new song."""
    new = _new_alias(name)
    if raw is not None:
        link = _raw_link(live, *raw)
        song = catalog.link_owner(link.chart, link.song)
        if song is None:
            with _rules():
                work, sid = catalog.new_song(link.artist, link.title, [new], [link])
            _no_takeover(work, live, sid, [new], [link])
            return _change(
                catalog,
                work,
                [
                    f"new song @{sid} ({link.artist} - {link.title}) linking "
                    f"{link.chart}:{link.song}",
                    f"  alias {name[0]} - {name[1]}",
                ],
            )
    target = _song(catalog, song)
    if any(a.key() == new.key() for a in target.aliases):
        return Change(catalog, [f"{_label(target)} already has that alias"], False)
    with _rules():
        work = catalog.with_song(replace(target, aliases=(*target.aliases, new)))
    _no_takeover(work, live, target.id, [new], [])
    return _change(
        catalog, work, [f"alias {_label(target)}:", f"  add {name[0]} - {name[1]}"]
    )


def unlink(catalog: Catalog, song: str, raw: tuple[str, str]) -> Change:
    """Take a raw song out of a catalog song; its last link drops the song."""
    target = _song(catalog, song)
    remaining = tuple(ln for ln in target.links if (ln.chart, ln.song) != raw)
    if len(remaining) == len(target.links):
        raise CurationError(f"@{song} does not link {raw[0]}:{raw[1]}")
    lines = [f"unlink {raw[0]}:{raw[1]} from {_label(target)}"]
    if remaining:
        with _rules():
            work = catalog.with_song(replace(target, links=remaining))
    else:
        work = catalog.without_song(song)
        lines.append(f"  that was its last link: @{song} is dropped")
    return _change(catalog, work, lines)


def unalias(catalog: Catalog, song: str, name: tuple[str, str]) -> Change:
    """Remove the alias with ``name``'s match key from a catalog song."""
    target = _song(catalog, song)
    key = Alias(*name).key()
    remaining = tuple(a for a in target.aliases if a.key() != key)
    if len(remaining) == len(target.aliases):
        raise CurationError(f"@{song} has no alias {name[0]} - {name[1]}")
    with _rules():
        work = catalog.with_song(replace(target, aliases=remaining))
    return _change(
        catalog, work, [f"unalias {_label(target)}:", f"  remove {name[0]} - {name[1]}"]
    )


def drop(catalog: Catalog, song: str) -> Change:
    """Remove a catalog song; its raw songs resolve as if never curated."""
    target = _song(catalog, song)
    work = catalog.without_song(song)
    return _change(
        catalog,
        work,
        [
            f"drop {_label(target)}: {_count(len(target.links), 'link')}, "
            f"{_count(len(target.aliases), 'alias', 'aliases')}"
        ],
    )


_RAW_REF = re.compile(r"([^:\s]+):(\S+)")
_SONG_REF = re.compile(r"@(\d+)")


def parse_refs(
    args: list[str], live: Mapping[str, LiveChart], charts: Iterable[str] = ()
) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Raw refs (``chart:id`` for a chart in the data), catalog song refs
    (``@id``) and the remaining words, each in argument order."""
    known = set(charts)
    raws: list[tuple[str, str]] = []
    songs: list[str] = []
    words: list[str] = []
    for arg in args:
        raw = _RAW_REF.fullmatch(arg)
        song = _SONG_REF.fullmatch(arg)
        if raw and (raw.group(1) in live or raw.group(1) in known):
            raws.append((raw.group(1), raw.group(2)))
        elif song:
            songs.append(song.group(1))
        else:
            words.append(arg)
    return raws, songs, words


@dataclass(frozen=True)
class Candidate:
    ref: str
    raw: tuple[str, str] | None
    song: str | None
    line: str


def _tokens(*texts: str) -> set[str]:
    return {token for text in texts for token in match_key(text).split()}


def run(chart: LiveChart, raw_id: str) -> str:
    """``1966–1967, 12 editions, best 3`` for a raw song's placements."""
    placements = chart.placements.get(raw_id, [])
    years = sorted({axes["year"] for axes, _ in placements if "year" in axes})
    span = (
        ""
        if not years
        else str(years[0])
        if years[0] == years[-1]
        else f"{years[0]}–{years[-1]}"
    )
    best = min(position for _, position in placements)
    parts = [span] if span else []
    parts += [_count(len(placements), "edition"), f"best {best}"]
    return ", ".join(parts)


def find(
    catalog: Catalog, live: Mapping[str, LiveChart], words: list[str]
) -> list[Candidate]:
    """Raw songs, then catalog songs, whose names hold every word (compared
    through ``match_key``, token by token)."""
    wanted = _tokens(*words)
    if not wanted:
        raise CurationError("find needs words to look for")
    owners = {
        (ln.chart, ln.song): sid
        for sid, song in catalog.songs.items()
        for ln in song.links
    }
    out: list[Candidate] = []
    for chart_name in sorted(live):
        chart = live[chart_name]
        for raw_id in sorted(chart.cited, key=_id_sort_key):
            raw = chart.songs[raw_id]
            if not wanted <= _tokens(raw.artist, raw.title):
                continue
            ref = f"{chart_name}:{raw_id}"
            owner = owners.get((chart_name, raw_id))
            owned = f"  → @{owner}" if owner else ""
            line = f"{ref:<13} {raw.artist} - {raw.title}  {run(chart, raw_id)}{owned}"
            out.append(Candidate(ref, (chart_name, raw_id), None, line))
    for sid in sorted(catalog.songs, key=_id_sort_key):
        song = catalog.songs[sid]
        names = [(song.artist, song.title)] + [
            (a.artist, a.title) for a in song.aliases
        ]
        if any(wanted <= _tokens(*n) for n in names):
            line = (
                f"{'@' + sid:<13} {song.artist} - {song.title}  "
                f"{_count(len(song.links), 'link')}, "
                f"{_count(len(song.aliases), 'alias', 'aliases')}"
            )
            out.append(Candidate("@" + sid, None, sid, line))
    return out


def describe(
    catalog: Catalog,
    live: Mapping[str, LiveChart],
    report: BindReport,
    raw: tuple[str, str] | None = None,
    song: str | None = None,
) -> list[str]:
    """``show``: a catalog song with its aliases and links' states, or a raw
    song with its run and the catalog song linking it."""
    if song is not None:
        target = _song(catalog, song)
        lines = [f"@{song} {target.artist} - {target.title}"]
        lines += [f"  alias {a.artist} - {a.title}" for a in target.aliases]
        for state in report.states.get(song, []):
            link = state.link
            how = (
                f"re-bound from {state.previous}"
                if state.state == "rebound"
                else f"dangling ({link.chart} {state.candidate} is linked by "
                f"@{state.held_by})"
                if state.held_by is not None
                else state.state
            )
            lines.append(
                f"  link {link.chart + ':' + link.song:<14} {link.artist} - "
                f"{link.title}  {how}"
            )
        return lines
    assert raw is not None
    chart_name, raw_id = raw
    chart = live.get(chart_name)
    if chart is None or raw_id not in chart.cited:
        raise CurationError(f"{chart_name}:{raw_id} is not in the current data")
    name = chart.songs[raw_id]
    owner = catalog.link_owner(chart_name, raw_id)
    return [
        f"{chart_name}:{raw_id} {name.artist} - {name.title}  {run(chart, raw_id)}",
        f"  linked by @{owner}" if owner else "  linked by no catalog song",
    ]
