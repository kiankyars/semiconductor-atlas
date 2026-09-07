# Retained source-observation population

This inventory moves the denominator upstream of the
[accepted-project comparison census](project_target_evaluation.md). It includes retained exact-URL
document checks whether or not they created a review candidate, entered the source queue, or
became accepted claims. It does not infer target-statement revisions or physical production from
HTML changes. The collector, source queue, claim store and older evaluators remain unchanged.

## Selection and accounting

A study declares a source queue, capture/poll-state retention-root pairs, a completed half-open
UTC window and any original manual seed ledgers needed for predecessor support. The census is
the union of all queue-admitted capture paths, every capture directory in the declared roots,
and every durable polling intent in the declared state roots. Queue candidates are not the
selection criterion: they deduplicate content and omit unsuccessful checks.

This is complete relative to the declared retained inputs at freeze, not a complete publisher,
manual-acquisition or global manufacturing inventory. The study and window are retrospective;
the roots were not preregistered. Source observations not retained anywhere in these inputs
remain outside this denominator. A portable hash is not independent attestation of past storage
completeness or reviewer identity.

One completed capture produces one observation row per approved document. The row retains the
exact URL, monitoring scope, source family, plan hash, source-check outcome, document response
clock, capture assessment clock, queue admission clock, body/text hashes and actual predecessor.
The monitoring facility key is a routing scope, not a canonical assignment of every project
mentioned on a multi-project page.

The window uses completed-capture assessment time, preserving timestamp precision:
`start <= finished_at < end`. Older captures remain available for provenance; later completed
captures are explicitly outside the completed-observation denominator. An in-window durable
intent with no completed capture before the cutoff remains in an uncomparable stratum, even if
completion or import occurred later. Missing receipts never prove that no request occurred.

These units remain separate:

| Unit | Interpretation |
| --- | --- |
| Approved document check | Includes a policy-blocked assessment with no document request |
| Actual document request | Requires a retained attempt receipt; failure is not no change |
| Paired byte/text comparison | Only unchanged, markup-only or visible-text-changed collector results with retained predecessors |
| First observation | No predecessor supplied to this capture; not a detected transition |
| Poll plan outcome | `not_due`, expired review or failed preparation is not a fabricated document request |
| Original seed attempt | Retained separately; embedded copies of prior observations are not counted again |

Collector categories are not `supported_revision` or `no_revision` semantic labels. All target
revision verdicts remain null in this inventory. Repeated unchanged pages are not independent
truth cases, and unchanged visible text is not confirmation of a current physical state.

## Actual predecessors

Predecessors come from each packet's embedded `prior/source_checks.json`, its exact ledger hash,
URL, original response time and body hash. The nearest capture or queue event is not substituted:
imports may arrive out of order, failures can carry an older success, and two captures can share
one seed without forming a chain between themselves.

The September Amkor records demonstrate this distinction. Capture 01 and capture 02 both use
the original manual source-check ledger. Capture 03 uses capture 02, followed by the two polling
captures. The original seed has eleven attempts, including four failed policy attempts, and was
never separately imported into the source queue. Its prior bodies are evidence for comparisons,
not newly retrieved observations in each child capture.

## Freeze and historical source replay

```sh
python3 scripts/freeze_curated_observations.py freeze \
  --study review_plans/2026-09-07-curated-observation-population-v1-study.json \
  --reference-root . \
  --output artifacts/curated-observation-population-new.json

python3 scripts/freeze_curated_observations.py verify-sources \
  --frozen artifacts/curated-observation-population-new.json \
  --reference-root .
```

Freeze reads the full live source-queue export, validates every completed capture with the
existing validators, checks polling intent/plan/review bindings, inventories all retained files,
and repeats the collection after projection to detect drift. A completed but invalid packet
fails the freeze; it never disappears as a no-change observation. Incomplete captures with known
approved plans remain explicit. Symlinks, root overlaps, missing queue packets, duplicate capture
identities, future clocks and changed inputs fail. Outputs are new-only and bounded rather than
silently truncated.

