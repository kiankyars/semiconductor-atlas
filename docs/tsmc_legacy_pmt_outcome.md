# TSMC first-Phoenix-fab source-guidance outcome

## Result

The September 9, 2026 comparison adds one supported outcome to the complete
[frozen NIST PMT screen](nist_pmt_outcome_screen.md), without rewriting that screen
or its earlier zero-score diagnostic. The original April 8, 2024 NIST narrative
expects high-volume production in the first Phoenix fab by the first half of
2025. The already-retained TSMC filing reports first-facility HVM at the end of
2024. Its conservative upper bound, December 31, 2024, satisfies the original
June 30, 2025 deadline.

This is one retrospective **source-guidance deadline hit**, not a successful
Atlas forecast. Both documents were retained after the reported event; the
comparison was selected with outcome exposure. Source truth, independent physical
corroboration and historical byte availability remain unverified.

| Population | Result |
| --- | --- |
| Frozen original cases | 49, across all 45 manufacturing groups; identities and targets unchanged |
| Original calendar-eligible cases | 3; one deadline met, two results still unresolved |
| Original unknown outcomes | 48 |
| Supplemental Bosch sample event | 1, unchanged and unscored; not first-200mm-chip attainment |
| Combined cases | 50; exactly one outcome changed |

There is no cohort success-rate or forecast-accuracy claim. Unknown outcomes are
not failures, and the selected successful source report is not representative
evidence of project performance. The report retains every original placeholder,
shared-source dependency and unresolved result.

## Scope and date correspondence

Both complete narratives distinguish first, second and third facilities in the
Phoenix manufacturing program. Matching the first facility's HVM event does not
equate the aggregate manufacturing entity Fab 21 or its operating-table row with
that phase. The previously retained commercial-commencement observation remains
separate. No geometry or canonical facility assignment is created.

The filing supports only `observed_by: 2024-12-31` here. The adapter invents no
exact start day, quarter lower bound or midpoint. It adds no capacity,
qualification, yield, utilization or technology-at-startup assertion. Main review
and a separate local agent both inspected the complete source narratives and
the unchanged case population; this collaborative review is not blind evaluation.

## Legacy provenance, not reconstructed HTTP evidence

The full 10,355,816-byte filing is pinned to SHA-256
`c3ebd05cd8fb383f53fc21a0ac497ee12cf908b709c380f9bec4f39c4916647b`.
The review also pins the original baseline and snapshot manifest. Validation
reconciles source identity, URL, publisher, content type, rights, exact primary
input, ingestion producer and recorded clock order across those records.

The legacy record gives a source publication day of April 16, 2026 and a
retrieval/acquisition batch-completion timestamp of August 20, 2026 at 16:11:52
UTC. That batch clock is not an exact per-request HTTP finish. No HTTP status,
TLS validation, redirect chain or historical timestamp attestation is invented.
The newer HTTP-capture adapter and its size/access checks are unchanged.

The complete Arizona narrative occupies UTF-8 byte offsets `[1494045,1497004)`
in the retained filing, SHA-256
`eb22e3ca5b1afd9fb497a89eae3e7d08459bbc9045f39e4c4fcdefbb7b2ce97b`.
The bounded source-specific rule requires the first-facility HVM statement and
the surrounding first/second/third-facility context, plus explicit manual
organization, location, phase and event correspondence.

Historical rights metadata is retained, not promoted to a new redistribution
grant. This operation is local-review-only, embeds no publisher body, makes no
network request and authorizes no model training. The original baseline, core
databases, prospective vintages, collector and scheduled runtime remain unchanged.

## Executed artifacts and verification

- Review: `review_plans/2026-09-09-tsmc-legacy-pmt-outcome-review-v1.json`,
  5,920 bytes; SHA-256
  `147603d1556cd8220a1e32d98f04831c13a01ce894b5f5249a0adcb7ac623c32`.
- Diagnostic: `artifacts/2026-09-09-tsmc-legacy-pmt-outcome-diagnostic-v1.json`,
  168,207 bytes; SHA-256
  `a4a660544ebb32ab8a5603be5d807b4c90be4623ec1989e2e8f9d581d20e7986`.
- Actual diagnostic completion: `2026-09-09T05:13:35.567830Z`.

The separate adapter reconstructs the parent population/capture verification,
validates the legacy source, replaces only the previously unknown matched outcome
and recomputes all 50 case results. Verification requires exact report bytes and
producer hashes. Creation is new-only; older artifacts are never repinned.

From the isolated development checkout, verify with the report's exact producer:

```sh
python3 scripts/evaluate_legacy_pmt_outcomes.py \
  --review review_plans/2026-09-09-tsmc-legacy-pmt-outcome-review-v1.json \
  --review-sha256 147603d1556cd8220a1e32d98f04831c13a01ce894b5f5249a0adcb7ac623c32 \
  --reference-root . \
  --source-body /Users/kian/Developer/semiconductor_atlas/source_snapshots/2026-08-20-ai-critical-manufacturing-v1/tsmc-2025-form-20f.html \
  --provenance /Users/kian/Developer/semiconductor_atlas/baselines/ai_critical_manufacturing_v1.json \
  --source-manifest /Users/kian/Developer/semiconductor_atlas/source_snapshots/2026-08-20-ai-critical-manufacturing-v1/manifest.json \
  verify \
  --report artifacts/2026-09-09-tsmc-legacy-pmt-outcome-diagnostic-v1.json \
  --report-sha256 a4a660544ebb32ab8a5603be5d807b4c90be4623ec1989e2e8f9d581d20e7986
```

