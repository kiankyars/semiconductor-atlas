# AI-Critical Manufacturing Baseline v1 gap matrix

Audit date: 2026-08-20

## Audited baseline

The pre-implementation audit used the newest retained database and release available at the start
of this work:

- database: `artifacts/2026-07-20-open-seed-taiwan-tax-relationships.sqlite`
- database SHA-256: `c6083299cae65a96890dff831ca201566de82dae7532ca555c65391f7ee21ca9`
- release: `releases/2026-07-20-open-seed-taiwan-tax-relationships`
- release manifest SHA-256: `6d7bca2cd392d1372774ae227ae9cd6e3a665454e2c25084b357c9b58691f4ce`
- world-state cutoff: `2026-07-20`
- knowledge cutoff: `2026-07-20T09:54:46Z`

All 277 entries in `LOCAL_DATA_SHA256SUMS` passed `shasum -a 256 -c`. The pre-change core
suite passed 457 tests and the standalone-map suite passed 11 tests under Python 3.14.3.

That broad release contains 4,865 entities and 104,286 current claims. It was not an AI-critical
company cohort: it had no cohort-specific allowlist, release contract, strict unknown-state surface,
or compact Supply Intelligence export. The v1 implementation therefore uses a separately pinned
and fail-closed input rather than treating the broad database as complete.

### Seven-company coverage found before implementation

Counts below are open claims in that retained database. They describe source-native projects and
registry rows, not a facility-safe AI-critical cohort.

| Company | Entities and open claims found | Evidence, geography, capability, and capacity result |
| --- | --- | --- |
| TSMC | One Arizona NIST project; 34 claims: 28 scalar, 4 relationship, 1 capability, 1 milestone | 24 evidence links across 2 NIST documents; city text only, null geometry; one project-level leading-edge capability; zero capacity claims. |
| Samsung | One Texas NIST project; 36 claims: 29 scalar, 5 relationship, 2 capability | 25 evidence links across 2 NIST documents; Austin/Taylor program scope and null geometry; one derived FD-SOI capability was overbroad across sites; zero milestone and capacity claims. |
| Intel | Four NIST projects: Arizona 36 claims/2 capabilities, New Mexico 40/6, Ohio 36/2, Oregon 35/1; 147 claims and 11 capability rows total | Every project inherited program-wide site relationships, all project geometries were null, and capacity claims were zero. Location-specific source subsections were required to avoid cross-site joins. |
| Micron | Three NIST projects: Idaho 29 claims/2 capabilities, New York 33/5, Virginia 29/4; 91 claims and 11 capability rows total. Taiwan also had 18 source-native facility rows covering 10 distinct names and 7 MOENV points. | The NIST rows were generic leading-edge DRAM rather than facility-specific HBM; Virginia was out of scope. Taiwan registry semantics did not prove HBM or advanced-packaging capability or lifecycle. Capacity claims were zero. |
| SK hynix | Indiana NIST project: 36 claims and 4 capability rows, plus a project site | Project-level HBM/advanced-packaging language and an expected 2028 milestone; null geometry and zero capacity claims. The milestone was forward-looking, not operation or qualification. |
| Amkor | Five entities and 63 current claims | Peoria carried the database's two relevant capacity claims, both `announced`: approximately 14,500 wafers/month and 3.7 million units/month. Neither had an applicability period or supported construction, installation, qualification, or usable output. |
| ASE | 90 source entities: 38 IDA facilities, 46 MOENV facilities, 4 MOF organizations, and an OSM Shanghai site plus organization; 1,596 claims including 35 geometry claims | Zero capacity claims. Registry production/activity labels did not establish facility-specific advanced packaging, HBM, qualification, yield, or utilization; 18 IDA-MOENV matches remained registry identity evidence only. |

## Exact production cohort

`baselines/ai_critical_manufacturing_v1.json` contains exactly seven facility or project-site rows,
one per required company, supported by nine official source documents and twelve precise evidence
fragments dated 2024 or later. All seven rows publish named geography but no facility-safe point, so
their GeoJSON geometry is intentionally null. This is achieved coverage, not a claim that each
company has only one relevant facility.

