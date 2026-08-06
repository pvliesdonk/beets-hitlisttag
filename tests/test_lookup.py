"""Tests for the normalized exact song lookup (beetsplug.hitlisttag.lookup)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from beetsplug.hitlisttag.dataset import Edition, Entry, HitlistData, Song
from beetsplug.hitlisttag.lookup import (
    Placement,
    SongLookupIndex,
    normalize,
)

log = logging.getLogger("test.lookup")


def _data(chart, songs, editions, source="mem"):
    return HitlistData(
        chart=chart,
        songs={s.id: s for s in songs},
        editions=editions,
        source=Path(source),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Beyoncé", "beyonce"),
        ("BEYONCE", "beyonce"),
        ("Mötley Crüe", "motley crue"),
        ("Straße", "strasse"),
        ("Encyclopædia", "encyclopaedia"),
        ("Encyclopaedia", "encyclopaedia"),
        ("Cœur", "coeur"),
        ("Søren", "soren"),
        ("Łódź", "lodz"),
        ("Þórr", "thorr"),
        ("Đevojka", "devojka"),
        ("Håkan", "hakan"),
        ("İstanbul", "istanbul"),
        ("naïve café", "naive cafe"),
        ("Café  del   Mar!", "cafe del mar"),
        ("AC/DC", "ac dc"),
        ("...", ""),
        ("   ", ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_single_chart_hit_returns_placements():
    a = Song(id="1", artist="Beyoncé", title="Halo")
    data = _data(
        "top40",
        [a],
        [Edition(axes={"year": 2023, "week": 1}, size=40, entries=[Entry(1, [a])])],
    )
    index = SongLookupIndex.from_datasets([data], log)

    result = index.lookup("beyonce", "halo")
    assert result.normalized == ("beyonce", "halo")
    assert result.ambiguous_charts == set()
    assert result.placements == {
        "top40": [Placement(axes={"year": 2023, "week": 1}, position=1, size=40)]
    }


def test_song_in_two_charts_aggregates_not_ambiguous():
    a1 = Song(id="1", artist="ABBA", title="SOS")
    a2 = Song(id="1", artist="ABBA", title="SOS")
    top2000 = _data("top2000", [a1], [Edition({"year": 2022}, 5, [Entry(3, [a1])])])
    top40 = _data(
        "top40", [a2], [Edition({"year": 1975, "week": 20}, 40, [Entry(7, [a2])])]
    )
    index = SongLookupIndex.from_datasets([top2000, top40], log)

    result = index.lookup("abba", "sos")
    assert set(result.placements) == {"top2000", "top40"}
    assert result.ambiguous_charts == set()
    assert result.placements["top2000"][0].position == 3
    assert result.placements["top40"][0].position == 7


def test_song_across_editions_yields_several_placements():
    a = Song(id="1", artist="A", title="T")
    data = _data(
        "top40",
        [a],
        [
            Edition({"year": 2023, "week": 40}, 40, [Entry(1, [a])]),
            Edition({"year": 2023, "week": 41}, 40, [Entry(2, [a])]),
        ],
    )
    index = SongLookupIndex.from_datasets([data], log)

    positions = [p.position for p in index.lookup("a", "t").placements["top40"]]
    assert positions == [1, 2]


def test_miss_returns_empty_result():
    a = Song(id="1", artist="A", title="T")
    data = _data(
        "top40", [a], [Edition({"year": 2023, "week": 1}, 40, [Entry(1, [a])])]
    )
    index = SongLookupIndex.from_datasets([data], log)

    result = index.lookup("nobody", "nothing")
    assert result.normalized == ("nobody", "nothing")
    assert result.placements == {}
    assert result.ambiguous_charts == set()
    assert result.is_miss
