# beets-hitlisttag

A [beets](https://beets.io) plugin that stores chart ("hitlist") history for
songs — positions per year or per year/week in charts such as the Dutch
Top 2000, Top 40, or Top 100 — in a custom `CHARTS` file tag holding JSON,
and makes that data usable inside beets.

It provides:

- a typed `charts` media field read from and written to file tags;
- a `chartsupdate` command that parses the `CHARTS` tag into queryable
  per-chart flexible fields (`top2000`, `top2000_score`, `top2000_highest`,
  `top2000_when`, …);
- a `charts` command showing a song's chart history (summary or full
  positions);
- a `hitlist` command that reconstructs a full chart for a given year (or
  year/week) from the library, including a report of missing positions;
- a `chartsacquire` command that fills a local chart dataset from public
  sources, one chart at a time, through pluggable ingestors;
- a `chartsgen` command that generates `CHARTS` tags from that dataset
  (see [The chart dataset](#the-chart-dataset));
- a `chartscatalog` command that checks the song catalog against the
  dataset (see [The song catalog](#the-song-catalog)).

## Installation

Install the plugin into the same Python environment as beets:

```
pip install beets-hitlisttag
```

(If beets is installed via pipx, use `pipx inject beets beets-hitlisttag`
instead.)

Then enable it by adding `hitlisttag` to the `plugins` section of your
beets `config.yaml`:

```yaml
plugins:
  - hitlisttag
```

(beets also accepts the space-separated form, `plugins: hitlisttag`.)

## The `CHARTS` tag

The plugin stores chart history in a custom `CHARTS` file tag. The tag
is not part of any standard tagging scheme. This plugin both reads and
generates it: tags written by an external tagging tool are read, exposed
as the `charts` field, and materialized into queryable fields, and the
`chartsgen` command writes the same tag from a local chart dataset —
generated and externally written tags are indistinguishable to the rest
of the plugin. It is stored as:

- a `TXXX` frame with description `CHARTS` in MP3 (ID3) files;
- a freeform atom `----:nl.liesdonk.tagger:CHARTS` in MP4/M4A files;
- a plain `CHARTS` field in other formats (FLAC, Vorbis, …).

The tag value is a JSON array of chart objects. Each object has five
required keys:

| Key | Type | Meaning |
| --- | --- | --- |
| `name` | string | Chart name, e.g. `top2000` — matched against the configured hitlists |
| `score` | integer | Aggregate score for the song in this chart, stored and shown as-is |
| `highest` | integer | Best (lowest-numbered) position the song ever reached in this chart |
| `chart_type` | list of strings | The chart's axes, e.g. `["year"]` or `["year", "week"]` |
| `positions` | object | Nested positions, one nesting level per axis (see below) |

`positions` is keyed by axis values (JSON object keys, so numbers appear
as strings), nested in the order given by `chart_type`, with the chart
position as the leaf value. For example:

```json
[
  {
    "name": "top2000",
    "score": 3407,
    "highest": 231,
    "chart_type": ["year"],
    "positions": {"2021": 480, "2022": 231, "2023": 305}
  },
  {
    "name": "top40",
    "score": 120,
    "highest": 3,
    "chart_type": ["year", "week"],
    "positions": {"1997": {"20": 15, "21": 8, "22": 3}}
  }
]
```

This says the song stood at position 480, 231, and 305 in the Top 2000
of 2021–2023, and spent three weeks in the Top 40 in 1997 (weeks 20–22,
peaking at 3). A chart object missing any of the five keys, or with a
value of the wrong type, is rejected when the tag is parsed.

## The chart dataset

### Quick start

To tag your library with its Top 2000 history:

1. Set a dataset folder in your beets `config.yaml`:

   ```yaml
   hitlisttag:
     dataset_dir: ~/charts
   ```

2. Fetch every edition since 1999 (one request to Wikipedia):

   ```
   beet chartsacquire top2000
   ```

3. Write `CHARTS` tags to the tracks that match:

   ```
   beet chartsgen
   ```

4. See a year's chart as your library has it:

   ```
   beet hitlist top2000 2024
   ```

Run step 2 again after each December's edition, then step 3.

### How the dataset works

`chartsgen` generates `CHARTS` tags from a local dataset of chart
history. The plugin ships no chart data — complete chart listings
generally cannot be redistributed — so each user builds the dataset on
their own machine: `chartsacquire` fetches it from public sources (see
[`chartsacquire`](#chartsacquire)), and you can also author files by hand.
Point the plugin at the dataset with the `dataset_dir` configuration key
(see [Configuration](#configuration)).

The dataset is one JSON file per hitlist, discovered recursively under
`dataset_dir` (only lowercase `*.json` files are read; symlinked
directories are not followed). Each file declares:

- `chart` — the hitlist name, matching a configured hitlist. Files for
  unconfigured charts are skipped with a warning; two files declaring the
  same chart are an error.
- `songs` — an object mapping a file-local song id to `{artist, title}`.
  A song that appears in several editions is stored once and referenced
  by id.
- `editions` — a list of editions. Each declares `axes` (an object keyed
  by the chart's configured axis names, positive-integer values), `size`
  (the number of ranks), and `entries`. An entry is one release at a
  rank: a `position` in `1..size` and a non-empty list of song ids — a
  release crediting more than one song (a double A-side) lists them all
  at its single rank. Positions and songs are each unique within an
  edition.
- An entry may also carry `source_ids`: the identifiers the chart's source
  publishes for it, such as `{"top40.nl/title": "8522"}`. They are raw
  information kept for later curation and do not affect matching.
  `chartsacquire` writes them when an ingestor provides them; hand-authored
  files can leave them out.

```json
{
  "chart": "top2000",
  "songs": {
    "1": {"artist": "Queen", "title": "Bohemian Rhapsody"},
    "2": {"artist": "Danny Vera", "title": "Roller Coaster"}
  },
  "editions": [
    {
      "axes": {"year": 2022},
      "size": 2000,
      "entries": [
        {"position": 1, "songs": ["1"]},
        {"position": 2, "songs": ["2"]}
      ]
    },
    {
      "axes": {"year": 2023},
      "size": 2000,
      "entries": [
        {"position": 2, "songs": ["1"]}
      ]
    }
  ]
}
```

Editions may be partial — hand-authoring just the songs you care about
is fine — but `size` must be the edition's real size, because scores are
computed from it (see below).

For generated tags, `score` is a positional-points sum: a placement at
position *p* in an edition of declared size *N* contributes *N* + 1 −
*p*, summed over every edition the song appears in. This is the Top 40's
official scoring method; whether any other chart defines an official
method of its own is not assumed either way, so the plugin applies this
formula as the default for every chart.
`highest` is the best (lowest-numbered) position across editions. In the
example above, Bohemian Rhapsody scores (2000 + 1 − 1) + (2000 + 1 − 2)
= 3999 with `highest` 1.

### The song catalog

The dataset's raw files are rewritten by every `chartsacquire` run and hold
names as each source published them, so the same song can sit under
several spellings across editions and charts. The song catalog is the layer
above them: `catalog.json` in `dataset_dir`, written by the plugin's
`chartscatalog` commands and not meant for hand-editing. It holds only
songs that carry a decision; everything else matches as described under
[`chartsgen`](#chartsgen).

A catalog song has a display name, `aliases` (spellings that resolve to it,
matched like `chartsgen` matches), and `links`: the raw songs, per chart,
whose chart positions are its own. A link records the raw song's id, name and
source ids as they were when the link was made. For source ids, that means the
keys on which every entry citing the raw song agrees, with their values.
Several links under one song merge spellings and charts into one history; the
same raw song linked from two songs is a split (a double A-side crediting
both).

```json
{
  "catalog": 1,
  "songs": {
    "1": {
      "artist": "Simon & Garfunkel",
      "title": "The Sound of Silence",
      "aliases": [{"artist": "Simon and Garfunkel", "title": "The Sounds of Silence"}],
      "links": [
        {"chart": "top40", "song": "412", "artist": "Simon & Garfunkel",
         "title": "The Sounds Of Silence", "source_ids": {"top40.nl/title": "9981"}},
        {"chart": "top2000", "song": "77", "artist": "Simon & Garfunkel",
         "title": "The Sound of Silence"}
      ]
    }
  }
}
```

Rules the file keeps: an alias belongs to one song; a song has at least one
link; a song links a raw song at most once. The name `catalog` is reserved:
don't configure a hitlist called `catalog`, because its dataset file would
take the catalog's path.

A track resolves in this order: an alias it spells; else the name of a raw
song that links to exactly one catalog song; else the plain match. A song's
history is the union over its links, so a track spelled like the Top 40's
entry still gets the Top 2000's positions once the two are linked.
`chartsgen` does not consult the catalog yet
([#165](https://github.com/pvliesdonk/beets-hitlisttag/issues/165)); today
only `chartscatalog check` reads it.

**After a re-acquisition.** A link points at a raw id; a forced or
from-scratch re-acquisition can re-mint ids or respell names. On every use
the plugin checks each link against the live data. A link is *bound* when its
id is still cited by an edition and still holds the recorded song: its source
ids agree (and, where several raw songs share those ids, its name does too),
or, without any source ids, its name agrees. Otherwise it is *re-bound* when
exactly one live raw song carries the recorded source ids (the recorded name
decides between several), or, failing that, exactly one has the recorded name.
Otherwise it is *dangling* and contributes nothing until you re-link it. Two
candidates never count as a match, and a link never re-binds onto a raw song
the same catalog song already links; it stays dangling. So Top 40 and Top 100
links survive even a from-scratch re-acquisition through top40.nl's title ids,
and where a title id names several raw songs (a cover bundle) the recorded
name decides between them. A Top 2000 link survives while Wikipedia's
spelling matches, and a respelled Top 2000 song shows up as dangling.

```
beet chartscatalog check
```

prints the counts and every re-bound and dangling link, writes re-bound
links back, and exits 1 when a link is dangling or when two raw songs with
one name link different catalog songs (a merge you may want). A missing
catalog is an empty one.

## Usage

### `chartsupdate`

Reads the `CHARTS` tag from matching items and materializes per-chart
flexible fields (`top2000`, `top2000_score`, `top2000_highest`,
`top2000_when`, …) so they can be queried and sorted in beets.

```
beet chartsupdate [QUERY]
```

Run it after importing new music or when chart data changes. Without a
query it processes every item in the library.

### `charts`

Displays the chart history stored in a song's `CHARTS` tag.

```
beet charts [-F] [QUERY]
```

By default it prints a one-line summary per chart. The `-F` / `--full`
flag shows every position in a table.

### `hitlist`

Reconstructs a full chart for a given hitlist and year (or year/week)
from the library, listing songs in position order.

```
beet hitlist [-M] [-p] [-f FORMAT] HITLIST YEAR [WEEK]
```

The available hitlists are defined in configuration (see
[Configuration](#configuration)); the shipped defaults are `top2000`,
`top100`, `top40`, `zwaarstelijst`, and `kerst`. All take a year; `top40`
also takes a week. The `-M` / `--missing` flag reports any positions that
are absent from the library. When `dataset_dir` is set and the chart
dataset holds that edition, the report runs up to the edition's declared
size: it also lists positions above the highest one in the library, and the
whole edition when the library holds none of it; a library position beyond
the declared size is listed but does not widen the report. Otherwise the
edition's size is unknown and the report runs only up to the highest
position found, which it says. A dataset that cannot be read gives a
warning and the same fallback. The `-p` / `--path` flag prints file paths
instead of the formatted string. The `-f` / `--format` option overrides the
display format (default: `$artist - $album - $title`).

```
beet hitlist top2000 2023
beet hitlist -M top40 2024 5
```

### `chartsacquire`

Fetches chart editions from public sources into the dataset. Requires
`dataset_dir`. Each chart is fetched by an *ingestor*, a small piece of
Python that knows one chart's source; `top2000`, `top100` and `top40`
ship with the plugin.

```
beet chartsacquire [--force [--prune]] [CHART ...]
```

With no `CHART`, it acquires every chart that has both an ingestor and a
`hitlists` entry. A plain run fetches only the editions the chart's
dataset file doesn't have yet. Run it again after a new edition is
published and it adds just that one; with nothing new, it reports the
chart as up to date and doesn't touch the file.

| Option | Effect |
| --- | --- |
| `-f`, `--force` | Re-acquire every edition the source lists, replacing the ones the file already has. Use it after the source corrects an edition, or to turn a hand-made partial edition into the full one. |
| `--prune` | Only with `--force`. Also drop editions the source no longer lists (without it, they're kept). Refused if the source lists no editions at all. |

Song ids stay the same across runs: a song whose artist and title are
already in the file keeps its id (compared the way `chartsgen` matches,
ignoring case, diacritics and punctuation; a name that is nothing but
punctuation, like a song titled `?`, is compared as written). Songs are
never deleted.

An edition that can't be fetched or read is skipped, and the chart
carries on with the next one; after 3 failed editions in a row it stops,
since the source is probably down or has changed. What was acquired is
kept: during a long run the file is written about once a minute, again
at the end, and when you interrupt it with Ctrl-C, each time completely
or not at all. A later run fetches whatever is still missing, so it
retries the failed editions. Other charts in the same run still go
ahead, and the command exits with an error naming the charts that had
failures. If the source can't even list its editions, the chart fails
and its file is left exactly as it was. A chart is also refused before
anything is fetched, leaving its file as it was, when:

- `dataset_dir/<chart>.json` holds a different chart: rename or move
  that file;
- the chart's file has fields the [dataset format](#the-chart-dataset)
  doesn't define: remove them, or keep that chart hand-maintained.

A bug in an ingestor (an unexpected Python error) fails only that chart,
keeping what it acquired: the report names the error,
`beet -v chartsacquire` shows its traceback, and the other charts still
run.
Ctrl-C stops the whole run, after printing what the interrupted chart's
file kept. If a folder under `dataset_dir` can't be read, nothing is
fetched at all, since a chart's file might be in it and the command
would otherwise start a second one.

Each chart gets a report line:

```
top2000: acquired 27 editions (1999–2025), 54,000 entries, 4,925 new songs
top2000: re-acquired 27 editions (1999–2025), 54,000 entries, 0 new songs
top2000: up to date (27 editions)
top2000: 2 editions in the file are not listed by the source (1990–1991); kept
top2000: dropped 2 editions not listed by the source (1990–1991)
top100: 2 editions failed (1965, 1971): <reason>; a later run retries them
top40: 3 editions failed (1990 week 7, 2005 weeks 14–15): <reason>; a later run retries them
top100: stopped after 3 failed editions in a row; 40 editions not attempted
kerst: FAILED — <reason>; file unchanged
kerst: FAILED — <reason>; file keeps the editions written before it
top40: interrupted; file keeps the editions written before it
```

The *acquired* line counts only editions that reached the file. When a
failure or Ctrl-C comes before the latest ones were written, the last
line ends with how many were lost, e.g. `; 3 acquired editions not
saved`; a later run fetches them again.

**The `top2000` ingestor** reads the consolidated table on Dutch
Wikipedia, *Lijst van Radio 2-Top 2000's*: every edition since 1999, in
one request, licensed CC BY-SA. It doesn't use the broadcaster's own
site, whose terms forbid automated retrieval. A year that isn't complete
on Wikipedia yet (while the new edition is being entered each December)
is skipped with a warning and picked up by a later run. Titles use
Wikipedia's spelling, which is the same across years and leaves out the
broadcaster's "(Albumversie)" markers.

**The `top100` and `top40` ingestors** read top40.nl, the site of the
Stichting Nederlandse Top 40:

- `top100`: the *Top 100 jaaroverzichten*, one page per year since 1965
  (61 years to 2025). A first run takes about a minute.
- `top40`: the weekly Top 40, one page per week since 2 January 1965
  (3,194 weeks to October 2026). A first run takes about an hour and
  writes a dataset file of about 28 MB; later runs fetch only the new
  weeks.

Both fetch at most one page per second (a retry after a server error
can follow sooner) and identify themselves as `beets-hitlisttag/<version>`
with this repository's URL. There is no setting for either.

The site reserves copyright and database rights over its charts. What
`chartsacquire` stores is your private copy: don't publish or share the
dataset files.

Entries are stored as the site publishes them. The site merges versions
of a song into one entry (`Artist A / Artist B`, `Title ((1965))`) and
lists double A-sides as `Side A ; Side B`. `chartsgen` doesn't connect
these entries to your tracks; telling which song an entry means is
planned work ([roadmap](docs/roadmap.md), song ontology and curation).
When a song comes back spelled differently only in case, accents or
punctuation, it keeps the spelling it was first stored with. Each entry
also keeps the site's own ids in `source_ids`.

The Top 100 is the site's current list, recomputed from the weekly
charts, and can differ from the list printed at the time.

`top40` uses the site's week numbers. A year has 51, 52 or 53 weeks,
and in 1982, 1983, 1988, 1993, 1994, 1997, 1998, 1999, 2000 and 2005 the
first chart of the year is week 2, so `beet hitlist top40 1982 1`
reports no positions. Week pages shorten long names with `..`; the ingestor restores
the full name from the same page, or keeps the shortened one with a
warning when it can't.

**Other charts** need an ingestor of your own: a script in
`ingestor_dir` plus a `hitlists` entry, with no change to the plugin.
See [Writing an ingestor](docs/writing-an-ingestor.md).

### `chartsgen`

Generates `CHARTS` tags for matching items from the chart dataset (see
[The chart dataset](#the-chart-dataset); requires `dataset_dir` to be
configured).

```
beet chartsgen [QUERY]
```

Without a query it processes every item in the library. Each track's
artist and title are matched against the dataset's songs — exact
matching, insensitive to case, diacritics, punctuation, and whitespace,
and to three differences between sources that are not differences
between songs: a `((…))` disambiguator in a name (top40.nl's
`The Scorpions ((GBR))`), `&` against `and`, and one leading `The`
(unless it is the whole name). Anything else — a qualifier such as
`(live)` or `- Remix`, `feat.` against `featuring`, a Dutch `De` — is a
different name to the matcher. For every chart with a match, the chart's object in the track's
`CHARTS` tag is replaced wholesale from the dataset. Everything else in
the tag is left untouched: charts the dataset does not know, and charts
where this particular song has no match, survive unchanged, so
generation never destroys externally written data. An existing tag that
does not parse is treated as absent: if the track gets any generated
data, the whole tag is replaced by it; if the track matches nothing, the
unparseable tag is left on disk untouched. Either way the parse failure
is reported.

Tracks with no unambiguous match get no generated data — they are
reported, never guessed at. The end-of-run report counts generated
tracks and lists unmatched tracks, ambiguous tracks (where distinct
dataset songs collapse onto the same normalized artist/title), tracks
whose metadata normalizes to nothing, unreadable files, files that
could not be written, and existing `CHARTS` tags that failed to parse.
A track only counts as generated once its file holds the generated
data; on a write failure neither the file nor the database is touched.

A track whose file already holds exactly the generated chart data isn't
written again; only its database entry is brought up to date. So a run
after nothing changed leaves your files alone, and the report adds a
line such as `Already up to date, not rewritten: 5198.`

Writing goes through beets' normal tag-writing machinery, so — like
`beet write` — it writes the item's media fields from the library's
values, and it materializes the same per-chart flexible fields as
`chartsupdate`, making generated tags indistinguishable downstream from
externally produced ones. A track that isn't rewritten doesn't get its
other media fields written either; use `beet write` for that.

### `chartscatalog`

Checks the song catalog (see [The song catalog](#the-song-catalog))
against the dataset; requires `dataset_dir` to be configured.

```
beet chartscatalog check
```

## Keeping the dataset current

Run `chartsacquire` and then `chartsgen` on a schedule to keep your
dataset and tags current. New editions keep appearing: a Top 40 every
week, a Top 100 after each year, a Top 2000 each December.

1. Note the full path of the `beet` executable, for example
   `/home/you/.local/bin/beet`; cron doesn't use your shell's `PATH`.
   If you point beets at its configuration with `BEETSDIR` or
   `XDG_CONFIG_HOME` in a shell startup file, cron won't see that
   either: set it in the crontab, or pass `-c /path/to/config.yaml` to
   each `beet`.
2. Add a weekly job with `crontab -e`, for example Sunday at 09:30:

   ```
   30 9 * * 0  /path/to/beet chartsacquire; /path/to/beet chartsgen > ~/charts/chartsgen.log
   ```

   Use `;` rather than `&&`: `chartsacquire` exits with an error when
   any chart had a failure, and `chartsgen` should still run for the
   charts that succeeded. `chartsgen`'s report lists every track it
   couldn't match, so it goes to a log file, in a folder that must
   already exist; its errors still reach cron's mail.
3. Check the output cron mails you. If your system doesn't deliver
   cron mail, append `>> ~/charts/acquire.log 2>&1` to the
   `chartsacquire` command and read that instead. A week with one new
   Top 40 chart looks like this:

   ```
   top100: up to date (61 editions)
   top2000: up to date (27 editions)
   top40: acquired 1 edition, 40 entries, 2 new songs
   ```

A run fetches only what the dataset lacks, so the day doesn't matter:
a run before a new chart is published picks it up the week after.

**If a chart fails.** A `FAILED` line or *editions failed* usually
means its source was down or has changed. The *editions failed* line
names the editions and gives the last one's reason; each failed edition
is also logged as a warning with its own reason, such as
`top40: 1990 week 7 failed: Top 40 1990 week 7: page not found`. The
next run retries what is missing. If an ingestor itself crashed,
`beet -v chartsacquire CHART` shows the traceback.

**The Top 2000 in December** is skipped until Wikipedia has the whole
new edition (see the `top2000` ingestor above).

**A corrected edition.** A source may correct an edition it has already
published. The plugin can't detect that, since a plain run never
fetches an edition the file already has. When you know of a correction,
re-acquire that chart with `beet chartsacquire --force CHART`. For
`top40` that fetches every week again, about an hour; don't start it
while the scheduled job may run, since whichever of the two finishes
last replaces the other's file.

## Configuration

Which hitlists exist and what axes each one uses are defined by the
`hitlists` key under the `hitlisttag` section of `config.yaml`. It maps
a hitlist name to a list of axis names — the arguments `hitlist` expects
for that chart:

```yaml
hitlisttag:
  hitlists:
    top2000: [year]
    top100: [year]
    top40: [year, week]
    zwaarstelijst: [year]
    kerst: [year]
```

Omitting the key falls back to the defaults above. A hitlist takes one
argument per axis, in order; a single-axis hitlist like `top2000` takes a
year, while the two-axis `top40` takes a year and a week. An entry whose
axes are not a non-empty list of strings is dropped with a warning.

The chart dataset used by `chartsgen`, `chartsacquire` and `hitlist -M`
is located by the `dataset_dir` key (unset by default; `chartsgen` and
`chartsacquire` error until it is configured, and `hitlist -M` falls back
to the highest position found). A relative path is resolved against the
beets configuration directory, and `~` is expanded:

```yaml
hitlisttag:
  dataset_dir: ~/charts
```

The song catalog lives at `dataset_dir/catalog.json`; `catalog` is a reserved
hitlist name.

Ingestor scripts of your own are read from `ingestor_dir`, which
defaults to `ingestors` under the beets configuration directory (for
example `~/.config/beets/ingestors/`) and resolves the same way:

```yaml
hitlisttag:
  ingestor_dir: ~/charts/ingestors
```

A chart present in a file's `CHARTS` tag but absent from the configured
hitlists is still stored in the `charts` blob, but its per-chart
flexible fields are not materialized — only configured hitlists get
queryable `name`, `name_score`, `name_highest`, and `name_when` fields.
`beet -v` notes each such chart; a normal run says nothing about them.

## Status

Released on PyPI as
[beets-hitlisttag](https://pypi.org/project/beets-hitlisttag/). See
`docs/roadmap.md` and the repository's milestones and issues for
direction and progress.

## License

MIT — see `LICENSE`.
