# Semiconductor Atlas data contract

Status: normative v0.1 contract. `MUST`, `MUST NOT`, `SHOULD`, and `MAY` have their usual standards
meanings.

## 1. Layering

The system has four data layers:

1. **Source layer:** immutable retrieved objects, exact evidence fragments, extracted source
   records, and source-level claims.
2. **Canonical layer:** resolved entities and versioned reconciled facts or estimates.
3. **Analytical layer:** pinned forecasts, scenarios, evaluations, and alerts.
4. **Release layer:** deterministic, rights-filtered snapshots and manifests.

Records MUST NOT skip lineage between layers. A canonical or analytical value MUST resolve to
evidence fragments, or to an explicitly declared scenario assumption.

## 2. Entities and identity

An entity has an immutable identifier, kind, stable key, creation time, and creation decision. The
minimum kinds are `organization`, `site`, `facility`, `building`, `production_unit`, `project`, and
`infrastructure_asset`.

Mutable names, locations, boundaries, classifications, and relationships MUST be claims rather
than columns on an entity master row. Subtype records MAY enforce structural constraints but MUST
NOT become a second mutable truth store.

The containment model is:

```text
site
  -> facility
       -> production_unit
  -> building

project -> one or more target entities
```

Containment and targeting are versioned relationships. Cycles are invalid. Co-location does not
imply containment or identity.

Organization relationships use typed roles such as `owner`, `operator`, `joint_venture_partner`,
`customer`, `contractor`, `equipment_supplier`, `utility`, and `financier`. They MAY include an
economic share, contract or qualification state, and process, product, or project scope. Free-text
owner and supplier lists are not canonical.

## 3. Sources, documents, and fragments

A source record MUST include a stable source identifier, source family, source type, publisher or
authority, access status, and rights metadata. Unknown rights MUST be represented as unknown; they
MUST NOT be converted into an invented open license.

Each retrieval MUST record:

- canonical URL or official source key;
- publication time when known;
- acquisition or observation time when applicable;
- retrieval time;
- MIME type and language;
- SHA-256 of the exact bytes or canonical source record;
- blob locator or an explicit hash-only retention reason;
- license, attribution, access class, and redistribution class; and
- ingestion run and adapter version.

The canonical source-record hash MUST be recomputed from its canonical JSON payload during insert
and validation; a merely well-formed digest is insufficient. A claim may use a source record only
after both that record's observation time and its ingestion run's start time. Evidence-fragment
identity includes the excerpt as well as document, record, role, and locator, so distinct fragments
cannot collapse silently.

An evidence fragment MUST locate the exact support inside a retrieved object. Locators can contain
document page and section, table and cell, media timestamp, dataset row key, geographic footprint,
or imagery acquisition identifier. Excerpts MUST respect redistribution limits.

Evidence links are many-to-many. Each link has a role of `support`, `refute`, or `context`, and
MAY carry extraction metadata and link confidence. Source-family membership, rather than URL count,
defines evidence independence.

### EPA FRS source profile

The EPA Facility Registry Service adapter MUST use source family `epa-frs`, source key
`epa-frs-national-single:epa-frs-semiconductor-direct-v1`, and entity stable-key prefix
`epa:frs:`. Its ingestion input MUST be a deterministic, schema-complete filtered derivative made by
scanning EPA's official monthly National Single File. It MUST retain every selected 39-field row,
the exact filter, retrieval time, upstream archive and CSV hashes and byte counts, CSV CRC, and
full-row denominator. It MUST record the retrieval timestamp basis and data-as-of source and basis.
The snapshot MUST copy the raw ZIP to `raw/sha256/<archive-sha256>.zip` and verify that regenerating
the derivative produces the declared candidate hash and count. `data_as_of` MUST NOT be later than
`retrieved_at`. Imports MUST preserve source retrieval order, database acceptance order, and
non-decreasing data-as-of order, including complete snapshots that select zero candidates.

