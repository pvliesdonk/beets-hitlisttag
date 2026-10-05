"""Tests for the normalized exact song lookup (beetsplug.hitlisttag.lookup)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from beetsplug.hitlisttag.dataset import Edition, Entry, HitlistData, Song
from beetsplug.hitlisttag.lookup import (
    Placement,
    SongLookupIndex,
    match_key,
    normalize,
)

log = logging.getLogger("test.lookup")


def _data(chart, songs, editions, source="mem"):
    return HitlistData(
        chart=chart,
        songs={s.id: s for s in songs},
        editions=editions,
        source=Path(source),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Beyoncé", "beyonce"),
        ("BEYONCE", "beyonce"),
        ("Mötley Crüe", "motley crue"),
        ("Straße", "strasse"),
        ("Encyclopædia", "encyclopaedia"),
        ("Encyclopaedia", "encyclopaedia"),
        ("Cœur", "coeur"),
        ("Søren", "soren"),
        ("Łódź", "lodz"),
        ("Þórr", "thorr"),
        ("Đevojka", "devojka"),
        ("Håkan", "hakan"),
        ("İstanbul", "istanbul"),
        ("naïve café", "naive cafe"),
        ("Café  del   Mar!", "cafe del mar"),
        ("AC/DC", "ac dc"),
        ("...", ""),
        ("   ", ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # top40.nl's disambiguator for same-named artists (#160).
        ("The Scorpions ((GBR))", "scorpions"),
        ("Sonny ((Bono))", "sonny"),
        ("You've Got Your Troubles ((1965))", "you ve got your troubles"),
        ("InCrowd ((NLD))", "incrowd"),
        # "&" and "and" are interchangeable across sources.
        ("Simon & Garfunkel", "simon and garfunkel"),
        ("Simon and Garfunkel", "simon and garfunkel"),
        ("Ike & Tina Turner", "ike and tina turner"),
        # One leading "The" is dropped, unless it is the whole name.
        ("The Bangles", "bangles"),
        ("Bangles", "bangles"),
        ("The Eagles", "eagles"),
        ("The The", "the"),
        ("The", "the"),
        ("Theatre Of Hate", "theatre of hate"),
        # Everything normalize does still applies.
        ("Beyoncé", "beyonce"),
        ("...", ""),
        ("((GBR))", ""),
        # A name that is nothing but "&" still normalizes to nothing.
        ("&", ""),
        ("& &", ""),
        ("((GBR)) &", ""),
    ],
)
def test_match_key(raw, expected):
    assert match_key(raw) == expected


@pytest.mark.parametrize(
    ("stored", "looked_up"),
    [
        (
            ("The Scorpions ((GBR))", "Hello Josephine"),
            ("Scorpions", "Hello Josephine"),
        ),
        (
            ("Simon & Garfunkel", "Mrs. Robinson"),
            ("Simon and Garfunkel", "Mrs. Robinson"),
        ),
        (("Bangles", "Eternal Flame"), ("The Bangles", "Eternal Flame")),
        (
            ("Scorpions", "Wind Of Change"),
            ("The Scorpions ((GBR))", "Wind of Change"),
        ),
    ],
)
def test_lookup_matches_across_source_spellings(stored, looked_up):
    song = Song(id="1", artist=stored[0], title=stored[1])
    data = _data("top40", [song], [Edition({"year": 1991}, 40, [Entry(1, [song])])])
    index = SongLookupIndex.from_datasets([data], log)

    result = index.lookup(*looked_up)

    assert result.placements == {"top40": [Placement({"year": 1991}, 1, 40)]}
    assert not result.ambiguous_charts


def test_two_spellings_of_one_song_in_one_chart_are_ambiguous():
    a = Song(id="1", artist="Simon & Garfunkel", title="Cecilia")
    b = Song(id="2", artist="Simon and Garfunkel", title="Cecilia")
    data = _data(
        "top40",
        [a, b],
        [
            Edition({"year": 1970}, 40, [Entry(1, [a])]),
            Edition({"year": 1971}, 40, [Entry(1, [b])]),
        ],
    )
    index = SongLookupIndex.from_datasets([data], log)

    result = index.lookup("Simon & Garfunkel", "Cecilia")

    assert result.ambiguous_charts == {"top40"}
    assert result.placements == {}


def test_single_chart_hit_returns_placements():
    a = Song(id="1", artist="Beyoncé", title="Halo")
    data = _data(
        "top40",
        [a],
        [Edition(axes={"year": 2023, "week": 1}, size=40, entries=[Entry(1, [a])])],
    )
    index = SongLookupIndex.from_datasets([data], log)

    result = index.lookup("beyonce", "halo")
    assert result.normalized == ("beyonce", "halo")
    assert result.ambiguous_charts == set()
    assert result.placements == {
        "top40": [Placement(axes={"year": 2023, "week": 1}, position=1, size=40)]
    }


def test_song_in_two_charts_aggregates_not_ambiguous():
    a1 = Song(id="1", artist="ABBA", title="SOS")
    a2 = Song(id="1", artist="ABBA", title="SOS")
    top2000 = _data("top2000", [a1], [Edition({"year": 2022}, 5, [Entry(3, [a1])])])
    top40 = _data(
        "top40", [a2], [Edition({"year": 1975, "week": 20}, 40, [Entry(7, [a2])])]
    )
    index = SongLookupIndex.from_datasets([top2000, top40], log)

    result = index.lookup("abba", "sos")
    assert set(result.placements) == {"top2000", "top40"}
    assert result.ambiguous_charts == set()
    assert result.placements["top2000"][0].position == 3
    assert result.placements["top40"][0].position == 7


def test_song_across_editions_yields_several_placements():
    a = Song(id="1", artist="A", title="T")
    data = _data(
        "top40",
        [a],
        [
            Edition({"year": 2023, "week": 40}, 40, [Entry(1, [a])]),
            Edition({"year": 2023, "week": 41}, 40, [Entry(2, [a])]),
        ],
    )
    index = SongLookupIndex.from_datasets([data], log)

    positions = [p.position for p in index.lookup("a", "t").placements["top40"]]
    assert positions == [1, 2]


def test_miss_returns_empty_result():
    a = Song(id="1", artist="A", title="T")
    data = _data(
        "top40", [a], [Edition({"year": 2023, "week": 1}, 40, [Entry(1, [a])])]
    )
    index = SongLookupIndex.from_datasets([data], log)

    result = index.lookup("nobody", "nothing")
    assert result.normalized == ("nobody", "nothing")
    assert result.placements == {}
    assert result.ambiguous_charts == set()
    assert result.unbound_charts == set()
    assert result.is_miss


def test_two_ids_one_chart_same_key_is_ambiguous():
    a = Song(id="1", artist="The Fixtures", title="Song")
    b = Song(id="2", artist="the  fixtures", title="song")  # distinct id, same key
    data = _data(
        "top40",
        [a, b],
        [Edition({"year": 2023, "week": 1}, 40, [Entry(1, [a]), Entry(2, [b])])],
    )
    index = SongLookupIndex.from_datasets([data], log)

    result = index.lookup("the fixtures", "song")
    assert result.ambiguous_charts == {"top40"}
    assert "top40" not in result.placements


def test_ambiguous_in_one_chart_hit_in_another():
    a = Song(id="1", artist="Dup", title="X")
    b = Song(id="2", artist="dup", title="x")
    clean = Song(id="1", artist="Dup", title="X")
    top40 = _data(
        "top40",
        [a, b],
        [Edition({"year": 2023, "week": 1}, 40, [Entry(1, [a]), Entry(2, [b])])],
    )
    top2000 = _data(
        "top2000", [clean], [Edition({"year": 2023}, 5, [Entry(4, [clean])])]
    )
    index = SongLookupIndex.from_datasets([top40, top2000], log)

    result = index.lookup("dup", "x")
    assert result.ambiguous_charts == {"top40"}
    assert set(result.placements) == {"top2000"}


@pytest.mark.parametrize(
    ("artist", "title"),
    # both, title-only, artist-only, disambiguator-only artist
    [("...", "!!!"), ("A", "..."), ("...", "T"), ("((GBR))", "T")],
)
def test_unnormalizable_track_reports_none(artist, title):
    a = Song(id="1", artist="A", title="T")
    data = _data(
        "top40", [a], [Edition({"year": 2023, "week": 1}, 40, [Entry(1, [a])])]
    )
    index = SongLookupIndex.from_datasets([data], log)

    result = index.lookup(artist, title)
    assert result.unnormalizable
    assert result.normalized is None
    assert result.placements == {}
    assert not result.is_miss


def test_dataset_song_with_empty_key_is_skipped_with_warning(caplog):
    good = Song(id="1", artist="A", title="T")
    bad = Song(id="2", artist="A", title="...")  # title normalizes to empty
    data = _data(
        "top40",
        [good, bad],
        [Edition({"year": 2023, "week": 1}, 40, [Entry(1, [good]), Entry(2, [bad])])],
    )
    with caplog.at_level(logging.WARNING):
        index = SongLookupIndex.from_datasets([data], log)

    assert "normalizes to an empty key" in caplog.text
    assert index.lookup("a", "t").placements == {
        "top40": [Placement(axes={"year": 2023, "week": 1}, position=1, size=40)]
    }


def test_dataset_song_that_is_only_a_disambiguator_is_skipped(caplog):
    good = Song(id="1", artist="A", title="T")
    bad = Song(id="2", artist="A", title="((1965))")
    data = _data(
        "top40",
        [good, bad],
        [Edition({"year": 2023, "week": 1}, 40, [Entry(1, [good]), Entry(2, [bad])])],
    )
    with caplog.at_level(logging.WARNING):
        index = SongLookupIndex.from_datasets([data], log)

    assert "normalizes to an empty key" in caplog.text
    assert index.lookup("A", "1965").is_miss


def test_orphan_song_not_in_any_entry_is_not_indexed():
    charted = Song(id="1", artist="A", title="T")
    orphan = Song(id="2", artist="Orphan", title="Ghost")  # in table, no entry
    data = _data(
        "top40",
        [charted, orphan],
        [Edition({"year": 2023, "week": 1}, 40, [Entry(1, [charted])])],
    )
    index = SongLookupIndex.from_datasets([data], log)

    assert index.lookup("orphan", "ghost").is_miss


def test_placement_rejects_position_above_size():
    with pytest.raises(ValueError, match="out of range"):
        Placement(axes={"year": 2023}, position=5, size=3)


def test_placement_rejects_position_below_one():
    with pytest.raises(ValueError, match="out of range"):
        Placement(axes={"year": 2023}, position=0, size=3)


def test_placement_rejects_nonpositive_size():
    with pytest.raises(ValueError, match="size must be"):
        Placement(axes={"year": 2023}, position=1, size=0)


def test_placement_accepts_valid_bounds():
    p = Placement(axes={"year": 2023}, position=3, size=3)
    assert p.position == 3
    assert p.size == 3
