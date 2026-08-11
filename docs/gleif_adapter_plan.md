# GLEIF organization identity adapter plan

Status: the organization-resolution ledger, offline Level 1 parser, bounded acquisition utility,
offline snapshot verifier, reviewed importer, and schema-v3 release are implemented and tested. One
reviewed TSMC Arizona Level 1 record is accepted in a separate pilot database and release. This is
an activation proof for exact reviewed LEIs, not broad GLEIF or facility coverage.

The first GLEIF milestone supplies legal-entity identifiers and source-backed names for a reviewed,
bounded set of organizations. It does not merge organizations by name, infer facility ownership,
or treat an LEI registration status as evidence that a facility or company is operating.

## Accepted one-record pilot

The retained snapshot is `source_snapshots/2026-07-19-gleif-level-1-tsmc-arizona`. Its manifest
SHA-256 is `fe82dd00b8ad02006f923b259c2b9d9d3b955fd9b20e5bb01a8601c123ed76d8`, and it pins Golden Copy
`2026-07-19T16:00:00Z`. The allowlist contains only LEI `2549005GOBWLCSY63Q97` for TSMC Arizona
Corporation. The separate review plan is
`review_plans/2026-07-19-gleif-tsmc-arizona.json`; it binds the snapshot to the existing NIST
organization using the NIST recipient-name claim as target evidence.

The accepted database is `artifacts/2026-07-19-open-seed-gleif-pilot.sqlite`, with SHA-256
`a1a7f210fb46de2760e56914f4c69fbab5d8f69a5b66c15b510c33c2bed49e5a`. Acceptance at
`2026-07-20T06:38:00Z` produced a knowledge cutoff of `2026-07-20T06:38:06Z`, one source-native
organization, 21 claims, one reviewed candidate and decision, and one assignment. Exact replay made
no writes and left the database hash unchanged.

The schema-v3 release is `releases/2026-07-19-open-seed-gleif-pilot`, with manifest SHA-256
`77b52c9b6b79f8713bb07f5290c3022e1be9246441d68d4527bb598394fd500f`. A fresh database-derived
release plus standalone atlas reproduced all 24 files byte-for-byte. See
`docs/gleif_pilot_audit_2026-07-19.md` for the complete acceptance record and limitations.

## Official access contract

