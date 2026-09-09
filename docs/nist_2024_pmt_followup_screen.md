# Later evidence for the frozen 2024 PMT population

The September 9 review keeps all **45 source-native manufacturing groups** from
the [complete original-article review](nist_2024_pmt_body_review.md). It does not
select projects on the basis of later success, a convenient target date or a
final award. Selection and review are nevertheless outcome-exposed, US-focused
and not a historically complete project census.

Two different evidence products are retained. They must not be conflated.

## Complete retained 2025 index subset

All eleven calendar-2025 articles in the frozen current NIST CHIPS-filtered
archive were captured and read in full, including governance, research and new
PMTs. The packet contains **993,532 article bytes** and 125 narrative blocks.
Article requests ran from `2026-09-09T03:45:36.579903Z` through
`2026-09-09T03:46:05.330015Z`. Both NIST policy checks passed under the exact
source review; the local capture grants neither core admission nor raw publication.

| Article classification | Articles |
| --- | ---: |
| Final awards, including a separately identified scope amendment | 3 |
| New 2025 PMTs, outside the original population | 2 |
| Research and piloting | 4 |
| Governance | 2 |

The manual correspondence decisions produce:

- **Six finalized-funding links:** Corning Canton, Edwards Genesee County,
  Infinera San Jose, Infinera Bethlehem, HP Corvallis and Hemlock. These are six
  project associations, not six independent awards; Infinera shares its award.
- **One additional-scope link:** GlobalFoundries existing Malta packaging and
  photonics. It is not attainment of the frozen automotive expansion and does
  not silently enlarge that project or the new Malta fab.
- **Two explicit nonmatches:** Coherent Easton SiC is not Sherman InP; Absolics
  SMART research funding is not the commercial-facility manufacturing award,
  despite sharing a company and city.
- **Zero physical outcomes and zero forecast scores.** The other 39 groups have
  no finalized-funding link in this eleven-article subset. This says nothing
  about their funding or physical status outside that source set.

HP's final award is up to $53 million, compared with the original up-to-$50
million PMT. The original statement is preserved, not overwritten. Final awards
remain subject to milestone-based disbursements; expected manufacturing and
capacity growth are not achieved output.

This subset is **not all later NIST news**. The broader search discovered a
June 2025 Micron NIST article absent from the filtered archive. The captured
pages also carry October/November 2025 modification metadata. Publication dates
therefore neither prove identical historical bytes nor reconstruct Atlas
knowledge in 2024 or 2025.

### Retained inputs and replay

The new review is
[`2026-09-09-nist-2025-followup-review-v1.json`](../review_plans/2026-09-09-nist-2025-followup-review-v1.json),
SHA-256 `67829c9b075b0cfec4a17d66971ce525908320ae3183fbce38aeb30101f006d2`.
It pins the original population review and the complete new capture manifest,
SHA-256 `9afeabecf3d7b90c4dd60785058b9809c44ef5ee6eec29aa3c536143d056b737`.
Raw bodies remain local under
`artifacts/2026-09-09-nist-2025-followup-capture-v1`; tracked plans do not imply
those raw artifacts are distributed with the repository.

Run from the isolated checkout with the retained inputs available:

```sh
python3 scripts/verify_nist_followup_review.py \
  --review review_plans/2026-09-09-nist-2025-followup-review-v1.json \
  --review-sha256 67829c9b075b0cfec4a17d66971ce525908320ae3183fbce38aeb30101f006d2 \
  --reference-root .
```

The read-only verifier first replays the original 65-article population review
and the new acquisition graph. It requires exact membership of the complete
2025 subset, retained source hashes, complete narrative spans, actual capture
clocks and publication/index agreement. Each correspondence references both
the later article and the original project's article. Unknown, repeated or
research-excluded project IDs and physical outcome fields fail validation.
All 45 groups appear in the resulting screen, including empty link arrays.

