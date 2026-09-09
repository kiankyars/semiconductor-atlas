# Bosch Roseville: source-reported sample production by a date

NIST's retained July 13, 2026 narrative reports that sample production at Bosch's
Roseville silicon-carbide facility had already commenced. Commercial production
is separately expected during 2026. The report supplies neither a lower occurrence
bound nor an exact start day. Sample wafer diameter is unspecified, and Bosch's
worldwide cumulative chip deliveries are not Roseville output.

## Schema-11 contract

`MilestoneUpperBoundValue` is a separate typed variant with `ValueKind.MILESTONE`:

- `status=completed`, `date_relation=on_or_before`;
- null `date_low` and `date_base`, nonnull `date_high=2026-07-13`;
- `date_precision=day` describes the upper bound, not occurrence precision;
- the source wording is retained as `date_literal`;
- claim kind is `SOURCE_STATEMENT`, with null effective dates and confidence.

The new relation is included in new value hashes and JSON only. Existing
`MilestoneValue` dataclass fields, payloads and value hashes are unchanged.
Migration 11 rebuilds only the milestone leaf table, adds one source-only guard,
and preserves inherited fields. Explicit null checks prevent SQLite's nullable
CHECK semantics from accepting malformed legacy values. New bounds reject invalid
calendar dates, non-text or whitespace-only literals, lower bounds, midpoints,
other statuses and physical-effective or confidence-bearing parents. Full semantic
validation occurs before migration commit; failure rolls back the migration.

The migration API optionally accepts an explicit aware `applied_at` timestamp for
reconstruction. Bosch's private migration ledger records the actual admission
clock and reuses it for exact replay; older migration rows remain unchanged.
Omitted timestamps retain the historical SQLite default.

The local evidence viewer and dossier say “occurred on or before …; lower bound
and exact date unknown.” Source JSON and release history preserve the relation.
Release notes explain it even after a later claim supersedes the upper bound.
The original standalone map template is unchanged because its bytes are pinned
by the baseline; it consumes physical-current claims, which exclude this source-only
value. No map or r3 producer hash is changed.

## Scope and evidence

Create one source-native facility, without choosing among the six unresolved
Roseville/TSI/NEC/Bosch registry candidates. The NIST narrative provides no street
address or registry ID. Reuse existing NIST family `us-official-semiconductor-awards`
without adding a purportedly independent publisher. No geometry, ownership,
qualification, HVM, utilization, yield, capacity or whole-project completion follows.

The source body is 87,744 bytes, SHA-256
`928d2b6a812e6d9842272497dcbe48202c70c5f6c8828d114be36d3c0e298c42`.
The complete six-paragraph narrative is bytes `[73053,75787)`, SHA-256
`58bcbb52e6244363f6097357466d63718b0bb2f991418369aa23ccdadd79fb2c`.
The complete title and visible dateline are separately hash-bound. Two evidence
links distinguish the occurrence report from its publication-date anchor.
The complete retained acquisition packet and unchanged access review comprise
18 inputs and 375,005 bytes. Offline validation performs no new requests.

The original PMT case `nist2024-d17-bosch-roseville--timing-01` predicts first
chips on 200 mm wafers in 2026. Unspecified samples cannot establish that event;
the unknown lower bound also cannot establish calendar-2026 containment. The case
and frozen outcomes remain unchanged. Its already-reviewed supplemental sample
case remains targetless and unscored. This is exposed retrospective source evidence,
not blind evaluation or independently corroborated physical observation.

Forecast ramps and revision alerts continue to abstain on null midpoints and
unknown effective dates/confidence. Timing benchmark v1 requires finite event
bounds and does not score this value. An event observed by a date is not a
right-censored non-occurrence; those meanings must never be conflated.

## Admission and verification

`scripts/admit_bosch_sample_production.py admit` creates a new-only schema-11
successor of the unchanged accepted Coherent schema-10 database. `verify` performs
full parent-plus-migration-plus-claim reconstruction with the original clocks and
producer inventory, then compares every output byte. Capture packet containment
checks use filesystem identity, including case aliases on macOS.

The `bosch-sample-production` frozen-runtime operation dispatches only verification,
with literal review and successor SHA-256 inputs. It neither admits to the parent
nor acquires sources, changes the scheduler, publishes data or backdates knowledge.
Historical project-target replay now builds its expected image at the inspected
database's own schema version, preserving older value shapes without implicit migration.

This is progress toward event coverage and replay integrity. Measured alert utility,
representative realized-outcome coverage and calibrated forecasts remain open.

## Executed v2 successor

The [selected admission review](../review_plans/2026-09-09-bosch-sample-production-admission-v2.json)
has SHA-256 `fd73d221cc039087ffc158dfd26fb1dc51d3357b6d1d6a35ff2f9c42a9c2b5fd`.
Its unchanged parent is `artifacts/2026-09-09-coherent-sherman-capability-schema10-v2.sqlite`,
SHA-256 `d7fa83b3593030d4dc9a8942e066c8dd71b9e005dcabef7f887adcb0439d7572`.
The selected successor is `artifacts/2026-09-09-bosch-sample-production-schema11-v2.sqlite`,
310,259,712 bytes, SHA-256
`2b453d9fda486d5ec367966a617575d830a83049c263113468fe611dc5ec09b1`.
Actual admission and the new migration ledger timestamp are
`2026-09-09T09:39:40.434852Z`. Subject:
`6140f614-bb7a-5f29-b108-de28b762b47d`; run:
`876f8f30-4871-53c1-8835-cac5718227b8`; claim:
`9b61f850-1646-5822-9376-94a2ed1b1106`.

