"""Tests for the song catalog (beetsplug.hitlisttag.catalog)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from beetsplug.hitlisttag.acquire import merge_acquired
from beetsplug.hitlisttag.catalog import (
    Alias,
    BindReport,
    Catalog,
    CatalogError,
    CatalogIndex,
    CatalogSong,
    CheckResult,
    ImplicitPair,
    Link,
    LinkState,
    bind_links,
    catalog_path,
    check_catalog,
    dump_catalog,
    read_catalog,
    stray_catalog_files,
    write_catalog_file,
)
from beetsplug.hitlisttag.dataset import (
    Edition,
    Entry,
    HitlistData,
    Song,
    read_dataset,
    write_dataset_file,
)
from beetsplug.hitlisttag.ingest import AcquiredEdition, EditionRef, RawEntry, RawSong
from beetsplug.hitlisttag.lookup import Placement

log = logging.getLogger("test.catalog")

SAMPLE = {
    "catalog": 1,
    "songs": {
        "1": {
            "artist": "Simon & Garfunkel",
            "title": "The Sound of Silence",
            "aliases": [
                {"artist": "Simon and Garfunkel", "title": "The Sounds of Silence"}
            ],
            "links": [
                {
                    "chart": "top40",
                    "song": "412",
                    "artist": "Simon & Garfunkel",
                    "title": "The Sounds Of Silence",
                    "source_ids": {"top40.nl/title": "9981"},
                },
                {
                    "chart": "top2000",
                    "song": "77",
                    "artist": "Simon & Garfunkel",
                    "title": "The Sound of Silence",
                },
            ],
        }
    },
}


def _write(tmp_path: Path, payload) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestModel:
    def test_alias_and_link_keys_use_match_key(self):
        assert Alias("The Bangles", "Eternal Flame").key() == (
            "bangles",
            "eternal flame",
        )
        link = Link("top40", "1", "Simon & Garfunkel", "Cecilia")
        assert link.key() == ("simon and garfunkel", "cecilia")

    def test_link_needs_chart_and_song(self):
        with pytest.raises(ValueError, match="non-empty 'chart' and 'song'"):
            Link("", "1", "A", "T")
        with pytest.raises(ValueError, match="non-empty 'chart' and 'song'"):
            Link("top40", " ", "A", "T")

    def test_song_needs_a_link(self):
        with pytest.raises(ValueError, match="at least one link"):
            CatalogSong("1", "A", "T")

    def test_song_rejects_the_same_raw_song_twice(self):
        link = Link("top40", "5", "A", "T")
        with pytest.raises(ValueError, match="links top40 '5' twice"):
            CatalogSong("1", "A", "T", links=[link, Link("top40", "5", "A", "T")])

    def test_catalog_rejects_one_alias_key_under_two_songs(self):
        a = CatalogSong(
            "1", "A", "T", [Alias("Bangles", "X")], [Link("c", "1", "A", "T")]
        )
        b = CatalogSong(
            "2", "B", "U", [Alias("The Bangles", "X")], [Link("c", "2", "B", "U")]
        )
        with pytest.raises(ValueError, match="belongs to songs '1' and '2'"):
            Catalog({"1": a, "2": b}, Path("mem"))

    def test_alias_owner(self):
        a = CatalogSong(
            "1", "A", "T", [Alias("Bangles", "X")], [Link("c", "1", "A", "T")]
        )
        catalog = Catalog({"1": a}, Path("mem"))
        assert catalog.alias_owner(("bangles", "x")) == "1"
        assert catalog.alias_owner(("bangles", "y")) is None

    def test_a_split_may_link_one_raw_song_from_two_songs(self):
        a = CatalogSong("1", "A", "T", links=[Link("c", "9", "A / B", "T ; U")])
        b = CatalogSong("2", "B", "U", links=[Link("c", "9", "A / B", "T ; U")])
        Catalog({"1": a, "2": b}, Path("mem"))  # no error


class TestRead:
    def test_missing_file_is_an_empty_catalog(self, tmp_path):
        catalog = read_catalog(tmp_path / "catalog.json", log)
        assert catalog.songs == {}
        assert catalog.source == tmp_path / "catalog.json"

    def test_catalog_path(self, tmp_path):
        assert catalog_path(tmp_path) == tmp_path / "catalog.json"
        assert catalog_path(str(tmp_path)) == tmp_path / "catalog.json"

    def test_reads_sample(self, tmp_path):
        catalog = read_catalog(_write(tmp_path, SAMPLE), log)
        song = catalog.songs["1"]
        assert (song.artist, song.title) == (
            "Simon & Garfunkel",
            "The Sound of Silence",
        )
        assert song.aliases == [Alias("Simon and Garfunkel", "The Sounds of Silence")]
        assert song.links[0] == Link(
            "top40",
            "412",
            "Simon & Garfunkel",
            "The Sounds Of Silence",
            {"top40.nl/title": "9981"},
        )
        assert song.links[1].source_ids == {}

    def test_dataset_file_at_the_reserved_path_is_refused(self, tmp_path):
        path = _write(tmp_path, {"chart": "catalog", "songs": {}, "editions": []})
        with pytest.raises(CatalogError, match="is a dataset file.*reserved"):
            read_catalog(path, log)

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ([], "top-level value must be an object"),
            ({"catalog": 2, "songs": {}}, "'catalog' must be 1"),
            ({"catalog": True, "songs": {}}, "'catalog' must be 1"),
            ({"catalog": 1}, "'songs' must be an object"),
            ({"catalog": 1, "songs": {}, "notes": 1}, "top level has unknown field"),
            ({"catalog": 1, "songs": {"1": []}}, "song '1' must be an object"),
            (
                {"catalog": 1, "songs": {"1": {"artist": "A", "links": []}}},
                "song '1' must have string 'artist' and 'title'",
            ),
            (
                {
                    "catalog": 1,
                    "songs": {"1": {"artist": "A", "title": "T", "links": [], "x": 1}},
                },
                "song '1' has unknown field",
            ),
            (
                {
                    "catalog": 1,
                    "songs": {"1": {"artist": "A", "title": "T", "links": []}},
                },
                "song '1' must have at least one link",
            ),
            (
                {
                    "catalog": 1,
                    "songs": {
                        "1": {
                            "artist": "A",
                            "title": "T",
                            "aliases": [{"artist": "A"}],
                            "links": [
                                {"chart": "c", "song": "1", "artist": "A", "title": "T"}
                            ],
                        }
                    },
                },
                "song '1' alias 0 must have string 'artist' and 'title'",
            ),
            (
                {
                    "catalog": 1,
                    "songs": {
                        "1": {
                            "artist": "A",
                            "title": "T",
                            "links": [{"chart": "c", "song": "1", "artist": "A"}],
                        }
                    },
                },
                (
                    "song '1' link 0 must have string 'chart', 'song', 'artist' "
                    "and 'title'"
                ),
            ),
            (
                {
                    "catalog": 1,
                    "songs": {
                        "1": {
                            "artist": "A",
                            "title": "T",
                            "links": [
                                {
                                    "chart": "c",
                                    "song": "1",
                                    "artist": "A",
                                    "title": "T",
                                    "source_ids": {"k": 1},
                                }
                            ],
                        }
                    },
                },
                "song '1' link 0 'source_ids' must map non-empty strings",
            ),
        ],
    )
    def test_malformed_content_names_the_file(self, tmp_path, payload, message):
        path = _write(tmp_path, payload)
        with pytest.raises(CatalogError, match=message) as info:
            read_catalog(path, log)
        assert str(path) in str(info.value)

    def test_alias_conflict_names_both_songs(self, tmp_path):
        payload = {
            "catalog": 1,
            "songs": {
                "1": {
                    "artist": "A",
                    "title": "T",
                    "aliases": [{"artist": "Bangles", "title": "X"}],
                    "links": [{"chart": "c", "song": "1", "artist": "A", "title": "T"}],
                },
                "2": {
                    "artist": "B",
                    "title": "U",
                    "aliases": [{"artist": "The Bangles", "title": "X"}],
                    "links": [{"chart": "c", "song": "2", "artist": "B", "title": "U"}],
                },
            },
        }
        with pytest.raises(CatalogError, match="belongs to songs '1' and '2'"):
            read_catalog(_write(tmp_path, payload), log)

    def test_unreadable_file_names_the_file(self, tmp_path):
        path = tmp_path / "catalog.json"
        path.write_bytes(b"\xff\xfe")
        with pytest.raises(CatalogError, match="cannot read catalog file"):
            read_catalog(path, log)


class TestWrite:
    def _catalog(self, tmp_path) -> Catalog:
        song = CatalogSong(
            "2",
            "Nena",
            "99 Luftballons",
            [Alias("Nena", "99 Red Balloons")],
            [Link("top40", "7", "Nena", "99 Luftballons", {"top40.nl/title": "900"})],
        )
        other = CatalogSong("10", "A", "T", links=[Link("top2000", "3", "A", "T")])
        return Catalog({"10": other, "2": song}, tmp_path / "catalog.json")

    def test_dump_orders_songs_by_id_and_omits_empty_source_ids(self, tmp_path):
        text = dump_catalog(self._catalog(tmp_path))
        payload = json.loads(text)
        assert list(payload["songs"]) == ["2", "10"]
        assert payload["catalog"] == 1
        assert "source_ids" not in payload["songs"]["10"]["links"][0]
        assert payload["songs"]["2"]["links"][0]["source_ids"] == {
            "top40.nl/title": "900"
        }
        assert text.endswith("\n")
        assert "Luftballons" in text  # ensure_ascii=False keeps text readable

    def test_write_round_trips_through_the_reader(self, tmp_path):
        catalog = self._catalog(tmp_path)
        write_catalog_file(catalog, catalog.source, log)
        again = read_catalog(catalog.source, log)
        assert again.songs == catalog.songs
        assert not list(tmp_path.glob("*.tmp"))

    def test_write_refuses_a_dataset_file_at_the_reserved_path(self, tmp_path):
        path = tmp_path / "catalog.json"
        before = json.dumps({"chart": "catalog", "songs": {}, "editions": []})
        path.write_text(before, encoding="utf-8")
        with pytest.raises(CatalogError, match="reserved"):
            write_catalog_file(self._catalog(tmp_path), path, log)
        assert path.read_text(encoding="utf-8") == before

    def test_failed_write_leaves_the_file_untouched(self, tmp_path, monkeypatch):
        catalog = self._catalog(tmp_path)
        write_catalog_file(catalog, catalog.source, log)
        before = catalog.source.read_bytes()

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            write_catalog_file(catalog, catalog.source, log)
        assert catalog.source.read_bytes() == before
        assert not list(tmp_path.glob("*.tmp"))


def _data(chart, songs, editions):
    return HitlistData(
        chart=chart,
        songs={s.id: s for s in songs},
        editions=editions,
        source=Path(chart),
    )


def _week(year, week, *entries):
    """entries: (position, [songs], source_ids)"""
    return Edition(
        {"year": year, "week": week},
        40,
        [Entry(p, songs, ids) for p, songs, ids in entries],
    )


class TestBind:
    def _catalog(self, *links: Link) -> Catalog:
        song = CatalogSong("1", "Scorpions", "Hello Josephine", links=list(links))
        return Catalog({"1": song}, Path("mem"))

    def test_bound_when_the_raw_id_is_cited(self):
        raw = Song("5", "The Scorpions ((GBR))", "Hello Josephine")
        data = _data(
            "top40", [raw], [_week(1965, 1, (1, [raw], {"top40.nl/title": "75"}))]
        )
        link = Link("top40", "5", "The Scorpions ((GBR))", "Hello Josephine")
        report = bind_links(self._catalog(link), [data], log)
        assert [s.state for s in report.all()] == ["bound"]
        assert not report.changed

    def test_cited_id_holding_another_song_is_rebound_not_kept(self):
        # From scratch, ids are re-minted in fetch order: the link's id now
        # cites a different song. Bound needs the recorded song, not just the
        # id, so this re-binds by source ids instead of keeping Nena.
        nena = Song("5", "Nena", "99 Luftballons")
        scorpions = Song("9", "Scorpions ((GBR))", "Hello Josephine")
        data = _data(
            "top40",
            [nena, scorpions],
            [
                _week(
                    1965,
                    1,
                    (1, [nena], {"top40.nl/title": "900"}),
                    (2, [scorpions], {"top40.nl/title": "75"}),
                )
            ],
        )
        link = Link(
            "top40",
            "5",
            "The Scorpions ((GBR))",
            "Hello Josephine",
            {"top40.nl/title": "75"},
        )
        report = bind_links(self._catalog(link), [data], log)
        (state,) = report.all()
        assert (state.state, state.previous, link.song) == ("rebound", "5", "9")

    def test_cited_id_with_another_name_and_no_ids_is_not_bound(self):
        other = Song("5", "Nena", "99 Luftballons (1983)")
        data = _data(
            "top2000", [other], [Edition({"year": 2023}, 2000, [Entry(1, [other])])]
        )
        link = Link("top2000", "5", "Nena", "99 Luftballons")
        report = bind_links(self._catalog(link), [data], log)
        assert [s.state for s in report.all()] == ["dangling"]

    def test_bound_when_the_name_changed_but_ids_agree(self):
        live = Song("5", "Scorpions ((GBR))", "Hello Josephine")
        data = _data(
            "top40", [live], [_week(1965, 1, (1, [live], {"top40.nl/title": "75"}))]
        )
        link = Link(
            "top40",
            "5",
            "The Scorpions ((GBR))",
            "Hello Josephine",
            {"top40.nl/title": "75"},
        )
        report = bind_links(self._catalog(link), [data], log)
        assert [s.state for s in report.all()] == ["bound"]

    def test_orphaned_id_is_not_bound(self):
        # The id exists in the songs table but no edition cites it any more.
        old = Song("5", "The Scorpions ((GBR))", "Hello Josephine")
        new = Song("9", "Scorpions ((GBR))", "Hello Josephine")
        data = _data("top40", [old, new], [_week(1965, 1, (1, [new], {}))])
        link = Link("top40", "5", "Old Name", "Gone")
        report = bind_links(self._catalog(link), [data], log)
        assert [s.state for s in report.all()] == ["dangling"]

    def test_rebound_by_source_ids(self, caplog):
        new = Song("9", "Scorpions ((GBR))", "Hello Josephine")
        data = _data(
            "top40", [new], [_week(1965, 1, (1, [new], {"top40.nl/title": "75"}))]
        )
        link = Link(
            "top40",
            "5",
            "The Scorpions ((GBR))",
            "Hello Josephine",
            {"top40.nl/title": "75"},
        )
        with caplog.at_level(logging.INFO):
            report = bind_links(self._catalog(link), [data], log)
        (state,) = report.all()
        assert (state.state, state.previous) == ("rebound", "5")
        assert link.song == "9"
        assert (link.artist, link.title) == ("Scorpions ((GBR))", "Hello Josephine")
        assert link.source_ids == {"top40.nl/title": "75"}
        assert report.changed
        assert "re-bound" in caplog.text

    def test_rebound_by_name_when_no_ids_recorded(self):
        new = Song("9", "The Scorpions", "Hello Josephine")
        data = _data(
            "top2000", [new], [Edition({"year": 2023}, 2000, [Entry(1, [new])])]
        )
        link = Link("top2000", "5", "Scorpions", "Hello Josephine")
        report = bind_links(self._catalog(link), [data], log)
        assert [s.state for s in report.all()] == ["rebound"]
        assert link.song == "9"

    def test_rebound_by_name_when_ids_match_nothing(self):
        new = Song("9", "Scorpions", "Hello Josephine")
        data = _data(
            "top40", [new], [_week(1965, 1, (1, [new], {"top40.nl/title": "1"}))]
        )
        link = Link(
            "top40", "5", "Scorpions", "Hello Josephine", {"top40.nl/title": "75"}
        )
        report = bind_links(self._catalog(link), [data], log)
        assert [s.state for s in report.all()] == ["rebound"]

    def test_two_candidates_by_ids_is_dangling(self):
        # Review focus 2: the site's id groups a versions bundle.
        a = Song("1", "Orkest Gudrun Jankis / Stig Rauno", "Let Kiss / Letkis")
        b = Song("2", "Orkest Gudrun Jankis", "Let Kiss")
        data = _data(
            "top40",
            [a, b],
            [
                _week(1965, 1, (1, [a], {"top40.nl/title": "4493"})),
                _week(1965, 2, (1, [b], {"top40.nl/title": "4493"})),
            ],
        )
        link = Link("top40", "7", "Stig Rauno", "Letkis", {"top40.nl/title": "4493"})
        report = bind_links(self._catalog(link), [data], log)
        assert [s.state for s in report.all()] == ["dangling"]
        assert link.song == "7"  # untouched

    def test_two_candidates_by_name_is_dangling(self):
        a = Song("1", "Bangles", "Eternal Flame")
        b = Song("2", "The Bangles", "Eternal Flame")
        data = _data(
            "top40",
            [a, b],
            [_week(1989, 1, (1, [a], {})), _week(1989, 2, (1, [b], {}))],
        )
        link = Link("top40", "7", "The Bangles", "Eternal Flame")
        report = bind_links(self._catalog(link), [data], log)
        assert [s.state for s in report.all()] == ["dangling"]

    def test_chart_not_in_datasets_is_dangling(self):
        # Review focus 3: the user removed the chart from hitlists.
        link = Link("kerst", "7", "Wham!", "Last Christmas")
        report = bind_links(self._catalog(link), [], log)
        assert [s.state for s in report.all()] == ["dangling"]

    def test_differing_ids_across_entries_record_none(self):
        # A raw song whose entries disagree on every id key has no usable
        # ids, so a recorded id matches nothing and the name step decides.
        new = Song("9", "Scorpions", "Hello Josephine")
        data = _data(
            "top40",
            [new],
            [
                _week(1965, 1, (1, [new], {"top40.nl/title": "75"})),
                _week(1965, 2, (1, [new], {"top40.nl/title": "76"})),
            ],
        )
        link = Link(
            "top40", "5", "Scorpions", "Hello Josephine", {"top40.nl/title": "75"}
        )
        report = bind_links(self._catalog(link), [data], log)
        (state,) = report.all()
        assert state.state == "rebound"
        assert link.source_ids == {}

    def test_ids_agreeing_on_one_key_keep_that_key(self):
        # Entries differing only on the subtitle id still agree on the title
        # id, which is what a link records and what binds it.
        raw = Song("75", "The Scorpions ((GBR))", "Hello Josephine")
        data = _data(
            "top40",
            [raw],
            [
                _week(
                    1965,
                    1,
                    (
                        1,
                        [raw],
                        {"top40.nl/title": "3065", "top40.nl/subtitle": "38149"},
                    ),
                ),
                _week(
                    1965,
                    2,
                    (
                        1,
                        [raw],
                        {"top40.nl/title": "3065", "top40.nl/subtitle": "38150"},
                    ),
                ),
            ],
        )
        bound = Link(
            "top40",
            "75",
            "The Scorpions ((GBR))",
            "Hello Josephine",
            {"top40.nl/title": "3065"},
        )
        report = bind_links(self._catalog(bound), [data], log)
        (state,) = report.all()
        assert state.state == "bound"
        assert bound.source_ids == {"top40.nl/title": "3065"}
        stale = Link(
            "top40",
            "5",
            "The Scorpions ((GBR))",
            "Hello Josephine",
            {"top40.nl/title": "3065"},
        )
        report = bind_links(self._catalog(stale), [data], log)
        (state,) = report.all()
        assert state.state == "rebound"
        assert stale.song == "75"
        assert stale.source_ids == {"top40.nl/title": "3065"}

    def test_report_groups_states_by_song(self):
        raw = Song("5", "A", "T")
        data = _data("top40", [raw], [_week(1965, 1, (1, [raw], {}))])
        song = CatalogSong(
            "1",
            "A",
            "T",
            links=[Link("top40", "5", "A", "T"), Link("top40", "6", "A", "U")],
        )
        report = bind_links(Catalog({"1": song}, Path("mem")), [data], log)
        assert [s.state for s in report.states["1"]] == ["bound", "dangling"]
        assert len(report.with_state("dangling")) == 1
        assert isinstance(report, BindReport)
        assert isinstance(report.states["1"][0], LinkState)


class TestResolve:
    def _datasets(self):
        s40 = Song("1", "The Scorpions ((GBR))", "Hello Josephine")
        n40 = Song("2", "Nena", "99 Luftballons")
        split = Song("3", "The Beatles", "Strawberry Fields Forever ; Penny Lane")
        top40 = _data(
            "top40",
            [s40, n40, split],
            [
                _week(1965, 1, (1, [s40], {"top40.nl/title": "75"}), (2, [n40], {})),
                _week(1967, 9, (3, [split], {})),
            ],
        )
        s2000 = Song("1", "Scorpions", "Hello Josephine")
        n2000 = Song("2", "Nena", "99 Luftballons")
        top2000 = _data(
            "top2000",
            [s2000, n2000],
            [Edition({"year": 2023}, 2000, [Entry(10, [s2000]), Entry(20, [n2000])])],
        )
        return [top40, top2000]

    def _index(self, catalog):
        return CatalogIndex.from_datasets(self._datasets(), catalog, log)

    def test_explicit_alias_gives_the_union_across_charts(self):
        song = CatalogSong(
            "1",
            "Scorpions",
            "Hello Josephine",
            [Alias("Scorpions (UK)", "Hello Josephine")],
            [
                Link("top40", "1", "The Scorpions ((GBR))", "Hello Josephine"),
                Link("top2000", "1", "Scorpions", "Hello Josephine"),
            ],
        )
        index = self._index(Catalog({"1": song}, Path("mem")))
        result = index.lookup("Scorpions (UK)", "Hello Josephine")
        assert result.placements == {
            "top40": [Placement({"year": 1965, "week": 1}, 1, 40)],
            "top2000": [Placement({"year": 2023}, 10, 2000)],
        }
        assert result.unbound_charts == set()
        assert result.normalized == ("scorpions uk", "hello josephine")

    def test_implicit_alias_from_a_singly_linked_raw_name(self):
        song = CatalogSong(
            "1",
            "Scorpions",
            "Hello Josephine",
            links=[
                Link("top40", "1", "The Scorpions ((GBR))", "Hello Josephine"),
                Link("top2000", "1", "Scorpions", "Hello Josephine"),
            ],
        )
        index = self._index(Catalog({"1": song}, Path("mem")))
        # Spelled like the Top 2000's raw name; gets the Top 40 history too.
        result = index.lookup("Scorpions", "Hello Josephine")
        assert set(result.placements) == {"top40", "top2000"}

    def test_split_shares_placements_between_its_songs(self):
        raw = Link(
            "top40", "3", "The Beatles", "Strawberry Fields Forever ; Penny Lane"
        )
        a = CatalogSong(
            "1",
            "The Beatles",
            "Strawberry Fields Forever",
            [Alias("The Beatles", "Strawberry Fields Forever")],
            [raw],
        )
        b = CatalogSong(
            "2",
            "The Beatles",
            "Penny Lane",
            [Alias("The Beatles", "Penny Lane")],
            [
                Link(
                    "top40",
                    "3",
                    "The Beatles",
                    "Strawberry Fields Forever ; Penny Lane",
                )
            ],
        )
        index = self._index(Catalog({"1": a, "2": b}, Path("mem")))
        expected = {"top40": [Placement({"year": 1967, "week": 9}, 3, 40)]}
        assert index.lookup("Beatles", "Penny Lane").placements == expected
        assert (
            index.lookup("The Beatles", "Strawberry Fields Forever").placements
            == expected
        )
        # The raw name links two songs: it implies neither; raw lookup finds it.
        result = index.lookup("The Beatles", "Strawberry Fields Forever ; Penny Lane")
        assert result.placements == expected
        assert index.implicit_pairs == []

    def test_explicit_alias_wins_over_implicit(self):
        a = CatalogSong(
            "1",
            "Nena",
            "99 Luftballons",
            links=[Link("top40", "2", "Nena", "99 Luftballons")],
        )
        b = CatalogSong(
            "2",
            "Nena",
            "99 Red Balloons",
            [Alias("Nena", "99 Luftballons")],
            [Link("top2000", "2", "Nena", "99 Luftballons")],
        )
        index = self._index(Catalog({"1": a, "2": b}, Path("mem")))
        assert set(index.lookup("Nena", "99 Luftballons").placements) == {"top2000"}

    def test_implicit_pair_cancels_to_raw_lookup_and_is_reported(self):
        a = CatalogSong(
            "1",
            "Nena",
            "99 Luftballons",
            links=[Link("top40", "2", "Nena", "99 Luftballons")],
        )
        b = CatalogSong(
            "2",
            "Nena",
            "99 Luftballons",
            links=[Link("top2000", "2", "Nena", "99 Luftballons")],
        )
        index = self._index(Catalog({"1": a, "2": b}, Path("mem")))
        result = index.lookup("Nena", "99 Luftballons")
        assert set(result.placements) == {"top40", "top2000"}  # raw lookup, as today
        assert index.implicit_pairs == [
            ImplicitPair(
                ("nena", "99 luftballons"),
                [("top2000", "2"), ("top40", "2")],  # lexical: "top2" < "top4"
                ["1", "2"],
                "Nena",
                "99 Luftballons",
            )
        ]

    def test_implicit_pairs_do_not_depend_on_catalog_order(self):
        n40 = Song("2", "The Nena", "99 Luftballons")
        a40 = Song("5", "The Abba", "Waterloo")
        top40 = _data(
            "top40",
            [n40, a40],
            [_week(1984, 1, (1, [n40], {}), (2, [a40], {}))],
        )
        n2000 = Song("2", "Nena", "99 Luftballons")
        a2000 = Song("5", "Abba", "Waterloo")
        top2000 = _data(
            "top2000",
            [n2000, a2000],
            [Edition({"year": 2023}, 2000, [Entry(1, [n2000]), Entry(2, [a2000])])],
        )
        songs = {
            "1": CatalogSong(
                "1",
                "Nena",
                "99 Luftballons",
                links=[Link("top40", "2", "The Nena", "99 Luftballons")],
            ),
            "2": CatalogSong(
                "2",
                "Nena",
                "99 Luftballons",
                links=[Link("top2000", "2", "Nena", "99 Luftballons")],
            ),
            "3": CatalogSong(
                "3",
                "Abba",
                "Waterloo",
                links=[Link("top40", "5", "The Abba", "Waterloo")],
            ),
            "4": CatalogSong(
                "4",
                "Abba",
                "Waterloo",
                links=[Link("top2000", "5", "Abba", "Waterloo")],
            ),
        }
        index = CatalogIndex.from_datasets(
            [top40, top2000], Catalog(songs, Path("mem")), log
        )
        # Sorted by key (not catalog order); the name comes from the first
        # sorted raw (top2000 before top40), not the first-linked one.
        assert index.implicit_pairs == [
            ImplicitPair(
                ("abba", "waterloo"),
                [("top2000", "5"), ("top40", "5")],
                ["3", "4"],
                "Abba",
                "Waterloo",
            ),
            ImplicitPair(
                ("nena", "99 luftballons"),
                [("top2000", "2"), ("top40", "2")],
                ["1", "2"],
                "Nena",
                "99 Luftballons",
            ),
        ]

    def test_falls_back_to_raw_lookup(self):
        index = self._index(Catalog({}, Path("mem")))
        result = index.lookup("Nena", "99 Luftballons")
        assert set(result.placements) == {"top40", "top2000"}
        assert index.lookup("Nobody", "Nothing").is_miss

    def test_unbound_charts_name_dangling_links(self):
        song = CatalogSong(
            "1",
            "Nena",
            "99 Luftballons",
            [Alias("Nena", "99 Red Balloons")],
            [
                Link("top40", "2", "Nena", "99 Luftballons"),
                Link("top2000", "77", "Nena", "99 Luftballons (1983)"),
            ],
        )
        index = self._index(Catalog({"1": song}, Path("mem")))
        result = index.lookup("Nena", "99 Red Balloons")
        assert set(result.placements) == {"top40"}
        assert result.unbound_charts == {"top2000"}

    def test_song_with_only_dangling_links(self):
        # Review focus 5.
        song = CatalogSong(
            "1",
            "Nena",
            "Irgendwie",
            [Alias("Nena", "Irgendwie")],
            [Link("top40", "99", "Nena", "Irgendwie")],
        )
        index = self._index(Catalog({"1": song}, Path("mem")))
        result = index.lookup("Nena", "Irgendwie")
        assert result.placements == {}
        assert result.unbound_charts == {"top40"}

    def test_alias_that_normalizes_to_nothing_never_matches(self):
        # Review focus 4.
        song = CatalogSong(
            "1",
            "Nena",
            "?",
            [Alias("Nena", "?")],
            [Link("top40", "2", "Nena", "99 Luftballons")],
        )
        index = self._index(Catalog({"1": song}, Path("mem")))
        assert index.lookup("Nena", "?").unnormalizable

    def test_bind_report_is_exposed(self):
        index = self._index(Catalog({}, Path("mem")))
        assert index.bind_report.all() == []


HITLISTS = {"top40": ["year", "week"], "top2000": ["year"]}


def _acquired(axes, *entries):
    """entries: (artist, title, source_ids)."""
    raw = tuple(
        RawEntry(i + 1, (RawSong(a, t),), ids) for i, (a, t, ids) in enumerate(entries)
    )
    return AcquiredEdition(EditionRef(axes), len(raw), raw)


def _acquire(data_dir, existing, chart, editions):
    data, _ = merge_acquired(
        existing,
        chart,
        HITLISTS[chart],
        editions,
        data_dir / f"{chart}.json",
        log,
        replace=existing is not None,
    )
    write_dataset_file(data, data_dir / f"{chart}.json", HITLISTS, log)


def _history(index):
    scorpions = index.lookup("Scorpions", "Hello Josephine")
    nena = index.lookup("Nena", "99 Luftballons")
    return scorpions, nena


class TestDurability:
    """The criterion's third clause: hand corrections survive a full
    re-acquisition, both --force over the file and from scratch."""

    V1_TOP40 = [
        _acquired(
            {"year": 1965, "week": 1},
            ("The Scorpions ((GBR))", "Hello Josephine", {"top40.nl/title": "75"}),
            ("Nena", "99 Luftballons", {"top40.nl/title": "900"}),
        )
    ]
    V1_TOP2000 = [
        _acquired(
            {"year": 2023},
            ("Scorpions", "Hello Josephine", {}),
            ("Nena", "99 Luftballons", {}),
        )
    ]
    # The source respelled every song: the Top 40 keeps its ids (re-bind by
    # source id); the Top 2000 has none, so Scorpions re-binds by name and
    # Nena's new title is dangling by design.
    V2_TOP40 = [
        _acquired(
            {"year": 1965, "week": 1},
            (
                "Scorpions ((GBR)) & The Hurricanes",
                "Hello Josephine",
                {"top40.nl/title": "75"},
            ),
            ("Nena", "99 Luftballons", {"top40.nl/title": "900"}),
        )
    ]
    V2_TOP2000 = [
        _acquired(
            {"year": 2023},
            ("The Scorpions", "Hello Josephine", {}),
            ("Nena", "99 Luftballons (1983)", {}),
        )
    ]

    def _seed(self, tmp_path):
        data_dir = tmp_path / "data"
        _acquire(data_dir, None, "top40", self.V1_TOP40)
        _acquire(data_dir, None, "top2000", self.V1_TOP2000)
        datasets = {d.chart: d for d in read_dataset(data_dir, HITLISTS, log)}
        catalog = Catalog(
            {
                "1": CatalogSong(
                    "1",
                    "Scorpions",
                    "Hello Josephine",
                    [Alias("Scorpions", "Hello Josephine")],
                    [
                        Link(
                            "top40",
                            "1",
                            "The Scorpions ((GBR))",
                            "Hello Josephine",
                            {"top40.nl/title": "75"},
                        ),
                        Link("top2000", "1", "Scorpions", "Hello Josephine"),
                    ],
                ),
                "2": CatalogSong(
                    "2",
                    "Nena",
                    "99 Luftballons",
                    [Alias("Nena", "99 Luftballons")],
                    [
                        Link(
                            "top40",
                            "2",
                            "Nena",
                            "99 Luftballons",
                            {"top40.nl/title": "900"},
                        ),
                        Link("top2000", "2", "Nena", "99 Luftballons"),
                    ],
                ),
            },
            catalog_path(data_dir),
        )
        write_catalog_file(catalog, catalog.source, log)
        return data_dir, datasets

    def _assert_v1(self, index):
        scorpions, nena = _history(index)
        assert scorpions.placements == {
            "top40": [Placement({"year": 1965, "week": 1}, 1, 2)],
            "top2000": [Placement({"year": 2023}, 1, 2)],
        }
        assert nena.placements == {
            "top40": [Placement({"year": 1965, "week": 1}, 2, 2)],
            "top2000": [Placement({"year": 2023}, 2, 2)],
        }

    def _assert_v2(self, index):
        scorpions, nena = _history(index)
        assert scorpions.placements == {
            "top40": [Placement({"year": 1965, "week": 1}, 1, 2)],
            "top2000": [Placement({"year": 2023}, 1, 2)],
        }
        assert scorpions.unbound_charts == set()
        assert nena.placements == {
            "top40": [Placement({"year": 1965, "week": 1}, 2, 2)]
        }
        assert nena.unbound_charts == {"top2000"}

    def test_before_any_reacquisition(self, tmp_path):
        data_dir, datasets = self._seed(tmp_path)
        catalog = read_catalog(catalog_path(data_dir), log)
        self._assert_v1(
            CatalogIndex.from_datasets(list(datasets.values()), catalog, log)
        )

    def test_survives_force_reacquisition(self, tmp_path):
        data_dir, datasets = self._seed(tmp_path)
        _acquire(data_dir, datasets["top40"], "top40", self.V2_TOP40)
        _acquire(data_dir, datasets["top2000"], "top2000", self.V2_TOP2000)
        live = read_dataset(data_dir, HITLISTS, log)
        # --force keeps the songs table: the respelled songs got new ids 3.
        assert {d.chart: sorted(d.songs) for d in live} == {
            "top40": ["1", "2", "3"],
            "top2000": ["1", "2", "3", "4"],
        }
        catalog = read_catalog(catalog_path(data_dir), log)
        index = CatalogIndex.from_datasets(live, catalog, log)
        self._assert_v2(index)
        states = {(s.link.chart, s.state) for s in index.bind_report.all()}
        assert states == {
            ("top40", "rebound"),
            ("top40", "bound"),
            ("top2000", "rebound"),
            ("top2000", "dangling"),
        }
        # Verify the Top 40 Scorpions link re-bound by source_id: its
        # artist changed to a different match_key, so only the carried id can
        # re-bind it.
        (scorpions_top40,) = [
            s
            for s in index.bind_report.all()
            if s.link.chart == "top40" and s.state == "rebound"
        ]
        assert scorpions_top40.previous == "1"
        assert scorpions_top40.link.song == "3"
        assert scorpions_top40.link.source_ids == {"top40.nl/title": "75"}
        assert scorpions_top40.link.artist == "Scorpions ((GBR)) & The Hurricanes"

    def test_survives_from_scratch_reacquisition(self, tmp_path):
        data_dir, _ = self._seed(tmp_path)
        for name in ("top40.json", "top2000.json"):
            (data_dir / name).unlink()
        _acquire(data_dir, None, "top40", self.V2_TOP40)
        _acquire(data_dir, None, "top2000", self.V2_TOP2000)
        live = read_dataset(data_dir, HITLISTS, log)
        assert {d.chart: sorted(d.songs) for d in live} == {
            "top40": ["1", "2"],
            "top2000": ["1", "2"],
        }
        catalog = read_catalog(catalog_path(data_dir), log)
        index = CatalogIndex.from_datasets(live, catalog, log)
        self._assert_v2(index)
        states = {(s.link.chart, s.state) for s in index.bind_report.all()}
        assert states == {
            ("top40", "bound"),
            ("top2000", "bound"),
            ("top2000", "dangling"),
        }


class TestCheck:
    def test_report_lines_counts_rebound_dangling_and_pairs(self, tmp_path):
        s40 = Song("9", "Scorpions ((GBR))", "Hello Josephine")
        b40 = Song("5", "Bangles", "Eternal Flame")
        top40 = _data(
            "top40",
            [s40, b40],
            [_week(1965, 1, (1, [s40], {"top40.nl/title": "75"}), (2, [b40], {}))],
        )
        b100 = Song("3", "The Bangles", "Eternal Flame")
        top100 = _data(
            "top100", [b100], [Edition({"year": 1989}, 100, [Entry(4, [b100])])]
        )
        catalog = Catalog(
            {
                "1": CatalogSong(
                    "1",
                    "Scorpions",
                    "Hello Josephine",
                    [Alias("Scorpions", "Hello Josephine")],
                    [
                        Link(
                            "top40",
                            "1",
                            "The Scorpions ((GBR))",
                            "Hello Josephine",
                            {"top40.nl/title": "75"},
                        ),
                        Link("top2000", "77", "Scorpions", "Hello Josephine"),
                    ],
                ),
                "2": CatalogSong(
                    "2",
                    "Bangles",
                    "Eternal Flame",
                    links=[Link("top40", "5", "Bangles", "Eternal Flame")],
                ),
                "3": CatalogSong(
                    "3",
                    "The Bangles",
                    "Eternal Flame",
                    links=[Link("top100", "3", "The Bangles", "Eternal Flame")],
                ),
            },
            tmp_path / "catalog.json",
        )
        index = CatalogIndex.from_datasets([top40, top100], catalog, log)
        result = check_catalog(index, catalog)
        assert result.lines == [
            "catalog: 3 songs, 1 alias, 4 links",
            "links: 2 bound, 1 re-bound, 1 dangling",
            "re-bound: song 1 (Scorpions - Hello Josephine) top40 1 -> 9",
            "dangling: song 1 (Scorpions - Hello Josephine) top2000 77 "
            "(recorded: Scorpions - Hello Josephine)",
            "implicit alias pair: top100 3 and top40 5 (The Bangles - Eternal Flame) "
            "link different songs",
        ]
        assert result.problems == 2
        assert result.changed

    def test_clean_report(self, tmp_path):
        raw = Song("1", "A", "T")
        data = _data("top40", [raw], [_week(1965, 1, (1, [raw], {}))])
        catalog = Catalog(
            {"1": CatalogSong("1", "A", "T", links=[Link("top40", "1", "A", "T")])},
            tmp_path / "catalog.json",
        )
        result = check_catalog(
            CatalogIndex.from_datasets([data], catalog, log), catalog
        )
        assert result == CheckResult(
            [
                "catalog: 1 song, 0 aliases, 1 link",
                "links: 1 bound, 0 re-bound, 0 dangling",
            ],
            0,
            False,
        )

    def test_stray_catalog_files(self, tmp_path):
        (tmp_path / "catalog.json").write_text(
            '{"catalog": 1, "songs": {}}', encoding="utf-8"
        )
        sub = tmp_path / "old"
        sub.mkdir()
        (sub / "catalog.json").write_text(
            '{"catalog": 1, "songs": {}}', encoding="utf-8"
        )
        (sub / "top40.json").write_text('{"chart": "top40"}', encoding="utf-8")
        (sub / "broken.json").write_text("{", encoding="utf-8")
        assert stray_catalog_files(tmp_path, log) == [sub / "catalog.json"]