Source replay checks the frozen queue history against the unchanged prefix of the current queue,
rehashes each original retained file, and reconstructs the exact old file inventory in a private
temporary directory. It replays the original captures, polling receipts and predecessors there,
then compares every snapshot and derived observation field. It rechecks original inputs and
code at the final boundary. New captures and appended queue events are permitted but cannot
rewrite the frozen population. Original evidence must remain unchanged and available.

The temporary reconstruction makes local files only; it neither calls a collector nor restores
or mutates the source queue. Source replay requires the original retained files and pinned code.
The frozen artifact retains source metadata and hashes, not the original publisher bodies; it
is not a standalone raw-source archive. No raw redistribution permission follows from hashing
or replayability.

## Verified September 7 inventory

The [actual study](../review_plans/2026-09-07-curated-observation-population-v1-study.json) selects
`[2026-09-07T00:00:00Z, 2026-09-07T10:00:00Z)`. The final local `r3` freeze was sealed at
`2026-09-07T11:08:02.151016Z` and binds 335 retained files.

| Completed document-check category | Count |
| --- | ---: |
| Unchanged raw bytes | 10 |
| Raw bytes changed; normalized visible text unchanged | 6 |
| First observation without a paired predecessor | 5 |
| Policy-blocked, document not requested | 1 |
| Visible-text change | 0 |
| Total approved document checks | 22 |

Those checks span twelve captures, eight exact URLs and five monitoring scopes. All twelve
completed packets were source-queue admitted by the cutoff. There are 21 actual document requests
and 55 total successful requests including policy checks. The original seed's eleven attempts
(seven succeeded, four failed) remain separate. Sixteen polling invocations produced eight
`captured_imported` and 44 `not_due` plan outcomes. Two invocations were forced; this does not
prove that any invocation was scheduler-triggered.

The sixteen paired collector comparisons have unchanged normalized visible text. This is a
small, repeated and previously exposed source sample, not sixteen independent no-revision
labels. No target statements were accepted or semantically adjudicated by this inventory.
The previously reviewed Fab 2 **July-to-September** target revision is not one of these sixteen
collector pairs: the TSMC collector began with a September first observation and then repeated
that version. It must not be scored here as either a detected transition or a miss without an
explicitly reviewed July anchor and appropriate denominator.

Local retained artifacts:

- `artifacts/2026-09-07-curated-observation-population-v1-r3-frozen.json`, 520,156 bytes,
  SHA-256 `857d95ff223d83e30666d0ad73481f4901ff84e579ae1cb8da767f63de4a6fd4`.
- `artifacts/2026-09-07-curated-observation-population-v1-r3-verification.json`, generated by
  `artifacts/2026-09-07-verify-curated-observation-population-r3.py`, SHA-256
  `d703ce0618b9be9269ad4fb8b56183b7c21369769be513c85d6b97818b5365c4`.

The earlier `v1-frozen` and `r2-frozen` files remain retained validation candidates. The first
predates historical replay of appended inputs; the second predates an additional replay check
that rejects a freeze timestamp earlier than any retained observation. Their real observation
counts and clocks were valid, but their pinned verifier versions differ. Do not replace their
code fingerprints or treat them as the final candidate.

The real verifier checks source replay, all category counts, the shared Amkor seed, unchanged
databases and pinned producers, and byte-identical rebuilding of both the previous project-target
and Amkor diagnostic scores. Nineteen focused tests cover unimported and interrupted captures,
failed/blocked checks, source drift, historical additions, cutoffs, predecessor selection and the
CLI's read-only/new-only boundary.
The final local suite passed 989 core and 12 web tests; all 353 checksum-listed payloads passed.

The next evaluation gate is source-native statement review over this frozen source inventory,
with explicit prior exposure and no-change/uncomparable cases kept separate. Genuine blind
evaluation still requires a prespecified unseen or future cohort and an independent unexposed
adjudicator. Publication availability, broad recall, realized production, calibrated forecasts
and alert delivery remain unestablished.
