# Selected source-vintage review

This additive layer compares selected historical NIST award pages with the latest eligible
successful observations of the same exact URLs in a
[frozen source-observation population](curated_observation_population.md). It makes a retained
July-to-September comparison explicit without pretending that the collector used July as its
predecessor. It does not collect sources, mutate original observations, admit claims, revise
the manufacturing baseline or score an alert detector.

The design is `selected_retrospective_cross_vintage_review_not_independent_or_blind`. It covers
the complete exact-URL intersection of two declared retained inputs, not all publishers,
facilities, target statements or historical changes. The selected inputs and reviewers were
already exposed to the known TSMC Fab 2 change.

## Study and API contract

The study has exactly these fields:

| Field | Meaning |
| --- | --- |
| `format` | `semiconductor-atlas-source-vintage-study-v1` |
| `study_id` | Nonempty identifier included in comparison-case identity |
| `before_manifest` | `{path, sha256}` binding to the historical source snapshot's `manifest.json` |
| `after_population` | `{path, sha256}` binding to the exact frozen source-observation population |
| `selection_rule` | `all-shared-nist-award-urls-latest-eligible-response-v1` |
| `selection_rationale` | Explicit reviewer-supplied explanation of the retrospective selection |

Study bindings are canonical paths relative to `reference_root`; absolute paths, `..` and hash
mismatches fail. The historical manifest must retain a parseable `retrieved_at` and a nonempty
`retrieval_timestamp_basis`.

The entry points in `semiconductor_atlas/source_vintage_review.py` are:

- `freeze(study_path, reference_root=...) -> dict`: validate retained inputs and build the
  selected cohort without writing it.
- `validate(frozen_path, reference_root=...) -> (dict, bytes)`: validate canonical cohort
  bytes, pinned code, clocks and exact source replay.
- `review(frozen_path, labels_path, reference_root=...) -> dict`: validate the cohort and
  bind retrospective source-statement annotations without accepting claims.
- `write_new(path, data, reference_root=..., protected_directories=...) -> dict`: write a
  new artifact and return its path, byte count and SHA-256.

The cohort format is `semiconductor-atlas-source-vintage-cohort-v1`. It retains the study bytes,
freeze clocks, selected snapshot and its hash, implementation hashes and explicit false claim,
identity, performance and delivery boundaries. The review format is
`semiconductor-atlas-source-vintage-review-v1`.

## Selection, failures and ties

All inputs in the historical source snapshot are verified before selection. A historical
candidate must be an HTML `award_detail_page` at an exact HTTPS `www.nist.gov/chips/<slug>` URL
without query or fragment. Index pages and other inputs remain explicitly excluded; an invalid
input cannot disappear merely because it is not selected. Duplicate historical award URLs fail.

The after-side URL inventory includes every in-window completed document check and each
incomplete planned-document case. Intersect that inventory with the historical award URLs
before filtering for successful content. This keeps failure-only and incomplete-only
intersections visible as uncomparable URL cases instead of silently removing them.

For each shared URL, an eligible after observation requires a successful transport, retained
body/text hashes and body path, a response-completion clock, and one of the successful collector
statuses. A first observation can qualify here: the separately selected historical manifest
supplies this new comparison's before side. It remains a first observation in the original
collector inventory.

Select by the latest actual `response_finished_at`, preserving timestamp precision, not by
queue admission, capture assessment, article date or filesystem order. Keep all shared checks
and incomplete cases alongside the selection. The possible URL-case statuses are:

| Status | Treatment |
| --- | --- |
| `paired` | Historical knowledge clock precedes the selected successful response; eligible for paired target review |
| `no_eligible_successful_after` | Shared URL is retained but no usable after body exists; uncomparable |
| `ambiguous_latest_successful_bodies` | Latest response clocks tie but body/text hash pairs differ; no arbitrary body is chosen |
| `nonincreasing_knowledge_clocks` | Selected response does not follow the historical knowledge clock; uncomparable |

When latest responses tie with the same body and text hashes, the observation with the smallest
sorted ID is the deterministic representative, and all tied IDs remain recorded. A tie with
different raw bodies remains ambiguous even if their normalized text hashes agree.

Case IDs bind the selection rule, study ID, exact URL, before manifest/body hashes, after
population hash and latest tied observation IDs. They are not the original collector
`observation_id`. Excluded historical URLs, non-award historical inputs, after checks outside
the intersection and outside incomplete cases remain in the cohort and report.

## Clock and predecessor boundaries

The July source directory is named `2026-07-17-open-seed`, but its corrected manifest clock is
**2026-07-18T01:54:47Z**. The declared basis is a conservative batch-completion correction using
the archived Overpass response file's completion mtime. It is not an exact NIST request clock,
publication date or independently attested time at which a particular target changed.