The v1 review, database and wheel are retained validation candidates, not the
selected successor or publication artifacts. The broad suite caught a pinned-map
template edit and a legacy target-replay schema mismatch before v2. The pinned
template was restored byte-for-byte; replay now respects the input schema.
Neither source interpretation nor the original PMT target/outcome changed.

The independent checker imported no admission code and verified all 543,248
inherited rows across 30 tables, with exactly 14 additions: one migration,
one facility, one claim/series/value, its milestone value, and source/run/evidence
provenance. All source families and all inherited field values/storage classes
are unchanged; old milestone relations are null. Only the milestone leaf schema
and its new guard differ. Installed readers show no Bosch entity one microsecond
before admission and exactly one source claim at admission, with no physical-current
claim. The dossier and feed preserve the upper-bound relation and actual clocks.

Checker: `artifacts/2026-09-09-check-bosch-independent-v2.py`, SHA-256
`a6e05641f32747e95396f25755a9979e7299b369fc145a5cc822e9ff054d1bd4`.
Receipt: `artifacts/2026-09-09-bosch-independent-check-v2.json`, SHA-256
`3873da29b6c8000f1dd7de1fd834f128fcc294c0779498d4ce2b40dd8f5d9b57`.
All 107 wheel/source/installed package files match exactly. Wheel SHA-256:
`94c50b87b8fc66e33fd26a0806f58172111920efcac7448c6e87644fad6ecce0`.
The installed checker uses 19 package modules under `-I -S -B`.

Installed CLI dossier: `artifacts/2026-09-09-bosch-sample-production-dossier-v2.md`,
10,944 bytes, SHA-256 `d2283c5235870d0076c616ba973f0ffbb0dddaf2537ffd1b4866011a64145292`.
The main reviewer read the complete rendered-text artifact. Direct reconstruction
matches all successor bytes. The previous Coherent producer also replayed exactly
after these changes; receipt `artifacts/2026-09-09-post-bosch-coherent-frozen-replay-v1.json`,
SHA-256 `df5dbb8d7e004fb2b657c758731528b286d76c17fe29fc53afbef80107d0284d`.

The protected parent checkout, scheduled database, original checksum ledger,
baseline, r3 archive, PMT population reviews and frozen outcome diagnostic retain
their prior hashes. No sources were acquired and nothing was pushed or published.

Validation: the complete core suite executed 2,142 tests in 281.057 seconds:
2,141 passed and one optional-data test skipped as expected. The focused migration,
admission, historical target replay, frozen-runtime and reader suite passed all
187 tests. Web generation passed 12 tests, and viewer state/script checks passed
21 tests. Those script checks are not browser-rendering QA; this background,
local-only continuation did not open a browser or deploy a site.

The implementation is committed as `3d414ee4cb6d8fce3ea5504de529079b9a35b44c`.
Its frozen runtime is `artifacts/2026-09-09-3d414ee-frozen-runtime-v1.json`:
159 source files, 3,418,526 decoded payload bytes, 4,604,643 bundle bytes,
SHA-256 `b31fbe13a642021fabd9f4b4a222df1b7d5eb682c07c5adf5e1eb9612756e700`.
The isolated frozen producer reproduced the complete selected database exactly,
using 25 project modules. Replay receipt:
`artifacts/2026-09-09-frozen-bosch-sample-production-replay-v2.json`, 5,343 bytes,
SHA-256 `8a194bdb61df7d05f981039705186a2f9f0c74be91d89e8f9405a28d8200d74f`.

## Outstanding publisher-family correction

A separate read-only audit of the selected successor found an inherited NIST
family split. Documents `b357aa1e-1f3e-5bff-a831-db6ba5c136df` and
`e54850f0-e411-57e9-8f6e-819385101e86` share the exact Fab 2 source URL,
retrieval clock `2026-07-18T01:54:47Z`, and SHA-256
`0475c14cd01810d9e8424c9fa1525ecb4eb938b48f2f549a987883cb51c5a71c`,
but belong to `reviewed-source-project-targets` and
`us-official-semiconductor-awards`. This is one publisher, not independent evidence.
The Bosch admission adds no family and does not repair those immutable inherited
assignments. The existing project census uses the former label as an importer-route
marker, so a correction must preserve its historical population and cutoff behavior.
A reviewed, knowledge-time-aware publisher-family correction is the next concrete
provenance gate; editing old family rows in place would destroy replay fidelity.

This inherited split is now corrected in a separate [schema-12 successor](source_family_assignments.md).
The selected Bosch schema-11 database and this frozen producer remain unchanged;
the new assignment applies only from its actual September 9 acceptance clock.
