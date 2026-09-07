# Source-native statement review

This additive review layer annotates the full
[retained source-observation population](curated_observation_population.md), including checks
that never became accepted claims. It asks what planned construction, opening or production
target the retained project text actually states, and whether that statement changed between
the exact before/after bodies supplied to a check. It does not equate a changed HTML hash with
a target revision or unchanged text with an attained physical milestone.

The output is a source-review coverage report. It does not admit claims, revise the baseline,
change queues, collect sources or authorize alert delivery. Existing collectors, claim
acceptance, alert ledgers and evaluators remain separate.

## Review inputs and evidence bindings

The review consumes a frozen population, a separate annotation file and the retained evidence
under an explicit reference root. The annotations bind to that exact frozen artifact; they are
not labels for whichever version of a source happens to be available later.

Each evidence-bearing annotation identifies its observation and the exact retained body, then
binds the relevant statement and scope context with byte offsets and SHA-256 hashes. Offsets
are UTF-8 byte positions with an exclusive end, not character positions, rendered-page offsets
or a search for a matching sentence in another document. Context is important: a table heading,
phase reference or preceding project description can change which subject a sentence supports.

A paired annotation uses only the observation's actual embedded predecessor. Neither the
nearest capture nor a more convenient earlier article may replace it. A missing predecessor
cannot be supplied retrospectively by relabeling an unrelated document. The frozen population
retains the predecessor's URL, body hash, response clock and original ledger binding.

The reviewer records source-native subject scope, milestone type, literal timing and the
relationship between the two statements. Scope annotations are reviewer assertions grounded in
the cited text, not certification of a canonical facility identity. A monitoring facility key
does not assign every project on a multi-facility page to that facility.

Review metadata must disclose prior exposure. The initial retained corpus and its collection
outcomes were already examined during implementation and source audits. Review performed after
the freeze is retrospective; the freeze does not make that review blind, independently
adjudicated or preregistered.

## Annotation contract

The Python entry point is `source_statement_review.review(frozen_path, labels_path,
reference_root=...)`. It returns a `semiconductor-atlas-source-statement-report-v1` report under
rule `retained-source-native-statement-review-v1`. Structured label objects use exact field
sets: unknown or missing fields are rejected rather than ignored.

The label file has these required fields:

| Field | Contract |
| --- | --- |
| `format` | `semiconductor-atlas-source-statement-labels-v1` |
| `frozen_sha256` | SHA-256 of the exact frozen JSON bytes |
| `reviewer`, `prior_exposure`, `coverage_statement`, `target_scope` | Nonempty reviewer-supplied text, not independently authenticated assertions |
| `reviewed_at` | Strictly after the freeze clock and no later than the local validation clock |
| `supporting_reviews` | Array of distinct `{path, sha256}` bindings to retained review records |
| `adjudications` | Array of observation-case annotations; omission leaves a case `unlabelled` |

Supporting-review paths resolve relative to the label file's directory. They must be canonical
relative paths without `..`; `--reference-root` instead resolves the frozen inventory's original
source paths. Each adjudication's `review_sha256` must identify a declared supporting review.
The review file's bytes are checked, but its authorship and semantic reasoning are not machine
authenticated. Reviewer text is bounded to 10,000 characters per field.

An adjudication requires `case_id`, `disposition`, `reason`, `review_sha256`, `locator`,
`reviewed_regions` and `targets`. Completed in-window document checks use their original
`observation_id` as `case_id`. An incomplete intent creates one deterministic case per planned
document: its identifier hashes `{"incomplete_at_cutoff": {"capture_path": ...,
"document_id": ..., "intent_started_at": ...}}` using the population's canonical hash helper.
Use the identifier generated from the frozen case, not the document ID alone. Unknown,
out-of-window and duplicate case labels fail validation.

| `disposition` | Interpretation |
| --- | --- |
| `reviewed_targets` | Eligible paired case with a nonempty `targets` array and reviewed project regions on both actual sides |
| `no_relevant_scoped_target` | Eligible pair with an empty target array and reviewed project regions on both actual sides |
| `uncomparable` | Required for any labelled first, failed, blocked or incomplete case; target array is empty |
| `unresolved` | A recorded unresolved adjudication of an eligible pair; target array is empty |

Each reviewed region is `{side, span}`, where `side` is `before` or `after`. A span has exactly
`start`, `end`, `sha256` and a nonempty `locator`. Body bindings are derived from the frozen case,
not supplied by the reviewer. Every referenced body must match both its observation hash and
the frozen file inventory's size and hash. Both `reviewed_targets` and
`no_relevant_scoped_target` require reviewed regions from each actual side, in addition to any
individual target's fragment and context citations.

