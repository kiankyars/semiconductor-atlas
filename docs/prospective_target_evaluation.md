# Separate outcome review and document-triage evaluation

This workflow adds a prespecified evaluation policy to the
[prospective shadow study](prospective_source_targets.md). It does not modify the
registered detector, collector, study window or prediction journal. It prepares
source-only material after closure and joins predictions only when separate,
evidence-bound labels are supplied.

The evaluand is **document-level triage of source-stated calendar manufacturing
target revisions**. It is not subject-level extraction accuracy, physical
manufacturing truth, publisher recall, or capacity-forecast accuracy.

## Register the scoring policy before observations

The [v1 policy record](../review_plans/2026-09-07-prospective-target-evaluation-v1-policy-record.json)
binds the actual September 7 14:32:00 UTC acceptance and independent replay,
before the September 8 start. The real command returned
`awaiting_sealed_population`, with zero packets, labels or scores. The existing
daily task now invokes that offline preparation step after shadow processing;
its schedule, source authority and no-label/no-score restrictions are preserved.
This saved task configuration does not prove scheduled execution.

```sh
python3 scripts/evaluate_prospective_source_targets.py register-policy \
  --registration artifacts/2026-09-07-prospective-source-targets-v1-registration.json \
  --reference-root . \
  --output artifacts/2026-09-07-prospective-target-evaluation-v1-policy.json

python3 scripts/evaluate_prospective_source_targets.py verify-policy \
  --registration artifacts/2026-09-07-prospective-source-targets-v1-registration.json \
  --policy artifacts/2026-09-07-prospective-target-evaluation-v1-policy.json \
  --reference-root .
```

The policy binds the existing registration, evaluation contract and transitive
producer hashes. Its separate acceptance receipt is sampled after saving the
policy and rechecking code and registration. Acceptance must precede the study
start. An unaccepted draft cannot be used for scoring. This is a locally checked
clock, not an external timestamp attestation. A necessary later code or scoring
change requires a separately disclosed version, not a silent policy repin.

## Prepare the source-only packet

```sh
python3 scripts/evaluate_prospective_source_targets.py prepare-review \
  --registration artifacts/2026-09-07-prospective-source-targets-v1-registration.json \
  --policy artifacts/2026-09-07-prospective-target-evaluation-v1-policy.json \
  --reference-root . \
  --output artifacts/2026-09-15-prospective-target-source-only-v1.json
```

Before the shadow population is sealed, this returns
`awaiting_sealed_population` without acquisition, labels or a packet. After
closure, it creates a new packet and post-write acceptance receipt. Later calls
verify the same packet instead of replacing it. `packet` is the strict one-time
creation command; `verify-packet --packet PATH` is the explicit replay command.
Neither command can freeze or shorten the study, accept claims, or fabricate
missing observations.

The packet is built from an explicit source-only projection of the full final
population, not by deleting a few fields from prediction batches. It contains:

- every final case, ordered independently of prediction status;
- the complete registered document roster, including documents with no checks;
- exact source URLs and bounded monitoring scope, not inferred facility identity;
- source clocks, actual predecessor references and neutral comparability metadata;
- full original bodies, deduplicated by SHA-256 and embedded as byte-counted
  Base64 objects; a successful first observation can retain its after-body but
  cannot become a comparable pair; and
- opaque policy, registration and seal hash commitments.

It excludes detector results and reasons, parser-support flags, extracted subjects,
suggested literals, detector-selected excerpts, recording timing, batch paths,
queue dispositions and collector text-change categories. Failed and incomplete
cases stay visible without invented evidence. No source bodies are fetched for
the packet, and its raw content remains local under the existing source rights.

A source-only presentation is **not verified blinding**. A reviewer with shared
repository or task-history access may already know predictions. The current
developer and agents have seen historical sources, detector rules and synthetic
outcomes. Reviewer identity, independence and exposure remain disclosed assertions,
not authenticated properties. Do not hand a reviewer the full seal or journal
while describing the packet as prediction-free.

## Label contract

Labels use `semiconductor-atlas-prospective-target-outcome-labels-v1`. The envelope
requires `packet_sha256`, `seal_sha256`, `policy_sha256`, `reviewer`, `reviewed_at`,
`prior_exposure`, `coverage_statement`, `target_scope`, `supporting_reviews` and
`adjudications`. `target_scope` must equal
`source_stated_calendar_manufacturing_targets`. Review time must be strictly
after packet acceptance and no later than the current local clock.