Passing verification establishes byte/membership integrity, not semantic truth,
correct manual identity correspondence, independent corroboration or search
recall. It does not modify the database, scheduler or old forecast vintages.

The local replay receipt is
`artifacts/2026-09-09-nist-2025-followup-verification-v1.json`, SHA-256
`a67528bf9ec3c5590ffcafc0d6edf01ccd36f31f7ec75d6f55a02babe55048b8`.
It records the verifier's hash and complete result; exact result replay passed.
This receipt is not a full frozen runtime bundle or a forecast evaluation.

## Broader 45-group research screen

[`2026-09-09-nist-pmt-later-evidence-leads-v1.json`](../review_plans/2026-09-09-nist-pmt-later-evidence-leads-v1.json)
preserves a bounded primary-source search for **every one of the 45 groups**,
including unknown and paused cases. It contains 38 unretained source leads,
explicit source-date labels, access/rights status, retained NIST references,
scope limitations and the next action for each group. This is a research work
queue, **not admissible outcome evidence**. A regression check requires exact
membership of the frozen manufacturing population and keeps every referenced
lead visible.

High-value distinctions found by the screen include:

| Candidate | What must remain distinct |
| --- | --- |
| TSMC first Phoenix fab | Reported Q4 2024 N4 HVM is a strong attainment lead; quarter precision is not an exact start day. |
| TI Sherman | SM1 production does not complete the frozen two-fab group; the award bridge distinguishes SM1 tools from SM2 shell construction. |
| Intel Rio Rancho | Government-reported operational Fab 9/Fab 11x packaging by publication is not an exact start date or completion of every funded component. |
| Microchip Gresham and Colorado Springs | Expansion pauses through fiscal 2027 are not physical closure or cancellation; existing fab production continues. |
| Edwards Genesee County | Equipment installation/commissioning is not demonstrated commercial production; current opening guidance conflicts internally. |
| Infinera Bethlehem | Nokia's existing Allentown operation is not automatically the frozen new Bethlehem facility. |
| Samsung Taylor packaging | Omission from the final-award description is not cancellation. |
| GlobalWafers St Peters | A new SOI line entering production requires a reviewed line-to-facility correspondence, not a whole-building completion label. |

The SK hynix IEDC lead reports a later H2 2029 mass-production target instead of
the original H2 2028 target. Its August 2026 publication predates the September
prospective freeze and exposes a source-coverage gap. It is not measured future
delay. IEDC's personal-download allowance does not approve Atlas derivative
use: no raw capture or admission was performed. Preserve the old forecast
vintage; seek independently permissible primary evidence before any admission.

Other restrictions and failed routes remain explicit. Intel/GF and X-Fab
issuer routes were excluded; the Commerce Micron route returned 403 and was
not retried, and an Ohio House lead remained unread after a 502. PDF text leads
still need applicable rights review and visual verification. A missed search
result, mutable directory or failed request is not nonoccurrence evidence.

## Next outcome gate

The existing fiscal-2025 TSMC 20-F Arizona narrative was inspected and its exact
body and previously reviewed span hashes verified. It identifies the first
Phoenix facility and reports HVM at the end of 2024, **not explicitly Q4**.
Do not manufacture quarter bounds from that wording or alias the aggregate
Fab 21 commercial-commencement table row to this distinct event. The locator
remains in the [earlier correspondence review](../review_plans/2026-09-08-tsmc-operating-fab-correspondence.json).

The next acquisition/adjudication batch should retain successful, partial and
non-attained projects together: resolve the TSMC narrative's supported date
precision, then review exact source rights for the TI identity/production pair, New Mexico
Rio Rancho statement and Microchip pause disclosure. Bosch's new NIST statement
also warrants separate sample-production versus 200mm-first-chip review.

After capture, preserve source-native components and date bounds, admit only
supported event types, and pair later evidence to the unchanged original
targets. Descriptive retrospective pairing remains separate from pre-outcome
system forecasting and calibration. Complete global coverage, independently
evaluated change detection and twenty-quarter forecasting remain open.
