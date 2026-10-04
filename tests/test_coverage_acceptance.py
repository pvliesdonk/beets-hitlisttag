"""Milestone 10 acceptance, pinned clause by clause (#121).

The criterion, verbatim from the milestone:

    The Top 40 (weekly) and Top 100 jaarlijst can be populated and kept
    current from their public sources with routine runs of the bundled
    tool. The hitlist missing-report reflects each edition's true size
    instead of the highest position found in the library.

Each test names the clause it pins. They run the real path offline:
``chartsacquire`` discovers the bundled ``top100`` and ``top40`` ingestors,
which fetch through ``top40nl.fetcher()``, a real ``Fetcher`` on requests.
Only the network is replaced, at the bottom: ``HTTPAdapter.send`` serves a
synthetic top40.nl from a dict and answers 404 for anything else, so no
request leaves the process. Pages carry made-up names in the site's markup.

The real-source clause ("from their public sources") is checked against
the live site at the milestone's closeout, not here.
"""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from requests.adapters import HTTPAdapter
from beets import config
from beets.plugins import find_plugins, load_plugins

from beetsplug.hitlisttag import HitlistTag, top40nl
from beetsplug.hitlisttag.dataset import read_dataset
from beetsplug.hitlisttag.ingestors import top40, top100

ROOT = Path(__file__).resolve().parent.parent
RSRC = ROOT / "tests" / "rsrc"
HITLISTS = {"top100": ["year"], "top40": ["year", "week"]}
INDEX = top100.INDEX_PATH

_ITEM = """
<div class="top40-list__item">
    <a href="/made-up/tune-{tid}" class="image-link" tabindex="-1">
        <img src="https://www.top40.nl/media/cache/list/uploads/subtitle/{tid}/o.jpg"
             title="Details {artist} - {title}" alt=""/>
    </a>
    <div class="number-block number-block--red"><span class="h4"> {pos} </span></div>
    <a href="https://www.top40.nl/made-up/tune-{tid}" class="h3">
        <h2 class="h3">{title}</h2></a>
    <a href="https://www.top40.nl/made-up/tune-{tid}" class="p lead lowercase">
        <h3 class="p lead lowercase">{artist}</h3></a>
</div>
"""


def _list_page(title: str, items: str, extra: str = "") -> str:
    return (
        f"<html><head><title>{title}</title></head><body>{extra}"
        f'<div class="list__list">{items}</div></body></html>'
    )


def _year_page(size: int = 100) -> str:
    items = "".join(
        _ITEM.format(
            pos=p, tid=5000 + p, artist=f"Pretend Artist {p}", title=f"Made Up Song {p}"
        )
        for p in range(1, size + 1)
    )
    return _list_page("Top 100", items)


def _week_page(year: int, week: int, previous: tuple[int, int] | None = None) -> str:
    items = "".join(
        _ITEM.format(
            pos=p, tid=7000 + p, artist=f"Weekly Act {p}", title=f"Weekly Tune {p}"
        )
        for p in range(1, top40.SIZE + 1)
    )
    # The first week of a year links the last week before it, as the site's do.
    nav = (
        f'<a href="https://www.top40.nl/top40/{previous[0]}/week-{previous[1]}">'
        "vorige</a>"
        if previous
        else ""
    )
    return _list_page(f"Top 40-lijst van week {week}, {year}", items, nav)


def _index_page(*years: int) -> str:
    links = "".join(f'<a href="https://www.top40.nl{INDEX}/{y}">{y}</a>' for y in years)
    return f"<html><body>{links}</body></html>"


class FakeSite:
    """top40.nl as a dict of path -> page; records every URL requested."""

    def __init__(self) -> None:
        self.pages: dict[str, str] = {}
        self.requested: list[str] = []

    def publish_top100(self, *years: int) -> None:
        self.pages[INDEX] = _index_page(*years)
        for year in years:
            self.pages[f"{INDEX}/{year}"] = _year_page()

    def publish_top40(self, latest: tuple[int, int], weeks) -> None:
        self.pages["/top40"] = _week_page(*latest)
        for year, week in weeks:
            previous = (year - 1, 2) if week == 1 else None
            self.pages[f"/top40/{year}/week-{week}"] = _week_page(year, week, previous)

    def send(self, adapter, request, **kwargs) -> requests.Response:
        self.requested.append(request.url)
        path = request.url.removeprefix(top40nl.BASE_URL)
        response = requests.Response()
        response.url = request.url
        response.request = request
        page = self.pages.get(path)
        response.status_code = 404 if page is None else 200
        response._content = (page or "").encode("utf-8")
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        return response

    def paths(self) -> list[str]:
        return [url.removeprefix(top40nl.BASE_URL) for url in self.requested]


@pytest.fixture
def env(tmp_path, monkeypatch):
    from beets.test.helper import TestHelper

    site = FakeSite()
    monkeypatch.setattr(
        HTTPAdapter,
        "send",
        lambda adapter, request, **kw: site.send(adapter, request, **kw),
    )
    monkeypatch.setattr(top40nl, "MIN_INTERVAL", 0)
    # Test seam: the real table lists every week since 1965 (3,194 pages).
    # A one-year table keeps the same code path: table years, then a year
    # after the table read from its first week's "previous" link.
    monkeypatch.setattr(top40, "LAST_WEEK", {2024: 2})
    (tmp_path / "ingestors").mkdir()
    helper = TestHelper()
    with helper:
        config["plugins"] = ["hitlisttag"]
        config["hitlisttag"]["hitlists"] = HITLISTS
        config["hitlisttag"]["dataset_dir"] = str(tmp_path / "data")
        config["hitlisttag"]["ingestor_dir"] = str(tmp_path / "ingestors")
        load_plugins()
        plugin = next(p for p in find_plugins() if isinstance(p, HitlistTag))
        yield SimpleNamespace(
            helper=helper,
            plugin=plugin,
            site=site,
            data=tmp_path / "data",
            tmp=tmp_path,
            monkeypatch=monkeypatch,
        )