Supporting-review references contain canonical relative `path` and `sha256`,
resolved beside the label file. Their exact bytes must remain available. Each
adjudication uses the source-statement review fields `case_id`, `disposition`,
`reason`, `review_sha256`, `locator`, `reviewed_regions` and `targets`, plus the
explicit boolean `complete_scope_review`.

Each target is entered by the reviewer, not prefilled from detector output. It
contains `source_native_subject`, `milestone`, `formulation`, `scope`, `verdict`,
`reason`, and `before`/`after` evidence. Each evidence side has a literal, an
end-exclusive UTF-8 byte span and supporting context spans. Each span requires
`start`, `end`, `sha256` and a substantive `locator`. Both sides must use the
actual retained pair and fit within the reviewer-declared source regions.

The existing [source-statement evidence contract](source_statement_review.md)
validates exact bytes, visible-text literal support and scope-context containment.
Those checks do not authenticate the semantic judgment or prove that a declared
complete review is truly complete. Source dates are not inferred probability
intervals. Added, removed or ambiguously aligned targets that cannot be supported
by this two-sided schema remain unresolved; they cannot be labelled negative
merely because the detector abstained.

Truth is derived as follows:

| Review | Document-level truth |
| --- | --- |
| At least one evidence-supported `revision` | Positive, even if other scoped targets remain unresolved |
| Complete two-sided `reviewed_targets`, all `no_revision` | Negative within the declared target scope |
| Complete two-sided `no_relevant_scoped_target` | Negative within the declared target scope |
| Partial, unresolved, ambiguous or missing review | Unresolved or unlabelled, never negative |
| First, failed, blocked or incomplete observation | Uncomparable, never a reviewed negative |

One supported change anywhere in the reviewed scope establishes only that the
document deserved review. A candidate about one subject and a real revision
about another could satisfy this document-level criterion; it must not be
reported as correct subject extraction. Two formulations of one fab target are
not two independent detections.

## Score and interpret

```sh
python3 scripts/evaluate_prospective_source_targets.py score \
  --registration artifacts/2026-09-07-prospective-source-targets-v1-registration.json \
  --policy artifacts/2026-09-07-prospective-target-evaluation-v1-policy.json \
  --packet artifacts/2026-09-15-prospective-target-source-only-v1.json \
  --labels review_plans/prospective-target-outcome-labels-v1.json \
  --reference-root . \
  --output artifacts/prospective-target-evaluation-v1-report.json
```

The label path above is a future workflow example, not an existing real outcome
file. No real future label or score has been created by implementing these tools.

Every final case remains in the report. Accuracy uses all in-protocol,
collector-comparable document opportunities, including unsupported parser routes
and missing predictions. Separate counts show out-of-protocol and uncomparable
cases. Each exact registered URL has a report row even if it has no checks.

- **Resolved-label coverage:** resolved positive or negative labels divided by
  eligible opportunities. Unknown labels never silently become false positives,
  true negatives or false negatives.
- **Timely-decision coverage:** on-time candidate or quiet decisions divided by
  eligible opportunities. Abstentions, errors, late and missing outputs remain
  outside the numerator and inside the population.
- **Candidate-document support:** positive labels divided by resolved timely
  candidate labels. Bounds include unresolved candidate labels. This is conditional
  triage support, not exact-subject precision or independently proven truth.
- **Timely document sensitivity:** timely candidates with a positive label divided
  by every resolved positive opportunity. Positive cases with quiet, abstain,
  error, late or missing predictions are counted as operational nondetections.
- **False-positive document burden:** negative labels among all recorded candidate
  documents divided by eligible opportunities. Late wrong candidates still impose
  workload. A separate timely-only burden and unresolved-candidate upper bound
  prevent timeliness from erasing that cost.

The report provides a resolved/timely decision confusion matrix, all-recorded
candidate accounting, by-URL strata, original recording lag and exact source-pair
repetition diagnostics. Every actual check remains a workload unit. Global
value-pair deduplication is not used to erase recurrence such as A→B→A→B, and no
unique-event count or independence assumption is inferred from repeated pairs.

All zero-denominator ratios are null. In particular, zero reviewed positives do
not establish sensitivity. Source publication lag, publisher recall, exact-subject
precision, physical outcome accuracy and forecast calibration remain unscored.
No confidence interval, delivery gate, generalized performance or investment
conclusion follows from these conditional diagnostics.

Replay requires the policy and receipt, original registration and receipt, seal,
prediction journal, retained source inventory, packet and receipt, labels,
supporting reviews and pinned code. Output installation is new-only and excludes
the registered source and prediction directories. Input mutation or a late-disclosed
in-window opportunity causes failure; preserve the original artifacts and
investigate rather than rewriting the denominator.
