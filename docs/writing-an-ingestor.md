# Writing an ingestor

An ingestor teaches `beet chartsacquire` how to get one chart's data. The
plugin ships one, for the Top 2000. For any other chart (a station's
Christmas list, a regional chart, a list you keep yourself) you write a
small Python script, drop it in a folder, and run `chartsacquire` as
usual. Nothing in the plugin needs changing.

This page covers what an ingestor must provide, where the script goes,
how errors are handled, and how to test one without committing chart
data.

## What an ingestor does, and what it doesn't

An ingestor answers two questions about one chart:

- which editions exist at the source (`editions()`);
- what one edition contains: its size and its ranked entries, with artist
  and title exactly as the source publishes them (`fetch(ref)`).

Everything else is the tool's job. It decides which editions are
missing, assigns song ids, matches spellings to existing songs, writes
the dataset file atomically, and reports. An ingestor never touches
files, ids or configuration.

## The contract

Everything an ingestor needs is in `beetsplug.hitlisttag.ingest`:

| Name | What it is |
| --- | --- |
| `EditionRef(axes)` | The identity of one edition: its axis values, e.g. `EditionRef({"year": 2023})` or `EditionRef({"year": 2024, "week": 7})`. |
| `RawSong(artist, title)` | One credited song, spelled as published. Both must be non-empty. |
| `RawEntry(position, songs)` | One release at one rank. `songs` is a tuple of `RawSong`, usually one. |
| `AcquiredEdition(ref, size, entries)` | One edition: the ref it answers, its real size, and a tuple of `RawEntry`. |
| `IngestError(message)` | Raise this when the source misbehaves. |

The ingestor itself is any object with these four members:

```python
class MyIngestor:
    chart: str  # the hitlist name, as in your hitlists config
    axes: tuple[str, ...]  # its axis names in order, e.g. ("year",)

    def editions(self) -> Iterable[EditionRef]: ...

    def fetch(self, ref: EditionRef) -> AcquiredEdition: ...
```

There is no base class to inherit from. The script exposes one instance
as a module-level name `INGESTOR`.

The rules the tool enforces:

- `axes` is a tuple, not a list, and must equal the axes configured for
  the chart in `hitlists`. If they differ, the chart fails with both
  lists named.
- `fetch(ref)` returns the edition for exactly that `ref`. Returning a
  different one is an `IngestError`.
- `size` is the edition's real size, even when you only have some of its
  entries. Scores are computed from it.
- Positions are whole numbers from 1 to `size`, each used once per
  edition.
- An entry lists several songs only when the source itself lists them
  separately, as with a double A-side. Don't split a title such as
  "Side A / Side B" yourself; deciding whether that is one song or two is
  left to the dataset's song handling, not the ingestor.
- `editions()` may be slow (it may need to fetch a page). The tool calls
  it once per run and ignores duplicates.

A violation of the data rules (a position out of range, an empty title)
raises `ValueError` from the class concerned, and the tool reports the
chart as "broke its contract", naming the problem.

## A complete example

Suppose you keep a station's yearly Christmas top 100 as one CSV file per
year, `kerst-2023.csv` and so on, each with a header row
`position,artist,title`. This ingestor reads them:

```python
"""Ingestor for the 'kerst' hitlist, from CSV files I keep by hand."""

import csv
import re
from pathlib import Path

from beetsplug.hitlisttag.ingest import (
    AcquiredEdition,
    EditionRef,
    IngestError,
    RawEntry,
    RawSong,
)

SOURCE = Path.home() / "charts" / "kerst-csv"
SIZE = 100


class KerstIngestor:
    chart = "kerst"
    axes = ("year",)

    def editions(self):
        years = []
        for path in sorted(SOURCE.glob("kerst-*.csv")):
            match = re.fullmatch(r"kerst-(\d{4})\.csv", path.name)
            if match:
                years.append(int(match.group(1)))
        return [EditionRef({"year": year}) for year in years]

    def fetch(self, ref):
        path = SOURCE / f"kerst-{ref.axes['year']}.csv"
        try:
            with path.open(encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
        except OSError as err:
            raise IngestError(f"cannot read {path}: {err}") from err
        try:
            entries = tuple(
                RawEntry(int(row["position"]), (RawSong(row["artist"], row["title"]),))
                for row in rows
            )
        except (KeyError, ValueError) as err:
            raise IngestError(f"{path}: malformed row: {err}") from err
        return AcquiredEdition(ref, SIZE, entries)


INGESTOR = KerstIngestor()
```