Candidate inclusion MUST require exact NAICS `334413` or exact SIC `3674`. Adjacent classifications,
including NAICS `325180`, `334418`, and `334419`, MUST NOT be widened into the filter. These records
MUST be represented as U.S. registry candidate leads only and MUST NOT create claims of operation,
lifecycle state, semiconductor activity, capacity, or global coverage.
Candidate-classification confidence applies to deterministic execution of the exact-code rule, not
to the probability that a row is an operating semiconductor facility.

FRS NAD83 latitude and longitude MUST remain raw source scalars unless a separately evidenced and
versioned geometry transformation is implemented. The scalars MUST NOT be serialized as GeoJSON
geometry, and the current adapter MUST NOT imply a CRS transform or FRS geometry import. A missing,
reassigned, or merged Registry ID MUST NOT be interpreted as closure, cancellation, or inactivity.
Corrections MUST preserve prior source records and claim versions while updating source-to-entity
resolution explicitly. Only a verified-complete later snapshot using the identical filter may close
the valid-time intervals of candidates absent from the selected derivative. Such non-selection means
only that the Registry
ID is no longer selected by that source/filter; it MUST NOT emit closure, cancellation, inactivity,
or a merge redirect. Distinguishing code removal from archive absence requires additional raw-source
change metadata.

Public attribution MUST include: `Source: U.S. Environmental Protection Agency, Facility Registry
Service (public-domain U.S. Government data; no EPA endorsement)`. Releases MUST NOT use EPA seals
or logos or imply EPA review, warranty, or endorsement.

### Taiwan MOENV EMS_S_01 source profile

The Taiwan Ministry of Environment adapter MUST use source family `taiwan-moenv-ems`, source key
`taiwan-moenv-ems:ems_s_01`, and source-native facility stable keys
`taiwan-moenv-ems:ems_s_01:<case-sensitive-emsno>`. It MUST retain the official full-package ZIP at
`raw/sha256/<archive-sha256>.zip`, validate the publisher MD5 for the inner JSON, and regenerate a
canonical JSONL derivative from that retained object. The snapshot MUST pin the exact official POST
endpoint and sanitized `pid`, `rid`, and `download_type` body, separate dataset-update and retrieval
timestamps with their bases, all archive/member/checksum hashes and byte counts, the complete
26-field schema, full-row denominator, filter version, and rights decision. Credentials MUST NOT be
accepted into or persisted by the snapshot.

Candidate inclusion MUST require exact `industrygroup` `261` and exact `industryid` `2611`, `2612`,
or `2613`, paired with its exact official Chinese industry label. The importer MUST preserve all
case-sensitive control numbers, collapse only identical canonical payloads, and retain every
distinct same-control-number variant in one grouped source record. Conflicting non-empty values MUST
remain parallel source statements; empty strings and true source nulls MUST remain distinguishable
in the retained payload.

Environmental-control flags and release dates are registry facts only. They MUST NOT create claims
of operation, production, lifecycle, ownership, operator, technology, capacity, qualification, or
utilization. A deterministic candidate classification MAY depend on the exact industry-code claim,
and a deterministic `currently_environmentally_regulated` value MAY depend on all five raw flags,
provided both explicitly deny those broader meanings. Business, administration, and factory
registration identifiers are resolution evidence and MUST NOT create an automatic organization or
facility assignment.

A geometry claim MAY be derived only when every retained variant for a control number supplies the
same valid WGS84 pair inside the bounded Taiwan coordinate range. Raw coordinate strings MUST also
remain source claims. The point is source-reported facility geography, not a surveyed parcel,
building, cleanroom, or fab boundary. Missing, zero, partial, nonnumeric, conflicting, or
out-of-range pairs MUST NOT create geometry.

Only a verified-complete later full-package snapshot using the identical exact filter may close a
prior MOENV source-assertion interval. Such closure records only later source nonselection or field
absence; it MUST NOT assert real-world facility closure, inactivity, cancellation, or production
stop. Partial imports MUST NOT close omitted values or facilities. Public releases containing MOENV
data MUST include: `Source: 環境部資源循環署 (Taiwan Ministry of Environment, Resource Circulation
Administration), 環境保護許可管理系統(暨解除列管)對象基本資料 (EMS_S_01), 2026. Released under the
Taiwan Open Government Data License, Version 1.0: https://data.gov.tw/license`.