Each target requires `source_native_subject`, `milestone`, `formulation`, `scope`, `verdict`,
`reason`, `before` and `after`. Subject, milestone, formulation and scope are nonempty
source-review descriptions, not members of a certified facility ontology. A target's two
evidence sides each contain `literal`, `fragment` and `context`. The fragment is a byte span;
context is a nonempty list of scope spans. The whitespace-normalized literal must occur in the
fragment's source text under full-body HTML normalization. Visibility is derived before slicing:
script/style contents, comments and attributes cannot become evidence by selecting bytes from
inside them. UTF-8 characters and HTML character references cannot be split. This is text
normalization, not browser rendering or CSS visibility certification. Fragments and contexts
must lie inside that side's declared reviewed regions. The report retains the supplied evidence
coordinates and derives both body bindings.

Target verdicts are `no_revision`, `revision` and `unresolved`. `no_revision` may reflect
reviewer-adjudicated equivalent rephrasing; it does not require byte equality. A `revision`
verdict is rejected when the full normalized source bodies are identical, or when the selected
normalized fragment and context texts are identical on both sides. Context ordering is ignored.
Different normalized text is only a necessary check, not machine proof of a material revision.
The reviewer must still explain the meaning and scope of the change.

This version requires a target on both sides. A newly added or removed statement must remain
`unresolved`, with both reviewed regions recorded, rather than fabricating the missing target,
calling it physical cancellation, or treating it as a no-target/no-change pair.

Duplicate URL/subject/milestone/formulation/scope groups within one case are rejected. The same
group may recur across cases and is counted as repeated coverage. The report also derives a
normalized evidence-pair identifier from its two normalized evidence sides, independently of
freeform subject labels. Grouping fields require canonical whitespace. These checks help expose
repeated evidence without implying independent observations.

## Denominators and unknowns

Keep these units separate:

| Unit | What belongs in it |
| --- | --- |
| Completed document check | Every in-window approved document check, including a policy-blocked check with no request |
| Eligible paired observation | A check with the retained before/after bodies needed for comparison |
| First observation | A retained body without a paired predecessor; useful source text, but not a detected transition |
| Blocked or failed check | An unsuccessful or unattempted check, not evidence of no change |
| Incomplete planned-document opportunity | Each approved document in a retained in-window intent that lacked completion by cutoff, not merely one row per invocation |
| Statement formulation | One explicitly scoped source formulation in one reviewed paired observation |
| Unique statement group | A grouping by exact URL, subject, milestone, formulation and scope; repeated checks do not create new unique groups |

Every frozen observation remains accounted for. Missing annotations remain unknown, rather
than disappearing from the denominator or receiving an implicit no-revision label. A first
observation, failure, blocked check or incomplete intent remains uncomparable even if a later
successful capture exists. It is not a false negative or a no-change example by default.

The population's half-open completed-capture assessment window remains authoritative. This
review does not replace it with article dates, reviewer timestamps, queue admission times or
the date an implementation was run. Incomplete intents retain their own cutoff accounting.

Several statements may occur in one paired observation, and one statement may recur in many
checks. Neither statement count nor paired-observation count is a count of independent truth
cases. In particular, two formulations in the same source narrative and timeline table are
not two independent confirmations.

## Semantic distinctions

A finding of no revision is narrow: the reviewer found no substantive change to the cited
scoped target in that particular pair, including any equivalent rephrasing. It does not
establish that the target remains achievable, that no other project fact changed, or that a
facility reached the target.

A finding of no explicit dated planned target must identify the reviewed project-text scope.
It is not a global assertion that the publisher supplied no schedule anywhere. Historical
groundbreaking language and an undated statement about construction progressing do not become
planned production-start dates.

Preserve literal calendar precision and modality. An expected opening in `2028` is not an exact
day. `End of 2027` must retain its qualifier rather than becoming an invented December 31
timestamp. Construction, opening, production start, qualification and realized output are
different milestones; an announcement cannot establish the latter states.

The initial source audits require these particular scope boundaries:

- **TSMC:** preserve separate project subjects and separate narrative/timeline formulations.
  A whole Arizona award page is not wholly about Fab 21. Future targets for other fabs must not
  be assigned to Fab 21 or read as attained production. The earlier July-to-September Fab 2
  revision is not one of the frozen collector's paired observations and cannot be inserted as
  a detection or miss in this report.
- **Samsung:** an all-facilities Texas schedule retains its award-wide scope, including the
  separate Taylor and Austin projects described by the source. Neither a Taylor URL nor the
  monitoring scope narrows that statement to the baseline Taylor two-logic-fab aggregate.
- **Amkor:** the company article's Phase One opening in `2028` and NIST's phase-unspecified
  mass-production start at the `end of 2027` differ in URL, milestone and scope. They are not a
  comparable cross-URL schedule revision. Preserve both source statements without synthesizing
  a delay or contradiction.
- **Amkor groundbreaking:** the scoped project text reports a past groundbreaking and a
  two-phase campus, but supplies no explicit dated planned construction-completion, opening
  or production-start target. That absence is a scoped review finding, not evidence of project
  cancellation or realized manufacturing capacity.

## CLI workflow

The CLI accepts the existing final `r3` population and a separate label file. For a new replay
output, choose an unused path:

