# Reviewed publisher grouping without rewriting importer history

Schema 12 adds an immutable, per-source publisher-family assignment ledger. It
corrects attribution at an explicit knowledge cutoff while preserving the source's
original importer family, all document identities and all manufacturing claims.
It does not merge entire families, deduplicate documents or establish independent
corroboration. A later reviewed assignment can change or undo the grouping without
deleting either historical view.

## Contract

A review binds exact source and target-family rows, document identities and hashes,
the current assignment predecessor, reviewer, reason, prior exposure and explicit
provenance-only boundaries. Exact review JSON bytes and their SHA-256 are stored.
Endpoint and evidence clocks must precede review; review must precede acceptance.
The database guard and Python validator enforce source-specific linear chains,
strictly increasing acceptance times, genuine changes and immutable assignments.
Migration 12 adds one table, one index and three triggers without rebuilding or
changing existing schema objects. Failed validation rolls back the migration.

Claims, dossiers, searches, coverage and release exports resolve publisher grouping
at the selected knowledge cutoff. An optional `source_family_resolution` object
retains the original family, assignment ID, acceptance clock and review hash.
Release CSV adds `source_family_resolution_json` only when needed. The conditional
`source_family_assignments.jsonl` export includes the exact stored `review_json`
string, so the raw-byte review hash remains verifiable after export.

Original-family search matches identify that field explicitly in `matched_fields`.
Importer population tests, source-specific rights notices and attribution continue
to use the unchanged original family. Counts are publisher-grouping metadata, not
calibrated confidence or evidence of independent physical verification.

The claim changefeed does not invent a manufacturing or claim-admission event for
a provenance correction. Earlier claim events queried at a later cutoff carry the
later attribution, retaining their original event identities. Assignment
notifications, if needed, require a distinct event contract.

## Actual NIST correction

The [review](../review_plans/2026-09-09-nist-source-family-assignment-v1.json), SHA-256
`fc9ed86a8152c238aa34ab9132478197670c88e1b194a33f3a02ed5f580a395a`, assigns only
source `a04f43a6-8a47-51de-922f-92b122c9101e` from importer label
`reviewed-source-project-targets` to the existing
`us-official-semiconductor-awards` publisher group. The original family remains
available for other sources; no family-wide equivalence is inferred.

Both retained NIST TSMC Arizona HTML vintages identify NIST in their title,
canonical URL and site-name metadata. Their retained bodies are 98,048 and
99,980 bytes, with SHA-256 respectively:

- `0475c14cd01810d9e8424c9fa1525ecb4eb938b48f2f549a987883cb51c5a71c`
- `6e62a364921f03f376cf352147e7495d59a4c50dc41bf473a37b7f4a2c6d2598`

The older document is byte-identical to an existing document in the target family,
including its URL and retrieval clock. The older conservative batch-completion
retrieval qualification and later response-finished microsecond clock are preserved.
The bodies are read from the protected parent's existing `source_snapshots/`; no
new acquisition, rights clearance, redistribution or physical finding is implied.

Selected parent: `artifacts/2026-09-09-bosch-sample-production-schema11-v2.sqlite`,
SHA-256 `2b453d9fda486d5ec367966a617575d830a83049c263113468fe611dc5ec09b1`.
Selected successor: `artifacts/2026-09-09-nist-family-correction-schema12-v1.sqlite`,
310,259,712 bytes, SHA-256
`0f59f6bc9595effbc08c4eae54bd34606ae0ef6088205a0c3fe71d90aad1d599`.
Assignment `0bcb09a9-b549-5eaa-b628-10084c1950dd` was accepted at
`2026-09-09T10:09:23.429052Z`, also the new migration-ledger timestamp.

An independent checker loaded 38 modules exclusively from the installed wheel,
without importing the admission producer. It verified all 543,262 inherited rows
including SQLite value types, every inherited schema SQL definition, 104,476 claims,
all value hashes and original importer-family membership. Exactly two rows were
added: one migration and one assignment. All 56 documents and 16 raw family rows
remain; document-bearing publisher groups change from 15 to 14. Pre-correction
source claims, dossier and coverage are unchanged at `10:09:23.429051Z`, apart from
the dossier's explicit database schema-version metadata. At the next microsecond,
the reviewed group and its provenance appear. No manufacturing event is emitted.

