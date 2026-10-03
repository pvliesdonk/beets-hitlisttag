"""Tests for the bundled Top 2000 ingestor (beetsplug.hitlisttag.ingestors.top2000)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from beetsplug.hitlisttag.ingest import EditionRef, IngestError
from beetsplug.hitlisttag.ingestors import top2000
from beetsplug.hitlisttag.ingestors.top2000 import (
    PAGE_URL,
    SIZE,
    Row,
    Table,
    Top2000Ingestor,
    _clean,
    _decode_year,
    _download_page,
    parse_table,
)

FIXTURE = Path(__file__).parent / "fixtures" / "wikitext" / "top2000-trimmed.txt"


def _fixture() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _row(table: Table, title: str) -> Row:
    return next(r for r in table.rows if r.title == title)


class TestDecodeYear:
    @pytest.mark.parametrize(
        ("cell", "year"), [("99", 1999), ("00", 2000), ("25", 2025), ("26", 2026)]
    )
    def test_two_digit_years(self, cell, year):
        # Review focus 1: a future column decodes without code change.
        assert _decode_year(cell) == year

    @pytest.mark.parametrize("cell", ["", "9", "999", "ab", "2025"])
    def test_malformed_year_is_layout_error(self, cell):
        with pytest.raises(IngestError, match="layout changed"):
            _decode_year(cell)


class TestClean:
    def test_piped_wikilink_keeps_label(self):
        assert _clean("[[Fixture Band (band)|Fixture Band]]") == "Fixture Band"

    def test_plain_wikilink_keeps_target(self):
        assert _clean("[[Made Up Song]]") == "Made Up Song"

    def test_sortnaam_joins_prefix_and_name(self):
        assert _clean("{{SortNaam|The|Placeholders}}") == "The Placeholders"

    def test_abbr_keeps_short_form(self):
        assert _clean("{{Abbr|HP|Hoogste behaalde positie}}") == "HP"

    def test_ref_and_tags_dropped(self):
        assert _clean("[[X]]<ref>Een noot.</ref>") == "X"
        assert _clean("<small>Spacer</small>") == "Spacer"

    def test_align_attribute_and_whitespace(self):
        assert _clean('align="left"|  Side A/Side B  ') == "Side A/Side B"

    def test_unknown_template_left_for_caller_to_reject(self):
        # _clean does not raise; parse_table does, naming the row.
        assert "{{" in _clean("{{nowrap|Some Artist}}")


class TestParseTable:
    def test_years_from_header(self):
        assert parse_table(_fixture()).years == (1999, 2000, 2025)

    def test_rows_and_positions(self):
        table = parse_table(_fixture())
        assert [r.title for r in table.rows] == [
            "Made Up Song",
            "Second Song",
            "Side A/Side B",
            "505",
            "Only Recent",
            "Partial Title",
            "Boogie Blame",
            "Stad Zonder Naam",
        ]
        assert _row(table, "Made Up Song").artist == "Fixture Band"
        assert _row(table, "Made Up Song").positions == {1999: 1, 2000: 1, 2025: 2}

    def test_sortnaam_artist_and_ref_stripped(self):
        row = _row(parse_table(_fixture()), "Second Song")
        assert row.artist == "The Placeholders"
        assert row.positions == {1999: 2, 2025: 1}  # '—' absent

    def test_not_yet_released_marker_is_absent(self):
        assert _row(parse_table(_fixture()), "505").positions == {2000: 4}

    def test_double_a_side_title_kept_whole(self):
        assert _row(parse_table(_fixture()), "Side A/Side B").title == "Side A/Side B"

    def test_numeric_title_stays_text(self):
        assert _row(parse_table(_fixture()), "505").title == "505"

    def test_spacer_row_skipped(self):
        titles = [r.title for r in parse_table(_fixture()).rows]
        assert not any("Spacer" in t for t in titles)

    def test_windows_line_endings_and_bom(self):
        # Review focus 5.
        text = "﻿" + _fixture().replace("\n", "\r\n")
        assert parse_table(text).years == (1999, 2000, 2025)

    @pytest.mark.parametrize(
        "text", ["", "Deze pagina bestaat niet.", '{| class="wikitable"\n|}\n']
    )
    def test_missing_header_is_layout_error(self, text):
        # Review focus 3.
        with pytest.raises(IngestError, match="layout changed"):
            parse_table(text)

    def test_unknown_template_names_row(self):
        # Review focus 2.
        text = _fixture().replace("[[Solo Artist]]", "{{nowrap|Solo Artist}}")
        with pytest.raises(IngestError, match=r"Side A/Side B.*template"):
            parse_table(text)

    def test_bad_position_cell_names_row_and_year(self):
        text = _fixture().replace("||1975||1||1||1||2", "||1975||1||1||1||n/a")
        with pytest.raises(IngestError, match=r"Made Up Song.*2025"):
            parse_table(text)

    def test_position_above_size_names_row(self):
        text = _fixture().replace("||1975||1||1||1||2", f"||1975||1||1||1||{SIZE + 1}")
        with pytest.raises(IngestError, match=r"Made Up Song.*2001"):
            parse_table(text)


def _generated_table(
    years_rows: dict[int, int], double_a_side_year: int | None = None
) -> str:
    """Wikitext with made-up rows: for each year, positions 1..N filled.

    Rows are shared across years: row k holds position k in every year whose
    count is >= k. ``double_a_side_year`` puts an 'A/B' title at position 2.
    """
    years = sorted(years_rows)
    header = "!Artiest||Titel||Jaar||HP||" + "||".join(f"{y % 100:02d}" for y in years)
    lines = ['{| class="wikitable sortable"', "|-", header]
    total = max(years_rows.values())
    for k in range(1, total + 1):
        title = f"Song {k}"
        if double_a_side_year is not None and k == 2:
            title = "Side A/Side B"
        cells = [f"[[Artist {k}]]", title, "1970", str(k)]
        cells += [str(k) if years_rows[y] >= k else "—" for y in years]
        lines += ["|-", "|" + "||".join(cells)]
    lines.append("|}")
    return "\n".join(lines) + "\n"


class TestTop2000Ingestor:
    def test_declares_chart_and_axes(self):
        ingestor = Top2000Ingestor(fetch_text=lambda: "")
        assert ingestor.chart == "top2000"
        assert ingestor.axes == ("year",)

    def test_editions_lists_complete_years_in_order(self, caplog):
        text = _generated_table({1999: SIZE, 2000: SIZE, 2025: 1500})
        ingestor = Top2000Ingestor(fetch_text=lambda: text)
        with caplog.at_level(logging.WARNING):
            refs = list(ingestor.editions())
        assert refs == [EditionRef({"year": 1999}), EditionRef({"year": 2000})]
        assert "2025" in caplog.text and "1500" in caplog.text

    def test_duplicate_position_excludes_year_with_warning(self, caplog):
        # Review focus 4: an editor's typo must not raise from editions().
        text = _generated_table({1999: SIZE, 2000: SIZE}).replace(
            "|[[Artist 7]]||Song 7||1970||7||7||7",
            "|[[Artist 7]]||Song 7||1970||7||7||6",
        )
        ingestor = Top2000Ingestor(fetch_text=lambda: text)
        with caplog.at_level(logging.WARNING):
            refs = list(ingestor.editions())
        assert refs == [EditionRef({"year": 1999})]
        assert "2000" in caplog.text

    def test_fetch_complete_year(self):
        text = _generated_table({1999: SIZE, 2025: SIZE}, double_a_side_year=2025)
        ingestor = Top2000Ingestor(fetch_text=lambda: text)
        edition = ingestor.fetch(EditionRef({"year": 2025}))
        assert edition.ref == EditionRef({"year": 2025})
        assert edition.size == SIZE
        assert len(edition.entries) == SIZE
        assert [e.position for e in edition.entries] == list(range(1, SIZE + 1))
        assert all(len(e.songs) == 1 for e in edition.entries)
        assert edition.entries[1].songs[0].title == "Side A/Side B"
        assert edition.entries[0].songs[0].artist == "Artist 1"

    def test_fetch_incomplete_year_is_ingest_error(self):
        text = _generated_table({1999: SIZE, 2025: 10})
        ingestor = Top2000Ingestor(fetch_text=lambda: text)
        with pytest.raises(IngestError, match=r"2025.*1999"):
            ingestor.fetch(EditionRef({"year": 2025}))

    def test_fetch_absent_year_is_ingest_error(self):
        text = _generated_table({1999: SIZE})
        ingestor = Top2000Ingestor(fetch_text=lambda: text)
        with pytest.raises(IngestError, match=r"1980.*1999"):
            ingestor.fetch(EditionRef({"year": 1980}))

    def test_page_fetched_once_per_instance(self):
        calls = []
        text = _generated_table({1999: SIZE, 2000: SIZE})

        def fetch_text():
            calls.append(1)
            return text

        ingestor = Top2000Ingestor(fetch_text=fetch_text)
        list(ingestor.editions())
        ingestor.fetch(EditionRef({"year": 1999}))
        ingestor.fetch(EditionRef({"year": 2000}))
        assert len(calls) == 1

    def test_failed_fetch_is_ingest_error_and_retried(self):
        import requests

        attempts = []
        text = _generated_table({1999: SIZE})

        def fetch_text():
            attempts.append(1)
            if len(attempts) == 1:
                raise requests.ConnectionError("no route")
            return text

        ingestor = Top2000Ingestor(fetch_text=fetch_text)
        with pytest.raises(IngestError, match="cannot fetch.*no route"):
            list(ingestor.editions())
        assert list(ingestor.editions()) == [EditionRef({"year": 1999})]
        assert len(attempts) == 2


class TestDefaultFetcher:
    """The default download path goes through the shared Fetcher (#126)."""

    @staticmethod
    def _fake(monkeypatch, result):
        captured: dict = {}

        class FakeFetcher:
            def __init__(self, **kwargs):
                captured["kwargs"] = kwargs

            def get(self, url):
                captured["url"] = url
                if isinstance(result, Exception):
                    raise result
                return result

        monkeypatch.setattr(top2000, "Fetcher", FakeFetcher)
        return captured

    def test_download_uses_a_default_fetcher_on_page_url(self, monkeypatch):
        captured = self._fake(monkeypatch, "page text")
        assert _download_page() == "page text"
        assert captured == {"kwargs": {}, "url": PAGE_URL}

    def test_404_is_ingest_error(self, monkeypatch):
        self._fake(monkeypatch, None)
        with pytest.raises(IngestError, match="gone"):
            _download_page()

    def test_default_ingestor_passes_fetch_errors_through(self, monkeypatch):
        self._fake(monkeypatch, IngestError(f"cannot fetch {PAGE_URL}: HTTP 503"))
        with pytest.raises(IngestError, match="cannot fetch.*503"):
            list(Top2000Ingestor().editions())

    def test_fetcher_is_built_per_download_not_at_import(self, monkeypatch):
        captured = self._fake(monkeypatch, "page text")
        Top2000Ingestor()  # constructing an ingestor builds no Fetcher
        assert captured == {}


class TestFinalReviewFixes:
    """Findings from the whole-branch review against the real page."""

    def test_sorteer_template_keeps_display_text(self):
        # Critical 1: the real page uses {{Sorteer|sortkey|display}}.
        assert _clean("{{Sorteer|Jacksons, The|[[The Jacksons]]}}") == "The Jacksons"
        row = _row(parse_table(_fixture()), "Boogie Blame")
        assert row.artist == "The Jacksons"

    def test_four_argument_sortnaam_drops_link_target(self):
        # Critical 2: {{SortNaam|prefix|name|link target}} must not leak the target.
        assert _clean("{{SortNaam|De|Dijk|De Dijk (band)}}") == "De Dijk"
        row = _row(parse_table(_fixture()), "Stad Zonder Naam")
        assert row.artist == "De Dijk"
        assert row.positions == {2000: 8, 2025: 8}

    @pytest.mark.parametrize("leftover", ["Solo|Artist", "[[Solo Artist", "Solo}}"])
    def test_leftover_markup_in_name_names_row(self, leftover):
        text = _fixture().replace("[[Solo Artist]]", leftover)
        with pytest.raises(IngestError, match=r"Side A/Side B.*markup"):
            parse_table(text)

    def test_missing_fixed_header_column_is_layout_error(self):
        # Important 3: a dropped HP column must not silently shift every year.
        text = _fixture().replace("||{{Abbr|HP|Hoogste behaalde positie}}", "")
        with pytest.raises(IngestError, match="layout changed.*header"):
            parse_table(text)

    def test_bom_and_crlf_with_header_on_first_line(self):
        # Important 4: the BOM must be stripped where it matters, and rows survive.
        plain = parse_table(_fixture())
        body = "\n".join(_fixture().split("\n")[1:])  # header is now line 0
        text = "\ufeff" + body.replace("\n", "\r\n")
        table = parse_table(text)
        assert table.years == plain.years
        assert table.rows == plain.rows


@pytest.mark.skipif(
    not os.environ.get("HITLISTTAG_TOP2000_WIKITEXT"),
    reason="set HITLISTTAG_TOP2000_WIKITEXT to a saved copy of the real page",
)
def test_real_page_parses_completely():
    """Opt-in check against a locally saved copy of the real page; commits no data."""
    path = Path(os.environ["HITLISTTAG_TOP2000_WIKITEXT"])
    ingestor = Top2000Ingestor(fetch_text=lambda: path.read_text(encoding="utf-8"))
    table = parse_table(path.read_text(encoding="utf-8"))
    assert len(table.years) >= 27 and table.years[0] == 1999
    assert len(table.rows) >= 4900
    refs = list(ingestor.editions())
    assert [r.axes["year"] for r in refs] == list(table.years)  # every year complete
    for ref in refs:
        edition = ingestor.fetch(ref)
        assert len(edition.entries) == SIZE
    bad = [
        (r.artist, r.title)
        for r in table.rows
        if any(m in r.artist + r.title for m in ("{{", "}}", "[[", "]]", "|"))
    ]
    assert bad == []
