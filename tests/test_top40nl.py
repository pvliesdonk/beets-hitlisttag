"""Tests for beetsplug.hitlisttag.top40nl (top40.nl specifics)."""

from __future__ import annotations

import ssl
import time
from pathlib import Path

from beetsplug.hitlisttag import top40nl
from beetsplug.hitlisttag.fetch import Fetcher


def _loaded_cert() -> dict:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=top40nl.SECTIGO_R36_PEM)
    [cert] = ctx.get_ca_certs()
    return cert


def test_embedded_intermediate_is_sectigo_r36():
    subject = dict(pair[0] for pair in _loaded_cert()["subject"])
    assert subject["commonName"] == "Sectigo Public Server Authentication CA DV R36"


def test_embedded_intermediate_has_not_expired():
    not_after = ssl.cert_time_to_seconds(_loaded_cert()["notAfter"])
    assert not_after > time.time(), "SECTIGO_R36_PEM expired; refresh it (#126)"


def test_fetcher_is_configured_for_the_site():
    fetcher = top40nl.fetcher()
    assert isinstance(fetcher, Fetcher)
    assert fetcher._min_interval == 1.0
    adapter = fetcher._session.get_adapter(top40nl.BASE_URL + "/")
    ctx = adapter.poolmanager.connection_pool_kw["ssl_context"]
    trusted = {
        dict(pair[0] for pair in cert["subject"]).get("commonName")
        for cert in ctx.get_ca_certs()
    }
    assert "Sectigo Public Server Authentication CA DV R36" in trusted


def test_base_url():
    assert top40nl.BASE_URL == "https://www.top40.nl"


FIXTURES = Path(__file__).parent / "fixtures" / "top40nl"


def _items(name: str) -> list[top40nl.ListItem]:
    return top40nl.parse_list((FIXTURES / name).read_text(encoding="utf-8"))


class TestParseList:
    def test_year_layout(self):
        assert _items("year-layout.html") == [
            top40nl.ListItem(
                1,
                "Made Up Song ((1965)) / Made Up Song",
                "Imaginary Duo / Fictional Trio",
                "1001",
                "2001",
            ),
            top40nl.ListItem(2, "Side A ; Side B", "The Pretend Band", "1002", None),
            top40nl.ListItem(
                3, "Rock & Roll Dream", "Sinéad & The Nothings", "1003", "1003_4003"
            ),
            top40nl.ListItem(4, "Spaced Out", "Echo", "1004", None),
        ]

    def test_week_layout_with_dropouts_and_a_sidebar_image(self):
        assert _items("week-layout.html") == [
            top40nl.ListItem(1, "First Tune", "Invented Band", "2001", "2001_3001"),
            top40nl.ListItem(
                2, "Second Tune", "Make Believe feat. Someone", "2002", None
            ),
            top40nl.ListItem(None, "Gone Tune", "Vanished", "2004", "2004_3004"),
            # The sidebar's subtitle image after the list must not leak in.
            top40nl.ListItem(None, "Last Tune", "Phantom", "2005", None),
        ]

    def test_page_without_items(self):
        assert top40nl.parse_list("<html><body><p>Nothing</p></body></html>") == []

    def test_malformed_markup_does_not_raise(self):
        html = '<div class="top40-list__item"><div class="number-block"><h4>7'
        assert top40nl.parse_list(html) == []

    def test_link_without_trailing_id(self):
        html = (
            '<div class="top40-list__item"><div class="number-block"><h4>5</h4></div>'
            '<a href="https://www.top40.nl/x/y" class="h3">T</a>'
            '<a href="#" class="p lead lowercase">A</a></div>'
        )
        assert top40nl.parse_list(html) == [top40nl.ListItem(5, "T", "A", None, None)]


def _one(img: str) -> top40nl.ListItem:
    html = (
        f'<div class="top40-list__item">{img}'
        '<div class="number-block"><h4>1</h4></div>'
        '<a href="https://www.top40.nl/a/b-7" class="h3">T</a>'
        '<a href="#" class="p lead lowercase">A</a></div>'
    )
    [item] = top40nl.parse_list(html)
    return item


