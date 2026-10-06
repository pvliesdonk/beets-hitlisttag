"""Bundled ingestor for the weekly Top 40 on top40.nl.

Source: the Stichting Nederlandse Top 40's chart archive,
https://www.top40.nl/top40/<year>/week-<week>: one page per weekly chart
since 2 January 1965 (research spike #116). Weeks are numbered by the site;
every chart is dated on a Saturday, but year-end breaks make a year 51, 52
or 53 weeks long, so the weeks are listed from ``LAST_WEEK`` (a one-time
probe, 2026-10-03) and ``KNOWN_MISSING``, the latest week in ``/top40``'s
title, and for years after the table their first chart (week 1, or week 2
after a New Year break), whose "previous" link gives the year before's last
week.

The site reserves copyright and database rights over its data. Each user's
acquired copy is private and must stay unpublished; the plugin ships no chart
data.

Entries are recorded as published: the site's merged titles and artists and
" ; " double A-sides are kept as they are. Week pages shorten long names with
"..", so a shortened name is restored from the item image's title
(``restore_names``). ``source_ids`` carries the site's title id and the raw
subtitle image id, uninterpreted.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from beetsplug.hitlisttag import top40nl
from beetsplug.hitlisttag.ingest import (
    AcquiredEdition,
    EditionRef,
    IngestError,
    RawEntry,
    RawSong,
)

SIZE = 40
"""Positions per weekly chart."""

FIRST_YEAR = 1965
"""The year of the first chart, 2 January 1965 (week 1)."""

LAST_WEEK: dict[int, int] = {
    1965: 52, 1966: 53, 1967: 52, 1968: 52, 1969: 52, 1970: 52, 1971: 52,
    1972: 53, 1973: 52, 1974: 51, 1975: 52, 1976: 52, 1977: 53, 1978: 51,
    1979: 51, 1980: 51, 1981: 52, 1982: 52, 1983: 52, 1984: 51, 1985: 51,
    1986: 51, 1987: 52, 1988: 52, 1989: 51, 1990: 51, 1991: 51, 1992: 52,
    1993: 52, 1994: 52, 1995: 51, 1996: 52, 1997: 52, 1998: 52, 1999: 52,
    2000: 52, 2001: 51, 2002: 51, 2003: 52, 2004: 52, 2005: 53, 2006: 52,
    2007: 52, 2008: 52, 2009: 52, 2010: 52, 2011: 53, 2012: 51, 2013: 52,
    2014: 51, 2015: 52, 2016: 53, 2017: 52, 2018: 52, 2019: 52, 2020: 52,
    2021: 52, 2022: 53, 2023: 52, 2024: 52, 2025: 52,
}  # fmt: skip
"""The site's last week number of each completed year, probed 2026-10-03.

Calendar metadata, not chart data; it only saves fetches, since the
"previous" link on the next year's week 1 gives the same answer.
"""

KNOWN_MISSING: frozenset[tuple[int, int]] = frozenset(
    (year, 1) for year in (1982, 1983, 1988, 1993, 1994, 1997, 1998, 1999, 2000, 2005)
)
"""(year, week) pairs inside a year's range that the site does not have.

