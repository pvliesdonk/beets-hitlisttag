"""Pure core of CHARTS tag generation.

`build_chart` turns one chart's placements for one song into the `Chart`
object the tag schema stores; `merge_charts` applies replace-per-chart
semantics against a track's existing tag. Both are side-effect free.
`RunReport` accumulates one run's outcome buckets.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .charts import Chart, ChartList
from .lookup import Placement
from .scoring import compute


def build_chart(name: str, axes: list[str], placements: list[Placement]) -> Chart:
    """Build the tag's chart object for one song's placements in one chart.

    `axes` is non-empty; the `hitlists` config validation guarantees it for
    every configured chart. `positions` nests by the chart's configured axis
    order with string keys (matching what JSON round-tripping produces) and
    integer leaf positions. The dataset reader guarantees at most one placement
    per axes-tuple, so nesting never collides.
    """
    result = compute(name, placements)
    positions: dict = {}
    for p in placements:
        node = positions
        for axis in axes[:-1]:
            node = node.setdefault(str(p.axes[axis]), {})
        node[str(p.axes[axes[-1]])] = p.position
    chart = Chart(name)
    chart.chart_type = list(axes)
    chart.positions = positions
    chart.score = result.score
    chart.highest = result.highest
    return chart


def merge_charts(existing: ChartList, generated: list[Chart]) -> ChartList:
    """Merge generated charts into a track's existing chart list.

    Replace-per-chart: a generated chart replaces its namesake wholesale.
    `existing` is a `ChartList`, which never holds two charts with the same
    name (enforced at parse); `generated` may still carry duplicates from a
    buggy caller, and the append step collapses them to the last. Every other
    existing chart survives unchanged -- both charts the dataset does not know
    and charts where this song simply had no match. Charts new to the tag are
    appended in the given order.
    """
    by_name = {chart.name: chart for chart in generated}
    merged = ChartList(by_name.pop(chart.name, chart) for chart in existing)
    merged.extend(
        by_name.pop(chart.name) for chart in generated if chart.name in by_name
    )
    return merged


@dataclass
class RunReport:
    """Outcome buckets of one chartsgen run, and their printable form."""

    total: int = 0
    generated: int = 0
    unmatched: list[str] = field(default_factory=list)
    ambiguous: list[tuple[str, list[str]]] = field(default_factory=list)
    unnormalizable: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    unwritable: list[str] = field(default_factory=list)
    unparseable_tags: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"Generated charts for {self.generated} of {self.total} tracks."]

        def bucket(header: str, entries: list[str]) -> None:
            if entries:
                out.append(header)
                out.extend(f"  {entry}" for entry in entries)

        bucket("Unmatched tracks:", self.unmatched)
        bucket(
            "Ambiguous tracks (no data generated for the listed charts):",
            [f"{track} [{', '.join(charts)}]" for track, charts in self.ambiguous],
        )
        bucket(
            "Tracks whose artist/title normalize to nothing:",
            self.unnormalizable,
        )
        bucket("Unreadable files:", self.unreadable)
        bucket("Files that could not be written:", self.unwritable)
        bucket("Existing CHARTS tags that did not parse:", self.unparseable_tags)
        return out
