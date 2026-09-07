# Prospective milestone-timing benchmark sidecars

This is an additive Python API and [command-line workflow](milestone_benchmark_cli.md) for
**unfitted milestone-timing scenarios**, not a calibrated capacity forecast or an admission route
into the core claim store. It adds no tables, migrations, network requests, scheduled tasks, or
changes to the registered detector study.
All tests use invented engineering fixtures. No real study, vintage, or realized outcome is
created by adding this module.

The intended sequence is:

1. Review the exact source-native project/phase roster and declare held-out project groups,
   geographies and a future temporal split. Freeze it against the current source-claim store.
2. Freeze complete prediction/abstention rows against an explicit input cutoff, using actual
   current time as the prediction vintage. Save the result before outcome review.
3. Later, separately review every roster case, including unknown, censored and cancelled cases.
4. Verify the frozen input lineage against the read-only database, then score timing intervals.

The complete denominator means **every case in the declared roster**, not every publisher,
facility, project, or manufacturing event in the world. Reviewer identity, source interpretation,
project aliases, geography and selection completeness remain substantive review responsibilities.

## API and storage

The implementation is `semiconductor_atlas.milestone_benchmark`:

```python
study = freeze_study(connection, specification)
write_new("study.json", study)

vintage = freeze_vintage(
    connection, study,
    evidence_cutoff_at=actual_input_cutoff,
    horizon_end=future_calendar_date,
    predictions=complete_active_partition_rows,
    model_artifact=exact_model_artifact_bytes,
    configuration=explicit_json_configuration,
)
write_new("vintage.json", vintage)

# A separate later review, not part of prediction generation:
outcomes = review_outcomes(
    vintage, reviewed_by=reviewer, prior_exposure=exposure_disclosure,
    outcomes=complete_roster_outcomes, bodies=retained_body_bytes_by_sha256,
)
write_new("outcomes.json", outcomes)
report = score(connection, vintage, outcomes, bodies=retained_body_bytes_by_sha256)
write_new("report.json", report)
```

These variables are placeholders, not evidence or a runnable real-world example. Every artifact
has a format version and SHA-256 over its canonical content excluding its own `sha256` field.
`canonical_bytes(artifact)` produces UTF-8, sorted-key, indented JSON with a final newline.
Consumers accept these dictionaries or exact canonical bytes. Duplicate JSON keys, changed bytes,
unsupported fields and inconsistent nested references fail validation.

`write_new` stages mode-0600 bytes in the verified destination directory, fsyncs and re-reads them,
then publishes using an atomic no-replace hard link. Existing files and symlinks are never replaced.
The routine never deletes the destination on failure. A failure after publication can leave a
complete artifact requiring caller verification. Cleanup is restricted to the staging inode owned
by that call, not a competing writer's destination or replacement.
The intended parent pathname is reconciled with its pinned directory descriptor before and after
publication; moving/replacing the parent cannot silently publish into a moved directory and report
success for a different intended path.
Use a real, non-symlink parent directory; on macOS a temporary directory may need its resolved
`/private/var/...` path. Artifacts are limited to 20 MB. Source bodies remain in the caller's
existing local source storage. Outcome sidecars retain a sorted `source_bodies` manifest of exact
SHA-256/byte counts, not full publisher bodies or base64 copies. Review and scoring both require
caller-supplied local bodies that exactly match this manifest. There are no automatic downloads.
The sidecar marks its bounded excerpts `local_review_only_not_cleared_for_redistribution` and
`redistribution_authorized: false`. This is **not** a redistribution permission or public-release
format; even compact excerpts require their own rights decision before publication.

The module never changes database rows. Use a read-only SQLite connection with
`row_factory = sqlite3.Row`. Operations acquire a coherent read transaction and release it without
committing. An existing caller transaction is rejected rather than adopted or committed.
Schema 5 must already exist; no implicit migration or backup is performed. Tests deny all SQLite
INSERT/UPDATE/DELETE operations during registration, freeze, verification and scoring.

## Frozen study contract

`specification` has exactly these fields:

| Field | Meaning |
| --- | --- |
| `study_id` | Explicit bounded study identifier |
| `roster_scope` | Honest selection/population statement |
| `reviewer`, `reviewed_at` | Scope reviewer and declared review clock, no later than registration |
| `time_split_at` | Future UTC split; registration must finish strictly before it |
| `held_out_geographies` | Sorted, unique geography labels withheld from training and temporal test |
| `roster` | Complete sorted case array, maximum 1,000 cases |

Each roster case contains `case_id`, `project_entity_id`, `project_stable_key`, `phase`,
`event_type`, `project_group`, `geography`, `partition`, `scope_reason`, and `scope_claims`.

- The exact core entity must be a `project` with the specified stable key. This does not assign it
  to a canonical facility or owner. The source-native entity must already support the reviewed
  phase: v1 does not allocate an aggregate entity among multiple phases. One entity cannot be
  placed in different phase or split groups.
- `event_type` is exactly `production_start` or `high_volume_production`. There are no aliases:
  opening, mass production, qualification, commercial-production commencement and construction
  do not silently become either event. Adding another event requires an explicit contract change.
- `project_group` groups related source-native identities. All cases for one project/group stay
  in one partition and geography. Reviewers must conservatively group known aliases and phases;
  matching addresses do not establish identity.
- `partition` is `train`, `time_test`, or `geography_test`. A held-out geography can occur only in
  `geography_test`, and every geography-test case must use a held-out geography.
- `scope_claims` is a sorted, unique, nonempty array of
  `{"claim_id": "...", "value_sha256": "..."}`. These are exact accepted claim references,
  not caller-supplied values. The source-backed scope review must support the phase and geography;
  the software checks exact references, not the truth of that interpretation.

The study retains the exact specification, actual registration clocks, complete scope lineage,
content hash and relevant implementation hashes. Registration is not retrospective. Local clocks
and hashes are not externally attested timestamps or an authentication system.

## Prediction vintage and cutoff contract

The prediction vintage is the actual post-validation freeze clock. Callers cannot supply an older
vintage timestamp. `evidence_cutoff_at` must be no earlier than study registration and no later
than the actual freeze start. Prediction dates must be strictly after the freeze's calendar day
and no later than `horizon_end`. Thus newly collected historical evidence cannot create a
pre-outcome prediction. Same-day timing is conservatively unsupported.

Before the temporal split, every `train` case is active. At or after it, every `time_test` and
`geography_test` case is active. Each call retains all roster cases, explicitly marking inactive
ones. If the clock crosses the split while freezing, the call fails and must be retried.

`predictions` must cover the active set exactly once. Each row has:

```json
{
  "case_id": "engineering-placeholder",
  "input_claims": [{"claim_id": "accepted-claim-id", "value_sha256": "exact-value-hash"}],
  "prediction": {"low": "YYYY-MM-DD", "base": "YYYY-MM-DD", "high": "YYYY-MM-DD"},
  "abstention_reason": null
}
```

Dates and hashes above are schema placeholders. An abstention instead has `prediction: null`, a
nonempty `abstention_reason`, and an explicit input-reference array, which may be empty. No case
disappears because it has no usable inputs or prediction.

For every supplied reference the freezer reads the actual typed value and verifies its stored
hash and exact subject. Scalar and milestone inputs are supported; capacity or other typed input
graphs are rejected in v1. Root milestone inputs must be `expected` and match the case's exact
event type. Source-stated imprecise dates and unknown confidence/effective time remain unchanged;
the caller's timing scenario is separate and does not become a source midpoint or probability.

The retained lineage includes transitive claim dependencies, typed values, source evidence,
source records, documents, sources/families, entities/series, successful ingestion runs and their
document roles. Claims, document retrievals, source observations, entity/series creation and run
completion/admission must all be knowable by the cutoff. A future-dated dependency or document
fails even if the supplied root claims otherwise appear eligible. Superseded root inputs fail;
a supersession genuinely after the cutoff is hidden during historical replay.
Every project-bearing transitive input must belong to the same registered project group,
geography and partition as the prediction case. Unregistered or cross-group project dependencies
are conservatively rejected; a derived training root cannot launder a held-out project's inputs.
This checks structured claim lineage, not private model training or every mention within a
multi-project source document.

