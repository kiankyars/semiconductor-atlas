# Reviewed capacity-to-event scope

The experimental forecast bridge now requires an affirmative, hash-bound
project/phase/event review before emitting a ramp from a candidate capacity and
production milestone. Shared documents, common ancestors and direct dependencies
remain provenance candidates, not sufficient evidence of semantic applicability.
This repairs a concrete current-projection defect without removing historical
capacity claims or asserting capacity loss.

## Reproduced defect and real result

Before the repair, the selected schema-12 database emitted two Amkor scenario
series and 40 quarterly points by pairing both announced rates with a legacy
end-2027 mass-production estimate. Their only pairing basis was a shared source
document. The retained [September 7 successor review](amkor_peoria_successor_review_2026-09-07.md)
explicitly left the rates' applicability to the expanded campus and Phase One
unresolved. A same-document association did not close that gap.

A separate reviewer reproduced the real result and a synthetic counterexample:
a Phase Two capacity and Phase One milestone on one aggregate campus in one
document produced a ramp without phase correspondence. This is a scope defect,
not evidence that the original announced rates were false or withdrawn.

| Same real input database | Before | Repaired, no review | Repaired, explicit deferral |
| --- | --- | --- | --- |
| Amkor 14,500 wafers/month | 20 scenario points | Scope unreviewed | Scope deferred |
| Amkor 3,700,000 units/month | 20 scenario points | Scope unreviewed | Scope deferred |
| Rocket Lab relative capacity increase | Excluded as relative | Same exclusion | Same exclusion |
| Stored capacity rows | 3 | Same 3 | Same 3 |

The zero eligible forecast-series result is not zero physical capacity. Both
Amkor rates remain non-additive, announced historical claims. No cancellation,
withdrawal, relocation, qualification, comparable delay or current usable
capacity is inferred. Existing historical scenario artifacts remain unchanged.

The real read-only comparison uses the old committed analytics from
`3f1e2327fb7be28c271d6b3061e52ff16aa8c7a7` and the repaired analytics with the
same database and query cutoffs. Shared forecast/model/reader files are verified
unchanged. Receipt `artifacts/2026-09-09-capacity-scope-real-check-v1.json`
is 8,345 bytes, SHA-256
`c1b2b19a0d959a26202e0ec1cd67e179679ce46f7e7123a28a5cb8ae9490c58c`.
It records zero database changes and identical before/after database SHA-256
`0f59f6bc9595effbc08c4eae54bd34606ae0ef6088205a0c3fe71d90aad1d599`.
This is a current differential check, not a historical model evaluation or a
complete isolated replay of the former execution environment.

## Review contract

`create_scope_review` accepts deliberate reviewer decisions, not inferred labels.
Each decision supplies the exact capacity and milestone claim-version IDs,
project scope, phase scope, typed event, `accepted` or `deferred` decision and
rationale. It binds them to one exact entity and to an evidence-dossier digest
at explicit world and knowledge cutoffs. Numeric capacity bounds, milestone
type/status and accepted-ramp date/confidence prerequisites are checked.
The review records the actual current UTC review clock; a caller cannot supply
a historical review/admission time through the API or CLI.

The validator requires exact canonical review bytes and a separately trusted
SHA-256. It reconstructs both the originally reviewed and the requested current
evidence views. Altered values, stale/superseded roots, changed same-entity
evidence, missing lineage, unsupported events, mismatched entities, unavailable
source/run clocks or changed bindings fail. The requested knowledge cutoff must
be at or after the actual review; a later review cannot authorize an earlier
forecast. World `as_of` must equal the reviewed date.

The dossier digest excludes only its top-level query `recorded_at` envelope,
which is separately bound in the review. Every underlying claim, evidence,
source, record and ingestion clock represented by the dossier remains bound.
A later knowledge query with unchanged evidence can reuse the review; changed
evidence requires a new explicit review. No automatic reapproval is performed.
The complete exact-entity dossier is deliberately conservative: unrelated new
evidence on that same entity can also require re-review.

These mechanics do not authenticate reviewer identity, prove project/phase
labels, independently verify source bodies or supply external timestamp
attestation. The trusted hash selects a particular review, not a guarantee that
its semantic judgment is correct. Source permissions remain those of the
underlying material; the workflow does not grant publication or training rights.

## Forecast selection and interfaces

`forecast_current_capacity(..., scope_review=raw_bytes,
scope_review_sha256=trusted_digest)` validates the scope artifact inside the
same read snapshot as the authoritative claim view. The optional existing
`claims=` parameter remains a complete-view consistency check, never a caller
override. Caller transactions/savepoints remain intact.

An otherwise selectable non-usable-basis capacity receives
`capacity_event_scope_unreviewed` without an entry,
`capacity_event_scope_deferred` for a deferred entry, or
`capacity_event_scope_mismatch` if the accepted review names a different
milestone from the uniquely linked candidate. No points are emitted for these
exclusions. Every other forecast guard remains in force: an affirmative scope
review does not override conflicting/cancelled/ambiguous production evidence,
unknown midpoint/confidence, relative quantities or period exclusions.
The existing same-entity provenance linkage remains necessary as well as the
new semantic review; this v1 gate is not a general cross-entity phase allocator.

Economically usable input capacity does not require a production-start pairing,
so that existing path is unchanged. Its evidence quality and forecast assumptions
remain separate concerns, not newly established by this scope review.

Accepted pairings carry the review hash, project/phase labels and review clock
in JSONL, CSV and summary outputs. The numerical ramp model is unchanged and
still labelled `assumption_not_fact`, not calibrated probabilities.

