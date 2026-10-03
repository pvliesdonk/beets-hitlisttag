"""Tests for the bundled Top 40 weekly ingestor (ingestors/top40.py)."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pytest

from beetsplug.hitlisttag import top40nl
from beetsplug.hitlisttag.ingest import EditionRef, IngestError, discover_ingestors
from beetsplug.hitlisttag.ingestors import top40
from beetsplug.hitlisttag.ingestors.top40 import (
    FIRST_YEAR,
    LAST_WEEK,
    SIZE,
    Top40Ingestor,
    restore_names,
)

BASE = top40nl.BASE_URL
TABLE_END = max(LAST_WEEK)

_ITEM = """
<div class="top40-list__item {extra}" data-video="x">
    <a href="/made-up/tune-{tid}" class="image-link" tabindex="-1">
        <img src="https://www.top40.nl/media/cache/list/uploads/subtitle/{tid}_{sub}/o.jpg"
             title="Details {full}" alt=""/>
    </a>
    <div class="top40-list__item__container">
        <div class="top40-list__item__info">
            <div class="top40-list__item__info__position">
                <div class="number-block number-block--red">
                    <span class="h4"> {pos} </span></div>
            </div>
            <a href="https://www.top40.nl/made-up/tune-{tid}" class="h3">
                <h2 class="h3">{title}</h2></a>
            <a href="https://www.top40.nl/made-up/tune-{tid}" class="p lead lowercase">
                <h3 class="p lead lowercase">{artist}</h3></a>
        </div>
    </div>
