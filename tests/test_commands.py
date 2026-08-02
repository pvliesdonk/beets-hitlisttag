"""Command-level tests for chartsupdate, charts, and hitlist edge cases.

These tests exercise the three plugin commands against a real, temporary
beets library, covering the edge cases the acceptance criterion names:
no chart data, empty results, bad arguments, and malformed (None) chart
data. None of these paths may raise a traceback.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from beets import config
from beets.library import Item
from beets.plugins import find_plugins, load_plugins

from beetsplug.charts import Chart, ChartList
from beetsplug.hitlisttag import HitlistTag


def _make_chart(
    name: str = "top2000",
    chart_type: tuple[str, ...] = ("year",),
    positions: dict | None = None,
    score: int = 100,
    highest: int = 1,
) -> Chart:
    """Build a fully-populated Chart for testing."""
    chart = Chart(name)
    chart.score = score
    chart.highest = highest
    chart.chart_type = list(chart_type)
    chart.positions = positions if positions is not None else {"2023": 5}
    return chart


def _add_item(helper, charts=None, **kwargs) -> Item:
    """Add an item to the helper's library, optionally setting its charts."""
    item = helper.add_item(**kwargs)
    if charts is not None:
        item.charts = charts
        item.store()
    return item


@pytest.fixture
def env():
    """A beets TestHelper with the hitlisttag plugin loaded.

    ``TestHelper`` is imported inside the fixture because ``beets.test.helper``
    mutates ``sys.path``; importing it at module level would drop the current
    directory before ``beetsplug`` is cached in ``sys.modules``.
    """
    from beets.test.helper import TestHelper

    helper = TestHelper()
    with helper:
        config["plugins"] = ["hitlisttag"]
        load_plugins()
        plugin = next(p for p in find_plugins() if isinstance(p, HitlistTag))
        yield helper, plugin


# ── chartsupdate ───────────────────────────────────────────────────────────


class TestChartsUpdate:
    def test_valid_chart_populates_flexible_fields(self, env):
        helper, plugin = env
        item = _add_item(
            helper,
            charts=ChartList([_make_chart("top2000", positions={"2023": 5})]),
            artist="Artist A",
            title="Song A",
            album="Album A",
        )

        plugin.update_item(item)

        assert item["top2000"] is True
        assert item["top2000_score"] == 100
        assert item["top2000_highest"] == "1"
        assert item["top2000_when"] == "year: 2023"

    def test_no_charts_key_returns_early(self, env):
        helper, plugin = env
        item = _add_item(helper, artist="Artist B", title="Song B", album="Album B")

        # Must not raise.
        plugin.update_item(item)

        assert "top2000" not in item._values_flex

    def test_none_charts_returns_early(self, env):
        """A malformed CHARTS tag normalizes to None; update must skip it."""
        helper, plugin = env
        item = _add_item(
            helper,
            charts=None,
            artist="Artist C",
            title="Song C",
            album="Album C",
        )

        # Must not raise (guarded since #41).
        plugin.update_item(item)

        assert "top2000" not in item._values_flex

    def test_unknown_hitlist_skipped(self, env):
        helper, plugin = env
        item = _add_item(
            helper,
            charts=ChartList([_make_chart("nonexistent", positions={"2023": 3})]),
            artist="Artist D",
            title="Song D",
            album="Album D",
        )

        # Must not raise; unknown chart is logged and skipped.
        plugin.update_item(item)

        assert "nonexistent" not in item._values_flex


# ── charts ─────────────────────────────────────────────────────────────────