### Taiwan registered-factory source profile

The Taiwan Industrial Development Administration adapter MUST use source family
`taiwan-ida-factory`, source key `taiwan-ida-factory:registered-factories`, and source-native
facility stable keys `taiwan-ida-factory:registered-factories:<eight-character-registration-number>`.
It MUST retain the exact official national ZIP at `raw/sha256/<archive-sha256>.zip`, scan its sole
root CSV to completion, and regenerate a deterministic privacy-minimized JSONL derivative. The
snapshot MUST pin the exact download URL, response `Last-Modified` source-update clock, retrieval
clock, archive/member hashes, CRC and byte counts, complete row denominator, exact 13-column schema,
filter version, redaction rule, and rights decision. Response metadata MUST be sanitized and MUST NOT
contain credentials or cookies.

Candidate inclusion MUST require the exact line-delimited principal-product token `261半導體`.
Section `26`, adjacent product codes, substrings, company names, and address terms MUST NOT widen the
filter. The source responsible-person field MUST be validated in the raw schema but MUST be omitted
from the derivative, claims, and public releases. Factory number, factory name and address, business
number, organization type, raw approval dates, administrative registration status, industry tokens,
and product tokens MAY remain source statements.

The publisher value `生產中` MUST be represented only as an administrative factory-registration
status. It MUST NOT create operation, output, lifecycle, capacity, utilization, ownership, or
qualification claims. Absence from this active-only roster MUST NOT become closure evidence. A
verified-complete same-filter refresh MAY close only the earlier source-assertion interval; a partial
import MUST NOT close omitted values or facilities.

MOENV `facno` MAY create a facility-identity candidate only after a documented legacy certificate
shape is normalized to its first eight alphanumeric characters and exactly matches a factory-registry
number. The raw value MUST remain preserved. Address evidence MUST be reviewed for compatibility;
name and business-number differences MUST remain visible. An exact factory number MUST NOT create a
facility-to-organization assignment or ownership claim. Public releases containing this source MUST
include: `Source: 經濟部產業發展署 (Taiwan Ministry of Economic Affairs, Industrial Development
Administration), 登記工廠名錄, 2026. Released under the Taiwan Open Government Data License,
Version 1.0: https://data.gov.tw/license`.

The complete facility-identity candidate artifact MUST freeze state at its explicitly bound selected
ingestion runs, not at a later caller cutoff. Reused evidence MAY come from an older producer run, but
every evidence value MUST bind its exact claim version and source record. Later same-source runs MUST
NOT leak into the artifact. A stale artifact MUST NOT create or restore an assignment. A correction
MAY only retract or identically reaffirm an open assignment previously created from that artifact,
and replay MUST verify any later supersession through its succeeded correction run.

### Taiwan MOF active tax-registration source profile

The Taiwan Ministry of Finance adapter MUST use source family `taiwan-mof-tax`, source key
`taiwan-mof-tax:bgmopen1`, and source-native organization stable keys
`taiwan-mof-tax:bgmopen1:<eight-digit-UBN>`. The acquisition MUST retain the exact fixed-url national
ZIP at `raw/sha256/<archive-sha256>.zip`, validate the exact 16-column root CSV and publisher-date row,
scan the complete archive, and regenerate both the canonical UBN allowlist and matched JSONL.

The allowlist MUST be a sorted exact union of valid UBN evidence from the separately verified MOENV
and registered-factory snapshots. Blank and malformed values MUST be excluded with diagnostics. All
values from a source record with conflicting nonblank UBN variants MUST be excluded for that record;
the same valid UBN MAY still enter through another accepted source record. Source-aware verification
MUST re-derive the allowlist and its source bindings after reopening the target snapshot.

