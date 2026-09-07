# EEA Industrial Reporting v16 candidate-source acceptance audit: 2026-07-20

## Decision

Accept European Environment Agency Industrial Reporting dataset edition `16.00` as a bounded,
source-native semiconductor-facility lead source. The accepted scope is the production-facility
population reported in the pinned EEA relational package. It is not a European industrial-facility
census, a semiconductor-facility census, or a recall denominator.

The tabular source is pinned by
[DOI 10.2909/657ac3cb-affa-4295-a4a9-27b4f539adab](https://doi.org/10.2909/657ac3cb-affa-4295-a4a9-27b4f539adab),
dataset ID `eea_t_ied-eprtr_p_2007-2024_v16_r00`. EEA assigns CC BY 4.0. The derivative must retain
EEA attribution and identify that Semiconductor Atlas filtered and transformed the source.

## Source and clock separation

The manifest records distinct source, acquisition, extraction, derivative, and acceptance clocks:

- reporting-country submission cutoff: `2026-02-10`;
- Access file HTTP `Last-Modified`: `2026-02-17T10:28:13Z`;
- dataset status/publication date: `2026-02-20`;
- source download: `2026-07-20T10:35:04.194155Z` to
  `2026-07-20T10:37:16.510781Z`;
- official metadata retrieval: `2026-07-20T10:51:14Z`;
- primary extraction: `2026-07-20T11:04:46Z` to `2026-07-20T11:04:59Z`;
- independent extraction: `2026-07-20T11:05:00Z` to `2026-07-20T11:05:14Z`;
- accepted candidate generation: `2026-07-20T16:26:42Z`; and
- snapshot acceptance: `2026-07-20T16:42:04Z`.

Publication, file modification, acquisition, and database acceptance are not collapsed into one
timestamp.

## Immutable artifacts

Snapshot:
`source_snapshots/2026-07-20-eea-industrial-v16-semiconductor-candidates`

- manifest SHA-256: `3fe6f2d54a04265e7a5d07b31cf4fc87c25147b2c31e66073ff05d8b9c7f132c`;
- official ACCDB: 2,031,214,592 bytes,
  `804bdcab04a31a4c9fb7ffa7e4affde6253f44d4617b8eadd6a20a90648c28d7`;
- pinned extractor metadata: 16,893 bytes,
  `31686cdc0f9fa3832f11c06d2ea813bfa5f03d5ac320231379ffa7c3111b60c8`;
- Access schema: 15,714 bytes,
  `d61b886e835b0fc7e275af88d339ca841f593008955f9964d6d47bc97951bb6c`;
- v16 table contract:
  `0eefb0342cba39115a243c6a05543ddc8e6f821b4ac620a98c173cc2e2b51dad`;
- candidate JSONL: 1,376,709 bytes,
  `1ecb3aa2f1339739c2b889f5bd2831e7868402da116e39c7ac36e1eb649456a7`; and
- exact tree: 59 manifest-declared inputs plus `manifest.json`, with no extras or symlinks.

The snapshot retains the raw ACCDB, six official metadata/catalogue artifacts, all 43 artifacts from
the primary extraction, the independent extraction manifest, seven exact adapter table views, and
the privacy-minimized candidate derivative. The public-folder listing is sanitized. No request
headers, cookies, credentials, or ephemeral access material are retained.

## Pinned extraction

The extractor runs offline in a read-only, network-disabled container. Its exact image is
`sha256:623681f2ac9779af553e72d0578be8bec5e0dea0c703b76eb4dbd16c4b80bc55`
(`linux/arm64`, mdbtools `1.0.1-0.1`). Package and binary manifests are pinned. Every warning is fatal;
all retained stderr artifacts are empty. The corrected serializer uses the documented mdbtools
1.0.1 CSV defaults, explicit date/datetime formats, a unique NULL sentinel, and hex binary fields.
This avoids the malformed embedded-quote behavior produced by treating `quote` as an escape string.

Two fresh runs produced byte-identical 43-file trees and identical canonical extraction manifests.
The five tables previously exported by an independent direct procedure also matched byte-for-byte.
The Access inventory contains 33 tables. Seven reviewed tables are admitted to the adapter:

| Table | Rows | Bytes | SHA-256 |
| --- | ---: | ---: | --- |
| `0a_DataCollectionMetadata_EUReg` | 663 | 161,145 | `95287699a54ba943004ef4a5cd69ceb0c7bdf0441c18454d5c77f84cb74ab2ad` |
| `0b_DataCollectionMetadata_EPRTR_LCP` | 609 | 148,462 | `92fbf26b4cc14dfecc884428a29b404f469d0c5599903afbad4b9393cace0005` |
| `1_ProductionSite` | 97,758 | 14,899,911 | `dd67c9b80125dd865d187f3bd9eab8821530ad9157418022f5001771226f096c` |
| `2_ProductionFacility` | 99,275 | 76,309,804 | `de8dc0dfd8a1a8580e6a783d8fcf5fd6e91085509d2e2625315a2c8d0fd2622f` |
| `2a_ProductionFacilityDetails` | 873,090 | 336,975,799 | `1b769e18f2d39a9f636809f4d975712b2861cfa4024e592054d6af714d8caf96` |
| `2c_Function` | 97,403 | 8,352,279 | `020b89053f62132bcbca93faefe75ecb8b22fd241919267db1ef6abee3a254d4` |
| `2e_ProductionVolume` | 0 | 125 | `459d9c36d855dcd8bf47c4ef97cc1e43c3a66f8466ef4aaeca5c7242260ec73c` |

The production wrapper rejects any clean truncation, header-only fixture, row-count drift, or byte
change even when the generic structural scanner would otherwise parse it.

## Candidate boundary

The complete reported facility population contains 99,275 exact, case-sensitive facility IDs in 34
observed country codes. The deterministic filter creates 108 manual-review leads in 16 countries:

- 98 have at least one exact raw `NACEMainEconomicActivityCode` cell equal to `26.11`;
- 21 match the bounded explicit facility-name lexicon; and
- 20 inherit bounded explicit name context from their exact parent site.

Those reason counts overlap. Parent-company names never create candidates. Whitespace-padded and
Unicode-normalized lookalikes of `26.11` do not match. Adding the explicit `stmicroelectronics` term
recovers the Swedish silicon-carbide facility that a whole-token `microelectronics` rule missed.
The lexicon is frozen at
`27cae8879f8997025c9cd5b6652211bc4019664ca3094304b9a38892b1d3aaac`.

The derivative retains the complete exact metadata lineage for the facility, parent site, and every
emitted historical detail row across both metadata namespaces. Its latest-detail-year distribution
is explicit: 43 candidates reach 2024, 17 reach 2023, and 48 reach 2019 or earlier. A latest source
year is freshness metadata, not evidence of current operation.

## Quality, confidentiality, and identifiers

- 2,637 detail rows do not join under exact facility-ID semantics. Audit-only classification finds
  2,636 case-only variants and one trailing-whitespace variant, with zero unresolved variants. They
  are never normalized, repaired, or emitted as joined evidence.
- 19,805 facility IDs and 33 candidate IDs use publisher-created `CC.EEA/...` mapping namespaces.
  They remain EEA identifiers, not independent national-registry identifiers.
- Nine facility rows contain `(0, 0)` and are marked as invalid representative points.
- Confidentiality markers suppress 1,451 facility names, 1,431 parent-company names, 221 addresses,
  and 799 detail rows. Either a nonempty reason code or reason name fails closed, including
  whitespace-only markers. Reason names, remarks, parent-company URLs, and other unnecessary free
  text are omitted from the derivative.
- There are no missing parent-site joins, qualifying function orphans, duplicate facility IDs, or
  unresolved detail-key drifts in the accepted package.

## Geographic and capacity limits

The official PDF's geographic list names 31 countries and omits the observed codes `ME`, `NO`, and
`SK`. The catalogue keywords name 33 and omit observed `ME`; the facility table contains 34. The
snapshot retains this discrepancy rather than treating any list as a completeness denominator.
Official notes also identify incomplete recent reporting for multiple countries.

The tabular adapter retains raw latitude/longitude scalars but emits no geometry. The separate
[v16 spatial companion](https://doi.org/10.2909/3bbf28cb-70e8-4073-8fe9-8c1d9c513f52) declares
EPSG:4326, but tabular-spatial correspondence has not been verified.

EEA states that production-volume reporting needs further quality assurance, and the v16
`2e_ProductionVolume` table has zero rows. The adapter therefore emits no production, operation,
facility-type, capacity, utilization, yield, or ramp claim. NACE `26.11` includes PCBs, LEDs,
photovoltaics, and other electronic components; filter membership is not a semiconductor
classification.

## Verification

`scripts/fetch_eea_industrial.py --verify-only <snapshot>` reopens the exact tree without network
access, rehashes every input, validates extractor commands and evidence, rejects nonempty stderr,
rescans the seven exact table exports, replays the candidate JSONL byte-for-byte, and rechecks all
file and root identities across the verification interval.

## Review and database gate

The deterministic review queue is
`review_plans/2026-07-20-eea-industrial-v16-candidates.json`. It is 127,155 bytes with SHA-256
`a5a0738edcbed94f6eb7b75e992e9342a5d1e3b12f04b9789e3191c7bbfaf58d`. The queue cutoff and
generation time are both `2026-07-20T17:05:51Z`. It binds all 108 candidates to the accepted
manifest and derivative hashes. Its work ordering contains 13 priority-one candidates with both an
explicit name and exact NACE `26.11`, 10 priority-two explicit-name candidates, and 85
priority-three exact-NACE-only candidates. Priority is not outcome, and queue membership is not a
semiconductor classification.

The complete review format requires one sorted decision for every queue item. Allowed outcomes are
`accept_in_scope`, `defer`, and `reject_out_of_scope`. Accept and reject decisions require external
HTTPS evidence whose access time does not exceed the review knowledge cutoff. The validator rejects
missing, duplicate, extra, or reordered decisions, noncanonical JSON, changed bindings, and evidence
from after the cutoff.

The source-native importer re-verifies the snapshot, rebuilds the queue byte-for-byte, and re-reads
the review before and after its atomic database write. Only `accept_in_scope` creates a source
record, source-local facility, or claim. Promoted claims are limited to exact source strings for the
INSPIRE facility ID, facility name, address fields, and NACE function code and name. The importer
does not emit a review classification, geometry, lifecycle or operating status, organization
assignment, ownership relationship, production, or capacity claim. Exact replay is write-free; a
changed review fails until explicit replacement semantics are versioned.

At this July 20 gate, no v16 review artifact had been accepted and none of the 108 leads had been
adjudicated or imported into the canonical database. Geometry remained a separate gate requiring
verified correspondence to the spatial companion.

### September 7 follow-up

The [complete bounded scope review and isolated admission](eea_scope_admission_2026-09-07.md)
subsequently accepted 15 source records and deferred 93, with zero final exclusions. The corrected
v2 importer adds 132 exact EEA scalar statements with null effective dates and confidence. Every
parent row is preserved, the frozen parent database is unchanged, and write-denied replay leaves
the admitted derivative byte-identical. This does not establish canonical identity, current
operation or capacity. The remaining evidence/scope holds, spatial gate and append-only review
refresh semantics remain open.