The legacy project population inventory still rejects both parent and successor
under the current importer with `project route markers or accepted run status
disagree`: its importer-code marker compares against the current code, while the
old accepted runs pin an older producer. This inherited limitation was not silently
relaxed. Raw population membership and every ingestion record remain identical;
historical evaluations retain their separate frozen producer and population.

## Validation and replay

`scripts/admit_source_family.py admit` accepts a literal parent hash and review
hash, verifies retained evidence, captures actual execution time and writes a
new-only private successor. Subject documents must each verify their own retained
path; an unlocated context duplicate must match verified subject bytes. Existing
outputs, invalid or backward clocks, stale predecessors and changed inputs/code
are rejected. `verify` reconstructs the complete parent-plus-migration-plus-review
image using the accepted clock and compares every successor byte. The
`source-family-assignment` frozen-runtime operation permits verification only.

Validation on this implementation: 2,190 core tests executed in 278.755 seconds,
2,189 passed and one expected optional-data skip; 48 new focused regressions and
12 web-generation tests passed. All 110 package files matched source, wheel and
installed bytes. This was backend/local artifact verification, not browser-rendering QA.

Retained receipts and exact hashes:

- Admission `artifacts/2026-09-09-nist-family-correction-admission-v1.json`:
  `2c38c6d18e5d833224a8758c26eff891d66724809fbce68308769cce46b11a61`.
- Independent checker `artifacts/2026-09-09-check-source-family-independent-v1.py`:
  `0071983f0f86af27c6ec8b523c84c0afa0cbbb22702a7ee10d9c226a6bcb2929`.
- Independent receipt `artifacts/2026-09-09-source-family-independent-check-v1.json`:
  `3bc1617bcc054123eff5d705211eb38d9bc9ef946d8fcd46a2175e462647e5c3`.
- Wheel `artifacts/2026-09-09-source-family-dist-v1/semiconductor_atlas-0.1.0-py3-none-any.whl`:
  `351853af437ee7cd893c12602324a6badef3091188c0f3f69ba222a8c8e8ea5f`.
- Installed dossier `artifacts/2026-09-09-nist-family-correction-dossier-v1.md`:
  `ff5eb5df4b5d3f5cadcc1fa4d37a8bb21bef4823c67cbf8cf40950e070603a78`.
- Previous Bosch producer independently replayed its exact schema-11 successor
  after this change; receipt `artifacts/2026-09-09-post-family-bosch-frozen-replay-v1.json`:
  `820bf2ab20ec2acce189087f182691f7b09d45c6d4a413887cc2857b7e1b56ec`.

The protected checkout, original checksum ledger, baseline, r3 release and
scheduled runtime remain unchanged. No push, publication, deployment or scheduler
change is part of this correction. Useful measured alerts, broader outcome coverage,
calibrated forecasts and supply-demand scenarios remain open north-star gates.

## Frozen implementation receipt

Implementation commit: `3b9adf7ce2b3ca98ad52433c06f6ae2176ccb4cb`.
The retained runtime `artifacts/2026-09-09-3b9adf7-frozen-runtime-v1.json` contains
163 committed source files and 3,447,099 decoded payload bytes; bundle size is
4,643,928 bytes, SHA-256
`4bda7fb689152ad0beb55c3d5d25121946854ad5d30d1949479df47ec97a9380`.

The isolated frozen producer loaded 19 project modules and reconstructed all
310,259,712 successor bytes exactly. Receipt:
`artifacts/2026-09-09-source-family-frozen-replay-v1.json`, 17,518 bytes, SHA-256
`69591c619448a44812b47b55c9e83e98d8c2bb1f1f319196a0e8a7f2fdc1a5ff`.
The separate current-producer replay receipt
`artifacts/2026-09-09-source-family-direct-replay-v1.json` has SHA-256
`9635ba4a093a736f14ba84a47b5587598ef5308c4407c68508415e9ad2b2c41b`.
Both require literal parent, review and successor hashes; the input source bodies
remain at their original protected-parent paths. Frozen source isolation does not
claim a hermetic operating system or independent semantic adjudication.
