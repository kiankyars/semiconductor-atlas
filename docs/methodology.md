# Semiconductor Atlas methodology

Status: v0.1 operating specification, 2026-07-20.

## Objective and scope

Semiconductor Atlas is an evidence-first registry of semiconductor sites, facilities, production
units, and expansion projects. Its long-term objective is to detect construction, technology
transitions, production ramps, bottlenecks, delays, redesigns, and cancellations earlier and more
accurately than announcement-only datasets.

Version 0.1 is not a global census and does not claim competitor parity. It proves a narrower
contract: every published field is an atomic, dated claim with evidence or explicit derivation;
facts and estimates remain distinguishable; revisions are reproducible; and unknown values remain
unknown. Coverage is reported by geography, facility activity, source family, and observation age.
A large row count is not evidence of completeness.

The product has two visibility layers:

1. **Canonical registry:** entities and claims that pass identity, provenance, and review gates.
2. **Leads:** weakly evidenced, unresolved, or potentially duplicate candidates. Leads are useful
   for investigation but are excluded from capacity totals unless a release says otherwise.

A missing signal is not negative evidence unless the relevant source, geography, and time window
were actually checked. Source checkpoints therefore record query scope, cursor, run, result count,
and last successful check.

## Units of analysis

Stable entity identifiers survive name, owner, geometry, and status changes. The registry keeps
these objects separate:

- **Site:** a geographically coherent campus or parcel group.
- **Facility:** an operating establishment at a site, such as a wafer fab, packaging plant, test
  plant, substrate plant, mask shop, or materials plant.
- **Building:** a physical shell or utility structure.
- **Production unit:** the smallest practical capacity-accounting unit, such as a fab module,
  cleanroom, wafer line, packaging line, test line, or materials line.
- **Project:** a greenfield build, expansion, retrofit, or technology conversion that targets one
  or more sites, facilities, buildings, or production units.
- **Organization:** an owner, operator, joint-venture partner, customer, contractor, supplier,
  financier, regulator, or utility.
- **Infrastructure asset:** a substation, water or wastewater plant, gas system, generation asset,
  or other external dependency when it must be tracked independently.

Co-location is not identity. Two facilities on one site remain separate when they have distinct
operators, activities, qualifications, or capacity accounting. A project is not itself installed
capacity, and a company announcement is not a project unless a real target and scope can be
resolved.

## Facts, estimates, and evidence

The registry stores atomic claims rather than evidence-decorated facility snapshots. A name,
boundary, owner relationship, process capability, completion date, and capacity value may come
from different evidence and therefore must be separate claims.

Claim kinds have explicit epistemic meaning:

- **Source statement:** the source reported a value. This establishes what the source said, not
  that the statement is true.
- **Direct observation:** a dated measurement or visible event, such as a permit issuance,
  metered quantity, shipment, or imagery-derived construction change.
- **Reconciled fact:** the current canonical interpretation of one or more source claims.
- **Derived estimate:** a calculated or modelled value with a method, inputs, and uncertainty.

Company guidance about future output remains a source statement with the `announced` capacity
basis and a target period. It never becomes the system forecast merely because it is precise.
System forecasts and scenarios are separate, versioned products.

Evidence has three levels:

1. a source and source family;
2. an immutable retrieved document or dataset object, identified by URL or source key and content
   hash; and
3. an exact evidence fragment, such as a page, section, table cell, timestamp, image footprint, or
   legally retainable excerpt.

A claim may have many supporting, refuting, or contextual fragments, and one fragment may support
many claims. A press release, its wire copy, and articles that merely repeat it belong to one source
family. Confidence depends on independent evidence, not URL count. Derived claims carry links to
all upstream claim versions and the transformation or model run that produced them.

## Temporal and revision semantics

Canonical claims are bitemporal:

- `valid_from` and `valid_to` describe when the claim is believed to apply in the world;
- `system_from` and `system_to` describe when that version was present in the database. The v0.1
  schema names these columns `recorded_at` and `superseded_at`.

Ranges are half-open. A historical query must specify both a world time and a knowledge time. Late
evidence about an old event, a correction for the same valid date, and a revised future target each
create a new version; they do not erase the earlier database state. Publication, acquisition,
retrieval, observation, and extraction times are retained separately.

A verified CLI NIST snapshot is treated as complete only when its manifest explicitly declares the
bounded NIST scope complete, its declared index/detail counts equal the archived inputs, and the
detail inputs exactly cover the CHIPS Program Office links parsed from those index pages. If a field
or record disappears in a later complete snapshot, the earlier claim's `superseded_at` is closed and
its history remains available. Undeclared, low-level, or partial imports default to incremental mode
and never treat absence as a retraction. Repeated source fields are keyed by semantic label, scope,
entity, or metric rather than array position, so a reordered page cannot silently change series
meaning.

