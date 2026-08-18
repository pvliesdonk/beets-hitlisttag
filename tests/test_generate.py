"""Tests for the pure generation core: chart building and merge semantics."""

from __future__ import annotations

import pytest

from beetsplug.hitlisttag.charts import Chart, ChartList
from beetsplug.hitlisttag.generate import RunReport, build_chart, merge_charts
from beetsplug.hitlisttag.lookup import Placement


def _p(position: int, size: int, **axes: int) -> Placement:
    return Placement(axes=axes, position=position, size=size)


class TestBuildChart:
    def test_single_axis_nesting(self):
        chart = build_chart(
            "top2000", ["year"], [_p(1, 3, year=2022), _p(3, 3, year=2023)]
        )
        assert chart.name == "top2000"
        assert chart.chart_type == ["year"]
        assert chart.positions == {"2022": 1, "2023": 3}

    def test_two_axis_nesting_in_axis_order(self):
        chart = build_chart(
            "top40",
            ["year", "week"],
            [_p(1, 40, year=2023, week=40), _p(2, 40, year=2023, week=41)],
        )
        assert chart.positions == {"2023": {"40": 1, "41": 2}}

    def test_score_and_highest_from_scoring(self):
        # size 3: (3+1-1) + (3+1-3) = 4; highest = 1
        chart = build_chart(
            "top2000", ["year"], [_p(1, 3, year=2022), _p(3, 3, year=2023)]
        )
        assert chart.score == 4
        assert chart.highest == 1

    def test_round_trips_through_tag_json(self):
        chart = build_chart("top2000", ["year"], [_p(1, 3, year=2022)])
        assert Chart.from_json_string(chart.to_json_string()) == chart

    def test_empty_placements_rejected(self):
        with pytest.raises(ValueError):
            build_chart("top2000", ["year"], [])


def _make(name: str, score: int = 10, positions: dict | None = None) -> Chart:
    chart = Chart(name)
    chart.score = score
    chart.highest = 1
    chart.chart_type = ["year"]
    chart.positions = positions if positions is not None else {"2020": 1}
    return chart


class TestMergeCharts:
    def test_generated_replaces_namesake_in_place(self):
        existing = ChartList([_make("top2000", score=1), _make("kerst", score=2)])
        new_top2000 = _make("top2000", score=99)
        merged = merge_charts(existing, [new_top2000])
        assert [c.name for c in merged] == ["top2000", "kerst"]
        assert merged.get_chart("top2000").score == 99

    def test_unrelated_existing_charts_survive_unchanged(self):
        foreign = _make("someones_chart", score=7, positions={"1999": 2})
        merged = merge_charts(ChartList([foreign]), [_make("top40")])
        assert merged.get_chart("someones_chart").to_dict() == foreign.to_dict()
        assert [c.name for c in merged] == ["someones_chart", "top40"]

    def test_new_charts_appended_in_given_order(self):
        merged = merge_charts(ChartList(), [_make("top2000"), _make("top40")])
        assert [c.name for c in merged] == ["top2000", "top40"]

    def test_inputs_not_mutated(self):
        existing = ChartList([_make("top2000", score=1)])
        merge_charts(existing, [_make("top2000", score=99)])
        assert existing.get_chart("top2000").score == 1
        assert len(existing) == 1

    def test_duplicate_generated_names_collapse_to_last(self):
        merged = merge_charts(ChartList(), [_make("x", score=1), _make("x", score=2)])
        assert [c.name for c in merged] == ["x"]
        assert merged.get_chart("x").score == 2


class TestRunReport:
    def test_counts_line_always_first(self):
        report = RunReport(total=3, generated=2)
        assert report.lines()[0] == "Generated charts for 2 of 3 tracks."

    def test_empty_buckets_omitted(self):
        assert RunReport(total=1, generated=1).lines() == [
            "Generated charts for 1 of 1 tracks."
        ]

    def test_buckets_listed_with_entries(self):
        report = RunReport(total=5, generated=1)
        report.unmatched.append("A - B - C")
        report.ambiguous.append(("D - E - F", ["top2000", "top40"]))
        report.unnormalizable.append("!!! - ???")
        report.unreadable.append("bad.mp3")
        report.unwritable.append("ro.mp3")
        report.unparseable_tags.append("corrupt.mp3")
        lines = report.lines()
        assert "Unmatched tracks:" in lines
        assert "  A - B - C" in lines
        assert "Ambiguous tracks (no data generated for the listed charts):" in lines
        assert "  D - E - F [top2000, top40]" in lines
        assert "Tracks whose artist/title normalize to nothing:" in lines
        assert "  !!! - ???" in lines
        assert "Unreadable files:" in lines
        assert "Files that could not be written:" in lines
        assert "Existing CHARTS tags that did not parse:" in lines
