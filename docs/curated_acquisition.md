# Repeatable reviewed-URL acquisition

The fixed-URL collector turns a reviewed acquisition plan into an immutable local source-check
packet. It checks access and rights policies before requesting the bound source documents,
retains exact response bytes and failed attempts, and compares content with prior observations.
It neither accepts claims nor creates delivery-eligible alerts.

## Run and replay

Use a source checkout and a fresh output directory. The reviewed Amkor v2 plan covers exactly two
issuer articles and one NIST award page. Its internal review deadline is October 7, 2026 at
02:50:12 UTC; that deadline is not a publisher-granted permission period.

```sh
python3 scripts/capture_curated_sources.py capture \
  --plan acquisition_plans/amkor_peoria_v2.json \
  --prior-checks source_snapshots/2026-09-07-amkor-repeatable-capture-02/last_successful_checks.json \
  --prior-root source_snapshots/2026-09-07-amkor-repeatable-capture-02 \
  --output source_snapshots/amkor-next-reviewed-check

python3 scripts/capture_curated_sources.py verify \
  --run source_snapshots/amkor-next-reviewed-check
```

Omit both prior arguments for a first observation, or supply a previously validated manual
source-check ledger with its response root. Offline verification makes no network requests and
does not require the plan to remain unexpired today: it checks that acquisition occurred within
the recorded review window. An existing output is never overwritten. Interrupted runs retain
completed response bodies and per-attempt receipts, but are not valid completed packets.

For subsequent runs, pass **`last_successful_checks.json`**, not just the immediately preceding
`source_checks.json`. The history file carries the last content-eligible observation through
timeouts, blocked policies, and responses missing the reviewed identity text. It preserves the
original retrieval clocks and copies all necessary prior bytes into the new packet. If no eligible
document has ever been observed, there is no history file; use that run's source-check ledger.
This is observation history, not evidence-review acceptance or a claim-truth ledger.

## Review and access boundary

Plans bind an exact review-record hash, facility scope, document URLs, same-origin access and
rights policies, media types, document identity markers, rate, size limit, and validity window.
The collector issues identified HTTPS GETs, disables ambient curl configuration, and does not
follow redirects, retry requests, use cookie sessions, or discover more URLs. Requests are serial
with the configured pause after each completed request.

Policy checks compare normalized content with previously reviewed hashes. They do not interpret
new policy language, automatically renew permission, or treat an unchanged copyright notice as a
license. Any failed or changed bound policy blocks its source-document requests pending review.
The policy endpoints themselves remain explicitly included checks in the plan.

`robots_text_v1` standardizes line endings only. `html_policy_v1` binds visible text and all link
targets. `html_policy_v2` additionally canonicalizes the rotating XOR key used in the exact public
contact-link encoding observed on NIST's policy page. The decoded destination remains hash-bound,
so a changed destination, ordinary link, or visible policy text still requires review. The
[normalization review](../review_plans/2026-09-07-nist-policy-normalization.json) records the retained
byte comparison; v1 plans and their blocked packets remain unchanged.

Raw HTTP bodies stay local. Neither a plan nor a successful run grants redistribution, training,
unrestricted crawling, or access to subscriber/restricted material. Review metadata and derivative
facts may be published only within the owning source's documented rights boundary.

## Results and limitations

Transport success, content eligibility, and review acceptance are separate. Empty, wrong-media,
invalid-UTF-8, redirected, or identity-marker-missing responses require attention and do not replace
the last eligible document. Identity markers are a bounded check, not a complete challenge-page
detector or proof of a document's claims.

Eligible documents receive one of four comparison statuses:

- `unchanged`: exact response bytes match the prior eligible observation;
- `raw_bytes_only`: response bytes differ but normalized visible text matches;
- `first_observation_requires_review`: no prior eligible observation exists; or
- `visible_text_changed_requires_review`: visible text differs from the prior observation.

The latter two are review candidates, not accepted updates. An unchanged subsequent check does
not resolve an earlier candidate. `attention_required=false`
describes only this run and must not be interpreted as an empty reviewer backlog.
Document comparison uses visible text;
linked attachments, hyperlink-only changes, images, script-only data, and structured metadata need
separate adapters or review. Even unchanged text can contain stale claims. No status supports
facility closure, cancellation, capacity loss, or any other inference from absence.

The packet contains the plan, bound review bytes, run clocks and code hashes, per-attempt receipts,
response hashes, prior observations, deterministic results, and a complete managed-file manifest.
The manifest detects inconsistency; it is not an external authenticity signature. Run v1 remains
replayable; run v2 additionally verifies the derived last-success history. Retained code hashes
identify acquisition versions; verification uses the installed compatible rules, not execution of
archived code.

## September 7, 2026 live pilot

The initial v1 run completed at 02:47:01.432469 UTC. It checked four policy URLs and both Amkor
documents, then blocked the NIST document because the public contact-link encoding changed.
Review established that visible policy text, decoded contact destination, and every other link
were identical. Its retained manifest SHA-256 is
`9f0cfd287496db5186294831a8624d8b967d0358ce206fb7901343434b5da2aa`.

The reviewed v2 run at `source_snapshots/2026-09-07-amkor-repeatable-capture-02` started at
02:52:28.359394 UTC and finished at 02:52:36.229614 UTC. All four policy checks passed. Both Amkor
documents were byte-identical to their previous captures; the NIST page changed bytes but not
visible text. Seven requests produced zero content-review candidates and no attention condition.
Its manifest SHA-256 is
`70f8626fc9985291e877aeaa3395eee07e73066698f7d8ba6e3c21daa1c1e7f7`.
Both packets replay offline from their retained bytes.

This proves the bounded acquisition/comparison loop on three documents, not publisher-wide update
coverage, detection precision, or continuous operation. The other six cohort companies were not
refreshed. Scheduling, new-document discovery, a durable reviewer work queue, broader coverage-age
accounting, and blind historical evaluation remain open roadmap work.
