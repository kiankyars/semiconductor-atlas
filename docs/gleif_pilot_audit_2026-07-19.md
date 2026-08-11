# GLEIF TSMC Arizona pilot audit, 2026-07-19

The pilot covers one reviewed Level 1 record: LEI `2549005GOBWLCSY63Q97`, TSMC Arizona
Corporation. The [snapshot manifest](../source_snapshots/2026-07-19-gleif-level-1-tsmc-arizona/manifest.json)
has SHA-256 `fe82dd00b8ad02006f923b259c2b9d9d3b955fd9b20e5bb01a8601c123ed76d8` and pins
Golden Copy `2026-07-19T16:00:00Z`. Its [raw API response](../source_snapshots/2026-07-19-gleif-level-1-tsmc-arizona/raw/sha256/3f93762b1af89081de14a499c1b34b689ec86f5ac2815e055296dbc30e18336d.json)
has SHA-256 `3f93762b1af89081de14a499c1b34b689ec86f5ac2815e055296dbc30e18336d`.

The [review plan](../review_plans/2026-07-19-gleif-tsmc-arizona.json) binds that snapshot and
Golden Copy to the existing NIST TSMC Arizona organization. The review file has raw SHA-256
`94128300cfa10cd7ae113b48da6c8c119782eed587a02e6b3f6c147659daaa0c` and canonical SHA-256
`0e481011fc1249bcbf394b73bc711f9b51e16ab924eb9fb60892688101fd8e32`.

The importer accepted the review at `2026-07-20T06:38:00Z` and set the knowledge cutoff to
`2026-07-20T06:38:06Z`, a six-second offset. It created one source-native organization, 21 claims,
and one reviewed assignment. The [pilot database](../artifacts/2026-07-19-open-seed-gleif-pilot.sqlite)
has SHA-256 `a1a7f210fb46de2760e56914f4c69fbab5d8f69a5b66c15b510c33c2bed49e5a`.
At the cutoff it contains 36 source documents, 3,157 source observations, 76,473 claims, and 3,198
entities.

An exact replay reused the accepted ingestion and resolution runs, made zero writes, and preserved
the database hash. A fresh core release plus standalone atlas reproduced all 24 release files
byte-for-byte. The [release manifest](../releases/2026-07-19-open-seed-gleif-pilot/manifest.json)
has SHA-256 `77b52c9b6b79f8713bb07f5290c3022e1be9246441d68d4527bb598394fd500f`.
The release exposes the decision in
[`entity_resolution_decisions.jsonl`](../releases/2026-07-19-open-seed-gleif-pilot/entity_resolution_decisions.jsonl)
and the bitemporal mapping in
[`source_entity_assignments.jsonl`](../releases/2026-07-19-open-seed-gleif-pilot/source_entity_assignments.jsonl).

## Evidence boundary

The pilot's scope consists of legal-entity identity fields. GLEIF claims remain on the source-native
`gleif:lei:2549005GOBWLCSY63Q97` organization. The reviewed assignment points to the existing NIST
organization. Legal and headquarters addresses remain organization attributes. The pilot
provides no facility geometry, parent relationship, facility ownership, operating-status, capacity,
or production evidence. LEI registration status cannot support any of those conclusions.
