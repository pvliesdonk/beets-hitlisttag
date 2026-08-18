"""Tests for the ChartListType beets field type.

Covers normalize and parse consistency for the ``charts`` flexible
field — both degrade malformed input to the null value rather than
raising uncaught exceptions. to_sql raises ChartsParseException for
unexpected types, as the write path's encoding contract requires.
"""

from __future__ import annotations

import json

import pytest

from beetsplug.hitlisttag import CHARTLISTTYPE
from beetsplug.hitlisttag.charts import Chart, ChartList, ChartsParseException


def _make_chart(name: str = "top2000") -> Chart:
    chart = Chart(name)
    chart.score = 100
    chart.highest = 1
    chart.chart_type = ["year"]
    chart.positions = {"2023": 5}
    return chart


class TestNormalize:
    def test_none_returns_null(self):
        assert CHARTLISTTYPE.normalize(None) is None

    def test_chartlist_passthrough(self):
        cl = ChartList([_make_chart()])
        result = CHARTLISTTYPE.normalize(cl)
        assert result is cl

    def test_valid_string_returns_chartlist(self):
        cl = ChartList([_make_chart()])
        result = CHARTLISTTYPE.normalize(cl.to_json_string())
        assert isinstance(result, ChartList)
        assert result == cl

    def test_malformed_string_returns_null(self):
        assert CHARTLISTTYPE.normalize("not valid json") is None

    def test_valid_json_not_array_returns_null(self):
        assert CHARTLISTTYPE.normalize("42") is None
        assert CHARTLISTTYPE.normalize("null") is None
        assert CHARTLISTTYPE.normalize("{}") is None


class TestParse:
    def test_malformed_string_returns_none(self):
        assert CHARTLISTTYPE.parse("not valid json") is None

    def test_valid_string_returns_chartlist(self):
        cl = ChartList([_make_chart()])
        result = CHARTLISTTYPE.parse(cl.to_json_string())
        assert isinstance(result, ChartList)
        assert result == cl


class TestToSql:
    def test_chartlist_returns_json_string(self):
        cl = ChartList([_make_chart()])
        result = CHARTLISTTYPE.to_sql(cl)
        assert result == cl.to_json_string()

    def test_none_returns_none(self):
        assert CHARTLISTTYPE.to_sql(None) is None


class TestDuplicateChartNames:
    def test_duplicate_chart_names_rejected_in_json(self):
        chart = {
            "name": "top2000",
            "score": 4,
            "highest": 1,
            "chart_type": ["year"],
            "positions": {"2022": 1},
        }
        raw = json.dumps([chart, chart])
        with pytest.raises(ChartsParseException, match="duplicate chart name"):
            ChartList.from_json_string(raw)

    def test_duplicate_chart_names_rejected_in_chart_list(self):
        charts = ChartList.from_json_string(
            json.dumps(
                [
                    {
                        "name": "top2000",
                        "score": 4,
                        "highest": 1,
                        "chart_type": ["year"],
                        "positions": {"2022": 1},
                    }
                ]
            )
        )
        with pytest.raises(ChartsParseException, match="duplicate chart name"):
            ChartList.from_json_string([charts[0], charts[0]])

    def test_duplicate_named_tag_normalizes_to_null(self):
        chart = {
            "name": "top2000",
            "score": 4,
            "highest": 1,
            "chart_type": ["year"],
            "positions": {"2022": 1},
        }
        assert CHARTLISTTYPE.normalize(json.dumps([chart, chart])) is None
