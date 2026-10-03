"""Tests for chart acquisition: merge, per-chart orchestration."""

from __future__ import annotations

import logging
from pathlib import Path

from beetsplug.hitlisttag.acquire import merge_acquired
from beetsplug.hitlisttag.dataset import Edition, Entry, HitlistData, Song
from beetsplug.hitlisttag.ingest import AcquiredEdition, EditionRef, RawEntry, RawSong

log = logging.getLogger("test.acquire")
SRC = Path("data/fake.json")


def _acq(year: int, *pairs: tuple[str, str], size: int | None = None):
    entries = tuple(
        RawEntry(i + 1, (RawSong(artist, title),))
        for i, (artist, title) in enumerate(pairs)
    )
    return AcquiredEdition(EditionRef({"year": year}), size or len(pairs), entries)


def _existing(songs: dict[str, tuple[str, str]], editions=()) -> HitlistData:
    return HitlistData(
        chart="fake",
        songs={sid: Song(sid, a, t) for sid, (a, t) in songs.items()},
        editions=list(editions),
        source=SRC,
    )


def _ids(data: HitlistData, year: int) -> list[str]:
    edition = next(e for e in data.editions if e.axes["year"] == year)
    return [s.id for entry in edition.entries for s in entry.songs]


class TestMergeAcquired:
    def test_new_file_mints_from_one(self):
        data, new = merge_acquired(
            None, "fake", ["year"], [_acq(2001, ("A", "x"), ("B", "y"))], SRC, log
        )
        assert data.chart == "fake" and data.source == SRC
        assert _ids(data, 2001) == ["1", "2"]
        assert new == 2

    def test_reuses_ids_by_normalized_key(self):
        existing = _existing({"7": ("Queen", "Bohemian Rhapsody")})
        data, new = merge_acquired(
            existing,
            "fake",
            ["year"],
            [_acq(2001, ("queen", "Bohemian  rhapsody!"))],
            SRC,
            log,
        )
        assert _ids(data, 2001) == ["7"]
        assert new == 0
        assert data.songs["7"].artist == "Queen"  # existing spelling kept

    def test_minting_continues_past_numeric_max_ignoring_other_ids(self):
        existing = _existing({"3": ("A", "x"), "12": ("B", "y"), "hand-1": ("C", "z")})
        data, _ = merge_acquired(
            existing, "fake", ["year"], [_acq(2001, ("D", "w"))], SRC, log
        )
        assert _ids(data, 2001) == ["13"]
        assert "hand-1" in data.songs

    def test_duplicate_existing_keys_reuse_lowest_id_with_warning(self, caplog):
        existing = _existing({"9": ("A", "x"), "4": ("a", "X")})
        with caplog.at_level(logging.WARNING):
            data, _ = merge_acquired(
                existing, "fake", ["year"], [_acq(2001, ("A", "x"))], SRC, log
            )
        assert _ids(data, 2001) == ["4"]
        assert "'4'" in caplog.text and "'9'" in caplog.text

    def test_same_key_twice_in_one_edition_gets_distinct_ids(self):
        data, new = merge_acquired(
            None,
            "fake",
            ["year"],
            [_acq(2001, ("Abba", "Song"), ("ABBA", "song"))],
            SRC,
            log,
        )
        assert _ids(data, 2001) == ["1", "2"]
        assert new == 2

    def test_same_song_across_editions_shares_id(self):
        data, new = merge_acquired(
            None,
            "fake",
            ["year"],
            [_acq(2001, ("A", "x")), _acq(2002, ("A", "x"))],
            SRC,
            log,
        )
        assert _ids(data, 2001) == _ids(data, 2002) == ["1"]
        assert new == 1

    def test_unnormalizable_names_never_reuse(self):
        existing = _existing({"1": ("A", "?!")})
        data, new = merge_acquired(
            existing, "fake", ["year"], [_acq(2001, ("A", "?!"))], SRC, log
        )
        assert _ids(data, 2001) == ["2"]
        assert new == 1

    def test_existing_editions_and_unreferenced_songs_survive(self):
        song = Song("1", "Hand", "Made")
        lonely = Song("5", "No", "Edition")
        existing = HitlistData(
            "fake",
            {"1": song, "5": lonely},
            [Edition({"year": 1999}, 10, [Entry(3, [song])])],
            SRC,
        )
        data, _ = merge_acquired(
            existing, "fake", ["year"], [_acq(2001, ("B", "y"))], SRC, log
        )
        assert [e.axes["year"] for e in data.editions] == [1999, 2001]
        assert data.editions[0].size == 10
        assert "5" in data.songs

    def test_editions_sorted_by_configured_axes_and_songs_by_id(self):
        existing = _existing({"2": ("A", "x"), "10": ("B", "y")})
        data, _ = merge_acquired(
            existing,
            "fake",
            ["year"],
            [_acq(2005, ("C", "z")), _acq(2001, ("D", "w"))],
            SRC,
            log,
        )
        assert [e.axes["year"] for e in data.editions] == [2001, 2005]
        assert list(data.songs) == ["2", "10", "11", "12"]

    def test_two_axis_edition_axes_in_configured_order(self):
        acquired = AcquiredEdition(
            EditionRef({"week": 7, "year": 2024}),
            1,
            (RawEntry(1, (RawSong("A", "x"),)),),
        )
        data, _ = merge_acquired(None, "fake", ["year", "week"], [acquired], SRC, log)
        assert list(data.editions[0].axes) == ["year", "week"]
