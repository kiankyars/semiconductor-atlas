# Semiconductor Atlas

Semiconductor Atlas is an evidence-first registry for semiconductor organizations, sites,
facilities, production units, and projects. It stores each published field as an atomic,
bitemporal claim linked directly to archived evidence or to an explicit derivation chain.

Version 0.1 is a bounded, reproducible seed. It is **not** a global facility census, proof that an
announced project is operating, a comparison with commercial databases, a calibrated forecast, or
investment advice. Unknown values remain unknown.

## Capacity contract

Every capacity claim uses exactly one basis:

1. `announced`
2. `physical_construction`
3. `tool_installed`
4. `qualified`
5. `economically_usable`

The bases are never collapsed. Measured output is a separate observation metric, not a sixth basis.

## Current open-source seed

`source_snapshots/2026-07-17-open-seed` contains 33 content-hashed inputs retrieved at
`2026-07-18T01:54:47Z`: four NIST CHIPS awards index pages, 28 NIST project detail pages, and one
bounded OpenStreetMap Overpass result.

NIST records preserve the government's award disclosures, qualifiers, scope, and stated project
details. They do not establish physical progress beyond what the source says. OpenStreetMap records
are candidate leads only; the importer does not promote them to operating facilities or capacity.
OSM-derived releases retain ODbL attribution.

NIST site allocations, single-project totals, and repeated multi-site program totals use distinct
monetary predicates. Shared totals are emitted once on durable award-program entities. Do not sum
program totals with site allocations, and do not treat repeated raw source statements as a
reconciled fact.

### EPA FRS candidate contract

The EPA Facility Registry Service adapter uses the official monthly National Single File archive
and admits only rows with exact NAICS `334413` or exact SIC `3674`. Broader adjacent material,
printed-circuit assembly, and electronic-component codes are excluded. Matching rows are U.S.
registry candidate leads only: they do not establish semiconductor activity, operation, lifecycle
state, or capacity, and there is no global recall denominator. Registry absence, reassignment, or a
Registry ID merge is not evidence of closure, cancellation, or inactivity.
The classification claim has confidence `1.0` only because exact filter membership is deterministic;
it is not a probability that the row represents an operating semiconductor facility.

FRS latitude and longitude remain raw NAD83 source scalars. They are not GeoJSON geometry, and the
adapter performs no CRS transform or FRS geometry import. The official monthly download URL is
mutable and does not publish a content checksum. Each snapshot copies the exact ZIP to
`raw/sha256/<archive-sha256>.zip`, records its archive and CSV digests, and regenerates the selected
39-field rows from that retained object during verification. The manifest also records retrieval
metadata, the official data-as-of source, filter version, and full-row denominator. Public releases
use the required EPA credit, make no warranty, and do not use the EPA seal or logo.

### EEA Industrial Reporting candidate contract

The accepted EEA edition-16 adapter scans all 99,275 production facilities reported in the pinned
2.03 GB relational package. Exact raw NACE `26.11` or bounded explicit facility/site name terms
produce 108 manual-review leads. That reported population is not a European industrial or
semiconductor census, and filter membership does not establish semiconductor activity, operation,
facility type, output, or capacity. Exact INSPIRE IDs remain case-sensitive; historical case and
whitespace drift is audited but never repaired.

The immutable snapshot is
`source_snapshots/2026-07-20-eea-industrial-v16-semiconductor-candidates`. It retains the raw ACCDB,
all pinned extraction evidence, independent identical extraction metadata, official metadata and
catalogue responses, and the replayed derivative. Confidential fields fail closed and unnecessary
free text is omitted. Raw coordinate scalars remain non-geometry values until correspondence with
the EPSG:4326 spatial companion is verified. The source publishes no production-volume rows in v16,
so the adapter emits no capacity evidence.

The retained review queue binds all 108 leads to the exact snapshot and candidate hashes. Its three
priorities order work; they are not classifications or decisions. A complete review must decide
every candidate as `accept_in_scope`, `defer`, or `reject_out_of_scope`. Accept and reject decisions
require external HTTPS evidence acquired no later than the declared review cutoff. The importer
creates source-native facilities and a narrow scalar claim set only for accepted candidates. It
does not promote review outcomes, coordinates, status, ownership, output, or capacity. The
[September 7 bounded admission](docs/eea_scope_admission_2026-09-07.md) now records 15 in-scope
decisions and 93 deferrals, with no final exclusions. Its isolated schema-5 database contains
15 source-local facility records and 132 exact EEA scalar statements; the frozen parent is unchanged.
Claim-effective dates and uncalibrated confidence remain null, not the publication date or `1.0`.

The [September 7 full-population scope research](review_plans/2026-09-07-eea-industrial-v16-scope-research-v2.json)
records research for all 108 candidates: 38 proposed in-scope, 23 proposed out-of-scope, and 47
deferred with explicit missing-evidence or scope reasons. The original
[31-candidate batch](review_plans/2026-09-07-eea-industrial-v16-scope-research-v1.json) and its entries
remain unchanged. Research completion is not final adjudication: these are historical
facility-scope proposals, not accepted claims, new unique fabs, AI-critical additions, or
operating-capacity evidence. Ordinary PCB, connector and passive-component exclusions remain
provisional process-specific judgments; chip embedding, packaging and dedicated IC substrates
need separate treatment. External bodies were web-read, not exact-byte archived; failed/search-only
leads and pending PDF layout checks remain explicit. Neither research format is importable as an
adjudication ledger. The separate complete admission review preserves all decisive layout/site/scope
holds as deferrals. The original v1/v2 importer still allows one immutable review per database.
The separate [append-only scope-revision workflow](docs/eea_scope_revisions.md) now supports later
complete decisions, preserves original source statements, and provides cutoff-scoped export and
parent-bound restoration. The real Newport evidence correction added review history without new
claims. Different source editions, canonical identity and the 93 evidence/scope holds remain open.

