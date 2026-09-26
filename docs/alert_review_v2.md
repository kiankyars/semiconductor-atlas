# Unified facility and source-project proposal review

Version 2 brings legacy facility-change proposals and separately scoped project-target proposals
into one append-only review queue. It does not merge their identities or meanings. A source-native
Fab 2 target revision remains separate from first-fab operating evidence and is not a manufacturing
completion, numeric capacity revision, calibrated forecast, or early-detection success.

The [version-1 ledger](ai_critical_alert_review.md), its command, and its existing portable exports
remain unchanged. Version 2 restores a validated v1 or v2 export into a **new** database, preserving
the original event prefix, identifiers, and admission clocks. It never upgrades or overwrites the
original ledger in place.

## Build from already accepted core claims

The proposal producer reads the core claim store and the previously accepted
[project-target review](source_project_targets.md). This is a read-only operation: it opens the core
database in SQLite `mode=ro`, performs no migration, and accepts no new canonical claims. Source
review and raw-evidence verification remain prerequisites owned by the project-claim workflow.
The builder takes a coherent SQLite backup and replays the existing acceptance against that
snapshot with writes disabled. Later core corrections cannot create a torn, mixed-time packet.
The packet describes the accepted historical comparison, not a fresh assertion of current status.

```sh
python3 scripts/review_alerts_v2.py build-project \
  --database artifacts/core-project-targets.sqlite \
  --review review_plans/project-target-claims.json \
  --source-queue artifacts/source-review.sqlite \
  --reference-root . \
  --output artifacts/project-target-proposal.json
```

Paths above are examples, not a record of a performed admission. The packet output must not already
exist. Source-stated year or half-year periods retain their literal wording and precision. Unknown
effective dates and confidence remain null; a period has no invented midpoint or exact acceleration
in days. The source queue's monitoring route does not establish canonical facility identity.

## Restore history and admit a project proposal

Start with an empty new queue using `init`, or restore existing history:

```sh
python3 scripts/review_alerts_v2.py restore \
  --database artifacts/unified-alert-review.sqlite \
  --events artifacts/legacy-alert-review-events.json
```

A project admission requires a separate review bound to the exact packet file SHA-256:

```json
{
  "format": "semiconductor-atlas-project-alert-admission-v1",
  "purpose": "alert_review_only",
  "reviewer": "reviewer identifier",
  "reviewed_at": "2026-09-07T12:00:00Z",
  "reason": "Review the source-stated target revision without promoting it to attainment.",
  "packet_sha256": "SHA-256 of the exact serialized packet file",
  "expected_head_event_id": null
}
```

The example packet hash is an explanatory placeholder. The example clock is illustrative; a real
review must record its actual review time. `expected_head_event_id` is JSON `null` for an empty
queue; otherwise copy its current global head identifier. The packet file SHA is distinct from its
internal semantic packet identifier. An acquisition
approval or previous claim review is not itself this admission. Import records the actual current
admission clock and never backdates detection to source retrieval, source publication, or a planned
production period.

```sh
python3 scripts/review_alerts_v2.py import-project \
  --database artifacts/unified-alert-review.sqlite \
  --packet artifacts/project-target-proposal.json \
  --review review_plans/project-target-alert-admission.json

python3 scripts/review_alerts_v2.py report \
  --database artifacts/unified-alert-review.sqlite
```

`import-facility` continues the legacy facility-comparison workflow in this same v2 queue. It
requires `--bundle`, `--prior`, `--current`, and `--review`, with the original manifest-bound
facility admission format. Use the unchanged v1 command only for a version-1 queue. Reports label
facility rows with `origin: baseline_facility` and project rows with `origin: source_native_project`.

## Decisions, cutoffs, and exports

`decide` supports `acknowledge`, `resolve`, `retract`, and `reopen`. Supply `--alert`, `--reviewer`,
`--reason`, and the current `--expected-event` compare-and-swap token. Resolution and retraction
require retained evidence references. Project references have this shape:

```json
{"packet_id":"admitted packet file SHA-256","side":"after","claim_id":"accepted core claim identifier","evidence_id":"bound evidence identifier","fragment_sha256":"bound fragment SHA-256"}
```