class TestImageTitle:
    def test_details_prefix_removed(self):
        img = '<img src="x/uploads/subtitle/7_8/o.jpg" title="Details Artist - Song"/>'
        assert _one(img).image_title == "Artist - Song"

    def test_entities_and_whitespace(self):
        img = '<img src="x" title="Details  Rock &amp; Roll\n  Band - Song"/>'
        assert _one(img).image_title == "Rock & Roll Band - Song"

    def test_details_alone_is_none(self):
        assert _one('<img src="x" title="Details"/>').image_title is None

    def test_other_title_kept_as_is(self):
        assert _one('<img src="x" title="Artist - Song"/>').image_title == (
            "Artist - Song"
        )

    def test_no_image_or_no_title_is_none(self):
        assert _one("").image_title is None
        assert _one('<img src="x"/>').image_title is None

    def test_first_image_counts_even_without_subtitle(self):
        img = (
            '<img src="x/uploads/title/7/o.jpg" title="Details A - T"/>'
            '<img src="x/uploads/subtitle/9/o.jpg" title="Details Other"/>'
        )
        item = _one(img)
        assert item.image_title == "A - T"
        assert item.subtitle == "9"

    def test_fixture_items_have_no_image_title(self):
        assert all(i.image_title is None for i in _items("year-layout.html"))
        assert all(i.image_title is None for i in _items("week-layout.html"))


class TestPositionProblems:
    def test_complete(self):
        assert top40nl.position_problems(range(1, 41), 40) == ""

    def test_missing_duplicated_and_outside(self):
        positions = [p for p in range(1, 41) if p not in (7, 8, 30)] + [12, 41]
        assert top40nl.position_problems(positions, 40) == (
            "missing positions 7–8, 30; duplicated positions 12; "
            "positions outside 1–40: 41"
        )

    def test_empty(self):
        assert top40nl.position_problems([], 100) == "missing positions 1–100"


