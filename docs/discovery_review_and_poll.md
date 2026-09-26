# Durable discovery review and scheduled polling

Discovery now has its own append-only URL review queue and cadence-guarded poll runner. This queue
does not share the acquired-source-text queue's database, identity rules, or dispositions. The
existing current-index inventory and its historical outputs remain unchanged.

## What is retained and reviewed

Every validated packet is admitted once by manifest SHA-256. The event records its retained path,
plan and baseline hashes, publisher scope, capture clocks, and complete index results. Candidate
identity binds the publisher and canonical URL—not a facility, company alias, page number, or raw
HTML hash. All links are retained, including unmatched records.

Each observation preserves the index's title, date, summary, locality/region, routing matches,
page URL/hash, response time and capture identity. Meaningful metadata fingerprints exclude page
position and retrieval clocks. Repeated identical observations do not recreate work or invalidate
an unchanged review token. Changed metadata, routing, an observed return to an earlier version, or
newly admitted historical context can change the review fingerprint. A resolved candidate then
requires explicit reopening; it is not silently resolved or converted into an accepted claim.

Reviewer actions are `acknowledge`, `defer`, `dismiss`, `handoff`, and `reopen`. Every action needs a
reviewer, reason and current expected-event token. Handoff also needs a scope/access review reference,
but the reference is a workflow locator, not an executable acquisition permission or verified claim.
Deferral remains open work. Any future document acquisition still requires its own reviewed plan.

The queue's cutoff is **admission/decision time**, not the older inventory's capture-finished cutoff.
Late imports do not appear in earlier knowledge snapshots. Equal-time observations and latest
captures remain visible; any incomplete latest chain requires attention. Failed checks and rolling
window disappearance cannot delete old links. Export/restore reproduces the event stream and its
historical projections. Full verification also checks every selected packet at its recorded location;
an event export alone is not a backup of those response bodies.

`candidate_count` counts all distinct URLs. `pending_count` counts open company-routed candidates,
including acknowledged and deferred ones; `unrouted_pending_count` exposes the remaining open links.
These are routing priorities, not complete relevance classification or facility coverage.

## Recoverable polling

`acquisition_plans/nist_discovery_poll_v1.json` binds the previously reviewed NIST index plan. Its
23-hour cadence applies to the publisher across plan revisions, using acquisition intents and
completed capture clocks. Not-due invocations do not move the next due time. The runner:

1. Verifies queue history and obtains a nonblocking process lock.
2. Recovers completed valid packets before checking expiry or cadence.
3. Retains incomplete receipts and their cooldown; corrupted completed packets block replacement.
4. Copies the plan, bound review and baseline into private inputs, then writes intent before fetching.
5. Acquires only the approved index chains and admits the completed packet transactionally.
6. Saves the queue report, event export and invocation outcome.

A crash after manifest completion recovers without refetch. A crash after queue commit recovers
idempotently. Acquiring the process lock, not the existence of a lock file, establishes exclusivity.
There is no background daemon or sleep-until-due loop in this command.

The notification fingerprint includes routed review state and meaningful metadata, latest chain
health, staleness, expiry, and recovery problems. It excludes raw-byte churn, growing observation
counts and ordinary clock advancement. Recovered interrupted work is reported once. Existing
backlog can keep `attention_required` true while `reportable_change` stays false.

## Commands and existing daily task

```sh
python3 scripts/review_discovered_sources.py report \
  --database artifacts/2026-09-07-nist-discovery-review.sqlite
python3 scripts/review_discovered_sources.py verify \
  --database artifacts/2026-09-07-nist-discovery-review.sqlite
python3 scripts/poll_discovered_sources.py \
  --config acquisition_plans/nist_discovery_poll_v1.json
```

The review command also provides explicit `init`, `import`, `decide`, `export`, and `restore`
subcommands. Initialization and restoration require a new database path. `--force` is available
only for manual poll diagnostics and is never part of the daily task.

The existing **Semiconductor Atlas source checks** task retains its daily 08:00 local schedule.
It runs the curated-document command first, waits at least one second after that process finishes,
then runs the discovery command. Each command has its own queue, lock and cadence; this is not a
global rate limiter for independently launched manual jobs. The task does not automatically review
URLs, acquire their linked documents, renew plans, repair evidence, or publish data.

The commands were tested manually before updating the task, following
[OpenAI Docs guidance](https://learn.chatgpt.com/docs/automations). Local scheduled execution requires
the computer and app to remain available. Actual scheduler-triggered execution remains unobserved;
the first scheduled invocation can legitimately be not due after a recent manual capture.

## Live pilot and remaining work

The first discovery packet was admitted with 64 URLs: 14 company-routed and 50 unmatched. Two
routed URLs received explicit deferrals recorded in
`review_plans/2026-09-07-discovered-url-triage.json`: TSMC's July announcement is wider Arizona
expansion context, and the Samsung Taylor URL repeats broader award scope without establishing
a newer facility lifecycle. Neither decision changes the baseline or authorizes acquisition.

A forced manual poll captured eight approved URLs and retained a second complete index packet.
The 64 candidates and both deferrals survived unchanged; no new review signal was produced. Ordinary
API and exact-CLI repeats made no requests. The four-event/two-packet queue restored identically at
every event cutoff. `review_plans/2026-09-07-discovery-poll-pilot.json` binds the retained evidence.

The acquired-source queue remained separate with its earlier pending work. Four facility scopes
were unmonitored at that pilot's cutoff. Subsequent Intel and Micron expansions leave two scopes
unmonitored; the original pilot counts are not rewritten.

The [subsequent Samsung handoff](discovery_handoff.md) connects one URL to separately reviewed
exact-document acquisition and a source-text disposition with offline verification. Its dedicated
Taylor page repeats the monitored Austin page's substantive narrative, so it is not an additional
recurring check or independent corroboration. Both observed versions were dismissed without a
baseline revision. The discovery queue now retains five events and 13 routed open leads, while
the source queue has one pending TSMC text review. This bounded local handoff does not complete
incremental evidence discovery, source-to-claim acceptance, full collection accounting, historical
detection evaluation or calibrated forecasts.
