"""Tests for the bundled Top 2000 ingestor (beetsplug.hitlisttag.ingestors.top2000)."""

from __future__ import annotations

from pathlib import Path

import pytest

from beetsplug.hitlisttag.ingest import IngestError
from beetsplug.hitlisttag.ingestors.top2000 import (
    SIZE,
    Row,
    Table,
    _clean,
    _decode_year,
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
