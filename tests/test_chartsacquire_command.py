"""Command-level tests for chartsacquire, with drop-in fake ingestors."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from beets import config, ui
from beets.plugins import find_plugins, load_plugins

from beetsplug.hitlisttag import HitlistTag
from beetsplug.hitlisttag.dataset import read_dataset

FAKE_SCRIPT = """
import builtins
import json
from pathlib import Path

from beetsplug.hitlisttag.ingest import (
    AcquiredEdition, EditionRef, IngestError, RawEntry, RawSong,
)

STATE = Path({state!r})


class Fake:
    chart = {chart!r}
    axes = {axes!r}

    def _state(self):
        return json.loads(STATE.read_text(encoding="utf-8"))

    def editions(self):
        return [EditionRef({{"year": int(y)}}) for y in self._state()["editions"]]

    def fetch(self, ref):
        state = self._state()
        year = ref.axes["year"]
        with STATE.with_suffix(".calls").open("a", encoding="utf-8") as fh:
            fh.write(f"{{year}}\\n")
        if year == state.get("fail_on"):
            raise IngestError(f"source broke on {{year}}")
        raise_on = state.get("raise_on") or {{}}
        if str(year) in raise_on:
            name, _, message = raise_on[str(year)].partition(":")
            raise getattr(builtins, name)(message or "boom")
        pairs = state["editions"][str(year)]
        entries = tuple(
            RawEntry(i + 1, (RawSong(a, t),)) for i, (a, t) in enumerate(pairs)
        )
        return AcquiredEdition(ref, len(pairs), entries)


