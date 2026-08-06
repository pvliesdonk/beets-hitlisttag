"""Tests for the chart-edition dataset reader (beetsplug.hitlisttag.dataset)."""

from __future__ import annotations

import logging
from pathlib import Path

from beetsplug.hitlisttag.dataset import (
    HitlistData,
    Song,
    read_dataset,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dataset"

# Axis definitions matching the shipped defaults the fixtures use.
HITLISTS = {"top2000": ["year"], "top40": ["year", "week"]}

log = logging.getLogger("test.dataset")


def _by_chart(datasets: list[HitlistData]) -> dict[str, HitlistData]:
    return {d.chart: d for d in datasets}


def test_reads_valid_fixtures():
    result = _by_chart(read_dataset(FIXTURES, HITLISTS, log))
    assert set(result) == {"top2000", "top40"}

    top2000 = result["top2000"]
    assert [e.axes for e in top2000.editions] == [{"year": 2022}, {"year": 2023}]
    first = top2000.editions[0]
    assert first.size == 3
    assert first.entries[0].position == 1
    assert first.entries[0].songs == [Song(id="1", artist="Artist A", title="Song A")]


def test_song_shared_across_editions_resolves_to_same_song():
    top2000 = _by_chart(read_dataset(FIXTURES, HITLISTS, log))["top2000"]
    song_2022 = top2000.editions[0].entries[0].songs[0]
    song_2023 = top2000.editions[1].entries[0].songs[0]
    assert song_2022 == song_2023 == Song(id="1", artist="Artist A", title="Song A")


def test_multi_song_entry_is_one_entry_with_several_songs():
    top40 = _by_chart(read_dataset(FIXTURES, HITLISTS, log))["top40"]
    week41 = top40.editions[1]
    pos1 = next(e for e in week41.entries if e.position == 1)
    assert [s.id for s in pos1.songs] == ["3", "4"]
    assert {s.artist for s in pos1.songs} == {"Artist C"}