### Taiwan MOENV candidate contract

The first accepted non-U.S. facility source is Taiwan Ministry of Environment dataset `EMS_S_01`.
The adapter scans the retained full national JSON package and locally admits only exact industry
codes `2611`, `2612`, or `2613` under group `261`, with the exact official Chinese label. It keeps
all 724 case-sensitive source control numbers and all 798 distinct source variants from the pinned
2026-07-20 package. Repeated identical payloads collapse; conflicting variants remain parallel
source statements.

These are environmental-registry facility candidates, not a complete Taiwan semiconductor census.
The five control flags establish only environmental-regulation membership, never operation,
production, ownership, lifecycle, capacity, or technology. The 618 valid invariant WGS84 pairs may
be released as source-reported facility points, not parcel or building boundaries. Business and
factory-registration identifiers are resolution evidence only; this import creates no organization
assignment. A complete later same-filter snapshot may close a prior source-assertion interval, but
absence cannot assert real-world closure or inactivity.

The immutable snapshot is
`source_snapshots/2026-07-20-taiwan-moenv-ems-s-01-semiconductor`. It retains the official ZIP at a
content-addressed path, validates the publisher MD5, regenerates the exact-code JSONL, records the
sanitized POST request and separate publisher/retrieval clocks, and carries the required Taiwan Open
Government Data License 1.0 attribution.

### Taiwan registered-factory identity contract

The independent national registered-factory publication is scanned in full and filtered only on the
exact line-delimited principal-product token `261半導體`. Its accepted snapshot contains 553
source-native registered-factory facilities and omits the source responsible-person field from its
privacy-minimized derivative, database records, claims, and release. The raw publisher value
`生產中` remains an administrative registration status only; it establishes neither physical
operation nor output, ownership, lifecycle, capacity, utilization, or yield.

Schema version 4 supports same-kind facility identity as well as organization identity. A strict
candidate artifact bound 143 MOENV records to exact official factory-registration evidence: 129
numbers were already exact and 14 used only the documented legacy-certificate normalization. Manual
review accepted 142 matches and deferred `J5904800` because its legacy number and civic address point
to succession while the names and UBNs conflict. Every evidence value retains its own exact source
record. Later stale evidence can only retract or identically reaffirm an existing assignment; it can
never create a relationship from stale bytes.

The accepted schema-v4 database is
`artifacts/2026-07-20-open-seed-taiwan-factory-identity.sqlite` (SHA-256
`8bcd2e840281f6646a967e3ef9ae6de798028e9245af9cc4fba9df6ffac99011`). The deterministic release is
`releases/2026-07-20-open-seed-taiwan-factory-identity` and its final manifest SHA-256 is
`bbf652a306f92e94a459bc2e16eb95b3e6039e2bcbf08756866141e1bb05a694`.

### Taiwan MOF organization evidence boundary

The pinned Ministry of Finance `BGMOPEN1` snapshot looks up an exact 466-UBN allowlist re-derived
from the two accepted Taiwan facility snapshots. It scans all 1,709,795 current business rows and
retains 390 exact active tax-registration matches while recording 76 misses. A tax registration may
be a company, branch, sole proprietorship, or other tax unit; it is not automatically a legal parent,
owner, facility operator, or proof of activity. The immutable snapshot is
`source_snapshots/2026-07-20-taiwan-mof-bgmopen1-semiconductor-organizations`, with manifest SHA-256
`c14892bffca9deccf5a5720aab19b03daa7b8aa14fc49d77f9d6cc1dac1541ce`.

The accepted source-native import created 390 tax-unit organizations and 3,241 directly evidenced
claims. Its database is `artifacts/2026-07-20-open-seed-taiwan-mof-organizations.sqlite` (SHA-256
`bcb39fb04a927484c1e74417f0952370bc7f8db5577bc463bf7dd46f6a9ecab9`), and its byte-reproducible
release is `releases/2026-07-20-open-seed-taiwan-mof-organizations`, with manifest SHA-256
`caf69a04edcd1a314d458d0844f123ed8d8319cb9e7c28308807b6bb82b02c29`.

### Taiwan facility-to-tax-unit reference boundary

A separate complete review bound the exact MOENV, registered-factory, and MOF ingestion runs and
evaluated 944 same-time exact-UBN candidates. All 944 were accepted only as
`registered_tax_unit_reference` claims, covering 935 facility subjects and all 390 imported tax
units. Nine facility subjects retain two conflicting current source references; neither value was
silently selected. These relationships assert no facility or legal-person identity, ownership,
parentage, operator, activity, lifecycle, output, or capacity.

The accepted database is
`artifacts/2026-07-20-open-seed-taiwan-tax-relationships.sqlite` (SHA-256
`c6083299cae65a96890dff831ca201566de82dae7532ca555c65391f7ee21ca9`). The deterministic 24-managed-file
release is `releases/2026-07-20-open-seed-taiwan-tax-relationships`; its manifest SHA-256 is
`6d7bca2cd392d1372774ae227ae9cd6e3a665454e2c25084b357c9b58691f4ce`.
`reviewed_relationship_runs.jsonl` exports the exact candidate and review hashes, selected source
bindings, every decision, and created, reaffirmed, and superseded claim lineage. See
`docs/taiwan_mof_relationship_gate_2026-07-20.md`.