The installed CLI adds `review-forecast-scope` with required `--database`,
`--decisions`, `--reviewer`, `--as-of`, `--recorded-at` and new-only `--output`.
It opens the database read-only, preserves legitimate WAL state and refuses
the database and its SQLite sidecars as output targets. An independent review
reproduced a lexical-path alias bypass in the first implementation. The final
guard compares resolved paths while retaining the non-symlink database-open
check; regression tests cover `..`, symlinked ancestors, missing sidecars and
byte-for-byte preservation of live WAL/shared-memory files. The independent
reviewer reran the original bypass and confirmed rejection before output creation.
`release` accepts `--forecast-scope-review` and
`--forecast-scope-review-sha256` together; it includes the exact review in the
local bundle. Without them, otherwise selectable ramps are explicitly excluded.
No release, scheduler or database migration is performed merely by adding these
interfaces. Publication still requires separate authorization and rights review.

## Executed local scope deferral

The deliberate [decision input](../review_plans/2026-09-09-amkor-capacity-scope-decisions-v1.json)
references the retained successor-review hash and preserves unresolved scope.
The new [evidence-bound review](../review_plans/2026-09-09-amkor-capacity-scope-review-v1.json)
contains two deferrals and no acceptance. It binds evidence known at
`2026-09-09T12:25:57Z`, and was actually reviewed at
`2026-09-09T12:26:28.808418Z`.

Review: 3,426 bytes, SHA-256
`3f36e1027cbb6bebdbc4730039ec659f72544275f12e2e0efa23c9bdbcd6bbce`.
Both entries bind dossier digest
`9a04602bd9a2f3f762ef417ab1a6fb6e851f9986dba34a7229b8ab083422f86e`.
This is a review of already retained evidence, not a fresh publisher check or
new core admission. The original source-native entity and typed values stay as
they were; no canonical identity bridge is created.

The built wheel SHA-256 is
`84666df633027ca6dcc244b7258977ffe0941710205d27811c578348a4454e04`.
An isolated `-I -S -B` subprocess verified all 111 installed package files
against the checkout, loaded 44 Atlas modules only from the wheel and imported
no source-checkout `scripts` module. The real forecast API produced the two
scope deferrals and unchanged relative-quantity exclusion. The installed CLI
also wrote a separate new-only review with a new actual clock and identical
bound decisions. Original database/review bytes stayed unchanged.
Receipt `artifacts/2026-09-09-installed-capacity-scope-check-v2.json`, 17,896
bytes, has SHA-256
`97e333144adfe8d9d9b87ad30293cd07fc67c8eb061a8d4e2200f04c03a41f69`.
The earlier wheel/check receipt remains retained; the v2 check covers the final
path-safety repair and does not overwrite the original review.

The 77-test focused scope/analytics/claim-view suite passes, including 32 new
scope-contract tests and nine integration tests. This validates an explicit
positive path on synthetic evidence as well as rejection paths; it does not
claim a currently accepted real capacity/event pairing or improved prediction
accuracy. Broader usable-capacity evidence, evaluated alerts, calibrated
forecasts and supply-demand scenarios remain unfinished north-star gates.

The retained AT&S diagnostic was independently reconstructed using its frozen
`455bd548cc35fe76f43d4c201e7c77c491ebeb3e` producer after the scope change. All
47,506 report bytes match SHA-256
`ce450803489b5c5e644f8dbdaad39aea6b33813db9b793151aa24e4163881523`.
The new 2,613-byte receipt
`artifacts/2026-09-09-post-capacity-scope-ats-frozen-replay-v1.json` has SHA-256
`b907460a63d617e9737b49c418b48f2d29af46ef4f74b3e54acda5b67eb0446b`.
This verifies exact retained-source diagnostic reconstruction, not a historical
forecast or physical-event verification. The original report and replay receipt
remain unchanged.

The original September 8 guidance-interval comparator also re-executed using
its frozen `0a78d259d4134eb47276bc8afecbed05b1f9cd51` producer. All 871,408
vintage bytes match SHA-256
`d7d0b539717b3c51799710190e21c40247e8d62b20908d7035992a1ba7de4b00`;
counts remain seven screened, six retained, three issued, three abstained and
one excluded. The 3,450-byte receipt
`artifacts/2026-09-09-post-capacity-scope-guidance-frozen-replay-v1.json` has
SHA-256 `7ea2721ab31350505d7e6b7f725f3f4d416a58df293fcba22fb20af7e8d29e3c`.
No new vintage, source acquisition, database writes or outcome score follows.

## Final regression and protected state

The final core suite ran 2,286 tests in 323.848 seconds: 2,285 passed and one
production-cohort test was skipped because its ignored archived source payloads
are absent from this isolated checkout. All 12 web tests and the offline
source/wheel build passed. Package and test source hashes were identical before
and after the full test process; its exit code was zero.

The retained full-test receipt
`artifacts/2026-09-09-capacity-scope-full-tests-v2.json` has SHA-256
`b4c00b50c2019d462466551c642759a27dc11a7912a5c1005c7c361f1bfb91db`.
It binds log SHA-256
`d0c40e20e58ba774c7f94d93c8e814f6aab8150c559e6dc99cc4cec7587ec026`.

The selected schema-12 database, protected parent's checksum ledger, original
`r3` archive, scheduled database, baseline, pinned template and capture module
still match their protected hashes. The parent remains clean at
`de7860fb108d17cc2f3c1a377c1ade85bdad84d0`; neither checkout has unmerged paths.
No scheduler/protocol change, core admission, migration, public release, push
or deployment was performed.
