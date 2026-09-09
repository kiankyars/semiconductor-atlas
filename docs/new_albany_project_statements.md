# Ohio One: municipal construction-pace report

## Accepted meaning

The City of New Albany's [project-timeline article](https://siliconheartland.newalbanyohio.org/project-timeline-update/)
reports a slower construction pace for Intel's Ohio One two-fab program alongside
reaffirmed commitment. Its [construction index](https://siliconheartland.newalbanyohio.org/construction-updates/)
dates the reported announcement to July 2025 and links that article. These are
two documents from one municipal source family, not two independent measurements.

The admission uses the existing schema-9 source-statement contract:

| Predicate | TEXT value | Supporting documents |
| --- | --- | --- |
| `source_reported.construction_pace` | `slowing` | Complete article and index project narratives |
| `source_reported.project_commitment` | `reaffirmed` | Complete article and index project narratives |
| `source_reported.slowdown_announcement_month` | `2025-07` | Complete index project narrative only |

All three belong to one source-native PROJECT, with no canonical assignment to
the existing NIST Ohio entity and no allocation to individual fabs. Effective
dates and confidence remain null. Publication dates are unknown: neither visible
page text nor publication metadata supplies one. The announcement month is not
the physical slowdown start; the February 2025 illustration caption, 2024
copyright footer and surrounding 2026 roadwork dates are not fab milestone dates.

The record establishes no full suspension, cancellation, completed intervention,
production attainment, numeric capacity change, qualified capacity or current
physical state. Source/history queries can expose these statements after actual
database admission. The null-effective claims do not enter the physical-current
view. No existing revision alert is generated from these first observations.

## Original target and evaluation boundaries

The full original NIST March 20, 2024 narrative describes two New Albany logic
fabs without an Ohio production deadline. Its multistate five-year investment
language is not an Ohio completion target. The frozen case
`nist2024-d70-intel-new-albany-new-fabs--no-timing` therefore remains
`project_intervention_undated` with `target: null` and unknown outcome.

Slower construction is not a completed intervention or proof of nonoccurrence
through a specified date. This admission does not change any frozen PMT case,
target, outcome or score, including the separately reviewed TSMC deadline result.
It adds an adverse-project source report outside the scoring adapter. The exposed
retrospective selection is not a representative adverse-project cohort or a
blind detection/forecast evaluation.

## Retention and access boundaries

The two Ohio House hostnames both failed their initial direct robots requests
with curl TLS error 60, no HTTP response and no retained document body. Their
failed probes remain in `artifacts/2026-09-09-ohio-house-policy-probe-v1` and
`artifacts/2026-09-09-ohio-house-canonical-policy-probe-v1`; no insecure override,
HTTP downgrade, retries or article acquisition followed. This failure says
nothing about project performance.

The City project site is a separate publisher, not a rehost of the stopped
article. Its exact robots and copyright/operator page were reviewed before the
two source captures. The main-City privacy page is ancillary context, not a
substituted same-origin rights policy or copyright license. No open license was
identified. The bounded internal local-review decision is not publisher-granted
permission or a blanket automation grant. No publisher bodies, images or
attachments are included in the committed artifacts; no raw redistribution,
training or commercial-reuse approval is inferred.

The immutable [admission review](../review_plans/2026-09-09-new-albany-project-admission-v1.json)
binds 35 input files totaling 985,497 bytes, including both policy-probe packets,
their original plans, the access review and the complete curated capture packet.
The latter's SHA-256 is
`9d758cfe6d95247d85673141df1ef953187ba24c551dc03a31a94045c5acaab4`.
Document retrieval finished at `2026-09-09T05:31:11.686176Z` and
`2026-09-09T05:31:22.455443Z`; claim review completed at
`2026-09-09T05:45:40.301668Z`. The admission-review hash is
`c94845c31b4f784c11a3ce6b7f8a532faba34327cbde8535b82e0d9470ca0de1`.

The verifier checks exact complete visible narrative blocks, index-to-article
link, same-origin policy normalization, plan/ledger/receipt bindings and actual
cross-packet request order. Ten-second per-origin cadence is enforced; the two
first robots probes on separate origins remain distinct. The current accepted
policy bodies normalize identically despite byte-only copyright-page churn.

## Local execution

`scripts/admit_new_albany_project_statements.py admit` creates a new-only schema-9
successor from the exact Polar source-rate parent. `verify` reconstructs the
entire successor from that parent, the reviewed evidence and the original
admission clock. The importer never edits a database in place or acquires sources.

After producer changes, use the verify-only `new-albany-project-statements`
operation in the [frozen-runtime controller](frozen_runtime.md), with literal
review/output hashes and absolute input paths. Old reports and producer hashes
must not be rewritten to accommodate newer code.

## Executed admission and independent preservation check

Actual admission completed at `2026-09-09T05:52:00.817388Z`. The new-only successor
is `artifacts/2026-09-09-new-albany-project-statements-schema9.sqlite`, 242,892,800
bytes; SHA-256
`0901ee33eeada18c70e949411be3c9582117262ec4e8cfd87638681f25787b5d`.
It contains 104,472 total claims, three more than its immutable schema-9 parent.
The change adds one project, one source family/source, two documents, one run,
two source records and five matched evidence links. No schema migration occurs.

The independent SQL/query checker does not import the admission module. It
verified every inherited row across all 30 tables, the complete `sqlite_master`,
exact table deltas and the one-microsecond-before-admission knowledge cutoff.
All 104,286 fully hydrated physical-current records remain byte-equivalent under
the checker's ordered JSONL representation, SHA-256
`df7620a1181e1d3f8978f84799404e4b3384781deec83e79ae1b408c82f88cfe`.
The three numeric capacity rows, 33 milestone rows, nine source-quantity rows and
one source-rate row are unchanged. This is independent software verification,
not independent corroboration of manufacturing activity.

The retained checker is `artifacts/2026-09-09-new-albany-independent-check.py`,
SHA-256 `64d4703152ca773b98ed9f348996a6983fe5c9427f9bf1727ff97bce95e173e2`.
Its receipt is `artifacts/2026-09-09-new-albany-independent-check-v1.json`, SHA-256
`d767c16abc3987b03940762fa2649ff5754b15685038ba0c1c13e6547a1e9b17`.
Direct full successor reconstruction also matched all 242,892,800 bytes.

The old Polar source-rate admission and TSMC PMT diagnostic were re-executed in
their original frozen runtimes after this code addition. Both matched their
unchanged complete outputs. New receipts are
`artifacts/2026-09-09-post-city-polar-frozen-replay-v1.json` (SHA-256
`3d6b063ede23cbad6eacbff0f0ee044c1cb9f4befd0253880d5e332ad1da87b2`) and
`artifacts/2026-09-09-post-city-tsmc-frozen-replay-v1.json` (SHA-256
`2ccdb5dc2800faa4f079e5c6d773da58c11c00dcc0b6b25aabae876f9e4210c0`).
The parent checkout, checksum ledger, baseline, scheduled database and prospective
guidance vintage retain their earlier hashes. Nothing was merged into scheduled
execution, pushed or published.

## Validation and remaining work

The final full core run executed 1,869 tests in 261.310 seconds: 1,868 passed and
one expected optional-payload test was skipped. All 12 web tests passed. The
30 new admission tests also passed with the 23 existing capture tests, and all
36 frozen-runtime tests passed, including two new operation checks. Syntax
checks and offline wheel/source-distribution builds passed. The final build in
`artifacts/2026-09-09-new-albany-build-v2` contains byte-matching implementation,
README and final test files. No CI or deployment result is asserted.

This completes a local source-to-core path for the reviewed municipal slowdown
report. Broader project/outcome coverage, independent event adjudication, measured
alert performance and pre-outcome forecast evaluation remain open. An undated
adverse source report is useful evidence, not a scored deadline failure or proof
that the forecasting north-star has been achieved.