INGESTOR = Fake()
"""


class FakeSource:
    """A drop-in ingestor script plus a JSON state file the tests mutate."""

    def __init__(self, ingestor_dir: Path, chart: str, axes=("year",)):
        ingestor_dir.mkdir(parents=True, exist_ok=True)
        self.state = ingestor_dir / f"{chart}.state.json"
        (ingestor_dir / f"fake_{chart}.py").write_text(
            FAKE_SCRIPT.format(state=str(self.state), chart=chart, axes=tuple(axes)),
            encoding="utf-8",
        )
        self.set({})

    def set(
        self,
        editions: dict[int, list[tuple[str, str]]],
        fail_on=None,
        raise_on: dict[int, str] | None = None,
    ):
        """``raise_on`` maps a year to a builtin exception to raise there.

        The value is the exception's name, optionally followed by
        ``:message`` (the message defaults to "boom").
        """
        self.state.write_text(
            json.dumps(
                {
                    "editions": {str(y): p for y, p in editions.items()},
                    "fail_on": fail_on,
                    "raise_on": {str(y): e for y, e in (raise_on or {}).items()},
                }
            ),
            encoding="utf-8",
        )

    def calls(self) -> list[int]:
        calls = self.state.with_suffix(".calls")
        return [int(x) for x in calls.read_text().split()] if calls.exists() else []


@pytest.fixture
def env(tmp_path):
    from beets.test.helper import TestHelper

    helper = TestHelper()
    with helper:
        config["plugins"] = ["hitlisttag"]
        config["hitlisttag"]["hitlists"] = {"fake": ["year"], "other": ["year"]}
        config["hitlisttag"]["dataset_dir"] = str(tmp_path / "data")
        config["hitlisttag"]["ingestor_dir"] = str(tmp_path / "ingestors")
        load_plugins()
        plugin = next(p for p in find_plugins() if isinstance(p, HitlistTag))
        yield SimpleNamespace(
            helper=helper,
            plugin=plugin,
            data=tmp_path / "data",
            ingestors=tmp_path / "ingestors",
        )


def _run(env, *charts):
    env.plugin.acquire(env.helper.lib, SimpleNamespace(), list(charts))


def _dataset(env):
    return {
        d.chart: d
        for d in read_dataset(env.data, {"fake": ["year"], "other": ["year"]}, _log())
    }


def _log():
    import logging

    return logging.getLogger("test.chartsacquire")


class TestChartsacquire:
    def test_registered(self, env):
        assert "chartsacquire" in [c.name for c in env.plugin.commands()]

    def test_first_run_creates_file_and_reports(self, env, capsys):
        # Review focus 5: dataset_dir does not exist yet.
        src = FakeSource(env.ingestors, "fake")
        src.set({2001: [["A", "x"], ["B", "y"]], 2002: [["A", "x"]]})
        assert not env.data.exists()
        _run(env, "fake")
        out = capsys.readouterr().out
        assert "fake: acquired 2 editions (2001–2002), 3 entries, 2 new songs" in out
        assert (env.data / "fake.json").exists()
        assert [e.axes["year"] for e in _dataset(env)["fake"].editions] == [2001, 2002]

    def test_second_run_up_to_date_and_untouched(self, env, capsys):
        src = FakeSource(env.ingestors, "fake")
        src.set({2001: [["A", "x"]]})
        _run(env, "fake")
        path = env.data / "fake.json"
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        capsys.readouterr()
        _run(env, "fake")
        assert "fake: up to date (1 edition)" in capsys.readouterr().out
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before
        assert src.calls() == [2001]

    def test_source_grows_only_new_edition_fetched(self, env):
        src = FakeSource(env.ingestors, "fake")
        src.set({2001: [["A", "x"]]})
        _run(env, "fake")
        src.set({2001: [["A", "x"]], 2002: [["A", "x"], ["C", "z"]]})
        _run(env, "fake")
        assert src.calls() == [2001, 2002]
        e2002 = next(
            e for e in _dataset(env)["fake"].editions if e.axes["year"] == 2002
        )
        assert [s.id for en in e2002.entries for s in en.songs] == ["1", "2"]

    def test_hand_authored_file_in_subdirectory_updated_in_place(self, env):
        sub = env.data / "hand"
        sub.mkdir(parents=True)
        (sub / "mine.json").write_text(
            json.dumps(
                {
                    "chart": "fake",
                    "songs": {"1": {"artist": "Hand", "title": "Made"}},
                    "editions": [
                        {
                            "axes": {"year": 1999},
                            "size": 5,
                            "entries": [{"position": 4, "songs": ["1"]}],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        FakeSource(env.ingestors, "fake").set({2001: [["Hand", "made"]]})
        _run(env, "fake")
        assert not (env.data / "fake.json").exists()
        data = _dataset(env)["fake"]
        assert data.source == sub / "mine.json"
        assert [e.axes["year"] for e in data.editions] == [1999, 2001]
        assert data.editions[1].entries[0].songs[0].id == "1"

    def test_failed_edition_keeps_the_rest_and_fails_the_command(self, env, capsys):
        FakeSource(env.ingestors, "fake").set(
            {2001: [["A", "x"]], 2002: [["B", "y"]]}, fail_on=2002
        )
        FakeSource(env.ingestors, "other").set({2001: [["C", "z"]]})
        with pytest.raises(ui.UserError, match="fake"):
            _run(env, "fake", "other")
        out = capsys.readouterr().out
        assert "fake: acquired 1 edition (2001), 1 entry, 1 new song" in out
        assert (
            "fake: 1 edition failed (2002): source broke on 2002; "
            "a later run retries them" in out
        )
        assert "other: acquired 1 edition" in out
        assert [e.axes["year"] for e in _dataset(env)["fake"].editions] == [2001]
        assert (env.data / "other.json").exists()

    def test_no_arguments_acquires_configured_charts_and_skips_unconfigured(self, env):
        FakeSource(env.ingestors, "fake").set({2001: [["A", "x"]]})
        FakeSource(env.ingestors, "stranger").set({2001: [["S", "s"]]})
        _run(env)
        assert (env.data / "fake.json").exists()
        assert not (env.data / "stranger.json").exists()

    def test_chart_named_twice_runs_once(self, env, capsys):
        # Review focus 4.
        src = FakeSource(env.ingestors, "fake")
        src.set({2001: [["A", "x"]]})
        _run(env, "fake", "fake")
        assert capsys.readouterr().out.count("fake: acquired") == 1
        assert src.calls() == [2001]


class TestPreconditions:
    def test_dataset_dir_unset(self, env):
        config["hitlisttag"]["dataset_dir"] = None
        with pytest.raises(ui.UserError, match="dataset_dir"):
            _run(env, "fake")

    def test_unknown_chart_lists_available(self, env):
        FakeSource(env.ingestors, "fake")
        with pytest.raises(ui.UserError, match=r"no ingestor for nope.*fake"):
            _run(env, "nope")

    def test_unconfigured_named_chart(self, env):
        FakeSource(env.ingestors, "stranger")
        with pytest.raises(ui.UserError, match="stranger.*not a configured hitlist"):
            _run(env, "stranger")

    def test_axes_mismatch_fails_without_writing(self, env, capsys):
        FakeSource(env.ingestors, "fake", axes=("year", "week")).set(
            {2001: [["A", "x"]]}
        )
        with pytest.raises(ui.UserError, match="fake"):
            _run(env, "fake")
        assert "differ from configured axes" in capsys.readouterr().out
        assert not (env.data / "fake.json").exists()

    def test_broken_existing_dataset_file(self, env):
        env.data.mkdir(parents=True)
        (env.data / "broken.json").write_text("{nope", encoding="utf-8")
        FakeSource(env.ingestors, "fake").set({2001: [["A", "x"]]})
        with pytest.raises(ui.UserError, match="broken.json"):
            _run(env, "fake")
        assert not (env.data / "fake.json").exists()

    def test_duplicate_drop_ins_are_user_error(self, env):
        FakeSource(env.ingestors, "fake")
        (env.ingestors / "fake_again.py").write_text(
            (env.ingestors / "fake_fake.py").read_text(), encoding="utf-8"
        )
        with pytest.raises(ui.UserError, match="claimed by two"):
            _run(env, "fake")


class TestForceFlags:
    def test_flags_registered(self, env):
        cmd = next(c for c in env.plugin.commands() if c.name == "chartsacquire")
        opts, _ = cmd.parser.parse_args(["--force", "--prune", "fake"])
        assert opts.force is True and opts.prune is True

    def test_force_reacquires_through_the_command(self, env, capsys):
        src = FakeSource(env.ingestors, "fake")
        src.set({2001: [["A", "x"]]})
        _run(env, "fake")
        env.plugin.acquire(
            env.helper.lib, SimpleNamespace(force=True, prune=False), ["fake"]
        )
        assert "fake: re-acquired 1 edition" in capsys.readouterr().out
        assert src.calls() == [2001, 2001]

    def test_prune_without_force_is_user_error(self, env):
        FakeSource(env.ingestors, "fake")
        with pytest.raises(ui.UserError, match="--prune requires --force"):
            env.plugin.acquire(
                env.helper.lib, SimpleNamespace(force=False, prune=True), ["fake"]
            )


IDS_SCRIPT = """
from beetsplug.hitlisttag.ingest import AcquiredEdition, EditionRef, RawEntry, RawSong


