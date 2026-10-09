# Adding or correcting a data center build

Each project is one JSON file in [`projects/`](projects/), named `<id>.json`. The record
contract is enforced by [`dcatlas/contract.py`](dcatlas/contract.py) and mirrored in
[`schema/project.schema.json`](schema/project.schema.json). Check your edit before opening a pull
request:

```sh
cd datacenters
python3 -m dcatlas validate projects
```

## Inclusion criteria (v1)

A record describes one data center build: a single campus, or tightly co-located buildings that
share a site name and power arrangement. New construction, major expansions, and conversions of
existing industrial or crypto-mining sites all count. A project qualifies when all three hold:

1. It was publicly announced, proposed in a public filing, under construction, or first became
   operational on or after 2022-01-01.
2. A cited source reports at least 100 MW of planned power in any scope, or at least USD 1 billion
   (or the equivalent in another currency on the announcement date) of planned investment.
3. It has a specific location at least to the county or city level.

Cancelled, paused, and blocked projects stay in the dataset if they qualified when announced.
Corporate capacity commitments without a named site (for example, a cloud contract measured in
gigawatts) are out of scope until a site is named.

## Evidence rules

- **Every value cites a source you opened.** A search-result snippet or memory is not a source.
  Each statement carries `source_ids` that point into the record's `sources` list, and every
  listed source must be cited at least once.
- **Write in your own words.** `summary`, every `note`, and every `description` are paraphrases.
  Do not copy sentences from sources; the dataset carries no verbatim excerpts.
- **Prefer primary sources.** Company newsrooms, filings (SEC, utility commission, air permits),
  governments, utilities, and courts come first. Reputable news and trade press are acceptable,
  especially to corroborate or when no primary source exists. Label each source's `type`.
- **Never collapse bases or scopes.** `power_capacity` keeps its `basis` (`planned`, `contracted`,
  `permitted`, `under_construction`, `operational`) and `power_scope` (`it_load`, `facility`,
  `grid_connection`, `onsite_generation`, or `unspecified` when the source does not say). A
  phase figure uses `applies_to: "phase"` with a `phase_label`; never add phases into a campus
  total yourself.
- **Keep original units and currencies.** Investment stays in the reported currency; there is no
  FX conversion. Use `qualifier` for "about", "up to", "more than", or a range.
- **Conflicts stay visible.** If sources disagree, record each statement separately with its own
  `as_of` and sources, and explain in a `note`.
- **Status reflects the latest evidence.** `status_as_of` is the publication date of the newest
  source supporting the status, not the date you looked. If that source is an undated living page
  (such as a company campus page), use the date you accessed it and say so in `notes`.
- **Roles need support.** List a party only in a role a source states. If a relationship is
  reported but unconfirmed by the parties, say so in the party's `note`.
- **Unknown stays unknown.** Leave out anything you cannot cite. Absence of a metric means it was
  not found, never zero.

## Fields

| Field | Meaning |
| --- | --- |
| `id` | Stable lowercase kebab-case slug, usually `<developer-or-program>-<place>`. Never reuse or rename casually; add the old name to `aliases`. |
| `name`, `aliases` | Common project name and other names used by sources (codenames, campus names). |
| `summary` | Neutral description of the build, in your own words, at most 700 characters. |
| `location` | `country` (ISO 3166-1 alpha-2), optional `admin1` (state/province), `admin2` (county), `locality`; WGS84 `lat`/`lon`; `precision` of `site`, `approximate_site`, `locality`, `admin2` or `admin1`; sources naming the location; `note` explaining how coordinates were derived. |
| `status` | `proposed` (filings or reports only), `announced`, `under_construction`, `partially_operational`, `operational`, `paused`, `cancelled`. |
| `workloads` | Reported intended uses: `ai_training`, `ai_inference`, `ai_unspecified`, `cloud`, `colocation`, `enterprise`, `hpc`, `crypto_mining`. |
| `parties` | Organizations with a `role`: `developer`, `owner`, `operator`, `tenant`, `investor`, `builder`, `power_supplier`, `utility`, `hardware_supplier`, `government`. |
| `metrics` | Quantities: `power_capacity` (MW), `investment` (currency code), `accelerators` (count, optional `accelerator_model`), `floor_area` (`sq_ft`/`sq_m`), `land_area` (`acres`/`hectares`), `buildings`, `jobs_construction`, `jobs_permanent` (count). Each has `basis`, `qualifier`, `applies_to`, `as_of`, and sources. |
| `milestones` | Dated events (`announced`, `site_acquired`, `zoning_approved`, `permit_approved`, `construction_started`, `first_operational`, `fully_operational`, `expansion_announced`, `paused`, `cancelled`, `other`) with `kind` `actual` or `planned` (a target). Dates may be `YYYY`, `YYYY-MM`, `YYYY-MM-DD`, `YYYY-Qn` or `YYYY-Hn`. |
| `power_sources` | Reported supply arrangements: `grid`, `natural_gas_onsite`, `nuclear`, `solar`, `wind`, `hydro`, `geothermal`, `battery_storage`, `fuel_cell`, `diesel_backup`, `other`. |
| `sources` | `id` (`s1`, `s2`, ...), `url`, `title`, `publisher`, `published` (partial date or `null`), `accessed` (YYYY-MM-DD), `type` (`company`, `government`, `regulatory_filing`, `utility`, `court`, `news`, `trade_press`, `analyst`, `other`), optional `archived_url`. |
| `last_reviewed` | Date the whole record was last checked against its sources. |

## Coordinates

Use the most precise location you can support. If a source gives a street address, parcel, or a
clearly identified site, geocode it (for example with OpenStreetMap Nominatim) and use `site` or
`approximate_site`; say how in `location.note`. Otherwise use the named town or county centroid
with `locality` or `admin2`. Do not guess a parcel from imagery alone.

## Example skeleton

```json
{
  "schema_version": "1.0",
  "id": "example-cloud-springfield",
  "name": "Example Cloud Springfield campus",
  "aliases": [],
  "summary": "Example Cloud plans a multi-building campus on former farmland east of Springfield.",
  "location": {"country": "US", "admin1": "Illinois", "admin2": "Sangamon County",
               "locality": "Springfield", "lat": 39.80, "lon": -89.64, "precision": "locality",
               "source_ids": ["s1"], "note": "Town centroid; the source names the town only."},
  "status": "announced",
  "status_as_of": "2025-05-01",
  "status_source_ids": ["s1"],
  "workloads": ["cloud"],
  "parties": [{"name": "Example Cloud", "role": "developer", "source_ids": ["s1"]}],
  "metrics": [{"metric": "power_capacity", "value": 300, "qualifier": "up_to", "unit": "MW",
               "basis": "planned", "power_scope": "unspecified", "applies_to": "campus",
               "as_of": "2025-05-01", "source_ids": ["s1"]}],
  "milestones": [{"event": "announced", "date": "2025-05-01", "kind": "actual",
                  "source_ids": ["s1"]}],
  "power_sources": [],
  "sources": [{"id": "s1", "url": "https://example.com/news/springfield",
               "title": "Example Cloud announces Springfield campus",
               "publisher": "Example Cloud", "published": "2025-05-01",
               "accessed": "2026-10-09", "type": "company"}],
  "last_reviewed": "2026-10-09"
}
```
