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


def _run(env, *args, **opts):
    env.plugin.catalog_command(env.helper.lib, SimpleNamespace(**opts), list(args))


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

    def test_ambiguous_fallback_is_reported_and_exits_zero(self, env, capsys):
        _seed(env)
        _dataset(
            env,
            "top2000",
            {
                "3": {"artist": "Scorpions", "title": "Hello Josephine"},
                "4": {"artist": "Scorpions (UK)", "title": "Hello Josephine"},
            },
            [
                {
                    "axes": {"year": 2023},
                    "size": 2000,
                    "entries": [
                        {"position": 9, "songs": ["3"]},
                        {"position": 11, "songs": ["4"]},
                    ],
                }
            ],
        )
        catalog = json.loads((env.data / "catalog.json").read_text(encoding="utf-8"))
        catalog["songs"]["1"]["aliases"].append(
            {"artist": "Scorpions (UK)", "title": "Hello Josephine"}
        )
        _write(env.data / "catalog.json", catalog)
        _run(env, "check")  # no UserError: not counted as a problem
        out = capsys.readouterr().out
        assert (
            "ambiguous fallback: song 1 (Scorpions - Hello Josephine) top2000: "
            "3 (Scorpions - Hello Josephine), 4 (Scorpions (UK) - Hello Josephine)"
        ) in out

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


def _songs(env):
    _dataset(
        env,
        "top40",
        {
            "1": {"artist": "Pretend Act", "title": "Made Up Tune"},
            "2": {"artist": "Pretend Act", "title": "Made-Up Tune"},
            "3": {"artist": "Other Act", "title": "Cheer"},
        },
        [
            {
                "axes": {"year": 1970, "week": 1},
                "size": 40,
                "entries": [
                    {"position": 1, "songs": ["1"]},
                    {"position": 2, "songs": ["3"]},
                ],
            },
            {
                "axes": {"year": 1970, "week": 2},
                "size": 40,
                "entries": [{"position": 1, "songs": ["2"]}],
            },
        ],
    )


def _catalog(env):
    return json.loads((env.data / "catalog.json").read_text(encoding="utf-8"))


