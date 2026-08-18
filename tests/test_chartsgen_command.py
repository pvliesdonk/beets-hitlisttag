"""Command-level tests for chartsgen.

Existing CHARTS tags are seeded through the *file* (via MediaFile), never
through the beets database: a prototype of this command read existing charts
from the database and destroyed file-only charts whenever the database lagged
the file (recorded on issue #78). Database-seeded tests cannot observe that
class of bug.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from beets import config, ui
from beets.plugins import find_plugins, load_plugins
from beets.util import syspath
from mediafile import MediaFile

from beetsplug.hitlisttag import HitlistTag
from beetsplug.hitlisttag.charts import Chart, ChartList

RSRC = Path(__file__).parent / "rsrc"

_counter = itertools.count()


@pytest.fixture
def env(tmp_path):
    """A TestHelper with the plugin loaded and dataset_dir pointing at tmp_path.

    Imported inside the fixture for the same sys.path reason as
    tests/test_commands.py. Yields (helper, plugin, dataset_dir).
    """
    from beets.test.helper import TestHelper

    helper = TestHelper()
    with helper:
        config["plugins"] = ["hitlisttag"]
        config["hitlisttag"]["dataset_dir"] = str(tmp_path)
        load_plugins()
        plugin = next(p for p in find_plugins() if isinstance(p, HitlistTag))
        yield helper, plugin, tmp_path


def _opts(**kwargs):
    return SimpleNamespace(**kwargs)


def _helper_temp_path(helper) -> Path:
    """TestHelper's temp dir as a Path across beets versions.

    beets 2.13 renamed ``TestHelper.temp_dir`` (bytes) to ``temp_path``
    (a ``Path``); the declared floor is beets 2.12, so support both.
    """
    if hasattr(helper, "temp_path"):
        return helper.temp_path
    return Path(os.fsdecode(helper.temp_dir))


def _add_file_item(helper, **kwargs):
    """Add a library item backed by a real copied audio file."""
    dest = _helper_temp_path(helper) / f"track_{next(_counter)}.mp3"
    shutil.copy(RSRC / "empty.mp3", dest)
    return helper.add_item(path=str(dest), format="MP3", **kwargs)


def _write_dataset(dataset_dir: Path, name: str, data: dict) -> None:
    (dataset_dir / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def _seed_file_charts(item, chartlist: ChartList) -> None:
    """Write a CHARTS tag directly to the item's file, bypassing the DB."""
    mf = MediaFile(syspath(item.path))
    mf.charts = chartlist.to_json_string()
    mf.save()


def _file_charts(item) -> list[dict]:
    """Read the CHARTS tag back from the file as parsed JSON."""
    raw = MediaFile(syspath(item.path)).charts
    return json.loads(raw) if raw else []


def _make_chart(
    name: str,
    chart_type: tuple[str, ...] = ("year",),
    positions: dict | None = None,
    score: int = 100,
    highest: int = 1,
) -> Chart:
    chart = Chart(name)
    chart.score = score
    chart.highest = highest
    chart.chart_type = list(chart_type)
    chart.positions = positions if positions is not None else {"1999": 2}
    return chart


_TOP2000 = {
    "chart": "top2000",
    "songs": {"1": {"artist": "Artist A", "title": "Song A"}},
    "editions": [
        {
            "axes": {"year": 2022},
            "size": 3,
            "entries": [{"position": 1, "songs": ["1"]}],
        },
        {
            "axes": {"year": 2023},
            "size": 3,
            "entries": [{"position": 3, "songs": ["1"]}],
        },
    ],
}