GLEIF provides LEI and reference data under CC0 1.0 through the
[LEI access service](https://www.gleif.org/en/meta/lei-data-terms-of-use). The
[GLEIF API](https://www.gleif.org/en/lei-data/gleif-api) is based on the current Golden Copy and
returns its publication time in `meta.goldenCopy.publishDate`. Current official API documentation
sets a limit of 60 requests per minute.

For the bounded first adapter, exact accepted LEIs are fetched from
`https://api.gleif.org/api/v1/lei-records/<LEI>`. Name and fuzzy searches may propose review
candidates, but they cannot create an assignment. Every request stays below the published rate
limit.

For later bulk coverage, use pinned
[Golden Copy and delta files](https://www.gleif.org/en/lei-data/gleif-golden-copy/download-the-golden-copy)
in JSON or XML. GLEIF publishes Golden Copies three times daily. The current formats are LEI-CDF
3.1, RR-CDF 2.1, and Reporting Exceptions 2.1; the legacy download keyword for Level 1 remains
`lei2`. CSV is not the canonical archive format because repeating names are bounded and extension
data is omitted. Format definitions and examples are in GLEIF's
[supporting documents](https://www.gleif.org/en/lei-data/access-and-use-lei-data/supporting-documents).

Public attribution:

> Source: Global Legal Entity Identifier Foundation (GLEIF), LEI data, CC0 1.0; no GLEIF
> endorsement implied.

## Resolution before enrichment

The atlas now has a versioned entity-resolution ledger that must be used before attaching an LEI
record to an existing organization. It records:

- the source record and candidate entity that were compared;
- normalized comparison features, score, resolver version, and run;
- a `match`, `reject`, or `defer` decision with actor, time, reason, and resolver version; and
- versioned source-record-to-entity assignments.

Merge, split, and redirect operations remain deferred until a reviewed workflow can apply and undo
them without hiding prior assignments. The migration must not advertise dormant decision types.
Source claims keep typed auxiliary metadata for organization name type, language, script, identifier
scheme, normalized identifier, and jurisdiction without changing scalar claim hashes. A generic
run-to-document association records every multi-document adapter input.

A resolution run begins in `running` state. Its succeeded ingestion inputs and cutoff-safe
candidates are appended before one terminal transition to `succeeded` or `failed`. Decisions are
allowed only after successful finalization, and only `match`, `reject`, and `defer` are active.

A name query creates candidates only. A reviewed allowlist identifies exact accepted LEIs and the
decision that justifies each assignment. TSMC Arizona and its parent, for example, remain distinct
legal entities; parentage does not make their names aliases.

## Snapshot contract

The bounded fetcher takes a sorted declared allowlist and archives exact API response bytes. The
snapshot does not assert that an LEI has been reviewed or assigned. A snapshot is accepted only
when:

1. every response reports the same `meta.goldenCopy.publishDate`;
2. the returned LEI set exactly matches the declared allowlist;
3. request URLs, retrieval time, byte counts, SHA-256 hashes, allowlist hash, and Golden Copy time
   are recorded; and
4. the bytes and manifest are rechecked at the database acceptance boundary.

This first snapshot has `level_1_only` coverage. It does not follow relationship links. Direct and
ultimate parent records and reporting exceptions require a later, separately versioned Level 2
snapshot that follows only links returned by GLEIF, stops after one hop, and pins every resource to
one Golden Copy publication.

If the Golden Copy changes during acquisition, the staged snapshot is discarded and retried. The
live API has no historical-as-of query, so retained response bytes are mandatory for replay.

The first-milestone allowlist format is canonical ASCII: one checksum-valid uppercase LEI per line,
sorted, unique, LF-terminated, and capped at 500 records. Acquisition spaces request starts by at
least 1.1 seconds across both normal requests and retries. Each successful snapshot retains the
allowlist, content-addressed raw responses, a deterministic link-free derivative, and a manifest in
a private staging directory. Inside the opened snapshot root, the verifier reopens every
snapshot-relative component without following symlinks, replays the derivative from the exact
hashed bytes, and the installer uses an atomic no-replace rename. The request policy caps each raw
response at 8 MiB and the snapshot total at 256 MiB. Its configured timeout is a socket inactivity
timeout, not a total acquisition deadline.

Review provenance belongs to the later database-acceptance decision. It must not be inferred from
the acquisition allowlist or written into the snapshot coverage label.

The review artifact is strict and independently hashed. It records the review actor and time,
snapshot manifest hash, Golden Copy publication, and exactly one disposition per declared LEI. Each
disposition records the target entity ID and stable key, sorted source-backed target claim IDs,
outcome, reason, score, candidate rank, and assignment start date. Import reopens and re-verifies
the snapshot and review file at entry and again at the commit boundary; a path swap, byte mutation,
semantic mismatch, early review, late target evidence, or failed producer run aborts the transaction.

## Implemented offline parser boundary

The Level 1 parser accepts a single full GLEIF JSON:API record, verifies that its resource ID and
`attributes.lei` agree, rejects duplicate JSON keys, invalid UTF-8, and non-finite numbers, and emits
deterministic link-free canonical JSON. It retains the bounded Level 1 legal names, addresses,
registration data, legal-entity events, expiration data, and successor data. Optional language
fields may be null. Relationship links, parent records, and reporting exceptions are deliberately
outside this parser boundary and are not inferred from Level 1 data.

## Field treatment

GLEIF records first create source organizations keyed `gleif:lei:<LEI>`. After a record is assigned,
publish source claims for:

- the 20-character LEI;
- legal name and language;
- alternative-language, prior legal, and trading or operating names;
- preferred and automatic transliterations;
- legal jurisdiction, legal form, category, and entity status;
- registration authority and registered entity ID;
- legal and headquarters addresses as organization addresses, never facility coordinates; and
- registration and validation status and dates.

Direct and ultimate parent records are accounting-consolidation relationships. They do not establish
facility ownership. Preserve reporting exceptions so missing parent data is not converted into a
negative relationship claim.

## Refresh semantics and gates

- Stable organization key for a GLEIF-native entity: `gleif:lei:<LEI>`.
- Level 1 records keep the LEI across corrections; semantic field changes create new claim versions.
- `LAPSED`, `RETIRED`, `DUPLICATE`, `ANNULLED`, successor, and legal-entity event fields are retained
  literally. None proves that a facility closed.
- Omission from a delta means unchanged, not deleted.
- The durable Level 2 slot is child LEI plus relationship type; the parent is its versioned value.
- Reporting-exception and relationship data are processed together for the same pinned publication.

The one-record pilot passed offline fixtures, exact replay, partial and complete refresh tests, a
mid-fetch Golden Copy rotation test, mutation-at-acceptance rollback, rate-limit handling,
assignment-history tests, cutoff-safe target-evidence lineage, release provenance, and a manual audit
of its only allowlist decision. Broader activation repeats the manual decision audit for every added
LEI and moves to pinned Golden Copy or delta files rather than scaling exact-record API calls.
Merge/split support later requires redirect-cycle rejection and a reversible resolution audit before
activation.
