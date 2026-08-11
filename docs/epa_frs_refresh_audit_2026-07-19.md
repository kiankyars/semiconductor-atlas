# EPA FRS refresh audit: 2026-07-19

This audit covers the canonical EPA FRS refresh in
`artifacts/2026-07-19-open-seed.sqlite` and `releases/2026-07-19-open-seed`.

## Pinned input and acceptance

- Source snapshot: `source_snapshots/2026-07-19-epa-frs-semiconductor-candidates`
- Retained national archive: 349,470,568 bytes, SHA-256
  `a3d988e5f6486eb248a808faada05111d55400cf1416d003d68ee06874d1ef75`
- Candidate derivative SHA-256:
  `c0dbbae7ca684c10e943e10aabe8997e79aa5672f2bc4bb4e8bb5d4cde2e61b5`
- Snapshot manifest SHA-256:
  `85f3bb1a54d4033f7afcce21ae3cdd8bb9752c51fca678a0c57a2611ae302f3b`
- Archive retrieval: `2026-07-20T01:12:47Z`
- Database acceptance: `2026-07-20T03:08:57Z`, supplied explicitly
- FRS run: `f42fed8b-ad88-5854-bbcc-763254a0f816`

The importer admitted 3,065 direct-code candidates from 5,300,149 upstream rows. It used exact
NAICS `334413` or SIC `3674`; it did not infer lifecycle, operating status, or capacity.

## Refresh and replay gates

A synthetic later acceptance of the same complete snapshot finished in 20.36 seconds. It created
3,065 new source observations, reused all 74,896 FRS claims, created no claim versions, and closed or
corrected no prior claims. Exact replay of the accepted run wrote nothing and left each database
file hash unchanged.

Snapshot verification and database acceptance are bound to the same candidate, raw-archive, and
manifest bytes. Regular-file, no-symlink, size, digest, and path-identity checks run immediately
before commit. Tests cover mutations after verification, transient swaps, symlinks, FIFOs, rollback,
and a successful retry.

## Rebuild and release gates

Two databases were rebuilt independently from the pinned open-source and FRS snapshots. Their rows
matched in every table except `schema_migrations.applied_at`, which records the wall-clock migration
installation time and is not exported. The two release directory trees were byte-identical across
19 files: 18 manifest-managed files plus `manifest.json`.

Canonical checksums:

- Database SHA-256: `77f2212caf683dfb8dd87f7d35a1e1d2e4caea0f8cba020f200f2ca5c7a49cc8`
- Release manifest SHA-256:
  `0a4aa1b6c0c45f44ca6367720e28dc13d86c26636ae5646ef86c14353bedebb5`

The canonical database passed SQLite integrity and semantic validation with 3,197 entities, 34
source documents, 3,156 source observations, and 76,452 current and historical claims. The release
includes the parameter-only NIST page 3 input, complete FRS processing-run parameters, separate
retrieval and acceptance clocks, raw-archive linkage, and the standalone HTML atlas in its manifest.

The warning-clean core suite passed 158 tests; the web suite passed 11 tests.

## Remaining limits

FRS declassification, archive absence, and Registry ID merge signals still need explicit evidence
models. None may be interpreted as facility closure. This U.S. public registry adapter is not a
worldwide facility census and has no measured recall denominator.
