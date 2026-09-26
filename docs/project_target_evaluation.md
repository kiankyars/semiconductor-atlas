# Accepted-project population and source-statement evaluation

This additive workflow evaluates source-native project target comparisons from the
[accepted claim store](source_project_targets.md) and the [unified review queue](alert_review_v2.md).
It does not evaluate realized production, physical capacity, calibrated forecasts, or user delivery.
The existing [seven-facility diagnostic evaluator](alert_evaluation.md) remains unchanged.

## Exact denominator and cutoffs

The supported route is `reviewed-source-native-project-target-v1` on the existing schema-5 core.
Freeze reads a coherent private SQLite backup and reconciles importer-code, source-family,
claim-method and ingestion-parameter markers. Conflicting markers, broken core integrity, or an
accepted review that cannot replay cause failure; they are not silently excluded.

The study supplies a complete review-path/hash manifest, not a hand-picked cohort. It must cover
**every supported-route accepted run strictly before `end`, including runs before `start`**.
Missing, extra, duplicate or mismatched references fail. The cohort is inferred from all these
accepted source-native PROJECT identities. A monitoring facility key does not assign a project to
that facility: Fab 2 remains separate from Fab 21.

| Retained population | Selection and meaning |
| --- | --- |
| Comparison packets and cohort | All supported-route core admissions with `accepted_at < end` |
| Accepted opportunities | Those comparisons with `start <= accepted_at < end`, whether or not an alert was admitted |
| Predictions | Source-project alert episodes first admitted with `start <= first_recorded_at < end` |
| Prior-window comparisons | Accepted before `start`; retained for lineage and possible in-window backfills, but excluded from opportunity denominators |

The window is half-open with timestamp precision preserved: an event exactly at `end` is excluded.
`end` must be no later than the actual snapshot start. The final freeze clock follows validation
and packet generation. Old retrieval times do not become earlier claim or alert admissions.

Reaffirmations remain opportunities even though the producer emits no proposal. Unadmitted
revisions also remain opportunities. A backfill can contribute an in-window prediction without
contributing an in-window accepted opportunity; the two denominators are intentionally different.
Legacy facility episodes are excluded from project predictions.

Each accepted review/run is one comparison unit. Repeated source transitions are grouped and
counted explicitly, not silently deduplicated or treated as independent confirmations. The unit
is an accepted comparison admission, not an independently observed external event.

The exact supplied v2 history is retained and validated. Later events present in that history
cannot affect the projection strictly before `end`, and events after freeze are rejected. A
queue comparison used at the cutoff must match the retained core comparison, not merely share
its run ID. Rebuilt packet generation clocks and internal packet hashes may differ; retained
semantic content must agree. A history file is not proof that every event in some external or
live queue was supplied.

## Freeze, retained validation, and source replay

The frozen artifact retains the study and history bytes, a hashed core ingestion inventory,
all selected derivative packets, cohort, opportunities, predictions, exclusions, cutoff head,
code fingerprints and explicit scope boundaries. A census exceeding the artifact size limit
fails rather than truncating the denominator.

There are three distinct checks:

- `freeze` verifies the supplied core snapshot and replays each accepted review through the
  existing producer using its local source dependencies. It accepts no new claims and does not
  migrate the core or mutate review queues.
- `verify` checks canonical frozen bytes, retained consistency, exact selection, cutoff
  reconstruction and pinned code versions. It does not read the original core or source corpus.
- `verify-sources` separately rebuilds from the supplied read-only core and original source
  dependencies. It checks that original inventory rows remain unchanged, the pre-cutoff
  comparison population matches, and the historical projection agrees. Later database additions
  are permitted only when those checks still hold.

The frozen artifact does not embed a complete core database or original publisher bodies.
Source replay still needs the accepted reviews, source queue, retained capture packets and body
paths, and the pinned importer/helper/migration versions. A portable consistency check cannot
authenticate core admission, reviewer identity, or completeness of the external publisher record.
Hashes and local replay do not establish independent verification or redistribution permission.

## CLI workflow

These are illustrative paths and contracts, not a record of a completed pilot. Replace the
placeholders with actual accepted run IDs, exact review-file SHA-256 values and the intended
completed UTC window. Review paths in the study are canonical paths relative to `--reference-root`.

