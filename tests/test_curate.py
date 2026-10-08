"""Tests for the curation operations (beetsplug.hitlisttag.curate, #162)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from beetsplug.hitlisttag.catalog import (
    Alias,
    Catalog,
    CatalogIndex,
    live_charts,
)
from beetsplug.hitlisttag.curate import (
    CurationError,
    alias,
    drop,
    merge,
    parse_name,
    unalias,
    unlink,
)
from beetsplug.hitlisttag.dataset import Edition, Entry, HitlistData, Song
from beetsplug.hitlisttag.lookup import Placement

log = logging.getLogger("test.curate")


def _data(chart, songs, editions):
    return HitlistData(chart, {s.id: s for s in songs}, editions, Path(chart))


def _week(year, week, *entries):
    """entries: (position, [songs])"""
    return Edition(
        {"year": year, "week": week}, 40, [Entry(p, s, {}) for p, s in entries]
    )


# Made-up names. Top 40: one song under two spellings (1, 2); its remix
# sharing a position with it, as the site publishes a same-artist variant
# (3, never split); a song charting only inside a bundle (4); a double
# A-side split at acquisition (5, 6 cite one entry); a live version (7).
R1 = Song("1", "Pretend Act", "Made Up Tune")
R2 = Song("2", "Pretend Act", "Made-Up Tune")
R3 = Song("3", "Other Act", "Cheer / Cheer - Remix")
R4 = Song("4", "Third Act", "Bundle Tune / Bundle Tune - Edit")
R5 = Song("5", "Duo Act", "Side A")
R6 = Song("6", "Duo Act", "Side B")
R7 = Song("7", "Pretend Act", "Made Up Tune (Live)")
R8 = Song("8", "Other Act", "Cheer")
TOP40 = _data(
    "top40",
    [R1, R2, R3, R4, R5, R6, R7, R8],
    [
        _week(1970, 1, (1, [R1]), (2, [R3]), (3, [R5, R6]), (4, [R7]), (5, [R4])),
        _week(1970, 2, (1, [R2]), (2, [R8])),
    ],
)
# Top 2000 spells the first song a third way.
T1 = Song("1", "Pretend Act", "Made Up Tune (Remaster)")
TOP2000 = _data("top2000", [T1], [Edition({"year": 2000}, 2000, [Entry(10, [T1], {})])])
DATASETS = [TOP40, TOP2000]
LIVE = live_charts(DATASETS)
EMPTY = Catalog({}, Path("mem"))


def _lookup(catalog, artist, title):
    return CatalogIndex.from_datasets(DATASETS, catalog, log).lookup(artist, title)


def _p(year, week, pos):
    return Placement({"year": year, "week": week}, pos, 40)


class TestParseName:
    def test_splits_at_the_first_dash(self):
        assert parse_name("Other Act - Cheer - Remix") == ("Other Act", "Cheer - Remix")

    @pytest.mark.parametrize("bad", ["No Dash", " - Title", "Artist - ", ""])
    def test_needs_both_sides(self, bad):
        with pytest.raises(CurationError, match="Artist - Title"):
            parse_name(bad)


class TestMerge:
    def test_two_raw_songs_make_a_new_song(self):
        change = merge(EMPTY, LIVE, [("top40", "1"), ("top40", "2")], [])
        assert change.changed
        song = change.catalog.songs["1"]
        assert (song.artist, song.title) == ("Pretend Act", "Made Up Tune")
        assert [(ln.chart, ln.song) for ln in song.links] == [
            ("top40", "1"),
            ("top40", "2"),
        ]
        assert change.lines[0] == "merge into new song @1 (Pretend Act - Made Up Tune):"
        assert change.catalog.next_id == 2

    def test_into_an_existing_song(self):
        first = merge(EMPTY, LIVE, [("top40", "1"), ("top40", "2")], []).catalog
        change = merge(first, LIVE, [("top2000", "1")], ["1"])
        assert [(ln.chart, ln.song) for ln in change.catalog.songs["1"].links] == [
            ("top40", "1"),
            ("top40", "2"),
            ("top2000", "1"),
        ]
        assert change.lines[0] == "merge into @1 (Pretend Act - Made Up Tune):"

    def _two_songs(self):
        """@1 links top40:1 (alias Pretend Act - Made Up Tune); @2 links
        top40:2 (alias Someone - Else). Made-Up Tune shares @1's match key,
        so @2 gets its own spelling through alias."""
        one = merge(
            EMPTY, LIVE, [("top40", "1")], [], ("Pretend Act", "Made Up Tune")
        ).catalog
        return alias(one, LIVE, ("Someone", "Else"), raw=("top40", "2")).catalog

    def test_a_raw_song_another_song_links_moves_and_an_emptied_song_drops(self):
        change = merge(self._two_songs(), LIVE, [("top40", "2")], ["1"])
        assert set(change.catalog.songs) == {"1"}
        assert any("moves from @2; @2 is dropped" in line for line in change.lines)

    def test_a_moved_link_leaves_the_holders_other_links(self):
        # @2 links top40:2 and top40:7; taking top40:2 leaves it top40:7.
        one = merge(
            EMPTY, LIVE, [("top40", "1")], [], ("Pretend Act", "Made Up Tune")
        ).catalog
        two = merge(one, LIVE, [("top40", "2"), ("top40", "7")], []).catalog
        change = merge(two, LIVE, [("top40", "2")], ["1"])
        assert [(ln.chart, ln.song) for ln in change.catalog.songs["2"].links] == [
            ("top40", "7")
        ]
        assert any(line.endswith("(moves from @2)") for line in change.lines)

    def test_absorbing_a_catalog_song_moves_its_links_and_aliases(self):
        change = merge(self._two_songs(), LIVE, [], ["1", "2"])
        assert set(change.catalog.songs) == {"1"}
        song = change.catalog.songs["1"]
        assert [(ln.chart, ln.song) for ln in song.links] == [
            ("top40", "1"),
            ("top40", "2"),
        ]
        assert [a.key() for a in song.aliases] == [
            ("pretend act", "made up tune"),
            ("someone", "else"),
        ]
        assert change.catalog.next_id == 3  # @2's id is not freed

    def test_a_raw_song_already_linked_is_a_no_op_line(self):
        first = merge(EMPTY, LIVE, [("top40", "1"), ("top40", "2")], []).catalog
        change = merge(
            first, LIVE, [("top40", "1")], ["1"], ("Pretend Act", "Made Up Tune")
        )
        assert any("top40:1 already linked" in line for line in change.lines)

    def test_name_renames_and_adds_the_alias(self):
        first = merge(EMPTY, LIVE, [("top40", "1"), ("top40", "2")], []).catalog
        change = merge(first, LIVE, [], ["1"], ("Pretend Act", "Made Up Tune!"))
        song = change.catalog.songs["1"]
        assert (song.artist, song.title) == ("Pretend Act", "Made Up Tune!")
        assert Alias("Pretend Act", "Made Up Tune!").key() in {
            a.key() for a in song.aliases
        }

    def test_needs_two_references_or_one_and_a_name(self):
        with pytest.raises(CurationError, match="two references"):
            merge(EMPTY, LIVE, [("top40", "1")], [])

    def test_a_raw_song_not_in_the_data_is_refused(self):
        for raw in (("top40", "99"), ("top100", "1")):
            with pytest.raises(CurationError, match="not in the current data"):
                merge(EMPTY, LIVE, [raw, ("top40", "1")], [])

    def test_an_unknown_catalog_song_is_refused(self):
        with pytest.raises(CurationError, match="no catalog song @9"):
            merge(EMPTY, LIVE, [("top40", "1")], ["9"])

    def test_a_broken_rule_is_refused_and_changes_nothing(self):
        one = merge(
            EMPTY, LIVE, [("top40", "1")], [], ("Pretend Act", "Made Up Tune")
        ).catalog
        with pytest.raises(CurationError, match="belongs to songs"):
            merge(one, LIVE, [("top40", "7")], [], ("Pretend Act", "Made Up Tune"))
        assert set(one.songs) == {"1"}


class TestAlias:
    def test_on_a_catalog_song(self):
        first = merge(EMPTY, LIVE, [("top40", "1"), ("top40", "2")], []).catalog
        change = alias(first, LIVE, ("Pretend Act", "The Made Up Tune"), song="1")
        assert change.changed
        assert change.catalog.alias_owner(("pretend act", "made up tune")) == "1"

    def test_on_an_unlinked_raw_song_mints_a_song(self):
        change = alias(EMPTY, LIVE, ("Other Act", "Cheer"), raw=("top40", "3"))
        song = change.catalog.songs["1"]
        assert (song.artist, song.title) == ("Other Act", "Cheer / Cheer - Remix")
        assert [(ln.chart, ln.song) for ln in song.links] == [("top40", "3")]

    def test_on_a_linked_raw_song_goes_to_its_song(self):
        first = merge(EMPTY, LIVE, [("top40", "1"), ("top40", "2")], []).catalog
        change = alias(first, LIVE, ("P. Act", "Tune"), raw=("top40", "2"))
        assert change.catalog.alias_owner(("p act", "tune")) == "1"

    def test_an_alias_the_song_has_is_no_change(self):
        first = merge(
            EMPTY, LIVE, [("top40", "1")], [], ("Pretend Act", "Made Up Tune")
        ).catalog
        change = alias(first, LIVE, ("pretend act", "MADE UP TUNE"), song="1")
        assert not change.changed

    def test_an_alias_that_matches_nothing_is_refused(self):
        first = merge(EMPTY, LIVE, [("top40", "1"), ("top40", "2")], []).catalog
        with pytest.raises(CurationError, match="matches nothing"):
            alias(first, LIVE, ("?", "?"), song="1")


class TestInverse:
    def _merged(self):
        return merge(
            EMPTY, LIVE, [("top40", "1"), ("top40", "2"), ("top2000", "1")], []
        ).catalog

    def test_unlink(self):
        change = unlink(self._merged(), "1", ("top2000", "1"))
        assert [(ln.chart, ln.song) for ln in change.catalog.songs["1"].links] == [
            ("top40", "1"),
            ("top40", "2"),
        ]

    def test_unlinking_the_last_link_drops_the_song(self):
        one = merge(
            EMPTY, LIVE, [("top40", "1")], [], ("Pretend Act", "Made Up Tune")
        ).catalog
        change = unlink(one, "1", ("top40", "1"))
        assert change.catalog.songs == {}
        assert any("@1 is dropped" in line for line in change.lines)

    def test_unlink_of_a_raw_song_the_song_does_not_link(self):
        with pytest.raises(CurationError, match="@1 does not link top40:7"):
            unlink(self._merged(), "1", ("top40", "7"))

    def test_unalias(self):
        named = merge(
            EMPTY, LIVE, [("top40", "1")], [], ("Pretend Act", "Made Up Tune")
        ).catalog
        change = unalias(named, "1", ("PRETEND ACT", "made up tune"))
        assert change.catalog.songs["1"].aliases == ()
        with pytest.raises(CurationError, match="has no alias"):
            unalias(change.catalog, "1", ("Pretend Act", "Made Up Tune"))

    def test_drop(self):
        change = drop(self._merged(), "1")
        assert change.catalog.songs == {} and change.catalog.next_id == 2


class TestPolicyCases:
    """One test per policy case of #162, through the resolution chartsgen
    will use (#165)."""

    def test_spelling_variants_within_a_chart(self):
        catalog = merge(EMPTY, LIVE, [("top40", "1"), ("top40", "2")], []).catalog
        for spelling in ("Made Up Tune", "Made-Up Tune"):
            result = _lookup(catalog, "Pretend Act", spelling)
            assert sorted(p.axes["week"] for p in result.placements["top40"]) == [1, 2]

    def test_spelling_variants_across_charts(self):
        catalog = merge(EMPTY, LIVE, [("top40", "1"), ("top2000", "1")], []).catalog
        result = _lookup(catalog, "Pretend Act", "Made Up Tune (Remaster)")
        assert set(result.placements) == {"top40", "top2000"}

    def test_a_re_release_is_the_same_song(self):
        # A remaster is a spelling variant by policy: merged like any other.
        catalog = merge(EMPTY, LIVE, [("top2000", "1"), ("top40", "1")], []).catalog
        result = _lookup(catalog, "Pretend Act", "Made Up Tune")
        assert result.placements["top2000"] == [Placement({"year": 2000}, 10, 2000)]

    def test_fold_where_the_base_charted_alone(self):
        catalog = merge(
            EMPTY, LIVE, [("top40", "8"), ("top40", "3")], [], ("Other Act", "Cheer")
        ).catalog
        result = _lookup(catalog, "Other Act", "Cheer")
        assert sorted(
            (p.axes["week"], p.position) for p in result.placements["top40"]
        ) == [
            (1, 2),
            (2, 2),
        ]
        assert _lookup(catalog, "Other Act", "Cheer - Remix").is_miss

    def test_fold_where_the_base_never_charted_alone(self):
        catalog = merge(
            EMPTY, LIVE, [("top40", "4")], [], ("Third Act", "Bundle Tune")
        ).catalog
        assert _lookup(catalog, "Third Act", "Bundle Tune").placements["top40"] == [
            _p(1970, 1, 5)
        ]
        assert _lookup(catalog, "Third Act", "Bundle Tune - Edit").is_miss

    def test_a_double_a_side_credits_each_side(self):
        # Split at acquisition (#183): no catalog decision needed.
        for side in ("Side A", "Side B"):
            assert _lookup(EMPTY, "Duo Act", side).placements["top40"] == [
                _p(1970, 1, 3)
            ]

    def test_a_live_version_stays_its_own_song(self):
        catalog = merge(EMPTY, LIVE, [("top40", "1"), ("top40", "2")], []).catalog
        assert _lookup(catalog, "Pretend Act", "Made Up Tune (Live)").placements[
            "top40"
        ] == [_p(1970, 1, 4)]
        assert (
            _p(1970, 1, 4)
            not in _lookup(catalog, "Pretend Act", "Made Up Tune").placements["top40"]
        )

    def test_every_inverse_restores_the_lookup(self):
        before = _lookup(EMPTY, "Pretend Act", "Made Up Tune")
        merged = merge(
            EMPTY,
            LIVE,
            [("top40", "1"), ("top40", "2")],
            [],
            ("Pretend Act", "Made Up Tune"),
        ).catalog
        assert _lookup(merged, "Pretend Act", "Made Up Tune") != before
        dropped = drop(merged, "1").catalog
        assert _lookup(dropped, "Pretend Act", "Made Up Tune") == before
        unlinked = unlink(
            unlink(merged, "1", ("top40", "2")).catalog, "1", ("top40", "1")
        ).catalog
        assert _lookup(unlinked, "Pretend Act", "Made Up Tune") == before
        aliased = alias(EMPTY, LIVE, ("P Act", "Tune"), raw=("top40", "1")).catalog
        restored = unalias(aliased, "1", ("P Act", "Tune")).catalog
        assert _lookup(restored, "P Act", "Tune") == _lookup(EMPTY, "P Act", "Tune")
