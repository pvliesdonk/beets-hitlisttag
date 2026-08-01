"""Round-trip and robustness tests for the Chart and ChartList model.

These tests verify that chart data survives serialization and
deserialization without loss, and that malformed input produces a clear
``ChartsParseException`` instead of a crash or silent misbehavior.
"""

from __future__ import annotations

import pytest

from beetsplug.charts import Chart, ChartList, ChartsParseException


def _make_chart(
    name: str = "top2000",
    chart_type: tuple[str, ...] = ("year",),
    positions: dict | None = None,
    score: int = 100,
    highest: int = 1,
) -> Chart:
    """Build a fully-populated Chart for testing.

    Positions keys are strings (the JSON canonical form) so that
    round-trips through JSON are exact.
    """
    chart = Chart(name)
    chart.score = score
    chart.highest = highest
    chart.chart_type = list(chart_type)
    chart.positions = positions if positions is not None else {"2023": 5, "2024": 3}
    return chart


# ── Chart round-trip via dict ─────────────────────────────────────────────


class TestChartDictRoundTrip:
    def test_basic(self):
        chart = _make_chart()
        assert Chart.from_dict(chart.to_dict()) == chart

    def test_multi_level_positions(self):
        chart = _make_chart(
            name="top40",
            chart_type=("year", "week"),
            positions={"2023": {"1": 10, "2": 8}, "2024": {"1": 5}},
        )
        assert Chart.from_dict(chart.to_dict()) == chart

    def test_single_position(self):
        chart = _make_chart(positions={"2023": 1})
        assert Chart.from_dict(chart.to_dict()) == chart


# ── Chart round-trip via JSON string ──────────────────────────────────────


class TestChartJsonRoundTrip:
    def test_basic(self):
        chart = _make_chart()
        assert Chart.from_json_string(chart.to_json_string()) == chart

    def test_multi_level_positions(self):
        chart = _make_chart(
            name="top40",
            chart_type=("year", "week"),
            positions={"2023": {"1": 10, "2": 8}},
        )
        assert Chart.from_json_string(chart.to_json_string()) == chart


# ── ChartList round-trip via JSON string ──────────────────────────────────


class TestChartListJsonRoundTrip:
    def test_multiple_charts(self):
        chartlist = ChartList(
            [_make_chart("top2000", score=100), _make_chart("top100", score=50)]
        )
        restored = ChartList.from_json_string(chartlist.to_json_string())
        assert len(restored) == len(chartlist)
        for original, round_tripped in zip(chartlist, restored, strict=True):
            assert round_tripped == original

    def test_empty(self):
        chartlist = ChartList()
        restored = ChartList.from_json_string(chartlist.to_json_string())
        assert restored.is_empty()

    def test_single_chart_single_position(self):
        chart = _make_chart(positions={"2023": 1})
        chartlist = ChartList([chart])
        restored = ChartList.from_json_string(chartlist.to_json_string())
        assert len(restored) == 1
        assert restored[0] == chart


# ── ChartList.from_json_string list branch (#7) ───────────────────────────


class TestChartListFromList:
    def test_list_of_charts(self):
        charts = [_make_chart("top2000"), _make_chart("top100")]
        restored = ChartList.from_json_string(charts)
        assert len(restored) == 2
        assert restored[0] == charts[0]
        assert restored[1] == charts[1]

    def test_empty_list(self):
        restored = ChartList.from_json_string([])
        assert restored.is_empty()


# ── Malformed input → ChartsParseException (#26) ──────────────────────────


class TestMalformedInput:
    def test_empty_dict(self):
        with pytest.raises(ChartsParseException):
            Chart.from_dict({})

    def test_partial_dict(self):
        with pytest.raises(ChartsParseException):
            Chart.from_dict({"name": "x"})

    def test_non_dict(self):
        with pytest.raises(ChartsParseException):
            Chart.from_dict("not a dict")

    def test_wrong_type_for_score(self):
        with pytest.raises(ChartsParseException):
            Chart.from_dict(
                {"name": "x", "score": "abc", "chart_type": ["year"], "positions": {}}
            )

    def test_missing_highest(self):
        with pytest.raises(ChartsParseException):
            Chart.from_dict(
                {"name": "x", "score": 5, "chart_type": ["year"], "positions": {}}
            )

    def test_invalid_json_string(self):
        with pytest.raises(ChartsParseException):
            ChartList.from_json_string("not valid json")

    def test_valid_json_not_array(self):
        for bad in ["42", "null", '"hello"', "{}"]:
            with pytest.raises(ChartsParseException):
                ChartList.from_json_string(bad)

    def test_invalid_type_to_deserialize(self):
        with pytest.raises(ChartsParseException):
            ChartList.from_json_string(42)