```sh
python3 scripts/review_source_statements.py \
  --frozen artifacts/2026-09-07-curated-observation-population-v1-r3-frozen.json \
  --labels review_plans/2026-09-07-source-statement-review-v1-labels.json \
  --reference-root . \
  --output artifacts/source-statement-review-new.json
```

The output is new-only: choose a new artifact path for another run, rather than overwriting a
retained report. Keep the frozen population, annotations, original bodies, capture/predecessor
records and required implementation available for replay. Hashes and fragment text alone do
not make the report a self-contained archive or grant permission to redistribute raw sources.
Outputs inside retained source packets or polling directories are rejected, as are symlink-parent
aliases. Creating an extra file inside a packet could otherwise break its later manifest replay.

The review replays the frozen population's original sources before annotation and again before
returning. It rechecks the frozen JSON, label file, supporting reviews, referenced bodies and
implementation hashes across the review. Invalid or changed inputs fail rather than silently
reducing coverage. This is a bounded consistency check, not a general guarantee against every
possible concurrent filesystem race. Reports larger than 20 MB are rejected, never truncated.

Validation of retained bytes and their bindings establishes consistency with those inputs. It
is not a fresh request to the publisher, independent verification of past publication, proof
of complete publisher coverage or authentication of reviewer identity. Semantic scope and
label correctness still require review of the cited text.

The report's `coverage` is labelled cases divided by all cases, including explicit unresolved
and uncomparable adjudications. It measures annotation accounting, not semantic resolution or
target completeness; it is null for an empty denominator. Disposition and target-verdict counts
must be read alongside it. `alert_precision`, `detection_recall`, `false_positive_burden`,
`publication_detection_lag` and `forecast_calibration` remain null. No accepted-claim or alert
join is made by this layer.

## Verified initial review

The existing `r3` frozen inventory records 22 completed document checks: 16 eligible paired
observations, five first observations and one policy-blocked check. It records zero incomplete
planned-document opportunities at its cutoff. The separate
[labels](../review_plans/2026-09-07-source-statement-review-v1-labels.json) and
[review memo](../review_plans/2026-09-07-source-statement-review-v1-support.md) bind that population.

The generated report validates all 22 labels, with the following paired-observation accounting:

| Paired-observation annotation | Observation rows | Statement formulations |
| --- | ---: | ---: |
| TSMC explicit targets, unchanged within the supplied pair | 1 | 6 |
| Samsung all-facilities target, unchanged within the supplied pair | 1 | 1 |
| Amkor Phase One opening target, unchanged within supplied pairs | 5 | 5 |
| NIST Amkor mass-production target, unchanged within supplied pairs | 4 | 4 |
| Amkor groundbreaking text without an explicit dated planned target | 5 | 0 |
| Total eligible paired observations | 16 | 16 |

Thus eleven paired rows carry sixteen statement formulations. The grouping yields
nine unique URL/subject/milestone/formulation/scope groups: six TSMC, one Samsung, one Amkor
company opening target and one NIST Amkor target. These span six source-native subject scopes:
three TSMC subjects, one Samsung aggregate, Amkor Phase One and NIST's phase-unspecified Amkor
project. Repeated checks do not add unique targets or independent subject scopes.

The review identifies zero revisions in the supplied pairs. This does not show that the collector
can detect a revision, estimate recall, support calibrated forecasts or identify actual
production. First observations and the blocked check remain outside the comparable stratum;
missing annotations would remain unknown. Nine distinct normalized evidence pairs and seven
repeats do not constitute sixteen independent evaluations.

The final retained report is
`artifacts/2026-09-07-source-statement-review-v1-r2-report.json`, 167,720 bytes,
SHA-256 `df962ed559f5421acd2b2a0f2b2bcaedbda9d8337f667e36da55738eb331d8af`.
The initial `v1-report` remains an unchanged local validation candidate; it predates the added
HTML-character-reference boundary check. The label file is unchanged between these report
versions. The source population's pinned producer and all earlier release/evaluation inputs
remain unchanged.

Forty-one focused tests cover denominators, exact source and span bindings, first/failed/blocked/
incomplete cases, later additions, input drift, hidden HTML, evidence grouping and protected output
paths. The full local suite passed 1,030 core and 12 web tests, and all 353 checksum-listed payloads
passed. The retained verifier rebuilt this report twice byte-identically and rebuilt both older
accepted-project and Amkor evaluation reports without byte changes. Its result is
`artifacts/2026-09-07-source-statement-review-v1-verification.json`, SHA-256
`28d0ce843bbf26ba43c420f1d6953d36e3b6e44ce3e49d6f967eb547721e5619`.

The next evidence step is a separate all-three retained July-to-September NIST comparison
cohort, followed by ordinary due collection. It must not rewrite the existing population's
predecessors. Genuine blind evaluation still needs unexposed adjudication and a prespecified
unseen or future cohort; calibrated forecasts and delivery remain later gates.
