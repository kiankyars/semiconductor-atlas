# Find a source-native entity before opening its dossier

`semiconductor-atlas evidence-search` adds read-only discovery to the installed
CLI. It searches retained registry labels and currently associated direct-source
metadata; it does not require an internal entity ID in advance. It returns JSON
on standard output and does not write files, migrate the database, acquire
sources, resolve identities or modify the geographic viewer.

```sh
semiconductor-atlas evidence-search \
  --database artifacts/2026-09-09-new-albany-project-statements-schema9.sqlite \
  --as-of 2026-09-09 \
  --recorded-at 2026-09-09T05:52:00.817388Z \
  --query Ohio \
  --kind project
```

The retained successor returns the City-reported Ohio One project and the NIST
Intel Ohio project separately. They have different exact IDs and source scope;
this command does not select a winner or make a canonical assignment. The City
entry has three known source statements, zero world-current claims, two direct
documents and one municipal source family. Its label retains the unresolved
canonical-identity qualification.

Copy the selected `dossier_selection.entity_id` into the
[evidence dossier](evidence_dossier.md) command, keeping its two cutoffs:

```sh
semiconductor-atlas evidence-dossier \
  --database artifacts/2026-09-09-new-albany-project-statements-schema9.sqlite \
  --entity-id acac346e-5928-5c14-b20e-ae2739dce92a \
  --as-of 2026-09-09 \
  --recorded-at 2026-09-09T05:52:00.817388Z \
  --output artifacts/selected-ohio-project.md
```

For a source checkout, replace `semiconductor-atlas` with
`python3 -m semiconductor_atlas.cli`. Other useful literal searches are `Fab 21`,
`Villach`, `Polar`, and the direct publisher `City of New Albany`. Polar returns
an organization, a NIST project and a separately admitted facility; three matches
do not mean three factories or an accepted identity between those entries.

## Matching, ordering and pagination

The optional query is one literal substring, normalized to Unicode NFC and
casefolded. Leading/trailing query whitespace is trimmed. `%`, `_`, brackets,
quotes and regex syntax have no wildcard meaning. Words are not silently split,
reordered, translated or expanded into aliases: `TSMC Fab 21` need not match a
record whose display name is `Fab 21` and whose separate stable key contains
`tsmc`. Search a shorter relevant label and inspect the resulting exact IDs.

Allowed fields are entity ID, immutable stable key and display name, plus direct
source key/name/publisher and family key/name. `matched_fields` identifies each
matching field. Claim values, source excerpts, document titles/bodies, claim
notes, identity assignments, aliases and transitive dependency sources are not
searched or returned as snippets.

Omit `--query` to browse retained registry entries. `--kind` is an optional exact
entity-kind filter, such as `project`, `facility`, `site` or `organization`.
Results sort by immutable stable key, then ID, not relevance or identity score.
`--limit` defaults to 50 and accepts 1–200; `--offset` is nonnegative.

The response reports the complete visible-registry denominator, complete matched
denominator after query/kind filtering, returned count and `next_offset`. Follow
that offset with the same query and cutoffs. An out-of-range page stays empty but
retains its complete denominator. A name match is a discovery lead; no registry
denominator is a count of confirmed operating semiconductor factories.

## Temporal and evidence semantics

Every query uses one coherent read savepoint. Entity creation and referenced
creation-run acceptance must be no later than the knowledge cutoff. Claim/series
clocks, run completion/explicit acceptance, run source/family creation and retained
run-input document availability are also filtered with microsecond precision.
Source metadata requires known document retrieval, publisher/family creation,
consistent publication-versus-retrieval chronology and, when present, a known
source-record observation and ingestion run. Future claim admissions do not
contribute selected-claim counts; future evidence does not contribute source
labels or direct-document counts.

There are two disjoint selected-claim counts per entity:

- `known_source_statement_count`: all known, unsuperseded source statements,
  independently of their world-validity dates. Multiple unsuperseded versions
  are not collapsed.
- `world_current_claim_count`: the existing world-current rank-one selection,
  followed by exclusion of source statements. Ranking all eligible kinds first
  prevents an older observation from being resurrected behind a newer source
  statement. Observations, reconciled facts and estimates are not all facts.

