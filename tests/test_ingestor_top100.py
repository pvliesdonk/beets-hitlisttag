"""Tests for the bundled Top 100 ingestor (beetsplug.hitlisttag.ingestors.top100)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from beetsplug.hitlisttag import top40nl
from beetsplug.hitlisttag.ingest import EditionRef, IngestError, discover_ingestors
from beetsplug.hitlisttag.ingestors import top100
from beetsplug.hitlisttag.ingestors.top100 import INDEX_PATH, SIZE, Top100Ingestor

INDEX_URL = top40nl.BASE_URL + INDEX_PATH

_ITEM = """
<div class="top40-list__item" data-video="x">
    <a href="https://www.top40.nl/made-up/song-{tid}" class="image-link">
        <img src="https://www.top40.nl/media/cache/list/uploads/{img}/original.jpg"
             alt=""/>
    </a>
    <div class="top40-list__item__container">
        <div class="top40-list__item__info">
            <div class="number-block number-block--red"><h4>{pos}</h4></div>
            <a href="https://www.top40.nl/made-up/song-{tid}" class="h3">{title}</a>
            <a href="https://www.top40.nl/made-up/song-{tid}"
               class="p lead lowercase">{artist}</a>
        </div>
    </div>
</div>
"""


def _year_page(positions, title=None, artist=None) -> str:
    """A synthetic year page in the year-list layout, with made-up names."""
    items = "".join(
        _ITEM.format(
            pos=pos,
            tid=5000 + pos,
            img=f"subtitle/{6000 + pos}" if pos % 2 else f"title/{5000 + pos}",
            title=(title or (lambda p: f"Made Up Song {p}"))(pos),
            artist=(artist or (lambda p: f"Pretend Artist {p}"))(pos),
        )
        for pos in positions
    )
    return f'<html><body><div class="list__list">{items}</div></body></html>'


def _index_page(*years) -> str:
    links = "".join(
        f'<a href="https://www.top40.nl{INDEX_PATH}/{y}">{y}</a>' for y in years
    )
    return (
        f'<html><body><a href="https://www.top40.nl{INDEX_PATH}">Top 100</a>'
        f'<a href="https://www.top40.nl/bijzondere-lijsten">Lijsten</a>{links}'
        f'<a href="https://www.top40.nl{INDEX_PATH}/19655">odd</a></body></html>'
    )


class Site:
    """An injectable ``get``: serves pages by URL, None for anything else."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url: str) -> str | None:
        self.calls.append(url)
        return self.pages.get(url)


def _ingestor(**pages) -> tuple[Top100Ingestor, Site]:
    site = Site(
        {INDEX_URL + ("" if k == "index" else f"/{k[1:]}"): v for k, v in pages.items()}
    )
    return Top100Ingestor(get=site), site


class TestEditions:
    def test_years_from_the_index_sorted_and_deduplicated(self):
        ing, site = _ingestor(index=_index_page(1966, 1965, 2025, 1965))
        assert ing.editions() == [
            EditionRef({"year": 1965}),
            EditionRef({"year": 1966}),
            EditionRef({"year": 2025}),
        ]
        ing.editions()
        assert site.calls == [INDEX_URL]  # fetched once per instance

    def test_index_without_years_is_an_ingest_error(self):
        ing, _ = _ingestor(index="<html><body>Nieuwe layout</body></html>")
        with pytest.raises(IngestError, match="cannot list Top 100 years"):
            ing.editions()

    def test_missing_index_is_an_ingest_error(self):
        ing, _ = _ingestor()
        with pytest.raises(IngestError, match="cannot list Top 100 years"):
            ing.editions()


