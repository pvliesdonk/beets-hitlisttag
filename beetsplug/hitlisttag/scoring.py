"""Per-chart score and highest computation for generated CHARTS tags.

`compute(chart, placements)` returns the `score` and `highest` the tag schema
stores. Scoring is resolved per chart: `_SCORERS` maps a chart to its scorer,
and every chart absent from it uses `_default_score` -- the Top 40's official
positional-points sum. Adding a chart-specific scorer is a one-line
registration, with no change to the dataset format or the tag schema.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .lookup import Placement


@dataclass
class ChartScore:
    score: int
    highest: int


def _default_score(placements: list[Placement]) -> ChartScore:
    """Top 40 official scoring.

    highest is the best (lowest) position reached; score sums
    (size + 1 - position) over every appearance, using the declared edition
    size so partial hand-authored editions score correctly.
    """
    if not placements:
        raise ValueError("cannot score an empty list of placements")
    highest = min(p.position for p in placements)
    score = sum(p.size + 1 - p.position for p in placements)
    return ChartScore(score=score, highest=highest)


# Per-chart overrides; empty today -- every chart uses the default.
_SCORERS: dict[str, Callable[[list[Placement]], ChartScore]] = {}


def compute(chart: str, placements: list[Placement]) -> ChartScore:
    """Compute score and highest for a song's placements in one chart."""
    return _SCORERS.get(chart, _default_score)(placements)
