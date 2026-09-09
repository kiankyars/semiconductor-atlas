# Exact-entity evidence dossiers

The installed `semiconductor-atlas evidence-dossier` command makes accepted core
evidence readable without converting source reports into physical-current facts.
It reads an existing schema-5-or-later database and writes one new local Markdown
or JSON file. It does not migrate, ingest, acquire sources, update the map, assign
identities, produce forecasts or deliver alerts.

## Read a retained subject

If the entity ID is not known, start with [evidence search](evidence_search.md).
It finds literal registry and direct-source metadata matches while keeping every
source-native ID separate; use the selected entry's `dossier_selection` fields.

Run from the isolated development checkout, using an existing output directory:

```sh
semiconductor-atlas evidence-dossier \
  --database artifacts/2026-09-09-new-albany-project-statements-schema9.sqlite \
  --entity-id acac346e-5928-5c14-b20e-ae2739dce92a \
  --as-of 2026-09-09 \
  --recorded-at 2026-09-09T05:52:00.817388Z \
  --output artifacts/ohio-one-evidence.md
```

Use `python3 -m semiconductor_atlas.cli` in place of `semiconductor-atlas` for an
uninstalled source checkout. Add `--format json` and choose a separate output
path for structured data. Standard output is a JSON receipt with exact output
bytes/hash and section counts; the requested report is in the file. Markdown
opens directly in a local document viewer. Output paths are new-only, require an
existing non-symlink parent path and cannot be the input database or its SQLite
sidecars. Existing files are never overwritten, including failed earlier runs.

These exact source-native IDs are in the retained schema-9 successor:

| Subject | Entity ID | Accepted source evidence |
| --- | --- | --- |
| Ohio One two-fab program | `acac346e-5928-5c14-b20e-ae2739dce92a` | Three City-reported statements, two documents, one municipal family |
| TSMC Fab 21 issuer-table row | `7902d8d6-d5f7-5907-a9a7-958f04251b31` | Commercial-production commencement in 2024 and source-reported location |
| Infineon Villach site | `494f3e3f-9640-512b-b5eb-f0ca88717cc7` | Five fiscal-year produced-wafer-area references |
| Polar Bloomington facility | `b6a6a186-1e8a-5c30-a609-9c21b2ae72b4` | One government-office-reported monthly production rate |

IDs are exact selection, not search results or canonical assignments. Selecting
the NIST Ohio project does not implicitly add the separate City project. The
Fab 21 commencement event is not renamed HVM. Villach area does not become wafer
count or capacity; Polar's monthly denominator does not supply a reference month,
native wafer diameter or installed/qualified capacity basis.

## The two clocks and four sections

`--as-of` is the physical-world date. `--recorded-at` is an explicit timezone-aware
knowledge cutoff, normalized to UTC with microsecond precision. The latter is a
query parameter, not a claim that this report was produced at that historical
time. There is no generated-at timestamp in the deterministic content.

1. **World-current claims:** the existing world-time selection, excluding every
   `source_statement`. Direct observations, reconciled facts and derived estimates
   retain their explicit kind; this is not a list of uniformly verified facts.
2. **Source statements:** versions known and not superseded at the knowledge
   cutoff, even with unknown or future effective dates. Multiple unsuperseded
   versions remain visible; physical-world rank-one selection is not substituted.
3. **Recorded history:** the exact subject's source versions known by the cutoff,
   plus non-source versions whose start is unknown or no later than the world
   date. Expired and superseded versions remain. Later supersession timestamps
   are masked; history is grouped deterministically by predicate and series.
4. **Dependency context:** additional transitive parents, including other subjects
   or versions outside the root world-date selection. They are not silently added
   to the selected entity's current claims or identity.

An entity not yet created at the knowledge cutoff is unavailable; its later label
and claim IDs are not exposed. Missing or future-known dependency/support clocks,
cyclic lineage, inconsistent typed-value hashes and malformed quantity/rate
metadata fail closed. A referenced ingestion run must have succeeded by the cutoff;
its completion and explicit actual acceptance clock, when present, are checked.
Sources, families, input documents, series, entities and record observations are
also checked for temporal availability. The report checks recorded database
provenance, not source-file bytes or substantive truth. Use the original frozen
admission verifier for exact source-byte reconstruction.

## Meaning, provenance and privacy