### Organization identity boundary

Schema version 3 introduced an append-only organization-resolution ledger and an explicit ledger of every
source document used by an ingestion run, including inputs that produce zero rows. Resolution runs
are sealed before a reviewer can record `match`, `reject`, or `defer`; only a reviewed `match` may
create a bitemporal source-to-canonical organization assignment. Names and fuzzy scores create
candidates only.

The GLEIF Level 1 acquisition utility archives exact API responses for a strict sorted LEI
allowlist, requires one Golden Copy publication, and regenerates a deterministic link-free
derivative during offline verification. Allowlist membership is not an organization assignment or a
review decision. A separately reviewed manifest binds each accepted record to its snapshot,
candidate target, source-backed target evidence, outcome, reviewer, and review time. Acceptance
re-verifies both artifacts before committing.

The accepted pilot is deliberately one record: TSMC Arizona Corporation, LEI
`2549005GOBWLCSY63Q97`, from Golden Copy `2026-07-19T16:00:00Z`. Its 21 GLEIF claims remain on the
source-native organization `gleif:lei:2549005GOBWLCSY63Q97`; one reviewed assignment connects that
record to the existing NIST organization without merging entities. Legal and headquarters
addresses are organization attributes, not facility coordinates. LEI registration status does not
prove facility ownership, operation, or production. The accepted database and schema-v3 release are
`artifacts/2026-07-19-open-seed-gleif-pilot.sqlite` and
`releases/2026-07-19-open-seed-gleif-pilot`.

## Use from a source checkout

Python 3.11 or newer is required. The package has no runtime dependencies.

```sh
python -m pip install -e .
```

The Python distribution installs the `semiconductor_atlas` package, its database migrations, and the
`semiconductor-atlas` command. It does not install the repository's acquisition scripts, web
generator, documentation, review plans, or ignored local data. Use a source checkout when working
with `scripts/`, `web/`, `docs/`, or `review_plans/`. Historical `source_snapshots/`, `artifacts/`,
and `releases/` are local-only payloads documented in [Local data](LOCAL_DATA.md); they are not in a
clean clone or a Python distribution.

Build a source distribution and wheel from a clean checkout with:

```sh
python -m pip install build
python -m build
```

## Reproduce the retained seed (local-only)

The exact historical replay below requires the ignored snapshots listed in
[Local data](LOCAL_DATA.md) and is not runnable from a clean clone. The public acquisition routes in
[the source registry](docs/source_registry.md), together with the fetcher examples in the next
section, can create new dated snapshots from currently available public sources. They do not promise
byte-identical recovery of a historical publisher payload.

Ingestion is offline and deterministic. It verifies every archived input against the snapshot
manifest before parsing it. A bounded NIST scope is accepted as complete only when its declared
index/detail counts match the archived inputs and those detail inputs exactly cover the CHIPS
Program Office links in the archived index pages. The CLI then retires claims from earlier NIST
snapshots that disappear, while their history and evidence remain exportable. Undeclared, partial,
and low-level imports default to incremental mode; absence is never treated as a retraction unless
`snapshot_is_complete=True` is explicit.

