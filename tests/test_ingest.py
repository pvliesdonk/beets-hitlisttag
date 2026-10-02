"""Tests for the ingestor contract and discovery (beetsplug.hitlisttag.ingest)."""

from __future__ import annotations

import pytest

from beetsplug.hitlisttag.ingest import (
    AcquiredEdition,
    EditionRef,
    RawEntry,
    RawSong,
)


def _song(artist: str = "Fixture Artist", title: str = "Fixture Song") -> RawSong:
    return RawSong(artist, title)


def _entry(position: int, *songs: RawSong) -> RawEntry:
    return RawEntry(position, songs or (_song(),))


class TestRawSong:
    def test_holds_strings_as_published(self):
        song = RawSong(" Queen ", "Bohemian Rhapsody")
        assert song.artist == " Queen "  # no normalization here; that is lookup's job
        assert song.title == "Bohemian Rhapsody"

    @pytest.mark.parametrize("artist", ["", "   "])
    def test_rejects_blank_artist(self, artist):
        with pytest.raises(ValueError, match="artist"):
            RawSong(artist, "Title")

    @pytest.mark.parametrize("title", ["", "\t"])
    def test_rejects_blank_title(self, title):
        with pytest.raises(ValueError, match="title"):
            RawSong("Artist", title)

    def test_is_frozen(self):
        song = _song()
        with pytest.raises(AttributeError):
            song.artist = "Other"  # type: ignore[misc]


class TestRawEntry:
    def test_holds_position_and_songs(self):
        entry = RawEntry(3, (_song("A", "x"), _song("B", "y")))
        assert entry.position == 3
        assert [s.artist for s in entry.songs] == ["A", "B"]

    @pytest.mark.parametrize("position", [0, -1, True, "1"])
    def test_rejects_non_positive_or_non_int_position(self, position):
        with pytest.raises(ValueError, match="position"):
            RawEntry(position, (_song(),))

    def test_rejects_empty_songs(self):
        with pytest.raises(ValueError, match="songs"):
            RawEntry(1, ())


class TestEditionRef:
    def test_equality_and_hash_by_axes(self):
        a = EditionRef({"year": 2023})
        b = EditionRef({"year": 2023})
        c = EditionRef({"year": 2024})
        assert a == b and hash(a) == hash(b)
        assert a != c
        assert len({a, b, c}) == 2

    def test_two_axis_ref_order_independent(self):
        assert EditionRef({"year": 2024, "week": 7}) == EditionRef(
            {"week": 7, "year": 2024}
        )

    def test_copies_mapping(self):
        axes = {"year": 2023}
        ref = EditionRef(axes)
        axes["year"] = 1999
        assert ref.axes == {"year": 2023}

    def test_rejects_empty_axes(self):
        with pytest.raises(ValueError, match="axes"):
            EditionRef({})

    @pytest.mark.parametrize("value", [0, -5, True, "2023", 2023.0])
    def test_rejects_non_positive_int_values(self, value):
        with pytest.raises(ValueError, match="axis"):
            EditionRef({"year": value})

    def test_rejects_non_string_axis_name(self):
        with pytest.raises(ValueError, match="axis"):
            EditionRef({1: 2023})  # type: ignore[dict-item]


class TestAcquiredEdition:
    def test_holds_ref_size_entries(self):
        ref = EditionRef({"year": 2023})
        edition = AcquiredEdition(ref, 3, (_entry(1), _entry(3)))
        assert edition.ref == ref
        assert edition.size == 3
        assert [e.position for e in edition.entries] == [1, 3]

    def test_partial_edition_allowed(self):
        edition = AcquiredEdition(EditionRef({"year": 2023}), 2000, (_entry(7),))
        assert len(edition.entries) == 1

    @pytest.mark.parametrize("size", [0, -1, True, "3"])
    def test_rejects_non_positive_or_non_int_size(self, size):
        with pytest.raises(ValueError, match="size"):
            AcquiredEdition(EditionRef({"year": 2023}), size, (_entry(1),))

    def test_rejects_position_beyond_size(self):
        with pytest.raises(ValueError, match="not in 1..3"):
            AcquiredEdition(EditionRef({"year": 2023}), 3, (_entry(4),))

    def test_rejects_duplicate_position(self):
        with pytest.raises(ValueError, match="duplicate position 2"):
            AcquiredEdition(EditionRef({"year": 2023}), 3, (_entry(2), _entry(2)))

    def test_empty_entries_allowed(self):
        # The dataset permits an edition with no entries; so does the contract.
        edition = AcquiredEdition(EditionRef({"year": 2023}), 3, ())
        assert edition.entries == ()
