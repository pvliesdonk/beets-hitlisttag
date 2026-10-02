"""Bundled ingestor for the NPO Radio 2 Top 2000.

Source: the consolidated table on Dutch Wikipedia, *Lijst van Radio 2-Top
2000's*, fetched as raw wikitext in one request. Every edition since 1999
is a column of that table; a row is one song with its position per year.

The page is editor-normalised (one spelling per song across all years), so
what this ingestor yields is Wikipedia's spelling, not the broadcaster's;
the research spike behind the source choice is issue #98. Partial editions
do not exist for the Top 2000: a year column that does not hold exactly
positions 1..2000 is Wikipedia mid-edit, is left out of ``editions()`` with
a warning, and is refused by ``fetch()``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

import requests

from beetsplug.hitlisttag.ingest import (
    AcquiredEdition,
    EditionRef,
    IngestError,
    RawEntry,
    RawSong,
)

SIZE = 2000
"""Positions per edition; the chart's size by definition."""

_ABSENT = {"", "—", "×"}
"""Position cells meaning "not listed" / "not yet released"."""

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Row:
    artist: str
    title: str
    positions: Mapping[int, int]


@dataclass(frozen=True)
class Table:
    years: tuple[int, ...]
    rows: tuple[Row, ...]


def _layout_error(detail: str) -> IngestError:
    return IngestError(f"Top 2000 page layout changed: {detail}")


def _decode_year(cell: str) -> int:
    """``99`` -> 1999, ``00``..``98`` -> 2000+; anything else is a layout change."""
    cell = cell.strip()
    if not re.fullmatch(r"\d{2}", cell):
        raise _layout_error(f"year column {cell!r} is not two digits")
    n = int(cell)
    return 1900 + n if n == 99 else 2000 + n


_REF = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.S)
_TAG = re.compile(r"<[^>]+>")
_SORTNAAM = re.compile(r"\{\{SortNaam\|([^|}]*)\|([^}]*)\}\}")
_ABBR = re.compile(r"\{\{Abbr\|([^|}]*)\|[^}]*\}\}")
_LINK = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]")
_ATTR = re.compile(r'^\s*align="[a-z]+"\|')


def _clean(cell: str) -> str:
    """Reduce a wikitext cell to its visible text.

    Known templates are expanded; an unknown one is left in place so that
    ``parse_table`` can reject the row by name rather than mangle it.
    """
    cell = _REF.sub("", cell)
    cell = _TAG.sub("", cell)
    cell = _SORTNAAM.sub(lambda m: f"{m.group(1)} {m.group(2)}", cell)
    cell = _ABBR.sub(lambda m: m.group(1), cell)
    cell = _LINK.sub(lambda m: m.group(1), cell)
    cell = _ATTR.sub("", cell)
    return " ".join(cell.split())


def parse_table(wikitext: str) -> Table:
    """Parse the consolidated Top 2000 wikitable.

    Raises ``IngestError`` when the header is missing or malformed, when an
    artist or title still contains a template after cleaning, or when a
    position cell is neither a number nor an absent marker.
    """
    lines = [line.rstrip("\r") for line in wikitext.lstrip("﻿").split("\n")]
    header_index = next(
        (
            i
            for i, line in enumerate(lines)
            if line.startswith("!") and "Artiest" in line and "Titel" in line
        ),
        None,
    )
    if header_index is None:
        raise _layout_error("no header row with 'Artiest' and 'Titel' found")
    header = [_clean(c) for c in lines[header_index].lstrip("!").split("||")]
    years = tuple(_decode_year(c) for c in header[4:])
    if not years:
        raise _layout_error("header has no year columns")

    rows: list[Row] = []
    for line in lines[header_index + 1 :]:
        if line.startswith("|}"):
            break
        if not line.startswith("|") or line.startswith("|-"):
            continue
        cells = line[1:].split("||")
        if len(cells) < 4 + len(years):
            continue  # spacer or caption row, not data
        artist, title = _clean(cells[0]), _clean(cells[1])
        for text in (artist, title):
            if "{{" in text:
                raise IngestError(
                    f"row {artist!r} / {title!r} contains an unknown template"
                )
        positions: dict[int, int] = {}
        for year, raw in zip(years, cells[4 : 4 + len(years)], strict=True):
            value = _clean(raw)
            if value in _ABSENT:
                continue
            if not value.isdigit():
                raise IngestError(
                    f"row {artist!r} / {title!r}: position {value!r} for {year} "
                    "is not a number"
                )
            position = int(value)
            if position > SIZE:
                raise IngestError(
                    f"row {artist!r} / {title!r}: position {position} for {year} "
                    f"exceeds {SIZE}"
                )
            positions[year] = position
        rows.append(Row(artist, title, positions))
    return Table(years, tuple(rows))


class Top2000Ingestor:
    """The ``top2000`` ingestor over Wikipedia's consolidated table.

    One page fetch per instance, on first use; ``fetch_text`` is injectable
    so tests never touch the network.
    """

    chart = "top2000"
    axes = ("year",)

    def __init__(self, fetch_text: Callable[[], str] | None = None) -> None:
        self._fetch_text = fetch_text or _download_page
        self._cached: Table | None = None
        self._complete: tuple[int, ...] | None = None

    def _table(self) -> Table:
        if self._cached is None:
            try:
                text = self._fetch_text()
            except requests.RequestException as err:
                raise IngestError(f"cannot fetch the Top 2000 page: {err}") from err
            self._cached = parse_table(text)
        return self._cached

    def _complete_years(self) -> tuple[int, ...]:
        """Years whose positions are exactly 1..SIZE; others warned about once."""
        if self._complete is None:
            table = self._table()
            complete: list[int] = []
            for year in table.years:
                found = [
                    row.positions[year] for row in table.rows if year in row.positions
                ]
                if sorted(found) == list(range(1, SIZE + 1)):
                    complete.append(year)
                else:
                    _log.warning(
                        f"Top 2000 {year} is not a complete edition on the source: "
                        f"{len(found)} positions, {len(set(found))} distinct"
                    )
            self._complete = tuple(complete)
        return self._complete

    def editions(self) -> Iterable[EditionRef]:
        return [EditionRef({"year": year}) for year in self._complete_years()]

    def fetch(self, ref: EditionRef) -> AcquiredEdition:
        year = ref.axes["year"]
        complete = self._complete_years()
        if year not in complete:
            raise IngestError(
                f"Top 2000 {year} is not a complete edition on the source; "
                f"available: {', '.join(str(y) for y in complete)}"
            )
        entries = sorted(
            (
                RawEntry(row.positions[year], (RawSong(row.artist, row.title),))
                for row in self._table().rows
                if year in row.positions
            ),
            key=lambda entry: entry.position,
        )
        return AcquiredEdition(ref, SIZE, tuple(entries))


def _download_page() -> str:  # replaced by the real fetcher in the next task
    raise IngestError("cannot fetch the Top 2000 page: no fetcher configured")
