"""Tests for the chart-edition dataset reader (beetsplug.hitlisttag.dataset)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from beetsplug.hitlisttag.dataset import (
    DatasetError,
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


def _write(tmp_path: Path, obj, name: str = "top40.json") -> list[HitlistData]:
    (tmp_path / name).write_text(json.dumps(obj), encoding="utf-8")
    return read_dataset(tmp_path, HITLISTS, log)


def _valid_top40() -> dict:
    return {
        "chart": "top40",
        "songs": {"1": {"artist": "A", "title": "T"}},
        "editions": [
            {
                "axes": {"year": 2023, "week": 1},
                "size": 40,
                "entries": [{"position": 1, "songs": ["1"]}],
            }
        ],
    }


def test_malformed_json_raises_naming_file(tmp_path):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(DatasetError, match="bad.json"):
        read_dataset(tmp_path, HITLISTS, log)


def test_axis_names_must_match_configured_axes(tmp_path):
    obj = _valid_top40()
    obj["editions"][0]["axes"] = {"year": 2023}  # missing 'week'
    with pytest.raises(DatasetError, match="do not match"):
        _write(tmp_path, obj)


@pytest.mark.parametrize("bad", [0, -1, True, "2023"])
def test_axis_value_must_be_positive_integer(tmp_path, bad):
    obj = _valid_top40()
    obj["editions"][0]["axes"]["week"] = bad
    with pytest.raises(DatasetError, match="axis"):
        _write(tmp_path, obj)


@pytest.mark.parametrize("bad", [0, -5, True, "40"])
def test_size_must_be_positive_integer(tmp_path, bad):
    obj = _valid_top40()
    obj["editions"][0]["size"] = bad
    with pytest.raises(DatasetError, match="size"):
        _write(tmp_path, obj)


@pytest.mark.parametrize("bad", [0, 41, -1])
def test_position_must_be_within_1_to_size(tmp_path, bad):
    obj = _valid_top40()
    obj["editions"][0]["entries"][0]["position"] = bad
    with pytest.raises(DatasetError, match="position"):
        _write(tmp_path, obj)


def test_duplicate_position_within_edition_rejected(tmp_path):
    obj = _valid_top40()
    obj["editions"][0]["entries"] = [
        {"position": 1, "songs": ["1"]},
        {"position": 1, "songs": ["1"]},
    ]
    with pytest.raises(DatasetError, match="duplicate position"):
        _write(tmp_path, obj)


def test_empty_songs_list_rejected(tmp_path):
    obj = _valid_top40()
    obj["editions"][0]["entries"][0]["songs"] = []
    with pytest.raises(DatasetError, match="non-empty 'songs'"):
        _write(tmp_path, obj)


def test_dangling_song_reference_rejected(tmp_path):
    obj = _valid_top40()
    obj["editions"][0]["entries"][0]["songs"] = ["99"]
    with pytest.raises(DatasetError, match="unknown song"):
        _write(tmp_path, obj)


def test_duplicate_axes_across_editions_rejected(tmp_path):
    obj = _valid_top40()
    obj["editions"].append(dict(obj["editions"][0]))
    with pytest.raises(DatasetError, match="duplicate edition"):
        _write(tmp_path, obj)


def test_unconfigured_chart_is_skipped_with_warning(tmp_path, caplog):
    (tmp_path / "top40.json").write_text(json.dumps(_valid_top40()), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = read_dataset(tmp_path, {"top2000": ["year"]}, log)
    assert result == []
    assert "not a configured hitlist" in caplog.text


def test_two_files_same_chart_rejected(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(_valid_top40()), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(_valid_top40()), encoding="utf-8")
    with pytest.raises(DatasetError, match="declared in two files"):
        read_dataset(tmp_path, HITLISTS, log)


def test_non_json_and_uppercase_json_ignored(tmp_path):
    (tmp_path / "top40.json").write_text(json.dumps(_valid_top40()), encoding="utf-8")
    (tmp_path / "NOTES.JSON").write_text("not read", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("not read", encoding="utf-8")
    result = read_dataset(tmp_path, HITLISTS, log)
    assert [d.chart for d in result] == ["top40"]


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permissions",
)
def test_unreadable_directory_warns_and_continues(tmp_path, caplog):
    (tmp_path / "top40.json").write_text(json.dumps(_valid_top40()), encoding="utf-8")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "hidden.json").write_text("{}", encoding="utf-8")
    locked.chmod(0o000)
    try:
        with caplog.at_level(logging.WARNING):
            result = read_dataset(tmp_path, HITLISTS, log)
    finally:
        locked.chmod(0o755)  # restore so tmp cleanup can remove it
    assert [d.chart for d in result] == ["top40"]
    assert "cannot read dataset directory" in caplog.text


def test_symlinked_subdirectory_is_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "top2000.json").write_text(
        json.dumps({"chart": "top2000", "songs": {}, "editions": []}),
        encoding="utf-8",
    )
    root = tmp_path / "root"
    root.mkdir()
    (root / "top40.json").write_text(json.dumps(_valid_top40()), encoding="utf-8")
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported here")
    result = read_dataset(root, HITLISTS, log)
    # top2000 sits behind the symlink and must not be followed.
    assert [d.chart for d in result] == ["top40"]
