# Reviewed operating-fab realization cohort

This additive workflow expands an already verified Fab 21 observation into a complete review
of the same retained issuer operating-fab table. It records source-reported commercial-production
commencement years, not canonical claims, independently corroborated physical outcomes, HVM
dates, qualified capacity or forecast scores. The existing observation and its producer stay
unchanged. This retrospective table cohort is separate from the published seven-company
AI-critical baseline; it does not change that release's facility or capability scope.

## Population and meaning

The unit is one source-native fab row in the table, identified by
`tsmc-2025-20f:fab:<literal>`. It is not a canonical project or project phase. In particular,
the table's `Fab 2` must not be joined by label to the separate Arizona Fab 2 project.
Location stays null. The adjacent wafer-size and technology columns are checked as table
structure, not treated as evidence of technology at commencement.

Every data row must appear exactly once in source order in the review. Omitted, duplicated,
reordered, relabelled or mismatched rows fail verification. Row decisions are:

- `accept_source_report`: retain the reviewed year as January 1 through December 31, with a
  null midpoint and a newly sampled admission clock.
- `carry_forward`: required only for Fab 21; preserve the exact original subject, event and
  admission time from the verified prior observation.
- `defer`: retain the row, evidence reference and reason in the denominator, but leave its
  admitted event and recorded time null. This does not retract a prior observation.

The population is **fabs reported in operation at the table's stated cutoff**. Projects absent
from that table, including unbuilt, delayed, cancelled or closed cases, are not enumerated. Retaining every
table row prevents cherry-picking within this table; it does not eliminate the table's
survivorship bias or establish an issuer-wide historical project census. This cohort alone
cannot support forecast calibration, geographic generalization or an unbiased training set.

## Evidence and historical clocks

Review format: `semiconductor-atlas-realized-fab-table-review-v1`.
Artifact format: `semiconductor-atlas-realized-fab-table-cohort-v1`.

The review pins the exact prior artifact bytes and inventories each row's fab/year literals,
end-exclusive UTF-8 byte span, hash, decision and original review rationale. Reviewer identity,
actual review time and prior exposure are mandatory. Verification replays the prior observation's
source/body/provenance/ingestion checks, complete table structure and semantics before checking
the complete row inventory. No browser-rendered visibility or external source-authentication
claim follows from these local checks.

The new admission samples its validation and admission clocks from the running process. It
cannot be backdated through a command-line argument. Review must follow the anchor admission;
new admission must follow review. Fab 21 keeps its original admission time in the cohort.
Publication, retrieval, table-state cutoff, commencement year, review and admission are separate
clocks. Older commencement years do not create older system knowledge or pre-outcome forecasts.

The cohort embeds the reference-only prior observation, not the publisher body, raw table,
excerpts or full baseline metadata. It remains local-review-only; this workflow grants no
redistribution rights. Verification binds the complete artifact, selection disclosures,
counts, case values, rights, boundaries and six producer-module hashes. Changes to loaded
producer files fail admission and replay. Self-hashes are integrity checks, not external
timestamp attestations: retain trusted exact artifact hashes separately when transferring
or evaluating these records.

Each `source_report_key` hashes the exact document hash, source-native identifier and event
type. It identifies a report in these bytes, not a globally unique physical event. Repeated
cohort files must not be summed as newly discovered events; deduplicate report keys and
preserve prior admissions. The workflow does not maintain a global first-observation registry.

## Local commands

Admission requires existing local source bytes, provenance, reviewed inventory and anchor:

```sh
python3 scripts/record_realized_fab_cohort.py admit \
  --review review_plans/2026-09-07-tsmc-operating-fab-realizations.json \
  --anchor artifacts/2026-09-07-tsmc-fab21-realized-milestone-v1.json \
  --source-body source_snapshots/2026-08-20-ai-critical-manufacturing-v1/tsmc-2025-form-20f.html \
  --provenance baselines/ai_critical_manufacturing_v1.json \
  --output artifacts/2026-09-07-tsmc-operating-fab-realizations-v1.json
```

The CLI verifies before writing and refuses to replace an existing output. Once admitted,
use replay rather than rerunning admission to refresh an observation's timestamp:

```sh
python3 scripts/record_realized_fab_cohort.py verify \
  --cohort artifacts/2026-09-07-tsmc-operating-fab-realizations-v1.json \
  --source-body source_snapshots/2026-08-20-ai-critical-manufacturing-v1/tsmc-2025-form-20f.html \
  --provenance baselines/ai_critical_manufacturing_v1.json
```

Neither command downloads evidence, modifies a database, assigns canonical identity, registers
a forecast or scores an outcome. Core support for completed year-precision milestones remains
a separate integration requirement; this workflow does not circumvent that schema limitation
with an invented midpoint or an `expected` status.
