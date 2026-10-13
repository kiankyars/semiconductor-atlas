# Methodology

The Open Data Center Atlas records large data center projects from public sources. Each figure
retains its source, date and meaning. Missing values are left blank.

## What a record is

A record is one project: a campus, or tightly co-located buildings that share a site name and power
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

Every statement points to at least one source, and every listed source supports a statement.

## Inclusion criteria

The atlas includes projects that were announced, proposed in a public filing, under construction, or
first operational on or after 1 January 2022, with at least 100 MW of planned power in any scope or
at least USD 1 billion (or equivalent) of planned investment, and a location known at least to the
county or city. Cancelled, paused and blocked builds stay in the dataset. Capacity contracts
without a named site are out of scope until a site is named.

Coverage is a selection of large projects. Smaller facilities, private projects
without public disclosures, and regions with little English-language coverage are
under-represented.

## Sources and research

Primary sources come first: company announcements and filings, government and economic-development
releases, utility and regulatory filings, permits, and court records. Reputable news and trade
press corroborate them or fill gaps where no primary source exists. Each source is labelled by
type, so users can filter to primary evidence.

All text in the dataset (summaries, notes, power-supply descriptions) is written by the
maintainers. Sources are linked, not copied; the dataset contains no verbatim excerpts.

Records are drafted from public sources, then checked in a separate review. The review re-opens
each source and corrects or removes statements it does not support. Report errors through the
issue tracker. Projects identified for further research are listed on the coverage page.

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

Each power figure has a **basis** describing its stage and a **scope** describing what it measures.

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

The table views select one headline value per project from the source statements, without adding,
averaging or converting them.

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

The map also includes two external datasets:

- **OpenStreetMap**: every feature tagged `telecom=data_center` or `building=data_center`,
  worldwide, with its tags. These are mapper-contributed locations without project status or
  capacity figures. © OpenStreetMap contributors, ODbL 1.0.
- **Wikidata**: items that are instances of data center (Q671224) with coordinates. CC0.

Each layer includes a retrieval manifest with its query, endpoint, retrieval time and response
checksum. Name and proximity alone are not enough to link a layer feature to an atlas project,
so these matches are not made automatically.

## Limitations

- **Coverage**: the atlas is not a census. A missing project may not have been researched or may
  lack public information that meets the inclusion criteria.
- **Source claims**: figures may be promotional or forward-looking. Research checks them against
  their cited sources; it does not independently confirm construction progress or forecast outcomes.
- **Timeliness**: statuses can lag reality between source updates and reviews.
- **Language**: English-language sources dominate, which under-covers some regions, notably China.

## Related work

[Epoch AI's Frontier Data Centers](https://epoch.ai/data/data-centers) tracks the largest AI
data centers with satellite imagery and permits (CC BY).

The atlas shares a repository with Semiconductor Atlas, a registry of semiconductor fabs.
