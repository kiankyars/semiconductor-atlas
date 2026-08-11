# Taiwan MOF active tax-registration snapshot audit — 2026-07-20

## Decision

Accept the Ministry of Finance Fiscal Information Agency `BGMOPEN1` publication as a bounded,
source-native organization-evidence layer. It is an active tax-registration dataset, not a legal
company, ownership, parentage, facility-operation, or closure register.

The official catalogue is [dataset 9400](https://data.gov.tw/dataset/9400), and the fixed archive is
`https://eip.fia.gov.tw/data/BGMOPEN1.zip`. The publisher assigns the
[Taiwan Open Government Data License 1.0](https://data.gov.tw/license). Public derivatives and
releases require this attribution:

> Source: 財政部財政資訊中心 (Taiwan Ministry of Finance, Fiscal Information Agency),
> 全國營業(稅籍)登記資料集, 2026. Released under the Taiwan Open Government Data License,
> Version 1.0: https://data.gov.tw/license

## Bounded selection

The allowlist is not a name or industry-text search. It is the sorted union of valid eight-digit UBN
evidence from the accepted Taiwan MOENV and registered-factory snapshots. Blank and malformed values
are excluded. When one source record has conflicting nonblank UBN variants, every conflicting value
from that record is excluded; another independently accepted record may still contribute the same
valid UBN.

The accepted allowlist contains 466 UBNs. The complete national CSV has 1,709,795 business rows after
its publisher-date row. Exact lookup produced 390 unique matches and 76 explicit misses. Organization
types among matches are 332 `股份有限公司`, 32 `有限公司`, 18 domestic-company branches, four
foreign-company branches, two `其他`, and two sole proprietorships. Those labels remain exact source
statements; they do not authorize parent or ownership inference.

The privacy-minimized derivative retains UBN, head-office UBN, business name and address, raw
establishment date, organization type, and exact industry code/name pairs. It omits capital and
invoice-use fields. A head-office UBN is context only.

## Immutable artifacts

Snapshot:
`source_snapshots/2026-07-20-taiwan-mof-bgmopen1-semiconductor-organizations`

- manifest SHA-256: `c14892bffca9deccf5a5720aab19b03daa7b8aa14fc49d77f9d6cc1dac1541ce`
- official ZIP SHA-256: `bee88f8a8c2ceb0710695269cbee013cc50215550bf72548a5054efee2bd744f`
- inner CSV SHA-256: `10c803a67defe9bf1ee5cb4e87c9dace038ecc160a0595fcb775dcfc2fbb03ef`
- canonical allowlist SHA-256: `b82feae0996ebabbb0f3fc854e93ed192189bbf7ab86b48385cbbcba7d8de0bf`
- matched JSONL SHA-256: `42825de080e835d4250e0fd8883d6df687c0c33946f230afd06ead4bf7e0ebfb`
- HTTP source update: `2026-07-19T21:12:27Z`
- publisher date: `2026-07-20` (Taiwan local date)
- retrieval completion: `2026-07-20T09:13:23.569375Z`

Offline verification re-hashes and replays the archive, exact 16-column CSV, publisher-date row,
allowlist, matched derivative, manifest, and exact directory tree. Source-aware verification then
re-derives the allowlist from both bound facility snapshots, reopens the MOF target, and compares its
captured identity.

## Acquisition and failure semantics

The fetcher performs one fixed-url GET with verified TLS and rejects redirects before a second
request. Archive, CSV, allowlist, derivative, and manifest sizes are bounded. ZIP paths, encryption,
compression ratio, member count, CRC, descriptor identity, and mutation clocks are checked. Creation
fully verifies the staging tree and both source bindings before atomic no-replace installation. A
post-install identity failure moves the just-installed inode back to staging before reporting failure,
so caller-visible failure does not leave an unexpected committed target.

Publisher dates must equal the HTTP `Last-Modified` UTC date or its UTC+8 Taiwan-local date and cannot
postdate Taiwan-local retrieval. This rejects two-digit-year century mistakes such as interpreting a
1999 row as 2099.

## Accepted database import

Run `9171c9df-7772-5d25-a9c7-bb869570dad6` accepted the snapshot at
`2026-07-20T09:44:32Z` and completed at `2026-07-20T09:44:33Z`. It added 390 source-native tax-unit
organizations, 390 source records, three distinct source documents (six role bindings), and 3,241
directly evidenced claims.
No owner, operator, parent, facility identity, lifecycle, production, or capacity claim was created.
Exact replay wrote zero rows and preserved the database hash.

The accepted database is `artifacts/2026-07-20-open-seed-taiwan-mof-organizations.sqlite` (SHA-256
`bcb39fb04a927484c1e74417f0952370bc7f8db5577bc463bf7dd46f6a9ecab9`). Its deterministic 23-managed-file
release is `releases/2026-07-20-open-seed-taiwan-mof-organizations`; an independent rebuild including
the standalone atlas was byte-identical, with final manifest SHA-256
`caf69a04edcd1a314d458d0844f123ed8d8319cb9e7c28308807b6bb82b02c29`.

The separately reviewed facility-reference stage is accepted and audited in
`docs/taiwan_mof_relationship_gate_2026-07-20.md`. Exact UBN agreement creates only a typed
`registered_tax_unit_reference`; it does not create facility identity, ownership, operator,
parentage, activity, output, lifecycle, or capacity claims.
