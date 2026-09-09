# Interim monitoring coverage

The first accepted prospective batch was recorded on September 8. A read-only
audit now exposes operational coverage during the registered window without
changing its collector, detector, permissions, scoring policy or prediction
journal. This is an interim operations report, not a source-only review packet,
an outcome evaluation, an alert, or a final study population.

## Verified first-batch result

At the September 9 audit cutoff `2026-09-09T10:45:07.517569Z`, there was one
accepted batch, no unaccepted batches, and seven in-protocol document
opportunities. All seven had on-time acceptance receipts. These receipts prove
locally recorded execution, not the identity of the scheduler trigger.

| Operational measure | Result | Interpretation |
| --- | --- | --- |
| Timely recording | 7 / 7 | Includes recorded abstentions |
| Timely candidate-or-quiet decision, comparable documents | 4 / 6 | Two unsupported parser routes abstained |
| Timely candidate-or-quiet decision, all protocol opportunities | 4 / 7 | Also retains one policy-blocked document |
| Registered documents with no in-protocol opportunity | 0 / 7 | Not a successful-fetch or publisher-coverage claim |
| Unmonitored baseline facilities | 2 | SK hynix M15X and ASE Kaohsiung remain outside collection |

The four `no_candidate` outputs are the NIST TSMC, Samsung and Amkor award
pages, plus Amkor's Peoria deal-recognition page. They mean the registered
detector found no candidate calendar-target revision; no independent negative
label follows. Chandler's advanced-manufacturing page and Amkor's original
Peoria packaging-facility page have comparable retained source pairs but no
supported exact-URL parser. MTI's Micron speech was not fetched because its
policy gate blocked the route. The recording lags were 20.595185–43.679488
seconds from collector assessment to batch acceptance, not source-publication
latency.

Local report: `artifacts/2026-09-09-prospective-monitoring-status-v3.json`,
127,595 bytes, SHA-256
`17a5d99a26026123f6e2f3e29e23c40bf51dbf66223f0b83c8a200268d291011`.
Its audit finished at `2026-09-09T10:45:13.447202Z`. The earlier `v1` and `v2`
files are retained development checks, not the selected report or publication assets.

## Read-only audit command

Run from this isolated evolution checkout. The reference root is the protected
scheduled checkout. Choose a new output filename for each audit:

```sh
python3 scripts/audit_prospective_monitoring.py \
  --bundle /Users/kian/Developer/semiconductor_atlas/artifacts/2026-09-07-f901691-frozen-runtime-v1.json \
  --sha256 4e1c106943fd38c0a531e428325b9fb06585d3f6a25c26c92a21a92e5965ed38 \
  --commit f901691f9fba998d1e59b450e1f24aaba265f875 \
  --registration /Users/kian/Developer/semiconductor_atlas/artifacts/2026-09-07-prospective-source-targets-v1-registration.json \
  --reference-root /Users/kian/Developer/semiconductor_atlas \
  --output artifacts/NEW-prospective-monitoring-status.json
```

The controller stages the trusted historical runtime and one separately hashed,
fixed audit worker into a temporary directory. It uses isolated Python startup
and verifies every imported project module's origin and all staged source bytes.
All 22 registered producer hashes match this historical runtime; the 34 project
modules loaded by the real audit came from that runtime. No registered code is
repinned to the evolving checkout. This isolates source code, not the full OS or
network permissions, and is not a security sandbox.

The worker validates registration and its original pre-window acceptance,
replays every batch and receipt, rebuilds the full retained observation
population, and reconciles every opportunity using its earliest accepted
prediction. Unaccepted batches never supply decisions. Missing, late, error,
abstention, blocked, incomplete and out-of-protocol rows remain visible. Every
registered URL has a row even without an opportunity; unmonitored facilities
remain separate. A completed collector assessment can report a policy block
without any document request. Zero-denominator ratios are null.

The observation cutoff is the earlier of audit start and registered study end.
Journal receipts after audit start cause failure rather than entering an
as-of-start report. A receipt after the observation window but before audit
start may be valid during the registered grace period. A sealed journal requires
the existing final workflow rather than an interim report.