class TestCurate:
    def test_merge_with_yes_writes(self, env, capsys):
        _songs(env)
        _run(env, "merge", "top40:1", "top40:2", yes=True)
        written = _catalog(env)
        assert written["next_id"] == 2
        assert [ln["song"] for ln in written["songs"]["1"]["links"]] == ["1", "2"]
        assert "merge into new song @1" in capsys.readouterr().out

    def test_a_declined_prompt_writes_nothing(self, env, monkeypatch):
        _songs(env)
        monkeypatch.setattr(ui, "input_yn", lambda *a, **k: False)
        _run(env, "merge", "top40:1", "top40:2")
        assert not (env.data / "catalog.json").exists()

    def test_the_prompt_has_no_default(self, env, monkeypatch):
        _songs(env)
        seen = {}

        def answer(prompt, require=False):
            seen["require"] = require
            return True

        monkeypatch.setattr(ui, "input_yn", answer)
        _run(env, "merge", "top40:1", "top40:2")
        assert seen == {"require": True} and _catalog(env)["songs"]

    def test_a_query_is_picked_from(self, env, monkeypatch):
        _songs(env)
        monkeypatch.setattr(ui, "input_", lambda *a, **k: "1 2")
        monkeypatch.setattr(ui, "input_yn", lambda *a, **k: True)
        _run(env, "merge", "made", "tune")
        assert [ln["song"] for ln in _catalog(env)["songs"]["1"]["links"]] == ["1", "2"]

    @pytest.mark.parametrize("answer", ["0", "3", "x"])
    def test_a_pick_out_of_range_is_refused(self, env, monkeypatch, answer):
        _songs(env)
        monkeypatch.setattr(ui, "input_", lambda *a, **k: answer)
        with pytest.raises(ui.UserError, match="pick numbers from 1 to 2"):
            _run(env, "merge", "made", "tune")

    def test_an_empty_pick_writes_nothing(self, env, monkeypatch):
        _songs(env)
        monkeypatch.setattr(ui, "input_", lambda *a, **k: "")
        _run(env, "merge", "made", "tune")
        assert not (env.data / "catalog.json").exists()

    def test_yes_with_a_query_needs_exactly_one_match(self, env):
        _songs(env)
        with pytest.raises(ui.UserError, match="matches 2 songs"):
            _run(env, "merge", "top40:3", "made", "tune", yes=True)
        _run(env, "alias", "cheer", "Other Act - Cheers", yes=True)
        assert _catalog(env)["songs"]["1"]["aliases"] == [
            {"artist": "Other Act", "title": "Cheers"}
        ]

    def test_a_query_with_too_many_matches_is_refused(self, env, monkeypatch):
        _dataset(
            env,
            "top40",
            {str(i): {"artist": "Act", "title": f"Love {i}"} for i in range(1, 25)},
            [
                {
                    "axes": {"year": 1970, "week": 1},
                    "size": 40,
                    "entries": [
                        {"position": i, "songs": [str(i)]} for i in range(1, 25)
                    ],
                }
            ],
        )
        with pytest.raises(ui.UserError, match="24 songs match.*narrow"):
            _run(env, "merge", "love")

    def test_name_folds(self, env):
        _songs(env)
        _run(env, "merge", "top40:3", name="Other Act - Cheer!", yes=True)
        song = _catalog(env)["songs"]["1"]
        assert (song["artist"], song["title"]) == ("Other Act", "Cheer!")

    def test_unlink_unalias_drop(self, env):
        _songs(env)
        _run(env, "merge", "top40:1", "top40:2", name="Pretend Act - Tune", yes=True)
        _run(env, "unlink", "@1", "top40:2", yes=True)
        _run(env, "unalias", "@1", "Pretend Act - Tune", yes=True)
        assert _catalog(env)["songs"]["1"]["aliases"] == []
        _run(env, "drop", "@1", yes=True)
        assert _catalog(env) == {"catalog": 1, "next_id": 2, "songs": {}}

    def test_find_and_show_print(self, env, capsys):
        _songs(env)
        _run(env, "find", "cheer")
        assert capsys.readouterr().out.startswith("top40:3")
        _run(env, "show", "top40:3")
        assert "linked by no catalog song" in capsys.readouterr().out

    def test_a_rebind_is_written_with_the_change(self, env, capsys):
        # _seed's link records raw id 1 with title id 75; the live data now
        # cites that song as id 5, so the link re-binds by the title id.
        _seed(env)
        _dataset(
            env,
            "top40",
            {"5": {"artist": "The Scorpions ((GBR))", "title": "Hello Josephine"}},
            [
                {
                    "axes": {"year": 1965, "week": 1},
                    "size": 40,
                    "entries": [
                        {
                            "position": 1,
                            "songs": ["5"],
                            "source_ids": {"top40.nl/title": "75"},
                        }
                    ],
                }
            ],
        )
        _run(env, "alias", "@1", "Scorpions - Hello Josefine", yes=True)
        assert "also writing a re-bind: @1 top40 1 -> 5" in capsys.readouterr().out
        assert _catalog(env)["songs"]["1"]["links"][0]["song"] == "5"

    def test_rebinds_are_written_even_when_the_change_is_none(self, env, capsys):
        _seed(env)  # @1 already has the alias Scorpions - Hello Josephine
        _dataset(
            env,
            "top40",
            {"5": {"artist": "The Scorpions ((GBR))", "title": "Hello Josephine"}},
            [
                {
                    "axes": {"year": 1965, "week": 1},
                    "size": 40,
                    "entries": [
                        {
                            "position": 1,
                            "songs": ["5"],
                            "source_ids": {"top40.nl/title": "75"},
                        }
                    ],
                }
            ],
        )
        _run(env, "alias", "@1", "Scorpions - Hello Josephine", yes=True)
        out = capsys.readouterr().out
        assert "already has that alias" in out
        assert "also writing a re-bind: @1 top40 1 -> 5" in out
        assert _catalog(env)["songs"]["1"]["links"][0]["song"] == "5"

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (("merge", "top40:9", "top40:1"), "top40:9 is not in the current data"),
            (("drop", "@7"), "no catalog song @7"),
            (("alias", "@1", "No Dash"), "Artist - Title"),
            (("merge", "top40:1"), "two references"),
            (("find",), "find needs words"),
            (("show", "top40:1", "top40:2"), "show takes one song"),
            (("unalias", "top40:1", "A - B"), "unalias takes a catalog song"),
            (("drop", "top40:1"), "drop takes one catalog song"),
            (("unlink", "@1"), "unlink takes a catalog song and a raw song"),
            (("check", "now"), "check takes no arguments"),
            (("merge", "top40:1", "top40:1"), "two references"),
            (("merge", "@1", "@1", "top40:1"), "named twice"),
            (("merge", "top2000:5", "top40:1"), "top2000:5 is not in the current data"),
        ],
    )
    def test_errors_write_nothing(self, env, args, message):
        _songs(env)
        with pytest.raises(ui.UserError, match=message):
            _run(env, *args, yes=True)
        assert not (env.data / "catalog.json").exists()


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
