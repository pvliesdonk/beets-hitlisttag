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
  year/week) from the library, including a report of missing positions.

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

## The `CHARTS` tag

The plugin reads chart history from a custom `CHARTS` file tag. The tag
is not part of any standard tagging scheme — it is expected to be written
by an external tagging tool; this plugin reads it, exposes it as the
`charts` field, and materializes it into queryable fields. It is stored
as:

- a `TXXX` frame with description `CHARTS` in MP3 (ID3) files;
- a freeform atom `----:nl.liesdonk.tagger:CHARTS` in MP4/M4A files;
- a plain `CHARTS` field in other formats (FLAC, Vorbis, …).

The tag value is a JSON array of chart objects. Each object has five
required keys:

| Key          | Type            | Meaning                                                                 |
| ------------ | --------------- | ----------------------------------------------------------------------- |
| `name`       | string          | Chart name, e.g. `top2000` — matched against the configured hitlists     |
| `score`      | integer         | Aggregate score for the song in this chart, stored and shown as-is       |
| `highest`    | integer         | Best (lowest-numbered) position the song ever reached in this chart      |
| `chart_type` | list of strings | The chart's axes, e.g. `["year"]` or `["year", "week"]`                  |
| `positions`  | object          | Nested positions, one nesting level per axis (see below)                 |

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
year, while the two-axis `top40` takes a year and a week.

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
