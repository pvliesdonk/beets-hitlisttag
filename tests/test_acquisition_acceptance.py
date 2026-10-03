"""Milestone 7 acceptance, pinned clause by clause (#104).

The criterion, verbatim from the milestone:

    A user can populate and refresh the local dataset for at least one real
    chart from its public source by running the bundled tool, and re-running
    acquires only what is missing. A third party can add a new chart by
    writing their own ingestor without modifying this package. The package
    ships no chart data.

Each test below names the clause it pins. The real-source clause ("at least
one real chart from its public source") is the Top 2000 ingestor's own
(#102): its parser runs against synthetic fixtures in CI and against a
saved copy of the real page in the opt-in ``HITLISTTAG_TOP2000_WIKITEXT``
test. Here the chart is a third party's, so the test stays offline.
"""

from __future__ import annotations

import itertools
import json
import logging
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from beets import config
from beets.plugins import find_plugins, load_plugins
from beets.util import syspath
from mediafile import MediaFile

from beetsplug.hitlisttag import HitlistTag
from beetsplug.hitlisttag.dataset import read_dataset
from beetsplug.hitlisttag.ingest import discover_ingestors

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_INGESTOR = ROOT / "tests" / "fixtures" / "ingestors" / "testchart.py"
RSRC = ROOT / "tests" / "rsrc"
HITLISTS = {"testchart": ["year"]}
log = logging.getLogger("test.acceptance")

# Appended to a *copy* of the fixture ingestor: records each fetch so the
# test can tell a re-run that fetches nothing from one that fetches again.
_COUNTER = """

from pathlib import Path as _Path

_CALLS = _Path(__file__).with_suffix(".calls")


class _CountingIngestor(FixtureIngestor):
    def fetch(self, ref):
        with _CALLS.open("a", encoding="utf-8") as fh:
            fh.write(f"{ref.axes['year']}\\n")
        return super().fetch(ref)


INGESTOR = _CountingIngestor()
"""

_counter = itertools.count()


@pytest.fixture
def env(tmp_path):
    """Plugin loaded with a third-party ingestor outside the package."""
    from beets.test.helper import TestHelper

    ingestors = tmp_path / "ingestors"
    ingestors.mkdir()
    script = ingestors / "testchart.py"
    script.write_text(
        FIXTURE_INGESTOR.read_text(encoding="utf-8") + _COUNTER, encoding="utf-8"
    )
    helper = TestHelper()
    with helper:
        config["plugins"] = ["hitlisttag"]
        config["hitlisttag"]["hitlists"] = HITLISTS
        config["hitlisttag"]["dataset_dir"] = str(tmp_path / "data")
        config["hitlisttag"]["ingestor_dir"] = str(ingestors)
        load_plugins()
        plugin = next(p for p in find_plugins() if isinstance(p, HitlistTag))
        yield SimpleNamespace(
            helper=helper,
            plugin=plugin,
            data=tmp_path / "data",
            ingestors=ingestors,
            calls=script.with_suffix(".calls"),
            tmp=tmp_path,
        )


def _acquire(env, *charts):
    env.plugin.acquire(
        env.helper.lib, SimpleNamespace(force=False, prune=False), list(charts)
    )


def _fetches(env) -> list[int]:
    if not env.calls.exists():
        return []
    return [int(y) for y in env.calls.read_text(encoding="utf-8").split()]


def _add_file_item(env, artist: str, title: str):
    dest = env.tmp / f"track_{next(_counter)}.mp3"
    shutil.copy(RSRC / "empty.mp3", dest)
    return env.helper.add_item(path=str(dest), format="MP3", artist=artist, title=title)


def _file_chart(item, name: str) -> dict | None:
    raw = MediaFile(syspath(item.path)).charts
    for chart in json.loads(raw) if raw else []:
        if chart["name"] == name:
            return chart
    return None


class TestAcceptance:
    def test_third_party_ingestor_needs_no_package_change(self, env):
        """Clause: a third party can add a new chart by writing their own
        ingestor without modifying this package."""
        found = discover_ingestors(env.ingestors, log=log)
        assert "testchart" in found
        assert not type(found["testchart"]).__module__.startswith("beetsplug.")
        tracked = subprocess.run(
            ["git", "ls-files", "beetsplug/"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        assert "testchart" not in tracked

    def test_bundled_tool_populates_dataset_through_to_tags(self, env, capsys):
        """Clause: a user can populate the local dataset by running the
        bundled tool — and the result reaches the library via chartsgen."""
        _acquire(env, "testchart")
        out = capsys.readouterr().out
        assert "testchart: acquired 2 editions (2001–2002)" in out
        data = {d.chart: d for d in read_dataset(env.data, HITLISTS, log)}
        assert [e.axes["year"] for e in data["testchart"].editions] == [2001, 2002]

        solo = _add_file_item(env, "Fixture Artist", "Song of 2001")
        side_a = _add_file_item(env, "Pair", "Side A")
        side_b = _add_file_item(env, "Pair", "Side B")
        env.plugin.generate(env.helper.lib, SimpleNamespace(), [])

        solo_chart = _file_chart(solo, "testchart")
        assert solo_chart is not None, "chartsgen wrote no testchart object"
        assert solo_chart["positions"] == {"2001": 1}
        assert solo_chart["score"] == 3  # size 3, position 1
        # Both sides of the two-song release are credited at its one rank.
        for item in (side_a, side_b):
            chart = _file_chart(item, "testchart")
            assert chart is not None, f"no testchart object for {item.title}"
            assert chart["positions"] == {"2001": 2, "2002": 2}
            assert chart["score"] == 4  # (3 + 1 - 2) twice

        env.plugin.update(env.helper.lib, SimpleNamespace(), [])
        fresh = env.helper.lib.get_item(side_a.id)
        assert fresh["testchart"] is True
        assert fresh["testchart_score"] == 4

    def test_rerun_acquires_only_what_is_missing(self, env, capsys):
        """Clause: re-running acquires only what is missing."""
        _acquire(env, "testchart")
        path = env.data / "testchart.json"
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        assert _fetches(env) == [2001, 2002]
        capsys.readouterr()

        _acquire(env, "testchart")
        assert "testchart: up to date (2 editions)" in capsys.readouterr().out
        assert _fetches(env) == [2001, 2002]  # nothing fetched again
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before

    def test_package_ships_no_chart_data(self):
        """Clause: the package ships no chart data.

        Everything tracked under beetsplug/ is Python source; #87's
        scripts/check_dist.py proves the wheel holds exactly those files.
        """
        if shutil.which("git") is None or not (ROOT / ".git").exists():
            pytest.skip("needs a git checkout")
        tracked = subprocess.run(
            ["git", "ls-files", "beetsplug/"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert tracked, "git lists nothing under beetsplug/"
        assert [p for p in tracked if not p.endswith(".py")] == []
