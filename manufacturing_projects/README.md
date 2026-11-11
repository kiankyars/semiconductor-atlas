# Manufacturing projects

Source-linked records of semiconductor manufacturing projects: new wafer fabs, fab
expansions and packaging and test plants announced, built, opened, paused, cancelled or closed since
2020. Each record lives in [`records/<id>.json`](records/) and every statement in it cites the
public sources that support it.

These records are not the evidence-archived release in [`web/data/2026-09-07`](../web/data/2026-09-07/).
That release keeps an exact copy of every source and a hash of every quoted passage. These records
link to live sources instead: they keep each source's URL, publisher, publication date and the
date it was read, and all text is written in our own words. Sources are linked, not copied.

Check records with:

```sh
python3 scripts/validate_manufacturing_projects.py     # every record and the links between them
python3 scripts/validate_manufacturing_projects.py manufacturing_projects/records/<id>.json
```

## What a record is

A record is one facility as its operator names it: a numbered fab, a named plant, or a fab
module the operator reports separately (`identity_scope: named_facility`). When the operator
announces a multi-fab project without naming separate fabs, the record is that project
(`project`). When sources report several fabs together as one site, the record is the campus
(`campus`). Phases stay inside one record (`phase_label` on milestones and figures) unless the
operator names them as separate facilities. A record never splits an announced aggregate into
unnamed fabs and never merges separately named facilities because they share a city.

## Inclusion

The dataset includes facilities that were announced, under construction, opened, expanded
with a new building or fab phase, paused, cancelled or closed on or after 1 January 2020, and
that are one of:

- a front-end wafer fab (silicon, silicon carbide, gallium nitride or other compound
  semiconductors, any wafer size), including a new fab building, module or phase at an existing
  site; or
- a packaging, assembly or test plant with planned investment of at least USD 500 million (or
  the equivalent in its source currency), or one its operator describes as advanced packaging
  (2.5D/3D integration, HBM stacking, fan-out or chiplet packaging).

Out of scope: research-only labs and prototyping lines without stated commercial production,
equipment, wafer, substrate, photomask, gas and chemical plants, display and solar fabs, design
centres and offices, and tool upgrades or node conversions inside an existing building.

Coverage is a selection, not a census. Projects in regions with little English or
company-language disclosure, and projects private companies do not announce, are
under-represented.

## Fields

| Field | Meaning |
| --- | --- |
| `company` | The parent group that controls the facility as of the status date, by its short name (TSMC for JASM and ESMC). For a joint venture, the holder of a majority share when a cited source states one, otherwise both partners ("STMicroelectronics and Sanan"). |
| `operator` | The legal entity that runs (or will run) the facility, as the sources name it in English, matching the `parties` entry with the operator role. Joint ventures name the venture. |
| `parties` | Owners, joint-venture partners, technology partners (licensors of the process), governments, customers and builders (developers or contractors), each with its source. `share_pct` only when a source states it. |
| `facility_type` | `wafer_fab`, `packaging_test` or `wafer_fab_and_packaging`. |
| `segments` | What the facility makes, from the list in the validator (logic, DRAM, HBM, NAND, analog, power, SiC, GaN, RF, image sensors, MEMS, photonics, advanced packaging, assembly and test). |
| `process_nodes` | Process technologies as the source names them (for example `N2`, `Intel 18A`, `28nm`, `1-gamma DRAM`). Never inferred. |
| `wafer_sizes` | 100, 150, 200 or 300 mm, only when a source states it. |
| `location` | Country (ISO code), first-level region, locality, the named site or industrial park, and a published street address when an official source gives one. Regions use one English form per country (Japanese prefectures without "Prefecture", Taiwan's "Kaohsiung City" or "Miaoli County", Korea's "Gyeonggi Province"). The locality is the city or town; for cities that are themselves first-level regions (Shanghai, Tianjin, Kaohsiung, Tainan, Singapore) it is the city name, with districts in the note or address. `site` holds a named site, park or campus, never a district or a description. |
| `status` | See below. `status_as_of` is the publication date of the newest source supporting the status. |
| `metrics` | Investment, government incentives, capacity, cleanroom and site area, and jobs. |
| `milestones` | Dated events. `planned: true` marks a target or expectation; `planned: false` an event a source reports as having happened. |
| `sources` | Every source with its title, publisher, URL, type, publication date and the date it was read. |
| `related` | Links between records on the same campus or between phases and successors. Links are two-way. |
| `archived_record` | The stable key of the same facility in the evidence-archived release, where one exists. |
| `registry_records` | Reviewed links to rows of the [facility registry](../web/data/registry-2026-07-20/): the U.S. CHIPS award page covering the project (`award_page`), or a registry record for the facility (`same_facility`) or for a site that includes it (`same_site`). |
| `review` | When and how the record was independently reviewed, and what the review changed. |