The frozen-runtime controller also exposes `legacy-pmt-outcome-diagnostic`, with
the same explicit evidence inputs and trusted review/report hashes. It permits
only verification, never new report creation or source acquisition, and requires
absolute evidence paths. It includes the parent population wrapper in the
isolated execution, not just the pure timing arithmetic.

## Frozen-code replay

Implementation commit: `a8f6eafee070dd0bc4844f7ffed522828daee226`.
Its retained runtime is `artifacts/2026-09-09-a8f6eaf-frozen-runtime-v1.json`:
144 files, 3,212,177 decoded source bytes, 4,325,134 bundle bytes; SHA-256
`797626dfa843bcf30038c8756429d18fdb8295917679b46cc40b90b1e5ba2f7f`.

The isolated replay matched all 168,207 diagnostic bytes. All 25 imported project
modules, including the parent population verifier, resolved inside the frozen
runtime. Receipt `artifacts/2026-09-09-frozen-tsmc-legacy-pmt-replay-v1.json`
contains 3,696 bytes; SHA-256
`9374c9f74e768359b1ee2bf3fc873322fe434311e9b768088eef14bc0a7ae7d4`.

The source runtime embeds no evidence bodies or databases. Replay still needs
the exact original evidence inputs and recorded interpreter identity. It verifies
local reconstruction, not a hermetic OS environment, external attestation or an
independent judgment of manufacturing truth.

```sh
atlas_root=/Users/kian/Developer/semiconductor_atlas
atlas_work="$atlas_root/artifacts/core-v6-evolution"
python3 "$atlas_work/scripts/replay_frozen_runtime.py" \
  --bundle "$atlas_work/artifacts/2026-09-09-a8f6eaf-frozen-runtime-v1.json" \
  --sha256 797626dfa843bcf30038c8756429d18fdb8295917679b46cc40b90b1e5ba2f7f \
  --commit a8f6eafee070dd0bc4844f7ffed522828daee226 \
  --working-directory "$atlas_work" \
  legacy-pmt-outcome-diagnostic \
  --review "$atlas_work/review_plans/2026-09-09-tsmc-legacy-pmt-outcome-review-v1.json" \
  --review-sha256 147603d1556cd8220a1e32d98f04831c13a01ce894b5f5249a0adcb7ac623c32 \
  --reference-root "$atlas_work" \
  --source-body "$atlas_root/source_snapshots/2026-08-20-ai-critical-manufacturing-v1/tsmc-2025-form-20f.html" \
  --provenance "$atlas_root/baselines/ai_critical_manufacturing_v1.json" \
  --source-manifest "$atlas_root/source_snapshots/2026-08-20-ai-critical-manufacturing-v1/manifest.json" \
  --report "$atlas_work/artifacts/2026-09-09-tsmc-legacy-pmt-outcome-diagnostic-v1.json" \
  --report-sha256 a4a660544ebb32ab8a5603be5d807b4c90be4623ec1989e2e8f9d581d20e7986
```

## Validation

The complete core run executed 1,837 tests in 257.853 seconds: 1,836 passed and
one expected optional-payload test was skipped. All 12 web tests passed. The
focused legacy adapter has 28 passing tests; all 34 frozen-runtime tests pass,
including four new tests for this operation. Python syntax checks and the offline
source/wheel build passed. Build artifacts remain local under
`artifacts/2026-09-09-tsmc-legacy-outcome-build-v1`.

Direct report replay matched its exact bytes. A separate read-only comparison
confirmed that all 50 roster rows and targets are identical to the frozen review,
that exactly one outcome changes and that the supplemental Bosch event remains
unscored. The original NIST-only diagnostic also replayed successfully under its
`efe11afb33978554d00b290c3698669c72d3766b` frozen producer. Tests and byte replay
do not independently authenticate publisher statements or semantic judgment.

Before and after this work, the parent checksum ledger, pinned baseline,
analytics module and scheduled schema-5 database retained their exact hashes,
as did the isolated schema-9 database and prospective guidance vintage. Neither
checkout has unmerged paths. No source was acquired, no core database was written
and nothing was pushed, published or rerouted to scheduled execution.

## Remaining north-star work

Acquire and review broader permitted outcome evidence, including delays, pauses
and failed projects, while preserving this full original population. The two
remaining calendar targets cannot be scored from current evidence. Core admission
of source-reported events is separate from this diagnostic; prospective forecast
evaluation additionally needs pre-outcome vintages and later matched outcomes.
No calibrated forecast, measured early-detection performance or global coverage
gate is closed by this one source-guidance result.

The existing Intel Ohio lead is a useful next adverse-project acquisition review:
`sources.id = ohio` in
`review_plans/2026-09-09-nist-pmt-later-evidence-leads-v1.json`. Its exact Ohio House
URL previously returned 502 and has no retained/cleared outcome body. Review
access and rights before any acquisition; distinguish a supported construction
pause from revised future guidance, preserve the two-fab scope and invent no
deadline for the original undated intervention. An access failure is not a
project outcome. TI and Microchip restrictions in the existing access addendum
remain in force.