class TestSplitNames:
    """top40.nl's convention (#160, classes A–E): " ; " is a double A-side,
    " / " between artists is versions sharing a position."""

    def test_plain_name_is_one_song(self):
        assert top40nl.split_names("Pretend Act", "Made Up Tune") == [
            ("Pretend Act", "Made Up Tune")
        ]

    def test_one_artist_two_sides(self):  # class A
        assert top40nl.split_names("Pretend Act", "Side A ; Side B") == [
            ("Pretend Act", "Side A"),
            ("Pretend Act", "Side B"),
        ]

    def test_sides_with_their_own_artists_pair_in_order(self):  # class B
        assert top40nl.split_names("Act One ; Act Two", "Side A ; Side B") == [
            ("Act One", "Side A"),
            ("Act Two", "Side B"),
        ]

    def test_versions_of_one_title(self):  # class C
        assert top40nl.split_names("Act One / Act Two / Act Three", "Shared Tune") == [
            ("Act One", "Shared Tune"),
            ("Act Two", "Shared Tune"),
            ("Act Three", "Shared Tune"),
        ]

    def test_versions_with_their_own_titles_pair_in_order(self):  # class D
        assert top40nl.split_names(
            "Act One / Act Two", "Tune / Abschiedstune (Tune)"
        ) == [("Act One", "Tune"), ("Act Two", "Abschiedstune (Tune)")]

    def test_same_artist_title_variants_stay_one_song(self):  # class E
        name = ("Pretend Act", "Tune / Tune - Original Version")
        assert top40nl.split_names(*name) == [name]

    def test_versions_then_sides(self):
        # " / " is the outer separator (the site's versions or re-entries,
        # each segment ending in its ((year)) marker), " ; " the inner one.
        assert top40nl.split_names("Duo One / Trio Two ((GBR))", "Side A ; Side B") == [
            ("Duo One", "Side A"),
            ("Duo One", "Side B"),
            ("Trio Two ((GBR))", "Side A"),
            ("Trio Two ((GBR))", "Side B"),
        ]

    def test_versions_with_a_double_a_side_inside(self):
        # Shape of the real "Motions / The Four Tops / The Four Tops" entry:
        # three versions paired with three titles, the first a double A-side,
        # the last two the same song's re-entries.
        assert top40nl.split_names(
            "Act One / Act Two / Act Two",
            "Tune ; B-Side ((1966)) / Tune ((1966)) / Tune ((1971))",
        ) == [
            ("Act One", "Tune"),
            ("Act One", "B-Side"),
            ("Act Two", "Tune"),
        ]

    def test_same_artist_variants_with_a_double_a_side_inside(self):
        # A same-artist name stays whole (curation folds it) unless a segment
        # is a double A-side, whose other side only the split can credit.
        assert top40nl.split_names("Pretend Act", "Tune / Tune ; Other Side") == [
            ("Pretend Act", "Tune"),
            ("Pretend Act", "Other Side"),
        ]

    def test_re_entries_collapse_by_match_key(self):
        # ((year)) on a title is the site's re-entry marker for the same song.
        assert top40nl.split_names(
            "Pretend Act", "Tune ; Other Side ((1982)) / Tune ((2013))"
        ) == [("Pretend Act", "Tune"), ("Pretend Act", "Other Side")]
        assert top40nl.split_names(
            "Pretend Act", "Tune ((1965)) / Tune ; Other Side ((1974))"
        ) == [("Pretend Act", "Tune"), ("Pretend Act", "Other Side")]

    # The site shows one merged name in every edition any of its versions
    # charted in; a segment's ((year)) marker says which edition it belongs
    # to (the real "The Righteous Brothers / Trea Dobbs / Cilla Black / The
    # Righteous Brothers" entry, in the Top 100 of 1965 and of 1988).
    MERGED = (
        "Act One / Act Two / Act Three / Act One",
        "Tune ((1965)) / Tune ((1965)) / Tune ((1965)) / Tune ((1988))",
    )

    def test_a_segment_marked_for_another_year_is_left_out(self):
        assert top40nl.split_names(*self.MERGED, year=1988) == [("Act One", "Tune")]
        assert top40nl.split_names(*self.MERGED, year=1965) == [
            ("Act One", "Tune"),
            ("Act Two", "Tune"),
            ("Act Three", "Tune"),
        ]

    def test_a_marker_one_year_off_still_counts(self):
        # A late-1965 version still charting in January 1966.
        assert top40nl.split_names(*self.MERGED, year=1966) == [
            ("Act One", "Tune"),
            ("Act Two", "Tune"),
            ("Act Three", "Tune"),
        ]

    def test_a_marker_covers_its_whole_segment(self):
        # Henk & Henk's shape, in a 2013 week: the 1982 double A-side goes.
        assert top40nl.split_names(
            "Pretend Act", "Tune ; Other Side ((1982)) / Tune ((2013))", year=2013
        ) == [("Pretend Act", "Tune")]

    def test_unmarked_segments_stay_and_no_year_keeps_all(self):
        name = ("Act One / Act Two", "Tune ((1965)) / Tune")
        assert top40nl.split_names(*name, year=1988) == [("Act Two", "Tune")]
        assert top40nl.split_names(*self.MERGED) == [
            ("Act One", "Tune"),
            ("Act Two", "Tune"),
            ("Act Three", "Tune"),
        ]

    def test_no_segment_near_the_year_keeps_them_all(self):
        assert top40nl.split_names(*self.MERGED, year=2000) == [
            ("Act One", "Tune"),
            ("Act Two", "Tune"),
            ("Act Three", "Tune"),
        ]

    def test_one_song_reads_the_same_in_every_year(self):
        # The marker has done its job once the year is chosen: dropping it
        # lets acquisition give the 1982 and 2013 editions one raw id (the
        # real Henk & Henk entry; match_key already ignores the marker).
        name = ("Pretend Act", "Tune ; Other Side ((1982)) / Tune ((2013))")
        assert ("Pretend Act", "Tune") in top40nl.split_names(*name, year=1982)
        assert top40nl.split_names(*name, year=2013) == [("Pretend Act", "Tune")]

    def test_a_same_artist_name_kept_whole_keeps_its_markers(self):
        name = ("Pretend Act", "Tune ((1966)) / Tune - Remix ((1990))")
        assert top40nl.split_names(*name, year=1990) == [name]

    def test_mismatched_counts_stay_one_song(self):
        for name in (
            ("Act One ; Act Two ; Act Three", "Side A ; Side B"),
            ("Act One ; Act Two", "One Title"),
            ("Act One / Act Two / Act Three", "Tune A / Tune B"),
            ("Act One / Act Two / Act Three", "A / B ; C"),
        ):
            assert top40nl.split_names(*name) == [name]

    def test_a_segment_that_cannot_split_stays_whole(self):
        # The versions split; the second version's sides don't pair (two
        # artists, three titles), so that segment stays as it is.
        assert top40nl.split_names(
            "Act One / Act Two ; Act Three", "Tune / A ; B ; C"
        ) == [("Act One", "Tune"), ("Act Two ; Act Three", "A ; B ; C")]

    def test_separators_need_their_spaces(self):
        for name in (("AC/DC", "Tune"), ("Pretend Act", "Hello;Goodbye")):
            assert top40nl.split_names(*name) == [name]

    def test_an_empty_part_leaves_the_name_unsplit(self):
        for name in (("Pretend Act", "Side A ; "), (" / Act Two", "Tune")):
            assert top40nl.split_names(*name) == [name]

    def test_parts_are_stripped(self):
        assert top40nl.split_names("Act One  /  Act Two", "Tune") == [
            ("Act One", "Tune"),
            ("Act Two", "Tune"),
        ]

    def test_duplicate_parts_collapse(self):
        # Equal under the strict normalizer acquisition reuses ids by; a
        # second copy would get its own raw id (ids never repeat in an edition).
        assert top40nl.split_names("Pretend Act / PRETEND ACT", "Tune") == [
            ("Pretend Act", "Tune")
        ]
        # And by match_key: the real "Jay and The Americans / Jay & The
        # Americans" is one artist; two parts would be ambiguous in lookup.
        assert top40nl.split_names("Jay and The Act / Jay & The Act", "Tune") == [
            ("Jay and The Act", "Tune")
        ]
