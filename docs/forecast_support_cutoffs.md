# Forecast input support at the knowledge cutoff

Every emitted experimental capacity series now validates the stored typed values
and transitive support of its input claims at the requested knowledge cutoff.
This applies to economically usable capacity as well as reviewed project ramps.
Usable capacity does not acquire an artificial production-start requirement.

## Reproduced historical leakage

An independent reviewer reproduced the defect with repository APIs in a fresh
in-memory schema-12 database. A scalar source statement was recorded on July 18,
2026, with a succeeded ingestion run completing and explicitly accepted on July
20. An economically usable capacity recorded July 18 depended on that statement.
The full database validator reported no errors. Nevertheless, at a July 19
knowledge cutoff the old bridge emitted one series and twenty quarterly points.
The evidence dossier already rejected that same unavailable run.

The bridge selected current root claims correctly, but only the capacity/event
scope-review path validated their deeper provenance. Already-usable capacity
does not require that review, so its support could bypass the cutoff check.

The retained same-input differential executes committed analytics from
`0ae62b194708a89b1c734ea1a60ceac8129c556c` and the repaired implementation:

| Synthetic query | Earlier implementation | Repaired implementation |
| --- | --- | --- |
| July 19, before supporting run completion | 1 series, 20 points | Explicit support exclusion |
| July 20, exactly at completion/acceptance | 1 series, 20 points | Same series and CSV bytes |

This is an engineering counterexample, not a real facility, observed production
outcome, historical forecast run or prediction-accuracy result. The shared ramp
model, models, readers, repository, database helpers and scope-review module were
verified unchanged against that commit. It is not a full historical OS/runtime
replay.

## Targeted validation and exclusions

`evidence_dossier.validate_claim_support` reads the selected roots and their
complete dependency closure directly from the database. It reuses the dossier's
typed-value, support-completeness, cycle and provenance-clock checks, including
source-record runs, claim/entity creation runs, explicit run acceptance and
run-linked documents. Unknown or unavailable support is not silently dropped.
Root world/current-view eligibility remains the forecast caller's responsibility;
the bridge still obtains that authoritative view inside one read snapshot.

The targeted closure does not pull in unrelated same-entity history. A separate
unavailable record therefore cannot suppress an independently supported usable
capacity series. A superseded ancestor can remain valid historical support; it
must have been known by the cutoff, but need not itself be a current root.
All timestamps are compared as timezone-aware instants, including microseconds.

Immediately before numerical evaluation, each selected capacity and any selected
production milestone are validated. Failure adds
`forecast_input_support_unavailable_or_invalid` with the specific validation
reason and emits no points or pairing for that capacity. Other supported inputs
remain eligible. Caller transactions, uncommitted work and existing savepoints
are preserved. The summary export describes this input-support rule.

The existing capacity/event semantic scope gate, period restrictions, cancellation
handling, root-cutoff checks and pure numerical model are unchanged. Validation
does not authenticate source bodies, establish semantic truth or independently
attest historical availability. Nullable ingestion-run references stay nullable;
when a run is referenced, its availability and succeeded state are checked.
This is not a new confidence score, usable-capacity observation or calibration.

## Real retained database and differential receipt

The read-only schema-12 database remains 310,259,712 bytes with SHA-256
`0f59f6bc9595effbc08c4eae54bd34606ae0ef6088205a0c3fe71d90aad1d599`.
All three capacity rows remain unchanged. The real result is still two
unreviewed or explicitly deferred Amkor scope pairings and one relative-capacity
exclusion, with zero eligible series. There is no real economically usable
numeric input in this selected population, so the synthetic repair does not
claim to remove a real forecast or establish zero physical capacity.

The comparison preserves the original Amkor review, its actual clock and SHA-256
`3f36e1027cbb6bebdbc4730039ec659f72544275f12e2e0efa23c9bdbcd6bbce`.
Forecast CSV, exclusions and pairings match before/after on both the unreviewed
and explicitly deferred real-data paths. The summary gains only the new support
rule description; older frozen outputs and producers are not rewritten.

Receipt: `artifacts/2026-09-10-forecast-support-cutoff-check-v1.json`, 7,900 bytes,
SHA-256 `8d5298b27be9c932570ebf62842db7af0185164f1761c14c6671470dff395b71`.
It binds the old/new code and unchanged shared modules, synthetic results, real
capacity rows, unchanged database/review hashes and zero real database writes.

Twelve new tests cover direct and transitive late runs, source-record and entity
runs, exact completion boundaries, equivalent timestamp offsets, unavailable
versus independently supported inputs, unrelated history, superseded ancestors,
explicit null acceptance, missing roots and caller savepoint preservation.
All 116 focused forecast/scope/dossier tests and all 12 web tests passed.

The complete core suite ran 2,320 tests in 322.291 seconds with no failures and
one skipped optional test. Its receipt verifies that every inventoried Python
source file was unchanged during the run:
`artifacts/2026-09-10-forecast-support-full-tests.json`, SHA-256
`4157718855c9559cb38c5a4a0e8d30460ef541c06a656f3c7186264c22de6ed1`.
Python compilation and `git diff --check` also passed.

An offline isolated build produced both source and wheel packages. The wheel-only
check verified 111 packaged files against the working source and loaded all 22
project modules from a temporary extraction under `python -I -S -B`, without
repository scripts or test imports. Its independently constructed schema-12
fixture reproduced the early exclusion and the unchanged twenty-point usable
series at completion, with no forecast-time database writes. Receipt:
`artifacts/2026-09-10-installed-forecast-support-check-v1.json`, SHA-256
`84c89cd6c4c5dad188ea7ceb2301f802f00c13849993b1e4cff91ce1f10d03bd`.
This establishes package-code isolation, not a hermetic operating-system runtime.

No collector, scheduler, study, core admission, migration or public release is
changed by this repair. Measured alert usefulness, broader prospective evidence,
real forecast/outcome pairs and calibrated supply-demand forecasts remain open.