### EPA FRS candidate semantics

The EPA Facility Registry Service input is the official monthly National Single File public
archive. The bounded semiconductor lead rule is an exact match on NAICS `334413` or SIC `3674`.
Adjacent NAICS classes, including `325180`, `334418`, and `334419`, are deliberately excluded. The
result is a low-precision, potentially stale U.S. registry candidate set with no global recall
denominator. A matching classification establishes only that an FRS row passed this discovery
filter; it does not establish semiconductor activity, operation, lifecycle state, capacity, or
canonical identity. Its `1.0` confidence records deterministic rule membership, not a 100% estimate
that the row is a real, current, operating semiconductor facility.

The source's latitude and longitude fields remain raw NAD83 scalars. The exporter omits FRS geometry
because the adapter performs no CRS transform. The official monthly endpoint is mutable and supplies
no publisher checksum. Each source snapshot copies the exact ZIP to a content-addressed path and
records the archive and CSV hashes, byte counts, CRC, retrieval timestamp basis, official data-as-of
source, and full-row denominator. Verification rescans the ZIP and regenerates the filtered JSONL,
which permits audits of selected and excluded rows from the immutable snapshot.

FRS corrections create immutable source records and claim revisions. Registry ID merges,
reassignments, and later absence can change source-to-entity resolution, but do not close a facility,
cancel a project, or retract operating status. A later complete snapshot of the same filter closes
prior FRS candidate claim intervals that are no longer selected while preserving their history. The
source still cannot distinguish `no_longer_matches_filter` from `absent_from_archive`; neither state
is a closure signal. Registry ID redirects require separate official merge evidence.

FRS runs preserve archive acquisition in `source_documents.retrieved_at` and database acceptance in
`ingestion_runs.started_at`. Claim `recorded_at` and `superseded_at` use the acceptance clock. The CLI
requires `--accepted-at` for a pinned build or `--accept-now` for a live refresh. Later rows with the
same semantic proposition remain source observations and reuse the prior claim ID. The release
exports those reaffirmations in `source_observations.jsonl`.
An importer upgrade can reprocess the same acquired ZIP without rewriting its source document.
Run-level provenance records the importer version, derivative digest, filter, completeness, retained
raw locator, rights decision, and source-snapshot manifest digest. Partial inputs can update explicit
values but never retract an omitted field or unseen facility.

### Taiwan MOENV candidate semantics

Taiwan MOENV `EMS_S_01` is the first accepted non-U.S. source-native facility registry. The
reproducibility anchor is the retained official full-package ZIP, its publisher checksum, and a
deterministic derivative restricted to exact industry group `261` and exact four-digit codes `2611`,
`2612`, and `2613` with their official labels. The broad group API filter is transport only. The
scanner validates every one of the package's 26 fields before selecting rows and preserves
case-sensitive control numbers, leading zeroes, empty strings, true nulls, and every conflicting
source variant.

An MOENV entity is a source-native environmental-registry facility, not a resolved site, fab,
operator, or owner. Five raw environmental-control flags support a mechanical
`currently_environmentally_regulated` derivation; that value is not operating status or production
evidence. Release dates are release from a regulatory category, not facility closure. Exact industry
codes support discovery classifications whose confidence measures deterministic rule execution, not
the probability of current semiconductor manufacturing. No MOENV row supplies process, wafer size,
technology, yield, utilization, capacity, or ramp evidence.

Valid WGS84 strings present identically in all retained variants may be converted deterministically
to a source-point geometry. Zero, malformed, out-of-range, missing, partial, or conflicting pairs
remain without geometry. Even a valid point is not a parcel, building, cleanroom, or fab boundary.
Business and factory-registration identifiers remain resolution evidence; they never trigger an
unreviewed merge.

The package's official Asia/Taipei update date supplies source validity, while retrieval and database
acceptance remain separate UTC clocks. A later verified-complete same-filter snapshot may close only
the prior source-assertion interval. Partial input never closes omissions, and no absence becomes a
real-world closure, inactivity, cancellation, or production-stop claim.

### Taiwan registered-factory and facility-identity semantics

The national registered-factory publication supplies an independent facility population. Its
reproducibility anchor is the retained official ZIP and a deterministic derivative restricted to the
exact principal-product token `261半導體`. The full national CSV is scanned before filtering. The
responsible person's name is present in the licensed raw source but omitted from derived data and
public outputs because it contributes no necessary facility evidence.

