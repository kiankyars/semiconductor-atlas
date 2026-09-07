# Durable source-version review queue

The local queue makes acquisition results actionable without treating them as manufacturing facts.
Every previously unseen eligible text version enters review, even when its capture says `unchanged`.
Later unchanged, blocked, failed, or omitted checks cannot resolve a pending item. Decisions are
append-only, attributable, and replayable at a knowledge-time cutoff.

## Capture to review

Use a separate queue database, never an Atlas claim database or a path inside a source packet.
Initialization refuses every existing file, including existing queues. The live local pilot is
`artifacts/2026-09-07-amkor-source-review.sqlite`; do not initialize over it.

```sh
python3 scripts/review_curated_sources.py init \
  --database artifacts/source-review.sqlite

python3 scripts/review_curated_sources.py import \
  --database artifacts/source-review.sqlite \
  --capture source_snapshots/2026-09-07-amkor-repeatable-capture-03

python3 scripts/capture_curated_sources.py capture \
  --plan acquisition_plans/amkor_peoria_v2.json \
  --prior-checks source_snapshots/2026-09-07-amkor-repeatable-capture-03/last_successful_checks.json \
  --prior-root source_snapshots/2026-09-07-amkor-repeatable-capture-03 \
  --output source_snapshots/amkor-next-queued-check \
  --review-queue artifacts/source-review.sqlite

python3 scripts/review_curated_sources.py report \
  --database artifacts/source-review.sqlite
```

The integrated command preflights the queue before network access. Capture publication and queue
commit are separate operations: if queue import fails afterward, the error identifies the retained
packet. Retry the `import` command; do not repeat acquisition just because queue import failed.
Import re-verifies the capture before commit and deduplicates by exact manifest hash. It cannot
grant a new collection permission or bypass an expired or blocked acquisition plan.

## Review semantics

Candidate identity binds the facility key, exact source URL, text normalization, review-rule version,
and current visible-text hash. Every observed occurrence retains its packet manifest hash, plan,
source-document ID, prior/current body and text hashes, retrieval clocks, and queue admission clock.
The initial prior hash is context for that first occurrence; later occurrences retain their own
predecessors instead of overwriting it. Scope and policy changes remain bound to the separate plans.

Statuses `pending`, `acknowledged`, and `deferred` all count as pending work. `dismiss` records that
no further review of this text version is needed; it does not declare the underlying statements
true or false. `handoff` records a reviewer-supplied reference to a downstream claim-review process.
Its `handed_off` status is queue bookkeeping, not proof that another system received or accepted the
item. The reference is recorded, not automatically followed or verified. Both dispositions leave
`claim_acceptance=false` and `delivery_eligible=false`.

Use the current candidate `id` and `last_event_id` from the report:

```sh
python3 scripts/review_curated_sources.py decide \
  --database artifacts/source-review.sqlite \
  --candidate CANDIDATE_ID \
  --expected-event LAST_EVENT_ID \
  --action defer \
  --reviewer REVIEWER_ID \
  --reason 'Need facility-specific evidence before claim review'
```

Every action requires an identified reviewer and nonempty reason; handoff additionally requires
`--evidence-ref`. Stale decision tokens fail transactionally. Reopen a resolved candidate explicitly
before another disposition. There is no `accept` action and no notification delivery.

If a resolved version is observed again as a collector review candidate, or returns after a
different version known to the queue, `requires_reopen` and `recheck_count` surface the recurrence
without rewriting the earlier disposition. This includes a missed return-transition packet followed
by an unchanged packet, and a middle version imported after the return becomes known. Backfilled
observations reevaluate known chronology at their admission time; later explicit dispositions remain
respected. A simple unchanged continuation of the same resolved version stays resolved.
Repeated comparisons against an old baseline can also require recheck; the queue does not claim
calibrated recurrence precision or automatic hysteresis.

`pending_count` alone is not a health indicator. Report-level `attention_required` also includes
recheck items, failed/blocked latest checks, and conflicting eligible versions tied at the latest
retrieval clock. Queue admission and decision clocks govern `--as-of` reports. Actual document
response-completion clocks govern latest-source and first/last-seen summaries; a slow packet cannot
make an older document look newer. An unattempted policy-blocked check has a separate assessment
clock, not an invented document retrieval. Equal-clock observations remain parallel.

## Replay and recovery

```sh
python3 scripts/review_curated_sources.py report \
  --database artifacts/source-review.sqlite --as-of 2026-09-07T03:22:00Z

python3 scripts/review_curated_sources.py export \
  --database artifacts/source-review.sqlite --output artifacts/source-review-events.json

python3 scripts/review_curated_sources.py restore \
  --database artifacts/source-review-restored.sqlite --events artifacts/source-review-events.json

python3 scripts/review_curated_sources.py verify \
  --database artifacts/source-review-restored.sqlite
```

The export is metadata only; publisher bodies stay in their local capture packets. Restore requires
a fresh database and preserves all original event IDs and clocks. It validates the event hash chain
and state transitions before creating the output. `report` verifies event-chain replay without
requiring source files; `verify` additionally checks every retained capture at its recorded location.
Keep those packets available for evidence-byte verification. Hash chains detect inconsistency but
are not externally signed authenticity proofs; preserve a separately pinned export/head hash.
This separate queue uses its own SQLite application ID and rejects other databases. It does not
change Atlas schema migrations, the published r3 producer, or any manufacturing claims.

## Live acceptance pilot

The [September 7 pilot record](../review_plans/2026-09-07-amkor-queue-pilot.json) pins the three
retained captures and six-event queue export. Initial import admitted two Amkor text versions;
the second packet admitted NIST's version. A live integrated seven-request capture at
03:21:47.754133–03:21:55.682197 UTC found no substantive text change and retained all three pending
items. No item disappeared merely because acquisition was quiet.

The three versions were then compared with the exact earlier Amkor successor packet.
All normalized text hashes matched. Explicit reviewer dispositions at 03:25:28 UTC dismissed the
duplicate text-version review work, preserving existing unresolved project, capacity, and rights
questions. This did not accept a new manufacturing claim or approve publication.

Export and restore reproduced the complete event stream and all tested historical reports exactly:
before admission, after the first admission, after the live repeat, between dispositions, and after
all dispositions. The backlog was respectively 0, 2, 3, 2, and 0 at those checkpoints.

The [coverage report](curated_coverage.md) adds the explicitly configured cohort denominator,
document freshness, acquisition health, and plan expiry to the queue backlog. An empty queue is
not complete monitoring coverage.

Coverage remains incomplete. The queue can flag an unseen version or a return evidenced by its
known observations; it cannot reconstruct an entirely omitted intermediate A→B→A episode from
final A→A alone. Scheduled execution, complete collection-chain accounting, broader approved source
coverage, operational alert delivery, and blind historical detection evaluation remain open.
