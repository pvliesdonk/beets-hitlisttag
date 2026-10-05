"""Command-level tests for chartscatalog check."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from beets import config, ui
from beets.plugins import find_plugins, load_plugins

from beetsplug.hitlisttag import HitlistTag

HITLISTS = {"top40": ["year", "week"], "top2000": ["year"]}
log = logging.getLogger("test.chartscatalog")


@pytest.fixture
def env(tmp_path):
    from beets.test.helper import TestHelper

    helper = TestHelper()
    with helper:
        config["plugins"] = ["hitlisttag"]
        config["hitlisttag"]["hitlists"] = HITLISTS
        config["hitlisttag"]["dataset_dir"] = str(tmp_path / "data")
        load_plugins()
        plugin = next(p for p in find_plugins() if isinstance(p, HitlistTag))
        yield SimpleNamespace(helper=helper, plugin=plugin, data=tmp_path / "data")


def _run(env, *args):
    env.plugin.catalog_command(env.helper.lib, SimpleNamespace(), list(args))


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _dataset(env, chart, songs, editions):
    _write(
        env.data / f"{chart}.json",
        {"chart": chart, "songs": songs, "editions": editions},
    )


def _seed(env, top40_song="The Scorpions ((GBR))"):
    _dataset(
        env,
        "top40",
        {"1": {"artist": top40_song, "title": "Hello Josephine"}},
        [
            {
                "axes": {"year": 1965, "week": 1},
                "size": 40,
                "entries": [
                    {
                        "position": 1,
                        "songs": ["1"],
                        "source_ids": {"top40.nl/title": "75"},
                    }
                ],
            }
        ],
    )
    _write(
        env.data / "catalog.json",
        {
            "catalog": 1,
            "songs": {
                "1": {
                    "artist": "Scorpions",
                    "title": "Hello Josephine",
                    "aliases": [{"artist": "Scorpions", "title": "Hello Josephine"}],
                    "links": [
                        {
                            "chart": "top40",
                            "song": "1",
                            "artist": "The Scorpions ((GBR))",
                            "title": "Hello Josephine",
                            "source_ids": {"top40.nl/title": "75"},
                        }
                    ],
                }
            },
        },
    )


class TestCheck:
    def test_registered(self, env):
        assert "chartscatalog" in [c.name for c in env.plugin.commands()]

    def test_clean_catalog_reports_and_exits_zero(self, env, capsys):
        _seed(env)
        _run(env, "check")
        out = capsys.readouterr().out
        assert "catalog: 1 song, 1 alias, 1 link" in out
        assert "links: 1 bound, 0 re-bound, 0 dangling" in out

    def test_missing_catalog_is_empty_and_exits_zero(self, env, capsys):
        _dataset(env, "top40", {}, [])
        _run(env, "check")
        assert "catalog: 0 songs, 0 aliases, 0 links" in capsys.readouterr().out

    def test_rebind_is_persisted_and_reported(self, env, capsys):
        _seed(env)
        # The source respelled the song: a --force run minted id 2 and kept 1.
        _dataset(
            env,
            "top40",
            {
                "1": {"artist": "The Scorpions ((GBR))", "title": "Hello Josephine"},
                "2": {"artist": "Scorpions ((GBR))", "title": "Hello Josephine"},
            },
            [
                {
                    "axes": {"year": 1965, "week": 1},
                    "size": 40,
                    "entries": [
                        {
                            "position": 1,
                            "songs": ["2"],
                            "source_ids": {"top40.nl/title": "75"},
                        }
                    ],
                }
            ],
        )
        _run(env, "check")
        out = capsys.readouterr().out
        assert "re-bound: song 1 (Scorpions - Hello Josephine) top40 1 -> 2" in out
        stored = json.loads((env.data / "catalog.json").read_text(encoding="utf-8"))
        link = stored["songs"]["1"]["links"][0]
        assert link["song"] == "2"
        assert link["artist"] == "Scorpions ((GBR))"

    def test_no_write_when_nothing_changed(self, env):
        _seed(env)
        path = env.data / "catalog.json"
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        _run(env, "check")
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before

    def test_dangling_link_is_named_and_exits_one(self, env, capsys):
        _seed(env)
        _dataset(env, "top40", {}, [])  # the song is gone
        with pytest.raises(ui.UserError, match="1 dangling link"):
            _run(env, "check")
        out = capsys.readouterr().out
        assert (
            "dangling: song 1 (Scorpions - Hello Josephine) top40 1 "
            "(recorded: The Scorpions ((GBR)) - Hello Josephine)" in out
        )

    def test_a_rebind_onto_an_already_linked_id_is_dangling_not_an_error(
        self, env, capsys
    ):
        _dataset(
            env,
            "top2000",
            {"11": {"artist": "The Scorpions", "title": "Hello Josephine"}},
            [
                {
                    "axes": {"year": 2023},
                    "size": 2000,
                    "entries": [{"position": 1, "songs": ["11"]}],
                }
            ],
        )
        links = [
            {
                "chart": "top2000",
                "song": song,
                "artist": artist,
                "title": "Hello Josephine",
            }
            for song, artist in (("10", "Scorpions"), ("11", "The Scorpions"))
        ]
        _write(
            env.data / "catalog.json",
            {
                "catalog": 1,
                "songs": {
                    "1": {
                        "artist": "Scorpions",
                        "title": "Hello Josephine",
                        "aliases": [],
                        "links": links,
                    }
                },
            },
        )
        path = env.data / "catalog.json"
        before = path.read_bytes()
        with pytest.raises(ui.UserError, match="1 dangling link"):
            _run(env, "check")
        assert "links: 1 bound, 0 re-bound, 1 dangling" in capsys.readouterr().out
        assert path.read_bytes() == before

    def test_implicit_pair_exits_one(self, env, capsys):
        _dataset(
            env,
            "top40",
            {"5": {"artist": "Bangles", "title": "Eternal Flame"}},
            [
                {
                    "axes": {"year": 1989, "week": 1},
                    "size": 40,
                    "entries": [{"position": 1, "songs": ["5"]}],
                }
            ],
        )
        _dataset(
            env,
            "top2000",
            {"3": {"artist": "The Bangles", "title": "Eternal Flame"}},
            [
                {
                    "axes": {"year": 2023},
                    "size": 2000,
                    "entries": [{"position": 9, "songs": ["3"]}],
                }
            ],
        )
        _write(
            env.data / "catalog.json",
            {
                "catalog": 1,
                "songs": {
                    "1": {
                        "artist": "Bangles",
                        "title": "Eternal Flame",
                        "aliases": [],
                        "links": [
                            {
                                "chart": "top40",
                                "song": "5",
                                "artist": "Bangles",
                                "title": "Eternal Flame",
                            }
                        ],
                    },
                    "2": {
                        "artist": "The Bangles",
                        "title": "Eternal Flame",
                        "aliases": [],
                        "links": [
                            {
                                "chart": "top2000",
                                "song": "3",
                                "artist": "The Bangles",
                                "title": "Eternal Flame",
                            }
                        ],
                    },
                },
            },
        )
        with pytest.raises(ui.UserError, match="1 implicit alias pair"):
            _run(env, "check")
        assert "implicit alias pair: top2000 3 and top40 5" in capsys.readouterr().out

    def test_dataset_file_at_the_reserved_path_is_a_user_error(self, env):
        _write(
            env.data / "catalog.json", {"chart": "catalog", "songs": {}, "editions": []}
        )
        with pytest.raises(ui.UserError, match="reserved"):
            _run(env, "check")

    def test_stray_catalog_file_is_warned_about(self, env, caplog):
        _seed(env)
        _write(env.data / "old" / "catalog.json", {"catalog": 1, "songs": {}})
        with caplog.at_level(logging.WARNING, logger="beets.hitlisttag"):
            _run(env, "check")
        assert "looks like a catalog file" in caplog.text

    def test_malformed_catalog_is_a_user_error(self, env):
        _dataset(env, "top40", {}, [])
        _write(env.data / "catalog.json", {"catalog": 1, "songs": [], "x": 1})
        with pytest.raises(ui.UserError, match="hitlisttag: .*catalog.json"):
            _run(env, "check")


class TestPreconditions:
    def test_unknown_action(self, env):
        with pytest.raises(ui.UserError, match="one action: check"):
            _run(env, "verify")
        with pytest.raises(ui.UserError, match="one action: check"):
            _run(env)

    def test_dataset_dir_unset(self, env):
        config["hitlisttag"]["dataset_dir"] = None
        with pytest.raises(ui.UserError, match="requires the dataset_dir option"):
            _run(env, "check")
