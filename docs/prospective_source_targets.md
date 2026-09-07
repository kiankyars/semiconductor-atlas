# Prospective source-target shadow recording

This workflow pins a detector and source population before a future window, records
its outputs before outcome adjudication, and reconciles every retained document
opportunity at closure. It does not establish manufacturing attainment, early
detection, independent truth, forecast calibration, or delivery eligibility.

## Fixed future protocol

The [v1 study](../review_plans/2026-09-07-prospective-source-targets-v1-study.json)
selects September 8, 2026 00:00 UTC inclusive through September 15 00:00 UTC
exclusive. The stopping rule is fixed, not adapted to results. Predictions are
on time only if accepted within 3,600 seconds of the actual capture assessment
(or the retained intent clock for an incomplete opportunity). Closure starts
no earlier than September 15 01:00 UTC, after that grace period.

The population is derived from every document in pinned curated-poll v4, with
no manual document allowlist. Seven exact URLs cover five facility/project
scopes; SK hynix and ASE remain unmonitored. Existing plan rights, expiry,
identity and cadence gates remain in force. The study fits inside those review
windows but does not renew them. An ordinary check must bind the registered
configuration, plan, document identity and source scope. Forced checks, standalone
captures and other configurations remain visible outside the protocol.

The collector's 23-hour minimum spacing is not a promise of one check per day.
Missed invocations, not-due outcomes, incomplete work, failed checks and documents
without any checks remain distinguishable. There is no publisher-complete or
global recall denominator.

## Detector contract and exposure

`source_target_detector.analyze(url, before, after)` is a pure standard-library
parser. It uses the collector's actual retained predecessor, not a substituted
historical anchor. The four implemented exact-URL routes are:

| Source | Source-native subject and target |
| --- | --- |
| NIST TSMC Arizona | First, second and third fab; narrative and timeline formulations |
| NIST Samsung Texas | All award-project facilities, including Taylor R&D and Austin |
| NIST Amkor Peoria | Phase-unspecified award project; planned mass-production start |
| Amkor Peoria award article | Phase One; planned opening, not production start |

The Amkor groundbreaking article, Intel Chandler page and Micron MTI speech have
no implemented parser route. They abstain rather than disappear from the study.
Two TSMC formulations of one revised fab target are one candidate subject, not
two independently detected events. Source-native identities are not canonical
facility assignments.

The HTML grammar requires recognized page and project structures, aligned
subjects, exact calendar literals and stable project context. Byte offsets,
literal wording and hashes are retained. Added or unparsed date-bearing text,
negation, scope changes and ambiguous layouts abstain; unknown residual-text
changes also abstain. Hidden/script/template text is excluded by explicit HTML
rules, but external CSS and browser-rendered visibility are not verified.

`no_candidate` means the recognized aligned target literals did not change under
this narrow grammar. It is not complete natural-language temporal parsing or
proof that the publisher, project or physical world did not change. Relative
dates outside the recognized grammar do not become inferred calendar dates.

Development used already exposed historical pages and labels, including the
TSMC Fab 2 revision. Synthetic mutation tests and exposed-source smoke checks
are engineering checks, not a blind historical evaluation. The future sample
may contain no positive revisions; that would not establish recall. Future
labels must remain separate from the immutable predictions. Independent,
unexposed adjudication has not been established.

## Registration, recording and closure

The [retained registration record](../review_plans/2026-09-07-prospective-source-targets-v1-registration-record.json)
binds the actual September 7 13:39:37 UTC acceptance, before the future window.
Independent replay verified seven documents, four implemented routes and the
pre-start no-work result. All 369 prior source-file bindings passed and four
older reports rebuilt byte-identically. There were zero future predictions at
that verification; the record makes no scheduled-execution or performance claim.

Run from the repository root, with an existing empty prediction directory:

```sh
python3 scripts/shadow_source_targets.py register \
  --study review_plans/2026-09-07-prospective-source-targets-v1-study.json \
  --reference-root . \
  --output artifacts/2026-09-07-prospective-source-targets-v1-registration.json

python3 scripts/shadow_source_targets.py verify \
  --registration artifacts/2026-09-07-prospective-source-targets-v1-registration.json \
  --reference-root .

python3 scripts/shadow_source_targets.py advance \
  --registration artifacts/2026-09-07-prospective-source-targets-v1-registration.json \
  --reference-root .
```

Registration saves the study, exact configured population, baseline inventory,
source-plan bindings and transitive code hashes. Its separate acceptance receipt
binds the saved registration bytes at a locally observed post-write clock strictly
before the start. A draft without a valid receipt is not an active registration.
This is local prespecification, not external timestamp attestation. Local clock
integrity and arbitrary privileged filesystem edits are outside that claim.

`advance` performs no acquisition. Before start it returns `not_started`; during
the window and grace period it records unseen input versions; after the grace
period it seals once, then verifies the existing seal on later calls. It must run
after the ordinary curated poll terminates and before manual semantic review.
It never accepts claims, changes source-review dispositions or enables delivery.

Each batch retains a coherent rolling source inventory and every prediction,
including abstentions and execution errors. A separate receipt samples acceptance
after the batch is written and its registration/code bindings are rechecked.
Generation time alone does not establish timely recording. A batch without a
receipt is retained but uncommitted; it cannot supply an on-time prediction.
Historical execution errors are preserved without requiring the same runtime
exception to recur during replay. Successful deterministic predictions must
recompute exactly. Later queue admission cannot erase an earlier prediction.

The nested source inventories describe already observed bytes and retain their
original retrospective-inventory format marker. The outer pre-window registration
prespecifies future selection and detector code; the nested marker is not rewritten
to pretend those older producers were preregistered.

Closure reserves the actual collector queue lock and the shadow journal lock
through the final census and new-only seal write. It compares the current full
retention snapshot before accepting closure. Replay checks original retained
bytes and permits genuinely later observations, but rejects a late-disclosed
in-window opportunity that would change the sealed denominator. Preserve and
investigate such a failure; never silently overwrite the seal.

The final population retains on-time, late and missing predictions, outside-
protocol cases, superseded interim cases and documents without checks. Empty
populations are not no-change labels. Precision, recall, false-positive burden,
publication-detection lag and forecast calibration remain null. Recording lag
from assessment is not publication-detection lag.

## Scheduled operation and preservation

The existing daily 08:00 America/Los_Angeles task remains the collection owner.
The shadow step does not force a poll, change cadence or collect discovered
documents. Scheduled local operation requires the computer and app to remain
available; saved configuration is not execution evidence. See the
[official scheduled-task guidance](https://developers.openai.com/codex/app/automations).

During this study, do not edit the pinned producer files or source configurations.
Necessary changes require a separate version and explicit exposure accounting,
not repinning this registration after observations arrive. Retained source bodies,
queues, retrospective reports, original checksum ledger and r3 release remain
unchanged by shadow recording. Raw publisher bodies stay local. This workflow
does not authorize a new public data release or claim acceptance.
