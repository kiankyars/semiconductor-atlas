# Retrospective source-guidance diagnostics

## What changed

Atlas now has an executed, evidence-bound comparison between a retained project
target and a later issuer-reported outcome. This is a descriptive comparison of
source statements, not a historical Atlas forecast or a passed forecasting gate.
It adds no core database claims and does not change the existing prospective study.

The separate [Dresden and Kulim timing review](infineon_dresden_kulim_timing.md)
now retains six additional event-specific cases across two projects. Five targets
have reviewed calendar bounds, one season remains unresolved, and zero outcomes
are scored. The opening upper bound, event mismatches and unresolved prospectus
clearance remain explicit; this does not change the original three-project report.

The September 9, 2026 screen contains three purposively selected candidates:

| Candidate | Reviewed target | Reviewed outcome | Result |
| --- | --- | --- | --- |
| Infineon new Villach power fab | July-September 2021 production startup | August 2021 production startup | Reported month is contained in target quarter |
| TSMC first Arizona fab | Unresolved in this retained screen | Unresolved | Not scored |
| Samsung Austin new NAND fab | Unresolved in this retained screen | Unresolved | Not scored |

There are three screened projects, one eligible/scored project and two unresolved
projects. This is not a 100-percent-accuracy claim. There is no representative
population, geographic holdout, blind outcome review, calibrated uncertainty,
exact-day error or model-versus-baseline result.

## Evidence and interpretation

Infineon's [Q1 FY2021 release](https://www.infineon.com/assets/row/public/documents/corporate/investors/presentations/2021/infxx202101-035e.pdf)
is visibly dated February 4, 2021. Complete PDF pages 1, 7 and 15 were rendered and
inspected by the main reviewer and a separate source-review agent. The title and
page 1 identify production startup of the new Villach power-semiconductor plant.
The last fiscal quarter is normalized to July 1-September 30, 2021 using the
document's first-quarter and September fiscal-year-end context. This calendar
interval is a reviewed derivation, not literal calendar-date wording.

