# beets-hitlisttag — repository guide

A beets plugin that stores per-song chart ("hitlist") history — positions per
year or per year/week — in a custom `CHARTS` file tag holding JSON, and makes
it queryable inside beets.

## Roadmap

The roadmap index is at `docs/roadmap.md`; milestones and issues live in this
repository on GitHub. Read the index before planning work here, and update it
when direction changes. Work starts from a refined, ready feature issue —
never from a milestone or the index.

## Layout

- `beetsplug/` — plugin source. Working prototype imported as-is in the
  baseline commit; the stale `.backup`/`.old`/`.template_funcs` variants were
  removed in the scaffolding milestone. Packaging, tests, and CI are also
  scaffolding-milestone work, modeled on
  [beets-plex](https://github.com/pvliesdonk/beets-plex).

## Conventions

- **Conventional Commits** (`type: summary`), as in beets-plex.
- Small PRs, one at a time off `main`, each closing at least one issue.
