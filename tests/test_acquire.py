"""Tests for chart acquisition: merge, per-chart orchestration."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from beetsplug.hitlisttag import acquire as acquire_module
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

    def test_source_disambiguated_artists_keep_distinct_ids(self):
        # Pins that acquisition's key is the strict one: the matching key of
        # #170 folds "((GBR))", "&"/"and" and a leading "The", but the source
        # publishes these as different artists and acquisition keeps them so.
        existing = _existing(
            {
                "1": ("The Scorpions ((GBR))", "Wind Of Change"),
                "2": ("Simon & Garfunkel", "Cecilia"),
                "3": ("The Bangles", "Eternal Flame"),
            }
        )
        data, new = merge_acquired(
            existing,
            "fake",
            ["year"],
            [
                _acq(
                    2001,
                    ("Scorpions", "Wind Of Change"),
                    ("Simon and Garfunkel", "Cecilia"),
                    ("Bangles", "Eternal Flame"),
                )
            ],
            SRC,
            log,
        )
        assert new == 3
        assert _ids(data, 2001) == ["4", "5", "6"]

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

    def test_ingest_error_keeps_editions_before_it(self, tmp_path):
        path = tmp_path / "fake.json"
        acquire_chart(
            FakeIngestor({2001: [("A", "x")]}), ["year"], None, path, HITLISTS, log
        )
        failing = FakeIngestor(
            {2001: [("A", "x")], 2002: [("B", "y")], 2003: [("C", "z")]}, fail_on=2003
        )
        result = acquire_chart(failing, ["year"], _read(tmp_path), path, HITLISTS, log)
        assert result.error is None
        assert [ref.axes["year"] for ref, _ in result.failed] == [2003]
        assert [e.axes["year"] for e in _read(tmp_path).editions] == [2001, 2002]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["fake.json"]

    def test_contract_violation_reported(self, tmp_path):
        class Broken(FakeIngestor):
            def fetch(self, ref):
                return AcquiredEdition(ref, 1, (RawEntry(2, (RawSong("A", "x"),)),))

        result = acquire_chart(
            Broken({2001: []}), ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert result.error is None
        [(ref, reason)] = result.failed
        assert reason.startswith("ingestor for fake broke its contract:")
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

    def test_failed_editions_line(self):
        r = ChartResult(
            "x",
            failed=[
                (EditionRef({"year": 2001}), "boom"),
                (EditionRef({"year": 2005}), "bang"),
            ],
        )
        assert r.lines(["year"]) == [
            "x: 2 editions failed (2001, 2005): bang; a later run retries them"
        ]

    def test_acquired_failed_and_stopped_lines(self):
        r = ChartResult(
            "x",
            acquired=[EditionRef({"year": 2000})],
            entries=40,
            new_songs=40,
            written=True,
            failed=[(EditionRef({"year": y}), "down") for y in (2001, 2002, 2003)],
            stopped=True,
            not_attempted=1200,
        )
        assert r.lines(["year"]) == [
            "x: acquired 1 edition (2000), 40 entries, 40 new songs",
            "x: 3 editions failed (2001–2003): down; a later run retries them",
            "x: stopped after 3 failed editions in a row; 1,200 editions not attempted",
        ]

    def test_failed_line_names_two_axis_editions(self):
        r = ChartResult(
            "top40",
            failed=[(EditionRef({"year": 1965, "week": 3}), "boom")],
        )
        assert r.lines(["year", "week"]) == [
            "top40: 1 edition failed (1965 week 3): boom; a later run retries them"
        ]

    def test_only_failures_is_not_up_to_date(self):
        r = ChartResult("x", held=27, failed=[(EditionRef({"year": 2026}), "boom")])
        assert r.lines(["year"]) == [
            "x: 1 edition failed (2026): boom; a later run retries them"
        ]

    def test_error_after_a_write_says_what_the_file_keeps(self):
        r = ChartResult(
            "x",
            acquired=[EditionRef({"year": 2001})],
            entries=1,
            new_songs=1,
            written=True,
            error="cannot write f: disk full",
        )
        assert r.lines(["year"]) == [
            "x: acquired 1 edition (2001), 1 entry, 1 new song",
            "x: FAILED — cannot write f: disk full; file keeps the editions "
            "written before it",
        ]

    def test_final_write_failure_says_file_unchanged(self, tmp_path, monkeypatch):
        def broken(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(acquire_module, "write_dataset_file", broken)
        path = tmp_path / "fake.json"
        ing = Scripted({2001: ("A", "x"), 2002: IngestError("boom")})
        result = acquire_chart(ing, ["year"], None, path, HITLISTS, log)
        assert result.lines(["year"]) == [
            "fake: 1 edition failed (2002): boom; a later run retries them",
            f"fake: FAILED — cannot write {path}: disk full; file unchanged; "
            "1 acquired edition not saved",
        ]

    def test_prune_with_all_fetches_failing_reports_both(self, tmp_path):
        path = tmp_path / "fake.json"
        acquire_chart(
            Scripted({2000: ("Z", "q"), 2001: ("A", "x")}),
            ["year"],
            None,
            path,
            HITLISTS,
            log,
        )
        result = acquire_chart(
            Scripted({2001: IngestError("down")}),
            ["year"],
            _read(tmp_path),
            path,
            HITLISTS,
            log,
            force=True,
            prune=True,
        )
        assert result.lines(["year"]) == [
            "fake: 1 edition failed (2001): down; a later run retries them",
            "fake: dropped 1 edition not listed by the source (2000)",
        ]


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
        # Important 2: an ingestor's network error is the ingestor's failure,
        # not a write failure (since #114, it fails the chart).
        class Offline(FakeIngestor):
            def fetch(self, ref):
                raise ConnectionError("connection refused")

        path = tmp_path / "fake.json"
        result = acquire_chart(Offline({2001: []}), ["year"], None, path, HITLISTS, log)
        assert result.error == (
            "ingestor for fake raised ConnectionError: connection refused"
        )
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


def _acq_ids(year: int, *rows: tuple[str, str, dict], size: int | None = None):
    entries = tuple(
        RawEntry(i + 1, (RawSong(artist, title),), ids)
        for i, (artist, title, ids) in enumerate(rows)
    )
    return AcquiredEdition(EditionRef({"year": year}), size or len(rows), entries)


def _entries(data: HitlistData, year: int):
    return next(e for e in data.editions if e.axes["year"] == year).entries


class TestSourceIds:
    def test_ids_land_on_stored_entries(self):
        acquired = _acq_ids(2001, ("A", "x", {"s/title": "1"}), ("B", "y", {}))
        data, _ = merge_acquired(None, "fake", ["year"], [acquired], SRC, log)
        assert [e.source_ids for e in _entries(data, 2001)] == [{"s/title": "1"}, {}]
        assert type(_entries(data, 2001)[0].source_ids) is dict

    def test_ids_never_change_song_resolution(self):
        data, _ = merge_acquired(
            None,
            "fake",
            ["year"],
            [
                _acq_ids(2001, ("A", "x", {"s/title": "1"})),
                _acq_ids(
                    2002, ("A", "x", {"s/title": "2"}), ("B", "y", {"s/title": "1"})
                ),
            ],
            SRC,
            log,
        )
        assert _ids(data, 2001) == ["1"]  # same name, different id: same song
        assert _ids(data, 2002) == ["1", "2"]  # same id, different name: new song

    def test_existing_editions_keep_their_entries(self):
        song = Song("1", "A", "x")
        existing = _existing(
            {"1": ("A", "x")},
            [Edition({"year": 2000}, 1, [Entry(1, [song], {"s/title": "9"})])],
        )
        acquired = _acq_ids(2001, ("A", "x", {"s/title": "1"}))
        data, _ = merge_acquired(existing, "fake", ["year"], [acquired], SRC, log)
        assert _entries(data, 2000)[0].source_ids == {"s/title": "9"}

    def test_replace_takes_the_new_ids(self):
        song = Song("1", "A", "x")
        existing = _existing(
            {"1": ("A", "x")},
            [Edition({"year": 2001}, 1, [Entry(1, [song], {"s/title": "old"})])],
        )
        acquired = _acq_ids(2001, ("A", "x", {"s/title": "new"}))
        data, _ = merge_acquired(
            existing, "fake", ["year"], [acquired], SRC, log, replace=True
        )
        assert _entries(data, 2001)[0].source_ids == {"s/title": "new"}

    def test_multi_song_entry_keeps_one_set_of_ids(self):
        acquired = AcquiredEdition(
            EditionRef({"year": 2001}),
            1,
            (RawEntry(1, (RawSong("A", "x"), RawSong("B", "y")), {"s/title": "7"}),),
        )
        data, _ = merge_acquired(None, "fake", ["year"], [acquired], SRC, log)
        [entry] = _entries(data, 2001)
        assert [s.id for s in entry.songs] == ["1", "2"]
        assert entry.source_ids == {"s/title": "7"}

    def test_integer_ids_are_a_contract_violation(self, tmp_path):
        class IntIds(FakeIngestor):
            def fetch(self, ref):
                entry = RawEntry(1, (RawSong("A", "x"),), {"top40.nl/title": 8522})
                return AcquiredEdition(ref, 1, (entry,))

        result = acquire_chart(
            IntIds({2001: []}), ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert result.error is None
        [(_ref, reason)] = result.failed
        assert reason.startswith("ingestor for fake broke its contract:")
        assert "source_ids" in reason
        assert not (tmp_path / "fake.json").exists()


class Scripted:
    """An ingestor with a scripted outcome per year.

    An outcome is (artist, title), "contract" (an edition breaking the
    contract), or an exception instance to raise. ``on_fetch(year)`` runs
    at the start of every fetch.
    """

    chart = "fake"
    axes = ("year",)

    def __init__(self, plan, on_fetch=None):
        self.plan = plan
        self.on_fetch = on_fetch
        self.fetched: list[int] = []

    def editions(self):
        return [EditionRef({"year": year}) for year in self.plan]

    def fetch(self, ref):
        year = ref.axes["year"]
        self.fetched.append(year)
        if self.on_fetch is not None:
            self.on_fetch(year)
        outcome = self.plan[year]
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "contract":
            return AcquiredEdition(ref, 1, (RawEntry(2, (RawSong("A", "x"),)),))
        return _acq(year, outcome)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _years(root: Path) -> list[int]:
    data = _read(root)
    return [e.axes["year"] for e in data.editions] if data else []


def _failed_years(result: ChartResult) -> list[int]:
    return [ref.axes["year"] for ref, _reason in result.failed]


class TestPartialProgress:
    def test_failure_in_the_middle_keeps_the_others(self, tmp_path):
        ing = Scripted({2001: ("A", "x"), 2002: IngestError("boom"), 2003: ("B", "y")})
        result = acquire_chart(
            ing, ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert result.error is None
        assert _years(tmp_path) == [2001, 2003]
        assert [(ref.axes["year"], why) for ref, why in result.failed] == [
            (2002, "boom")
        ]
        assert [ref.axes["year"] for ref in result.acquired] == [2001, 2003]
        assert result.written is True

    def test_a_later_plain_run_fetches_only_the_failed_edition(self, tmp_path):
        path = tmp_path / "fake.json"
        first = Scripted(
            {2001: ("A", "x"), 2002: IngestError("boom"), 2003: ("B", "y")}
        )
        acquire_chart(first, ["year"], None, path, HITLISTS, log)
        again = Scripted({2001: ("A", "x"), 2002: ("C", "z"), 2003: ("B", "y")})
        result = acquire_chart(again, ["year"], _read(tmp_path), path, HITLISTS, log)
        assert again.fetched == [2002]
        assert result.failed == []
        assert _years(tmp_path) == [2001, 2002, 2003]

    def test_three_failures_in_a_row_stop_the_run(self, tmp_path):
        plan = {
            2000: IngestError("early"),
            2001: ("A", "x"),
            2002: IngestError("a"),
            2003: IngestError("b"),
            2004: IngestError("c"),
            2005: ("B", "y"),
            2006: ("C", "z"),
        }
        ing = Scripted(plan)
        result = acquire_chart(
            ing, ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert result.stopped is True
        assert result.not_attempted == 2
        assert ing.fetched == [2000, 2001, 2002, 2003, 2004]
        assert _years(tmp_path) == [2001]
        assert _failed_years(result) == [2000, 2002, 2003, 2004]

    def test_a_success_resets_the_count(self, tmp_path):
        plan = {
            2001: IngestError("a"),
            2002: IngestError("b"),
            2003: ("A", "x"),
            2004: IngestError("c"),
            2005: IngestError("d"),
            2006: ("B", "y"),
        }
        result = acquire_chart(
            Scripted(plan), ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert result.stopped is False
        assert _years(tmp_path) == [2003, 2006]

    def test_contract_violation_is_one_failed_edition(self, tmp_path):
        plan = {2001: "contract", 2002: ("A", "x")}
        result = acquire_chart(
            Scripted(plan), ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        [(ref, reason)] = result.failed
        assert ref.axes["year"] == 2001
        assert reason.startswith("ingestor for fake broke its contract:")
        assert _years(tmp_path) == [2002]

    def test_ctrl_c_saves_what_was_acquired(self, tmp_path):
        plan = {2001: ("A", "x"), 2002: KeyboardInterrupt(), 2003: ("B", "y")}
        ing = Scripted(plan)
        result = acquire_chart(
            ing, ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert result.interrupted is True
        assert result.error is None
        assert ing.fetched == [2001, 2002]
        assert _years(tmp_path) == [2001]

    def test_ingestor_bug_saves_and_fails_the_chart(self, tmp_path):
        plan = {2001: ("A", "x"), 2002: RuntimeError("bug"), 2003: ("B", "y")}
        ing = Scripted(plan)
        result = acquire_chart(
            ing, ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert result.error == "ingestor for fake raised RuntimeError: bug"
        assert ing.fetched == [2001, 2002]
        assert _years(tmp_path) == [2001]

    def test_interrupt_before_anything_acquired_writes_nothing(self, tmp_path):
        result = acquire_chart(
            Scripted({2001: KeyboardInterrupt()}),
            ["year"],
            None,
            tmp_path / "fake.json",
            HITLISTS,
            log,
        )
        assert result.interrupted is True
        assert list(tmp_path.iterdir()) == []

    def test_checkpoint_writes_during_the_run(self, tmp_path):
        clock = FakeClock()
        seen: dict[int, list[int]] = {}

        def on_fetch(year):
            seen[year] = _years(tmp_path)
            clock.now += 40

        ing = Scripted(
            {2001: ("A", "x"), 2002: ("B", "y"), 2003: ("C", "z")}, on_fetch=on_fetch
        )
        acquire_chart(
            ing, ["year"], None, tmp_path / "fake.json", HITLISTS, log, clock=clock
        )
        # 2001 at t=40 (no write), 2002 at t=80 (checkpoint), 2003 sees it.
        assert seen == {2001: [], 2002: [], 2003: [2001, 2002]}
        assert _years(tmp_path) == [2001, 2002, 2003]

    def test_failed_checkpoint_write_keeps_the_last_good_state(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "fake.json"
        clock = FakeClock()
        real_write = acquire_module.write_dataset_file
        calls: list[int] = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("disk full")
            return real_write(*args, **kwargs)

        def tick(year):
            clock.now += 60

        monkeypatch.setattr(acquire_module, "write_dataset_file", flaky)
        ing = Scripted(
            {2001: ("A", "x"), 2002: ("B", "y"), 2003: ("C", "z"), 2004: ("D", "w")},
            on_fetch=tick,
        )
        result = acquire_chart(ing, ["year"], None, path, HITLISTS, log, clock=clock)
        assert result.error == f"cannot write {path}: disk full"
        assert ing.fetched == [2001, 2002]
        assert _years(tmp_path) == [2001]
        assert result.written is True
        assert [ref.axes["year"] for ref in result.acquired] == [2001]

    def test_force_failure_keeps_the_old_version(self, tmp_path):
        path = tmp_path / "fake.json"
        acquire_chart(
            Scripted({2001: ("A", "x"), 2002: ("B", "y")}),
            ["year"],
            None,
            path,
            HITLISTS,
            log,
        )
        ing = Scripted({2001: ("A2", "x2"), 2002: IngestError("boom")})
        result = acquire_chart(
            ing, ["year"], _read(tmp_path), path, HITLISTS, log, force=True
        )
        data = _read(tmp_path)
        titles = {e.axes["year"]: e.entries[0].songs[0].title for e in data.editions}
        assert titles == {2001: "x2", 2002: "y"}
        assert _failed_years(result) == [2002]

    def test_prune_when_every_fetch_fails(self, tmp_path):
        path = tmp_path / "fake.json"
        acquire_chart(
            Scripted({2000: ("Z", "q"), 2001: ("A", "x"), 2002: ("B", "y")}),
            ["year"],
            None,
            path,
            HITLISTS,
            log,
        )
        ing = Scripted({2001: IngestError("a"), 2002: IngestError("b")})
        result = acquire_chart(
            ing, ["year"], _read(tmp_path), path, HITLISTS, log, force=True, prune=True
        )
        assert _years(tmp_path) == [2001, 2002]
        assert result.pruned == [(2000,)]
        assert _failed_years(result) == [2001, 2002]

    def test_song_ids_across_checkpoints_match_a_single_write(self, tmp_path):
        plan = {2001: ("A", "x"), 2002: ("B", "y"), 2003: ("A", "x"), 2004: ("C", "z")}
        one, many = tmp_path / "one", tmp_path / "many"
        one.mkdir()
        many.mkdir()
        acquire_chart(Scripted(plan), ["year"], None, one / "fake.json", HITLISTS, log)
        clock = FakeClock()

        def tick(year):
            clock.now += 60

        acquire_chart(
            Scripted(plan, on_fetch=tick),
            ["year"],
            None,
            many / "fake.json",
            HITLISTS,
            log,
            clock=clock,
        )
        assert (one / "fake.json").read_text("utf-8") == (many / "fake.json").read_text(
            "utf-8"
        )


class TestReviewFixes123:
    """Findings from #123's whole-branch review."""

    def test_failed_ref_with_wrong_axis_names_does_not_crash_the_report(self, tmp_path):
        class WrongAxis(Scripted):
            def editions(self):
                return [EditionRef({"jaar": 2001}), EditionRef({"year": 2002})]

        ing = WrongAxis({2002: ("A", "x")})
        result = acquire_chart(
            ing, ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        [(_ref, reason)] = result.failed
        assert "declares axes" in reason
        lines = result.lines(["year"])
        assert lines[0] == "fake: acquired 1 edition (2002), 1 entry, 1 new song"
        assert lines[1].startswith("fake: 1 edition failed: ")

    def test_failed_ref_missing_first_of_two_axes_does_not_crash(self, tmp_path):
        class TwoAxis(Scripted):
            axes = ("year", "week")

            def editions(self):
                return [EditionRef({"week": 3})]

        ing = TwoAxis({})
        result = acquire_chart(
            ing, ["year", "week"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert len(result.failed) == 1
        assert result.lines(["year", "week"])[0].startswith("fake: 1 edition failed: ")

    def test_drop_that_reached_the_file_is_reported_even_after_a_failed_write(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "fake.json"
        acquire_chart(
            Scripted({1990: ("Z", "q"), 2000: ("A", "x"), 2001: ("B", "y")}),
            ["year"],
            None,
            path,
            HITLISTS,
            log,
        )
        clock = FakeClock()
        real_write = acquire_module.write_dataset_file
        calls: list[int] = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("disk full")
            return real_write(*args, **kwargs)

        def tick(year):
            clock.now += 60

        monkeypatch.setattr(acquire_module, "write_dataset_file", flaky)
        ing = Scripted({2000: ("A", "x"), 2001: ("B", "y")}, on_fetch=tick)
        result = acquire_chart(
            ing,
            ["year"],
            _read(tmp_path),
            path,
            HITLISTS,
            log,
            force=True,
            prune=True,
            clock=clock,
        )
        assert _years(tmp_path) == [2000, 2001]  # 1990 dropped at the checkpoint
        assert "fake: dropped 1 edition not listed by the source (1990)" in (
            result.lines(["year"])
        )

    def test_checkpoint_also_fires_after_a_failed_edition(self, tmp_path):
        clock = FakeClock()
        seen: dict[int, list[int]] = {}
        steps = {2001: 10, 2002: 60, 2003: 0}

        def on_fetch(year):
            seen[year] = _years(tmp_path)
            clock.now += steps[year]

        ing = Scripted(
            {2001: ("A", "x"), 2002: IngestError("slow failure"), 2003: ("B", "y")},
            on_fetch=on_fetch,
        )
        acquire_chart(
            ing, ["year"], None, tmp_path / "fake.json", HITLISTS, log, clock=clock
        )
        # t=70 after the failed 2002: the checkpoint writes 2001 before 2003.
        assert seen[2003] == [2001]


class _Listing:
    """An ingestor whose editions() returns or raises what it is given."""

    chart = "fake"
    axes = ("year",)

    def __init__(self, listing):
        self.listing = listing
        self.fetched: list[int] = []

    def editions(self):
        if isinstance(self.listing, BaseException):
            raise self.listing
        return self.listing

    def fetch(self, ref):
        self.fetched.append(ref.axes["year"])
        return _acq(ref.axes["year"], ("A", "x"))


def _disk_full(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(acquire_module, "write_dataset_file", broken)


class TestUnexpectedFailures:
    """#114 items 2 and 4, #132: what a broken ingestor or Ctrl-C leaves."""

    def _run(self, ing, tmp_path, **kwargs):
        return acquire_chart(
            ing, ["year"], None, tmp_path / "fake.json", HITLISTS, log, **kwargs
        )

    def test_listing_a_non_ref_is_a_contract_failure(self, tmp_path):
        ing = _Listing([EditionRef({"year": 2001}), {"year": 2002}])
        result = self._run(ing, tmp_path)
        assert result.error == (
            "ingestor for fake broke its contract: editions() yielded dict, "
            "not EditionRef"
        )
        assert ing.fetched == []
        assert list(tmp_path.iterdir()) == []

    def test_bug_while_listing_fails_the_chart(self, tmp_path, caplog):
        ing = _Listing(RuntimeError("listing broke"))
        with caplog.at_level(logging.DEBUG):
            result = self._run(ing, tmp_path)
        assert result.error == "ingestor for fake raised RuntimeError: listing broke"
        assert "Traceback" in caplog.text
        assert list(tmp_path.iterdir()) == []

    def test_bug_while_fetching_logs_the_traceback_at_debug(self, tmp_path, caplog):
        plan = {2001: ("A", "x"), 2002: KeyError("rank")}
        with caplog.at_level(logging.DEBUG):
            result = self._run(Scripted(plan), tmp_path)
        assert result.error == "ingestor for fake raised KeyError: 'rank'"
        tracebacks = [r for r in caplog.records if "Traceback" in r.getMessage()]
        assert tracebacks
        assert all(r.levelno == logging.DEBUG for r in tracebacks)

    def test_bug_without_a_message_names_the_exception(self, tmp_path):
        result = self._run(Scripted({2001: RuntimeError()}), tmp_path)
        assert result.error == "ingestor for fake raised RuntimeError"

    def test_bug_report_says_what_the_file_keeps(self, tmp_path):
        plan = {2001: ("A", "x"), 2002: RuntimeError("bug")}
        result = self._run(Scripted(plan), tmp_path)
        assert result.lines(["year"]) == [
            "fake: acquired 1 edition (2001), 1 entry, 1 new song",
            "fake: FAILED — ingestor for fake raised RuntimeError: bug; "
            "file keeps the editions written before it",
        ]

    def test_bug_and_a_failed_save_report_both(self, tmp_path, monkeypatch):
        _disk_full(monkeypatch)
        path = tmp_path / "fake.json"
        plan = {2001: ("A", "x"), 2002: RuntimeError("bug")}
        result = self._run(Scripted(plan), tmp_path)
        assert result.error == (
            "ingestor for fake raised RuntimeError: bug; could not save: "
            f"cannot write {path}: disk full"
        )
        assert result.lines(["year"])[-1].endswith(
            "; file unchanged; 1 acquired edition not saved"
        )

    def test_ctrl_c_while_listing_is_an_interrupt(self, tmp_path):
        result = self._run(_Listing(KeyboardInterrupt()), tmp_path)
        assert result.interrupted is True
        assert result.lines(["year"]) == ["fake: interrupted; file unchanged"]

    def test_ctrl_c_report_says_what_the_file_keeps(self, tmp_path):
        plan = {2001: ("A", "x"), 2002: KeyboardInterrupt()}
        result = self._run(Scripted(plan), tmp_path)
        assert result.lines(["year"]) == [
            "fake: acquired 1 edition (2001), 1 entry, 1 new song",
            "fake: interrupted; file keeps the editions written before it",
        ]

    def test_ctrl_c_before_anything_acquired_reports_file_unchanged(self, tmp_path):
        result = self._run(Scripted({2001: KeyboardInterrupt()}), tmp_path)
        assert result.lines(["year"]) == ["fake: interrupted; file unchanged"]

    def test_ctrl_c_with_a_failed_save_says_so(self, tmp_path, monkeypatch):
        _disk_full(monkeypatch)
        path = tmp_path / "fake.json"
        plan = {2001: ("A", "x"), 2002: KeyboardInterrupt()}
        result = self._run(Scripted(plan), tmp_path)
        assert result.lines(["year"]) == [
            f"fake: interrupted; could not save: cannot write {path}: disk full; "
            "file unchanged; 1 acquired edition not saved"
        ]

    def test_other_base_exceptions_still_save_and_propagate(self, tmp_path):
        plan = {2001: ("A", "x"), 2002: SystemExit(3)}
        with pytest.raises(SystemExit):
            self._run(Scripted(plan), tmp_path)
        assert _years(tmp_path) == [2001]


def _writes(monkeypatch, *outcomes):
    """Replace the dataset writer: each call takes the next outcome.

    ``None`` writes for real; an exception instance is raised instead.
    """
    real_write = acquire_module.write_dataset_file
    queue = list(outcomes)

    def write(*args, **kwargs):
        outcome = queue.pop(0) if queue else None
        if outcome is not None:
            raise outcome
        return real_write(*args, **kwargs)

    monkeypatch.setattr(acquire_module, "write_dataset_file", write)


class TestReviewFixes114:
    """Findings from the whole-branch review of #114/#132."""

    def _after_checkpoint(self, tmp_path, last, **kwargs):
        """2001 is saved by a checkpoint, 2002 is pending, 2003 is ``last``."""
        clock = FakeClock()

        def on_fetch(year):
            if year == 2001:
                clock.now += 60

        ing = Scripted({2001: ("A", "x"), 2002: ("B", "y"), 2003: last}, on_fetch)
        return acquire_chart(
            ing,
            ["year"],
            None,
            tmp_path / "fake.json",
            HITLISTS,
            log,
            clock=clock,
            **kwargs,
        )

    def test_bug_after_a_checkpoint_with_a_failed_save(self, tmp_path, monkeypatch):
        _writes(monkeypatch, None, OSError("disk full"))
        path = tmp_path / "fake.json"
        result = self._after_checkpoint(tmp_path, RuntimeError("bug"))
        assert result.lines(["year"]) == [
            "fake: acquired 1 edition (2001), 1 entry, 1 new song",
            "fake: FAILED — ingestor for fake raised RuntimeError: bug; could not "
            f"save: cannot write {path}: disk full; file keeps the editions "
            "written before it; 1 acquired edition not saved",
        ]
        assert _years(tmp_path) == [2001]

    def test_ctrl_c_after_a_checkpoint_with_a_failed_save(self, tmp_path, monkeypatch):
        _writes(monkeypatch, None, OSError("disk full"))
        path = tmp_path / "fake.json"
        result = self._after_checkpoint(tmp_path, KeyboardInterrupt())
        assert result.lines(["year"]) == [
            "fake: acquired 1 edition (2001), 1 entry, 1 new song",
            f"fake: interrupted; could not save: cannot write {path}: disk full; "
            "file keeps the editions written before it; 1 acquired edition "
            "not saved",
        ]

    def test_ctrl_c_during_a_checkpoint_write_is_reported(self, tmp_path, monkeypatch):
        _writes(monkeypatch, KeyboardInterrupt())
        clock = FakeClock()

        def tick(year):
            clock.now += 60

        ing = Scripted({2001: ("A", "x"), 2002: ("B", "y")}, on_fetch=tick)
        result = acquire_chart(
            ing, ["year"], None, tmp_path / "fake.json", HITLISTS, log, clock=clock
        )
        assert result.interrupted is True
        assert ing.fetched == [2001]
        assert result.lines(["year"]) == [
            "fake: interrupted; file unchanged; 1 acquired edition not saved"
        ]
        assert list(tmp_path.iterdir()) == []

    def test_second_ctrl_c_during_the_save_is_reported(self, tmp_path, monkeypatch):
        _writes(monkeypatch, KeyboardInterrupt())
        plan = {2001: ("A", "x"), 2002: KeyboardInterrupt()}
        result = acquire_chart(
            Scripted(plan), ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert result.lines(["year"]) == [
            "fake: interrupted; file unchanged; 1 acquired edition not saved"
        ]

    def test_unexpected_save_error_is_named_once(self, tmp_path, monkeypatch):
        _writes(monkeypatch, RuntimeError("encoder broke"))
        plan = {2001: ("A", "x"), 2002: KeyError("rank")}
        result = acquire_chart(
            Scripted(plan), ["year"], None, tmp_path / "fake.json", HITLISTS, log
        )
        assert result.error == (
            "ingestor for fake raised KeyError: 'rank'; could not save: "
            "RuntimeError: encoder broke"
        )

    def test_failed_checkpoint_reports_the_unsaved_edition(self, tmp_path, monkeypatch):
        _writes(monkeypatch, None, OSError("disk full"))
        clock = FakeClock()

        def tick(year):
            clock.now += 60

        path = tmp_path / "fake.json"
        ing = Scripted({2001: ("A", "x"), 2002: ("B", "y"), 2003: ("C", "z")}, tick)
        result = acquire_chart(ing, ["year"], None, path, HITLISTS, log, clock=clock)
        assert result.lines(["year"])[-1] == (
            f"fake: FAILED — cannot write {path}: disk full; file keeps the "
            "editions written before it; 1 acquired edition not saved"
        )

    def test_dataset_file_errors_are_not_blamed_on_the_ingestor(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "fake.json"
        acquire_chart(Scripted({2001: ("A", "x")}), ["year"], None, path, HITLISTS, log)

        def vanished(source):
            raise FileNotFoundError(f"no such file: {source}")

        monkeypatch.setattr(acquire_module, "unknown_fields", vanished)
        with pytest.raises(FileNotFoundError):
            acquire_chart(
                Scripted({2001: ("A", "x"), 2002: ("B", "y")}),
                ["year"],
                _read(tmp_path),
                path,
                HITLISTS,
                log,
            )

    def test_interrupt_report_keeps_the_prune_that_reached_the_file(self, tmp_path):
        path = tmp_path / "fake.json"
        acquire_chart(
            Scripted({1990: ("Z", "q"), 2001: ("A", "x")}),
            ["year"],
            None,
            path,
            HITLISTS,
            log,
        )
        clock = FakeClock()

        def tick(year):
            clock.now += 60

        ing = Scripted({2001: ("A", "x"), 2002: KeyboardInterrupt()}, on_fetch=tick)
        result = acquire_chart(
            ing,
            ["year"],
            _read(tmp_path),
            path,
            HITLISTS,
            log,
            force=True,
            prune=True,
            clock=clock,
        )
        assert result.lines(["year"]) == [
            "fake: re-acquired 1 edition (2001), 1 entry, 0 new songs",
            "fake: dropped 1 edition not listed by the source (1990)",
            "fake: interrupted; file keeps the editions written before it",
        ]
        assert _years(tmp_path) == [2001]


class TestFailedEditionsNamed:
    """#151: which editions failed, and why, for charts with two axes too."""

    def test_weeks_grouped_by_year_with_ranges(self):
        weeks = [(1982, 1), (1983, 1), (2005, 1), (2005, 2), (2005, 3), (2005, 7)]
        r = ChartResult(
            "top40",
            failed=[
                (EditionRef({"year": y, "week": w}), f"{y}/{w} broke") for y, w in weeks
            ],
        )
        assert r.lines(["year", "week"]) == [
            "top40: 6 editions failed (1982 week 1, 1983 week 1, 2005 weeks 1–3, 7): "
            "2005/7 broke; a later run retries them"
        ]

    def test_refs_lacking_an_axis_are_left_out_of_the_list(self):
        r = ChartResult(
            "top40",
            failed=[
                (EditionRef({"year": 1990, "week": 4}), "boom"),
                (EditionRef({"week": 3}), "no year"),
            ],
        )
        assert r.lines(["year", "week"])[0] == (
            "top40: 2 editions failed (1990 week 4): no year; a later run retries them"
        )

    def test_each_failure_is_logged_with_its_own_reason(self, tmp_path, caplog):
        plan = {
            2001: IngestError("source {down}"),
            2002: ("A", "x"),
            2003: IngestError("page not found"),
        }
        with caplog.at_level(logging.WARNING):
            acquire_chart(
                Scripted(plan), ["year"], None, tmp_path / "fake.json", HITLISTS, log
            )
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert warnings == [
            "fake: 2001 failed: source {down}",
            "fake: 2003 failed: page not found",
        ]

    def test_two_axis_failure_log_names_the_week(self, tmp_path, caplog):
        class Weekly(Scripted):
            axes = ("year", "week")

            def editions(self):
                return [EditionRef({"year": 1982, "week": 1})]

            def fetch(self, ref):
                raise IngestError("Top 40 1982 week 1: page not found")

        with caplog.at_level(logging.WARNING):
            acquire_chart(
                Weekly({}), ["year", "week"], None, tmp_path / "f.json", HITLISTS, log
            )
        assert "fake: 1982 week 1 failed: Top 40 1982 week 1: page not found" in (
            caplog.text
        )

    def test_bug_and_ctrl_c_are_not_logged_as_failed_editions(self, tmp_path, caplog):
        for outcome in (RuntimeError("bug"), KeyboardInterrupt()):
            caplog.clear()
            with caplog.at_level(logging.WARNING):
                acquire_chart(
                    Scripted({2001: outcome}),
                    ["year"],
                    None,
                    tmp_path / "fake.json",
                    HITLISTS,
                    log,
                )
            assert " failed: " not in caplog.text

    def test_three_axes_listed_one_by_one(self):
        r = ChartResult(
            "x",
            failed=[(EditionRef({"year": 2001, "week": 2, "day": 5}), "boom")],
        )
        assert r.lines(["year", "week", "day"])[0] == (
            "x: 1 edition failed (2001 week 2 day 5): boom; a later run retries them"
        )