class TestCharts:
    def test_summary_prints_chart_info(self, env, capsys):
        helper, plugin = env
        _add_item(
            helper,
            charts=ChartList(
                [_make_chart("top2000", positions={"2023": 5, "2024": 3})]
            ),
            artist="Artist A",
            title="Song A",
            album="Album A",
        )

        plugin.show_charts_summary(helper.lib, SimpleNamespace(), [])

        out = capsys.readouterr().out
        assert "top2000" in out
        assert "Artist A" in out

    def test_full_prints_positions(self, env, capsys):
        helper, plugin = env
        _add_item(
            helper,
            charts=ChartList([_make_chart("top2000", positions={"2023": 5})]),
            artist="Artist A",
            title="Song A",
            album="Album A",
        )

        opts = SimpleNamespace(full=True)
        plugin.show_charts_full(helper.lib, opts, [])

        out = capsys.readouterr().out
        assert "top2000" in out
        assert "position" in out

    def test_no_charts_key_skipped(self, env, capsys):
        helper, plugin = env
        _add_item(helper, artist="Artist B", title="Song B", album="Album B")

        # Must not raise.
        plugin.show_charts_summary(helper.lib, SimpleNamespace(), [])

        out = capsys.readouterr().out
        # Nothing printed for an item without charts.
        assert "top2000" not in out

    def test_none_charts_skipped(self, env, capsys):
        """A malformed CHARTS tag normalizes to None; show_charts must skip it."""
        helper, plugin = env
        _add_item(
            helper,
            charts=None,
            artist="Artist C",
            title="Song C",
            album="Album C",
        )

        # Must not raise (guarded since #41).
        plugin.show_charts_summary(helper.lib, SimpleNamespace(), [])

        out = capsys.readouterr().out
        assert "top2000" not in out


# ── hitlist ────────────────────────────────────────────────────────────────


class TestHitlist:
    def _opts(self, **kwargs):
        defaults = {"format": None, "show_missing": False}
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_no_arguments(self, env):
        helper, plugin = env
        # Must not raise.
        plugin.show_hitlist(helper.lib, self._opts(), [])

    def test_unknown_hitlist(self, env):
        helper, plugin = env
        # Must not raise.
        plugin.show_hitlist(helper.lib, self._opts(), ["nonexistent", "2023"])

    def test_wrong_argument_count(self, env):
        helper, plugin = env
        # top2000 expects [year]; no year given.
        plugin.show_hitlist(helper.lib, self._opts(), ["top2000"])

    def test_non_numeric_arguments(self, env):
        helper, plugin = env
        # Must not raise; validated and returned.
        plugin.show_hitlist(helper.lib, self._opts(), ["top2000", "abc"])

    def test_empty_library_no_crash(self, env, capsys):
        """No items at all — #10: max([]) used to raise ValueError."""
        helper, plugin = env

        plugin.show_hitlist(helper.lib, self._opts(), ["top2000", "2023"])

        out = capsys.readouterr().out
        assert "No positions found" in out

    def test_no_matching_items_no_crash(self, env, capsys):
        """Items exist but none carry the requested chart/date — #10."""
        helper, plugin = env
        _add_item(helper, artist="Artist", title="Song", album="Album")

        plugin.show_hitlist(helper.lib, self._opts(), ["top2000", "2023"])

        out = capsys.readouterr().out
        assert "No positions found" in out

    def test_valid_hitlist_prints_results(self, env, capsys):
        helper, plugin = env
        _add_item(
            helper,
            charts=ChartList([_make_chart("top2000", positions={"2023": 5})]),
            artist="Artist A",
            title="Song A",
            album="Album A",
        )
        _add_item(
            helper,
            charts=ChartList([_make_chart("top2000", positions={"2023": 1})]),
            artist="Artist B",
            title="Song B",
            album="Album B",
        )

        plugin.show_hitlist(helper.lib, self._opts(), ["top2000", "2023"])

        out = capsys.readouterr().out
        # Sorted by position, so Artist B (position 1) before Artist A (5).
        assert out.index("Artist B") < out.index("Artist A")

    def test_show_missing_reports_gaps(self, env, capsys):
        helper, plugin = env
        # Position 2 is missing.
        _add_item(
            helper,
            charts=ChartList([_make_chart("top2000", positions={"2023": 1})]),
            artist="Artist A",
            title="Song A",
            album="Album A",
        )
        _add_item(
            helper,
            charts=ChartList([_make_chart("top2000", positions={"2023": 3})]),
            artist="Artist B",
            title="Song B",
            album="Album B",
        )

        plugin.show_hitlist(
            helper.lib, self._opts(show_missing=True), ["top2000", "2023"]
        )

        out = capsys.readouterr().out
        assert "Missing" in out
        assert "2" in out
