# Discovery to reviewed source evidence

The offline handoff verifier connects one observed URL to a separately approved exact-document
capture and an explicit source-text disposition. It proves the retained workflow links; it does
not acquire a URL, change either queue, accept a manufacturing claim, or make an alert deliverable.

## Evidence and time contract

A handoff review binds both event exports, the selected discovery and source candidates, their
current decision tokens, the retained capture identities, raw and normalized source hashes, the
access review, and the source-text review. Verification replays every referenced capture and queue
chain. It checks the acquisition plan's exact URL, company, country, facility key and document
scope against the selected baseline facility and requires a successful eligible source observation.
Changed or failed latest source checks cannot be hidden by selecting an older successful capture.

The required order is discovery admission, independent access review, discovery handoff decision,
capture, source admission, source-text review, source decision, and final link review. The selected
discovery packet's clocks are not necessarily the candidate's first-ever observation or admission.
Reviewer identifiers and local timestamps are retained assertions, not authenticated identities or
externally attested clocks. Access approval is a recorded source-specific review, not a legal grant
created by the verifier.

Two outcomes are supported:

- `no_baseline_revision` requires an explicitly dismissed source-text version.
- `requires_claim_review` requires a source-queue handoff for a separate claim-review process.

Both preserve false claim-acceptance, baseline-modification, delivery, redistribution and acquisition
permission flags. Finding a company name in an index never verifies facility scope. Review still has
to distinguish a particular fab from a project aggregate, R&D facility, company portfolio or shared
award. A hash verifies bytes, not the truth of the assessment written in them.

## Build and replay

```sh
python3 scripts/verify_discovery_handoff.py build \
  --review review_plans/2026-09-07-samsung-discovery-source-handoff.json \
  --reference-root . \
  --output artifacts/2026-09-07-samsung-discovery-source-handoff-v1.json
python3 scripts/verify_discovery_handoff.py verify \
  --artifact artifacts/2026-09-07-samsung-discovery-source-handoff-v1.json
python3 scripts/verify_discovery_handoff.py history \
  --artifact artifacts/2026-09-07-samsung-discovery-source-handoff-v1.json \
  --as-of 2026-09-07T08:02:00Z
```

Build requires a new output path and cannot write inside retained capture packets. Verification
requires exact deterministic replay. The artifact is **not self-contained**: it requires its review,
hash-bound exports and supporting reviews under the recorded repository root, all original capture
packets at their admitted paths, and the pinned verifier code. It contains no raw source archive.
Moving only the artifact or changing a dependency does not constitute a successful restore.
Dependency bytes, file identities and capture inventories are checked around derivation and output
installation. This assumes a quiescent local evidence corpus; it does not atomically lock all files
against arbitrary concurrent writers. A successful replay is not a tamper-proof archival service.

Historical projection uses admission and decision clocks and ends at the final link-review cutoff.
Before that review, it exposes only then-admitted candidate state, with no final linked facts.
Target selection is retrospective, not a blind discovery test or evidence of early detection. The
complete retained dependencies must remain valid even when projecting an earlier cutoff.

## September 7 Samsung pilot

The separately approved Taylor landing page was captured once with three successful requests
(robots, terms and the exact document). Its substantive project narrative is identical to the
already monitored Austin landing page. The two pages are not independent corroboration: both
describe broader Texas award context, including two Taylor logic fabs, separate Taylor R&D and
Austin expansion. The duplicate Taylor page was not added to the recurring catalog.

Both observed text versions were explicitly reviewed without a baseline revision. Award status,
funding, jobs and a future all-facilities timeline do not establish attained construction, installed
tools, qualification, production or numeric manufacturing capacity. The baseline's Taylor scope
remains the two-logic-fab project aggregate; this review does not reaffirm its current stage.

Retained CMS metadata reports July 24, 2024 page publication and June 1, 2026 modification, with
different minute values on the two pages. These page-level clocks are preserved separately from
claim-effective dates. They neither prove every current assertion existed at initial publication
nor establish a manufacturing milestone on the modification date. The later text review qualifies
the earlier access review's unknown publication assessment without rewriting that frozen record.

After the decisions, the source queue contains 19 events, 12 packets, eight source candidates and
one pending TSMC text review. The discovery queue contains five events, two packets and 64 URLs:
13 routed open leads, 50 unrouted open leads, and the Samsung handoff. The TSMC broader-expansion URL
remains deferred. Scheduled coverage is unchanged at seven documents across five of seven scopes;
SK hynix M15X and ASE Kaohsiung remain unmonitored. This one-off capture is not an eighth scheduled
document or new accepted manufacturing evidence.

The [pilot record](../review_plans/2026-09-07-samsung-discovery-handoff-pilot.json) binds the exact
inputs and local verification results. Raw pages, images and linked resources are not published.
Independent source-to-claim acceptance, incremental discovery, broader permitted coverage, blind
alert evaluation and calibrated forecasts remain separate gates.