def _routine_run(env) -> None:
    """One ``beet chartsacquire top100 top40``, as a fresh process would run it.

    Each bundled ingestor caches its listing per instance; a real run is a
    new process with new instances, so each run here gets new ones.
    """
    env.monkeypatch.setattr(top100, "INGESTOR", top100.Top100Ingestor())
    env.monkeypatch.setattr(top40, "INGESTOR", top40.Top40Ingestor())
    env.site.requested.clear()
    env.plugin.acquire(
        env.helper.lib, SimpleNamespace(force=False, prune=False), ["top100", "top40"]
    )


def _editions(env) -> dict[str, list]:
    datasets = {d.chart: d for d in read_dataset(env.data, HITLISTS, _log())}
    return {
        chart: [(tuple(e.axes.values()), e.size) for e in data.editions]
        for chart, data in datasets.items()
    }


def _log():
    import logging

    return logging.getLogger("test.coverage_acceptance")


def _initial_site(env) -> None:
    env.site.publish_top100(2023, 2024)
    env.site.publish_top40(
        latest=(2025, 2), weeks=[(2024, 1), (2024, 2), (2025, 1), (2025, 2)]
    )


_counter = itertools.count()


def _add_file_item(env, artist: str, title: str):
    dest = env.tmp / f"track_{next(_counter)}.mp3"
    shutil.copy(RSRC / "empty.mp3", dest)
    return env.helper.add_item(path=str(dest), format="MP3", artist=artist, title=title)


class TestAcceptance:
    def test_bundled_tool_populates_both_charts(self, env, capsys):
        """Clause: the Top 40 (weekly) and Top 100 jaarlijst can be populated
        from their public sources with the bundled tool."""
        _initial_site(env)

        _routine_run(env)

        out = capsys.readouterr().out
        assert "top100: acquired 2 editions (2023–2024), 200 entries" in out
        assert "top40: acquired 4 editions, 160 entries" in out
        assert _editions(env) == {
            "top100": [((2023,), 100), ((2024,), 100)],
            "top40": [
                ((2024, 1), 40),
                ((2024, 2), 40),
                ((2025, 1), 40),
                ((2025, 2), 40),
            ],
        }

    def test_routine_runs_keep_both_charts_current(self, env, capsys):
        """Clause: ... and kept current with routine runs."""
        _initial_site(env)
        _routine_run(env)
        capsys.readouterr()

        # The site publishes a new year list and a new week.
        env.site.publish_top100(2023, 2024, 2025)
        env.site.publish_top40(latest=(2025, 3), weeks=[(2025, 3)])
        _routine_run(env)

        out = capsys.readouterr().out
        assert "top100: acquired 1 edition (2025), 100 entries, 0 new songs" in out
        assert "top40: acquired 1 edition, 40 entries, 0 new songs" in out
        # Only the listing pages and the two new editions were fetched.
        assert env.site.paths() == [
            INDEX,
            f"{INDEX}/2025",
            "/top40",
            "/top40/2025/week-1",
            "/top40/2025/week-3",
        ]
        editions = _editions(env)
        assert editions["top100"][-1] == ((2025,), 100)
        assert editions["top40"][-1] == ((2025, 3), 40)

        # Nothing new: nothing is fetched beyond the listings, nothing written.
        files = [env.data / "top100.json", env.data / "top40.json"]
        before = [(f.read_bytes(), f.stat().st_mtime_ns) for f in files]
        _routine_run(env)

        out = capsys.readouterr().out
        assert "top100: up to date (3 editions)" in out
        assert "top40: up to date (5 editions)" in out
        assert env.site.paths() == [INDEX, "/top40", "/top40/2025/week-1"]
        assert [(f.read_bytes(), f.stat().st_mtime_ns) for f in files] == before

    def test_missing_report_uses_each_editions_true_size(self, env, capsys):
        """Clause: the hitlist missing-report reflects each edition's true
        size instead of the highest position found in the library."""
        _initial_site(env)
        _routine_run(env)
        for p in (3, 7):
            _add_file_item(env, f"Pretend Artist {p}", f"Made Up Song {p}")
        _add_file_item(env, "Weekly Act 5", "Weekly Tune 5")
        env.plugin.generate(env.helper.lib, SimpleNamespace(), [])
        capsys.readouterr()

        report = SimpleNamespace(format=None, show_missing=True)
        env.plugin.show_hitlist(env.helper.lib, report, ["top100", "2024"])
        env.plugin.show_hitlist(env.helper.lib, report, ["top40", "2025", "1"])

        out = capsys.readouterr().out
        # The highest positions owned are 7 and 5; the editions run to 100 and 40.
        assert "Missing the following positions: 1-2 / 4-6 / 8-100" in out
        assert "Missing the following positions: 1-4 / 6-40" in out
        assert "highest found" not in out