The [Austria FY2021 release](https://www.infineon.com/regional/austria/press-release/2021/fiscal-year-20-21)
has a December 7, 2021 visible dateline and explicitly reports production startup
of the new automated 300-mm thin-wafer fab in August. Complete title/date and
context paragraphs are byte-bound to the retained HTML. HTML markup was inspected;
rendered-browser visibility was not independently verified. August remains a month
interval. September's opening ceremony and the subsequent four-year production
ramp are not aliases for that event.

This is a manual correspondence between source-native new-project descriptions,
not a canonical identity assignment. The established Villach campus, its five
annual wafer-area totals, earlier production lines and the Villach-Dresden network
are not the new fab. Whole-site chip production is not new-fab output or capacity.

Both sources were actually retrieved on September 9, 2026 UTC. Their printed 2021
dates are publisher assertions, not historical availability or Atlas issue clocks.
The PDF metadata records May 2024 modification; its nature is unknown. The
September 7 scope research already records exposure to a successful September 2021
factory announcement, before this pairing was selected. Infineon reports both
target and outcome; neither assertion is independent physical measurement. The
February guidance says the schedule was brought forward, but supplies no prior
target from which to calculate acceleration. This is not an original-schedule or
complete revision-history evaluation.

Retained rights review permits local informational noncommercial use with originals,
notices and Infineon attribution preserved. Raw source redistribution, commercial
use and model training are not authorized by this work. TSMC and Samsung URLs in
the separate screening note are unretained research leads, not acquisition grants.

## Executed artifact

- Review: `review_plans/2026-09-09-source-guidance-pairing-review-v1.json`,
  SHA-256 `8c8c0144f1652bbc9c59d12c19f2be3faba2ea8deed7913a86b653a0aff3eac5`.
- Diagnostic: `artifacts/2026-09-09-source-guidance-diagnostic-v1.json`,
  SHA-256 `25d2ed49cd5218517a0e72e511702d9b79172f23e559cd6415a8bb9427f8be39`.
- Evidence: 25 input files, 6,500,366 bytes, including both complete source bodies,
  access/rights policy captures, actual retrieval ledger, PDF renders/extraction,
  reviewed spans and the unresolved-candidate screening note.

The new-only adapter requires a trusted external review hash, verifies each input
and support span, binds primary references to their exact capture URL/body/clock,
and rejects unmatched or dropped cases. Its report retains actual review and
execution times. These checks authenticate bytes and reference membership; they do
not mechanically prove the manual semantic decisions or historical availability.

The direct command below is valid only with the original producer
`1d4e9b2b3c508183474d6ddb4c45a765efa28065`. From the evolved checkout, use the
frozen-runtime command at the end of this document. A producer mismatch is an
intentional rejection; do not repin the old report to make it pass.

```sh
python3 scripts/evaluate_source_guidance.py \
  --review review_plans/2026-09-09-source-guidance-pairing-review-v1.json \
  --review-sha256 8c8c0144f1652bbc9c59d12c19f2be3faba2ea8deed7913a86b653a0aff3eac5 \
  --reference-root . verify \
  --report artifacts/2026-09-09-source-guidance-diagnostic-v1.json \
  --report-sha256 25d2ed49cd5218517a0e72e511702d9b79172f23e559cd6415a8bb9427f8be39
```

## Reusable arithmetic and remaining gate

The later [complete PMT-population screen](nist_pmt_outcome_screen.md) adds
one-sided `observed_by` outcomes without inventing a start lower bound. Such an
observation can prove an inclusive deadline was met, but a later upper bound
cannot prove lateness and an absent lower bound cannot establish interval
containment. Reports using this status select v2; legacy-status-only arithmetic
remains v1. The 45-group retained comparison preserves all original targets and
keeps Bosch's separately scoped sample observation unscored.

`source_guidance_outcomes.evaluate_case` distinguishes an interval from an inclusive
deadline. Early completion can satisfy a deadline while falling outside an interval;
no lower bound is invented for deadline wording. Overlap is unresolved, not a hit.
Explicit nonoccurrence through the target's upper bound establishes a miss. Missing
reports remain unknown; cancellations remain separate, without fabricated dates.

`score` retains all screened cases and separate eligible/scored case, project,
project-group and outcome-status counts. Multiple targets for the same project do
not become independent projects. Source-document chronology is descriptive context
only, even when reversed or uncertain. The adapter separately enforces the pinned
manual evidence review; the pure arithmetic helper claims no evidence verification.

The September 9 follow-up closes an evidence-gate defect before expanding the
population: observed events, cancellations and explicit nonoccurrence now require
reviewed-primary references for **every** disposition, including unresolved and
excluded cases. Previously, only eligible cases received that check. Unknown
outcomes may still use screening notes or no outcome references; eligible targets
still require primary evidence. Conditional targets remain unresolved unless
their applicability is separately established. No new arithmetic or outcome
labels follow from this correction.

The original three-project review contains no affected substantive outcomes on
unresolved/excluded cases. It remains immutable and replays under its original
producer. The post-fix frozen replay verified the same 35,855 report bytes and
all 16 imported module origins; receipt
`artifacts/2026-09-09-post-primary-gate-frozen-guidance-replay-v1.json` has SHA-256
`d7daa46ccecab67f4a4b646c369dbc0827e68ed08a0d76dbe91c578942ff7c67`.

The [broader population source screen](../review_plans/2026-09-09-project-population-source-screen.md)
records why the Intel announcement leads were not acquired and why the current
NIST archive is being enumerated separately from the mutable award portfolio.

The substantive next gate remains broad, declared project coverage including
non-survivors, version-specific historical evidence where available, actual
pre-outcome forecast vintages, later comparable outcomes and held-out evaluation.
This result supplies one real evidence/arithmetic path; it does not close that gate
or justify fitting a model on these materials.

The original prospective comparator was separately re-executed under its unchanged
producer `0a78d259d4134eb47276bc8afecbed05b1f9cd51`; all 871,408 vintage bytes matched.
Receipt `artifacts/2026-09-09-post-diagnostic-guidance-replay-v1.json` has SHA-256
`7cd55ee32a73c66d423e7831e374e6b9e4c4e9dd75b38cfe98b239f31f283573`.

## Independent checks and frozen producer

The separate checker imports neither the scoring helper nor its adapter. It
verified all 25 retained files, primary source identities/clocks, support spans,
three-project accounting and interval containment. It independently re-extracted
the PDF text and re-rendered pages 1, 7 and 15; all text and PNG bytes matched.
Its receipt is `artifacts/2026-09-09-independent-guidance-check-v1.json`, 731 bytes,
SHA-256 `ac6ef0a47bd0c8387e223352e4beb5aa655b4042db7b7f120b07ba760f669fc0`.
This independent implementation check is not independent physical corroboration.

Committed producer `1d4e9b2b3c508183474d6ddb4c45a765efa28065` is retained in
`artifacts/2026-09-09-1d4e9b2-frozen-runtime-v1.json`:

- 4,220,158 bundle bytes, 140 files, 3,134,300 source bytes;
- SHA-256 `11e0762264eeeec02d20079a714955e0130a3afdae184d8d1aae0f284ee10cb8`;
- all 97 diagnostic producer-file hashes retained without repinning the report.

The new allowlisted read-only replay verified the entire 35,855-byte report in an
isolated source runtime. All 16 imported project modules resolved inside that
runtime. Receipt `artifacts/2026-09-09-frozen-source-guidance-diagnostic-replay-v1.json`
is 2,613 bytes, SHA-256
`e5e97f374f3b2b639ea409d29b9640f54700007f2f3efbc44e3f440351d5b0e8`.
Evidence stays outside the code bundle. No claim of a hermetic operating system,
historical 2021 execution or changed scheduled runtime follows.

All 140 decoded runtime files were independently compared with their committed Git
blobs, and all 97 report producer hashes matched that exact commit. Final validation:
1,695 core tests ran (1,694 passed, one expected optional-payload skip), all 12 web
tests passed, and the 66-test focused diagnostic/adapter/runtime suite passed.
Offline source-distribution and wheel builds succeeded. The parent checkout's
checksum ledger, baseline, analytics module and scheduled database hashes remained
unchanged; the latest isolated schema-9 database and prospective vintage also
retained their exact hashes. No merge conflicts, publication or scheduler changes
were introduced.

```sh
python3 scripts/replay_frozen_runtime.py \
  --bundle /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-09-1d4e9b2-frozen-runtime-v1.json \
  --sha256 11e0762264eeeec02d20079a714955e0130a3afdae184d8d1aae0f284ee10cb8 \
  --commit 1d4e9b2b3c508183474d6ddb4c45a765efa28065 \
  --working-directory /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  source-guidance-diagnostic \
  --review /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/review_plans/2026-09-09-source-guidance-pairing-review-v1.json \
  --review-sha256 8c8c0144f1652bbc9c59d12c19f2be3faba2ea8deed7913a86b653a0aff3eac5 \
  --reference-root /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  --report /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-09-source-guidance-diagnostic-v1.json \
  --report-sha256 25d2ed49cd5218517a0e72e511702d9b79172f23e559cd6415a8bb9427f8be39
```
