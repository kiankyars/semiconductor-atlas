# Taiwan registered-factory identity gate — 2026-07-20

## Decision

Use the Ministry of Economic Affairs Industrial Development Administration's
[national registered-factory publication](https://data.gov.tw/dataset/6569) as an independent,
source-native facility registry and as the canonical target for reviewed Taiwan MOENV facility
identity. Do not use it to infer ownership, current physical operation, output, utilization,
capacity, or closure.

The publication is free, requires no authentication, and is assigned the
[Taiwan Open Government Data License 1.0](https://data.gov.tw/license). The catalogue points to an
official [CSV resource](https://www.ida.gov.tw/opendata/02/SDD6569.csv), which in turn points to the
[current national ZIP](https://serv.gcis.nat.gov.tw/RDownLoad/Data/statistical/%E7%94%9F%E7%94%A2%E4%B8%AD%E5%B7%A5%E5%BB%A0%E6%B8%85%E5%86%8A.zip).
The declared refresh cadence is irregular.

## Legal and identifier semantics

The [Factory Management Act](https://law.moea.gov.tw/LawContent.aspx?id=FL011071) defines a factory
as a fixed place used for manufacturing or processing. Factory registration records a factory name
and address; registered changes must be reported, and moving the factory address requires a new
registration application. The number therefore identifies a registered factory/site record, not a
legal organization or an unchanging owner.

MOENV `facno` is the factory-registration certificate number. Official legacy-query guidance maps
old certificate formats to the first eight alphanumeric characters after separator removal. The
implemented normalizer accepts only documented shapes, retains the raw value, and leaves unfamiliar
forms unresolved. It does not guess from name, address, or business number.

MOENV `uniformno` is a business-registration number. It is useful organization-resolution evidence
but is not a facility key: one business number may occur on many factories, and branch numbers may
differ from a head office. No facility-to-organization relationship or ownership claim is created in
this gate.

## Source selection and privacy

The full national package is scanned before filtering. A row is retained only when its line-delimited
principal-product field contains the exact token `261半導體`. Adjacent electronics, PCB, photovoltaic,
equipment, and materials codes are not admitted by substring or industry-section membership alone.

The source includes a responsible person's name. The raw licensed archive is retained for replay,
but that field is deliberately omitted from the deterministic candidate derivative, database claims,
and release exports because it is unnecessary for facility intelligence.

The validated 2026-07-20 package contained:

- 100,363 logical CSV rows;
- 100,362 distinct factory-registration numbers in the full package;
- one byte-identical duplicate outside the selected semiconductor rows;
- 553 exact `261半導體` rows and 553 distinct selected factory numbers;
- 312 distinct selected business-registration numbers; and
- 553 selected rows whose raw administrative registration status was `生產中`.

The accepted snapshot is
`source_snapshots/2026-07-20-taiwan-ida-registered-factories-semiconductor`. Its manifest SHA-256 is
`747f3562c0eadacdf97a8b82b338f5789a92b22c2c2232ba04481ebf343eaac7`; the official ZIP is
`c6d6f3bff8fe8441bb8b0f060de914fcc73e2ab68276841f725cbdfa15264731`; its sole CSV member is
`2bc757ff33bfac4897e52ce00c5f188cc874253571d1a8c7c0e68dfe8e3206a8`; and the deterministic
privacy-minimized candidate JSONL is
`5088b7a64273484268d0cdfbc94dfdfa4dad11ec0861ef9f1b271aebb4412eb6`. The source update clock is
the normalized HTTP `Last-Modified` value `2026-07-20T03:37:05Z`; acquisition completed at
`2026-07-20T08:20:05.886179Z`. Offline verification rescans the retained ZIP and reproduces the
candidate bytes.

`生產中` is preserved only as the publisher's registration status. The Act provides administrative
cessation mechanisms, but the roster can lag real-world activity. It is not evidence that tools are
running, wafers are moving, output exists, or any capacity is economically usable. Absence from this
active-only roster is not evidence of closure.

## MOENV join audit

Among the 724 source-native MOENV facilities:

- 153 had a nonblank, invariant `facno` across their retained variants;
- 129 matched the current factory roster without legacy normalization;
- documented legacy normalization added 14 matches, for 143 candidates total;
- the 143 candidates mapped to 142 distinct registered-factory targets because two MOENV records
  refer to factory number `93A00026` at the same address;
- all 143 had compatible civic street/number evidence after manual review;
- 142 had compatible factory names;
- business numbers agreed for 129, were blank in MOENV for 3, and disagreed for 11; and
- 10 normalized MOENV factory numbers were absent from the active-only roster and remain unresolved.

The candidate score is a review rank, not a truth probability. The safe match rule is an exact
documented factory-number normalization plus reviewed compatible address evidence. Name and business
number agreement corroborate the record but are not required because a registered factory can
survive an organization/name change at the same registered address.

One candidate is explicitly deferred: MOENV entity `J5904800`, old certificate
`99-630508-01`, normalizes to factory `99630508` at the same address, but MOENV names 泰林科技 with
business number `96977956` while the current registry names 南茂科技股份有限公司湖口一廠 with business
number `16130042`. This is strong succession evidence, but it requires temporal review before the two
source-native facilities are assigned to one canonical record.

## Persistence contract

Facility identity uses the schema-v4 same-kind resolution ledger:

1. both the MOENV and factory-registry ingestion runs are declared inputs;
2. every candidate binds the source record, observed facility, target facility, raw and normalized
   identifier evidence, address/name/business-number evidence, and target claim versions;
3. the candidate batch is sealed before review;
4. a separate complete review artifact records one `match`, `reject`, or `defer` outcome per candidate;
5. only a reviewed match creates a bitemporal source-entity assignment; and
6. source-native claims remain on their original entities.

The accepted candidate artifact is
`review_plans/2026-07-20-taiwan-moenv-factory-candidates.json` (SHA-256
`1c296635a4a968c35c2b1430fdc39ca0db49be427e4206b14e982807c095ab48`). The complete review artifact
is `review_plans/2026-07-20-taiwan-moenv-factory-review.json` (SHA-256
`d7b2082576b254c1c22805abd1f54bfe633627504049cb5a2963f8191062d0e0`). Resolution run
`079334bc-a264-55fa-bfec-7211ac1d9a99` recorded 142 matches and one defer; exact replay wrote zero
rows and preserved database SHA-256
`8bcd2e840281f6646a967e3ef9ae6de798028e9245af9cc4fba9df6ffac99011`.

The deterministic schema-v4 release is
`releases/2026-07-20-open-seed-taiwan-factory-identity`. It contains 4,475 entities, 100,101 claim
versions, 4,434 source observations, 144 total resolution candidates/decisions including the earlier
GLEIF pilot, and 143 total assignments. A second independent build including `atlas.html` was
byte-identical; final manifest SHA-256 is
`bbf652a306f92e94a459bc2e16eb95b3e6039e2bcbf08756866141e1bb05a694`.

If later current evidence invalidates a match, the old artifact cannot create a new assignment. Its
restricted correction path may only retract or identically reaffirm an open assignment that it
previously created, with any supersession proven by the later succeeded resolution run.

Cross-kind facility-to-organization identity is prohibited. The later MOF gate follows this boundary:
tax units remain source-native organizations and exact UBN agreements become separately reviewed,
typed references rather than identity or ownership. See
`docs/taiwan_mof_relationship_gate_2026-07-20.md`.