The side is `before` or `after`. Legacy facility references retain their version-1 shape, including
`bundle_id`, `side` (`prior` or `current`), `claim_id`, `evidence_id`, and `fragment_sha256`.
Pass a reference with `--evidence-ref` and repeat the flag for multiple references. A reference must
match evidence admitted for that subject; it does not prove that the evidence logically supports
arbitrary reviewer prose. Resolution closes review handling, not construction. Retraction preserves
the original proposal and decision history. No decision enables delivery or rewrites core claims.

Packet-only validation and ledger replay establish retained-content consistency, not a fresh
verification of publisher bodies or proof that a claimed core admission genuinely occurred. The
local builder's separate accepted-run replay provides that check against the retained core/source
inputs; portable packets disclose this distinction. They contain bounded evidence derivatives,
not the complete original publisher pages.

`report`, `verify`, and `export` accept `--as-of` with a UTC knowledge-time cutoff. `export` prints
JSON by default or writes a new file with `--output`. `restore` validates before creating a new
destination; it does not overwrite an existing database.

```sh
python3 scripts/review_alerts_v2.py verify \
  --database artifacts/unified-alert-review.sqlite

python3 scripts/review_alerts_v2.py export \
  --database artifacts/unified-alert-review.sqlite \
  --output artifacts/unified-alert-review-events.json
```

Replayability does not grant redistribution rights. Packet and export contents remain subject to
their evidence restrictions. A hash chain detects inconsistent edits, not independently authenticated
reviewer identity or malicious rewriting of an entire history. This workflow does not establish
comprehensive coverage, blind alert performance, delivery readiness, or calibrated supply forecasts.

## September 7, 2026 local pilot

The real NIST Fab 2 packet compares the two core claims accepted at
`2026-09-07T09:28:12.852741Z`: 2028 and second half of 2027. The inclusive periods are disjoint,
so the proposal says the later document states a wholly earlier period. Exact acceleration,
effective time, first publication and calibrated confidence remain unknown. Canonical facility
assignment remains null; no first-fab baseline, capacity or attainment claim changes.

The [separate agent-authored admission](../review_plans/2026-09-07-tsmc-fab2-alert-admission.json)
binds the exact packet file and original Amkor queue head. The project proposal was admitted at
`2026-09-07T09:58:41.341424Z` and acknowledged at `2026-09-07T09:59:13.035537Z`, with all four
claim-evidence links referenced. This is not independent human outcome adjudication. The unified
queue has four events and two acknowledged proposals; neither is delivery-eligible. Original
Amkor events, proposal IDs, clocks and review content remain unchanged in the new history, and
the original v1 database remains untouched.

All local evidence below is retained under ignored `artifacts/`; a Git clone alone does not include
it. These are local review derivatives, not a new public data release.

| Artifact | SHA-256 |
| --- | --- |
| `2026-09-07-tsmc-fab2-target-proposal-v1.json` | `fc8d579b5018e4aef6376b48ccd57ef9baf34de298c7373bd2ddb96d85c35f74` |
| `2026-09-07-unified-alert-review-events-v2.json` | `9053e46660f54b59b48c58d1a82181e04dff92d7bb9a332d28b88b82b94d15d0` |
| `2026-09-07-unified-alert-pilot-verification.json` | `750d64c4c24d9192c682307b86636b31c084f0d7ef7d715b800a3609f2a0eb07` |

The packet rebuilt byte-identically at its recorded generation clock after replaying source
acceptance with core SQLite writes denied. All four mixed-history cutoffs restored exactly,
and the project proposal was absent one microsecond before its admission. Core and source-review
databases, the original v1 queue and r3 archive remained unchanged; the frozen Amkor diagnostic
reproduced its original report bytes. All 353 original checksum entries passed with the ledger
unchanged. The original seven-facility diagnostic evaluator does not accept this unassigned project
as an eighth facility; a new study must state its project cohort, all comparison opportunities,
missing labels and available-time semantics explicitly.

Duplicate packet rebuilds for the same accepted comparison preserve the first admission and
disposition. A changed retained comparison under the same identity is rejected. This bounded
deduplication is not time-based hysteresis, automatic retraction after a later core correction,
or evaluated delivery readiness. Retained-content validation can run without original inputs,
but replaying source acceptance still requires the external source queue, reviews, capture paths
and pinned importer dependencies. The portable packet does not authenticate the database or publisher.

Final local validation passed 917 core tests and 12 web tests, including 61 new producer,
mixed-ledger and CLI regressions. Syntax compilation and whitespace checks also passed. Local
Python environments do not have the build frontend installed; the PR CI separately builds the
wheel and source distribution with its declared build tooling.