The derivative MAY contain UBN, head-office UBN, business name and address, raw establishment date,
organization type, and exact industry code/name pairs. Capital and invoice-use fields MUST be omitted.
An imported UBN identifies a source tax unit, not necessarily a legal company. Head-office UBN is
contextual evidence only and MUST NOT create parentage. Exact UBN agreement with facility evidence
MUST NOT create identity, ownership, operator, or activity claims; any facility-to-tax-unit link
requires a separate reviewed relationship artifact.

A Taiwan facility-to-tax-unit review MUST bind the exact selected MOENV, registered-factory, and MOF
ingestion runs, their immutable snapshot hashes, every source claim and record used as evidence, and
one complete terminal decision per candidate. Accepted links MUST use predicate and relationship type
`registered_tax_unit_reference`, claim kind `reconciled_fact`, and method
`taiwan_registered_tax_unit_reference_review_v1`. A match asserts only exact same-time UBN agreement
between selected source records. Its attributes MUST explicitly deny identity, ownership, parentage,
and operator or operation semantics. Reject and defer decisions create no link; a later complete
review MAY supersede or identically reaffirm a prior reviewed link without deleting its history.

Releases MUST export each completed reviewed-relationship run only after every created, reaffirmed,
or superseded claim effect is visible at the release knowledge cutoff. Every claim ID named by that
run MUST be present in `claim_history.jsonl`, including claims outside the release world-state cutoff.
`reviewed_relationship_runs.jsonl` MUST preserve the exact candidate/review hashes and byte counts,
reviewer and clocks, selected source bindings, complete decisions, and created/reaffirmed/superseded
lineage. Coverage MUST report the current reviewed-method relationship count separately from any
future source-native or alternative-method relationship using the same type.

The publisher date MUST agree with the HTTP `Last-Modified` UTC date or its Taiwan-local UTC+8 date
and MUST NOT postdate Taiwan-local retrieval. Redirects MUST be rejected before a second request.
Public releases containing this source MUST include: `Source: 財政部財政資訊中心 (Taiwan Ministry
of Finance, Fiscal Information Agency), 全國營業(稅籍)登記資料集, 2026. Released under the
Taiwan Open Government Data License, Version 1.0: https://data.gov.tw/license`.

## 4. Atomic claims

Every published field MUST correspond to one atomic claim version. A claim series is identified by
subject, predicate, canonical dimensions, and layer or source authority. Examples are:

```text
facility F / canonical name
site S / boundary geometry
organization O / owns / facility F / economic share
production unit U / process capability / N3E / 300 mm
production unit U / wafer starts per month / qualified / N3E / 2028-Q1
```

A claim version includes:

- claim kind: `source_statement`, `direct_observation`, `reconciled_fact`, or
  `derived_estimate`;
- valid-time and system-time ranges;
- method and producing run;
- review state;
- calibrated claim confidence, or null when uncalibrated; and
- evidence and upstream-claim links.

Each claim version MUST have exactly one typed value representation. Supported representations are:

- scalar text, boolean, date, identifier, or controlled concept;
- geometry plus accuracy or precision;
- relationship to another entity;
- project milestone;
- process or packaging capability;
- capacity quantity;
- resource quantity; or
- constraint state and impact.

A predicate registry MUST declare allowed entity kinds, value representation, cardinality, and
canonical unit family. Application validation MUST reject values that do not match this registry.
Arbitrary JSON MAY preserve source payloads or non-canonical metadata, but MUST NOT hide a
publishable canonical field.

## 5. Facts versus estimates

A `source_statement` records what a source said. Its extraction can be certain even when the
reported proposition is doubtful. A `direct_observation` records a measured or visible event with
its own limitations. A `reconciled_fact` records the system's current evidence-backed
interpretation. A `derived_estimate` records a calculation or model output.

Derived estimates MUST name a method version and every input claim version. Numeric or modeled
estimates MUST include an uncertainty interval. A categorical deterministic transform MAY omit an
interval only when its confidence explicitly refers to rule execution rather than the probability of
the underlying real-world state. Derived estimates MUST NOT overwrite reported values. Conflicting
source claims remain in the source layer even after reconciliation.

