# First non-U.S. facility-source gate

Review date: 2026-07-20 UTC. This note records a source-selection decision, not a claim that any
listed facility is operating or producing semiconductors.

## Decision

Implement Taiwan Ministry of Environment dataset `EMS_S_01`,
"環境保護許可管理系統(暨解除列管)對象基本資料," as the first non-U.S. facility and geography
adapter.

The [official dataset record](https://data.moenv.gov.tw/dataset/detail/EMS_S_01) identifies the
Resource Circulation Administration as publisher, documents a daily update cadence, and defines a
source-native control number, facility name, business identifier, full address, industrial area,
four-digit industry classification, TWD97 coordinates, WGS84 coordinates, five environmental
control flags, release dates, and an optional factory-registration number. The
[national catalogue record](https://data.gov.tw/dataset/118447) assigns Taiwan Open Government Data
License 1.0. The [license](https://data.gov.tw/license) allows reuse and derivatives, including
commercial use, but requires explicit attribution.

The [official industry taxonomy](https://www.stat.gov.tw/StandardIndustrialClassificationContent.aspx?Level=3&PID=MjYx&RID=9&n=3144&sms=11195)
distinguishes the three exact in-scope classes:

- `2611`: integrated-circuit manufacturing;
- `2612`: discrete semiconductor-device manufacturing; and
- `2613`: semiconductor packaging and testing.

The broad API prefilter `industrygroup=261` is not sufficient by itself. Every accepted row must
pass one of the exact four-digit codes locally and match its official Chinese label. The
[documented API](https://data.moenv.gov.tw/paradigm) is a refresh transport; an immutable full
package, its publisher checksum, and a deterministic exact-code derivative are the reproducibility
anchor.

## Pinned evaluation package

The official full JSON package was retrieved at `2026-07-20T06:57:50Z`. The dataset page displayed
last update `2026-07-20 07:15:13` in Taiwan local time.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Official ZIP | 36,386,242 | `531920946aed3560c7aca3c8fea629478dded24154894af33d8064f507b2adee` |
| Inner JSON | 409,441,780 | `e0c62aece37dd46bdfc3cf9598dbdb9b620f0e00db3703da4befe2e0a439ca33` |

The publisher's `hash.txt` declares MD5
`a008f9bc87da8f907b2dd9de29d827c3`, which matches the inner JSON. Independent inspection found
450,027 total rows and a fixed 26-field schema. The exact `2611`/`2612`/`2613` subset contains 889
raw rows, 798 distinct row payloads, and 724 case-sensitive control numbers.

Repeated control numbers are source variants, not duplicate facility entities. Of 147 repeated
control numbers, 74 repeat an identical payload and 73 contain at least one conflicting field.
Facility name, address, exact industry code and label, TWD97 coordinates, and WGS84 coordinates are
invariant within every repeated exact-code group in this package. Conflicts occur in historical
release/de-list dates and a small number of environmental-control, business-identifier, and
industrial-area fields. The adapter must collapse exact payload duplicates while retaining every
distinct variant.

The source includes currently controlled and historically released records. "Currently
environmentally regulated" means at least one of the five source flags equals `1`; it does not mean
operating, producing, qualified, or economically usable. A valid official WGS84 point is a source
facility point, not a surveyed parcel or fab boundary. `(0, 0)`, partial, nonnumeric, and
out-of-Taiwan coordinate pairs remain missing geometry.

## Required acceptance boundary

The adapter may create source-native facility records and exact source statements for identity,
address, industry classification, regulatory fields, and valid WGS84 points. It may create a
deterministic semiconductor-facility candidate classification whose confidence refers only to
filter membership. It must not infer:

- current operation or semiconductor production from environmental-control flags;
- operator or owner identity from a similar facility name;
- wafer diameter, process node, technology, yield, utilization, capacity, or ramp timing;
- parcel or building geometry from the representative point; or
- closure, cancellation, or inactivity from a missing later row without a verified complete
  same-filter refresh and explicit source semantics.

Cross-source organization or facility assignment requires a separately reviewed decision. Business
and factory-registration numbers are resolution evidence, not automatic merges.

## Alternatives reviewed

### European Environment Agency

The [EEA Industrial Emissions Portal dataset](https://industry.eea.europa.eu/industrial-emissions/dataset)
version 16 is an official CC BY 4.0 source with stable INSPIRE identifiers and representative
facility points. Its tabular release is pinned by
[DOI 10.2909/657ac3cb-affa-4295-a4a9-27b4f539adab](https://doi.org/10.2909/657ac3cb-affa-4295-a4a9-27b4f539adab).
The separate [version-16 spatial companion](https://doi.org/10.2909/3bbf28cb-70e8-4073-8fe9-8c1d9c513f52)
declares EPSG:4326. A tabular candidate adapter should retain the raw latitude/longitude scalars and
emit no geometry until correspondence with that spatial product is verified. It remains a useful
reviewed European lead source, but it was not selected first: the convenient
CSV files omit the complete facility inventory, the complete relational package is a 2.03 GB Access
database, the live `latest` query layer is mutable, and NACE `26.11` includes PCBs, LEDs,
photovoltaics, and other electronic components. EEA activity codes and `functional` status do not
establish semiconductor operation or capacity.

### South Korea

KICOX FactoryOn publishes a national registered-factory snapshot and a
[live factory API](https://www.data.go.kr/data/15087611/openapi.do) with management numbers, full
road addresses, products, and five-digit industry codes. It is a strong future identity source,
but live access requires a Public Data Portal application/key, there is no nationwide query by
industry code, the current bulk discovery file lags, and the API has no coordinates. It is deferred
until credentials and a recall-auditable discovery strategy are available.

### Japan

The Ministry of Environment's
[EEGS facility disclosures](https://eegs.env.go.jp/ghg-santeikohyo-result/search) support exact
semiconductor industry filters and keyless official downloads, but cover only thresholded GHG
reporters and publish municipality rather than street-level geography. NITE PRTR files have richer
addresses and joinable older GIS data, but their industry code is broad and NITE's site terms do
not clearly authorize the intended republication and transformation. NITE PRTR is disabled pending
written rights clarification.

## Acceptance status and next gates

The first five gates are implemented and accepted: a fail-closed streaming scanner, immutable raw
retention, deterministic derivative, offline semantic verification, source-native bitemporal import,
and conditional public-release attribution and limitations. The accepted snapshot manifest and
derivative are recorded in `source_snapshots/2026-07-20-taiwan-moenv-ems-s-01-semiconductor`.

The deferred EEA alternative has since passed its candidate-source gate. The accepted v16 snapshot
retains the exact 2.03 GB Access database, the complete 99,275-row EEA-reported production-facility
population, two byte-identical pinned extractions, official metadata evidence, and a deterministic
108-record manual-review derivative. This does not convert EEA reporting into a European census,
operating-status signal, geometry product, or capacity source. See
`docs/eea_industrial_v16_acceptance_audit_2026-07-20.md`.

The EEA review boundary now has a deterministic 108-item queue and a source-native importer that
fails closed on changed snapshot, queue, or review bytes. No review ledger has been accepted and no
EEA candidate has been imported. Candidate adjudication therefore remains open.

The remaining gates are:

1. a separate reviewed resolution pass using business and factory-registration identifiers; and
2. companion permit adapters, beginning with water permits, only after quantity semantics are
   separated from semiconductor output and capacity.