| Company | Pinned row and identity scope | Geography | In-scope capability | Lifecycle evidence | Published capacity and explicit unknowns |
| --- | --- | --- | --- | --- | --- |
| TSMC | `tsmc:fab21-arizona`; `named_facility` | Phoenix, Arizona, US | `leading_edge_logic`; Fab 21 / first Arizona fab, 4nm-5nm leading-edge project scope and 5nm most-advanced volume technology; production readiness | `complete` as of 2026-02-28; the first facility entered high-volume production at the end of 2024 and Fab 21 appears in the operating-fab table | No numeric capacity. All five bases, qualification, utilization, and yield are unknown. |
| Samsung | `samsung:taylor-leading-edge-project`; `project_site_scope` for the two-fab aggregate | Taylor, Texas, US | `leading_edge_logic`; two planned foundry fabs explicitly described as leading-edge and focused on 2nm | `announced` as of 2024-07-24 | No numeric capacity or physical-progress evidence. All five bases, qualification, utilization, and yield are unknown. |
| Intel | `intel:fab52-chandler`; `named_facility` | Chandler, Arizona, US | `leading_edge_logic`; Intel 18A and Panther Lake / Clearwater Forest manufacturing; production readiness | `ramping` as of 2025-10-09; Fab 52 was fully operational with high-volume production stated for later in 2025 | No numeric capacity. All five bases, qualification, utilization, and yield are unknown. |
| Micron | `micron:singapore-hbm-packaging-project`; `project_site_scope` | Singapore | `hbm_packaging` and `advanced_packaging`; planned HBM advanced-packaging facility | `site_preparation` as of 2025-06-26; the source states a January 2025 groundbreaking and intended expansion beginning in calendar 2027 | No numeric expansion amount. All five bases, qualification, utilization, and yield are unknown. |
| SK hynix | `sk-hynix:m15x-cheongju`; `named_facility` | Cheongju, North Chungcheong Province, KR | `hbm_fabrication`; M15X is described for next-generation DRAM including HBM and as optimized for HBM production | `tools_installing` as of 2025-10-29; equipment installation had begun after early cleanroom opening | No numeric capacity. Tool-installation lifecycle evidence is not a `tool_installed` capacity amount; all five bases, qualification, utilization, and yield are unknown. |
| Amkor | `amkor:peoria-advanced-packaging-project`; `project_site_scope` | Peoria, Arizona, US | `advanced_packaging` and `advanced_test`; planned 2.5D / next-generation packaging and integrated-circuit test | `announced` as of 2024-07-24 | Two approximate future project-addition rates are `announced`: 14,500 wafers/month and 3,700,000 units/month. They have different units and are not additive. `physical_construction`, `tool_installed`, `qualified`, and `economically_usable`, plus qualification, utilization, and yield, are unknown. |
| ASE | `ase:kaohsiung-site`; `campus_scope` | Kaohsiung, Taiwan | `advanced_packaging` and `advanced_test`; FOCoS, 2.5D, CPO, high-end packaging, and one-stop packaging/test at aggregate campus scope; production readiness | `complete` as of the report's 2025-08 publication; the source describes Kaohsiung as ASE's largest operating location and manufacturing site | No plant-level allocation or numeric capacity. All five bases, qualification, utilization, and yield are unknown. |

## Deterministic selection and contamination guards

- The pinned input order is exactly TSMC, Samsung, Intel, Micron, SK hynix, Amkor, and ASE, with
  deterministic company and facility-key ordering.
- Scope is closed to `leading_edge_logic`, `hbm_fabrication`, `hbm_packaging`,
  `advanced_packaging`, and `advanced_test`; capability rows must exactly cover the declared scope.
- Leading-edge logic is source-explicit. The same bounded facility or project must be described by
  the official source as leading-edge, most-advanced logic, or equivalent. A node number alone is
  not an eligibility threshold.