Forward-looking company statements are source claims. System forecasts use the forecast contract in
section 11 and MUST NOT be represented as reconciled facts.

## 6. Bitemporal history

Claim validity uses half-open ranges:

```sql
valid_from <= world_time AND (valid_to IS NULL OR world_time < valid_to)
system_from <= knowledge_time AND (system_to IS NULL OR knowledge_time < system_to)
```

The v0.1 schema represents `system_from` as `recorded_at` and `system_to` as `superseded_at`.

`valid_*` means when the proposition applies in the world. `system_*` means when that version was
accepted by this database. A correction closes only the old system range and inserts a new immutable
version. Late-arriving evidence MAY introduce a new version for a historical valid range.

At most one canonical version of the same claim series may be active over the same valid and system
ranges. Competing source-level claims MAY overlap. Database constraints SHOULD enforce this rule;
SQLite implementations MUST validate it before release.

Source publication, acquisition, retrieval, and extraction clocks are not substitutes for either
bitemporal clock. Event observations SHOULD preserve an explicit event time or range.

From schema 5, a `source_statement` MAY retain `valid_from = null` only with `valid_to = null`:
the statement's effective time is unknown. This MUST NOT mean that it applies at every world time.
Physical-world `claims.jsonl` excludes such statements. The separate `source_claims.jsonl` view
selects by knowledge time alone, including known statements with unknown or future effective dates.
`claim_history.jsonl` retains their superseded versions and complete dependencies at the same
knowledge cutoff. A missing confidence value MUST remain null rather than being replaced with zero,
one, or an invented calibrated probability. Historical schemas keep their original release shape.

Reviewed source-native project targets MAY use expected milestone values with `date_base = null`,
literal source wording, and explicit calendar precision. Their low/high dates bound the stated
calendar period, not a probabilistic forecast interval. A source-stated year or half-year MUST NOT
receive a synthesized midpoint or enter legacy midpoint-based acceleration or ramp calculations.
Separate retained document versions MAY have distinct source-statement series on one unassigned
source-native project. Their simultaneous later review MUST preserve one actual admission clock,
not fabricate a historical acceptance interval between old and new document observations.

EPA FRS imports use `source_documents.retrieved_at` for archive acquisition and
`ingestion_runs.started_at` for database acceptance. New claim `recorded_at` values and prior claim
`superseded_at` values MUST use the acceptance timestamp. Deterministic builds require an explicit
acceptance timestamp; live refreshes MAY sample the process clock after snapshot verification. An
exact replay MUST use the original acceptance timestamp and verify the original plan without writes.

MOENV imports use the package retrieval time for `source_documents.retrieved_at`, the explicit
database acceptance time for `ingestion_runs.started_at`, and the publisher-local Asia/Taipei date
corresponding to the verified dataset-update timestamp for claim `valid_from`. The UTC calendar date
MUST NOT replace the publisher-local date when they differ. Exact replay MUST reverify the closed
snapshot and original database ledger without writes.

An adapter may close claims by absence only when its input is explicitly declared a complete
snapshot for that source scope. The transaction-time range is closed while the prior claim and
evidence remain immutable. Incremental or partial input MUST NOT be interpreted as negative
evidence. Repeated fields use semantic identities rather than list positions so source reordering
cannot reassign a claim series.

## 7. Classification and capability

Controlled concepts MUST distinguish:

- facility activity: front-end fabrication, advanced packaging, assembly, test, substrate,
  photomask, semiconductor materials, or other;
- product or business family: foundry, logic, DRAM, NAND, analog, power, compound semiconductor,
  photonics, image sensor, or other;
- process and packaging technology; and
- readiness: planned, equipment installed, qualified, or production.

Classifications are multi-valued. A capability value SHOULD include the process family,
vendor-marketed label, optional nominal node and node basis, wafer size, product scope, packaging
technology, and readiness when those dimensions are known. Unknown dimensions remain null.

