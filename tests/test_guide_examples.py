"""The ingestor-author guide's web example works as published (#126).

The example's code block is taken from docs/writing-an-ingestor.md, dropped
into an ingestor folder, discovered like a user's script, and run against a
pre-filled development cache, so no network is touched.
"""

from __future__ import annotations

import logging
import re
import textwrap
from pathlib import Path

import pytest
import requests

from beetsplug.hitlisttag.fetch import CACHE_ENV
from beetsplug.hitlisttag.ingest import (
    EditionRef,
    RawEntry,
    RawSong,
    discover_ingestors,
)

GUIDE = Path(__file__).parent.parent / "docs" / "writing-an-ingestor.md"
SITE = "charts.example.org"


def _web_example() -> str:
    text = GUIDE.read_text(encoding="utf-8")
    section = text.split("## Fetch from a website", 1)[1].split("\n## ", 1)[0]
    match = re.search(r"```python\n(.*?)```", section, re.S)
    assert match, "no python example under 'Fetch from a website'"
    return textwrap.dedent(match.group(1))


def _write(cache: Path, path: str, text: str | None) -> None:
    base = cache / SITE / path
    base.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        base.with_name(base.name + ".404").write_text("")
    else:
        base.with_name(base.name + ".page").write_text(text, encoding="utf-8")


def test_web_example_runs_as_published(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    _write(
        cache,
        "zwaarstelijst/2005.csv",
        "position,artist,title\n1,Metallica,One\n2,Rammstein,Sonne\n",
    )
    _write(cache, "zwaarstelijst/2006.csv", "position,artist,title\n1,Tool,Lateralus\n")
    _write(cache, "zwaarstelijst/2007.csv", None)
    monkeypatch.setenv(CACHE_ENV, str(cache))

    def no_network(*args, **kwargs):
        raise AssertionError("the example reached the network")

    monkeypatch.setattr(requests.Session, "get", no_network)

    folder = tmp_path / "ingestors"
    folder.mkdir()
    (folder / "zwaarste.py").write_text(_web_example(), encoding="utf-8")
    found = discover_ingestors(folder, log=logging.getLogger("test"))
    ingestor = found["zwaarstelijst"]

    assert list(ingestor.editions()) == [
        EditionRef({"year": 2005}),
        EditionRef({"year": 2006}),
    ]
    edition = ingestor.fetch(EditionRef({"year": 2005}))
    assert edition.size == 100
    assert edition.entries == (
        RawEntry(1, (RawSong("Metallica", "One"),)),
        RawEntry(2, (RawSong("Rammstein", "Sonne"),)),
    )


def test_web_example_reports_a_missing_year(tmp_path, monkeypatch):
    from beetsplug.hitlisttag.ingest import IngestError

    cache = tmp_path / "cache"
    _write(cache, "zwaarstelijst/2005.csv", None)
    monkeypatch.setenv(CACHE_ENV, str(cache))
    folder = tmp_path / "ingestors"
    folder.mkdir()
    (folder / "zwaarste.py").write_text(_web_example(), encoding="utf-8")
    ingestor = discover_ingestors(folder, log=logging.getLogger("test"))[
        "zwaarstelijst"
    ]
    with pytest.raises(IngestError, match="2005"):
        ingestor.fetch(EditionRef({"year": 2005}))
