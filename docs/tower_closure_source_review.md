# Tower historical event evidence and core admission

The September 8 research pass retained a distinct
[Tower main-domain release](https://towersemi.com/2025/02/10/02102025/) under its explicitly
applicable local-use terms. It supplies three reviewed historical event candidates:

| Source-native subject | Event | Source precision |
| --- | --- | --- |
| Fab1 legacy 150mm flows | Discontinuation | Q4 2024 |
| Fab1 | Last fab output | January 2025 |
| Arai facility | Operations cessation | 2022 |

These are different event meanings, not three interchangeable closure dates. The reported
strategic-flow transfer to Fab2 remains context because this release gives it no separate
event interval. No numeric output, capacity loss, demolition, current permanent closure,
canonical identity or facility location is inferred. The dateline is not a geolocation source.

The [source review](../review_plans/2026-09-08-tower-closure-source-review.json) retains full-block
byte references, source periods with null midpoints, actual acquisition clocks, policy dependencies
and exclusions. Publication is February 10, 2025 from the release dateline; retrieval finished
September 8, 2026 at `22:49:04.111976Z`. This is retrospective evidence, not early detection or an
independently observed forecast outcome. It improves the available non-survivor evidence but
does not establish a representative project population.

## Exact evidence and rights

The unchanged HTML is retained locally at
`artifacts/2026-09-08-tower-fab1-source-capture-v1/responses/tower-fy2024-results-fab1.body`:
185,743 bytes, SHA-256 `7945c366226cf0564916fa2a4f946c663f90900c2a41c4bfaad5c9d7460fe255`.

The [acquisition review](../review_plans/2026-09-08-tower-fab1-source-acquisition.json) permits only
the exact main-origin document for personal informational noncommercial local review. Original
notices remain unchanged; raw redistribution, commercial use, model training and recurring
collection are not authorized. Tower's IR-subdomain filing was not captured: the available
main-origin grant does not explicitly cover that subdomain. No blocked request was bypassed.

The two policy probes and document capture made three successful direct requests without
redirects, retries, credentials or cookies. The shortest actual gap was 38.710845 seconds,
exceeding the retained ten-second crawl delay.

## Original source-review validation

The retained read-only checker is `artifacts/2026-09-08-tower-source-review-check.py`, SHA-256
`db33df0c3fafc9e940e0a8d9aa1422e0da97b403809a604c4e126688aa53a526`.
Its receipt, `artifacts/2026-09-08-tower-source-review-check-v1.json`, has SHA-256
`5b9b1a62d976324762cc4e3ae8ed5357c2113d20ce15bce2ca16850eb183307a`.
It checks 20 input files totaling 583,154 bytes, all six complete selected HTML blocks,
policy-copy equality, actual acquisition chronology and all three existing-model milestone
values. Thirty-six source-check and realized-milestone regression tests pass. These checks
verify evidence storage and interpretation boundaries, not manufacturing truth or browser rendering.

That source-only review admitted no core claims. Its original JSON and receipt remain unchanged,
including `claim_acceptance: false`. The separate admission below closes the storage gate; it does
not retroactively change the source review's authority or knowledge date.

The parallel [numeric-source follow-up](../review_plans/2026-09-08-capacity-output-research-followup.json)
retains eleven lead/policy decisions. Material obstacles include apparent repeated values under
different years in the Amkor Shanghai EIAs, area-and-mask-weighted metrics that are not wafer
counts, unknown scope and period definitions, and explicit collection/retention restrictions.
None of those numeric leads enters supply totals.

## Executed core admission

The separate [admission review](../review_plans/2026-09-08-tower-closure-admission-v1.json), SHA-256
`d4355211ab593216b6fb4012ee83ff28454909c7da5329f2c5047afc6f5db96b`, authorizes exactly three
source-statement milestones. Actual admission was `2026-09-08T23:14:29.225816Z` in the isolated
`artifacts/core-v6-evolution` checkout. The importer makes no network requests or schema migration.

Both Fab1 events share one unresolved source-native facility; Arai has its own. All three use
null effective dates, null confidence and period bounds with no midpoint. Publication, retrieval,
source review and database admission remain distinct clocks. Historical source claims become
queryable only at admission, and never enter the physical-current view merely because their
reported event periods ended. Title/dateline context and two support blocks provide four
byte-located evidence links per claim, twelve total.

The new-only successor is `artifacts/2026-09-08-tower-closure-milestones-schema8.sqlite`:

- 242,585,600 bytes; SHA-256 `b2445daa4cc4dfb4ff56617ad2199ed9a7993f950794f5bf45a184b1d0974bea`.
- Schema 8 unchanged; 104,468 total claims, up from 104,465; 33 milestone values, up from 30.
- Two new source-native facilities, one source document and three source records.
- All parent rows across 29 tables and complete `sqlite_master` definitions preserved.
- Nine source-quantity rows and three numeric capacity rows unchanged.
- All 104,286 hydrated physical-current records unchanged, ordered JSONL SHA-256
  `df7620a1181e1d3f8978f84799404e4b3384781deec83e79ae1b408c82f88cfe`.

The schema-8 parent remains `artifacts/2026-09-08-infineon-villach-output-schema8.sqlite`, SHA-256
`83f24c839dda19f3924a245f10f7bd7bb1ec9439d676fbca558da7cded0c45b6`. Complete reconstruction
from that parent, the exact review and the twenty-file source/policy graph matched every successor
byte. Input and implementation fingerprints are rechecked before exclusive output installation
and after read-only verification; existing outputs cannot be overwritten.

The independent SQL/query checker, `artifacts/2026-09-08-tower-independent-check.py`, SHA-256
`047924e2a73c6a7d680263070cd41e68ca431c8110da2973bf72557e8e04ba85`, does not call the importer.
Its 13,163-byte receipt is `artifacts/2026-09-08-tower-independent-check-v1.json`, SHA-256
`a1171eca36c576a853cce5f8e7b34a9b5adbe1d198d792f6ed33ed6ac90b0082`. It verifies all inherited
rows, exact table deltas, event meanings, lineage and fourteen subject/time-cutoff checks, including
admission minus one microsecond. It also compares complete physical-current records at the same
admission cutoff. This is independent software verification, not independent manufacturing truth.

Read-only replay using the current producing code:

```sh
python3 scripts/admit_tower_closure_milestones.py verify \
  --parent artifacts/2026-09-08-infineon-villach-output-schema8.sqlite \
  --review review_plans/2026-09-08-tower-closure-admission-v1.json \
  --reference-root /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  --expected-review-sha256 d4355211ab593216b6fb4012ee83ff28454909c7da5329f2c5047afc6f5db96b \
  --output artifacts/2026-09-08-tower-closure-milestones-schema8.sqlite \
  --expected-output-sha256 b2445daa4cc4dfb4ff56617ad2199ed9a7993f950794f5bf45a184b1d0974bea
```

After producer code evolves, use the retained producer through the verify-only
[`tower-closure-milestones` frozen-runtime operation](frozen_runtime.md), not a weakened fingerprint
check or a rewritten old database. These commands are repository-local review tooling.

The old Villach producer and the registered guidance comparator were each replayed in their
original isolated runtime after this code addition. Both matched their complete existing outputs.
The new receipts are `artifacts/2026-09-08-post-tower-frozen-villach-replay-v1.json` (SHA-256
`19953f48afcb9bb1d747ab32d0e6819d2a85585042c34a238959d92dab603f44`) and
`artifacts/2026-09-08-post-tower-frozen-guidance-replay-v1.json` (SHA-256
`114c642c8f57838b75bddff67395df8bb0fc124dd406226163eebd023d9e13bb`). Scheduled execution in
the original parent checkout, its checksum ledger and the published baseline are unchanged.

This adds historical cessation/discontinuation evidence to the core, not a representative
non-survivor cohort, numeric capacity loss, usable-capacity measurement, a pre-outcome forecast
pair or calibrated prediction. Those evidence and evaluation gates remain open.

Regression validation: 1,624 core tests completed in 254.356 seconds (1,623 passed, one expected
optional-payload skip), all 12 web tests passed, and offline source-distribution/wheel builds
succeeded. Eighteen new Tower tests cover exact replay, inherited rows, shared facility identity,
separate clocks and fail-closed evidence/rights/input changes. The frozen-runtime suite has 24
passing tests, including the new verify-only Tower operation. Source bodies and database artifacts
remain local; nothing was merged into the scheduled parent, pushed or published.
