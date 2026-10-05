"""Tests for the song catalog (beetsplug.hitlisttag.catalog)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from beetsplug.hitlisttag.catalog import (
    Alias,
    Catalog,
    CatalogError,
    CatalogSong,
    Link,
    catalog_path,
    read_catalog,
)

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
