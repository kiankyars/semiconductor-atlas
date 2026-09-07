# Accepted-project population: retrospective source-support review

Reviewed at: `2026-09-07T10:31:13Z`.
Reviewer: Codex root agent, previously exposed to the case and implementation.

This is an agent-authored, non-blind source-support diagnostic. The reviewer already saw the
Fab 2 comparison, queue admission and expected classification before the freeze. A collaborating
agent who previously implemented the importer/producer also reviewed the same source evidence.
Neither review establishes independent adjudication, reviewer identity authentication or a
realized physical outcome. The review clock records this post-freeze reinspection, not first
knowledge of the case.

## Frozen scope

- Study: `2026-09-07-accepted-project-targets-retrospective-v1`.
- Window: `[2026-09-07T09:00:00Z, 2026-09-07T10:00:00Z)`; chosen retrospectively after admission.
- Frozen at: `2026-09-07T10:30:51.104446Z`.
- Frozen artifact: `artifacts/2026-09-07-project-target-population-v1-frozen.json`.
- Frozen SHA-256: `f8856e3ccfc1ff783d7d258fee230d339838a732d6cad0cd426a33ec00a64ff1`.
- Comparison: `574fa308-9352-5cf1-939e-9f7a48de055a`.
- Subject: NIST source-native TSMC Arizona Fab 2 PROJECT,
  `8f33e76f-ac2c-5962-a9a2-2cd1bcb55d86`; no canonical facility assignment.

The coherent core snapshot contains nine ingestion runs and exactly one accepted run on the
supported source-native project-target review route before the cutoff. This review labels that
one comparison, which is also the sole in-window proposal. It does not inventory all publisher
changes, other importer routes, unseen projects or independently sampled no-change cases.
The Amkor baseline-facility alert belongs to a different population and is excluded.

## Source-support finding

Verdict: `supported_revision`.

Both retained versions of the [NIST TSMC Arizona page](https://www.nist.gov/chips/tsmc-arizona-phoenix)
identify the second fab in the narrative and Fab 2 in the facility timeline. The earlier version
states a 2028 production target. The later version states the second half of 2027. Its narrative
uses the awkward wording "second half in 2027"; its Fab 2 timeline explicitly uses "second half
of 2027". The two sections agree on the bounded project and target period.

The typed calendar intervals, `2028-01-01` through `2028-12-31` and `2027-07-01` through
`2027-12-31`, are disjoint and ordered earlier in the second version. The proposal classification
`disjoint_earlier` is supported as a source-statement revision. These intervals encode source
precision, not probability or exact project acceleration. No midpoint delay or acceleration is
calculated. First-fab and third-fab text in the surrounding excerpts is not reassigned to Fab 2.

## Retained evidence and locators

Before body:
`source_snapshots/2026-07-17-open-seed/nist-chips-detail-tsmc-arizona-phoenix.html`,
SHA-256 `0475c14cd01810d9e8424c9fa1525ecb4eb938b48f2f549a987883cb51c5a71c`.

- Narrative: zero-based UTF-8 bytes `[81566, 81768)`, SHA-256
  `02b640979ae2d3b64a79115ad922287d005f6f35fb0ba33b9683fbb451ced2a4`.
- Timeline: bytes `[85816, 86098)`, SHA-256
  `72dc793b0a3981e5d21b095d833d66510918056e8735fe99a38e8fcbeae92787`.

After body:
`source_snapshots/curated-poll-v1/98974b8c0258460e91ae9c58a1d899e7-0/responses/nist-tsmc-arizona.body`,
SHA-256 `6e62a364921f03f376cf352147e7495d59a4c50dc41bf473a37b7f4a2c6d2598`.

- Narrative: bytes `[83588, 83809)`, SHA-256
  `b4f0af8dfe34c331cd34fa32bd3e1a8886f195f75152c4b37564720d7adf6b61`.
- Timeline: bytes `[87858, 88149)`, SHA-256
  `f7b1b27e15612e23397ffd72af3eecfc767049b55256ac0b6eaf7fc0f532569d`.

Human locators: Economic and National Security Impact, paragraph beginning "TSMC Arizona is on
track"; Facility Summary, Project Timeline, Fab 2. End offsets are exclusive. The root reviewer
read all four retained excerpts after the freeze. The verification script separately rehashes
the bodies and spans against the accepted claim review.

Accepted claim review:
`review_plans/2026-09-07-tsmc-fab2-project-target-claims.json`, SHA-256
`3e71100098d347ba673ef015a27268774522d62043a143acf20dd9cae7fc4e86`.

## Interpretation limits

The selected after document was retrieved at `2026-09-07T04:48:35.222019Z`; the earlier same-text
September observation was at `2026-09-07T04:41:53.837790Z`. They are repeated observations of one
publisher, not independent corroboration. The older retrieval clock is a conservative batch
completion correction, not a claim-specific publication date. Actual core admission was
`2026-09-07T09:28:12.852741Z`; actual alert admission was `2026-09-07T09:58:41.341424Z`.
Retrieval-to-claim and claim-to-alert durations describe this retrospective workflow only.

Publication time, first publisher availability, claim-effective time, realized production,
installed or qualified capacity, yield, utilization, calibrated confidence and exact physical
acceleration remain unknown. Acknowledgement is a review action, not outcome confirmation.
There are no reviewed no-change comparisons in this actual population, so no-change false-positive
burden is undefined. One source-supported proposal cannot establish general precision, detection
recall, forecast calibration or the blind historical evaluation gate. No delivery, public release
or raw-source redistribution is authorized by this review.