Historical batch completion, document response completion, capture assessment, queue admission,
population freeze, selected-cohort freeze and review are different clocks. The after inventory's
original half-open assessment window remains authoritative; this layer selects among its
eligible responses. Freeze cannot precede its retained input knowledge clocks, and review must
follow the selected-cohort freeze without claiming a future review time.

Every case states that its before side is a selected manifest anchor, not the after
observation's actual collector predecessor. Original predecessor paths, hashes and source
checks remain unchanged. The known July-to-September Fab 2 change can be reviewed in this
selected cohort; it cannot retroactively become a collector detection or miss in the original
repeat-observation cohort.

## Evidence and semantic annotations

The label object reuses the exact `semiconductor-atlas-source-statement-labels-v1` schema
documented in [source-native statement review](source_statement_review.md). Here
`frozen_sha256` binds the selected-vintage cohort, and `case_id` identifies its URL case.
`supporting_reviews` paths resolve relative to the label file's directory, not the source
reference root. Review author, prior exposure, target scope and rationale are declared and
retained; file hashes do not authenticate reviewer identity or the semantic judgment.

Adjudications use `reviewed_targets`, `no_relevant_scoped_target`, `uncomparable` or `unresolved`.
Unpaired URL cases must remain `uncomparable`. Missing labels remain `unlabelled`; they are not
implicit no-revision judgments. Only `reviewed_targets` carries a nonempty target array.
Both target review and a no-target finding require nonempty reviewed source regions on both
selected sides.

Each target retains `source_native_subject`, `milestone`, `formulation`, `scope`, `verdict`,
`reason`, `before` and `after`. Group descriptions use canonical whitespace. Evidence sides
have a literal, fragment span and nonempty context spans. Spans use end-exclusive UTF-8 byte
offsets, SHA-256 and locators; they must lie within their side's reviewed regions. Original body
size/hash bindings and full-body HTML context are checked. Hidden script/style text, tag
attributes or a split HTML character reference cannot be substituted for source text.

`no_revision` may preserve equivalent rephrasing. `revision` requires a whole-document visible
text change and different normalized target/context evidence. Those conditions reject certain
forged labels; they do not prove a semantic revision. The reviewer still has to inspect the
project narrative, timeline and scope. A target missing on one side remains unresolved: v1
requires paired formulations and cannot represent addition or removal by inventing the absent
side's evidence.

Whole-document change categories are mechanical: `unchanged_raw_bytes`, `raw_bytes_only` or
`visible_text_changed`. Navigation and unrelated text can change while all scoped project
targets remain unchanged. Conversely, an unchanged literal date with changed scope is not
automatically the same target.

Subject scopes are reviewer descriptions, never canonical facility assignments. Preserve TSMC
Fab 1, Fab 2 and Fab 3 separately, and retain narrative and timeline formulations without
counting them as independent events. Samsung's all-facilities Texas target is not the baseline
Taylor two-fab target. NIST Amkor's phase-unspecified mass-production target is not the company
article's Phase One opening target. Source-stated schedules, including stale schedules, do not
establish current construction, qualification, utilization or production.

## CLI and replay

The initial `v1` cohort, labels and report are retained. To create another cohort candidate,
use a new output name; this illustrative freeze has a new clock and requires labels bound to
its exact bytes before review:

```sh
python3 scripts/review_source_vintages.py freeze \
  --study review_plans/2026-09-07-nist-source-vintage-v1-study.json \
  --reference-root . \
  --output artifacts/nist-source-vintage-new-candidate.json
```

Verification of the retained cohort is rerunnable and creates no output artifact:

```sh
python3 scripts/review_source_vintages.py verify \
  --frozen artifacts/2026-09-07-nist-source-vintage-v1-frozen.json \
  --reference-root .
```

To rebuild the retained review, use its original cohort and labels with a new report path:

```sh
python3 scripts/review_source_vintages.py review \
  --frozen artifacts/2026-09-07-nist-source-vintage-v1-frozen.json \
  --labels review_plans/2026-09-07-nist-source-vintage-v1-labels.json \
  --reference-root . \
  --output artifacts/nist-source-vintage-v1-rebuilt-report.json
```

Freeze and review write new files only. The output parent must already exist; existing targets
are not overwritten, parent traversal is rejected, and outputs inside protected source,
capture or polling directories are rejected. Choose another new name for each subsequent
rebuild. Verification prints its replay result without writing an artifact. No command makes
network requests or updates source queues.

The implementation replays the historical snapshot and frozen after population, repeats
collection to detect drift, and checks study, cohort, labels, evidence and implementation bytes
at the relevant return boundaries. Invalid or changed inputs fail rather than shrinking the
selection. This is bounded local consistency validation, not a universal concurrent-filesystem
race guarantee. Cohorts and reports larger than 20 MB fail instead of being truncated.

Replay requires the original manifest and its inputs, frozen after population and its retained
sources, study/cohort, labels/supporting reviews and pinned code. The cohort is not a standalone
raw-source archive. Neither hashing nor verification grants raw redistribution permission or
proves publication availability, historical storage completeness or independent corroboration.

