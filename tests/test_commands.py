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

from beetsplug.hitlisttag import HitlistTag
from beetsplug.hitlisttag.charts import Chart, ChartList


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


_UNSET = object()


def _add_item(helper, charts=_UNSET, **kwargs) -> Item:
    """Add an item to the helper's library, optionally setting its charts.

    ``charts`` defaults to a sentinel so that ``None`` is a distinct, explicit
    value: passing ``charts=None`` sets ``item.charts = None`` (the malformed-
    tag path), while omitting ``charts`` leaves the field unset (the no-charts
    path).
    """
    item = helper.add_item(**kwargs)
    if charts is not _UNSET:
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


# ── config-driven hitlists ────────────────────────────────────────────────


class TestConfigDrivenHitlists:
    """The `hitlists` config key drives which per-chart fields exist.

    Covers custom definitions, empty config, default fallback, and the
    unknown-chart guard — all read from config rather than hardcoded.
    """

    def _opts(self, **kwargs):
        defaults = {"format": None, "show_missing": False}
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_default_fallback_uses_shipped_defaults(self, env):
        helper, plugin = env
        # No `hitlists` config set: the shipped defaults apply.
        names = plugin.hitlists
        assert "top2000" in names
        assert "top40" in names
        assert names["top40"] == ["year", "week"]

        assert "top2000" in plugin.item_types
        assert "top2000_score" in plugin.item_types
        assert "top40_when" in plugin.item_types

    def test_custom_hitlist_fields_appear(self, env):
        helper, plugin = env
        config["hitlisttag"]["hitlists"] = {"top500": ["year"]}

        assert plugin.hitlists == {"top500": ["year"]}
        assert "top500" in plugin.item_types
        assert "top500_score" in plugin.item_types
        assert "top500_highest" in plugin.item_types
        assert "top500_when" in plugin.item_types
        # A present `hitlists` key replaces the defaults, it does not merge.
        assert "top2000" not in plugin.item_types

    def test_custom_hitlist_populated_by_update(self, env):
        helper, plugin = env
        config["hitlisttag"]["hitlists"] = {"top500": ["year"]}
        item = _add_item(
            helper,
            charts=ChartList([_make_chart("top500", positions={"2023": 7})]),
            artist="Artist A",
            title="Song A",
            album="Album A",
        )

        plugin.update_item(item)

        assert item["top500"] is True
        assert item["top500_score"] == 100
        assert item["top500_highest"] == "1"
        assert item["top500_when"] == "year: 2023"

    def test_show_hitlist_accepts_custom_hitlist(self, env, capsys):
        helper, plugin = env
        config["hitlisttag"]["hitlists"] = {"top500": ["year"]}
        _add_item(
            helper,
            charts=ChartList([_make_chart("top500", positions={"2023": 2})]),
            artist="Artist A",
            title="Song A",
            album="Album A",
        )

        # A custom-configured hitlist name resolves through the command,
        # not a hardcoded set: the song is listed by position.
        plugin.show_hitlist(helper.lib, self._opts(), ["top500", "2023"])

        out = capsys.readouterr().out
        assert "Artist A" in out

    def test_empty_hitlists_generate_no_fields(self, env):
        helper, plugin = env
        config["hitlisttag"]["hitlists"] = {}

        assert plugin.hitlists == {}
        types = plugin.item_types
        assert "charts" in types
        assert "top2000" not in types
        assert "top2000_score" not in types

    def test_empty_hitlists_update_skips_charts(self, env):
        helper, plugin = env
        config["hitlisttag"]["hitlists"] = {}
        item = _add_item(
            helper,
            charts=ChartList([_make_chart("top2000", positions={"2023": 5})]),
            artist="Artist A",
            title="Song A",
            album="Album A",
        )

        # Must not raise; with no hitlists configured every chart is skipped.
        plugin.update_item(item)

        assert "top2000" not in item._values_flex

    def test_empty_axes_hitlist_skipped(self, env):
        helper, plugin = env
        config["hitlisttag"]["hitlists"] = {"good": ["year"], "bad": []}
        assert plugin.hitlists == {"good": ["year"]}

    def test_empty_hitlists_command_no_crash(self, env):
        helper, plugin = env
        config["hitlisttag"]["hitlists"] = {}

        # No hitlist given, none configured: no crash.
        plugin.show_hitlist(helper.lib, self._opts(), [])
        # An unknown hitlist with none configured: no crash.
        plugin.show_hitlist(helper.lib, self._opts(), ["top2000", "2023"])

    def test_unknown_chart_with_custom_config_skipped(self, env):
        helper, plugin = env
        config["hitlisttag"]["hitlists"] = {"top500": ["year"]}
        # top2000 is in the CHARTS tag but not in the custom config.
        item = _add_item(
            helper,
            charts=ChartList([_make_chart("top2000", positions={"2023": 5})]),
            artist="Artist A",
            title="Song A",
            album="Album A",
        )

        # Must not raise; the chart is logged and skipped, not populated.
        plugin.update_item(item)

        assert "top2000" not in item._values_flex
        # The raw data survives in the charts blob regardless of config.
        assert item.charts is not None

    def test_malformed_hitlists_config_degrades(self, env):
        helper, plugin = env
        # A non-dict value is rejected wholesale rather than crashing.
        config["hitlisttag"]["hitlists"] = "not a dict"

        assert plugin.hitlists == {}
        assert "top2000" not in plugin.item_types

    def test_invalid_axes_entry_dropped(self, env):
        helper, plugin = env
        # A valid entry is kept; an entry whose axes are not a list of
        # strings is dropped with a warning, leaving the rest intact.
        config["hitlisttag"]["hitlists"] = {"good": ["year"], "bad": "not a list"}

        resolved = plugin.hitlists
        assert resolved == {"good": ["year"]}
        assert "good" in plugin.item_types
        assert "bad" not in plugin.item_types