```sh
semiconductor-atlas ingest-snapshot \
  --database atlas.sqlite \
  --snapshot source_snapshots/2026-07-17-open-seed \
  --as-of 2026-07-17

semiconductor-atlas ingest-frs-snapshot \
  --database atlas.sqlite \
  --snapshot source_snapshots/2026-07-19-epa-frs-semiconductor-candidates \
  --as-of 2026-07-01 \
  --accepted-at 2026-07-20T03:08:57Z

semiconductor-atlas ingest-gleif-snapshot \
  --database atlas.sqlite \
  --snapshot source_snapshots/2026-07-19-gleif-level-1-tsmc-arizona \
  --review-plan review_plans/2026-07-19-gleif-tsmc-arizona.json \
  --accepted-at 2026-07-20T06:38:00Z

semiconductor-atlas ingest-moenv-snapshot \
  --database atlas.sqlite \
  --snapshot source_snapshots/2026-07-20-taiwan-moenv-ems-s-01-semiconductor \
  --accepted-at 2026-07-20T07:30:00Z

semiconductor-atlas ingest-taiwan-factory-snapshot \
  --database atlas.sqlite \
  --snapshot source_snapshots/2026-07-20-taiwan-ida-registered-factories-semiconductor \
  --accepted-at 2026-07-20T08:30:00Z

python -m semiconductor_atlas.taiwan_facility_identity propose \
  --database atlas.sqlite \
  --moenv-snapshot source_snapshots/2026-07-20-taiwan-moenv-ems-s-01-semiconductor \
  --factory-snapshot source_snapshots/2026-07-20-taiwan-ida-registered-factories-semiconductor \
  --moenv-ingestion-run-id 9be1e187-f5f7-5dbf-b8cd-3ed839f9182f \
  --factory-ingestion-run-id ceab537a-1ac8-5a38-82e9-6ac6b50b6e29 \
  --knowledge-cutoff-at 2026-07-20T08:31:00Z \
  --output review_plans/2026-07-20-taiwan-moenv-factory-candidates.json

python -m semiconductor_atlas.taiwan_facility_identity accept \
  --database atlas.sqlite \
  --moenv-snapshot source_snapshots/2026-07-20-taiwan-moenv-ems-s-01-semiconductor \
  --factory-snapshot source_snapshots/2026-07-20-taiwan-ida-registered-factories-semiconductor \
  --candidates review_plans/2026-07-20-taiwan-moenv-factory-candidates.json \
  --review review_plans/2026-07-20-taiwan-moenv-factory-review.json \
  --accepted-at 2026-07-20T09:09:00Z

semiconductor-atlas ingest-taiwan-mof-snapshot \
  --database atlas.sqlite \
  --snapshot source_snapshots/2026-07-20-taiwan-mof-bgmopen1-semiconductor-organizations \
  --moenv-snapshot source_snapshots/2026-07-20-taiwan-moenv-ems-s-01-semiconductor \
  --factory-snapshot source_snapshots/2026-07-20-taiwan-ida-registered-factories-semiconductor \
  --accepted-at 2026-07-20T09:44:32Z

python -m semiconductor_atlas.taiwan_tax_relationship propose \
  --database atlas.sqlite \
  --moenv-snapshot source_snapshots/2026-07-20-taiwan-moenv-ems-s-01-semiconductor \
  --factory-snapshot source_snapshots/2026-07-20-taiwan-ida-registered-factories-semiconductor \
  --mof-snapshot source_snapshots/2026-07-20-taiwan-mof-bgmopen1-semiconductor-organizations \
  --moenv-ingestion-run-id 9be1e187-f5f7-5dbf-b8cd-3ed839f9182f \
  --factory-ingestion-run-id ceab537a-1ac8-5a38-82e9-6ac6b50b6e29 \
  --mof-ingestion-run-id 9171c9df-7772-5d25-a9c7-bb869570dad6 \
  --knowledge-cutoff-at 2026-07-20T09:44:33Z \
  --output review_plans/2026-07-20-taiwan-facility-tax-unit-candidates.json

python -m semiconductor_atlas.taiwan_tax_relationship accept \
  --database atlas.sqlite \
  --moenv-snapshot source_snapshots/2026-07-20-taiwan-moenv-ems-s-01-semiconductor \
  --factory-snapshot source_snapshots/2026-07-20-taiwan-ida-registered-factories-semiconductor \
  --mof-snapshot source_snapshots/2026-07-20-taiwan-mof-bgmopen1-semiconductor-organizations \
  --candidates review_plans/2026-07-20-taiwan-facility-tax-unit-candidates.json \
  --review review_plans/2026-07-20-taiwan-facility-tax-unit-review.json \
  --accepted-at 2026-07-20T09:54:43Z

semiconductor-atlas validate --database atlas.sqlite

semiconductor-atlas summary \
  --database atlas.sqlite \
  --as-of 2026-07-20 \
  --recorded-at 2026-07-20T09:54:46Z
```

## Acquire new public snapshots

To acquire a new bounded snapshot, run the source-specific fetcher separately. The NIST/OSM fetcher
writes archived source files plus their byte counts, URLs, retrieval time, and SHA-256 hashes.

```sh
python scripts/fetch_open_sources.py \
  --output-dir source_snapshots/YYYY-MM-DD-open-seed \
  --include-nist-details
```

The fetchers stage complete acquisitions before atomically installing them and never overwrite an
existing snapshot directory. The EPA FRS fetcher scans the full raw archive, retains a byte copy, and
writes a deterministic candidate derivative:

```sh
python scripts/fetch_epa_frs.py \
  --output-dir source_snapshots/YYYY-MM-DD-epa-frs-semiconductor-candidates \
  --data-as-of YYYY-MM-DD
```

The EEA utility packages already acquired exact official bytes and pinned extractor outputs; it
never accepts or retains access credentials. The verification/replay example below is local-only and
requires the ignored v16 snapshot described in [Local data](LOCAL_DATA.md). It rehashes the complete
2.03 GB source and all extraction evidence, then replays the candidate derivative without network
access:

```sh
python scripts/fetch_eea_industrial.py \
  --verify-only source_snapshots/2026-07-20-eea-industrial-v16-semiconductor-candidates

python -m semiconductor_atlas.eea_industrial_review propose \
  --snapshot source_snapshots/2026-07-20-eea-industrial-v16-semiconductor-candidates \
  --generated-at 2026-07-20T17:05:51Z \
  --knowledge-cutoff-at 2026-07-20T17:05:51Z \
  --output /tmp/eea-industrial-v16-candidates.rebuilt.json

cmp review_plans/2026-07-20-eea-industrial-v16-candidates.json \
  /tmp/eea-industrial-v16-candidates.rebuilt.json
```

Validate the complete review and replay the retained local admission with its original clock. The
database and snapshot are ignored local artifacts, not included in a clean clone:

```sh
python -m semiconductor_atlas.eea_industrial_review validate-review \
  --candidates review_plans/2026-07-20-eea-industrial-v16-candidates.json \
  --review review_plans/2026-09-07-eea-industrial-v16-scope-admission-v2.json

semiconductor-atlas ingest-eea-industrial-snapshot \
  --database artifacts/2026-09-07-eea-reviewed-source-statements-v2-corrected.sqlite \
  --snapshot source_snapshots/2026-07-20-eea-industrial-v16-semiconductor-candidates \
  --candidate-queue review_plans/2026-07-20-eea-industrial-v16-candidates.json \
  --review review_plans/2026-09-07-eea-industrial-v16-scope-admission-v2.json \
  --accepted-at 2026-09-07T18:00:52.627180Z
```

For a new admission to a separately prepared existing schema-5 database, use `--accept-now` instead
of `--accepted-at`. The latter is replay-only, not permission to backdate a fresh import. The EEA
command does not initialize or migrate databases, and changed reviews fail closed.

