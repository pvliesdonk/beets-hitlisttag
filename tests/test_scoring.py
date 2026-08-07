"""Tests for per-chart score and highest (beetsplug.hitlisttag.scoring)."""

from __future__ import annotations

import pytest

from beetsplug.hitlisttag import scoring
from beetsplug.hitlisttag.lookup import Placement
from beetsplug.hitlisttag.scoring import ChartScore, compute


def _p(position, size=40, axes=None):
    return Placement(
        axes=axes or {"year": 2023, "week": 1}, position=position, size=size
    )


def test_highest_is_lowest_position():
    assert compute("top40", [_p(5), _p(2), _p(9)]).highest == 2


def test_score_sums_size_plus_one_minus_position():
    # size 40: #1 -> 40, #2 -> 39; sum 79
    result = compute("top40", [_p(1), _p(2)])
    assert result.score == 79
    assert result.highest == 1


def test_declared_size_used_not_entry_count():
    # a single #1 in a size-40 edition scores 40, not 1
    assert compute("top40", [_p(1)]).score == 40


def test_top_and_bottom_positions():
    assert compute("top40", [_p(1)]).score == 40  # N
    assert compute("top40", [_p(40)]).score == 1  # 1
    assert compute("top40", [_p(40)]).highest == 40


def test_per_chart_scorer_is_dispatched(monkeypatch):
    sentinel = ChartScore(score=-1, highest=-1)
    monkeypatch.setitem(scoring._SCORERS, "special", lambda placements: sentinel)
    assert compute("special", [_p(1)]) is sentinel
    # a chart with no registered scorer still uses the default
    assert compute("top40", [_p(1)]) == ChartScore(score=40, highest=1)


def test_empty_placements_raises():
    with pytest.raises(ValueError, match="empty"):
        compute("top40", [])
