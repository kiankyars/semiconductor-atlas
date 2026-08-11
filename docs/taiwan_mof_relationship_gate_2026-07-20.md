# Taiwan facility-to-MOF tax-unit relationship gate — 2026-07-20

## Decision

Accept exact same-time UBN agreement between selected Taiwan facility records and selected MOF tax
units only as a reviewed `registered_tax_unit_reference`. This is a contextual registration
reference, not facility identity, legal-person identity, ownership, parentage, operator, activity,
lifecycle, production, output, or capacity evidence.

## Frozen inputs and candidates

Candidate generation froze the database at knowledge cutoff `2026-07-20T09:44:33Z` and bound these
exact accepted source runs:

- MOENV EMS_S_01: run `9be1e187-f5f7-5dbf-b8cd-3ed839f9182f`, snapshot manifest
  `cae53460ba57be96516757a5c15126aaf91ad224dc64fbb27b8d0f77780304e1`;
- registered factories: run `ceab537a-1ac8-5a38-82e9-6ac6b50b6e29`, snapshot manifest
  `747f3562c0eadacdf97a8b82b338f5789a92b22c2c2232ba04481ebf343eaac7`; and
- MOF BGMOPEN1: run `9171c9df-7772-5d25-a9c7-bb869570dad6`, snapshot manifest
  `c14892bffca9deccf5a5720aab19b03daa7b8aa14fc49d77f9d6cc1dac1541ce`.

The canonical candidate artifact is
`review_plans/2026-07-20-taiwan-facility-tax-unit-candidates.json`, SHA-256
`c08294f6bbca07c3d52d61079a27774181e59f14c9bc9759b61d7df5aa8f4c89`. It contains 944 candidates,
935 distinct facility subjects, all 390 imported tax units, and 1,463 distinct source-claim IDs
across 2,017 candidate-level references. Canonical-subject selection used 807 source-native cases,
128 factory-native cases carrying reviewed MOENV assignment provenance, and nine accepted
factory-native assignment cases.

Nine facility subjects each retain two different current UBN references, producing 18 candidates.
The candidate layer does not collapse those conflicts or infer which tax unit owns or operates the
facility.

## Complete review

The review artifact is `review_plans/2026-07-20-taiwan-facility-tax-unit-review.json`, SHA-256
`b3e2de1f9fd44f7a93dcb2ac3b5d2a2e6d6bcb03d5b16727b1fb69838d134865`. Reviewer
`codex-agent:019f72de-95de-7b71-adc8-916cbca38f0b` completed it at
`2026-07-20T09:50:01Z`. All 944 candidates were marked `match`; none was rejected or deferred.

The recorded reasons distinguish 798 exact single-facility-source agreements, 128 concordant
two-source agreements, and 18 conflicting-reference cases explicitly preserved without resolution.
Every match records its relationship interval and binds the exact facility claim, MOF identifier
claim, source documents, source records, and any canonical facility assignment used.

## Atomic acceptance and semantics

Review run `a694602c-c53a-510e-9c31-73454459563e` started at
`2026-07-20T09:54:43Z`, completed at `2026-07-20T09:54:44Z`, and recorded all claim effects at
`2026-07-20T09:54:46Z`. It created 944 `reconciled_fact` relationship claims, reaffirmed none, and
superseded none. Exact replay wrote zero rows and preserved database SHA-256
`c6083299cae65a96890dff831ca201566de82dae7532ca555c65391f7ee21ca9`.

The first acceptance attempt installed nothing: 33 valid MOENV UBN claims each had two agreeing
support fragments, exposing an overly strict one-fragment check. Acceptance was corrected to require
at least one fragment and verify that every fragment has identical bound semantics before the
successful run.

Every accepted claim uses predicate and relationship type `registered_tax_unit_reference` and method
`taiwan_registered_tax_unit_reference_review_v1`. Its attributes explicitly set identity, ownership,
parentage, and operator-or-operation assertions to false. Evidence and dependency closure is
complete: 816 claims each carry two evidence links and two dependencies; 127 carry three of each;
one carries four of each.

The accepted database is
`artifacts/2026-07-20-open-seed-taiwan-tax-relationships.sqlite`. It contains 4,865 entities, 104,286
current claims, 4,824 source records, 43 source documents, eight ingestion runs, 1,139 total
relationship values, 100,758 evidence links, 13,424 claim dependencies, and 143 source assignments.

## Release lineage and reproducibility

The release is `releases/2026-07-20-open-seed-taiwan-tax-relationships`. Its 24 managed files include
`reviewed_relationship_runs.jsonl`, a 2,085,850-byte single-row ledger containing the exact candidate
and review hashes and sizes, reviewer and clocks, selected source bindings, all 944 decisions, and
every created, reaffirmed, or superseded claim descriptor. Downstream JSONL readers must not assume a
small maximum line length.

Coverage reports 944 current reviewed references, 935 distinct facility subjects, 390 distinct tax
units, and nine subjects with multiple current references. It explicitly reports that the layer
asserts none of identity, legal-person identity, ownership, parentage, operator or operation,
activity, or lifecycle.

Release hashes:

- final manifest SHA-256: `6d7bca2cd392d1372774ae227ae9cd6e3a665454e2c25084b357c9b58691f4ce`;
- reviewed-run ledger SHA-256: `5902e68ae08b4a0bd54a7875764465fe8da2dbcdef47a2aab754d6717b1fc8c6`;
- coverage SHA-256: `86f7d3957e347e69424a62158b872543190eadff755b7014841bc1930e22ba32`; and
- standalone atlas SHA-256: `35b198e08be16d3ad6d861ffe18cbf3624e136211cc8be17c5d2d64ce29be9bd`.

An independent full release and atlas rebuild produced the same 24 filenames and zero byte
differences. The exported ledger parameters equal the stored ingestion-run parameters exactly, and
all 944 action claim IDs resolve in `claim_history.jsonl`. Releases at the review run's completion
time and decision time omit the run because its claim effects are not yet transaction-visible; the
ledger appears only at the claim-recording cutoff. The organization-only predecessor also rebuilt
byte-for-byte with its original 23-file manifest, proving the lineage surface is additive.

Validation passed 402 core tests, 11 web tests, and Ruff checks for the touched implementation and
tests.