The bounded GLEIF fetcher accepts one checksum-valid uppercase LEI per line in canonical sorted
order. It spaces request starts by at least 1.1 seconds, retries a Golden Copy rotation as a whole,
and can replay a retained snapshot without network access:

```sh
python scripts/fetch_gleif.py \
  --allowlist declared-leis.txt \
  --output-dir source_snapshots/YYYY-MM-DD-gleif-level-1

python scripts/fetch_gleif.py \
  --verify-only source_snapshots/YYYY-MM-DD-gleif-level-1
```

The MOENV fetcher POSTs the fixed `EMS_S_01` full-package request, streams it into a private
temporary file, and passes the bytes to the atomic snapshot creator. Read the displayed update time
from the official dataset page, convert it to canonical UTC, and supply it explicitly rather than
guessing from the retrieval clock. Verification is fully offline:

```sh
python scripts/fetch_moenv.py \
  --output-dir source_snapshots/YYYY-MM-DD-taiwan-moenv-ems-s-01-semiconductor \
  --dataset-updated-at YYYY-MM-DDTHH:MM:SSZ

python scripts/fetch_moenv.py \
  --verify-only source_snapshots/YYYY-MM-DD-taiwan-moenv-ems-s-01-semiconductor
```

The Taiwan factory and MOF fetchers use fixed official URLs, verified TLS, bounded streaming,
descriptor-based mutation checks, source-aware allowlist replay, and atomic no-replace installation:

```sh
python scripts/fetch_taiwan_factory.py \
  --output-dir source_snapshots/YYYY-MM-DD-taiwan-ida-registered-factories-semiconductor

python scripts/fetch_taiwan_factory.py \
  --verify-only source_snapshots/YYYY-MM-DD-taiwan-ida-registered-factories-semiconductor

python scripts/fetch_taiwan_mof.py \
  --output-dir source_snapshots/YYYY-MM-DD-taiwan-mof-bgmopen1-semiconductor-organizations \
  --moenv-snapshot source_snapshots/YYYY-MM-DD-taiwan-moenv-ems-s-01-semiconductor \
  --factory-snapshot source_snapshots/YYYY-MM-DD-taiwan-ida-registered-factories-semiconductor

python scripts/fetch_taiwan_mof.py \
  --verify-only source_snapshots/YYYY-MM-DD-taiwan-mof-bgmopen1-semiconductor-organizations
```

Snapshot verification streams the retained archive hash, checks the source-specific ZIP and member
structure, and regenerates the derivative. `--accepted-at` pins the database knowledge clock for deterministic
builds. Use `--accept-now` for a live refresh after verification. A repeated row with the same
semantic fields creates a new source observation without creating duplicate claim versions.
Reprocessing the same acquired archive under a later importer reuses its immutable source document,
records a distinct processing run, and preserves unchanged claims. Processing-run provenance carries
the filter, derivative digest, retention decision, rights review, and source-snapshot manifest digest.

## AI-Critical Manufacturing Baseline v1

The pinned v1 cohort is exactly seven rows, one for each required company, in this order:
`tsmc:fab21-arizona`, `samsung:taylor-leading-edge-project`, `intel:fab52-chandler`,
`micron:singapore-hbm-packaging-project`, `sk-hynix:m15x-cheongju`,
`amkor:peoria-advanced-packaging-project`, and `ase:kaohsiung-site`. It includes only
`leading_edge_logic`, `hbm_fabrication`, `hbm_packaging`, `advanced_packaging`, and
`advanced_test`, using official source documents published in 2024 or later.

`leading_edge_logic` is an explicit-source classification. The cited official source must describe
the same facility or project scope as leading-edge, most-advanced logic, or equivalent source
language. A process-node number alone is never an implicit eligibility threshold. For NIST
multi-location project pages, the locator and excerpt must name the same site as the row; a company,
award, or source-page match cannot carry geography or capability from one site subsection to
another.

Yield, utilization, and qualification are `unknown` for every v1 row. Each unsupported capacity
basis is also `unknown`, not zero. The only numeric capacity claims are Amkor's two approximate
future Peoria project rates, both on the `announced` basis; their different units must not be added.
Lifecycle evidence such as groundbreaking or equipment installation does not create a numeric
capacity claim. Each numeric capacity row must exactly match a reviewed, evidence-fragment-bound
assertion covering its metric, basis, unit, range, period, scope, quantity semantics, and technology
scope; the loader also checks those numeric and time semantics against the verified excerpt.
The fixed v1 loader admits numeric claims only on the directly supported `announced` basis; all
other bases remain in the five-basis schema and are explicitly unknown.

Build the release into new, non-existing sibling paths:

```sh
python3 scripts/build_ai_critical_release.py \
  --input baselines/ai_critical_manufacturing_v1.json \
  --source-root . \
  --output releases/2026-08-20-ai-critical-manufacturing-baseline-v1-r3 \
  --archive releases/2026-08-20-ai-critical-manufacturing-baseline-v1-r3.tar.gz
```