Node labels MUST NOT be treated as comparable numeric measurements unless their physical basis is
explicit. A conversion or migration is a project or milestone claim, not a destructive edit to the
prior capability.

## 8. Capacity

Every capacity value MUST use exactly one of the following bases:

- `announced`
- `physical_construction`
- `tool_installed`
- `qualified`
- `economically_usable`

These are independent analytical states, not aliases for project lifecycle stages. Measured output
is stored as an observation metric rather than a new basis.

A capacity value MUST contain:

- metric and semantically compatible unit;
- low, base, and high values, or named quantiles and interval level;
- applicable period or target quarter;
- production-unit, facility, or project scope;
- input versus good-output basis; and
- relevant process, packaging, product, end-market, and wafer-size dimensions.

Values MUST satisfy `0 <= low <= base <= high`. Yield and utilization claims MUST be in `[0, 1]`.
Project additions and production-unit totals MUST be tagged distinctly and MUST NOT be aggregated
together. Aggregate views MUST state their attribution basis: owner economic share, operator
control, customer allocation, or full physical capacity.

## 9. Resources and constraints

Resource metrics MUST have a canonical unit family and explicit period. The contract distinguishes
electric demand from electric energy; water withdrawal, consumption, and discharge; each industrial
gas species; gross and cleanroom area; total and equipment capital expenditure; labor categories;
and absolute emissions from intensity. Currency values MUST include currency, nominal or real basis,
and price year.

A constraint value MUST include:

- controlled constraint class;
- affected entity, project, or capacity slice;
- state: `potential`, `binding`, `mitigated`, or `resolved`;
- valid period;
- probability or severity where estimated;
- schedule or capacity impact interval when estimated; and
- dependency or mitigation entity when known.

Initial classes include tools, HBM, packaging, substrates, transformers, grid, water, industrial
gases, specialist labor, export controls, and customer qualification. Orders, shipments, permits,
and job postings are evidence events; none independently proves installed or qualified capacity.

## 10. Entity resolution

Each imported source record has an immutable source key and payload hash. Organization aliases and
external identifiers retain typed auxiliary metadata without changing the scalar claim hash. Name
metadata includes name type, language, and script. Identifier metadata includes scheme, normalized
value, and jurisdiction. This metadata MUST attach only to organization text claims, and a claim
version MUST NOT be both a name and an identifier.

An entity-resolution run MUST start in `running` state. It may append only already-succeeded
ingestion runs as inputs and may append candidates only while running. Identity candidates MUST be
same-kind `organization`→`organization` or `facility`→`facility`; all cross-kind pairs are invalid.
The source record, source-backed observed entity, proposed same-kind entity, and supporting claim
versions used by a candidate MUST all exist at the run cutoff. Each candidate records normalized
comparison features, score, stable rank, resolver version, identity scope, and creation time. The
run then makes exactly one terminal
transition to `succeeded` or `failed`; its content and terminal state are sealed afterward. A
succeeded run MUST have at least one declared input.

The active decision vocabulary is `match`, `reject`, and `defer`. A decision may be appended only
after its candidate run succeeds and MUST record actor, time, reason, and decision metadata. Only a
`match` decision may create a source-record-to-canonical same-kind entity assignment. Assignments are
bitemporal, append-only, and non-overlapping for one observed entity across source refresh records at
the same world and system time. No adapter may assign an organization or facility from a name or
fuzzy score alone. Facility-to-organization roles are relationships, not identity assignments, and
require separately typed evidence and review.

Candidate target-evidence claim IDs MUST be sorted, unique, non-empty when declared, and refer to
directly evidenced claims on the proposed target entity. Every producer run for those claims
MUST have succeeded and completed before the resolution run starts, and MUST be a declared input to
that resolution run. Both repository writes and whole-database validation enforce this cutoff; a
release MUST reject legacy or directly inserted rows that violate it.