From the first full acquisition (2026-10-03): in these years the New Year
break fell in the first week, so the first chart is week 2.
"""

_LATEST = re.compile(r"<title>\s*Top 40-lijst van week (\d+), (\d{4})\b")

_log = logging.getLogger(__name__)


def restore_names(
    artist: str, title: str, image_title: str | None
) -> tuple[str, str] | None:
    """The full (artist, title) behind visible names the site shortened.

    A visible part ending in ".." is shortened; its prefix is the text
    before the "..". With nothing shortened the visible pair is returned
    as is. Otherwise ``image_title`` ("<artist> - <title>") is split at
    each " - "; a split fits when its left side equals the visible artist
    (or starts with its prefix, if shortened) and its right side the
    visible title likewise. Exactly one fit is returned; otherwise None.
    """

    def prefix(text: str) -> tuple[str, bool]:
        if text.endswith(".."):
            return text[:-2].rstrip(), True
        return text, False

    artist_prefix, artist_cut = prefix(artist)
    title_prefix, title_cut = prefix(title)
    if not artist_cut and not title_cut:
        return artist, title
    if not image_title:
        return None
    fits = []
    for match in re.finditer(" - ", image_title):
        left, right = image_title[: match.start()], image_title[match.end() :]
        left_fits = left.startswith(artist_prefix) if artist_cut else left == artist
        right_fits = right.startswith(title_prefix) if title_cut else right == title
        if left_fits and right_fits:
            fits.append((left, right))
    return fits[0] if len(fits) == 1 else None


class Top40Ingestor:
    """The ``top40`` ingestor over top40.nl's weekly chart archive.

    ``get`` fetches a URL and returns its text, or None for a 404; by
    default the ``get`` of one ``top40nl.fetcher()``, created on first use
    so a run is paced throughout and nothing is built at import.
    """

    chart = "top40"
    axes = ("year", "week")

    def __init__(self, get: Callable[[str], str | None] | None = None) -> None:
        self._get = get
        self._refs: list[EditionRef] | None = None

    def _page(self, path: str) -> str | None:
        if self._get is None:
            self._get = top40nl.fetcher().get
        return self._get(top40nl.BASE_URL + path)

    def editions(self) -> list[EditionRef]:
        if self._refs is None:
            self._refs = self._list()
        return list(self._refs)

    def _list(self) -> list[EditionRef]:
        html = self._page("/top40")
        match = _LATEST.search(html or "")
        if match is None:
            why = "not found" if html is None else "has no 'week N, YYYY' title"
            raise IngestError(f"cannot list Top 40 weeks: /top40 {why}")
        latest_week, latest_year = int(match.group(1)), int(match.group(2))
        if not 1 <= latest_week <= 53:
            raise IngestError(
                f"cannot list Top 40 weeks: /top40 shows week {latest_week} of "
                f"{latest_year}, outside 1–53"
            )
        if latest_year < max(LAST_WEEK):
            raise IngestError(
                f"cannot list Top 40 weeks: the site's latest chart ({latest_year} "
                f"week {latest_week}) is before the built-in table's last year "
                f"{max(LAST_WEEK)}"
            )
        last = dict(LAST_WEEK)
        first: dict[int, int] = {}
        for year in range(max(LAST_WEEK) + 1, latest_year + 1):
            first[year], previous_last = self._first_week(year)
            if year - 1 not in last:
                last[year - 1] = previous_last
        last[latest_year] = latest_week
        return [
            EditionRef({"year": year, "week": week})
            for year, final in last.items()
            for week in range(first.get(year, 1), final + 1)
            if (year, week) not in KNOWN_MISSING
        ]

    def _first_week(self, year: int) -> tuple[int, int]:
        """A year after the table: its first week, and the year before's last.

        The first chart is week 1, or week 2 when the New Year break fell in
        the first week; its "previous" link names the last week before it.
        """
        for week in (1, 2):
            path = f"/top40/{year}/week-{week}"
            html = self._page(path)
            if html is None:
                continue
            weeks = {
                int(w) for w in re.findall(rf"/top40/{year - 1}/week-(\d+)(?!\d)", html)
            }
            if len(weeks) != 1:
                raise IngestError(
                    f"cannot list Top 40 weeks: {path} links {len(weeks)} different "
                    f"last weeks of {year - 1}"
                )
            return week, weeks.pop()
        raise IngestError(
            f"cannot list Top 40 weeks: neither week 1 nor week 2 of {year} exists, "
            f"so its first week and the last week of {year - 1} are unknown"
        )

    def fetch(self, ref: EditionRef) -> AcquiredEdition:
        year, week = ref.axes["year"], ref.axes["week"]
        where = f"Top 40 {year} week {week}"
        html = self._page(f"/top40/{year}/week-{week}")
        if html is None:
            raise IngestError(f"{where}: page not found")
        # The page must be the chart asked for: a redirect to another week
        # would otherwise be stored under this one.
        shown = _LATEST.search(html)
        if shown is None:
            raise IngestError(f"{where}: the page has no 'week N, YYYY' title")
        if (int(shown.group(2)), int(shown.group(1))) != (year, week):
            raise IngestError(
                f"{where}: the page is for week {shown.group(1)}, {shown.group(2)}"
            )
        items = [item for item in top40nl.parse_list(html) if item.position is not None]
        problems = top40nl.position_problems([item.position for item in items], SIZE)
        if problems:
            raise IngestError(f"{where}: {problems}")
        entries = []
        for item in sorted(items, key=lambda item: item.position):
            artist, title = item.artist, item.title
            restored = restore_names(artist, title, item.image_title)
            if restored is None:
                _log.warning(
                    f"{where}: position {item.position}: cannot restore shortened "
                    f"names {artist!r} - {title!r}; kept as shown"
                )
            else:
                artist, title = restored
            for field, value in (("title", title), ("artist", artist)):
                if not value:
                    raise IngestError(
                        f"{where}: position {item.position} has no {field}"
                    )
            ids = {}
            if item.title_id:
                ids["top40.nl/title"] = item.title_id
            if item.subtitle:
                ids["top40.nl/subtitle"] = item.subtitle
            songs = tuple(RawSong(a, t) for a, t in top40nl.split_names(artist, title))
            entries.append(RawEntry(item.position, songs, ids))
        return AcquiredEdition(ref, SIZE, tuple(entries))


INGESTOR = Top40Ingestor()