class TestRegistrationAndGuards:
    def test_chartsgen_command_registered(self, env):
        helper, plugin, dataset_dir = env
        assert "chartsgen" in [c.name for c in plugin.commands()]

    def test_unconfigured_dataset_dir_is_user_error(self, env):
        helper, plugin, dataset_dir = env
        config["hitlisttag"]["dataset_dir"] = None
        with pytest.raises(ui.UserError, match="dataset_dir"):
            plugin.generate(helper.lib, _opts(), [])

    def test_malformed_dataset_is_user_error(self, env):
        helper, plugin, dataset_dir = env
        (dataset_dir / "broken.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(ui.UserError, match="broken.json"):
            plugin.generate(helper.lib, _opts(), [])

    def test_empty_run_prints_summary(self, env, capsys):
        helper, plugin, dataset_dir = env
        _write_dataset(dataset_dir, "top2000", _TOP2000)
        plugin.generate(helper.lib, _opts(), [])
        assert "Generated charts for 0 of 0 tracks." in capsys.readouterr().out

    def test_empty_dataset_dir_is_user_error(self, env):
        helper, plugin, dataset_dir = env
        with pytest.raises(ui.UserError, match="no chart data"):
            plugin.generate(helper.lib, _opts(), [])


class TestGeneration:
    def test_matched_track_gets_tag_written_to_file(self, env, capsys):
        helper, plugin, dataset_dir = env
        _write_dataset(dataset_dir, "top2000", _TOP2000)
        item = _add_file_item(helper, artist="Artist A", title="Song A")

        plugin.generate(helper.lib, _opts(), [])

        charts = _file_charts(item)
        assert charts == [
            {
                "name": "top2000",
                "score": 4,  # (3+1-1) + (3+1-3)
                "highest": 1,
                "chart_type": ["year"],
                "positions": {"2022": 1, "2023": 3},
            }
        ]
        assert "Generated charts for 1 of 1 tracks." in capsys.readouterr().out

    def test_flexible_fields_materialized_like_chartsupdate(self, env):
        helper, plugin, dataset_dir = env
        _write_dataset(dataset_dir, "top2000", _TOP2000)
        item = _add_file_item(helper, artist="Artist A", title="Song A")

        plugin.generate(helper.lib, _opts(), [])

        fresh = helper.lib.get_item(item.id)
        assert fresh["top2000"] is True
        assert fresh["top2000_score"] == 4
        assert fresh["top2000_highest"] == "1"

    def test_unknown_chart_in_file_survives(self, env):
        helper, plugin, dataset_dir = env
        _write_dataset(dataset_dir, "top2000", _TOP2000)
        item = _add_file_item(helper, artist="Artist A", title="Song A")
        foreign = _make_chart("someones_chart", positions={"1999": 2}, score=7)
        _seed_file_charts(item, ChartList([foreign]))

        plugin.generate(helper.lib, _opts(), [])

        charts = {c["name"]: c for c in _file_charts(item)}
        assert charts["someones_chart"] == foreign.to_dict()
        assert "top2000" in charts

    def test_covered_chart_without_match_survives(self, env):
        # Dataset covers top2000, but only with Artist A; the item's
        # externally written top2000 object must survive untouched, while
        # its top40 match is generated (decision 1 in the spec).
        helper, plugin, dataset_dir = env
        _write_dataset(dataset_dir, "top2000", _TOP2000)
        _write_dataset(
            dataset_dir,
            "top40",
            {
                "chart": "top40",
                "songs": {"1": {"artist": "Artist B", "title": "Song B"}},
                "editions": [
                    {
                        "axes": {"year": 2023, "week": 40},
                        "size": 40,
                        "entries": [{"position": 2, "songs": ["1"]}],
                    }
                ],
            },
        )
        item = _add_file_item(helper, artist="Artist B", title="Song B")
        external_top2000 = _make_chart("top2000", positions={"1999": 5}, score=42)
        _seed_file_charts(item, ChartList([external_top2000]))

        plugin.generate(helper.lib, _opts(), [])

        charts = {c["name"]: c for c in _file_charts(item)}
        assert charts["top2000"] == external_top2000.to_dict()
        assert charts["top40"]["positions"] == {"2023": {"40": 2}}
        assert charts["top40"]["score"] == 39

    def test_unmatched_track_reported_not_touched(self, env, capsys):
        helper, plugin, dataset_dir = env
        _write_dataset(dataset_dir, "top2000", _TOP2000)
        item = _add_file_item(helper, artist="Nobody", title="Nothing")

        plugin.generate(helper.lib, _opts(), [])

        out = capsys.readouterr().out
        assert "Generated charts for 0 of 1 tracks." in out
        assert "Unmatched tracks:" in out
        assert _file_charts(item) == []

    def test_ambiguous_chart_reported_and_skipped(self, env, capsys):
        helper, plugin, dataset_dir = env
        ambiguous = {
            "chart": "top2000",
            "songs": {
                "1": {"artist": "Artist A", "title": "Song A"},
                "2": {"artist": "artist a", "title": "song a"},
            },
            "editions": [
                {
                    "axes": {"year": 2023},
                    "size": 3,
                    "entries": [
                        {"position": 1, "songs": ["1"]},
                        {"position": 2, "songs": ["2"]},
                    ],
                }
            ],
        }
        _write_dataset(dataset_dir, "top2000", ambiguous)
        item = _add_file_item(helper, artist="Artist A", title="Song A")

        plugin.generate(helper.lib, _opts(), [])

        out = capsys.readouterr().out
        assert "Generated charts for 0 of 1 tracks." in out
        assert "Ambiguous tracks" in out
        assert "top2000" in out
        assert _file_charts(item) == []

    def test_partial_ambiguity_still_generates_matched_chart(self, env, capsys):
        # Ambiguous in top2000 but cleanly matched in top40: the matched
        # chart is generated, the ambiguity is reported, and the track
        # counts as generated (not unmatched).
        helper, plugin, dataset_dir = env
        ambiguous = {
            "chart": "top2000",
            "songs": {
                "1": {"artist": "Artist A", "title": "Song A"},
                "2": {"artist": "artist a", "title": "song a"},
            },
            "editions": [
                {
                    "axes": {"year": 2023},
                    "size": 3,
                    "entries": [
                        {"position": 1, "songs": ["1"]},
                        {"position": 2, "songs": ["2"]},
                    ],
                }
            ],
        }
        _write_dataset(dataset_dir, "top2000", ambiguous)
        _write_dataset(
            dataset_dir,
            "top40",
            {
                "chart": "top40",
                "songs": {"1": {"artist": "Artist A", "title": "Song A"}},
                "editions": [
                    {
                        "axes": {"year": 2023, "week": 40},
                        "size": 40,
                        "entries": [{"position": 2, "songs": ["1"]}],
                    }
                ],
            },
        )
        item = _add_file_item(helper, artist="Artist A", title="Song A")

        plugin.generate(helper.lib, _opts(), [])

        out = capsys.readouterr().out
        charts = _file_charts(item)
        assert [c["name"] for c in charts] == ["top40"]
        assert charts[0]["positions"] == {"2023": {"40": 2}}
        assert "Ambiguous tracks" in out
        assert "top2000" in out
        assert "Generated charts for 1 of 1 tracks." in out

    def test_unnormalizable_track_reported_separately(self, env, capsys):
        helper, plugin, dataset_dir = env
        _write_dataset(dataset_dir, "top2000", _TOP2000)
        _add_file_item(helper, artist="!!!", title="???")

        plugin.generate(helper.lib, _opts(), [])

        out = capsys.readouterr().out
        assert "Tracks whose artist/title normalize to nothing:" in out
        assert "Unmatched tracks:" not in out

    def test_unparseable_tag_overwritten_and_reported(self, env, capsys):
        helper, plugin, dataset_dir = env
        _write_dataset(dataset_dir, "top2000", _TOP2000)
        item = _add_file_item(helper, artist="Artist A", title="Song A")
        mf = MediaFile(syspath(item.path))
        mf.charts = "this is not json"
        mf.save()

        plugin.generate(helper.lib, _opts(), [])

        charts = _file_charts(item)
        assert [c["name"] for c in charts] == ["top2000"]
        assert "Existing CHARTS tags that did not parse:" in capsys.readouterr().out

    def test_query_restricts_generation_to_matched_items(self, env, capsys):
        helper, plugin, dataset_dir = env
        _write_dataset(dataset_dir, "top2000", _TOP2000)
        matched = _add_file_item(
            helper, artist="Artist A", title="Song A", album="AlbumOne"
        )
        other = _add_file_item(
            helper, artist="Artist A", title="Song A", album="AlbumTwo"
        )

        plugin.generate(helper.lib, _opts(), ["album:AlbumOne"])

        assert _file_charts(matched) != []
        assert _file_charts(other) == []
        assert "Generated charts for 1 of 1 tracks." in capsys.readouterr().out


class TestFileErrors:
    def test_unreadable_file_skipped_run_continues(self, env, capsys):
        helper, plugin, dataset_dir = env
        _write_dataset(dataset_dir, "top2000", _TOP2000)
        bad = _add_file_item(helper, artist="Artist A", title="Song A")
        Path(bad.path.decode()).write_text("not audio", encoding="utf-8")
        good = _add_file_item(helper, artist="Artist A", title="Song A")

        plugin.generate(helper.lib, _opts(), [])

        out = capsys.readouterr().out
        assert "Generated charts for 1 of 2 tracks." in out
        assert "Unreadable files:" in out
        assert _file_charts(good) != []

    def test_write_failure_reported(self, env, capsys, monkeypatch):
        from beets.library import Item

        helper, plugin, dataset_dir = env
        _write_dataset(dataset_dir, "top2000", _TOP2000)
        _add_file_item(helper, artist="Artist A", title="Song A")
        monkeypatch.setattr(Item, "try_write", lambda self, *a, **k: False)

        plugin.generate(helper.lib, _opts(), [])

        out = capsys.readouterr().out
        assert "Generated charts for 0 of 1 tracks." in out
        assert "Files that could not be written:" in out

        fresh = next(iter(helper.lib.items()))
        assert "top2000" not in fresh._values_flex
        assert "charts" not in fresh._values_flex