The builder verifies the pinned input, archived source bytes, implementation files, and map
template; stages the bundle; generates the standalone map; validates the release; and only then
installs the output directory and optional deterministic archive. It refuses an existing output or
archive. The managed bundle contains `facilities.csv`, `claims.jsonl`, `evidence.jsonl`,
`capacity.csv`, `atlas.geojson`, `atlas.html`, `source_inputs.json`, `producing_run.json`,
`source_ingestion_runs.json`, `coverage.json`, the zero-row eligible-capacity
`supply_intelligence.jsonl`, `supply_intelligence_contract.json`, the seven-row
`supply_intelligence_facilities.jsonl`, `METHODOLOGY.md`, `ATTRIBUTION.md`, `cohort.json`,
`README.md`, `atlas-template.html`, the three pinned `BUILD_*.py` producer modules, and
`manifest.json`. The manifest binds every managed file by byte count and SHA-256. Archived
publisher bytes remain in ignored local `source_snapshots/`
storage and are not copied into the public bundle; reuse remains subject to each source's recorded
rights decision.

The achieved row-level coverage and unresolved evidence are recorded in
`docs/ai_critical_baseline_v1_gap_matrix.md`.
The [public-release handoff audit](docs/supply_intelligence_handoff_audit_2026-09-07.md)
verifies the published r3 asset and distinguishes consumer compatibility from the still-missing
evidence for quarterly Blackwell supply estimates.

### Cross-vintage change ledger

Compare two independently built AI-critical release directories into new, non-existing output
paths:

```sh
python3 scripts/compare_ai_critical_releases.py \
  --prior releases/2026-08-20-ai-critical-manufacturing-baseline-v1-r3 \
  --current releases/YYYY-MM-DD-ai-critical-manufacturing-baseline-v2 \
  --output releases/YYYY-MM-DD-ai-critical-manufacturing-changes-v1 \
  --archive releases/YYYY-MM-DD-ai-critical-manufacturing-changes-v1.tar.gz
```

The comparison is a deterministic cross-vintage ledger and alert-proposal layer. It pins both input
release manifests, derives stable semantic series independently of release-local claim IDs, and
records each series as `reaffirmed`, `revised`, `added`, or `not_carried_forward`. The last status
means only that a prior series was not carried by the current release. Omission is never negative
evidence without a durable ledger proving that the relevant source, scope, and interval were checked
successfully.

Series identity binds the value kind. Capability slices additionally bind category, technology, and
effective date. Capacity slices additionally bind metric, basis, unit, accounting scope,
input/output basis, quantity semantics, period, technology scope, and effective date, so unlike
quantities are never revised across one another. A material capacity proposal fires when the exact
rational change in any comparable `low`, `base`, or `high` bound is at least 15 percent; it never
uses ambient decimal precision or non-finite JSON numbers.
Readiness changes with a later effective date also produce proposals when the two observations
match unambiguously on entity, category, and technology; both dated evidence lineages are retained.

Structured release payloads must validate under the installed schema. Manifest-bound historical
rendering differences are tolerated only for release `README.md` and `METHODOLOGY.md`; structured
claims, evidence, provenance, exports, hashes, and clocks remain exact. The retained local `r2` to
`r3` comparison therefore resolves to 75 reaffirmed series and zero alert proposals.

Alert proposals have unknown confidence and are not delivery-eligible. The separate
[review ledger](docs/ai_critical_alert_review.md) adds acknowledgment and retraction; calibrated
hysteresis and blind historical detection-performance evaluation remain open. This does not
complete roadmap Phase 3. The [frozen diagnostic evaluator](docs/alert_evaluation.md) measures
evidence-linked retrospective labels, preserves unresolved outcomes and separates backfills from
in-window detections. It cannot certify blind performance or enable delivery.
The comparison writes a new bundle and optional deterministic archive;
it does not modify either input release, including the retained `r3` bundle. Output paths are preflighted outside
both inputs. The bundle and archive are separate no-replace publications, so a late archive failure
can leave the already validated bundle in place.

## Release and interface

### Reviewed Amkor update

The [September 7 Amkor review](docs/amkor_peoria_successor_review_2026-09-07.md) exercises the
complete local evidence-to-change loop: three exact document checks, a reviewed successor input,
reproducible release, and one lifecycle review proposal. It preserves four failed acquisition
attempts and explicitly leaves the other six companies unrefreshed. Old NIST throughput figures
remain historical assertions with unresolved applicability to the expanded project; their omission
is not capacity loss. This is late ingestion of earlier evidence, not proven early detection.

The [reviewed-URL collector](docs/curated_acquisition.md) now repeats the three selected document
checks with bound policy gates, retained failure receipts, content comparisons and offline replay.
Last-eligible observations survive failed later checks. The first successful repeat found two
byte-identical Amkor pages and a NIST page with unchanged visible text; it accepted no new claims.
This is a batch acquisition tool, not a scheduled service or complete publisher search.

The [durable source-version review queue](docs/curated_review.md) keeps unreviewed versions pending
across later quiet or failed checks. Capture can import directly into the queue; explicit reviewer
actions and historical cutoffs replay from an exportable append-only event log. The live pilot
preserved three pending items through a quiet repeat, then resolved them through recorded review
against the earlier evidence. Queue disposition does not accept claims or grant publishing rights.

The [coverage report](docs/curated_coverage.md) binds the full seven-company denominator to an
explicit monitoring catalog. It distinguishes recent document checks, stale evidence, blocked or
failed checks, expired review windows, and unresolved review work. The initial pilot retains three
recent Amkor document checks and six unmonitored cohort facilities. The later
[NIST expansion](docs/nist_monitoring_expansion.md) adds two exact project-page checks for TSMC
Phoenix and Samsung Taylor: five fresh documents, three configured facility scopes, four remaining
gaps, and two pending review items. Report v2 exposes the broader document scopes; v1 reports remain
byte-replayable. An empty queue is not complete coverage or accepted manufacturing evidence.

