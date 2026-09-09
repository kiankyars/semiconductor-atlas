# NIST PMT outcome screen

## Result and scope

The September 9, 2026 retained-evidence comparison preserves all 45 manufacturing
groups in the frozen NIST 2024 PMT population. It contains 49 original cases:
15 timing associations and 34 placeholders for groups without timing assertions.
The shared GlobalFoundries statements remain associated with both original
groups; associations are not independent observations.

One supplemental Bosch sample-production observation brings the diagnostic to
50 cases, still 45 groups. The three original calendar-normalized targets remain
eligible but unresolved. There are **zero scored targets**, not zero successes
out of a fully observed population. All 49 original outcomes remain unknown in
this retained comparison. Later acquisition is incomplete; a complete original
roster is not a complete outcome review.

This is an executed, outcome-exposed source-statement comparison. It does not add
canonical facility assignments, core database claims, physical-capacity estimates,
historical system predictions or calibrated forecasts. Existing prospective
vintages and the earlier three-case Villach diagnostic remain unchanged.

## Three later sources

All three complete NIST narratives and the corresponding original PMT narratives
were inspected by the main reviewer and a separate local source-review agent.
This is collaborative review of government source reports, not independent
physical measurement. Exact full-narrative byte spans, capture ledgers and visible
and metadata publication days are retained.

| Later source | Supported information | Original-project boundary |
| --- | --- | --- |
| Bosch, July 13, 2026 | Roseville SiC sample production had commenced by the reported source day; commercial production remained expected during 2026 | Sample wafer diameter and start lower bound are unknown. This cannot settle the original first-200mm-chip-in-2026 target. |
| Coherent, June 16, 2026 | Existing high-volume 150mm InP capability at Sherman; a new LOI for prospective cleanroom/equipment expansion | The positive existing-capability statement is retained separately in the source interpretation. It is not dated completion of the full original expansion or incremental capacity. |
| Powerex, June 5, 2026 | Signed agreement and manufacturing/R&D investment associated with Pennsylvania power-module production | The narrative does not explicitly identify Youngwood, isolate the federal incentive amount or report production startup/completion. |

Bosch's worldwide chip-delivery total is not Roseville output. Its supplemental
sample event is a different event/scope from first chips on 200mm wafers, HVM,
commercial production and qualification. Even a later diameter bridge would not
by itself supply the lower bound needed to establish interval containment.

Coherent's later up-to-$50-million LOI is not a finalized award or evidence of a
formal replacement of the original up-to-$33-million PMT. Powerex's $30-million
manufacturing/R&D wording is not silently substituted for the original separately
stated proposed federal funding and project investment. Funding does not prove
manufacturing progress.

The later pages report publication and modification on the same calendar days.
All three were actually captured on September 9 between 04:23:21 and 04:23:26 UTC.
Printed dates remain source assertions, not proof that identical bytes were
available then. The original 2024 pages have 2025 modification metadata. No
retrospective source date is backdated into an Atlas prediction or admission.

## One-sided event timing

The pure diagnostic accepts `{"status":"observed_by","through":"YYYY-MM-DD"}`.
It stores no invented lower bound, midpoint or exact occurrence date.

- An upper bound on or before a deadline establishes that deadline was met.
- An upper bound after a deadline cannot establish a late miss.
- An upper bound before an interval establishes an early miss; otherwise the
  absent lower bound prevents interval containment, including at either endpoint.
- Without a target, the observation remains visible and unscored.

Reports containing this status use the v2 arithmetic format; legacy-status-only
reports retain v1. Every substantive outcome requires exact reviewed-primary
references, including unresolved and excluded cases. The arithmetic authenticates
neither semantic correspondence nor source truth.

## Verification and retained artifacts

The population wrapper verifies the frozen original review before checking every
original case's identity, event, scope, target, disposition and source association.
Untimed groups cannot be dropped. Supplemental cases must be explicit, use an
existing project group, have a distinct event/scope and remain targetless/unscored.
The later capture is replayed offline with its complete policy and response graph;
each later narrative is bound to its source metadata and exact primary reference.

The generic retained-source adapter independently checks file hashes, support
spans, primary capture identities and actual review/execution chronology. The
population wrapper's code hashes identify its producer; this is not a hermetic
frozen-runtime or historical-availability claim.

