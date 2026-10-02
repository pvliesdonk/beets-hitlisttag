"""Tests for the ingestor contract and discovery (beetsplug.hitlisttag.ingest)."""

from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path

import pytest

from beetsplug.hitlisttag.dataset import Edition, Entry, Song
from beetsplug.hitlisttag.ingest import (
    AcquiredEdition,
    DiscoveryError,
    EditionRef,
    IngestError,
    Ingestor,
    RawEntry,
    RawSong,
    _check_ingestor,
    acquire_edition,
    discover_ingestors,
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


class _FakeIngestor:
    """Minimal in-test ingestor; `fetch` returns whatever `responses` holds."""

    chart = "fakechart"
    axes = ("year",)

    def __init__(self, responses: dict[EditionRef, AcquiredEdition]) -> None:
        self.responses = responses
        self.fetched: list[EditionRef] = []

    def editions(self):
        return list(self.responses)

    def fetch(self, ref: EditionRef) -> AcquiredEdition:
        self.fetched.append(ref)
        return self.responses[ref]


class TestAcquireEdition:
    def test_fake_satisfies_protocol(self):
        assert isinstance(_FakeIngestor({}), Ingestor)

    def test_returns_fetched_edition(self):
        ref = EditionRef({"year": 2023})
        edition = AcquiredEdition(ref, 3, (_entry(1),))
        ingestor = _FakeIngestor({ref: edition})
        assert acquire_edition(ingestor, ref) is edition
        assert ingestor.fetched == [ref]

    def test_rejects_edition_with_other_ref(self):
        asked = EditionRef({"year": 2023})
        got = AcquiredEdition(EditionRef({"year": 2024}), 3, (_entry(1),))
        ingestor = _FakeIngestor({asked: got})
        with pytest.raises(IngestError, match="fakechart.*2024.*2023"):
            acquire_edition(ingestor, asked)

    def test_rejects_ref_whose_axis_names_differ_from_ingestor_axes(self):
        # Review focus 4: a ref for ("year", "week") against a ("year",) chart.
        ref = EditionRef({"year": 2024, "week": 7})
        ingestor = _FakeIngestor({ref: AcquiredEdition(ref, 3, ())})
        with pytest.raises(IngestError, match=r"fakechart.*year.*week"):
            acquire_edition(ingestor, ref)
        assert ingestor.fetched == []  # refused before fetch

    def test_ingest_error_from_fetch_propagates_unchanged(self):
        class Failing(_FakeIngestor):
            def fetch(self, ref):
                raise IngestError("source unreachable")

        with pytest.raises(IngestError, match="source unreachable"):
            acquire_edition(Failing({}), EditionRef({"year": 2023}))

    def test_other_exceptions_propagate(self):
        class Buggy(_FakeIngestor):
            def fetch(self, ref):
                raise KeyError("bug in script")

        with pytest.raises(KeyError):
            acquire_edition(Buggy({}), EditionRef({"year": 2023}))


log = logging.getLogger("test.ingest")

VALID_SCRIPT = """
from beetsplug.hitlisttag.ingest import AcquiredEdition, EditionRef, RawEntry, RawSong


class _Ingestor:
    chart = "{chart}"
    axes = ("year",)

    def editions(self):
        return [EditionRef({{"year": 2001}})]

    def fetch(self, ref):
        return AcquiredEdition(
            ref, 2, (RawEntry(1, (RawSong("Made Up", "Song {chart}"),)),)
        )


INGESTOR = _Ingestor()
"""


def _write_script(directory: Path, name: str, chart: str | None = None) -> Path:
    """Write a valid ingestor script `name`.py claiming `chart` (default: name)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(VALID_SCRIPT.format(chart=chart or name), encoding="utf-8")
    return path


class TestCheckIngestor:
    def test_valid_object_passes(self):
        assert _check_ingestor(_FakeIngestor({})) is None

    def test_none_is_missing(self):
        assert "no INGESTOR" in _check_ingestor(None)

    def test_class_instead_of_instance_rejected(self):
        # Review focus 2: INGESTOR = SomeClass looks valid until the first call.
        problem = _check_ingestor(_FakeIngestor)
        assert problem is not None and "instance" in problem

    @pytest.mark.parametrize("chart", ["", None, 3])
    def test_bad_chart_rejected(self, chart):
        class Bad(_FakeIngestor):
            pass

        Bad.chart = chart
        assert "chart" in _check_ingestor(Bad({}))

    @pytest.mark.parametrize("axes", [(), "year", ["year"], ("year", ""), (1,)])
    def test_bad_axes_rejected(self, axes):
        class Bad(_FakeIngestor):
            pass

        Bad.axes = axes
        assert "axes" in _check_ingestor(Bad({}))

    @pytest.mark.parametrize("method", ["editions", "fetch"])
    def test_missing_method_rejected(self, method):
        class Bad(_FakeIngestor):
            pass

        setattr(Bad, method, None)
        assert method in _check_ingestor(Bad({}))


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    """Point the bundled ingestors package at an empty temp directory.

    Returns the directory; tests write modules into it to simulate bundled
    ingestors without touching the source tree. Modules imported under the
    package are dropped from sys.modules afterwards.
    """
    import beetsplug.hitlisttag.ingestors as pkg

    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    monkeypatch.setattr(pkg, "__path__", [str(bundled_dir)])
    importlib.invalidate_caches()
    before = set(sys.modules)
    yield bundled_dir
    for name in set(sys.modules) - before:
        if name.startswith("beetsplug.hitlisttag.ingestors."):
            del sys.modules[name]


class TestDiscoverBundled:
    def test_empty_package_yields_nothing(self, bundled):
        assert discover_ingestors(None, log=log) == {}

    def test_bundled_module_discovered_by_chart(self, bundled):
        _write_script(bundled, "fakechart")
        found = discover_ingestors(None, log=log)
        assert set(found) == {"fakechart"}
        assert found["fakechart"].axes == ("year",)

    def test_invalid_bundled_module_warned_and_skipped(self, bundled, caplog):
        _write_script(bundled, "good")
        (bundled / "bad.py").write_text("INGESTOR = None\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            found = discover_ingestors(None, log=log)
        assert set(found) == {"good"}
        assert "ingestors.bad" in caplog.text and "no INGESTOR" in caplog.text

    def test_bundled_module_raising_on_import_warned_and_skipped(self, bundled, caplog):
        (bundled / "boom.py").write_text('raise RuntimeError("boom")\n')
        with caplog.at_level(logging.WARNING):
            found = discover_ingestors(None, log=log)
        assert found == {}
        assert "ingestors.boom" in caplog.text and "boom" in caplog.text

    def test_two_bundled_modules_same_chart_is_error(self, bundled):
        _write_script(bundled, "one", chart="dup")
        _write_script(bundled, "two", chart="dup")
        with pytest.raises(DiscoveryError, match="dup"):
            discover_ingestors(None, log=log)


INGESTOR_FIXTURES = Path(__file__).parent / "fixtures" / "ingestors"


class TestBundledTop2000:
    def test_real_bundled_package_exposes_top2000(self):
        # Without the `bundled` fixture: the real package is scanned.
        found = discover_ingestors(None, log=log)
        assert "top2000" in found
        assert found["top2000"].axes == ("year",)


class TestDiscoverDropIns:
    def test_fixture_directory(self, bundled, caplog):
        with caplog.at_level(logging.WARNING):
            found = discover_ingestors(INGESTOR_FIXTURES, log=log)
        assert set(found) == {"testchart"}
        # Each unusable script is warned about by path, and discovery went on.
        assert "broken_import.py" in caplog.text and "boom at import" in caplog.text
        assert "no_ingestor.py" in caplog.text and "no INGESTOR" in caplog.text
        assert "bad_axes.py" in caplog.text and "axes" in caplog.text
        assert "_private.py" not in caplog.text and "hidden" not in found

    def test_missing_directory_is_not_an_error(self, bundled, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            assert discover_ingestors(tmp_path / "absent", log=log) == {}
        assert caplog.text == ""

    def test_only_top_level_py_files(self, bundled, tmp_path):
        _write_script(tmp_path, "top")
        _write_script(tmp_path / "sub", "nested")
        (tmp_path / "notes.txt").write_text("INGESTOR = 1", encoding="utf-8")
        (tmp_path / "UPPER.PY").write_text("INGESTOR = 1", encoding="utf-8")
        assert set(discover_ingestors(tmp_path, log=log)) == {"top"}

    def test_drop_in_overrides_bundled_with_notice(self, bundled, tmp_path, caplog):
        _write_script(bundled, "shared")
        drop_in = _write_script(tmp_path, "mine", chart="shared")
        with caplog.at_level(logging.INFO):
            found = discover_ingestors(tmp_path, log=log)
        assert set(found) == {"shared"}
        assert type(found["shared"]).__module__.startswith("hitlisttag_ingestor_")
        assert "overrides" in caplog.text and str(drop_in) in caplog.text

    def test_two_drop_ins_same_chart_is_error(self, bundled, tmp_path):
        a = _write_script(tmp_path, "a", chart="dup")
        b = _write_script(tmp_path, "b", chart="dup")
        with pytest.raises(DiscoveryError) as excinfo:
            discover_ingestors(tmp_path, log=log)
        assert str(a) in str(excinfo.value) and str(b) in str(excinfo.value)

    def test_script_named_like_stdlib_module_does_not_shadow_it(
        self, bundled, tmp_path
    ):
        # Review focus 1.
        import json as real_json

        _write_script(tmp_path, "json")
        found = discover_ingestors(tmp_path, log=log)
        assert set(found) == {"json"}
        assert sys.modules["json"] is real_json
        assert real_json.dumps({"a": 1}) == '{"a": 1}'

    def test_sibling_import_is_warned_not_crashed(self, bundled, tmp_path, caplog):
        # Review focus 3: drop-ins are not on sys.path.
        _write_script(tmp_path, "helper")
        (tmp_path / "uses_helper.py").write_text(
            "import helper\nINGESTOR = helper.INGESTOR\n", encoding="utf-8"
        )
        with caplog.at_level(logging.WARNING):
            found = discover_ingestors(tmp_path, log=log)
        assert set(found) == {"helper"}
        assert "uses_helper.py" in caplog.text
        assert "No module named 'helper'" in caplog.text

    def test_unreadable_script_is_warned_and_skipped(self, bundled, tmp_path, caplog):
        # Review focus 5.
        if os.geteuid() == 0:
            pytest.skip("root can read anything")
        _write_script(tmp_path, "good")
        locked = _write_script(tmp_path, "locked")
        locked.chmod(0o000)
        try:
            with caplog.at_level(logging.WARNING):
                found = discover_ingestors(tmp_path, log=log)
        finally:
            locked.chmod(0o644)
        assert set(found) == {"good"}
        assert "locked.py" in caplog.text

    def test_unreadable_directory_is_warned_and_skipped(
        self, bundled, tmp_path, caplog
    ):
        if os.geteuid() == 0:
            pytest.skip("root can read anything")
        _write_script(bundled, "fromcode")
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o000)
        try:
            with caplog.at_level(logging.WARNING):
                found = discover_ingestors(locked, log=log)
        finally:
            locked.chmod(0o755)
        assert set(found) == {"fromcode"}
        assert "cannot read ingestor directory" in caplog.text


class TestIngestorDirConfig:
    @pytest.fixture
    def plugin(self):
        from beets import config
        from beets.plugins import find_plugins, load_plugins
        from beets.test.helper import TestHelper

        from beetsplug.hitlisttag import HitlistTag

        helper = TestHelper()
        with helper:
            config["plugins"] = ["hitlisttag"]
            load_plugins()
            yield next(p for p in find_plugins() if isinstance(p, HitlistTag))

    def test_default_is_ingestors_under_config_dir(self, plugin):
        from beets import config

        assert plugin.ingestor_dir == Path(config.config_dir()) / "ingestors"

    def test_relative_path_resolves_against_config_dir(self, plugin):
        from beets import config

        plugin.config["ingestor_dir"] = "my/scripts"
        assert plugin.ingestor_dir == Path(config.config_dir()) / "my" / "scripts"

    def test_absolute_path_kept(self, plugin):
        plugin.config["ingestor_dir"] = "/opt/ingestors"
        assert plugin.ingestor_dir == Path("/opt/ingestors")

    def test_tilde_expanded(self, plugin, monkeypatch):
        monkeypatch.setenv("HOME", "/home/someone")
        plugin.config["ingestor_dir"] = "~/ingestors"
        assert plugin.ingestor_dir == Path("/home/someone/ingestors")


class TestPluggabilityProof:
    """#99's acceptance clause: an ingestor outside beetsplug/ is discovered
    and used, and what it yields fits the dataset the reader accepts."""

    def test_fixture_ingestor_drives_the_contract_end_to_end(self, bundled):
        found = discover_ingestors(INGESTOR_FIXTURES, log=log)
        ingestor = found["testchart"]
        assert not type(ingestor).__module__.startswith("beetsplug.")

        refs = list(ingestor.editions())
        assert refs == [EditionRef({"year": 2001}), EditionRef({"year": 2002})]

        for ref in refs:
            acquired = acquire_edition(ingestor, ref)
            # What #100 will do: mint ids and build a dataset Edition. Doing
            # it here proves the contract's output satisfies the dataset's
            # invariants without needing the tool.
            ids: dict[tuple[str, str], str] = {}
            entries = []
            for raw in acquired.entries:
                songs = []
                for raw_song in raw.songs:
                    key = (raw_song.artist, raw_song.title)
                    song_id = ids.setdefault(key, str(len(ids) + 1))
                    songs.append(Song(song_id, raw_song.artist, raw_song.title))
                entries.append(Entry(raw.position, songs))
            edition = Edition(dict(acquired.ref.axes), acquired.size, entries)
            assert edition.size == 3
            assert [e.position for e in edition.entries] == [1, 2]
            assert len(edition.entries[1].songs) == 2  # the two-song release


class TestFinalReviewFixes:
    """Findings from the whole-branch review, each pinned before its fix."""

    def test_non_traversable_parent_is_warned_and_skipped(
        self, bundled, tmp_path, caplog
    ):
        # Path.is_dir() raises PermissionError before 3.14 and returns False
        # on 3.14; either way the user must get a warning, not a crash or
        # silence.
        if os.geteuid() == 0:
            pytest.skip("root can read anything")
        parent = tmp_path / "parent"
        target = parent / "ingestors"
        _write_script(target, "inside")
        parent.chmod(0o000)
        try:
            with caplog.at_level(logging.WARNING):
                found = discover_ingestors(target, log=log)
        finally:
            parent.chmod(0o755)
        assert found == {}
        assert "cannot read ingestor directory" in caplog.text

    def test_listable_but_not_traversable_directory_warns_per_file(
        self, bundled, tmp_path, caplog
    ):
        if os.geteuid() == 0:
            pytest.skip("root can read anything")
        _write_script(tmp_path, "inside")
        tmp_path.chmod(0o444)
        try:
            with caplog.at_level(logging.WARNING):
                found = discover_ingestors(tmp_path, log=log)
        finally:
            tmp_path.chmod(0o755)
        assert found == {}
        assert "inside.py" in caplog.text

    def test_acquired_edition_requires_an_edition_ref(self):
        with pytest.raises(ValueError, match="ref"):
            AcquiredEdition({"year": 2023}, 3, ())  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [None, {"year": 2023}])
    def test_fetch_returning_non_edition_is_ingest_error(self, bad):
        class Wrong(_FakeIngestor):
            def fetch(self, ref):
                return bad

        with pytest.raises(IngestError, match="fakechart"):
            acquire_edition(Wrong({}), EditionRef({"year": 2023}))

    def test_script_calling_sys_exit_is_warned_and_skipped(
        self, bundled, tmp_path, caplog
    ):
        _write_script(tmp_path, "good")
        (tmp_path / "quitter.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            found = discover_ingestors(tmp_path, log=log)
        assert set(found) == {"good"}
        assert "quitter.py" in caplog.text
        assert "hitlisttag_ingestor_quitter" not in sys.modules

    def test_edition_ref_axes_cannot_be_mutated(self):
        ref = EditionRef({"year": 2023})
        with pytest.raises(TypeError):
            ref.axes["year"] = 1999  # type: ignore[index]
        assert ref.axes == {"year": 2023}


class TestPrReviewHardening:
    """Points 1, 2 and 4 of the PR #106 bot review, pinned before the fix."""

    def test_raw_entry_songs_list_normalised_to_tuple(self):
        entry = RawEntry(1, [_song()])  # type: ignore[arg-type]
        assert isinstance(entry.songs, tuple)

    @pytest.mark.parametrize("songs", [("Artist - Title",), ({"artist": "A"},), "ab"])
    def test_raw_entry_rejects_non_rawsong_elements(self, songs):
        with pytest.raises(ValueError, match="RawSong"):
            RawEntry(1, songs)  # type: ignore[arg-type]

    def test_acquired_edition_entries_list_normalised_to_tuple(self):
        edition = AcquiredEdition(EditionRef({"year": 2023}), 3, [_entry(1)])  # type: ignore[arg-type]
        assert isinstance(edition.entries, tuple)

    @pytest.mark.parametrize("entries", [((1, ()),), ({"position": 1},)])
    def test_acquired_edition_rejects_non_rawentry_elements(self, entries):
        with pytest.raises(ValueError, match="RawEntry"):
            AcquiredEdition(EditionRef({"year": 2023}), 3, entries)  # type: ignore[arg-type]

    def test_duplicate_axis_names_rejected(self):
        class Bad(_FakeIngestor):
            pass

        Bad.axes = ("year", "year")
        problem = _check_ingestor(Bad({}))
        assert problem is not None and "duplicate" in problem

    def test_script_failing_validation_is_unregistered(self, bundled, tmp_path):
        (tmp_path / "novalid.py").write_text("INGESTOR = None\n", encoding="utf-8")
        assert discover_ingestors(tmp_path, log=log) == {}
        assert "hitlisttag_ingestor_novalid" not in sys.modules