Save it as `kerst.py` in your ingestor folder, make sure `kerst` is in
`hitlists` (it is one of the defaults), and run:

```
beet chartsacquire kerst
```

## Where the script goes

The tool looks in two places:

- **Bundled ingestors**, in the plugin's own `ingestors` package. That is
  where `top2000` lives.
- **Your folder**, set by the `ingestor_dir` option. It defaults to
  `ingestors` under the beets configuration directory, for example
  `~/.config/beets/ingestors/`.

```yaml
hitlisttag:
  ingestor_dir: ~/charts/ingestors
  hitlists:
    kerst: [year]
```

In your folder:

- Every top-level `*.py` file is loaded. Subfolders aren't searched, and
  files whose name starts with `_` are skipped.
- Each script is loaded under its own private module name, so a script
  called `json.py` doesn't hide the real `json` module.
- The folder is not added to Python's import path, so one script can't
  `import` another. Keep each ingestor in one file.
- A script that fails to load, or that defines no valid `INGESTOR`, is
  skipped with a warning naming the file; the other ingestors still load.
- A script for a chart that a bundled ingestor also serves takes its
  place, with a notice. You can use this to fix or replace the Top 2000
  ingestor without waiting for a release.
- Two of your scripts claiming the same chart is an error, since there is
  no sensible way to pick one.

## Errors

| Situation | What to do | What the user sees |
| --- | --- | --- |
| The source is unreachable, returns an error, or changed its layout | Raise `IngestError` with a message that names the problem | `kerst: FAILED — <your message>; file unchanged` |
| The data breaks a rule (position out of range, empty title, …) | Nothing; the contract classes raise `ValueError` | `kerst: FAILED — ingestor for kerst broke its contract: …` |
| A bug in your script | Nothing | A normal Python traceback |

Whatever goes wrong, the chart's dataset file is left exactly as it was.

Wrap network errors yourself. If you use `requests`, catch
`requests.RequestException` and raise `IngestError` from it, as the
bundled `top2000` ingestor does. Otherwise a connection failure comes
out as a traceback. If you fetch from a public website, send a
User-Agent that names your script and a way to contact you, and check the
site's terms before automating requests to it.

## Testing without committing chart data

Chart listings generally can't be redistributed, so keep real data out
of any repository you share. Test an ingestor against small made-up
inputs instead:

```python
from beetsplug.hitlisttag.ingest import EditionRef, acquire_edition

import kerst  # your script, imported directly for the test


def test_reads_a_made_up_year(tmp_path, monkeypatch):
    (tmp_path / "kerst-2023.csv").write_text(
        "position,artist,title\n1,Made Up,Song One\n2,Also Made Up,Song Two\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(kerst, "SOURCE", tmp_path)

    refs = list(kerst.INGESTOR.editions())
    assert refs == [EditionRef({"year": 2023})]

    edition = acquire_edition(kerst.INGESTOR, refs[0])
    assert [e.position for e in edition.entries] == [1, 2]
```

`acquire_edition` is what `chartsacquire` calls, so a test that goes
through it checks your ingestor against the same contract the tool
enforces. For a parser of a downloaded page, save a trimmed or invented
copy of the page's structure as the fixture, with a handful of fake
entries, never a full real edition.

To try the whole chain locally, point `dataset_dir` at a scratch folder
and run `beet chartsacquire kerst` followed by `beet chartsgen`.

## Contributing an ingestor

If a chart is useful to others and its source allows automated access,
it can ship with the plugin. Put it in `beetsplug/hitlisttag/ingestors/`
with tests that use only made-up data, and open a pull request. See
[CONTRIBUTING.md](../CONTRIBUTING.md) for the checks to run first.