The [scheduled polling runner](docs/curated_poll.md) now connects those components with a process
lock, cadence guard, retained invocation receipts, and completed-packet recovery before refetch.
A daily 08:00 local app task is enabled for the seven reviewed URLs. Manual end-to-end
capture and not-due repeats passed; scheduler-triggered execution and uninterrupted operation
are not yet demonstrated. It does not accept claims, publish data, or enable restricted sources.

The separate [NIST index discovery collector](docs/nist_discovery.md) follows the reviewed current
news and awards page chains, retaining exact responses and pagination accounting. Its manual pilot
observed 64 document links across six index pages, with 14 company-name matches for review; it
fetched no linked documents and accepted no claims. The inventory retains earlier links through
later failures or rolling-window disappearance, and a restored packet reproduced its bytes exactly.
The subsequent [discovery queue and runner](docs/discovery_review_and_poll.md) retain URL review
dispositions, admission-time replay and crash recovery; the existing daily task now runs both
collectors sequentially. Its manual pilot preserved all 64 URLs and two explicit deferrals through
an unchanged repeat. Actual scheduler execution remains unobserved. This is current index coverage,
not complete publisher history or additional refreshed facilities.

The [Intel expansion](docs/intel_chandler_monitoring.md) added a municipal page explicitly
naming Fab 52, bringing monitoring to six documents across four of seven scopes. Its undated
opening/production statement was reviewed without changing the manufacturing baseline; no HVM
attainment or usable capacity is inferred. Its versioned v3 catalog/config preserved prior plans
and due times. The 14-event queue and historical v1/v2 reports replay exactly.

The subsequent [Micron MTI expansion](docs/micron_mti_monitoring.md) brings monitoring to seven
documents across five of seven scopes, using one Singapore government speech about the January 8,
2025 HBM packaging groundbreaking. This is a recent check of an old document, not a current
production or capacity observation. Its first text version was reviewed without changing the
baseline. The daily task now uses v4; prior plans and due times are unchanged. SK hynix M15X and
ASE Kaohsiung remain unmonitored. The 16-event queue restores at every event cutoff, and historical
v1/v2/v3 coverage reports remain byte-identical. Two earlier NIST text-review items remained pending
at that pilot's cutoff.

The [discovery-to-source handoff](docs/discovery_handoff.md) now verifies an observed URL through
separate exact-document access approval, retained acquisition and explicit text review. One manual
Samsung Taylor capture repeated the monitored Austin page's project narrative; both versions were
reviewed without a baseline revision. CMS page dates are retained separately from claim dates and
physical milestones. The duplicate URL was not added to the recurring catalog. The source queue
now has one pending TSMC text review, and historical v1/v2/v3/v4 coverage reports replay exactly.
The handoff's local historical projection is retrospective and depends on original capture packets;
it is not a portable source archive, claim acceptance or evaluated early detection.

The [reviewed source-native project target gate](docs/source_project_targets.md) now admits the
TSMC Fab 2 target revision into the core claim store: two separately retained NIST document
statements, 2028 and second half of 2027, on an unassigned source-native project. Schema 5 preserves
unknown effective dates and confidence, literal calendar precision without an invented midpoint,
and actual admission clocks. Its knowledge-time export is separate from physical-world claims;
the first-fab baseline, capacity and attained production remain unchanged. The original schema-4
database and r3 artifacts are preserved. This is reviewed historical evidence, not early detection,
a calibrated forecast or a new public release.

The [AI-critical alert-review ledger](docs/ai_critical_alert_review.md) now connects retained
comparisons and both underlying releases to append-only proposal review. Imports require a
manifest-bound review record; portable exports retain derivative evidence and supporting reviews.
Acknowledgment, resolution, retraction and reopening preserve admission-time history without
changing canonical facts or enabling delivery. The Amkor pilot is retrospective, not a measured
early-detection success. The [Micron acquisition review](review_plans/2026-09-07-micron-singapore-acquisition-deferred.json)
leaves the selected issuer-site route disabled pending rights sufficient for durable retention;
the separately reviewed government speech does not change that restriction.

The [unified proposal-review queue](docs/alert_review_v2.md) now brings the Amkor facility proposal
and the separately scoped TSMC Fab 2 target proposal into one version-2 history. A read-only producer
replays already accepted core evidence from a coherent database snapshot; a separate packet-bound
review admits the proposal at its actual new clock. The original Amkor events remain unchanged.
Both local proposals are acknowledged, neither is delivery-eligible, and offline packet consistency
is explicitly distinct from replaying the original source acceptance. Blind performance evaluation
and calibrated forecasting remain open.

The [accepted-project population evaluator](docs/project_target_evaluation.md) freezes every
supported-route comparison before a cutoff, including unadmitted revisions and reaffirmations.
Post-freeze source-statement labels measure conditional support and admission coverage; they do
not establish realized production, publisher recall, independent evaluation or calibrated forecasts.

The [retained source-observation inventory](docs/curated_observation_population.md) moves the
denominator before claim acceptance: every captured document check, unimported capture and durable
polling intent in its declared inputs remains visible. The actual frozen sample has 22 checks,
including unchanged and policy-blocked observations; collector classifications are not semantic
revision labels or proof of complete publisher coverage.

The [prospective source-target workflow](docs/prospective_source_targets.md) adds pre-window
registration, pure target-literal detection, durable shadow predictions and full-population
closure. It includes all seven configured documents; four have parser routes and three abstain.
The fixed September 8–15 UTC study does not yet supply future outcomes, independently reviewed
labels, measured detector performance or calibrated forecasts.

