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

If your source is a web page or an API, fetch it with `Fetcher` from
`beetsplug.hitlisttag.fetch`. It is what the bundled ingestors use, and it
takes care of the parts that are easy to get wrong:

- requests are at least one second apart (`Fetcher(min_interval=2.0)` for
  slower);
- connection errors, timeouts, 429 and 5xx responses are retried, honouring
  the site's `Retry-After`;
- it sends a User-Agent naming this plugin and its homepage
  (`Fetcher(user_agent="my-script/1.0 (me@example.org)")` to name yours);
- every failure becomes an `IngestError`, so `chartsacquire` reports it as
  a clean failure line.

`get(url)` returns the page text, decoded with the charset the site
declares (UTF-8 when it declares none), or `None` when the page does not
exist (HTTP 404). What a missing page means is up to your ingestor: below, it
marks the end of the years the site has.

This example reads a site that publishes one CSV file per year:

```python
"""Ingestor for the 'zwaarstelijst' hitlist, one CSV file per year."""

import csv
import io

from beetsplug.hitlisttag.fetch import Fetcher
from beetsplug.hitlisttag.ingest import (
    AcquiredEdition,
    EditionRef,
    IngestError,
    RawEntry,
    RawSong,
)

BASE = "https://charts.example.org/zwaarstelijst"
FIRST_YEAR = 2005
SIZE = 100


class ZwaarsteIngestor:
    chart = "zwaarstelijst"
    axes = ("year",)

    def __init__(self):
        self._fetcher = Fetcher()
        self._pages = {}

    def _csv(self, year):
        # Each page is fetched once per run, even though editions() and
        # fetch() both need it.
        if year not in self._pages:
            self._pages[year] = self._fetcher.get(f"{BASE}/{year}.csv")
        return self._pages[year]

    def editions(self):
        years = []
        year = FIRST_YEAR
        while self._csv(year) is not None:
            years.append(year)
            year += 1
        return [EditionRef({"year": y}) for y in years]

    def fetch(self, ref):
        year = ref.axes["year"]
        text = self._csv(year)
        if text is None:
            raise IngestError(f"the site has no list for {year}")
        try:
            entries = tuple(
                RawEntry(int(row["position"]), (RawSong(row["artist"], row["title"]),))
                for row in csv.DictReader(io.StringIO(text))
            )
        except (KeyError, ValueError) as err:
            raise IngestError(f"malformed row in {year}: {err}") from err
        return AcquiredEdition(ref, SIZE, entries)


INGESTOR = ZwaarsteIngestor()
```

If the site's certificate chain is incomplete, pass the missing
intermediate certificate as `Fetcher(extra_ca_pem=...)`; verification stays
on. Never turn verification off.

While you work on a parser, set `HITLISTTAG_HTTP_CACHE` to a directory
outside your projects. `Fetcher` then keeps every page it fetches there as
a plain file, under the site's host and path, and serves it from there on
the next run, so you can re-run as often as you like without touching the
site. Delete the directory to fetch fresh copies. Leave the variable unset
for normal runs.

Check the site's terms and its `robots.txt` before you automate requests
to it.

## Reference

### The contract

Everything an ingestor uses comes from `beetsplug.hitlisttag.ingest`:

| Name | What it is |
| --- | --- |
| `EditionRef(axes)` | The identity of one edition: its axis values, such as `EditionRef({"year": 2023})` or `EditionRef({"year": 2024, "week": 7})`. |
| `RawSong(artist, title)` | One credited song, spelled as published. Both must be non-empty. |
| `RawEntry(position, songs, source_ids={})` | One release at one rank. `songs` is a tuple of `RawSong`, usually just one. `source_ids` is optional; see below. |
| `AcquiredEdition(ref, size, entries)` | One edition: the ref it answers, its real size, and a tuple of `RawEntry`. |
| `IngestError(message)` | Raise it when the source fails or changes. |

*Axes* are what identify an edition: just `year` for a yearly chart,
`year` and `week` for a weekly one.

If the source publishes its own identifiers for an entry, such as an id in
the link to the song's page, pass them as `source_ids`, keyed
`<source>/<kind>`: `{"top40.nl/title": "8522", "top40.nl/version": "7417"}`.
Keys and values are non-empty strings, recorded as the source gives them.
They are stored with the entry and never change which song it resolves to;
they are there so that later curation can check whether two entries are
really the same song. Leave them out if the source has none.

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
- An entry lists more than one song when the source credits several songs
  at one rank, as with a double A-side. Split a published name only by a
  convention you know your source follows: top40.nl writes double A-sides
  as `Side A ; Side B` and versions sharing a position as
  `Artist A / Artist B`, but on other sources (Wikipedia's Top 2000) a
  `" / "` is part of real titles such as *Laat me / vivre*. Each song is
  then credited with the entry's position.
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
| The source is unreachable, returns an error, or changed its layout | Raise `IngestError` with a message naming the problem | From `editions()`: `kerst: FAILED — <your message>; file unchanged`. From `fetch()`: `kerst: 1 edition failed (1999): <your message>; a later run retries them` |
| The data breaks a rule (position out of range, empty title, …) | Nothing: the contract classes raise `ValueError` | As above, with `ingestor for kerst broke its contract: …` as the message |
| A bug in your script | Nothing | `kerst: FAILED — ingestor for kerst raised KeyError: 'rank'; …`, and the traceback with `beet -v chartsacquire` |

Each edition that fails in `fetch()` is also logged as a warning with
its own message, such as `kerst: 1999 failed: <your message>`.

You can also catch a contract `ValueError` yourself and raise
`IngestError` with your own message, as the quick-start example does for
a malformed row. Either way the failure is reported cleanly, and the
other charts in the run still go ahead.

A failure while listing editions leaves the chart's dataset file exactly
as it was. A failure while fetching depends on its kind: an
`IngestError` or contract `ValueError` fails just that edition, and the
chart carries on with the next one. A bug ends the chart. Either way,
the editions acquired before it are kept.

## Contributing an ingestor

If a chart is useful to others and its source allows automated access,
it can ship with the plugin. Put it in `beetsplug/hitlisttag/ingestors/`
with tests that use only made-up data, and open a pull request. See
[CONTRIBUTING.md](../CONTRIBUTING.md) for the checks to run first.