- NIST multi-site pages are location-scoped at the evidence-fragment level. The locator and excerpt
  must identify the same city and project as the output row. A shared source page, award, company,
  or program cannot join a statement from one site subsection to another.
- Facility identity remains `named_facility`, `campus_scope`, or `project_site_scope`; aggregate
  source language cannot be silently allocated to an unnamed building, fab, or line.
- Every source is official primary evidence with a validated 2024-or-later publication date,
  separate retrieval/acquisition clocks, local byte count and SHA-256, locator, bounded excerpt,
  attribution, rights decision, and fragment-verification record. HTML and JSON excerpt segments
  are resolved against normalized archived text; ASE PDF fragments have dated manual visual review.
- Yield, utilization, and qualification remain unknown throughout v1. The five capacity bases are
  preserved exactly, and every unsupported basis is unknown rather than zero.
- Numeric capacity must exactly match a typed assertion inside a linked evidence fragment. The
  fragment hash binds metric, basis, unit, range, period, scope, quantity semantics, and technology
  scope, and the loader reconciles its number and time language to the verified excerpt.

## Release-contract gaps closed

The dedicated build now provides the requested facility CSV; claims and evidence JSONL; capacity
CSV; GeoJSON; standalone dependency-free map; methodology and attribution; coverage and producing-
run provenance; deterministic manifest; compact Supply Intelligence JSONL; and deterministic
archive. The build verifies pinned source and implementation bytes, refuses unmanaged or changed
files, validates every claim/evidence/run reference, and rebuild-checks release content from the
pinned cohort before acceptance.

The Supply Intelligence capacity projection is deliberately zero rows: both Amkor claims are
source-stated monthly rates with no applicability period, not quarter totals. The companion
contract records both exclusions as `quantity_semantics_not_quarter_total`; a separately versioned
seven-row facility-scope JSONL carries bounded identity, geography, lifecycle, and unknown states
without dangling capacity IDs. These are new, versioned handoff schemas; the existing sibling
Supply Intelligence Atlas adapter accepts only the legacy release contract and is not claimed to
consume this v1 bundle directly. The handoff contract records that boundary and keeps confidence
null rather than inventing a calibrated probability to satisfy the legacy adapter.

## Remaining evidence gaps

- Point geometry remains unsupported for all seven rows. City, region, and country are published,
  but null geometry must not be backfilled from a geocoder or company-campus centroid.
- Qualification, yield, and utilization remain unknown for all seven rows.
- Numeric capacity remains unknown for every company except the two announced Amkor project rates.
  There are no supported numeric `physical_construction`, `tool_installed`, `qualified`, or
  `economically_usable` capacity claims anywhere in the cohort.
- Samsung's Taylor row has announcement evidence only; construction, installation, operation, and
  qualification remain unsupported.
- Micron's Singapore row has a groundbreaking and future-expansion statement, but no numeric
  throughput, installed-tool, qualification, or operating evidence.
- SK hynix M15X has equipment-installation evidence but no numeric capacity tied to installed tools,
  no customer qualification evidence, and no supported yield or utilization.
- ASE capability is supported only at Kaohsiung campus aggregate scope. Plant/building allocation,
  HBM-specific packaging, qualification, and throughput remain unsupported.
- Amkor's approximate future rates have no stated applicability period and do not establish present
  construction, installed tools, qualification, utilization, yield, operation, or usable output.

## Public-release rights boundary

Archived publisher bytes remain ignored local evidence and are not release payloads. The bundle may
publish source metadata, hashes, precise locators, bounded excerpts, attribution, and derived claims
only under each recorded rights decision. Public availability does not create a blanket
redistribution license: company newsroom and ASE report content is metadata-and-excerpt-only; NIST
material retains embedded/third-party exceptions; and EDGAR access does not make all issuer-authored
content public domain. A bundle is publication-ready only after its final standalone map, manifest,
archive, full tests, and local validation all pass against unchanged pinned inputs.
