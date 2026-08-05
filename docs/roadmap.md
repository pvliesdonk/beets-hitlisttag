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
  endorsed by the user).
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

Milestones 1–5 (the first roadmap) are delivered; see History. Current
milestones (each holds its acceptance criterion in its GitHub description):

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
7. **Acquisition framework** second: real scraped data at scale is exactly
   the evidence the ontology design needs — how messy entries actually are,
   how often spellings diverge, how common multi-song entries are. Designing
   the ontology before seeing real data would be guessing. Top 2000 first
   within the milestone: yearly snapshot, lowest churn. (The tagger project
   independently reached the same first-chart conclusion — corroboration,
   not a dependency.)
8. **Song ontology and curation** third, designed against the observed mess
   rather than the imagined one.
9. **Matching beyond exact** after the ontology: fuzzy and interactive
   matching are only worth their complexity for the residue left after
   normalization plus aliases, and the earlier milestones' unmatched-track
   reporting quantifies that residue before we build against it.
10. **Chart coverage and upkeep** last: breadth (Top 40 weekly, Top 100) and
    cadence are operational concerns best not debugged at the same time as
    core design.

The order is a lean, not a wall: e.g. seeding the song catalog from an
already-tagged library (milestone 8) may be pulled earlier if milestone 6
wants realistic data before acquisition lands.

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
  may still occur, so the defensive-parser stance stands.
- **On-disk dataset form.** *Resolved (2026-08-04, milestone 6 refinement).*
  Editions are one human-authorable JSON file each, in a dataset directory
  set by plugin config; a file declares its chart, axis values, declared
  size, and ranked raw entries. The per-song lookup index is built in
  memory at command time — chart datasets are small enough — and a
  persisted compiled index is deliberately deferred until performance
  evidence demands it (recorded, not ticketed). The curation overlay's form
  is milestone 8's decision, not made here. `derived` (decisions carried by
  the milestone 6 feature issues).
- **Score beyond the Top 40.** *Resolved structurally (2026-08-04,
  milestone 6 refinement).* The positional-points sum over declared edition
  sizes — official for the Top 40 (`stated`) — ships as the default for
  every chart, and the computation is resolved per chart internally so an
  alternative definition can land without changing the dataset format or
  tag schema. Whether any other chart defines an official score remains
  unknown but no longer gates anything; recorded, not ticketed. `derived`.
- **Source viability.** Which public sources exist per chart, their terms,
  and whether anti-bot measures apply. Not knowing changes nothing now — the
  ingestor abstraction is source-agnostic by design. Resolved by: refining
  milestone 7 (Top 2000) and milestone 10 (Top 40, Top 100). `derived`.
- **Curation scale.** How many entries need hand attention after automatic
  normalization — this decides how much curation tooling milestone 8 must
  carry. Resolved by: refining milestone 8 against milestone 7's real data.
  `derived`.
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
