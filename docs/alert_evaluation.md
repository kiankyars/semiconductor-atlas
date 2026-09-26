# Frozen alert-history diagnostics

This offline evaluator measures explicitly reviewed outcomes of admitted AI-critical alert
episodes. It does not establish blind historical performance, calibrated confidence, delivery
eligibility or completion of roadmap Phase 3. The window, cohort and outcome deadlines are chosen
retrospectively; merely writing a freeze file before a label file does not undo prior exposure to
the outcomes. All reports say `retrospective_diagnostic_not_preregistered` and leave the Phase 3
and delivery gates false, including reports whose reviewer declares blinding.

## Freeze the prediction population

Export the [portable alert-review ledger](ai_critical_alert_review.md), then run:

```sh
python3 scripts/evaluate_ai_critical_alerts.py freeze \
  --history artifacts/2026-09-07-ai-critical-alert-review-events-v1.json \
  --baseline baselines/ai_critical_manufacturing_v1.json \
  --study-id amkor-retrospective-admission-diagnostic \
  --start 2026-09-07T00:00:00Z \
  --end 2026-09-07T06:40:00Z \
  --output artifacts/amkor-diagnostic-frozen.json

python3 scripts/evaluate_ai_critical_alerts.py verify \
  --frozen artifacts/amkor-diagnostic-frozen.json
```

The output path must not already exist. The actual freeze clock must follow the completed
half-open window `[start, end)`. Every cohort episode first admitted in that window is included,
irrespective of review status. Older admitted episodes are recorded as exclusions; admissions at
or after `end` are outside the prediction population. Later acknowledgments and retractions do
not leak into the reconstructed cutoff state. Admission time, not source publication or a
retrospectively assigned effective date, is the detection clock.

The freeze retains exact baseline and history bytes, their hashes, the full seven-facility
cohort, prediction rows and the event-chain head at cutoff. Verification replays the embedded
release inventories and comparison packets without the original queue, source paths or release
directories. It pins the evaluator and its local validation dependencies; replay requires those
versions plus the baseline's already pinned producer, builder and map assets. Individual retained
input files are limited to 20 MB by this portable format.

This is a reproducible retrospective selection, not an externally attested freeze: the baseline
must be knowable by freeze time, not necessarily by window start. The retained export can contain
post-window events, although those events do not enter the cutoff predictions or decisions.

## Evidence-linked labels

Labels use `semiconductor-atlas-alert-evaluation-labels-v1` and bind `frozen_sha256`. Required
fields are `adjudicator`, an actual `reviewed_at` strictly after freeze, `blinding`
(`not_blinded` or `reviewer_declares_blinded`), a boolean `truth_inventory_complete`, a substantive
`coverage_statement`, and the following arrays:

- `supporting_reviews`: objects containing `path` and lowercase `sha256`. Relative paths resolve
  beside the label file. Exact bytes are verified and embedded in the score report. Rights to
  those bytes remain separate from technical portability; do not bundle raw publisher content
  merely because a file can be hashed.
- `truth_events`: objects containing `id`, `facility_key`, `rule_id`, `available_at`, `deadline_at`,
  `review_sha256` and `locator`. The facility and rule must be in the frozen contract. The locator
  should identify the review's evidence for scope, change and earliest source availability.
  Source observability is a reviewer judgment, not proof that the collector saw the source then.
- `adjudications`: objects containing `alert_id`, `verdict`, `truth_event_id`, `reason` and
  `review_sha256`. Verdicts are `supported`, `false_positive` or `unresolved`. A supported match
  must resolve to the same facility and rule, with source availability no later than admission.
  Other verdicts require a null truth-event reference. Missing judgments remain `unlabelled`.

Earlier source events can support newly admitted backfill alerts. They are labelled
`prior_window_event` and excluded from the window's recall population, not rejected as unsupported.
For recall, a truth event must become observable within `[start, end)` and have a deadline strictly
before `end`. A deadline equal to or later than `end` is right-censored, even if an early matched
alert has already been observed. No current evidence or later correction is silently substituted
for the frozen alert. Labels may use later evidence, but that does not change its admission time.

```sh
python3 scripts/evaluate_ai_critical_alerts.py score \
  --frozen artifacts/amkor-diagnostic-frozen.json \
  --labels review_plans/amkor-diagnostic-labels.json \
  --output artifacts/amkor-diagnostic-report.json
```

Scoring parses the exact label bytes whose hash appears in the report and rejects a change detected
during scoring. No command updates a queue, accepts canonical claims, makes network requests or
enables delivery. Retain the frozen file, exact label file and referenced review files together;
the score report alone is not a standalone replay package. Relative review references allow that
set to move to a different directory and reproduce identical report bytes.

## What the measurements mean

- **Assessed episode precision:** supported / (supported + false positive). This is conditional on
  resolved judgments, not population accuracy. Lower and upper bounds include all unresolved and
  unlabelled episodes. Zero denominators produce null values, not perfect scores.
- **Confirmed false-positive review burden:** false positive / all admitted episodes. Its upper
  bound also includes unresolved and unlabelled episodes. It is not a false-positive rate over
  no-change opportunities; that denominator is not observed here.
- **Timely event recall:** unique timely matches / mature in-window truth events. A point value is
  withheld unless the reviewer declares a complete truth inventory and all predictions have
  resolved judgments. With a complete inventory but unknown judgments, bounds preserve possible
  same-facility, same-rule, timely matches. These upper bounds can be loose when one unknown
  prediction could match several different events. Completeness is declared, not independently
  established by this tool.
- **Duplicates:** supported episodes can reference the same event, but that event contributes only
  once to recall. Separate unique-event and duplicate-match counts expose repeated review burden.
- **Lag and retraction:** each supported episode reports source-to-admission seconds and whether
  its chosen deadline was met. Retraction counts and admission-to-first-retraction time describe
  recorded actions, not whether a correction was right or promptly handled. False positives remain
  in the denominator after retraction; correction-to-retraction latency remains unknown.

All seven cohort rows remain visible with admitted-alert and labelled-event counts. Zero alerts or
zero labels is not evidence of complete monitoring or of no real manufacturing changes.

## Remaining Phase 3 gate

A genuine blind evaluation still needs a prespecified population and time windows, source-check
coverage, independently adjudicated outcomes including missed events, protected information
cutoffs, a frozen rule version, and measured precision, false-positive burden, detection lag and
retraction behavior. The retrospective diagnostics help expose mistakes in that measurement
machinery; they do not satisfy the gate or justify forecasting and investment-signal claims.

## Retained September 7 Amkor diagnostic

The [pilot record](../review_plans/2026-09-07-amkor-alert-evaluation-v1-pilot.json) binds the local
frozen artifact, [labels](../review_plans/2026-09-07-amkor-alert-evaluation-v1-labels.json) and score
hashes. It includes all seven facilities and the one episode admitted before 06:40 UTC. The known
Amkor groundbreaking supports the proposal, but its source-to-admission interval is
28,987,857.225248 seconds, approximately 335.5 days. It is an older-event backfill, not a timely
detection. The illustrative 24-hour deadline was selected retrospectively, not preregistered.

One supported episode out of one resolved label is not population precision. The truth inventory
is incomplete and the only labelled event predates the window, so timely-event recall is null.
No retraction was observed, and the live alert remains acknowledged. The report reproduced exactly
from relocated frozen, label and review files with network connections denied; the live queue
export and original `r3` archive remained unchanged. These checks validate the measurement and
replay mechanism, not the blind historical performance gate.