class TestFetch:
    def test_complete_year(self):
        ing, _ = _ingestor(y1990=_year_page(range(1, SIZE + 1)))
        edition = ing.fetch(EditionRef({"year": 1990}))
        assert edition.size == 100
        assert len(edition.entries) == 100
        first, second = edition.entries[0], edition.entries[1]
        assert first.position == 1
        assert (first.songs[0].artist, first.songs[0].title) == (
            "Pretend Artist 1",
            "Made Up Song 1",
        )
        assert dict(first.source_ids) == {
            "top40.nl/title": "5001",
            "top40.nl/subtitle": "6001",
        }
        assert dict(second.source_ids) == {"top40.nl/title": "5002"}

    def test_multi_song_entries_credit_each_song(self):
        page = _year_page(
            range(1, SIZE + 1),
            title=lambda p: {7: "Side A ; Side B", 8: "Tune / Tune - Edit"}.get(
                p, f"Song {p}"
            ),
            artist=lambda p: "Duo One / Trio Two ((GBR))" if p == 7 else f"Act {p}",
        )
        ing, _ = _ingestor(y1965=page)
        entries = ing.fetch(EditionRef({"year": 1965})).entries
        assert [(s.artist, s.title) for s in entries[6].songs] == [
            ("Duo One", "Side A"),
            ("Duo One", "Side B"),
            ("Trio Two ((GBR))", "Side A"),
            ("Trio Two ((GBR))", "Side B"),
        ]
        assert dict(entries[6].source_ids)["top40.nl/title"] == "5007"
        assert [(s.artist, s.title) for s in entries[7].songs] == [
            ("Act 8", "Tune / Tune - Edit")
        ]

    def test_page_order_does_not_matter(self):
        ing, _ = _ingestor(y1990=_year_page(reversed(range(1, SIZE + 1))))
        positions = [e.position for e in ing.fetch(EditionRef({"year": 1990})).entries]
        assert positions == list(range(1, SIZE + 1))

    @pytest.mark.parametrize(
        ("positions", "match"),
        [
            (
                [p for p in range(1, SIZE + 1) if p not in (57, 58)],
                "missing positions 57–58",
            ),
            (list(range(1, SIZE + 1)) + [12], "duplicated positions 12"),
            (list(range(1, SIZE + 1)) + [101], "positions outside 1–100: 101"),
            ([], "missing positions 1–100"),
        ],
    )
    def test_incomplete_year_is_an_ingest_error(self, positions, match):
        ing, _ = _ingestor(y1990=_year_page(positions))
        with pytest.raises(IngestError, match=f"Top 100 1990: .*{match}"):
            ing.fetch(EditionRef({"year": 1990}))

    def test_layout_change_reports_every_position_missing(self):
        ing, _ = _ingestor(y1990="<html><body><table>Nieuw</table></body></html>")
        with pytest.raises(IngestError, match="missing positions 1–100"):
            ing.fetch(EditionRef({"year": 1990}))

    @pytest.mark.parametrize("field", ["title", "artist"])
    def test_empty_title_or_artist_is_an_ingest_error(self, field):
        blank = {field: lambda p: "" if p == 9 else f"X {p}"}
        ing, _ = _ingestor(y1990=_year_page(range(1, SIZE + 1), **blank))
        with pytest.raises(
            IngestError, match=f"Top 100 1990: position 9 has no {field}"
        ):
            ing.fetch(EditionRef({"year": 1990}))

    def test_missing_page_is_an_ingest_error(self):
        ing, _ = _ingestor()
        with pytest.raises(IngestError, match="Top 100 1990: page not found"):
            ing.fetch(EditionRef({"year": 1990}))


class TestFetcher:
    def test_one_fetcher_per_instance_created_on_first_use(self, monkeypatch):
        made = []

        class FakeFetcher:
            def get(self, url):
                return _index_page(2001) if url == INDEX_URL else None

        def fake_fetcher():
            made.append(1)
            return FakeFetcher()

        monkeypatch.setattr(top40nl, "fetcher", fake_fetcher)
        ing = Top100Ingestor()
        assert made == []
        ing.editions()
        with pytest.raises(IngestError):
            ing.fetch(EditionRef({"year": 2001}))
        assert made == [1]

    def test_module_ingestor_built_no_fetcher(self):
        assert top100.INGESTOR._get is None


def test_bundled_discovery_finds_top100():
    found = discover_ingestors(None, log=logging.getLogger("test.top100"))
    assert isinstance(found["top100"], Top100Ingestor)


@pytest.mark.skipif(
    not os.environ.get("HITLISTTAG_REAL_PAGES"),
    reason="set HITLISTTAG_REAL_PAGES to a development cache directory",
)
def test_real_cached_year_pages():
    root = Path(os.environ["HITLISTTAG_REAL_PAGES"]) / "www.top40.nl"
    pages = sorted((root / INDEX_PATH.lstrip("/")).glob("[0-9][0-9][0-9][0-9].page"))
    if not pages:
        pytest.skip("no cached Top 100 year pages")

    def get(url: str) -> str:
        path = root / (url.removeprefix(top40nl.BASE_URL + "/") + ".page")
        return path.read_text(encoding="utf-8")  # cached files only, never the site

    ing = Top100Ingestor(get=get)
    for page in pages:
        edition = ing.fetch(EditionRef({"year": int(page.stem)}))
        assert len(edition.entries) == SIZE, page
        assert all("top40.nl/title" in e.source_ids for e in edition.entries), page