class WithIds:
    chart = "fake"
    axes = ("year",)

    def editions(self):
        return [EditionRef({"year": 2001})]

    def fetch(self, ref):
        ids = {"top40.nl/title": "8522", "top40.nl/version": "7417"}
        return AcquiredEdition(
            ref,
            2,
            (
                RawEntry(1, (RawSong("A", "x"),), ids),
                RawEntry(2, (RawSong("B", "y"),)),
            ),
        )


INGESTOR = WithIds()
"""


class TestSourceIds:
    def test_ingestor_ids_reach_the_file_and_a_rerun_leaves_it(self, env):
        env.ingestors.mkdir(parents=True, exist_ok=True)
        (env.ingestors / "with_ids.py").write_text(IDS_SCRIPT, encoding="utf-8")
        _run(env, "fake")
        path = env.data / "fake.json"
        first, second = json.loads(path.read_text(encoding="utf-8"))["editions"][0][
            "entries"
        ]
        assert first["source_ids"] == {
            "top40.nl/title": "8522",
            "top40.nl/version": "7417",
        }
        assert "source_ids" not in second
        before, mtime = path.read_bytes(), path.stat().st_mtime_ns
        _run(env, "fake")
        assert path.read_bytes() == before
        assert path.stat().st_mtime_ns == mtime


class TestUnexpectedEnds:
    """#114 items 1, 3 and 4, #132, through the command."""

    def test_unreadable_subdirectory_stops_before_fetching(self, env, capsys):
        if os.geteuid() == 0:
            pytest.skip("root can read any directory")
        src = FakeSource(env.ingestors, "fake")
        src.set({2001: [["A", "x"]]})
        hidden = env.data / "hidden"
        hidden.mkdir(parents=True)
        (hidden / "fake.json").write_text(
            '{"chart": "fake", "songs": {}, "editions": []}\n', encoding="utf-8"
        )
        hidden.chmod(0)
        try:
            with pytest.raises(ui.UserError, match="cannot read dataset directory"):
                _run(env, "fake")
        finally:
            hidden.chmod(0o755)
        assert not (env.data / "fake.json").exists()
        assert src.calls() == []

    def test_first_run_logs_no_unreadable_directory_warning(self, env, caplog):
        FakeSource(env.ingestors, "fake").set({2001: [["A", "x"]]})
        assert not env.data.exists()
        with caplog.at_level(logging.WARNING):
            _run(env, "fake")
        assert "cannot read dataset directory" not in caplog.text

    def test_bug_in_one_chart_fails_it_and_the_next_still_runs(self, env, capsys):
        FakeSource(env.ingestors, "fake").set(
            {2001: [["A", "x"]], 2002: [["B", "y"]]}, raise_on={2002: "RuntimeError"}
        )
        FakeSource(env.ingestors, "other").set({2001: [["C", "z"]]})
        with pytest.raises(ui.UserError, match="acquisition failed for fake$"):
            _run(env, "fake", "other")
        out = capsys.readouterr().out
        assert (
            "fake: FAILED — ingestor for fake raised RuntimeError: boom; "
            "file keeps the editions written before it" in out
        )
        assert "other: acquired 1 edition" in out
        assert [e.axes["year"] for e in _dataset(env)["fake"].editions] == [2001]

    def test_ctrl_c_reports_the_chart_then_stops(self, env, capsys):
        fake = FakeSource(env.ingestors, "fake")
        fake.set(
            {2001: [["A", "x"]], 2002: [["B", "y"]]},
            raise_on={2002: "KeyboardInterrupt"},
        )
        other = FakeSource(env.ingestors, "other")
        other.set({2001: [["C", "z"]]})
        with pytest.raises(KeyboardInterrupt):
            _run(env, "fake", "other")
        out = capsys.readouterr().out
        assert "fake: acquired 1 edition (2001), 1 entry, 1 new song" in out
        assert "fake: interrupted; file keeps the editions written before it" in out
        assert other.calls() == []
        assert not (env.data / "other.json").exists()

    def test_traceback_with_braces_reaches_the_debug_log(self, env, caplog):
        # beets' logger runs str.format on every message it is given.
        FakeSource(env.ingestors, "fake").set(
            {2001: [["A", "x"]]}, raise_on={2001: "RuntimeError:bad {key}"}
        )
        with caplog.at_level(logging.DEBUG), pytest.raises(ui.UserError):
            _run(env, "fake")
        assert "RuntimeError: bad {key}" in caplog.text
        assert "Traceback" in caplog.text