`verify_vintage(connection, vintage)` re-reads that exact graph at its cutoff. Missing/changed
rows, typed hashes, roles or references fail. Unrelated later claim additions do not alter the
frozen result. The graph is a selected-input closure, not a full database export or claim that
all relevant available information was selected.

The exact model artifact (maximum 1 MB), configuration and their hashes are retained. V1 only
supports a declared unfitted timing scenario; `training_release` stays null. The freezer does not
execute the artifact, prove that it generated the supplied predictions, audit private model
training, fit coefficients, or turn interval endpoints into quantiles. A fitted model workflow
requires a separate training-data/version/cutoff contract.

## Separate outcome review

`review_outcomes` samples its own actual start/admission clocks after the vintage. It requires an
exposure disclosure; it does not certify independent or blinded adjudication. The outcome array
must cover **all** roster cases in order, including inactive cases and abstentions.

Each row has `case_id`, `project_entity_id`, `phase`, `event_type`, `status`, `event_interval`,
`censor_at`, `cancellation_interval`, `reason`, and `evidence`. Exact identity fields must match
the frozen case without event aliases. Status/date combinations are:

| Status | Required dates | Meaning |
| --- | --- | --- |
| `observed` | `event_interval` | Reviewed report of the exact attained event |
| `right_censored` | `censor_at` | Explicit reviewed support that this event had not occurred through that date |
| `cancelled` | `cancellation_interval` | Separately dated cancellation, not a realized production event |
| `unknown` | All dates null | No usable outcome; absence of a report is not nonoccurrence |

Intervals contain `low`, `high`, and the source's `literal`. They are inclusive calendar bounds,
not probability intervals. No midpoint is generated. Nonmatching date fields must be null.
Observed, censored and cancelled cases require substantive source evidence. In particular,
`right_censored` is a reviewed nonoccurrence assertion, never an inference from a missing page.

Each evidence item has `document_sha256`, `document_url`, `published_at`, `retrieved_at`, `start`,
`end`, `excerpt`, and `locator`. Each excerpt is limited to 1,000 UTF-8 bytes. Offsets are zero-based
UTF-8 bytes with exclusive end. Exact caller-supplied body
bytes, excerpt bytes, public HTTPS URL and chronology verify; date literals must occur in the
evidence. Publication may remain null or retain date-only precision. Retrieval cannot be after
outcome review, and a reported event/censor date cannot be after the source retrieval date.
The actual outcome admission clock is separate from these declared source clocks.

These checks cannot infer that a passage truly supports the event, precision, phase allocation,
cancellation or nonoccurrence. A substantive review must preserve ambiguous temporal wording:
for example, “at the end of 2024” does not automatically mean December 31 or Q4. Unknowns must
remain unknown if defensible bounds are not available.

## What scoring measures

`score` first replays the frozen database lineage, then validates the later exact outcome package
against the required local `bodies` mapping. The sidecar alone is not a portable copy of publisher
sources; missing or changed source bodies fail scoring.
It retains every case, status and split. Only an observed event wholly after the actual vintage
and wholly within the horizon receives timing diagnostics. Pre-vintage/straddling events,
post-horizon events, horizon-straddling intervals, unknowns, censored cases, cancellations,
abstentions and inactive cases remain separately counted.

For an observed interval `[L, H]` and predicted base date `P`, signed timing error is the interval
`[P - H, P - L]` in days. Positive means the prediction was later. Mean absolute error and bias
are calculated only for exact-day outcomes, with their explicit conditional denominator.
Scenario-interval coverage is reported as *guaranteed* or *possible* containment/overlap; it is
not coverage of calibrated probability quantiles. Partition-specific counts and exact-day metrics
are retained, including null metrics for empty held-out sets.

There is no capacity metric, probability calibration, fitted-model comparison, multi-vintage
aggregation or claim of passing the roadmap's forecasting gate. A real evaluation still needs
a reviewed, adequately populated multi-project/geography roster, honestly frozen future vintages,
separate later realized outcomes, and a prespecified fitting/scoring policy. This module provides
the prospective evidence boundary without fabricating any of those observations.
