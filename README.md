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
- a `chartsgen` command that generates `CHARTS` tags from a local chart
  dataset you author or acquire yourself (see
  [The chart dataset](#the-chart-dataset)).

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

`chartsgen` generates `CHARTS` tags from a local dataset of chart
history. The plugin ships no chart data — complete chart listings
generally cannot be redistributed — so you author the dataset by hand or
acquire it yourself, and point the plugin at it with the `dataset_dir`
configuration key (see [Configuration](#configuration)).

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
official scoring method and is used as the default for every chart.
`highest` is the best (lowest-numbered) position across editions. In the
example above, Bohemian Rhapsody scores (2000 + 1 − 1) + (2000 + 1 − 2)
= 3999 with `highest` 1.

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
are absent from the library. The `-p` / `--path` flag prints file paths
instead of the formatted string. The `-f` / `--format` option overrides
the display format (default: `$artist - $album - $title`).

```
beet hitlist top2000 2023
beet hitlist -M top40 2024 5
```

### `chartsgen`

Generates `CHARTS` tags for matching items from the chart dataset (see
[The chart dataset](#the-chart-dataset); requires `dataset_dir` to be
configured).

```
beet chartsgen [QUERY]
```

Without a query it processes every item in the library. Each track's
artist and title are matched against the dataset's songs — exact
matching, insensitive to case, diacritics, punctuation, and whitespace —
and for every chart with a match, the chart's object in the track's
`CHARTS` tag is replaced wholesale from the dataset. Everything else in
the tag is left untouched: charts the dataset does not know, and charts
where this particular song has no match, survive unchanged, so
generation never destroys externally written data. An existing tag that
does not parse is treated as absent and overwritten (and reported).

Tracks with no unambiguous match get no generated data — they are
reported, never guessed at. The end-of-run report counts generated
tracks and lists unmatched tracks, ambiguous tracks (where distinct
dataset songs collapse onto the same normalized artist/title), tracks
whose metadata normalizes to nothing, unreadable files, files that
could not be written, and existing `CHARTS` tags that failed to parse. A track only counts as generated once its file
write succeeded; on a write failure neither the file nor the database is
touched.

Writing goes through beets' normal tag-writing machinery, so — like
`beet write` — it writes the item's media fields from the library's
values, and it materializes the same per-chart flexible fields as
`chartsupdate`, making generated tags indistinguishable downstream from
externally produced ones.

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

The chart dataset used by `chartsgen` is located by the `dataset_dir`
key (unset by default; `chartsgen` errors until it is configured). A
relative path is resolved against the beets configuration directory, and
`~` is expanded:

```yaml
hitlisttag:
  dataset_dir: ~/charts
```

A chart present in a file's `CHARTS` tag but absent from the configured
hitlists is still stored in the `charts` blob, but its per-chart
flexible fields are not materialized — only configured hitlists get
queryable `name`, `name_score`, `name_highest`, and `name_when` fields.

## Status

Released on PyPI as
[beets-hitlisttag](https://pypi.org/project/beets-hitlisttag/). See
`docs/roadmap.md` and the repository's milestones and issues for
direction and progress.

## License

MIT — see `LICENSE`.