## Verified September 7 selected cohort

The [actual study](../review_plans/2026-09-07-nist-source-vintage-v1-study.json) binds the July
manifest and the final September `r3` source inventory, whose after window is
`[2026-09-07T00:00:00Z, 2026-09-07T10:00:00Z)`. The selected cohort was frozen at
`2026-09-07T12:22:52.936322Z`; the labels record review at `2026-09-07T12:23:19.316401Z`.
The [supporting review](../review_plans/2026-09-07-nist-source-vintage-v1-support.md) preserves
the target scope, evidence rationale and prior-exposure disclosure.

The verified cohort binds 369 retained source files and validates all 33 historical inputs.
Of those inputs, 28 are NIST award-detail URLs; the other five are outside the historical award
selection. Three exact URLs intersect the frozen September inventory: TSMC Arizona Phoenix,
Samsung Texas Austin and Amkor Arizona Peoria. The remaining 25 historical award URLs are
explicitly unmatched, not no-change cases.

The 22 September document checks partition as follows:

| After-check stratum | Count |
| --- | ---: |
| Selected successful observation, one per shared URL | 3 |
| Earlier eligible successful observations of shared URLs | 5 |
| Shared policy-blocked check | 1 |
| Checks outside the historical exact-URL intersection | 13 |
| Total | 22 |

Thus nine shared checks remain visible even though only three successful after observations
are selected. There are no incomplete document cases in these declared retained inputs. All
three selected whole-document pairs have changed normalized visible text; that mechanical
comparison does not establish three target revisions.

The report contains adjudications for all three paired URL cases, with no unlabelled cases,
eight target formulations and five reviewer-described source-native subject scopes:

| Recorded scoped review | Revision formulations | No-revision formulations |
| --- | ---: | ---: |
| TSMC, separate narrative and timeline formulations across three subjects | 2 | 4 |
| Samsung, all-facilities operational target | 0 | 1 |
| NIST Amkor, phase-unspecified mass-production target | 0 | 1 |
| Total | 2 | 6 |

The two changed TSMC formulations describe one revised Fab 2 subject scope within one URL case,
not two independent changes or manufacturing events. Samsung and Amkor's scoped project sections
are unchanged despite whole-document text changes. Environmental goals, undated construction
language and other out-of-scope areas remain distinct from manufacturing schedule targets.
These are verified counts of the retained reviewer annotations, not independently established
semantic truth or physical outcomes.

Local retained artifacts:

- [Frozen cohort](../artifacts/2026-09-07-nist-source-vintage-v1-frozen.json), 175,652 bytes,
  SHA-256 `62d192522d38cd65f646f21f0ffd0568ef0d4f7a86aafc4d9e30294c6110dd93`.
- [Labels](../review_plans/2026-09-07-nist-source-vintage-v1-labels.json), 22,913 bytes,
  SHA-256 `5449a5a79571bcb31ed1406d6c23243add3f940f2fd0846a559b820c275a6952`.
- [Review report](../artifacts/2026-09-07-nist-source-vintage-v1-report.json), 133,044 bytes,
  SHA-256 `e1ae2375cce8dbb6aa7ad6fb4475849fff073d9682cc8bfbe2d533469506c217`.
- [Verification record](../artifacts/2026-09-07-nist-source-vintage-v1-verification.json),
  10,399 bytes, SHA-256 `0a38c115fd17540257a22f67cbb30cb87cbcbc504e13140a109b9eb9db93617b`.

The [retained verifier](../artifacts/2026-09-07-verify-nist-source-vintage-v1.py) records exact
source replay, two byte-identical rebuilds of the new report, preserved actual collector rows
and predecessors, unchanged stores and pinned producers, and byte-identical rebuilding of the
earlier source-statement, project-target and Amkor reports. It made no network requests and
published nothing. A separate rerun of the cohort's read-only `verify` command also passed.

Thirty-four new regression tests accompany this layer. The full validation run passed 1,064
core tests in 124.830 seconds and 12 web tests in 0.028 seconds; all 353 checksum-listed payloads
passed. These engineering checks establish the reported local validation, not detector
performance, reviewer independence or manufacturing attainment.

## What this establishes, and the next useful gap

This is a reviewable retained-source comparison with explicit selection, exclusions and
evidence. It is not an unseen evaluation or detector output. Alert precision, detection recall,
false-positive burden, publication-detection lag and forecast calibration remain null. A
verified source-statement revision does not establish attained production, canonical facility
identity, claim acceptance or alert delivery.

The next useful step is **source-target detector performance over prespecified future
observations**, including changed, unchanged, failed and uncomparable cases. Freeze the cohort,
target definitions, observation window and detector outputs before outcome adjudication;
separate actual detector proposals from reviewer labels and disclose adjudicator exposure.
Measure denominators and unknown outcomes explicitly. Another repeat-only sample or another
retrospective review of the known Fab 2 change would not establish that performance.
