# beets-hitlisttag: roadmap index

> **Agent-authored synthesis, not a record of decisions.** Items are tagged
> `stated` (the user said it), `derived` (agent synthesis, revisable at any
> refinement), or `evidenced` (read from code, with a locator). Milestones and
> issues live in the GitHub repository
> (<https://github.com/pvliesdonk/beets-hitlisttag>); this document holds the
> argument only — direction, ordering, and known unknowns. It never holds
> state: issue counts, progress, and dependencies belong to GitHub.

## What this is

A beets plugin that stores chart ("hitlist") history for songs — positions per
year or per year/week in charts such as the Top 2000 or Top 40 — in a custom
`CHARTS` file tag holding JSON, and makes that data usable inside beets:
queryable fields, chart display, and reconstruction of a full chart for a
given year/week from the library. `stated` (purpose), `evidenced` (mechanics:
`beetsplug/charts.py`, `beetsplug/hitlisttag.py`).

## Direction

- Turn the working prototype into a clean, installable, tested package
  modeled on [beets-plex](https://github.com/pvliesdonk/beets-plex):
  PEP 420 `beetsplug` namespace, hatchling + hatch-vcs, ruff, pytest with
  audio fixtures, CI on every PR, release automation. `stated` (the model),
  `derived` (the specific ingredients, read from beets-plex's layout).
- Keep the existing two-layer design — JSON in a `CHARTS` media tag, parsed
  into a typed model, materialized into queryable flexible database fields —
  rather than redesigning it. The design is sound for beets; the defects are
  in execution (type-layer inconsistencies, hardcoded chart definitions,
  unpackaged imports), not in the shape. `derived`.
- Chart definitions (which hitlists exist, their axes) become user
  configuration instead of constants in code. `derived` from the hardcoded
  `HITLISTS_DEFINITION` at `beetsplug/hitlisttag.py:15` and the user's ask
  for "better/cleaner"; the user has not explicitly requested configurability.

## Milestones and order

The ordering argument is information gain, not just dependency.

1. **Package scaffolding** first: it is cheap, it resolves the largest
   unknown of everything after it (whether the plugin loads at all when
   properly packaged — today a sibling absolute import suggests it cannot,
   see the bug issues), and it produces the test harness every later
   milestone needs to state its own acceptance honestly. `derived`.
2. **Chart model correctness** before **command robustness**: the commands
   sit on the model, so fixing the model first means command tests are
   written once against corrected behavior instead of twice. `derived`.
3. **Configurable chart definitions** after the field-materialization
   research resolves (see unknowns): the shape of the config surface depends
   on how per-chart fields are produced. `derived`.
4. **Release automation** last: lowest information gain, independent of the
   rest, and pointless before there is something worth installing. `derived`.

## Known unknowns

- **Field materialization strategy.** The live code materializes per-chart
  flexible fields via an explicit `chartsupdate` command; an abandoned
  variant (`beetsplug/hitlisttag.py.template_funcs`, `evidenced`) computed
  them on the fly as template fields. Materialized fields are queryable but
  can go stale; computed fields are always fresh but interact differently
  with queries. *Resolved by:* the research issue in the tracker (blocks
  refining the configurable-definitions milestone).
- **Scope of the non-chart fields.** `my_song_id`, `backup_artist`, and
  `backup_title` (`beetsplug/charts.py:246-267`, `evidenced`) belong to an
  external tagger ecosystem (`nl.liesdonk.tagger`). Whether this plugin owns
  them or they move elsewhere is the user's call. *Resolved by:* user
  decision; flagged in the session handoff. Until answered, work does not
  remove them.
- **The producer of the `CHARTS` tag.** Some external tool writes the tag
  this plugin consumes; its format stability is unknown. Not knowing does not
  change the next steps (the parser must be defensive either way), so this is
  recorded, not ticketed. `derived`.
- **beets version floor.** beets-plex pins `beets>=2.12`; whether this
  plugin's API usage needs newer or older is unknown. *Resolved by:*
  scaffolding-milestone refinement, where CI pins an interpreter and beets
  version and the test suite becomes the evidence. `derived`.

## History

- 2026-07-31 — charted. Baseline source committed unmodified, including
  stale variant files; their removal is scaffolding-milestone work.