The counts cover the whole exact entity, not just records whose source matched
the query. They count versions before joining evidence; repeated links and
documents do not multiply claims. Known entries with no selected claims remain
discoverable by their own ID/name/key. Zero selected records is not zero capacity,
closure or inactivity.

`direct_sources` includes only directly linked evidence on those selected claims.
Its distinct document IDs, source/family labels and evidence roles remain explicit.
Support, context and refutation links all establish a recorded association, not
agreement. Superseded sources and dependency-only sources require the dossier's
history/lineage view. Multiple documents in one family are not independent
corroboration, and family counts are not confidence scores.

This is a metadata inventory, not complete transitive lineage validation, typed
value validation, source-byte reconstruction or substantive evidence review. A
dossier can still reject inconsistent lineage. A source statement recorded before
the cutoff can have no eligible direct-source labels if its evidence clocks are
inconsistent; that count does not certify its evidence. Unknowns remain unknown.

## Storage and remaining scope

The search and dossier commands share an existing-file `mode=ro`, query-only
connection helper. They honor committed WAL state rather than ignoring it with
`immutable=1`. The API preserves the caller's transaction. Discovery uses SQL
metadata and does not hydrate the full claim store or build thousands of dossiers.

No baseline, historical admission, producer pin, scheduled runtime or source
payload is changed. A query is not a fresh source check or proof of coverage age.
Map integration, semantic/alias discovery with reviewed identity, wider evidence
coverage, useful alerts and calibrated forecasts remain separate north-star work.

## Executed acceptance

The full installed-wheel check covered all 4,904 visible registry entries in 25
pages of at most 200 results. Independent SQL/Python selection matched every
entity ID and label, every source/world claim count, and every direct source's
document-ID and evidence-role set. Across this retained database, the selected
counts are 96,987 source-statement versions and 7,485 world-current claims; these
are not counts of physical observations or factories.

Five actual queries returned two Ohio entries, one Fab 21 entry, one Villach entry,
three Polar entries and one City-publisher entry. Five repeated searches matched
exactly. At one microsecond before the City admission, the City entry and its
source labels were absent while the older NIST Ohio project remained visible.
Every subprocess used the wheel's actual console-entry metadata under `-I -S -B`,
with package import origins checked against the temporary installed wheel.

The retained checker is `artifacts/2026-09-09-check-installed-evidence-search.py`,
SHA-256 `c25fa0bcfb8493685d34138de816d052a32ae01eca0166f6fc2565f9c6397366`.
Its receipt is `artifacts/2026-09-09-installed-evidence-search-check-v1.json`,
SHA-256 `8d7aa1b96f8ae5bdcb84bfc8bab7dccdf79f65a94f30e6ed84c9ce835943a8e5`.
The receipt pins wheel SHA-256
`60a49fa631b860a0360c07dc7f3e2bd360dbd088a7a08f74764ffd5aec76129d`.
The five local query outputs are in `artifacts/2026-09-09-installed-evidence-search-v1`.

All 29 search tests passed with the 40 dossier and two CLI tests. The complete
fixed-source core suite executed 1,938 tests in 267.254 seconds: 1,937 passed and
one expected optional-payload test was skipped. All 12 web tests passed. Syntax
checks and offline wheel/source-distribution builds passed.

All eight earlier real dossier outputs remained byte-identical through the new
installed package. Their verification receipt is
`artifacts/2026-09-09-post-search-installed-dossiers-check-v1.json`, SHA-256
`11a26499443890bd97cdb62a46069a1579a6103cd9d5c2634b8c59fe6def251d`.
The original Ohio admission also replayed in its frozen runtime to the entire
unchanged 242,892,800-byte database. Its receipt is
`artifacts/2026-09-09-post-search-city-frozen-replay-v1.json`, SHA-256
`617961a99f021256402f8159c0ae19d6a9d964c10def95094df702d1c71ca930`.
The scheduled database, baseline, ledger, parent analytics producer and prospective
guidance vintage retain their prior hashes. No push, publication or scheduler
change is part of this acceptance.
