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

## Status

Working prototype being turned into a clean, tested, installable package.
See `docs/roadmap.md` and the repository's milestones and issues for
direction and progress. Not yet installable from PyPI.

## License

MIT — see `LICENSE`.
