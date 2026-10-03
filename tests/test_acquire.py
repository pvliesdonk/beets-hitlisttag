"""Tests for chart acquisition: merge, per-chart orchestration."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from beetsplug.hitlisttag.acquire import ChartResult, acquire_chart, merge_acquired
from beetsplug.hitlisttag.dataset import Edition, Entry, HitlistData, Song, read_dataset
from beetsplug.hitlisttag.ingest import (
    AcquiredEdition,
    EditionRef,
    IngestError,
    RawEntry,
    RawSong,
)

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


HITLISTS = {"fake": ["year"]}


class FakeIngestor:
    """In-memory ingestor: {year: [(artist, title), ...]}; records fetches."""

    chart = "fake"
    axes = ("year",)

    def __init__(self, editions, fail_on=None, as_generator=False, repeat=False):
        self.data = editions
        self.fail_on = fail_on
        self.as_generator = as_generator
        self.repeat = repeat
        self.fetched: list[int] = []

    def editions(self):
        refs = [EditionRef({"year": y}) for y in self.data]
        if self.repeat:
            refs = refs + refs
        return (r for r in refs) if self.as_generator else refs

    def fetch(self, ref):
        year = ref.axes["year"]
        self.fetched.append(year)
        if year == self.fail_on:
            raise IngestError(f"source broke on {year}")
        return _acq(year, *self.data[year])


def _read(tmp_path):
    found = read_dataset(tmp_path, HITLISTS, log)
    return found[0] if found else None


class TestAcquireChart:
    def test_first_run_writes_file(self, tmp_path):
        path = tmp_path / "fake.json"
        ing = FakeIngestor({2001: [("A", "x"), ("B", "y")], 2002: [("A", "x")]})
        result = acquire_chart(ing, ["year"], None, path, HITLISTS, log)
        assert result.error is None
        assert [r.axes["year"] for r in result.acquired] == [2001, 2002]
        assert result.entries == 3 and result.new_songs == 2
        assert [e.axes["year"] for e in _read(tmp_path).editions] == [2001, 2002]

    def test_up_to_date_does_not_rewrite(self, tmp_path):
        path = tmp_path / "fake.json"
        ing = FakeIngestor({2001: [("A", "x")]})
        acquire_chart(ing, ["year"], None, path, HITLISTS, log)
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        again = FakeIngestor({2001: [("A", "x")]})
        result = acquire_chart(again, ["year"], _read(tmp_path), path, HITLISTS, log)
        assert result.error is None and result.acquired == []
        assert result.held == 1
        assert again.fetched == []
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before

    def test_only_missing_editions_fetched_and_ids_reused(self, tmp_path):
        path = tmp_path / "fake.json"
        acquire_chart(
            FakeIngestor({2001: [("A", "x")]}), ["year"], None, path, HITLISTS, log
        )
        grown = FakeIngestor({2001: [("A", "x")], 2002: [("a", "X"), ("C", "z")]})
        result = acquire_chart(grown, ["year"], _read(tmp_path), path, HITLISTS, log)
        assert grown.fetched == [2002]
        assert result.new_songs == 1
        data = _read(tmp_path)
        e2002 = next(e for e in data.editions if e.axes["year"] == 2002)
        assert [s.id for en in e2002.entries for s in en.songs] == ["1", "2"]

    def test_ingest_error_leaves_file_unchanged(self, tmp_path):
        path = tmp_path / "fake.json"
        acquire_chart(
            FakeIngestor({2001: [("A", "x")]}), ["year"], None, path, HITLISTS, log
        )
        before = path.read_bytes()
        failing = FakeIngestor(
            {2001: [("A", "x")], 2002: [("B", "y")], 2003: [("C", "z")]}, fail_on=2003
        )
        result = acquire_chart(failing, ["year"], _read(tmp_path), path, HITLISTS, log)
        assert result.error == "source broke on 2003"
        assert path.read_bytes() == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ["fake.json"]

    def test_contract_violation_reported(self, tmp_path):
        class Broken(FakeIngestor):
            def fetch(self, ref):
                return AcquiredEdition(ref, 1, (RawEntry(2, (RawSong("A", "x"),)),))

        result = acquire_chart(
            Broken({2001: []}), ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert result.error.startswith("ingestor for fake broke its contract:")
        assert not (tmp_path / "fake.json").exists()

    def test_axes_mismatch_reported_without_fetch(self, tmp_path):
        ing = FakeIngestor({2001: [("A", "x")]})
        result = acquire_chart(
            ing, ["year", "week"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert "['year']" in result.error and "['year', 'week']" in result.error
        assert ing.fetched == []

    def test_write_error_reported(self, tmp_path, monkeypatch):
        import beetsplug.hitlisttag.acquire as acquire_module

        def boom(*args, **kwargs):
            raise OSError("read-only file system")

        monkeypatch.setattr(acquire_module, "write_dataset_file", boom)
        path = tmp_path / "fake.json"
        result = acquire_chart(
            FakeIngestor({2001: [("A", "x")]}), ["year"], None, path, HITLISTS, log
        )
        assert result.error == f"cannot write {path}: read-only file system"

    def test_generator_and_repeated_refs_read_once_and_deduplicated(self, tmp_path):
        # Review focus 3.
        ing = FakeIngestor(
            {2001: [("A", "x")], 2002: [("B", "y")]}, as_generator=True, repeat=True
        )
        result = acquire_chart(
            ing, ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert result.error is None
        assert ing.fetched == [2001, 2002]

    def test_unlisted_editions_kept_and_reported(self, tmp_path):
        path = tmp_path / "fake.json"
        acquire_chart(
            FakeIngestor({1990: [("Old", "one")]}), ["year"], None, path, HITLISTS, log
        )
        result = acquire_chart(
            FakeIngestor({2001: [("A", "x")]}),
            ["year"],
            _read(tmp_path),
            path,
            HITLISTS,
            log,
        )
        assert result.unlisted == [(1990,)]
        assert [e.axes["year"] for e in _read(tmp_path).editions] == [1990, 2001]


class TestChartResultLines:
    def test_acquired_line_with_year_ranges(self):
        refs = [EditionRef({"year": y}) for y in (1999, 2000, 2001, 2005)]
        r = ChartResult("top2000", acquired=refs, entries=8000, new_songs=4925)
        assert r.lines(["year"]) == [
            "top2000: acquired 4 editions (1999–2001, 2005), "
            "8,000 entries, 4,925 new songs"
        ]

    def test_single_edition_and_multi_axis(self):
        one = ChartResult(
            "top2000", acquired=[EditionRef({"year": 2025})], entries=2000, new_songs=3
        )
        assert one.lines(["year"]) == [
            "top2000: acquired 1 edition (2025), 2,000 entries, 3 new songs"
        ]
        refs = [EditionRef({"year": 2024, "week": w}) for w in (1, 2)]
        multi = ChartResult("top40", acquired=refs, entries=80, new_songs=0)
        assert multi.lines(["year", "week"]) == [
            "top40: acquired 2 editions, 80 entries, 0 new songs"
        ]

    def test_up_to_date_failed_and_unlisted_lines(self):
        assert ChartResult("x", held=27).lines(["year"]) == [
            "x: up to date (27 editions)"
        ]
        assert ChartResult("x", error="boom").lines(["year"]) == [
            "x: FAILED — boom; file unchanged"
        ]
        r = ChartResult("x", held=3, unlisted=[(1990,), (1991,)])
        assert r.lines(["year"])[-1] == (
            "x: 2 editions in the file are not listed by the source (1990–1991); kept"
        )


class TestFinalReviewFixes:
    """Findings from the whole-branch review, pinned before their fixes."""

    def test_existing_file_for_another_chart_is_not_overwritten(self, tmp_path):
        # Important 1: <chart>.json exists but holds a different chart.
        path = tmp_path / "fake.json"
        path.write_text(
            '{"chart": "other", "songs": {}, "editions": []}\n', encoding="utf-8"
        )
        before = path.read_bytes()
        ing = FakeIngestor({2001: [("A", "x")]})
        result = acquire_chart(ing, ["year"], None, path, HITLISTS, log)
        assert "exists but does not hold fake" in result.error
        assert path.read_bytes() == before
        assert ing.fetched == []

    def test_os_error_from_ingestor_is_not_reported_as_write_failure(self, tmp_path):
        # Important 2: an ingestor's network error propagates (#99 contract).
        class Offline(FakeIngestor):
            def fetch(self, ref):
                raise ConnectionError("connection refused")

        path = tmp_path / "fake.json"
        with pytest.raises(ConnectionError):
            acquire_chart(Offline({2001: []}), ["year"], None, path, HITLISTS, log)
        assert not path.exists()
        assert list(tmp_path.iterdir()) == []

    def test_unknown_fields_in_existing_file_block_the_write(self, tmp_path):
        # Important 3 (re-graded): extras would be silently dropped.
        path = tmp_path / "fake.json"
        path.write_text(
            json.dumps(
                {
                    "chart": "fake",
                    "note": "hand curated",
                    "songs": {"1": {"artist": "A", "title": "x", "mbid": "abc"}},
                    "editions": [],
                }
            ),
            encoding="utf-8",
        )
        before = path.read_bytes()
        result = acquire_chart(
            FakeIngestor({2001: [("A", "x")]}),
            ["year"],
            _read(tmp_path),
            path,
            HITLISTS,
            log,
        )
        assert "would drop" in result.error
        assert "'note'" in result.error and "'mbid'" in result.error
        assert path.read_bytes() == before


class TestSymlinkedDatasetFile:
    def test_symlink_kept_and_target_updated(self, tmp_path):
        # Important 5 (re-graded): replacing must write through the link.
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        target = real_dir / "fake.json"
        target.write_text(
            '{"chart": "fake", "songs": {}, "editions": []}\n', encoding="utf-8"
        )
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        link = data_dir / "fake.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported here")
        existing = read_dataset(data_dir, HITLISTS, log)[0]
        result = acquire_chart(
            FakeIngestor({2001: [("A", "x")]}), ["year"], existing, link, HITLISTS, log
        )
        assert result.error is None
        assert link.is_symlink()
        assert '"year": 2001' in target.read_text(encoding="utf-8")


class TestPrReviewMinting:
    def test_unicode_digit_existing_id_does_not_crash_minting(self):
        existing = _existing({"²": ("A", "x")})
        data, new = merge_acquired(
            existing, "fake", ["year"], [_acq(2001, ("B", "y"))], SRC, log
        )
        assert _ids(data, 2001) == ["1"]
        assert list(data.songs) == ["1", "²"]
        assert new == 1


class TestForce:
    """#101: --force re-acquires listed editions; --prune drops unlisted ones."""

    def _seed(self, tmp_path, editions):
        path = tmp_path / "fake.json"
        acquire_chart(FakeIngestor(editions), ["year"], None, path, HITLISTS, log)
        return path

    def test_force_replaces_same_axes_editions_and_keeps_ids(self, tmp_path):
        path = self._seed(tmp_path, {2001: [("A", "x"), ("B", "y")]})
        src = FakeIngestor({2001: [("B", "y"), ("a", "X"), ("C", "z")]})
        result = acquire_chart(
            src, ["year"], _read(tmp_path), path, HITLISTS, log, force=True
        )
        assert result.error is None and src.fetched == [2001]
        data = _read(tmp_path)
        assert [e.axes["year"] for e in data.editions] == [2001]  # not duplicated
        assert _ids(data, 2001) == ["2", "1", "3"]  # B, A reused; C new
        assert result.new_songs == 1
        assert result.lines(["year"])[0].startswith("fake: re-acquired 1 edition")

    def test_force_replaces_hand_authored_partial_edition(self, tmp_path):
        path = tmp_path / "fake.json"
        song = Song("1", "A", "x")
        partial = HitlistData(
            "fake", {"1": song}, [Edition({"year": 2001}, 3, [Entry(2, [song])])], path
        )
        from beetsplug.hitlisttag.dataset import write_dataset_file

        write_dataset_file(partial, path, HITLISTS, log)
        src = FakeIngestor({2001: [("C", "z"), ("A", "x"), ("D", "w")]})
        acquire_chart(src, ["year"], _read(tmp_path), path, HITLISTS, log, force=True)
        edition = _read(tmp_path).editions[0]
        assert [e.position for e in edition.entries] == [1, 2, 3]
        assert _ids(_read(tmp_path), 2001) == ["2", "1", "3"]

    def test_force_without_prune_keeps_unlisted(self, tmp_path):
        path = self._seed(tmp_path, {1990: [("Old", "one")], 2001: [("A", "x")]})
        src = FakeIngestor({2001: [("A", "x")]})
        result = acquire_chart(
            src, ["year"], _read(tmp_path), path, HITLISTS, log, force=True
        )
        assert [e.axes["year"] for e in _read(tmp_path).editions] == [1990, 2001]
        assert result.lines(["year"])[-1].endswith("(1990); kept")

    def test_force_with_prune_drops_unlisted_editions_but_keeps_songs(self, tmp_path):
        path = self._seed(tmp_path, {1990: [("Old", "one")], 2001: [("A", "x")]})
        src = FakeIngestor({2001: [("A", "x")]})
        result = acquire_chart(
            src, ["year"], _read(tmp_path), path, HITLISTS, log, force=True, prune=True
        )
        data = _read(tmp_path)
        assert [e.axes["year"] for e in data.editions] == [2001]
        assert data.songs["1"].artist == "Old"  # songs are never deleted
        assert result.lines(["year"])[-1] == (
            "fake: dropped 1 edition not listed by the source (1990)"
        )

    def test_prune_refuses_when_source_lists_nothing(self, tmp_path):
        path = self._seed(tmp_path, {2001: [("A", "x")]})
        before = path.read_bytes()
        result = acquire_chart(
            FakeIngestor({}),
            ["year"],
            _read(tmp_path),
            path,
            HITLISTS,
            log,
            force=True,
            prune=True,
        )
        assert "lists no editions" in result.error
        assert path.read_bytes() == before

    def test_force_failure_leaves_file_unchanged(self, tmp_path):
        path = self._seed(tmp_path, {2001: [("A", "x")], 2002: [("B", "y")]})
        before = path.read_bytes()
        src = FakeIngestor({2001: [("A", "x")], 2002: [("B", "y")]}, fail_on=2002)
        result = acquire_chart(
            src, ["year"], _read(tmp_path), path, HITLISTS, log, force=True
        )
        assert result.error == "source broke on 2002"
        assert path.read_bytes() == before


class TestRefDeduplication:
    def test_dedup_does_not_compare_refs_pairwise(self, tmp_path):
        # Quadratic `ref not in list` would make ~n²/2 equality comparisons.
        comparisons = []

        class CountingRef(EditionRef):
            def __eq__(self, other):
                comparisons.append(1)
                return super().__eq__(other)

            __hash__ = EditionRef.__hash__

        class Many(FakeIngestor):
            def editions(self):
                return [CountingRef({"year": y}) for y in self.data]

        years = {y: [("A", f"s{y}")] for y in range(1000, 1400)}
        acquire_chart(
            Many(years), ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert len(comparisons) < 2000  # n = 400; pairwise would be ~80,000
