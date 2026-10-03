# Writing an ingestor

An ingestor teaches `beet chartsacquire` how to fetch one chart. The
plugin ships one, for the Top 2000. For any other chart (a station's
Christmas list, a regional chart, a list you keep yourself) you write a
small Python script, put it in your ingestor folder, and run
`chartsacquire` as usual. You don't change the plugin.

Your script answers two questions: which editions of the chart exist,
and what one edition contains. `chartsacquire` does the rest: it works
out which editions are missing, assigns song ids, writes the dataset
file safely, and reports. Your script never touches files, ids or
configuration.

## Quick start: your first ingestor

This example reads a station's yearly Christmas top 100 from CSV files
you keep yourself, `kerst-2023.csv` and so on, each with a header row
`position,artist,title`.

1. Save this as `kerst.py` in your ingestor folder (by default
   `~/.config/beets/ingestors/`; see [Where scripts go](#where-scripts-go)):

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
           for path in sorted(SOURCE.glob("*.csv")):
               match = re.fullmatch(r"kerst-(\d{4})\.csv", path.name)
               if match:
                   years.append(int(match.group(1)))
           return [EditionRef({"year": year}) for year in years]

       def fetch(self, ref):
           path = SOURCE / f"kerst-{ref.axes['year']}.csv"
           try:
               with path.open(encoding="utf-8", newline="") as fh:
                   rows = list(csv.DictReader(fh))
           except (OSError, UnicodeDecodeError, csv.Error) as err:
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

2. Make sure the chart is in `hitlists` with the same axes as the
   script's `axes`. `kerst` is one of the defaults, so for this example
   there's nothing to add:

   ```yaml
   hitlisttag:
     hitlists:
       kerst: [year]
   ```

3. Fetch it:

   ```
   beet chartsacquire kerst
   ```

   The report line says how many editions were acquired. From here on,
   `chartsgen` tags matching tracks with `kerst` positions like any other
   chart.

## Test your ingestor without chart data

Chart listings generally can't be redistributed, so keep real data out
of any repository you share. Test against small made-up inputs instead:

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
through it checks your ingestor against the same rules the tool
enforces. For an ingestor that parses a downloaded web page, save a
trimmed or invented copy of the page's structure as the test input, with
a handful of fake entries, never a full real edition.

To try the whole chain, point `dataset_dir` at a scratch folder and run
`beet chartsacquire kerst`, then `beet chartsgen`.

## Fetch from a website

If your source is a web page or an API rather than local files:

- Catch network errors and raise `IngestError` from them. With
  `requests`, catch `requests.RequestException`, as the bundled
  `top2000` ingestor does. An uncaught connection error stops
  `chartsacquire` with a traceback instead of a clean failure line.
- Send a User-Agent that names your script and gives a way to contact
  you.
- Check the site's terms before you automate requests to it.

## Reference

### The contract

Everything an ingestor uses comes from `beetsplug.hitlisttag.ingest`:

| Name | What it is |
| --- | --- |
| `EditionRef(axes)` | The identity of one edition: its axis values, such as `EditionRef({"year": 2023})` or `EditionRef({"year": 2024, "week": 7})`. |
| `RawSong(artist, title)` | One credited song, spelled as published. Both must be non-empty. |
| `RawEntry(position, songs)` | One release at one rank. `songs` is a tuple of `RawSong`, usually just one. |
| `AcquiredEdition(ref, size, entries)` | One edition: the ref it answers, its real size, and a tuple of `RawEntry`. |
| `IngestError(message)` | Raise it when the source fails or changes. |

*Axes* are what identify an edition: just `year` for a yearly chart,
`year` and `week` for a weekly one.

The ingestor is any object with these four members. There is no base
class to inherit from:

```python
class MyIngestor:
    chart: str  # the hitlist name, as in your hitlists config
    axes: tuple[str, ...]  # its axis names in order, e.g. ("year",)

    def editions(self) -> Iterable[EditionRef]: ...

    def fetch(self, ref: EditionRef) -> AcquiredEdition: ...
```

The script exposes one instance of it under the module-level name
`INGESTOR`.

The rules `chartsacquire` enforces:

- `axes` is a tuple, not a list, and equals the chart's axes in
  `hitlists`. If they differ, the chart fails with both named.
- `fetch(ref)` returns the edition for exactly that `ref`; returning a
  different one is an error.
- `size` is the edition's real size, even if you have only some of its
  entries. Scores are computed from it.
- Positions are whole numbers from 1 to `size`, each used once per
  edition.
- An entry lists more than one song only when the source itself lists
  them separately, as with a double A-side. Don't split a title such as
  "Side A / Side B" yourself.
- `editions()` may be slow (it may need to fetch a page). It's called
  once per run, and duplicate refs are ignored.

### Where scripts go

`chartsacquire` looks in two places:

- **Bundled ingestors**, in the plugin's own `ingestors` package. That's
  where `top2000` lives.
- **Your ingestor folder**, set by `ingestor_dir`. It defaults to
  `ingestors` under the beets configuration directory, such as
  `~/.config/beets/ingestors/`:

  ```yaml
  hitlisttag:
    ingestor_dir: ~/charts/ingestors
  ```

How scripts in your folder are loaded:

- Every top-level `*.py` file is loaded. Subfolders aren't searched, and
  files whose name starts with `_` are skipped.
- Each script gets its own private module name, so a script called
  `json.py` doesn't hide the real `json` module.
- The folder isn't on Python's import path, so one script can't import
  another. Keep each ingestor in one file.
- A script that fails to load, or has no valid `INGESTOR`, is skipped
  with a warning naming the file. The other ingestors still load.
- A script for a chart a bundled ingestor also serves takes its place,
  with a notice. You can use this to fix or replace the `top2000`
  ingestor without waiting for a release.
- Two of your scripts for the same chart is an error, because there's
  no sensible way to pick one.

### Errors

| What happens | What you do | What the user sees |
| --- | --- | --- |
| The source is unreachable, returns an error, or changed its layout | Raise `IngestError` with a message naming the problem | `kerst: FAILED — <your message>; file unchanged` |
| The data breaks a rule (position out of range, empty title, …) | Nothing: the contract classes raise `ValueError` | `kerst: FAILED — ingestor for kerst broke its contract: …` |
| A bug in your script | Nothing | A Python traceback |

You can also catch a contract `ValueError` yourself and raise
`IngestError` with your own message, as the quick-start example does for
a malformed row. Either way the chart fails cleanly. Whatever goes
wrong, the chart's dataset file stays exactly as it was.

## Contributing an ingestor

If a chart is useful to others and its source allows automated access,
it can ship with the plugin. Put it in `beetsplug/hitlisttag/ingestors/`
with tests that use only made-up data, and open a pull request. See
[CONTRIBUTING.md](../CONTRIBUTING.md) for the checks to run first.
