"""Bundled ingestor for the Top 100 year lists on top40.nl.

Source: the Stichting Nederlandse Top 40's *Top 100 jaaroverzichten*,
https://www.top40.nl/bijzondere-lijsten/top-100-jaaroverzichten: one page per
year since 1965, each the foundation's points recomputation over that year's
weekly Top 40 (research spike #116). The index page lists the years.

The site reserves copyright and database rights over its data. Each user's
acquired copy is private and must stay unpublished; the plugin ships no chart
data.

Entries are recorded as published: the site's merged titles and artists
(" / " lists of versions, "((1965))" qualifiers) and " ; " double A-sides are
kept as they are. ``source_ids`` carries the site's title id and the raw
subtitle image id, uninterpreted.
"""

from __future__ import annotations

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

SIZE = 100
"""Positions per edition."""

INDEX_PATH = "/bijzondere-lijsten/top-100-jaaroverzichten"
"""The index page; a year's page is ``INDEX_PATH/<year>``."""

_YEAR_LINK = re.compile(re.escape(INDEX_PATH) + r"/(\d{4})(?!\d)")


class Top100Ingestor:
    """The ``top100`` ingestor over top40.nl's year lists.

    ``get`` fetches a URL and returns its text, or None for a 404; by
    default the ``get`` of one ``top40nl.fetcher()``, created on first use
    so a run is paced throughout and nothing is built at import.
    """

    chart = "top100"
    axes = ("year",)

    def __init__(self, get: Callable[[str], str | None] | None = None) -> None:
        self._get = get
        self._years: list[int] | None = None

    def _page(self, path: str) -> str | None:
        if self._get is None:
            self._get = top40nl.fetcher().get
        return self._get(top40nl.BASE_URL + path)

    def editions(self) -> list[EditionRef]:
        if self._years is None:
            html = self._page(INDEX_PATH)
            if html is None:
                raise IngestError("cannot list Top 100 years: index page not found")
            years = sorted({int(year) for year in _YEAR_LINK.findall(html)})
            if not years:
                raise IngestError(
                    "cannot list Top 100 years: no years on the index page "
                    "(has its layout changed?)"
                )
            self._years = years
        return [EditionRef({"year": year}) for year in self._years]

    def fetch(self, ref: EditionRef) -> AcquiredEdition:
        year = ref.axes["year"]
        html = self._page(f"{INDEX_PATH}/{year}")
        if html is None:
            raise IngestError(f"Top 100 {year}: page not found")
        items = [item for item in top40nl.parse_list(html) if item.position is not None]
        problems = top40nl.position_problems([item.position for item in items], SIZE)
        if problems:
            raise IngestError(f"Top 100 {year}: {problems}")
        entries = []
        for item in sorted(items, key=lambda item: item.position):
            for field in ("title", "artist"):
                if not getattr(item, field):
                    raise IngestError(
                        f"Top 100 {year}: position {item.position} has no {field}"
                    )
            ids = {}
            if item.title_id:
                ids["top40.nl/title"] = item.title_id
            if item.subtitle:
                ids["top40.nl/subtitle"] = item.subtitle
            songs = tuple(
                RawSong(a, t)
                for a, t in top40nl.split_names(item.artist, item.title, year)
            )
            entries.append(RawEntry(item.position, songs, ids))
        return AcquiredEdition(ref, SIZE, tuple(entries))


INGESTOR = Top100Ingestor()
