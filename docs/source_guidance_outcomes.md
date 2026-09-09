# Retrospective source-guidance diagnostics

## What changed

Atlas now has an executed, evidence-bound comparison between a retained project
target and a later issuer-reported outcome. This is a descriptive comparison of
source statements, not a historical Atlas forecast or a passed forecasting gate.
It adds no core database claims and does not change the existing prospective study.

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

From this isolated checkout, verify without creating a new assessment:

```sh
python3 scripts/evaluate_source_guidance.py \
  --review review_plans/2026-09-09-source-guidance-pairing-review-v1.json \
  --review-sha256 8c8c0144f1652bbc9c59d12c19f2be3faba2ea8deed7913a86b653a0aff3eac5 \
  --reference-root . verify \
  --report artifacts/2026-09-09-source-guidance-diagnostic-v1.json \
  --report-sha256 25d2ed49cd5218517a0e72e511702d9b79172f23e559cd6415a8bb9427f8be39
```

## Reusable arithmetic and remaining gate

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

The substantive next gate remains broad, declared project coverage including
non-survivors, version-specific historical evidence where available, actual
pre-outcome forecast vintages, later comparable outcomes and held-out evaluation.
This result supplies one real evidence/arithmetic path; it does not close that gate
or justify fitting a model on these materials.

The original prospective comparator was separately re-executed under its unchanged
producer `0a78d259d4134eb47276bc8afecbed05b1f9cd51`; all 871,408 vintage bytes matched.
Receipt `artifacts/2026-09-09-post-diagnostic-guidance-replay-v1.json` has SHA-256
`7cd55ee32a73c66d423e7831e374e6b9e4c4e9dd75b38cfe98b239f31f283573`.