The separate [prospective outcome evaluator](docs/prospective_target_evaluation.md) pins its
scoring policy before observations, produces source-only review material after closure and
retains unresolved labels, abstentions, late and missing outputs in conditional document-triage
diagnostics. It does not infer reviewer independence, subject-level correctness or physical truth.

The separate [source-assertion inventory](docs/source_assertions.md) extracts bounded ceremony,
opening, production and undated aspiration wording from the retained Amkor, Chandler and MTI
documents. It preserves all 22 document opportunities: five unchanged Amkor pairs, two
extraction-only first observations and 15 checks outside these three routes. Exact evidence and
source context remain bound; first observations, unsupported wording and editorial revisions
cannot become manufacturing progress. This is exposed engineering work, not a change to the
registered study, claim acceptance or measured detection performance.

The separate [source-native statement review](docs/source_statement_review.md) now labels all
22 frozen checks: eleven target-bearing pairs, five scoped no-target findings and six uncomparable
checks. Sixteen reviewed target pairs reduce to nine distinct evidence pairs, with no revisions
found in these supplied versions. Source-native scope, literal deadlines and milestone distinctions
remain explicit; this exposed retrospective review measures coverage, not alert accuracy or
physical production. It leaves the frozen collector population and accepted claims unchanged.

The separate [selected source-vintage review](docs/source_vintage_review.md) now compares every
exact NIST award URL shared by the retained July snapshot and frozen September inventory: three
URLs, with 25 unmatched historical award pages and 13 outside September checks kept explicit.
Eight reviewed calendar-target formulations include two changed Fab 2 formulations describing
one revised subject; the other six formulations are unchanged. The July anchors do not replace
actual collector predecessors. All 369 retained source bindings verify, and the new report
rebuilds byte-identically while earlier reports remain unchanged. This is exposed retrospective
review, not a detector prediction, independent evaluation or proof of physical acceleration.
The next gate requires prespecified future detector outputs followed by separate adjudication.

Create a deterministic release for explicit world-state and knowledge cutoffs:

```sh
semiconductor-atlas release \
  --database atlas.sqlite \
  --output releases/2026-07-20-open-seed-taiwan-tax-relationships \
  --as-of 2026-07-20 \
  --recorded-at 2026-07-20T09:54:46Z \
  --forecast-start 2026-07-01
```

The bundle includes entity, claim, source-observation, identity, and reviewed-relationship lineage
JSONL; evidence and capacity CSV; GeoJSON; source metadata; attribution; summary; and content hashes.
Its 20-quarter baseline forecast is deterministic and
parameter-fingerprinted, but every coefficient and ramp curve is labelled `assumption_not_fact`.
It has not been calibrated or backtested and is not approved for investment use.
The CLI builds from a private SQLite backup, so forecasts, alerts, claims, source inputs, and coverage
share one database snapshot without holding a long read lock on the live store.

The separate [milestone benchmark](docs/milestone_benchmark.md) and
[CLI workflow](docs/milestone_benchmark_cli.md) freeze evidence-bound timing scenarios and match
them to separately reviewed outcomes. They preserve cutoff-safe lineage, project/geography splits
and unknown or censored cases without writing core claims or embedding publisher bodies. The
workflow is tested, but no real prediction/outcome study or calibrated forecasting result has been
produced. See the [implementation review](docs/milestone_benchmark_implementation_review.md).

The separate [realized-event workflow](docs/realized_milestones.md) now retains one
[reviewed Fab 21 commercial-production commencement observation](docs/fab21_realized_observation_2026-09-07.md)
with year-2024 bounds and a null midpoint. It is a source-reported retrospective event, not a
canonical core claim, an HVM alias, independent physical corroboration or a scored forecast pair.

Generate the standalone, dependency-free interface from the release GeoJSON:

```sh
python web/generate_atlas.py \
  releases/2026-07-20-open-seed-taiwan-tax-relationships/atlas.geojson \
  releases/2026-07-20-open-seed-taiwan-tax-relationships/atlas.html
```

The generator embeds the data in one HTML file and adds its hash to an existing release manifest.

## Tests

```sh
python -m unittest discover -s tests -v
python -m unittest discover -s web/tests -v
```

The clean-clone CI workflow runs both suites on Python 3.11, checks Python syntax, and builds the
source distribution and wheel. It intentionally does not require ignored local data.

The normative contract and limitations are in `docs/data_contract.md` and `docs/methodology.md`.
Source rights and activation rules are in `docs/source_registry.md`; future work is gated in
`docs/roadmap.md`. The installed FRS rebuild and release evidence is recorded in
`docs/epa_frs_refresh_audit_2026-07-19.md`; the reviewed GLEIF pilot is recorded in
`docs/gleif_pilot_audit_2026-07-19.md`; and the Taiwan MOENV source decision is recorded in
`docs/non_us_source_gate_2026-07-20.md`, with installed acceptance evidence in
`docs/taiwan_moenv_acceptance_audit_2026-07-20.md`. The registered-factory source, reviewed
same-kind assignments, and organization boundary are audited in
`docs/taiwan_factory_identity_gate_2026-07-20.md`. The MOF acquisition/import evidence and reviewed
facility-to-tax-unit relationship gate are recorded in
`docs/taiwan_mof_snapshot_audit_2026-07-20.md` and
`docs/taiwan_mof_relationship_gate_2026-07-20.md`. The EEA candidate-source acceptance is audited
in `docs/eea_industrial_v16_acceptance_audit_2026-07-20.md`.
