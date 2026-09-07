# AI-critical alert review and historical admission

This ledger connects validated standalone change bundles to persistent proposal review. It is
separate from the source-text queue: a changed page is not a manufacturing claim, while a change
bundle already contains explicit before/after claim snapshots and evidence lineage. Neither an
import nor a reviewer action enables automatic delivery or calibrates a proposal's confidence.

## Admit a reviewed comparison

Supply the comparison, both underlying releases, and a manifest-bound admission review. The importer
recomputes the comparison against those releases, rather than accepting a self-consistent proposal
file alone. It retains the release derivatives, comparison, admission record and supporting review
bytes in the ledger so export/restore does not depend on the original directories. Raw publisher
documents are not imported; their hashes and the released evidence fragments remain in the release
derivatives. Exact raw-source verification is a separate prerequisite of the owning evidence review.

The admission record has purpose `alert_review_only`, a reviewer, review clock, rationale, all three
manifest hashes and at least one hash-bound supporting review. Supporting prose is retained for
accountability, not interpreted as a machine-verifiable claim of truth, rights or calibration.
Admission cannot precede the release knowledge cutoff or the admission review.

From a checkout containing the retained Amkor releases, use a new database path:

```sh
python3 scripts/review_ai_critical_alerts.py init \
  --database artifacts/ai-critical-alert-review.sqlite

python3 scripts/review_ai_critical_alerts.py import \
  --database artifacts/ai-critical-alert-review.sqlite \
  --bundle releases/2026-09-07-ai-critical-manufacturing-amkor-changes-v1-r2 \
  --prior releases/2026-08-20-ai-critical-manufacturing-baseline-v1-r3 \
  --current releases/2026-09-07-ai-critical-manufacturing-amkor-successor-v1-r2 \
  --review review_plans/2026-09-07-amkor-alert-admission.json

python3 scripts/review_ai_critical_alerts.py report \
  --database artifacts/ai-critical-alert-review.sqlite
```

The import uses the actual current admission clock. The October 6, 2025 Amkor groundbreaking is
the underlying event date; it is not the date the system detected or reviewed the change. Queries
before admission must show no admitted proposal even when the event itself is much older.

## Reviewer state and lineage

Actions are `acknowledge`, `resolve`, `retract` and `reopen`. Each requires a reviewer, reason and
the alert's latest event identifier as a compare-and-swap token. A stale token cannot overwrite a
newer decision. Resolution closes review handling; it does not mean construction is complete.
Retraction withdraws the proposal interpretation; it does not rewrite the original claims or erase
the alert history.

Exact repeated imports are idempotent. Manifest-only observations with the same assertions and
evidence can share a review episode without erasing its disposition. A later bound prior showing a
return to the earlier state creates a new episode, even if no separate reverse comparison was
imported. Older comparisons sharing the exact prior manifest can be coalesced within the same
episode without moving the latest observed state backward. This is bounded semantic duplicate
handling, not a guarantee that arbitrary incomplete or conflicting histories are equivalent.

Resolution and retraction require a reference to evidence retained in an imported comparison:

```json
{
  "bundle_id": "ai-critical-change-681b7472b26345570aa3801514d1be38",
  "side": "current",
  "claim_id": "eb995aa3-5c66-5e52-9dd8-6f8df0754055",
  "evidence_id": "amkor-peoria-groundbreaking-two-phase",
  "fragment_sha256": "4de5f828e91a1d0231fc539a089adef6c9e9db1fa43aaeb916bd8d2297bcebc7"
}
```

Pass each reference as a JSON object with `--evidence-ref`. The ledger checks the referenced
claim, fragment hash and facility correspondence. This establishes a retained citation, not that
the cited text logically proves the reviewer's reason. A genuinely new correction needs an
independently reviewed release/comparison before its evidence can be referenced.

## Replay and portability

```sh
python3 scripts/review_ai_critical_alerts.py export \
  --database artifacts/ai-critical-alert-review.sqlite \
  --output artifacts/ai-critical-alert-review-events.json

python3 scripts/review_ai_critical_alerts.py restore \
  --database artifacts/ai-critical-alert-review-restored.sqlite \
  --events artifacts/ai-critical-alert-review-events.json

python3 scripts/review_ai_critical_alerts.py verify \
  --database artifacts/ai-critical-alert-review-restored.sqlite
```

Export and initialization never overwrite an existing file. Exported evidence remains subject to
its original rights; portability is not permission to redistribute the export. A hash chain detects
inconsistency, not an independently authenticated reviewer identity or a maliciously rewritten
entire history. `report --as-of <UTC timestamp>` reconstructs knowledge by admission/decision clock,
not by the release's event dates.

## September 7, 2026 retained pilot

The [pilot record](../review_plans/2026-09-07-ai-critical-alert-review-pilot.json) binds the local
database, portable export, final report and verification code hash. One real Amkor proposal was
admitted at 06:35:36.225248 UTC and acknowledged, producing two append-only events. The underlying
groundbreaking remains dated October 6, 2025. Both original baseline and successor passed exact-byte
source/fragment verification; the comparison was independently recomputed against both releases.

The 1,531,234-byte export restored to identical events and a fully verified report in a fresh
temporary directory. Both event cutoffs replayed exactly, with no alert before admission. Guarded
reads confirmed no dependency on original release or review paths during restore. The live case
remains acknowledged and non-deliverable; resolution, retraction, reopening and recurring changes
are exercised by offline synthetic tests, not fabricated decisions about the real facility.

## Remaining delivery and evaluation gates

This is review infrastructure, not evidence of good detection performance. It does not deliver
messages, claim comprehensive monitoring, accept manufacturing claims, infer losses from omitted
rows, or repair canonical data. Confidence remains unknown. Duplicate handling is not a calibrated
time-based hysteresis policy. Blind historical evaluation still needs independently adjudicated
outcomes, a defined source/coverage denominator, precision and false-positive burden, detection lag,
and retraction behavior. Forecast and supply-demand scenario gates remain separate.