```json
{
  "format": "semiconductor-atlas-project-target-study-v1",
  "study_id": "project-target-retrospective-study",
  "start": "2026-09-01T00:00:00Z",
  "end": "2026-09-02T00:00:00Z",
  "selection": "all_accepted_project_target_reviews_before_end",
  "reviews": [
    {
      "run_id": "<accepted run UUID>",
      "path": "review_plans/project-target-claims.json",
      "sha256": "<64 lowercase SHA-256 hex characters>"
    }
  ]
}
```

Use the existing acceptance receipts and review files to assemble the manifest; selecting only
comparisons that already produced alerts will fail the core census check. `reviews` is empty
only when there are no supported-route admissions before `end`.

```sh
python3 scripts/review_alerts_v2.py export \
  --database artifacts/unified-alert-review.sqlite \
  --output artifacts/project-study-history.json

python3 scripts/evaluate_project_targets.py freeze \
  --database artifacts/core-project-targets.sqlite \
  --study review_plans/project-target-study.json \
  --history artifacts/project-study-history.json \
  --reference-root . \
  --source-queue artifacts/source-review.sqlite \
  --output artifacts/project-target-population.json

python3 scripts/evaluate_project_targets.py verify \
  --frozen artifacts/project-target-population.json

python3 scripts/evaluate_project_targets.py verify-sources \
  --database artifacts/core-project-targets.sqlite \
  --frozen artifacts/project-target-population.json \
  --reference-root . \
  --source-queue artifacts/source-review.sqlite
```

The database opens in read-only mode. Freeze and score outputs are new-only files; existing files
are not overwritten. Frozen artifacts require their pinned implementation versions. Do not
replace recorded code hashes merely to make an older artifact pass under changed code.

## Post-freeze labels

Labels assess whether the retained source comparison supports the proposed revision, not whether
manufacturing later met the target. `reviewed_at` must be an actual time strictly after freeze
and no later than scoring. Declare prior exposure explicitly; a post-freeze timestamp does not
make an exposed reviewer blind or independent.

```json
{
  "format": "semiconductor-atlas-project-target-evaluation-labels-v1",
  "frozen_sha256": "<exact frozen population file SHA-256>",
  "adjudicator": "<reviewer identifier>",
  "reviewed_at": "<actual UTC review timestamp after frozen_at>",
  "prior_exposure": "<what this reviewer had already seen>",
  "coverage_statement": "<what was checked and what remains unreviewed>",
  "adjudications": [
    {
      "comparison_id": "<accepted run UUID>",
      "verdict": "supported_revision",
      "reason": "<source-statement finding, not a production-outcome assertion>",
      "review_sha256": "<supporting review file SHA-256>",
      "locator": "<location within that review supporting this comparison label>"
    }
  ],
  "supporting_reviews": [
    {
      "path": "project-target-adjudication.md",
      "sha256": "<supporting review file SHA-256>"
    }
  ]
}
```

Supporting review paths are relative to the labels file's directory, or absolute. Their exact
bytes are checked and retained in the report. Unknown or duplicate comparison IDs, unsupported
verdicts and unbound review references fail. Labels may address in-window opportunities and
comparisons underlying in-window predictions, including backfills; other excluded comparisons
cannot be labelled through this study.

The verdicts are:

- `supported_revision`: the source-statement revision is supported.
- `no_revision`: review establishes a no-revision comparison for this target.
- `unsupported_comparison`: the comparison is unsupported; this does **not** establish no change.
- `unresolved`: the reviewer cannot settle the source-statement question.

An omitted label remains `unlabelled`, not correct, false, or no change. Queue acknowledgment,
resolution and retraction are not outcome labels and cannot establish production attainment.

```sh
python3 scripts/evaluate_project_targets.py score \
  --frozen artifacts/project-target-population.json \
  --labels review_plans/project-target-labels.json \
  --output artifacts/project-target-score.json
```

Retain the exact frozen artifact, labels file, supporting review files and pinned code for score
replay. An embedded supporting review does not eliminate the CLI's referenced-file checks.

## Metrics and unknowns

Prediction precision and opportunity coverage answer different questions:

- **Conditional supported-proposal precision:** supported predictions divided by assessed
  predictions. Assessed negatives include `no_revision` and `unsupported_comparison`.
  Missing and unresolved labels remain in the total prediction denominator for lower/upper bounds.