</div>
"""


def _item(pos, artist, title, full=None, extra=""):
    return _ITEM.format(
        pos=pos,
        tid=7000 + (pos if isinstance(pos, int) else 99),
        sub=8000,
        artist=artist,
        title=title,
        full=full or f"{artist} - {title}",
        extra=extra,
    )


def _week_page(positions=range(1, SIZE + 1), names=None, title_text="") -> str:
    names = names or {}
    items = [
        _item(p, *names.get(p, (f"Pretend Act {p}", f"Made Up Tune {p}")))
        for p in positions
    ]
    items += [
        _item("-", "Gone Act", "Gone Tune", extra="no-longer-listed"),
        _item("-", "Left Act", "Left Tune", extra="no-longer-listed"),
    ]
    return (
        f"<html><head><title>{title_text}</title></head><body>"
        f'<div class="list__list">{"".join(items)}</div></body></html>'
    )


def _latest(year: int, week: int) -> str:
    return _week_page(title_text=f"Top 40-lijst van week {week}, {year}")


def _week1(year: int, previous_last: int, week: int = 1) -> str:
    """A year's first week with nav links like the site's: previous, self, next."""
    links = "".join(
        f'<a href="https://www.top40.nl/top40/{y}/week-{w}">x</a>'
        for y, w in ((year - 1, previous_last), (year, week), (year, week + 1))
    )
    return f"<html><body>{links}</body></html>"


class Site:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url: str) -> str | None:
        self.calls.append(url)
        return self.pages.get(url.removeprefix(BASE))


def _years_weeks(refs):
    return [(r.axes["year"], r.axes["week"]) for r in refs]


class TestRestoreNames:
    def test_nothing_shortened_returns_visible(self):
        assert restore_names("A", "T", None) == ("A", "T")

    def test_shortened_artist(self):
        assert restore_names(
            "Bob Smit en het Duke City Sextet o.l.v. van J..",
            "Ik Heb Me Weer Vergist",
            "Bob Smit en het Duke City Sextet o.l.v. van Jan Bijlaart - "
            "Ik Heb Me Weer Vergist",
        ) == (
            "Bob Smit en het Duke City Sextet o.l.v. van Jan Bijlaart",
            "Ik Heb Me Weer Vergist",
        )

    def test_shortened_title(self):
        assert restore_names(
            "Drafi Deutscher / Trea Dobbs",
            "Marmor, Stein Und Eisen Bricht / Marmer, Staa..",
            "Drafi Deutscher / Trea Dobbs - Marmor, Stein Und Eisen Bricht / "
            "Marmer, Staal En Steen Vergaan",
        ) == (
            "Drafi Deutscher / Trea Dobbs",
            "Marmor, Stein Und Eisen Bricht / Marmer, Staal En Steen Vergaan",
        )

    def test_both_shortened(self):
        assert restore_names(
            "Duo Acropolis / Trio Hellenique / Mikis Theod..",
            "Zorba Le Grec / La Danse De Zorba///Sirtaki /..",
            "Duo Acropolis / Trio Hellenique / Mikis Theodorakis - Zorba Le Grec / "
            "La Danse De Zorba///Sirtaki / Zorba De Griek///Zorba Le Grec",
        ) == (
            "Duo Acropolis / Trio Hellenique / Mikis Theodorakis",
            "Zorba Le Grec / La Danse De Zorba///Sirtaki / Zorba De Griek///"
            "Zorba Le Grec",
        )

    def test_dash_inside_the_title_is_anchored(self):
        assert restore_names(
            "De Praatpalen",
            "Ome Sjakie - Het Hele Zakie Loopt In Z'n Naki..",
            "De Praatpalen - Ome Sjakie - Het Hele Zakie Loopt In Z'n Nakie",
        ) == ("De Praatpalen", "Ome Sjakie - Het Hele Zakie Loopt In Z'n Nakie")

    def test_site_own_ellipsis_restores_to_itself(self):
        assert restore_names(
            "Jan Boezeroen", "Ze Zeggen...", "Jan Boezeroen - Ze Zeggen..."
        ) == ("Jan Boezeroen", "Ze Zeggen...")

    def test_two_fitting_splits_is_none(self):
        assert restore_names("A..", "B..", "A - B - B") is None

    def test_no_fit_or_no_image_title_is_none(self):
        assert restore_names("Someone El..", "T", "Other - T") is None
        assert restore_names("Someone El..", "T", None) is None


class TestTable:
    def test_years_contiguous_from_first_year(self):
        assert sorted(LAST_WEEK) == list(range(FIRST_YEAR, TABLE_END + 1))
        assert set(LAST_WEEK.values()) <= {51, 52, 53}

    def test_known_values(self):
        assert [y for y, w in LAST_WEEK.items() if w == 53] == [
            1966,
            1972,
            1977,
            2005,
            2011,
            2016,
            2022,
        ]
        assert LAST_WEEK[1984] == 51 and LAST_WEEK[1983] == 52


class TestEditions:
    def test_table_years_then_the_latest_year(self):
        site = Site(
            {
                "/top40": _latest(TABLE_END + 1, 3),
                f"/top40/{TABLE_END + 1}/week-1": _week1(TABLE_END + 1, 52),
            }
        )
        refs = Top40Ingestor(get=site).editions()
        ywk = _years_weeks(refs)
        assert ywk[0] == (FIRST_YEAR, 1)
        assert ywk[-4:] == [
            (TABLE_END, LAST_WEEK[TABLE_END]),
            (TABLE_END + 1, 1),
            (TABLE_END + 1, 2),
            (TABLE_END + 1, 3),
        ]
        assert len(refs) == sum(LAST_WEEK.values()) - len(top40.KNOWN_MISSING) + 3
        assert ywk == sorted(ywk)
        assert site.calls == [BASE + "/top40", BASE + f"/top40/{TABLE_END + 1}/week-1"]

    def test_listed_once_per_instance(self):
        site = Site(
            {
                "/top40": _latest(TABLE_END + 1, 3),
                f"/top40/{TABLE_END + 1}/week-1": _week1(TABLE_END + 1, 52),
            }
        )
        ing = Top40Ingestor(get=site)
        ing.editions()
        ing.editions()
        assert len(site.calls) == 2

    def test_early_january_reads_the_completed_year_from_week_1(self):
        done = TABLE_END + 1
        site = Site(
            {
                "/top40": _latest(done + 1, 1),
                f"/top40/{done}/week-1": _week1(done, 52),
                f"/top40/{done + 1}/week-1": _week1(done + 1, 53),
            }
        )
        ywk = _years_weeks(Top40Ingestor(get=site).editions())
        assert ywk[-3:] == [(done, 52), (done, 53), (done + 1, 1)]
        assert site.calls == [
            BASE + "/top40",
            BASE + f"/top40/{done}/week-1",
            BASE + f"/top40/{done + 1}/week-1",
        ]

    def test_year_without_week_1_starts_at_week_2(self):
        done = TABLE_END + 1
        site = Site(
            {
                "/top40": _latest(done + 1, 4),
                f"/top40/{done}/week-1": _week1(done, 52),
                f"/top40/{done + 1}/week-2": _week1(done + 1, 51, week=2),
            }
        )
        ywk = _years_weeks(Top40Ingestor(get=site).editions())
        assert ywk[-4:] == [(done, 51), (done + 1, 2), (done + 1, 3), (done + 1, 4)]

    def test_known_missing_holds_the_live_run_findings(self):
        assert top40.KNOWN_MISSING == frozenset(
            (year, 1)
            for year in (1982, 1983, 1988, 1993, 1994, 1997, 1998, 1999, 2000, 2005)
        )

    def test_known_missing_weeks_are_left_out(self, monkeypatch):
        monkeypatch.setattr(top40, "KNOWN_MISSING", frozenset({(1970, 30)}))
        site = Site(
            {
                "/top40": _latest(TABLE_END + 1, 1),
                f"/top40/{TABLE_END + 1}/week-1": _week1(TABLE_END + 1, 52),
            }
        )
        assert (1970, 30) not in _years_weeks(Top40Ingestor(get=site).editions())

    @pytest.mark.parametrize(
        "page",
        [None, "<html><body>{links}</body></html>", "two"],
    )
    def test_unusable_week_1_page_is_an_ingest_error(self, page):
        done = TABLE_END + 1
        if page == "two":
            page = _week1(done + 1, 52) + _week1(done + 1, 53)
        elif page is not None:
            page = page.format(links='<a href="/top40/1999/week-4">x</a>')
        pages = {
            "/top40": _latest(done + 1, 1),
            f"/top40/{done}/week-1": _week1(done, 52),
        }
        if page is not None:
            pages[f"/top40/{done + 1}/week-1"] = page
        with pytest.raises(IngestError, match=f"cannot list Top 40 weeks: .*{done}"):
            Top40Ingestor(get=Site(pages)).editions()

    def test_neither_week_1_nor_week_2_is_an_ingest_error(self):
        site = Site({"/top40": _latest(TABLE_END + 1, 5)})
        with pytest.raises(IngestError, match="neither week 1 nor week 2"):
            Top40Ingestor(get=site).editions()

    @pytest.mark.parametrize("page", [None, "<html><head><title>Top40.nl</title>"])
    def test_unusable_latest_page_is_an_ingest_error(self, page):
        pages = {} if page is None else {"/top40": page}
        with pytest.raises(IngestError, match="cannot list Top 40 weeks: /top40"):
            Top40Ingestor(get=Site(pages)).editions()

    def test_latest_year_before_the_table_end_is_an_ingest_error(self):
        site = Site({"/top40": _latest(TABLE_END - 1, 10)})
        with pytest.raises(IngestError, match="cannot list Top 40 weeks: .*before"):
            Top40Ingestor(get=site).editions()


class TestFetch:
    def test_complete_week(self):
        site = Site({"/top40/1990/week-20": _week_page()})
        edition = Top40Ingestor(get=site).fetch(EditionRef({"year": 1990, "week": 20}))
        assert edition.size == 40 and len(edition.entries) == 40
        first = edition.entries[0]
        assert (first.songs[0].artist, first.songs[0].title) == (
            "Pretend Act 1",
            "Made Up Tune 1",
        )
        assert dict(first.source_ids) == {
            "top40.nl/title": "7001",
            "top40.nl/subtitle": "7001_8000",
        }
        assert all(e.songs[0].title != "Gone Tune" for e in edition.entries)

    def test_shortened_name_restored_from_image_title(self):
        names = {5: ("The Very Long Name Of An Imaginary Orch..", "Tune")}
        page = _week_page(names=names).replace(
            "Details The Very Long Name Of An Imaginary Orch.. - Tune",
            "Details The Very Long Name Of An Imaginary Orchestra - Tune",
        )
        edition = Top40Ingestor(get=Site({"/top40/1990/week-20": page})).fetch(
            EditionRef({"year": 1990, "week": 20})
        )
        assert edition.entries[4].songs[0].artist == (
            "The Very Long Name Of An Imaginary Orchestra"
        )

    def test_unrestorable_name_kept_as_shown_with_a_warning(self, caplog):
        names = {5: ("Somebody Somewh..", "Tune")}
        page = _week_page(names=names).replace(
            "Details Somebody Somewh.. - Tune", "Details Nobody - Tune"
        )
        with caplog.at_level(logging.WARNING):
            edition = Top40Ingestor(get=Site({"/top40/1990/week-20": page})).fetch(
                EditionRef({"year": 1990, "week": 20})
            )
        assert edition.entries[4].songs[0].artist == "Somebody Somewh.."
        assert "Top 40 1990 week 20: position 5" in caplog.text

    @pytest.mark.parametrize(
        ("positions", "match"),
        [
            ([p for p in range(1, 41) if p != 17], "missing positions 17"),
            (list(range(1, 41)) + [3], "duplicated positions 3"),
            (list(range(1, 41)) + [41], "positions outside 1–40: 41"),
        ],
    )
    def test_incomplete_week_is_an_ingest_error(self, positions, match):
        site = Site({"/top40/1990/week-20": _week_page(positions=positions)})
        with pytest.raises(IngestError, match=f"Top 40 1990 week 20: {match}"):
            Top40Ingestor(get=site).fetch(EditionRef({"year": 1990, "week": 20}))

    def test_empty_artist_is_an_ingest_error(self):
        site = Site({"/top40/1990/week-20": _week_page(names={9: ("", "Tune")})})
        with pytest.raises(IngestError, match="position 9 has no artist"):
            Top40Ingestor(get=site).fetch(EditionRef({"year": 1990, "week": 20}))

    def test_missing_page_is_an_ingest_error(self):
        with pytest.raises(IngestError, match="Top 40 1990 week 20: page not found"):
            Top40Ingestor(get=Site({})).fetch(EditionRef({"year": 1990, "week": 20}))


class TestFetcher:
    def test_one_fetcher_per_instance_created_on_first_use(self, monkeypatch):
        made = []

        class FakeFetcher:
            def get(self, url):
                return {
                    BASE + "/top40": _latest(TABLE_END + 1, 1),
                    BASE + f"/top40/{TABLE_END + 1}/week-1": _week1(TABLE_END + 1, 52),
                }.get(url)

        def fake_fetcher():
            made.append(1)
            return FakeFetcher()

        monkeypatch.setattr(top40nl, "fetcher", fake_fetcher)
        ing = Top40Ingestor()
        assert made == []
        ing.editions()
        with pytest.raises(IngestError):
            ing.fetch(EditionRef({"year": 1990, "week": 1}))
        assert made == [1]

    def test_module_ingestor_built_no_fetcher(self):
        assert top40.INGESTOR._get is None


def test_bundled_discovery_finds_top40():
    found = discover_ingestors(None, log=logging.getLogger("test.top40"))
    assert isinstance(found["top40"], Top40Ingestor)


@pytest.mark.skipif(
    not os.environ.get("HITLISTTAG_REAL_PAGES"),
    reason="set HITLISTTAG_REAL_PAGES to a development cache directory",
)
def test_real_cached_week_pages(caplog):
    root = Path(os.environ["HITLISTTAG_REAL_PAGES"]) / "www.top40.nl"
    pages = sorted((root / "top40").glob("[0-9][0-9][0-9][0-9]/week-*.page"))
    if not pages:
        pytest.skip("no cached Top 40 week pages")

    def get(url: str) -> str:
        path = root / (url.removeprefix(BASE + "/") + ".page")
        return path.read_text(encoding="utf-8")  # cached files only

    ing = Top40Ingestor(get=get)
    with caplog.at_level(logging.WARNING):
        for page in pages:
            year, week = int(page.parent.name), int(page.stem.removeprefix("week-"))
            edition = ing.fetch(EditionRef({"year": year, "week": week}))
            assert len(edition.entries) == SIZE, page
            assert all("top40.nl/title" in e.source_ids for e in edition.entries)
    assert "cannot restore" not in caplog.text
    for page in pages:
        year = int(page.parent.name)
        first = "week-2" if (year, 1) in top40.KNOWN_MISSING else "week-1"
        if page.stem == first and year - 1 in LAST_WEEK:
            weeks = {
                int(w)
                for w in re.findall(
                    rf"/top40/{year - 1}/week-(\d+)(?!\d)", page.read_text("utf-8")
                )
            }
            assert weeks == {LAST_WEEK[year - 1]}, page
