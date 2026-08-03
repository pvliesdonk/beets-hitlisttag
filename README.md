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
beet hitlist [-M] [-f FORMAT] HITLIST YEAR [WEEK]
```

The built-in hitlists are `top2000`, `top100`, `top40`,
`zwaarstelijst`, and `kerst`. All take a year; `top40` also takes a
week. The `-M` / `--missing` flag reports any positions that are absent
from the library. The `-f` / `--format` option overrides the display
format (default: `$artist - $album - $title`).

```
beet hitlist top2000 2023
beet hitlist -M top40 2024 5
```

## Status

Working prototype being turned into a clean, tested, installable package.
See `docs/roadmap.md` and the repository's milestones and issues for
direction and progress. Not yet installable from PyPI.

## License

MIT — see `LICENSE`.