A reviewed GLEIF Level 1 import MUST use a separate immutable review artifact bound to the exact
snapshot-manifest hash and Golden Copy publication. It records reviewer and review time plus, for
every declared LEI, the exact target entity ID and stable key, supporting target claim IDs, outcome,
reason, score, rank, and assignment start date. The review MUST occur after snapshot retrieval.
Acceptance MUST re-verify snapshot and review bytes and semantics at the transaction boundary.
Source claims remain on the `gleif:lei:<LEI>` organization even after a match; the assignment is the
only canonical mapping. GLEIF addresses are organization addresses, never facility geometry, and
Level 1 registration status is never operating-status evidence.

Merge, split, and redirect operations are deferred. They MUST NOT be advertised as supported until
a reviewed workflow can apply and reverse them without hiding prior assignments or claim lineage.
Entity rows are never deleted to conceal a resolution mistake.

## 11. Forecasts, scenarios, and alerts

A forecast run pins a model artifact and version, code and configuration hashes, training data
release, input claim versions, evidence cutoff, and vintage. A forecast series identifies the
entity and capacity/resource dimensions; forecast points contain target quarter and uncertainty
quantiles. The initial horizon is twenty quarters.

Forecast input clocks MUST be no later than the declared cutoff. Forecast evaluations link later
realized claims and report error, bias, and interval coverage. Aggregates are derived from unit-level
series with a declared attribution basis.

A scenario pins a base forecast and explicit assumptions. Assumptions are either evidence-backed or
labelled hypothetical. Scenario outputs MUST NOT enter the canonical layer.

Alerts are append-only events with a deterministic fingerprint, rule or model version, target,
prior and current claim or forecast references, severity, confidence, and evidence lineage. Alert
state transitions support acknowledgement, resolution, and retraction. Deduplication and hysteresis
are mandatory before automated delivery.

Investment signals are a later analytical product. Each signal MUST identify the issuer or security,
direction, driver, horizon, magnitude range, confidence, and exact forecast, scenario, alert, and
claim lineage. Narrative text is explanatory output, not evidence.

## 12. Runs, rights, and release manifests

Ingestion is idempotent by source, source key, and content hash. Every run records code version,
environment or container, configuration, inputs, start and finish times, and outcome. Restricted
bytes and derived artifacts remain in rights-controlled storage.

Every input source document MUST have an immutable `ingestion_run_documents` link to its run,
including inputs that produce zero source records. A run's primary document, every document used by
a source record, and every document ID declared in adapter parameters MUST be linked explicitly and
must belong to the same source. Multi-document adapters MUST preserve role labels that distinguish
index, detail, archive, derivative, and other input purposes. Creating a run and its initial document
links MUST be atomic.

Adapters SHOULD preserve repeated semantic observations as immutable source records. They MUST NOT
mint a claim version when the value, claim kind, method, confidence, notes, and dependency set remain
unchanged. Releases SHOULD expose an observation index so auditors can see reaffirmations that did
not revise a claim.

Source-document identity represents acquired bytes, not importer code. A later importer MAY reuse the
same document at a later acceptance time. Filter configuration, derivative hashes, retention status,
rights review, and snapshot identifiers belong to the ingestion run and MUST be exported for replay.
Failed or running ingestion runs are not accepted knowledge and MUST NOT contribute release inputs or
source observations.

A release manifest MUST pin:

- schema and code version;
- world-time and knowledge-time cutoffs;
- source input, configuration, model, and output hashes;
- entity-resolution version;
- row counts and stable sort rules;
- license, attribution, access, and redaction decisions;
- source checkpoint and coverage-age summary; and
- known limitations.

Public exports MUST omit restricted evidence while preserving permitted locators and hashes. A
lineage audit MUST be able to traverse every published field to evidence or an explicit scenario
assumption. The same pinned inputs MUST produce byte-identical canonical release files.
All database-derived files in one release MUST come from one consistent database snapshot. Historical
exports MUST mask a `superseded_at` value that occurs after the release knowledge cutoff.