Each selected eight-character factory-registration number creates one source-native facility.
Names, addresses, business numbers, organization type, raw approval dates, registration status, and
industry/product tokens remain source statements. `生產中` means only that the administrative
factory registration appears in the publisher's active roster. It is not direct observation of
production and supports no output, utilization, capacity, qualification, or current-operation
estimate. Because the roster excludes other administrative states, absence is not closure evidence.

The exact `261半導體` source-token claim is the sole dependency of the deterministic semiconductor
candidate classification. The classification adds no facility subtype, actual-operation, output,
lifecycle, ownership, capacity, utilization, or yield assertion. The HTTP `Last-Modified` timestamp,
archive retrieval timestamp, and database acceptance timestamp remain separate clocks. A verified
complete same-filter refresh may close only prior source-assertion intervals; partial-refresh
omissions remain open, and neither case turns absence into real-world closure evidence.

Facility identity between MOENV and this registry is separately reviewed. Only documented legacy
certificate formats are normalized, and the normalized value must exactly match the official
factory-registration number. Review then checks address compatibility and retains all name and
business-number discrepancies. A match says that two source-native records refer to the same
registered factory/site record; it does not say that the same organization owned or operated that
factory throughout history. Cross-kind identity is prohibited. Company, branch, ownership, and
operator work remains a distinct relationship-resolution stage. The accepted 2026-07-20 review had
143 candidates: 129 exact identifiers and 14 documented legacy forms. It created 142 assignments and
deferred the one name-and-UBN succession conflict. Every evidence value binds its exact source record.
Selected-run state excludes later refreshes, and a stale correction can only retract or identically
reaffirm an assignment already created from the same reviewed artifact.

### Taiwan MOF active tax-registration semantics

The daily `BGMOPEN1` source is a population of active tax registrations, not a legal-company or
ownership register. The bounded snapshot scans the complete archive but publishes only exact UBNs
from an allowlist re-derived from the accepted MOENV and factory snapshots. The accepted 2026-07-20
snapshot contains 466 allowlisted identifiers, 390 exact matches, and 76 explicit misses across
1,709,795 business rows. Missing active rows do not invalidate historical identifiers or prove
closure.

Each imported match is a source-native organization tax unit. A branch remains distinct from
its stated head office; the head-office UBN is context, not automatic parentage. Names, addresses,
organization types, raw dates, and industry activities remain source statements. Capital and
invoice-use fields are deliberately omitted from the public derivative.

Facility references are a separate complete-review layer. The accepted 2026-07-20 pass froze the
selected MOENV, registered-factory, and MOF runs at one knowledge cutoff, then evaluated 944 exact
same-time UBN candidates. All were accepted only as `registered_tax_unit_reference` claims across 935
facility subjects and 390 tax units. Nine facility subjects have two current source references; both
remain visible because the evidence does not select one legal or operating relationship. A reference
never creates facility or legal-person identity, ownership, parentage, operator, activity, production,
lifecycle, or capacity evidence. The release exports the full decision and claim-action ledger, and
coverage counts only the exact reviewed method rather than every relationship sharing its predicate.

Forecasts use an additional pair of clocks: the forecast vintage and the target quarter. Encoding
a forecast only as a future valid-time claim would lose what was knowable at each vintage and is
therefore prohibited.

## Lifecycle and capacity

Physical project lifecycle is tracked independently from capacity. Initial stages are:

`lead -> announced -> site_control -> permitting -> permitted -> site_preparation -> civil_works -> shell -> cleanroom_fitout -> utilities_ready -> tools_installing -> commissioning -> customer_qualification -> ramping -> complete`

Side states are `paused`, `cancelled`, `redesigned`, `repurposed`, `decommissioned`, and `unknown`.
An announcement, land purchase, or permit alone is not physical construction. Satellite imagery can
support visible stages but cannot by itself prove tool installation, qualification, process node,
yield, or production.

Every capacity claim uses exactly one of these five bases:

1. **announced** — stated intention or nameplate target;
2. **physical_construction** — capacity associated with work demonstrably being built;
3. **tool_installed** — capacity supported by installed production equipment;
4. **qualified** — capacity that has passed the relevant process or customer qualification; and
5. **economically_usable** — realistic output at expected yield, utilization, and commercial
   constraints.

Actual measured output may be stored as a separate observation metric, not a sixth capacity basis.
The five bases must never be silently collapsed. Project capacity additions and production-unit
totals remain distinct so they cannot be double counted.

Capacity metrics include their semantic unit and scope: wafer starts, processed wafers, good die,
packaged units, panels, substrates, masks, or material output per period. Each value has low, base,
and high values or a named probability interval, plus process, packaging, product, wafer-size, and
time dimensions where applicable. Yield and utilization are separate bounded claims rather than
hidden assumptions.