### Statuses

| Status | Meaning |
| --- | --- |
| `announced` | Publicly announced or approved, with no cited evidence of construction. |
| `under_construction` | Cited groundbreaking, construction or equipment installation; no production yet. |
| `partially_operating` | At least one recorded fab or phase produces while another is still being built or is only announced. |
| `operating` | Producing, with no cited ongoing construction of the recorded scope. |
| `paused` | Work reported as halted or postponed without a new date, and not cancelled. |
| `cancelled` | Withdrawn or cancelled by the operator, or its incentive agreement ended without a replacement plan. |
| `closed` | A facility that produced and has shut down. |
| `transferred` | Sold to another operator, whose record (linked as the successor) carries the facility from the sale on. |

The validator enforces the combinations: production statuses need a reported production event,
`under_construction` needs reported construction and no reported production, `announced` allows
neither, and the latest facility-wide pause, cancellation or closure must match the status unless a
later reported event (resumption, construction, production or a change of owner) supersedes it.

A groundbreaking does not make a facility operating, and an announced production target is a
planned milestone, not an achievement. A missed target stays in the record as a planned
milestone with its source.

Use `construction_progress` when a dated source reports physical construction underway but
does not establish when it started. Its date is the report date, not an inferred start date.
Capacity-growth targets belong in metrics or notes, not production-start milestones.

### Figures

Amounts keep the source's currency and are never converted. `basis` says what the figure is:
`planned` (a stated plan or target), `committed` (a signed agreement or formal decision),
`preliminary_award` (a preliminary or non-binding government award), `awarded` (a final award
or grant decision) or `reported` (an actual figure the source reports). `applies_to` says whether
the figure covers this facility, one phase, the whole announced project, or several sites
(`multi_site`, with a note naming what it covers). Capacity always says its basis in the note,
such as 300 mm wafer starts per month. `qualifier` keeps the source's precision: `exact`,
`approximately`, `up_to`, `at_least`, `more_than`, or a `range` with `value_max`.

### Dates

Dates keep the precision the source gives: `2027`, `2027-H2`, `2027-Q3`, `2027-09` or
`2027-09-15`.

## Sources and research rules

- Primary sources first: operator announcements, annual reports and securities filings,
  government and economic-development agencies, and regulatory filings. News and trade press
  corroborate or fill gaps where no primary source exists. Reference works are never the only
  source for a statement.
- Every statement cites the sources that support it as they read today. A source that does not
  support a statement is removed from it, and every listed source supports at least one statement.
- Write every summary and note in your own words. Titles stay as published. Do not copy sentences
  or long phrases from sources, and attribute superlatives ("which Bosch called its largest
  investment") rather than stating them as fact.
- Label every source by who published the copy that was read. A press release read in a trade
  publication's republication is cited as that publication (`trade_press`); a filing read from a
  hosting site other than the exchange or the company names the host in `publisher`; a news story
  read on an aggregator names the original outlet and the aggregator ("Securities Times (via
  10jqka)"). Prefer the original when it can be read.
- One publisher name and type per outlet across all records: company, government and regulator
  sources are primary; outlets that cover the chip and electronics industry are `trade_press`;
  general and business news is `news`. SEC filings name the filing company as publisher.
- Do not geocode. Coordinates stay null unless a cited source gives coordinates for the facility
  itself; a city name is not a facility point.
- Do not name individual people; refer to roles (for example, the state governor).
- Do not work around paywalls, logins, robots.txt rules or blocked pages. Use another source.

## Review

A second, independent review re-opens every source, checks each statement against it, and corrects
or removes what the source does not support. The `review` field records the date, the method and
what changed. Report an error or a missing project through the
[issue tracker](https://github.com/kiankyars/semiconductor-atlas/issues/new/choose).
