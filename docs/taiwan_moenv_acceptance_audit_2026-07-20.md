# Taiwan MOENV EMS_S_01 acceptance audit: 2026-07-20

This audit covers the first accepted non-U.S. facility source in
`artifacts/2026-07-20-open-seed-taiwan-moenv.sqlite` and
`releases/2026-07-20-open-seed-taiwan-moenv`.

## Acquisition and immutable snapshot

The official full JSON package was retrieved by POST at `2026-07-20T06:57:50Z`. The sanitized
request selected package `816037bc-53f1-4951-b32d-8e607b948344`, resource
`56ea8602-c7d5-4c27-ac20-236e51e889c4`, and `download_type=json`. The official dataset page's
`2026-07-20 07:15:13` Asia/Taipei update time is recorded canonically as
`2026-07-19T23:15:13Z`; it is distinct from retrieval and database acceptance.

The accepted snapshot is
`source_snapshots/2026-07-20-taiwan-moenv-ems-s-01-semiconductor`:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Snapshot manifest | 7,220 | `cae53460ba57be96516757a5c15126aaf91ad224dc64fbb27b8d0f77780304e1` |
| Official ZIP | 36,386,242 | `531920946aed3560c7aca3c8fea629478dded24154894af33d8064f507b2adee` |
| Inner JSON | 409,441,780 | `e0c62aece37dd46bdfc3cf9598dbdb9b620f0e00db3703da4befe2e0a439ca33` |
| Exact-code JSONL derivative | 530,141 | `e4cb515898af1962d0c217611cb4edb9bfbcab0a11126b8433c3be17dc4d8eff` |

The publisher MD5 `a008f9bc87da8f907b2dd9de29d827c3` matches the complete inner JSON.
Offline verification binds the path to a private descriptor, validates the complete ZIP and
26-field JSON schema, rescans all source rows, and regenerates the derivative byte-for-byte. The
closed snapshot tree contains only its canonical manifest, derivative, and content-addressed raw
archive. No credential is retained.

## Selection and import

The full package contains 450,027 rows. The broad group `261` contains 910 rows; the exact
`2611`/`2612`/`2613` rule selects 889 raw rows, 798 distinct canonical variants, and 724
case-sensitive control numbers. The derivative contains 510 code-`2611`, 27 code-`2612`, and 261
code-`2613` variants. Seventy-three control numbers have conflicting variants, 699 are mechanically
current in at least one environmental-control category, and 618 have a valid invariant Taiwan WGS84
point.

The snapshot was accepted at `2026-07-20T07:30:00Z` into schema version 3. The import created:

- 724 source-native facility entities and grouped source records;
- 16,724 atomic claim series and claim versions;
- 618 derived source-point geometries;
- two exact source documents for the raw archive and deterministic derivative; and
- one successful ingestion run with complete same-filter source-interval semantics.

All MOENV claims use publisher-local `valid_from=2026-07-20` and database
`recorded_at=2026-07-20T07:30:00Z`. Validation found no MOENV predicates asserting operation,
production, ownership, operator, lifecycle, or capacity and no MOENV source-entity assignments.
Exact replay created no rows and preserved database SHA-256
`af26eb58e5d244011da9f0c950c4c53f564c3f1eacd560f8bf97a18e6d18e115`.

## Release gate

The 24-file schema-v3 release uses world cutoff `2026-07-20`, knowledge cutoff
`2026-07-20T07:30:06Z`, and manifest SHA-256
`d6cb15eccb6358790f2a85bdc4edce3a13798c750a6004235d56b3d79aa5232a`. A second clean release plus
standalone atlas generation reproduced the complete directory byte-for-byte. `coverage.json`
reports the 724-entity `taiwan_moenv` namespace and all snapshot counts above as complete and
internally consistent. `ATTRIBUTION.txt` includes the canonical Taiwan Open Government Data License
1.0 attribution, and release methodology states that the registry is not a national semiconductor
census and does not establish operation, production, ownership, closure, or capacity.

The earlier GLEIF pilot and schema-v2 canonical artifacts were not overwritten. Their database and
release-manifest hashes remain respectively `a1a7f210fb46de2760e56914f4c69fbab5d8f69a5b66c15b510c33c2bed49e5a`
and `77b52c9b6b79f8713bb07f5290c3022e1be9246441d68d4527bb598394fd500f`, and
`77f2212caf683dfb8dd87f7d35a1e1d2e4caea0f8cba020f200f2ca5c7a49cc8` and
`0a4aa1b6c0c45f44ca6367720e28dc13d86c26636ae5646ef86c14353bedebb5`.

## Semantic boundary and next review

Environmental-control flags mean registry inclusion only. Release dates mean release from one
environmental-control category only. A WGS84 point is not a parcel, building, cleanroom, or fab
boundary. Industry-code confidence is deterministic filter confidence, not a probability of current
semiconductor manufacturing. Business and factory-registration identifiers remain resolution
evidence; organization or cross-source facility assignment requires a separate reviewed decision.