Directory listings, retained journal bytes, registration/receipt bytes, current
source inventory, code and interpreter identities are checked again. A detected
race fails instead of reporting a partial denominator. These repeated checks
are optimistic consistency checks, not an atomic filesystem snapshot or proof
that no transient concurrent mutation occurred. A rerun creates a report with
a new cutoff; this metadata report does not replace the retained population and
journal required for historical evaluation.

Output installation is new-only and excludes the study's source/poll/prediction
directories and exact protected inputs. The audit makes no network requests,
collector writes, prediction writes, labels, source claims or scheduler changes.
It emits hashes, paths and operational metadata, not source-page bodies or
detector target analyses. Do not give it to a reviewer as a blinded packet.

## MTI policy blockage: exact retained comparison

The September 8 run preserved `policy_changed_requires_review` for MTI terms
and `not_attempted_policy_blocked` for the Micron speech. Its only requests
were robots and terms; no retry or speech request was performed.

The old and new terms bodies each contain 377,361 bytes:

| Retained capture suffix | Body SHA-256 | `html_policy_v1` SHA-256 |
| --- | --- | --- |
| `896451f7f5d741e68247482a467559bd-3` | `58ef9d018774011afe805a7894f3daa54f920ef3c181e6f702afa0d09c1c6608` | `f8a2dd63e2813af8c6014adc0575fda827f5751bc3e740c3ede5d410d1fba270` |
| `196365d6962b4ab5a94aba8104151a74-3` | `d68e876912449340766ce3dff561f5403c40d2469c2fd6cb791cb8e9df2d5ef3` | `e5c6ce26e5c74e9e6409c732b956fd592e2bcf09199bf6ebecd8f390f2e8ed38` |

Both bodies are at `source_snapshots/curated-poll-v1/<suffix>/responses/mti-terms.body`
under the protected reference root. In the full normalized payload, replacing
exactly two `4 September 2026` footer dates with `8 September 2026` reproduces
every byte of the new normalized payload. The visible terms-update date remains
`22 October 2025`; normalized terms content and link inventory are otherwise
unchanged. This does not establish a changed legal term, a new reuse license,
or changed Micron manufacturing guidance.

The active whole-policy hash gate remains unchanged and blocked. A later,
separately reviewed policy normalization could distinguish identified site
boilerplate from terms content, but must retain sensitivity to actual terms and
link changes. This audit does not authorize a repin, a normalization bypass,
additional requests, or an in-window protocol amendment.

## Verification

Twenty-eight new tests cover all-opportunity and comparable-only denominators,
empty populations, incomplete checks, out-of-protocol checks, abstentions,
missing receipts, earliest acceptance, exact recording deadlines, exact study
closure, grace-period receipts, future-to-audit receipts, source/journal/receipt
races, output protection, staged-code changes and imported-module origins.
The synthetic audit fixture also verifies that network-call counts and every
persistent input byte remain unchanged. The controller's synthetic tests are
failure-path tests; the real frozen-runtime run separately verifies integration.
The final core suite executed 2,218 tests in 291.886 seconds: 2,217 passed and
one expected test was skipped. All 12 web tests passed. These are engineering
regressions, not evidence of manufacturing-change accuracy.

An independent standard-library check reproduced the batch counts and original
recording lags, then verified all 442 retained source files (10,588,849 bytes),
journal and registration bindings, report identity and four audit-producer
hashes. No outcome label or performance claim was derived. The original
checksum ledger, `r3` publication archive, pinned template and scheduled core
database still match their protected hashes; neither checkout has unmerged paths.
The original scoring-policy verification also passed in the frozen runtime,
loading 35 project modules and preserving policy SHA-256
`1dae1b766cfa8f041938657ae73ec923fdaa194eb02f75c6f2dac1ad7145f472`.

## Remaining decision gates

The fixed study ends September 15 at 00:00 UTC; its earliest seal remains
01:00 UTC after the registered recording grace. Independent source-bound
outcome review, meaningful positive-case coverage and measured alert utility
remain open. All accuracy, publication-lag and forecast-calibration fields in
this interim report are null. The next product work is separately versioned
review of unsupported exact-URL routes and policy-review robustness, while
preserving this study's original denominator and producer. Parser support must
not manufacture calendar targets from historical or undated assertions.
