# Methodology

The Open Data Center Atlas records large data center builds as collections of sourced
statements. It reports what public sources said, when they said it, and on what basis. It does not
estimate missing values, verify construction from imagery, or forecast. Unknown values stay unknown.

## What a record is

A record is one build: a campus, or tightly co-located buildings that share a site name and power
arrangement. Each record holds:

- **Location** with an explicit precision (site, approximate site, locality, county, or state).
- **Status** with the date and source of the newest supporting evidence.
- **Organizations** in stated roles: developer, owner, operator, tenant, investor, builder, power
  supplier, utility, hardware supplier, or government.
- **Quantities** (power, investment, accelerators, floor and land area, buildings, jobs), each with
  a basis, qualifier, scope, date and source.
- **Milestones**, actual or planned, at the precision the source gives (year, half, quarter,
  month, or day).
- **Power supply** arrangements as reported.
- **Sources** with publisher, type, publication date and access date.

Every statement points to at least one source, and every source must be cited by a statement.
The record contract is enforced in code and published as a JSON Schema.

## Inclusion criteria

Version 1 includes builds that were announced, proposed in a public filing, under construction, or
first operational on or after 1 January 2022, with at least 100 MW of planned power in any scope or
at least USD 1 billion (or equivalent) of planned investment, and a location known at least to the
county or city. Cancelled, paused and blocked builds stay in the dataset. Capacity contracts
without a named site are out of scope until a site is named.

This is a curated selection of large builds, not a census. Smaller facilities, private projects
without public disclosures, and regions with little English-language coverage are
under-represented.

## Sources and research

Primary sources come first: company announcements and filings, government and economic-development
releases, utility and regulatory filings, permits, and court records. Reputable news and trade
press corroborate them or fill gaps where no primary source exists. Each source is labelled by
type, so users can filter to primary evidence.

All text in the dataset (summaries, notes, power-supply descriptions) is written by the
maintainers. Sources are linked, not copied; the dataset contains no verbatim excerpts.

The first release was compiled in October 2026 by AI-assisted research agents working from public
web sources, each record then checked against its cited sources in a separate verification pass
before publication. Because every value links to its source, any reader can audit it. Errors
should be reported through the issue tracker and are corrected in the open.

## Statuses

| Status | Meaning |
| --- | --- |
| `proposed` | Known from filings, zoning applications or reports; no formal announcement by the developer. |
| `announced` | Publicly announced; no cited evidence that construction has started. |
| `under_construction` | Cited evidence of site work or construction. |
| `partially_operational` | At least one building or phase is operating while construction continues. |
| `operational` | Operating, with no cited ongoing construction of the recorded scope. |
| `paused` | Work or approvals reported as halted, without cancellation. |
| `cancelled` | Reported as cancelled, withdrawn, or rejected without a pending appeal. |

`status_as_of` is the publication date of the newest source supporting the status; when that
source is an undated living page, such as a company campus page, it is the access date and the
record's notes say so. A status can be out of date if nothing newer has been published or
recorded.

## Power: basis and scope

Power figures are the most frequently misread numbers in data center reporting. Each statement
keeps two separate labels, and they are never merged.

| Basis | Meaning |
| --- | --- |
| `planned` | Announced or targeted eventual capacity. |
| `contracted` | Capacity in a signed utility, lease or offtake agreement. |
| `permitted` | Capacity approved in a permit or interconnection decision. |
| `under_construction` | Capacity reported as being built. |
| `operational` | Capacity reported as energized or in service. |

| Scope | Meaning |
| --- | --- |
| `it_load` | Critical IT power available to servers. |
| `facility` | Total facility demand, including cooling and losses. |
| `grid_connection` | Size of the utility interconnection or service agreement. |
| `onsite_generation` | Capacity of generation built for the site. |
| `unspecified` | The source does not say. Most announcements fall here. |

Facility power is typically larger than IT load by the site's overhead, so figures with different
scopes are not directly comparable. Phase figures are labelled as phases and are never summed into
campus totals by the dataset.

## Investment and other quantities

Investment is kept in the reported currency, without exchange-rate conversion or inflation
adjustment. Announced investment often bundles land, buildings, power infrastructure and sometimes
IT equipment, and the bundle is rarely itemized. Accelerator counts name the model when the
source does. Areas keep their reported unit (square feet, square metres, acres, hectares).

## Derived headline values

The table views show one headline value per project for convenience. Headline values select one
statement; they never add, average or convert statements.

- **Planned power**: campus-level statements with basis planned, contracted or permitted. Facility,
  grid-connection and unspecified scopes are preferred over IT load; on-site generation is excluded.
  The most recent statement wins, then the largest value. The chosen statement's scope, basis,
  qualifier and date are published next to it.
- **Operational power**: the same rule over operational statements.
- **Investment** and **accelerators**: the most recent campus-level statement, then the largest.
- **Announced**, **construction started** and **first operation**: the earliest actual milestone
  of that kind. When no first operation has happened, the latest planned target is shown and
  labelled as a target.

Summing headline power across projects mixes scopes and dates. If you need a total, filter the
statement table to one basis and scope and decide how to treat ranges and phases.

## Locations

Coordinates are WGS84. `site` means the build's parcel or campus is identified; `approximate_site`
means within a few kilometres; `locality`, `admin2` and `admin1` mean the point is a town, county
or state centroid because the sources name nothing finer. The location note explains how the
point was derived. Locations are never inferred from imagery alone.

## Uncurated layers

Two open layers add breadth around the curated records:

- **OpenStreetMap**: every feature tagged `telecom=data_center` or `building=data_center`,
  worldwide, with its tags. It shows where mappers have recorded data centers; it has no status,
  capacity or completeness guarantee. © OpenStreetMap contributors, ODbL 1.0.
- **Wikidata**: items that are instances of data center (Q671224) with coordinates. CC0.

Each layer's retrieval manifest records the exact query, endpoint, retrieval time, and the SHA-256
of the raw response. Layers are refreshed by re-running the fetch command; changes are reviewed as
ordinary diffs. Layer features are not linked to curated records automatically, because name and
proximity matches are not evidence of identity.

## Limitations

- **Selection**: large, publicly announced builds are over-represented; the dataset is not a
  census and absence from it means nothing.
- **Reported, not verified**: figures are what sources said, often promotional and forward-looking.
  The dataset does not confirm construction progress independently.
- **Timeliness**: statuses can lag reality between source updates and reviews.
- **Language**: English-language sources dominate, which under-covers some regions, notably China.
- **Not advice**: nothing here is investment, legal or engineering advice.

## Related work

[Epoch AI's Frontier Data Centers](https://epoch.ai/data/data-centers) tracks the largest AI
data centers with satellite imagery and permits (CC BY). Commercial trackers cover more facilities
behind paywalls. This atlas focuses on statement-level provenance, explicit power scopes, open
licensing and many access routes (CSV, JSON, GeoJSON, SQLite, Excel, Parquet and a static API).
It complements rather than replaces imagery-based analysis.

The atlas lives in the same repository as Semiconductor Atlas, an evidence-first registry of
semiconductor fabs, and shares its rule that every published value must trace to evidence.
