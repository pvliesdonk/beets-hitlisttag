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
`beetsplug/hitlisttag/charts.py`, `beetsplug/hitlisttag/__init__.py`).

A second roadmap (2026-08-04) extends the ambition from consuming the tag to
producing it: acquiring chart data from public sources into a local,
library-independent dataset, and generating `CHARTS` tags for library tracks
from that dataset. `stated`.

## Direction

### Consuming CHARTS (first roadmap, delivered 2026-08-03)

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
  configuration instead of constants in code. `stated` (2026-07-31): getting
  charts into the database queryable/sortable generically was a major
  struggle, so the chart names were hardcoded as a forced workaround; a
  generic way is wanted. The user attributes the difficulty to the
  **mediafile layer** rather than beets itself (`stated`, correction). That
  constraint is real and current: mediafile 0.17.0 still registers custom
  tags one `MediaField` at a time, with explicit per-format storage styles,
  erroring on name collisions (`evidenced`: installed
  `mediafile.MediaFile.add_field` source). The existing design already
  routes around it — files carry a single generic `CHARTS` JSON tag, so
  genericity is needed only on the beets side, where `item_types` may be a
  computed property generating typed fields from the plugin's config
  (`evidenced`: beets stable docs, *Flexible Field Types*,
  <https://beets.readthedocs.io/en/stable/dev/plugins/other/fields.html>).
- Accepted limitation of config-driven definitions: a chart present in a
  file's `CHARTS` tag but absent from config is ignored for per-chart field
  materialization — "not a huge problem". `stated` (2026-07-31). The raw
  data survives in the `charts` blob either way; only the queryable
  projection is skipped (`evidenced`: the current hardcoded equivalent at
  `beetsplug/hitlisttag/__init__.py:209-213` logs and skips unknown chart names).
- The `my_song_id`, `backup_artist`, and `backup_title` fields are **out of
  scope** — they belong to the external `nl.liesdonk.tagger` ecosystem, not
  this plugin. `stated` (2026-07-31). Their removal is tracked as a work item
  in the chart-model-correctness milestone.

### Generating CHARTS (second roadmap, charted 2026-08-04)

- **Standalone.** Independent of the external `tagger` project. That project
  is reference material only — the user calls it suitable as a reference
  though not elegantly coded, and its future is uncertain — so nothing here
  may depend on it. `stated` (2026-08-04). Its old flow canonicalized library
  files via MusicBrainz and matched hitlist entries against them, which was
  inefficient and only worked because the library was near-complete per
  chart. `stated`.
- **Single package, acquisition included.** Acquisition tooling ships in this
  package as an additional executable, not as a separate distribution:
  without acquisition the generation side is useless, because chart data
  cannot be redistributed (see next item). `stated` (2026-08-04).
  *Wording revised by the user (2026-10-03, `stated`):* the bundled tool
  is a beets subcommand, `beet chartsacquire`, rather than a separate
  executable, so it reuses the plugin's configuration with no
  duplication. The substance — one package, acquisition bundled, no
  separate distribution — is unchanged (`evidenced`:
  [#100](https://github.com/pvliesdonk/beets-hitlisttag/issues/100)).
- **No chart data is shipped or published.** Complete publication of chart
  listings likely raises copyright problems for at least some lists.
  `stated` (2026-08-04). Consequence: each user regenerates the dataset
  locally from public sources; the project distributes the means, never the
  data. `derived`.
- **Acquisition is pluggable.** Per-chart *ingestors* — scrapers, API
  fetchers, file downloaders, however a chart's data is obtained — that
  third parties can add for their own hitlists without modifying this
  package. `stated` (generalized scrapers/fetchers/downloaders, 2026-08-04);
  `derived` (the ingestor framing as broader than scraping). The
  pluggability promise includes developer documentation: how to implement
  another acquirer must be documented, not just possible. `stated`
  (2026-08-04); picked up when the acquisition milestone is refined.
- **Match direction is song → hitlists.** beets works per track, so the
  dataset must answer "which chart positions does this song have", not
  "which library track fills this chart slot". `stated` (2026-08-04).
- **A song ontology is the join point.** Chart entries and songs are
  distinct: the same song appears under different spellings across editions
  and charts, and some entries are multi-song singles (double A-sides) that
  must credit each constituent song. Entries resolve to songs once, inside
  the dataset; library tracks resolve to songs at beets time — neither side
  needs to know the other's mess. `stated` (the need, the cross-hitlist
  mismatch, multi-song singles; 2026-08-04); `derived` (the entry/song split
  as the mechanism).
- **Variant policy.** An explicit version qualifier ("live", "remix", …)
  makes a distinct song; how strictly the beets side matches variants is
  user-configurable. `stated` (2026-08-04; originated as an agent proposal,
  endorsed by the user). *Checked against the Top 2000 source
  (2026-10-02):* Wikipedia's table keeps explicit variants as distinct
  songs — "(live)", "(unplugged)", "(akoestisch)" rows beside the studio
  version — so the policy holds there; what it omits is "(Albumversie)",
  the broadcaster's marker for which cut is played, not a distinct song.
  The user notes a few live versions are merged on Wikipedia, though not
  many. `stated` (correction of an agent over-reading; `evidenced`:
  [#98](https://github.com/pvliesdonk/beets-hitlisttag/issues/98),
  correction comment).
- **Raw data is disposable; curation is precious.** Acquired editions are
  re-acquirable at will; hand-curation (aliases, entry–song links, merges,
  splits) must survive full re-acquisition. `derived`.
- **The existing `CHARTS` tag format is the unchanged output contract.**
  Generated tags must be indistinguishable from externally produced ones to
  the rest of the plugin. When generating, a chart's object in a track's tag
  is replaced wholesale from the dataset — the dataset is the source of
  truth — while charts the dataset does not know remain untouched. `stated`
  (replace-per-chart, 2026-08-04); `derived` (format stability as contract).
- **Score.** A positional-points sum — position *p* in an edition of size
  *N* contributes *N*+1−*p* — is the official scoring method for the Top 40
  and the default meaning of the tag's `score` here; alternatives remain
  open. `stated` (2026-08-04).

## Milestones and order

Milestones 1–5 (the first roadmap), 6 and 7 are delivered; see
History. Milestones (each holds its acceptance criterion in its GitHub
description):

6. [Chart dataset and tag generation](https://github.com/pvliesdonk/beets-hitlisttag/milestone/6)
7. [Acquisition framework](https://github.com/pvliesdonk/beets-hitlisttag/milestone/7)
8. [Song ontology and curation](https://github.com/pvliesdonk/beets-hitlisttag/milestone/8)
9. [Matching beyond exact](https://github.com/pvliesdonk/beets-hitlisttag/milestone/9)
10. [Chart coverage and upkeep](https://github.com/pvliesdonk/beets-hitlisttag/milestone/10)

The ordering argument is information gain, not just dependency. `derived`
throughout.

6. **Chart dataset and tag generation** first: the end-to-end thin slice —
   hand-authorable dataset, exact-normalized lookup, tag writing — is the
   cheapest full-pipeline proof. It resolves the largest global unknown
   (whether per-track lookup against a local dataset is viable and pleasant
   inside beets), and it fixes the dataset contract that every later
   milestone consumes. It is also immediately useful even hand-fed.
   *Delivered 2026-08-19; the bet paid: per-track lookup against a local
   dataset is viable, and the dataset contract every later milestone
   consumes now exists in code (`evidenced`:
   `beetsplug/hitlisttag/dataset.py`).*
7. **Acquisition framework** second: real scraped data at scale is exactly
   the evidence the ontology design needs — how messy entries actually are,
   how often spellings diverge, how common multi-song entries are. Designing
   the ontology before seeing real data would be guessing. Top 2000 first
   within the milestone: yearly snapshot, lowest churn. (The tagger project
   independently reached the same first-chart conclusion — corroboration,
   not a dependency.) *Premise weakened (2026-10-02):* the only
   terms-clean Top 2000 source is Wikipedia's editor-normalised table —
   one harmonised spelling per song across every year, qualifiers dropped
   (`evidenced`:
   [#98](https://github.com/pvliesdonk/beets-hitlisttag/issues/98)). The
   Top 2000 will therefore *not* show the raw spelling mess this argument
   expected; the mess evidence now arrives with the Top 40 weekly in
   milestone 10. Top 2000 first still stands — it proves the framework on
   the cheapest chart — but it no longer buys the ontology its evidence.
   *Delivered 2026-10-03.* The framework bet paid: a third-party
   ingestor needs no change to the package, and the Top 2000 acquires
   live from its public source in under two seconds (`evidenced`:
   `tests/test_acquisition_acceptance.py`; History). The ontology
   evidence did not come with it, as the 2026-10-02 revision expected.
8. **Song ontology and curation** after a raw-published chart, designed
   against the observed mess rather than the imagined one. *Reordered
   (2026-10-02, `stated`):* with the Top 2000 pre-curated at its source,
   the user chose to wait — refining this milestone is blocked by
   milestone 10's refinement (#72 ← #74) rather than by the Top 2000
   ingestor, and the edge moves to the Top 40 ingestor feature once that
   refinement creates it. So milestone 10 is no longer last in full: its
   first raw-published ingestor is pulled ahead of this milestone, while
   its upkeep and remaining-coverage parts stay where they were. The
   milestone numbers are names, not an order. *Edge repointed
   (2026-10-03):* milestone 10's refinement named the Top 100 year
   list rather than the Top 40 weekly as the first raw-published
   ingestor (#72 ← [#117](https://github.com/pvliesdonk/beets-hitlisttag/issues/117)).
   The owner says either serves, as the two lists are different
   published views of the same data (`stated`); the Top 100 has far
   fewer editions to fetch, so it is the cheaper first proof
   (`derived`). If the Top 40 ingestor lands first, the edge moves to it.
9. **Matching beyond exact** after the ontology: fuzzy and interactive
   matching are only worth their complexity for the residue left after
   normalization plus aliases, and the earlier milestones' unmatched-track
   reporting quantifies that residue before we build against it.
10. **Chart coverage and upkeep** last: breadth (Top 40 weekly, Top 100) and
    cadence are operational concerns best not debugged at the same time as
    core design. *Refined 2026-10-03:* within the milestone, a one-day
    source spike on top40.nl gates both ingestors, because the terms of
    the one source both charts share decide whether either is buildable.
    The missing-report fix
    ([#119](https://github.com/pvliesdonk/beets-hitlisttag/issues/119))
    needs only the milestone 6 dataset and can be taken at any time.

The order is a lean, not a wall. The standing example — pulling milestone
8's catalog seeding earlier so milestone 6 had realistic data — lapsed
unused: milestone 6 shipped on hand-authored fixtures, which was enough
to prove the pipeline. The lean itself stands for the milestones that
remain. `derived`.

## Known unknowns

- **Field materialization strategy.** *Resolved (2026-08-03).* The live code
  materializes per-chart flexible fields via an explicit `chartsupdate`
  command; an abandoned variant (`beetsplug/hitlisttag.py.template_funcs`,
  `evidenced`) computed them on the fly as template fields. Research issue #6
  confirmed that template fields are not queryable — beets'
  `template_fields` is exclusively for path formatting (`$name` in format
  strings) and has no query support. Flexible fields are queryable (via
  Python-side matching on the `_flex_table`), which is the primary value
  proposition of the per-chart fields. The staleness concern is managed by
  the `auto` config option and the explicit `chartsupdate` command. No
  change to the current design. `evidenced` (beets 2.12.0 source:
  `BeetsPlugin.template_field`, `dbcore.query.FieldQuery.clause`,
  `dbcore.db.Database._fetch`).
- **The producer of the `CHARTS` tag.** Some external tool writes the tag
  this plugin consumes; its format stability is unknown. Not knowing does not
  change the next steps (the parser must be defensive either way), so this is
  recorded, not ticketed. `derived`. *Superseded in part (2026-08-04):* the
  second roadmap makes this plugin a producer itself; externally written tags
  may still occur, so the defensive-parser stance stands. *Realized
  (2026-08-19):* `chartsgen` writes the tag, and the two producers are
  indistinguishable to the rest of the plugin — pinned by test, not
  asserted (`evidenced`:
  `tests/test_chartsgen_command.py::TestRoundTrip`). The defensive
  parser earns its keep on the generation path too: an unparseable
  existing tag is treated as absent and reported, and is replaced only if
  the track gets generated data.
- **On-disk dataset form.** *Resolved (2026-08-04, refined 2026-08-06 during
  #75).* The dataset is one human-authorable JSON file **per hitlist**, in a
  dataset directory set by plugin config. Each file holds a `songs` table (a
  file-local id mapped to artist/title) and a list of `editions`; an edition
  declares its axis values and declared size, and its entries reference songs
  by id. A song persisting across editions of the hitlist is stored once and
  referenced by id; a single crediting several songs (double A-side, early
  multi-song single) is one entry citing several ids at a single rank, so
  positions stay unique within an edition. Song ids are hitlist-scoped and
  minted per new song, so acquisition never coordinates ids across charts.
  The per-song lookup index is built in memory at command time — chart
  datasets are small enough — and a persisted compiled index is deliberately
  deferred until performance evidence demands it (recorded, not ticketed).
  The curation overlay's form is milestone 8's decision, not made here. Open
  known-unknown: whether a wholesale re-acquisition must preserve minted ids
  so milestone 7's ontology can link to them durably. `stated` (per-hitlist
  songs-table with hitlist-scoped minted ids, 2026-08-06); `derived` (the
  normalized shape). *Locators repointed (2026-08-19):* the form is no
  longer a plan but committed code and documentation — `evidenced`:
  `beetsplug/hitlisttag/dataset.py` (module docstring and the `Edition`
  and `Entry` dataclasses, which now own the per-edition invariants) and
  README, *The chart dataset*. The minted-id question is untouched by
  milestone 6 and still points at milestone 7. *Minted-id question resolved
  (2026-10-02, milestone 7 refinement):* a forced wholesale re-acquisition
  keeps a song's id where the song recurs — its normalized artist/title
  already exists in the chart's file — and mints ids only for new songs;
  the default incremental refresh never re-mints at all. `stated`
  (originated as an agent proposal, endorsed by the user); carried by
  [#101](https://github.com/pvliesdonk/beets-hitlisttag/issues/101).
- **Score beyond the Top 40.** *Resolved structurally (2026-08-04,
  milestone 6 refinement).* The positional-points sum over declared edition
  sizes — official for the Top 40 (`stated`) — ships as the default for
  every chart, and the computation is resolved per chart internally so an
  alternative definition can land without changing the dataset format or
  tag schema. Whether any other chart defines an official score remains
  unknown but no longer gates anything; recorded, not ticketed. `derived`.
  *Framing corrected (2026-08-19, `stated`):* what is known is that the
  Top 40 uses this method officially; what other hitlists do is not known
  either way, and there are many of them. The formula is therefore this
  plugin's **default** for charts whose own method is unknown or
  undefined — not a claim that every chart shares it. Code and docs say
  so (`evidenced`: `beetsplug/hitlisttag/scoring.py`, README, *The chart
  dataset*).
- **Source viability.** Which public sources exist per chart, their terms,
  and whether anti-bot measures apply. Not knowing changes nothing now — the
  ingestor abstraction is source-agnostic by design. Resolved by: refining
  milestone 7 (Top 2000) and milestone 10 (Top 40, Top 100). `derived`.
  *Ticketed for the Top 2000 (2026-10-02):* the user does not know the
  source offhand (`stated`), so refining milestone 7 turned this half into
  research issue [#98](https://github.com/pvliesdonk/beets-hitlisttag/issues/98)
  with a half-day appetite (`derived` — the user may revise it) that blocks
  the Top 2000 ingestor. Not knowing now *does* change what happens next:
  if the appetite runs out or no viable public source exists, *Top 2000
  first* is wrong and another chart leads the milestone — a change of
  direction to record here, not a request for more time. The Top 40 /
  Top 100 half still waits on milestone 10's refinement. *Resolved for the
  Top 2000 (2026-10-02, well within the appetite):* Dutch Wikipedia's
  consolidated table — every edition since 1999, complete, one fetch,
  CC BY-SA, with a sanctioned access path. The official NPO site is ruled
  out by its own terms, which prohibit automated retrieval; its
  per-edition spreadsheet exists for the current edition only. Everything
  third-party is a Wikipedia copy, a single year, or unsourced. Verdict,
  entry shape, and the comparison against the official file are in the
  issue (`evidenced`:
  [#98](https://github.com/pvliesdonk/beets-hitlisttag/issues/98)); the
  direction consequences are recorded under *Variant policy* and the
  ordering argument. *Top 2000 first* holds. *Ticketed for the Top 40 /
  Top 100 (2026-10-03, milestone 10 refinement):* research issue
  [#116](https://github.com/pvliesdonk/beets-hitlisttag/issues/116) on
  top40.nl, the owner's candidate source for both lists, with a one-day
  appetite (agent proposal, confirmed by the owner). It blocks both
  ingestors. It applies the milestone 7 lesson up front: entries are
  checked by name and shape, not counted. If the terms rule top40.nl out,
  the argument that milestone 10's first ingestor feeds milestone 8
  needs a new source or a new order. That goes here as a change of
  direction, not as a request for more time.
- **Partial progress on many-fetch charts.** `chartsacquire` fetches every
  missing edition before writing and discards the lot on one failed fetch
  (`evidenced`: `beetsplug/hitlisttag/acquire.py`, `acquire_chart`). That
  costs nothing for the one-fetch Top 2000 and may stop a chart needing
  hundreds of fetches from ever completing a first population. Keeping
  fetched editions across a failure would revise the all-or-nothing
  write recorded in History (2026-10-03), so the owner decides it.
  Resolved by: the source spike
  ([#116](https://github.com/pvliesdonk/beets-hitlisttag/issues/116)),
  whose fetch count decides whether a feature is filed. `stated` (record
  as an unknown, 2026-10-03); `derived` (the analysis).
- **Ingestor plug-in mechanism.** How a third party's ingestor is found —
  Python entry points, a config-pointed module path, or both — is a
  feature-level decision the user deliberately left to the contract
  feature's brainstorm (`stated`, 2026-10-02). Resolved by:
  [#99](https://github.com/pvliesdonk/beets-hitlisttag/issues/99). Whatever
  is chosen, the proof is an ingestor outside the package being discovered
  and used. `derived`.
- **Curation scale.** How many entries need hand attention after automatic
  normalization — this decides how much curation tooling milestone 8 must
  carry. Resolved by: refining milestone 8 against milestone 7's real data.
  `derived`. *Caution (2026-10-02):* the Top 2000's source is pre-curated
  (one spelling per song, a handful of double A-sides in 27 years —
  [#98](https://github.com/pvliesdonk/beets-hitlisttag/issues/98)), so its
  tidiness is evidence about that source, not about raw-published chart
  data. This unknown is still open; a raw-published chart answers it.
  Resolved by: refining milestone 10 (#74), whose first raw-published
  ingestor feeds milestone 8's refinement. The owner's candidate source
  for it, the top40.nl Top 100 year lists, is noted on #74. *Pointer
  updated (2026-10-03):* resolved by the Top 100 year-list ingestor
  ([#117](https://github.com/pvliesdonk/beets-hitlisttag/issues/117)),
  or the Top 40 weekly one
  ([#118](https://github.com/pvliesdonk/beets-hitlisttag/issues/118))
  if it lands first. Either serves (`stated`).
- **Residual miss-rate.** Whether fuzzy matching is needed at meaningful
  scale once exact-normalized lookup plus aliases exist; if the residue is
  tiny, milestone 9 shrinks — a possible change of direction, recorded here
  so it is checked rather than assumed. Resolved by: refining milestone 9 on
  the unmatched-track evidence from milestones 6–8. `derived`.
- **External-ID enrichment.** Whether attaching MusicBrainz (or other)
  identifiers to songs in the dataset pays for itself as a match path —
  amortized once in the dataset, never per-library as in the old tagger
  flow. Resolved by: refining milestone 9. `derived`.
- **zwaarstelijst and kerst acquisition.** These shipped chart definitions
  are station-specific lists with no committed acquisition plan; they are
  expected to arrive, if at all, as user-authored ingestors through the
  plug-in mechanism, which also serves as its proof. Not knowing changes
  nothing now; recorded, not ticketed. `derived`.
- **beets version floor.** beets-plex pins `beets>=2.12`; this plugin
  declares the same dependency floor in `pyproject.toml:28` and CI validates
  the plugin against whatever version `pip` resolves for it (`evidenced`:
  `.github/workflows/ci.yml`, `pyproject.toml:28`).

## History

- 2026-07-31 — charted. Baseline source committed unmodified, including
  stale variant files; their removal is scaffolding-milestone work.
- 2026-07-31 — two unknowns resolved by the user: the non-chart fields
  (`my_song_id`, `backup_*`) are out of scope (removal tracked as a work
  item), and configurable chart definitions moved from `derived` to `stated`
  — the hardcoding was a forced workaround on older beets, not a preference.
  Doc evidence that current beets supports config-driven typed fields was
  added to the field-materialization research issue.
- 2026-07-31 — user correction: the historical generalization difficulty sat
  in the mediafile layer, not beets. Verified against installed mediafile
  0.17.0 that the constraint persists; the single-blob `CHARTS` tag design
  is the correct boundary for it, so the argument stands with corrected
  attribution.
- 2026-08-01 — milestone 1 (package scaffolding) completed. Stale variant
  files removed; plugin packaged, installable, and tested; CI gating lint and
  tests on every PR. Roadmap locators repointed and the beets-version-floor
  unknown now evidenced by CI/pyproject.toml.
- 2026-08-01 — milestone 2 (chart model correctness) refined; no change to
  direction or ordering.
- 2026-08-02 — milestone 2 (chart model correctness) completed; milestone 3
  (command robustness) refined. A bug filed against milestone 3 turned out
  to be already fixed during milestone 2 work. No change to direction or
  ordering.
- 2026-08-03 — milestone 3 (command robustness) completed. All eight issues
  closed: three bugs fixed (None-guard, non-numeric arg continuation,
  empty-result ValueError), command-level tests added covering the acceptance
  criterion's edge cases, and README documentation written for all three
  commands. Milestone 4 (configurable chart definitions) refined into five
  work items. The field-materialization research (#6) remains open but does
  not block the config surface — the `hitlists` config key is the same
  regardless of whether per-chart fields are materialized or computed.
- 2026-08-03 — milestone 4 (configurable chart definitions) completed. All
  five issues closed: `hitlists` config key added with shipped defaults (#54),
  hardcoded `HITLISTS` replaced with config-driven definitions (#55), tests
  covering custom/empty/fallback/guard/malformed config (#56), and README
  documentation (#57). All seven acceptance criteria met. Milestone 5
  (release automation) refined into two work items: release workflow (#64)
  and release documentation (#65). The release workflow is modeled on
  beets-plex (trusted publishing via OIDC); hatch-vcs version derivation is
  already in place. No change to direction or ordering.
- 2026-08-03 — milestone 5 (release automation) completed. Both issues closed:
  a `release.yml` workflow publishes to PyPI via trusted publishing on GitHub
  Release (#64), and `CONTRIBUTING.md` documents the development setup, checks,
  commit and PR conventions, and the release process including the one-time
  PyPI trusted-publisher setup (#65). The workflow is dormant until the first
  release is cut; its PyPI prerequisites are manual and documented. This is the
  final milestone in the roadmap. No change to direction or ordering.
- 2026-08-03 — **first roadmap complete.** v0.1.0 released to PyPI. All five
  milestones delivered; all 33 issues closed. Research issue #6 (field
  materialization strategy) resolved: template fields are not queryable,
  confirming the current materialized-flexible-field design. No further
  milestones are charted. The roadmap is in a terminal state: the index
  and milestones remain as the record of what was built and why, not as a
  plan for future work.
- 2026-08-04 — **re-charted: second roadmap.** The ambition extends from
  consuming the `CHARTS` tag to producing it, ending the 2026-08-03 terminal
  state. Five new milestones (6–10) charted with the ordering argument
  above; no feature issues yet. Direction decisions stated by the user this
  session: standalone from the `tagger` project (reference only); a single
  package including a bundled, pluggable acquisition executable; no
  redistribution of chart data (copyright); song→hitlist as the match
  direction; a song ontology handling cross-hitlist spelling variance and
  multi-song singles; the Top 40's official positional-points sum as the
  default score; replace-per-chart semantics when writing generated tags.
  The "producer of the CHARTS tag" unknown is superseded in part — this
  plugin becomes a producer.
- 2026-08-04 — milestone 6 (chart dataset and tag generation) refined into
  six work items (#75–#80): edition format/reader/fixtures, normalized
  exact lookup, score and highest, the `chartsgen` command, tests, and
  README documentation. Both unknowns pointing at this refinement resolved
  (on-disk dataset form; score definable per chart with the positional-sum
  default). One cross-milestone edge encoded: refining milestone 7 is
  blocked by the edition format (#75), since ingestors write that format.
  No change to direction or ordering.
- 2026-08-04 — direction addition (`stated`): the ingestor pluggability
  promise includes developer documentation — how to implement another
  acquirer must be documented, not just possible. To be covered when
  milestone 7 is refined; no change to ordering.
- 2026-08-06 — dataset format refined (`stated`) while implementing #75: one
  file **per hitlist** (a `songs` table plus `editions` that reference songs
  by id) rather than one file per edition, removing the string duplication of
  a song's run across editions. Song ids are hitlist-scoped and minted per new
  song. New known-unknown recorded: minted-id stability across wholesale
  re-acquisition versus milestone 7's ontology links. No change to ordering.
- 2026-08-19 — **milestone 6 (chart dataset and tag generation) completed.**
  All eight issues closed: the dataset reader and fixtures (#75), normalized
  exact lookup (#76), per-chart score and highest (#77), the `chartsgen`
  command (#78), the test suite (#79), README documentation (#80), plus the
  package conversion (#85) and the milestone's refinement issue (#70). The
  acceptance criterion was checked clause by clause rather than inferred from
  an empty issue list, and each clause has a test behind it: a single command
  generates tags from data on disk in the documented form
  (`tests/test_chartsgen_command.py::TestGeneration`); generated tags are
  indistinguishable to the rest of the plugin from externally produced ones
  (`::TestRoundTrip`, which drives `chartsupdate`, `charts`, and `hitlist`
  over both); tracks with no unambiguous match are reported, not guessed at
  or silently skipped (the unmatched, ambiguous, and unnormalizable report
  buckets, each covered).
- 2026-08-19 — **method finding, worth more than the code it produced.** The
  first `chartsgen` implementation passed every per-task review and a
  whole-branch review, then was stopped before push by the local review gate
  with four confirmed findings sharing one root cause: the specification had
  designed the happy path and the *reporting* of failures, but never the
  *state* after a failure, so invariants were held by call-site ordering and
  parser-local checks rather than by the types. The branch was abandoned and
  the work re-implemented from a spec addendum stating four invariants: the
  file write gates the database store (a failed write leaves both stores
  untouched); `ChartList` rejects duplicate chart names at parse; edition
  invariants live on the `Edition`/`Entry` dataclasses rather than in the
  parser; and empty axis lists are rejected by config validation. The rework
  cleared the gate. Recorded here because the same omission is available to
  every later milestone — acquisition and curation both write state that a
  failure can leave half-applied. `derived`.
- 2026-08-19 — milestone 7 (acquisition framework) is unblocked: its
  refinement issue (#71) was waiting on the edition format (#75), which
  shipped. It is next; no change to the ordering argument, which held.
- 2026-10-02 — milestone 7 (acquisition framework) refined into six work
  items (#99–#104) and one research spike (#98): the ingestor contract and
  discovery, the acquisition executable, incremental refresh and forced
  re-acquisition, the Top 2000 ingestor, documentation for users and
  ingestor authors, and an end-to-end pin of the acceptance criterion. Four
  decisions from the user this session, each originating as an agent
  proposal: the Top 2000 source is unknown and gets a spike rather than a
  guess; the plug-in mechanism is left to the contract feature's brainstorm;
  a real ingestor's test fixtures are synthetic or trimmed pages, never a
  complete edition; and a forced re-acquisition preserves ids for recurring
  songs. The milestone 6 failure-state lesson is applied in the acquisition
  executable's own text: the all-or-nothing write is a stated invariant,
  not a detail for the implementer to discover. Two cross-milestone edges
  encoded that the ordering argument had only argued: refining milestone 8
  (#72) is blocked by the Top 2000 ingestor (#102) — the ontology is
  designed against real acquired data — and refining milestone 10 (#74) is
  blocked by the ingestor contract (#99), which the Top 40 and Top 100
  ingestors will implement. No change to direction or ordering.
- 2026-10-02 — research spike #98 (Top 2000 source viability) closed, well
  within its half-day appetite. Verdict: Dutch Wikipedia's consolidated
  table is the source; the official NPO site is excluded by its terms.
  Consequences recorded above: the *source viability* unknown is resolved
  for the Top 2000; the ordering argument's premise that the Top 2000
  supplies the ontology's "observed mess" is weakened (`derived`
  revision) — the Top 2000 proves the framework, the Top 40 supplies the
  mess; and the *curation scale* unknown is marked as not answered by a
  pre-curated source. An agent reading that the source undermined the
  `stated` variant policy was corrected by the user: Wikipedia keeps
  explicit variants distinct and omits only the album-cut marker. User
  decision (`stated`): milestone 8 is refined after a raw-published chart,
  not after the Top 2000 ingestor — the graph edge on #72 repointed from
  #102 to #74, and milestone 10's first ingestor is pulled ahead of
  milestone 8. The ingestor contract (#99) shipped the same day.
- 2026-10-03 — the acquisition tool (#100) became a beets subcommand,
  `beet chartsacquire`, by user decision, revising the wording of the
  `stated` "additional executable" item; its substance holds. A plain run
  acquires only missing editions from the first release, and writes are
  all-or-nothing per chart (temp file, re-read with the real reader,
  atomic replace) — the milestone 6 failure-state lesson decided up front.
- 2026-10-03 — **milestone 7 (acquisition framework) completed.** All
  eight issues closed: the Top 2000 source spike (#98), the ingestor
  contract (#99), `chartsacquire` (#100), `--force`/`--prune` (#101), the
  Top 2000 ingestor (#102), documentation and the ingestor-author guide
  (#103), the acceptance pin (#104), and the refinement issue (#71). As
  for milestone 6, the criterion was checked clause by clause rather than
  read off an empty issue list:
  - *populate and refresh at least one real chart from its public source
    by running the bundled tool* — a live `beet chartsacquire top2000`
    against Wikipedia wrote 27 editions (1999–2025) and 4,925 songs in
    about 1.7 s; the 2025 top three match the broadcaster's own 2025 file
    from the #98 spike (`evidenced`: closeout run recorded on the PR that
    adds this entry);
  - *re-running acquires only what is missing* — a live second run
    reported "up to date" and left the file's mtime unchanged; offline,
    `tests/test_acquisition_acceptance.py::TestAcceptance::test_rerun_acquires_only_what_is_missing`,
    shown to fail when a plain run re-fetches;
  - *a third party can add a new chart without modifying this package* —
    `::test_third_party_ingestor_needs_no_package_change` and
    `::test_bundled_tool_populates_dataset_through_to_tags`, which run a
    drop-in ingestor through `chartsacquire`, `chartsgen` and
    `chartsupdate`; the guide's example ingestor was run as published;
  - *the package ships no chart data* — `::test_package_ships_no_chart_data`
    together with `scripts/check_dist.py` in CI (#87), which proves the
    wheel holds exactly the tracked `beetsplug/` Python files.
- 2026-10-03 — **method finding from milestone 7.** Every feature PR went
  through a fresh-context whole-branch review before push, and three of
  them found defects that their own test suites passed: the Top 2000
  parser failed outright on the real page (two template forms the spike
  never exercised, because the spike counted positions and never checked
  names); `chartsacquire` could silently overwrite a file holding a
  different chart or drop fields it did not know; and an ingestor's
  network error was reported as a write failure. All were caught against
  real or adversarial input rather than the plan's own fixtures. The
  standing lesson for later milestones: a review that runs the code
  against real data finds what fixtures designed alongside the code do
  not. Four minor findings remain, tracked in #114. Next on the graph:
  refining milestone 10 (#74), which milestone 8's refinement now waits
  on. `derived`.
- 2026-10-03 — milestone 10 (chart coverage and upkeep) refined into five
  work items and one research spike: the top40.nl source spike (#116),
  the Top 100 year-list ingestor (#117), the Top 40 weekly ingestor
  (#118), the missing-report fix against declared edition sizes (#119),
  documentation for the new charts and routine upkeep (#120), and an
  end-to-end pin of the acceptance criterion (#121). The four
  `chartsacquire` robustness gaps (#114) join this milestone, since
  routine runs over three bundled charts make one ingestor's crash
  stopping the rest matter. Four decisions from the owner this session,
  each from an agent proposal: #72 now waits on the Top 100 ingestor
  rather than the Top 40, either serving since both lists are views of
  the same data; the partial-progress question is recorded as an
  unknown for the spike, not filed as a feature; #114 moves in; the
  spike's appetite is one day. Coverage against the frozen criterion:
  *populated from public sources* is #116–#118; *kept current with
  routine runs* rests on milestone 7's incremental refresh plus #114,
  #120, and whatever the partial-progress unknown turns up; the
  missing-report clause is #119; #121 pins both. Cross-milestone check:
  nothing outside the milestone blocks these items beyond the shipped
  contract and dataset (#99, #75); the one outgoing edge is #72 ← #117.
  The milestone description says "remaining bundled charts" but the
  criterion names only the Top 40 and Top 100; zwaarstelijst and kerst
  stay as recorded under their own unknown. No change to direction.
