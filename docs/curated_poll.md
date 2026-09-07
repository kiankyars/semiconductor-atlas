# Scheduled curated-source checks

The app's daily task invokes one recoverable polling command in the existing local workspace:

```sh
python3 scripts/poll_curated_sources.py \
  --config acquisition_plans/ai_critical_poll_v3.json
```

The enabled task is **Semiconductor Atlas source checks**, scheduled daily at 08:00 local time
(America/Los_Angeles at setup). It uses this existing task's context and does not create a new
worktree per run. The [official scheduling documentation](https://learn.chatgpt.com/docs/automations)
requires the computer and app to remain running for local work. A saved active schedule is not
proof that a scheduled execution has happened or that the service has uninterrupted uptime.

The current configuration covers six documents across four cohort scopes: two broader NIST
award pages relevant to TSMC Phoenix and Samsung Taylor, three unchanged Amkor-related URLs,
and the [Chandler municipal page identifying Intel Fab 52](intel_chandler_monitoring.md).
Micron Singapore, SK hynix M15X and ASE Kaohsiung remain unmonitored. Each page retains its broader
document-versus-facility scope. No restricted newsroom collection is enabled.

## Cadence, recovery, and permissions

The configuration binds the exact coverage catalog, queue, capture root, state root, and minimum
interval. Its 23-hour guard accommodates dispatch and request-duration variation around the daily
schedule. This guard is independent of the catalog's seven-day source-freshness threshold.
Completed failed or policy-blocked captures count toward cadence, so an unhealthy source is not
hammered by immediate retries. Skip times and queue admission times never postpone the next check.
Between attempted plans, the runner waits for the larger of their configured request intervals,
including after a failed capture. This extends pacing across plan boundaries; skipped plans do not
create additional waits.

The runner takes a kernel-backed lock beside the queue before recovery and acquisition. The lock
file can remain after exit; its presence alone does not indicate a running process. A competing
live lock holder causes a no-work error. This implementation supports macOS and Linux.

Before network work, each invocation retains its configuration/catalog hashes, queue head,
runner hash, and request clock. Each acquisition retains a separate intent containing the selected
plan, predecessor-ledger hash, facility, output name, and clock. Capture consumes a private copy
of the exact pinned plan and review bytes, not a mutable source-plan path. Raw responses stay in
separate immutable capture directories; runner receipts never alter a capture's manifest.

On the next invocation, completed managed packets are verified and imported before any fetch.
Recovery is manifest-idempotent even after review expiry or a crash after queue commit but before
the receipt. A missing manifest is recorded as an interrupted acquisition and retains a cooldown
from its intent clock. A malformed completed packet or failed recovery blocks new acquisition for
that facility/plan pending investigation. Partial bytes are preserved, not deleted or silently
promoted into a completed run.

The prior observation comes from the latest validated packet's `last_successful_checks.json`, with
`source_checks.json` used only when there is no retained eligible history. Equal-time ambiguous
packets and histories that omit or contradict known eligible documents require review. Older plan
versions may be recovered but do not satisfy a newer plan's cadence or coverage.

The collector rechecks access/rights policy hashes and plan expiry before requesting documents.
The runner cannot renew a plan, follow new URLs, accept manufacturing claims, close review items,
publish source bodies, or deliver manufacturing alerts. `--force` exists only for an explicit
manual cadence override; the scheduled prompt forbids it, and it never overrides policy, expiry,
locking, or recovery gates.

## Inspect a run

Each invocation is retained under `artifacts/curated-poll-v1/<invocation-id>/`:

- `request.json`: start clock and bound inputs;
- `jobs/<cohort-index>/intent.json` and `outcome.json`: attempted capture and its outcome;
- private plan/review copies for attempted jobs;
- `coverage.json`: the complete cohort coverage report at the final knowledge cutoff;
- `queue-events.json`: queue events through that same cutoff; and
- `finished.json`: terminal outcomes, recovery, report hashes, and the change indicator.

An invocation without `finished.json` is not a completed tick. Completed packets can still be
recovered independently. Inspect the actual process handle or kernel lock before interpreting a
partial invocation; do not infer that work stopped from a tool observation timeout.

`reportable_change` compares semantic source versions, freshness/health states, review backlog,
plan state, and unresolved operation problems. It ignores raw-byte churn, clock-age increments,
and mere event-ID changes. Thus repeated quiet checks do not generate repetitive notices about
unchanged coverage gaps or pending items. It is an operational notification aid, not a calibrated alert rule.

## Manual acceptance and remaining gate

The [September 7 pilot](../review_plans/2026-09-07-poll-pilot.json) records three manual invocations:
an ordinary not-due check, one forced live capture, and an immediate ordinary not-due repeat.
The live capture made seven requests, retained two unchanged Amkor documents and byte-only NIST
churn, imported its packet, and left zero pending/recheck items. All three invocations reported
no semantic change. Their retained coverage reports replay exactly at their respective cutoffs.

The daily app task was then enabled. The [NIST expansion pilot](../review_plans/2026-09-07-nist-monitoring-pilot.json)
subsequently captured two new pages, retained two pending review items, and skipped all three
plans without network requests on its ordinary repeat. Amkor's due time was preserved. The existing
daily task was updated to the v2 config; its identity and schedule were unchanged.
The initial expansion exposed a 92-millisecond inter-plan gap. Its receipts are preserved. After
the pacing fix, one explicitly forced manual repeat made 13 requests with a minimum observed gap
of 1.003697 seconds, kept both pending items, and produced no new text versions. Its ordinary
repeat made no requests; the twelve-event queue restored identically.
An actual scheduler-triggered execution has not yet been observed in this acceptance record.
The later [Intel pilot](intel_chandler_monitoring.md) adds one reviewed municipal page through
the v3 config. It made three requests, preserved the existing plans' due times, and verified quiet
no-network repeats. Its first text version was reviewed without a baseline revision. The daily
task now uses v3, with the unchanged discovery job following it sequentially.
The first wake-up may correctly skip because the manual test
was recent. Review the first scheduled outcomes before making any uptime or detection-lag claim.
Broader source coverage, new-document discovery, complete collection-chain accounting, evaluated
manufacturing alerts, and calibrated forecasts remain open.