Both formats preserve milestone event/status, interval bounds, date precision,
literal and null midpoint; quantity accounting scope, fiscal period, source-unit
scale and approximation/lower-bound qualifiers; and rate numerator, denominator,
reference interval, qualifier and native diameter. No rates are annualized, values
summed, phases allocated or unknown physical bases inferred.

Evidence includes publisher, stored source family/key, document URL/title/license,
publication and retrieval dates, claim admission, record observation, precise
locator, claim/evidence/record/document IDs and recorded hashes. Ingestion clocks
are separate. Document/family counts describe provenance, not independent
corroboration or calibrated confidence.

The report deliberately excludes evidence excerpts, raw record payloads, claim
notes, run parameters and the verbatim `source_rate.source_wording` field. Reviewed
semantic values, labels, definitions and date/number literals remain. This is a
local derivative, not a blanket redistribution license. Publishing a dossier
still requires its own rights review.

Markdown has a compact value overview followed by full details. Untrusted strings
are escaped; only HTTP(S) source URLs without credentials become clickable.

## Storage and replay boundaries

The CLI opens SQLite using `mode=ro` and query-only mode, with a coherent read
savepoint. It does not use `immutable=1`, so a legitimate WAL is not ignored. The
connection API preserves any existing caller transaction and does not commit or
roll back caller work. New-file publication uses the existing descriptor-bound,
no-replace sidecar writer for both formats.

No scheduled runtime, baseline, database or historical producer pin is changed by
the command. Older admissions continue to replay through their frozen runtimes;
new report code is not grounds to repin or rewrite those historical artifacts.

## Executed acceptance

The installed wheel was exercised in fresh `-I -S -B` subprocesses through its
actual console-entry-point metadata, with every Atlas import checked against the
temporary installed package and no `scripts` imports. Four real subjects produced
eight Markdown/JSON reports in `artifacts/2026-09-09-installed-evidence-dossiers-v2`.
Eight independent repeats matched exact bytes. A separate SQLite checker matched
the full selected claim-ID sets, retained quantity/rate metadata and Fab 21 event
precision, and verified that all four have zero world-current dossier claims.
Their 11 source statements are useful evidence, not 11 physical-state observations.

The checker is `artifacts/2026-09-09-check-installed-dossiers.py`, SHA-256
`c58277df1c1b4e08b677b8b68c00ca1d12467dedb9391aa5b4d57a6e60c44613`.
The acceptance receipt is `artifacts/2026-09-09-installed-evidence-dossiers-check-v2.json`,
SHA-256 `8676e7ee6fdf26389901258180d741dbb0ceb63ff3ea6c0cf3388824ee3752dd`.
It pins wheel SHA-256
`9d2433c02d1e2d80aad33b02622b30e6f7879b91cacebc9393a88fa65bb12348`;
the wheel's dossier, CLI and shared writer bytes match the tested source checkout.
Markdown parsing confirmed overview/detail tables and safe HTTP(S) citation links
for all four reports; this is a structural render check, not a screenshot review.

All 40 dossier tests pass. The combined dossier, CLI, quantity, rate and milestone
suite passes 117 tests. The final fixed-source full core run executed 1,909 tests
in 268.860 seconds: 1,908 passed and one expected optional-payload test was skipped.
All 12 web tests and 36 frozen-runtime tests passed. A prior run was invalidated by
concurrent source edits and rejected changed producer fingerprints; it is not used
as final validation. Syntax and offline wheel/source-distribution builds passed.

The existing Ohio admission was re-executed using its original frozen runtime,
matching all 242,892,800 database bytes, SHA-256
`0901ee33eeada18c70e949411be3c9582117262ec4e8cfd87638681f25787b5d`.
Its new receipt is `artifacts/2026-09-09-post-dossier-city-frozen-replay-v1.json`,
SHA-256 `8b6be40a1f94f777b344f55fc32d7d466c06f7d3befe80c148de97315c476cd2`.
The parent checkout's ledger, baseline, analytics producer and scheduled database,
and the isolated prospective guidance vintage, retain their prior hashes.

This closes a local inspection gap for already accepted evidence. Literal
[entity discovery](evidence_search.md) is now available separately. Integration
into the geographic viewer, broader evidence
coverage, independent outcome review, measured alert performance and calibrated
forecasts remain separate work toward the north-star.