## Capabilities, resources, and constraints

Facility activity is multi-valued. Front-end fabrication, advanced packaging, assembly, test,
substrates, photomasks, and materials can coexist at one site. Product families such as logic,
foundry, memory, analog, power, compound semiconductor, photonics, and image sensors are recorded
separately from activity.

A capability claim can name a process family, vendor-marketed label, optional nominal node and its
measurement basis, wafer size, product scope, packaging technology, and readiness stage. Marketing
node labels are not treated as directly comparable physical dimensions.

Resource claims distinguish quantities that are often incorrectly merged:

- electric demand from energy consumption;
- water withdrawal from consumption and discharge;
- gas species and delivery form;
- gross floor area from cleanroom area;
- total capital expenditure from equipment expenditure;
- direct labor from construction or contractor labor; and
- absolute emissions from carbon intensity.

Currency claims include currency, nominal or real basis, and price year. Resource values use dated
ranges and explicit facility, unit, or project scope.

Constraints are typed claims, not free-text notes. They record the affected entity and capacity
slice, constraint class, status, probability or severity, expected schedule or capacity impact,
dependency entity, and mitigation. Initial classes include EUV and other critical tools, advanced
packaging, HBM, substrates, transformers, grid, water, industrial gases, specialist labor, export
controls, and customer qualification. A tool order or shipment is evidence about progress; it does
not automatically equal installed capacity.

## Confidence and reconciliation

Source reliability, extraction confidence, entity-match confidence, claim confidence, and numeric
uncertainty are different quantities and are retained separately. Claim confidence is a calibrated
probability that a proposition or category is correct. Numeric uncertainty belongs in a stated
interval with a coverage level. A method and calibration version accompany both.

Conflicting source claims remain queryable. Reconciliation creates a new canonical claim with a
reasoning trail; it does not delete alternatives. Models account for source-family correlation and
must not average confidence scores or count repetitions as independent confirmation. `unknown` is
preferred to an unsupported point estimate.

The v0.1 NIST seed intentionally retains repeated index and detail statements as separate source
claims; it does not yet publish a general reconciled-fact layer for those fields. Raw source claims
therefore MUST NOT be counted or summed as independent facts. Monetary numeric predicates encode
scope explicitly: `site_amount_usd`, `project_amount_usd`, and `program_amount_usd`. A repeated
multi-site program total is emitted once on a separate award-program entity, while site allocations
remain on their page projects. Program totals and allocations are not additive without a reviewed
allocation model. Program identity is anchored to a connected set of NIST award pages and reused
through historical page-to-program relationships; an unconnected later award to the same recipient
gets a distinct entity, and ambiguous historical merges fail closed.

## Entity resolution

Source records first enter as immutable records with external keys and payload hashes. Candidate
matches use names and aliases, coordinates and boundary overlap, parcel or address, organizations,
dates, activities, products, and technologies. Geography alone cannot merge co-located facilities.

Every match, new-entity, merge, split, or reject decision records its candidate features, resolver
version, score, reviewer or model, time, and reason. Automatic high-confidence matching may be
introduced only after blind evaluation. Merges retain redirects from old identifiers; splits and
reassignments preserve the prior decision history.

## Release gates

A canonical v0.1 release requires:

- 100% field-level lineage for published claims;
- no invalid entity references, units, temporal ranges, or lineage cycles;
- reproducible output for a pinned world time and knowledge time;
- a content-hashed input and output manifest with schema, code, configuration, and model versions;
- source license, access class, attribution, and redaction checks;
- explicit source coverage, observation age, adapter failures, and known blind spots;
- a duplicate and incorrect-merge audit; and
- a representative random-claim audit against the cited evidence fragment.

Forecasts are not published until time-split backtests report error, bias, calibration, and interval
coverage by geography, company, technology, and lifecycle stage. Alerts require deterministic
deduplication, hysteresis, and retraction tests. Investment conclusions must link to exact forecast
or scenario outputs and underlying claims; narrative alone is not an auditable result.

## Rights, safety, and public output

Public visibility is separate from internal lawful use. Restricted source bytes and imagery stay in
an access-controlled store; public records may expose only hashes, locators, derived values, and
permitted excerpts. Every adapter follows the source-specific access method and rate limits in the
source registry.

Public outputs omit personal contact data, employee-level tracking, access-control layouts,
security procedures, exploitable equipment details, and unlawfully acquired material. Location or
geometry is coarsened when law, contractual terms, or safety requires it. Competitive products are
benchmarks or licensed inputs, never scrape targets.
