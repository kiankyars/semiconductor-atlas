# ASE K18B source-native milestone admission

The September 8 successor closes the retained K18B source-text-to-core handoff. It adds exactly
two source statements on one unresolved source-native project, not two physical observations,
production capacity, a revision to the seven-company baseline or a forecast result.

| Predicate | Source-reported status | Calendar bounds, inclusive | Precision |
| --- | --- | --- | --- |
| `milestone.groundbreaking` | Completed ceremony | 2025-10-03 to 2025-10-03 | Day |
| `milestone.building_complete` | Expected building completion | 2028-01-01 to 2028-03-31 | Quarter |

The ceremony's date is explicitly reviewer-derived: the paragraph's relative third-day statement
is bound to the article publication label, ROC `114-10-03`, converted to Gregorian `2025-10-03`.
That is a publication-context interpretation, not independently dated physical corroboration.
The completion statement refers to the **building**, not production start, HVM, qualification or
economically usable capacity. Both values retain null midpoints, null physical-effective dates
and null confidence. The CMS update has unknown timezone and does not date either event; first
publication of the completion target remains unknown.

The project is K18B only. It is not assigned to the full Kaohsiung campus aggregate, Dashe K28,
Nanzih Third Park, a canonical facility, an owner or a production unit. The existing NIST
production-target importer is unchanged; it cannot admit these different event types.

## Separate review and acceptance clocks

The [access review](../review_plans/2026-09-08-ase-k18b-kcg-source-review.json),
[text adjudication](../review_plans/2026-09-08-ase-k18b-source-text-adjudication.json) and
[claim-admission review](../review_plans/2026-09-08-ase-k18b-claim-admission-v1.json) remain
separate decisions. The last has SHA-256
`68f78bfc1536b07a601c48295fedd3e5b56832e74984501fd82c714e2f75283c`.

The source queue was handed off at `2026-09-08T21:01:34.022508Z`. Its subsequent unchanged
capture advanced the queue head, not the candidate's decision ID. The new importer validates
both identities, the text review's earlier compare-and-swap state, actual retrieval/import clocks,
all captured payloads and all three complete visible evidence blocks. Exact body hashes bind
the words, date context, publisher and project. Scope descriptions in the access review and
capture plan use different wording; each is checked against its own exact bounded wording.

New admission reserves the live queue with `BEGIN IMMEDIATE`, matches its complete history to
the frozen reviewed export, and holds that reservation through new-only successor installation.
It changes no queue events. Historical verification deliberately does not read or reserve the
live queue: later review work must not invalidate a correctly frozen historical snapshot.

Core admission occurred at **`2026-09-08T21:25:08.977607Z`**. Neither claim is visible one
microsecond earlier. Both appear in the knowledge-time source-claim view at admission; neither
appears in the physical-current view. Article publication, CMS metadata and source retrieval do
not become an earlier database-knowledge timestamp.

## Retained successor and replay

All artifact paths below are relative to the isolated `artifacts/core-v6-evolution` checkout.
The original scheduled checkout is not modified or rerouted.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `artifacts/2026-09-08-tsmc-source-quantities-schema7.sqlite` — unchanged parent | 241,917,952 | `131bcc1b1b6727cfefd4f02e7bbeeb90bdd168e528f5e78d7393892d3160a186` |
| `artifacts/2026-09-08-ase-k18b-milestones-schema7.sqlite` — successor | 242,032,640 | `ca0d973348301ee5f2d8f7c7e375838f56c224f8f46a1df6351d15ec9b366af3` |
| `artifacts/2026-09-08-ase-k18b-core-replay-v1.json` | 1,790 | `10a61ac9cdd69fc7ac5c82d0eead1b1a6a7d166dd1d1245055fe018e1cfc7f8c` |
| `artifacts/2026-09-08-k18b-core-independent-check-v1.json` | 7,027 | `58624bc66cfeaca2ab969bd5513913161554dd0cdba08484ab3dd4e473d19ab4` |

```sh
python3 scripts/admit_ase_k18b_milestones.py verify \
  --parent artifacts/2026-09-08-tsmc-source-quantities-schema7.sqlite \
  --review review_plans/2026-09-08-ase-k18b-claim-admission-v1.json \
  --reference-root /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  --expected-review-sha256 68f78bfc1536b07a601c48295fedd3e5b56832e74984501fd82c714e2f75283c \
  --output artifacts/2026-09-08-ase-k18b-milestones-schema7.sqlite \
  --expected-output-sha256 ca0d973348301ee5f2d8f7c7e375838f56c224f8f46a1df6351d15ec9b366af3
```

Here `--output` is the existing trusted successor being read, not a write destination. Verification
reconstructs the complete SQLite image from the parent, reviewed inputs and original admission
clocks, in memory. No schema migration is needed. A new `admit` call instead requires a matching
live `--queue`, a nonexistent output and no successor hash. The route rejects an already admitted
source/project; ongoing source-version refresh requires a separate append-only contract.

The producer binds all package Python/SQL, its CLI, runtime controller, interpreter and capture
dependencies, then rechecks inputs/code across installation or verification. Retain the committed
producer before changing it. The frozen-runtime controller's `ase-k18b-milestones` operation exposes
only `verify`, with no live queue or admission route. Replay currently uses the original evidence
paths; it is not a path-relocatable or hermetic operating-system archive.

## Verification and remaining gates

A separate read-only SQL comparison checked all 29 tables: all **543,075 parent rows** and the
complete schema remain unchanged. There are 104,460 total claims after adding the two milestones.
All four existing typed source quantities and all three numeric capacity rows remain unchanged;
those capacity rows remain announced-only. There are no new geometry, relationship, qualification,
utilization or yield claims. The original baseline, checksum ledger, analytics producer and
scheduled schema-5 database still match their retained hashes.

The earlier TSMC quantity successor and the issued guidance comparator both replayed with their
original frozen runtimes after this importer was added. The guidance vintage remains three issued
intervals, three abstentions and one excluded aggregate, with no new outcome or score. K18B was
not added retrospectively to that vintage or the registered detection study.

Thirty new K18B tests cover source interpretation, genuine offline capture replay, handoff
chronology, stale queue rejection, full parent preservation, input/code tampering, exact historical
reconstruction and replay after live queue advancement. A further runtime test checks the new
verify-only allowlist route. These are implementation tests, not independent manufacturing truth.
The full run completed 1,581 core tests in 250.425 seconds: 1,580 passed and one expected
optional-payload test skipped. All 12 web tests, syntax checks, `git diff --check`, and source/wheel
package builds passed.

Rights remain local-review-only. The new source records/evidence links retain short date literals,
original reviewer analysis and hash locators, not raw HTML, the full publisher paragraph or the
named ceremony participants. No redistribution or training permission is inferred.

Current manufacturing state, canonical facility correspondence, independently corroborated
realization, installed/qualified/usable quantities, durable monitored refresh and evaluated
forecast performance remain open. This admission advances the evidence-to-core chain; it does
not complete the supply-intelligence north-star.
