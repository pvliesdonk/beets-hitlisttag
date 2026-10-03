"""Tests for the dataset serializer and atomic writer."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest

from beetsplug.hitlisttag import dataset as dataset_module
from beetsplug.hitlisttag.dataset import (
    DatasetError,
    Edition,
    Entry,
    HitlistData,
    Song,
    _id_sort_key,
    dump_dataset,
    read_dataset,
    write_dataset_file,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dataset"
HITLISTS = {"top2000": ["year"], "top40": ["year", "week"]}
log = logging.getLogger("test.dataset_write")


def _by_chart(datasets):
    return {d.chart: d for d in datasets}


def _small(chart: str = "top2000", source: Path = Path("x.json")) -> HitlistData:
    a = Song("2", "Beyoncé", "Halo")
    b = Song("10", "Queen", "Bohemian Rhapsody")
    return HitlistData(
        chart=chart,
        songs={"10": b, "2": a},
        editions=[Edition({"year": 2020}, 3, [Entry(2, [a]), Entry(1, [b])])],
        source=source,
    )


class TestIdSortKey:
    def test_numeric_ids_sort_numerically_before_others(self):
        ids = ["b", "10", "2", "a", "1"]
        assert sorted(ids, key=_id_sort_key) == ["1", "2", "10", "a", "b"]


class TestDumpDataset:
    @pytest.mark.parametrize("chart", ["top2000", "top40"])
    def test_round_trips_fixtures(self, tmp_path, chart):
        original = _by_chart(read_dataset(FIXTURES, HITLISTS, log))[chart]
        (tmp_path / f"{chart}.json").write_text(dump_dataset(original), "utf-8")
        again = _by_chart(read_dataset(tmp_path, HITLISTS, log))[chart]
        assert again.songs == original.songs
        assert again.editions == original.editions

    def test_shape_and_order(self):
        doc = json.loads(dump_dataset(_small()))
        assert doc["chart"] == "top2000"
        assert list(doc["songs"]) == ["2", "10"]  # numeric-aware order
        assert doc["songs"]["2"] == {"artist": "Beyoncé", "title": "Halo"}
        assert doc["editions"] == [
            {
                "axes": {"year": 2020},
                "size": 3,
                "entries": [
                    {"position": 1, "songs": ["10"]},
                    {"position": 2, "songs": ["2"]},
                ],
            }
        ]

    def test_non_ascii_readable_and_trailing_newline(self):
        text = dump_dataset(_small())
        assert "Beyoncé" in text
        assert text.endswith("\n")

    def test_deterministic(self):
        assert dump_dataset(_small()) == dump_dataset(_small())


class TestWriteDatasetFile:
    def test_writes_new_file_readable_by_reader(self, tmp_path):
        target = tmp_path / "sub" / "top2000.json"  # parent created
        write_dataset_file(_small(source=target), target, HITLISTS, log)
        back = _by_chart(read_dataset(tmp_path, HITLISTS, log))["top2000"]
        assert back.source == target
        assert [s.id for s in back.editions[0].entries[0].songs] == ["10"]

    def test_new_file_mode_is_0644(self, tmp_path):
        # Review focus 2.
        target = tmp_path / "top2000.json"
        write_dataset_file(_small(source=target), target, HITLISTS, log)
        assert stat.S_IMODE(target.stat().st_mode) == 0o644

    def test_existing_file_mode_preserved(self, tmp_path):
        # Review focus 2.
        target = tmp_path / "top2000.json"
        target.write_text(dump_dataset(_small(source=target)), "utf-8")
        target.chmod(0o640)
        write_dataset_file(_small(source=target), target, HITLISTS, log)
        assert stat.S_IMODE(target.stat().st_mode) == 0o640

    def test_reread_rejection_leaves_original_and_no_temp(self, tmp_path, monkeypatch):
        target = tmp_path / "top2000.json"
        target.write_text(
            '{"chart": "top2000", "songs": {}, "editions": []}\n', "utf-8"
        )
        before = target.read_bytes()
        monkeypatch.setattr(dataset_module, "dump_dataset", lambda data: "{not json")
        with pytest.raises(DatasetError, match="internal error writing top2000"):
            write_dataset_file(_small(source=target), target, HITLISTS, log)
        assert target.read_bytes() == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ["top2000.json"]

    def test_replace_failure_leaves_original_and_no_temp(self, tmp_path, monkeypatch):
        target = tmp_path / "top2000.json"
        target.write_text(
            '{"chart": "top2000", "songs": {}, "editions": []}\n', "utf-8"
        )
        before = target.read_bytes()

        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(dataset_module.os, "replace", boom)
        with pytest.raises(OSError, match="disk full"):
            write_dataset_file(_small(source=target), target, HITLISTS, log)
        assert target.read_bytes() == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ["top2000.json"]

    def test_temp_file_is_never_named_json(self, tmp_path, monkeypatch):
        # Review focus 1: a stale temp must not be read as a second chart file.
        seen = []
        real_replace = os.replace

        def spy(src, dst):
            seen.append(Path(src).name)
            real_replace(src, dst)

        monkeypatch.setattr(dataset_module.os, "replace", spy)
        target = tmp_path / "top2000.json"
        write_dataset_file(_small(source=target), target, HITLISTS, log)
        assert len(seen) == 1
        assert seen[0].endswith(".json.tmp")
        assert seen[0].startswith(".")