- Diagnostic review: `review_plans/2026-09-09-nist-pmt-outcome-diagnostic-review-v1.json`,
  128,432 bytes; SHA-256
  `91ba19f5ce700fed519389b90d2ef04b9036be32d2e15dd52b204347636a8154`.
- Executed diagnostic: `artifacts/2026-09-09-nist-pmt-outcome-diagnostic-v1.json`,
  265,115 bytes; SHA-256
  `a5edc24cc4fb7cd96705575daa57d051047d9c9d132b28c4db0b18ba92e140df`.
- Population-binding review: `review_plans/2026-09-09-nist-pmt-outcome-screen-v1.json`,
  SHA-256 `21d1b8ca1d6cc3bb072917238fc8de002dfa68deff628e144ae7bce1a59358f4`.
- Population verification receipt:
  `artifacts/2026-09-09-nist-pmt-outcome-verification-v1.json`, SHA-256
  `8f4d38bd70af42266caa4f0f004ef6fb8408a68a056cb3fa64258de8bd4bda63`.
- Diagnostic evidence inventory: 51 files, 4,363,164 bytes, including the frozen
  population references and complete later capture packet.
- Later capture: `artifacts/2026-09-09-nist-2026-progress-capture-v1`; three source
  bodies total 261,572 bytes. Manifest SHA-256
  `05a33260bde85bf62efe4c3e8c5bd81430ba3c9c62f140ad718785cc5064579a`.
- Earlier diagnostic replay receipt:
  `artifacts/2026-09-09-observed-by-legacy-guidance-replay-v1.json`, SHA-256
  `b5d7eef8a28246fb09f7f29f62db7db4c1f86f8ceaac8dde073d99a9214746f8`.
  All 35,855 old report bytes matched under its original frozen producer; all
  16 imported project modules resolved inside that runtime.

```sh
python3 scripts/verify_pmt_outcome_screen.py \
  --review review_plans/2026-09-09-nist-pmt-outcome-screen-v1.json \
  --review-sha256 21d1b8ca1d6cc3bb072917238fc8de002dfa68deff628e144ae7bce1a59358f4 \
  --reference-root .

python3 scripts/evaluate_source_guidance.py \
  --review review_plans/2026-09-09-nist-pmt-outcome-diagnostic-review-v1.json \
  --review-sha256 91ba19f5ce700fed519389b90d2ef04b9036be32d2e15dd52b204347636a8154 \
  --reference-root . verify \
  --report artifacts/2026-09-09-nist-pmt-outcome-diagnostic-v1.json \
  --report-sha256 a5edc24cc4fb7cd96705575daa57d051047d9c9d132b28c4db0b18ba92e140df
```

This command requires the report's exact producer, not a future evolved checkout.
Never repin an old report solely to avoid a producer mismatch.

## Validation

The core run executed 1,803 tests: 1,802 passed and one expected optional-payload
skip. Two final wrapper regressions were added after that run's discovery; the
entire final 46-test wrapper suite subsequently passed. The 34 pure timing tests,
19 retained-adapter tests and all 12 web tests passed. Both the new diagnostic and
the complete population-verification receipt reconstructed exactly.

The offline source-distribution and wheel build uses `uv build --offline`; the
default Python lacks the `build` frontend and its installed setuptools is older
than the declared build requirement. Cached isolated build dependencies suffice;
no project runtime or parent environment was modified. The final build is in
`artifacts/2026-09-09-pmt-outcome-build-final`. The earlier build directory remains
local and unpublished.

The parent baseline, checksum ledger, analytics module and scheduled schema-5
database retained their exact hashes, as did the isolated schema-9 database and
existing prospective forecast vintage. Neither checkout has unmerged paths.
Nothing was pushed, published, admitted to the core database or rerouted to the
scheduled runtime.

## Remaining work

The [dated access addendum](../review_plans/2026-09-09-pmt-outcome-access-addendum.md)
retains TI's automated-acquisition restriction and Microchip's incomplete policy
review following a main-origin HTTP 403. No production-source body was acquired
through either route. Access restrictions are not project outcomes; the affected
groups stay in the original population.

Broader permitted, event-matched outcome evidence remains necessary, including
partial projects, pauses, failed projects and cancellations. Useful existing-state
evidence such as Coherent's capability must not be discarded merely because it
cannot score an original target; deliberate source-native core admission remains
separate work. Forecast evaluation still needs cutoff-safe prospective vintages,
later comparable outcomes, geographical and temporal holdouts, and a defensible
population. This descriptive screen does not close those gates.
