# AI-Critical Manufacturing Baseline v1 methodology

- Release ID: `2026-09-07-ai-critical-manufacturing-amkor-successor-v1-r2`
- World-state cutoff: `2026-09-07`
- Knowledge cutoff: `2026-09-07T02:31:58Z`
- Selection method: `ai-critical-cohort-selection-v1`

This release is a bounded, evidence-first baseline for exactly TSMC, Samsung, Intel, Micron,
SK hynix, Amkor, and ASE. It includes only leading-edge logic, HBM fabrication, HBM packaging,
advanced packaging, and advanced test. Every source document is official, was published on or
after 2024-01-01, and is bound to locally archived bytes by size and SHA-256 before release.

Facility identity is deliberately scoped as `named_facility`, `campus_scope`, or
`project_site_scope`. The latter two labels prevent a company or government project description
from being promoted into a more precise fab, building, line, operator, or ownership assertion.
City, region, and country remain publishable when point geometry is unknown. Null GeoJSON geometry
is intentional and must not be backfilled with an inferred facility coordinate. Canonical display
names, normalized geography labels and codes, lifecycle summaries, and mapped lifecycle stages are
reviewed `reconciled_fact` claims, not purported verbatim source statements.

Lifecycle values are evidence-bounded. Announcement, funding, project-type, groundbreaking, and
expected-start statements do not establish operation, tool installation, customer qualification,
yield, utilization, or economically usable output. Capability readiness is stored separately from
lifecycle. All capacity uses exactly one of the five bases: `announced`,
`physical_construction`, `tool_installed`, `qualified`, or `economically_usable`. Missing bases are
reported as unknown, never zero. Differently scoped or differently unitized capacity rows are not
summed.

The fixed v1 numeric-claim grammar admits only the cohort's directly supported `announced` basis.
The other four bases remain present in every facility's five-basis unknown contract and release
coverage, but a later release must add a separately reviewed and tested evidence grammar before it
can publish a numeric claim on any of them. This prevents construction or equipment-installation
lifecycle language from being reused as numeric capacity support.

Every published claim names one or more evidence fragment IDs. `evidence.jsonl` binds each fragment
to publisher, official URL, publication and retrieval clocks, exact-byte hash, source-ingestion
run, rights decision, precise locator, and a short excerpt. V1 accepts support links only; the
local release gate resolves ordered HTML/JSON excerpt segments against normalized archived text and
requires dated manual visual review for PDF fragments. The fragment hash binds the source record,
locator, excerpt, and verification record. Numeric capacity also requires a typed assertion inside
the linked fragment that exactly matches its metric, basis, unit, range, period, scope,
input/output basis, quantity semantics, and technology scope. The loader reconciles the assertion's
number and time language to the verified excerpt, so a monthly rate cannot be relabeled as a
quarterly total. Each claim ID binds those fragment hashes, the claim's system-time acceptance, and
its producing run. Re-recording the same proposition
under a different accepted run therefore creates a new immutable claim version rather than silently
changing an existing ID. Claim and extraction confidence remain null because neither is calibrated. The release does
not redistribute source bytes classified as
metadata-and-excerpt-only; their local archive locators and hashes remain sufficient for authorized
replay. Approximate publisher dates retain their explicit precision and normalization basis rather
than masquerading as exact days. `producing_run.json` resolves every claim's producing run and pins
the schema, code, configuration, selection, identity-review, model, and template versions. The
deterministic manifest hashes every managed release file.

The Supply Intelligence capacity export is a deliberately smaller, fail-closed consumer surface.
Only capacity with an exact half-open calendar-quarter period and quarter-total semantics is
eligible; v1 has zero eligible rows because the two direct Amkor statements are monthly future
rates with no applicability period. A separately versioned facility-scope JSONL carries the seven
bounded rows and explicit unknowns without dangling capacity IDs. Consumers must resolve claim and
evidence detail from the full release. These are new handoff schemas, not a claim that the existing
sibling Supply Intelligence legacy adapter can ingest this release directly. Handoff confidence is
null and explicitly means unknown/not calibrated; no numeric value is invented for compatibility.