- **Conditional no-change false-positive burden:** verified `no_revision` opportunities with a
  matched prediction divided by all verified `no_revision` opportunities. Unsupported comparisons
  are counted separately and excluded from this denominator. Unknown-label bounds condition on
  at least one genuine no-revision opportunity; they do not prove that such an opportunity exists.
- **Accepted expected-comparison admission coverage:** in-window comparisons expected to produce
  a proposal that actually have a matched alert by the cutoff, divided by all in-window comparisons
  expected to produce a proposal. This is mechanical workflow coverage, not detection recall,
  source completeness, or compliance with a routing deadline.

Ratios with zero denominators have `value: null`, not zero accuracy. A no-change point estimate
can remain undefined even when conditional bounds are shown. Reaffirmations remain in the full
opportunity population but are not expected to produce proposals.

Latency fields measure the selected after-document retrieval to actual core admission, and actual
core admission to alert-review admission. Publication first availability remains unknown;
`publication_to_alert_seconds`, publisher detection lag and detection recall remain null.
Retraction counts describe recorded actions, not verified correctness or timeliness of correction.

The study is explicitly retrospective and its cohort/window can be selected after seeing events.
Source-stated calendar bounds are not forecast confidence intervals. No blind evaluation,
independence, calibration, production-outcome, delivery, or roadmap Phase 3 gate is established
by a successful freeze, replay, label, or score.

## Verified local diagnostic, 2026-09-07

The [actual study](../review_plans/2026-09-07-project-target-population-v1-study.json) selects the
retrospective window `[2026-09-07T09:00:00Z, 2026-09-07T10:00:00Z)`. The coherent core inventory
has nine ingestion runs, of which one is an accepted supported-route comparison before the
cutoff. That is one source-native Fab 2 project, one in-window opportunity and one admitted
proposal; the Amkor baseline-facility alert is excluded. No eligible accepted comparison was
omitted from this core census. This does not mean that all external source changes were accepted.

The population was frozen at `2026-09-07T10:30:51.104446Z`. The root agent reinspected the four
retained narrative/timeline excerpts afterward, recorded an actual post-freeze review clock and
explicit prior exposure in the [support review](../review_plans/2026-09-07-project-target-population-v1-support.md),
and bound the [label](../review_plans/2026-09-07-project-target-population-v1-labels.json) to the
exact freeze. Both reviewers had seen the case beforehand. This is not a blind or independent
evaluation.

The label supports only NIST's Fab 2 planned-production target revision from 2028 to the second
half of 2027. Source-support precision and mechanical admission coverage are each `1/1` for this
single selected case, not estimates of general performance. There are zero reviewed no-revision
comparisons: the no-change false-positive denominator is zero and its rate remains null.
Detection recall and publication-to-alert lag also remain null. The selected after-document
retrieval-to-claim lag is `16,777.630722` seconds and claim-to-alert lag is `1,828.488683` seconds;
these are retrospective workflow durations, not evidence of early publisher detection.

Retained local artifacts, intentionally outside version control:

| Artifact | SHA-256 |
| --- | --- |
| `artifacts/2026-09-07-project-target-population-v1-frozen.json` | `f8856e3ccfc1ff783d7d258fee230d339838a732d6cad0cd426a33ec00a64ff1` |
| `artifacts/2026-09-07-project-target-population-v1-report.json` | `d739cdddd81bf7d249f13b19b04c9c3746fb9ea143ba8faa496d9cdfe5d36d51` |
| `artifacts/2026-09-07-project-target-population-v1-verification.json` | `8a421b4db2bafb0c66c411360b07355d5598af5b906f9f8f648abc960deae16f` |

The local verifier `artifacts/2026-09-07-verify-project-target-population.py` confirmed:

- the frozen history is byte-identical to the complete four-event live unified queue export;
- all nine original ingestion rows and the selected source acceptance replay from the read-only
  core, with zero core writes;
- the two source bodies and four excerpt spans match their accepted hashes;
- rescoring, including relocated frozen/label/review inputs, reproduces exact report bytes; and
- the four existing databases, pinned producers, r3 archive, original checksum ledger and legacy
  Amkor diagnostic remain unchanged; the legacy diagnostic also rebuilds byte-for-byte.

The full local suites passed 970 core and 12 web tests, including 53 new census/evaluation/CLI
tests. All 353 checksum-listed payloads passed. These checks verify this implementation and
retained pilot; they do not close broader coverage, independently adjudicated outcomes, blind
historical evaluation, calibrated forecasts, alert delivery or public-release gates.
