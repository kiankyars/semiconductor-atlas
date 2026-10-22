# Semiconductor Atlas facility registry

Registry records with semiconductor codes and U.S. CHIPS award pages, republished from retained,
hash-verified evidence in the local Semiconductor Atlas core. This bundle adds no new observations.

| Source | Public rows |
| --- | --- |
| Taiwan MOENV environmental registry (every record with exact industry code 2611, 2612 or 2613) | 724 |
| Taiwan IDA registered factories (records with exact product token 261 in the in-production factory roster, without a reviewed MOENV match; 2 sole proprietorships withheld) | 410 |
| NIST CHIPS for America final-award pages linked from the chips-america-awards index in the 17 July 2026 snapshot (15 of them in 6 multi-page programs) | 28 |
| EEA industrial reporting, accepted in scope after AI-assisted review | 15 |
| U.S. EPA FRS unverified leads (exact NAICS 334413 or SIC 3674), in `us_epa_frs_leads.*` | 3059 |

How to read it:

- Taiwan rows are every record carrying the exact code or token in the archived registry
  packages, with no per-record review of whether it is a semiconductor facility. Some MOENV rows
  coded 2611 are construction works or firms in other trades. The MOENV dataset includes
  deregistered historical entities (see `environmental_control`). The IDA package is the published
  in-production factory roster (生產中工廠清冊); it also lists a few factories under stop-work orders,
  but no other registration statuses. Of its 553 factories with the
  261 token, the registration statuses are 生產中 553
  ("in production" is an administrative status, not observed operation).
  410 appear as separate rows,
  141
  appear inside 142 reviewed links on MOENV rows, and
  2 sole proprietorships are withheld.
- Reviews named here were AI-assisted: the MOENV-to-IDA identity matches were decided by a Codex
  agent (`review_plans/2026-07-20-taiwan-moenv-factory-review.json`; its reasons say "manual
  review", which was the agent's review), and the EEA scope admission was a Codex-assisted
  collaborative review (`docs/eea_scope_admission_2026-09-07.md`), not independent human sign-off.
- Rows are registry records (`record_type` `registry_record`) or NIST award pages
  (`award_project`), not a census of distinct plants. Several MOENV control numbers can belong to
  one plant, and a registered factory can match more than one MOENV row.
- `registry_classification` is a Semiconductor Atlas label for the registry's classification:
  an English name for the MOENV industry code, "Semiconductors (registered principal product)" for
  IDA rows, and the EEA NACE activity name. `registry_classification_code` holds the registry's own
  values: the MOENV code and Chinese label, every product an IDA factory registered in the
  registry's order (including unrelated products), or the NACE code. Both are null for NIST rows.
  A code is a registration fact, not what a site does.
- `locality` is the MOENV township field for MOENV rows and the IDA administrative-area text after
  the county for IDA rows, which usually adds the village (里 or 村).
- Registry membership is not proof that a facility operates, produces semiconductors at any scale,
  or is owned by the named company. Environmental-control listings and factory-registration status
  are administrative registry facts.
- `environmental_control` gives each MOENV category (air, water, waste, toxic, soil) exactly as
  published: its flags (1 = listed), any release dates (release from that category), and
  `variant_states`, which pairs each registry variant's flag with that variant's release date or
  its absence. `registry_status` summarises them literally and says when registry variants
  differ: a release in one variant but not another, different release dates ("released A or B
  (registry variants differ)"), or a flag of 0 in one variant. A release date is not closure, and a
  listing is not operation.
- Coordinates appear only where the MOENV registry reports one consistent WGS84 point for the
  row. They are source-reported points, not surveyed boundaries. No location was geocoded.
- MOENV rows list the registered factory they match only where a reviewed identity assignment
  exists (142 links). Each link keeps the factory's own registered
  name, business number, address and products beside the MOENV values.
  `differs_from_environmental_registry` lists a name or business number that disagrees (names are
  compared after Unicode compatibility normalization), and `missing_from_environmental_registry`
  a business number MOENV does not record (19 links have
  either). Addresses are kept side by side but not compared, because the two registries write
  addresses differently. Matched factories appear only inside these links, not as separate rows.
- Only MOENV rows carrying a factory number that matched the IDA roster, exactly or after the
  documented legacy-certificate normalization (for example 95-C00128-00 to 95C00128), were
  candidates for the reviewed match, so "without a reviewed match" does not mean distinct.
  162 registered-factory rows share a name
  (compatibility-normalized, spacing ignored) and a business number with an MOENV row; they carry
  an `unreviewed_same_name_and_business_number` link and are flagged, not merged.
- NIST `award_statements` keep every captured statement verbatim (recipient, award-card and
  Project Summary description, project type, technology, direct funding, expected capital
  expenditure, jobs, timeline) with its page section,
  any amounts and qualifiers derived from it, and its scope: `site` (a multi-page program member's
  own site section or award card), `award` (a page that is not part of a program; it may name more
  than one location) or `program` (a program overview, shared across its member pages and never
  allocated to a site; a page's Key Facts summary is program scope only when every member page
  carries the same paragraph, and otherwise `page` scope: that member page's own paragraph, which
  may describe more than one site). Each statement lists the `pages` it was read from, so a program statement
  shows which member page said it. Rows of one multi-page program share a `program_key` and give
  `program_member_pages`; this groups pages that share one NIST project overview and is not a count
  of awards (NIST describes separate awards to subsidiaries inside some programs). `source_anomalies` repeats the release's own data-quality
  flags (Infinera's "$456 billion" is flagged as an implausible amount; it is kept, unchanged,
  beside the "$456 million" the other page states). Recipients come only from a page's Recipient
  field. Amounts are never summed and are not evidence of construction or capacity. The source
  release did not capture every NIST field. Missing labels include "Technologies" (plural),
  "Project" (Micron Boise and Manassas site descriptions), "Total Project Cost", "Available Loan"
  and "CHIPS Workforce Funding", so a missing statement means "not captured", not "not stated by
  NIST". Coverage is the final-award pages linked from NIST's chips-america-awards index in the
  snapshot dated 17 July 2026 (downloaded by 01:54 UTC on 18 July). Proposed-award pages, CHIPS
  Program Office pages missing from that index, and NIST's NAPMP, R&D Office and SBIR awards are
  not included. NIST `source_date` is the award page's last-updated date in the captured version.
- EEA `latest_reporting_year` is the facility's most recent reporting year in version 16 of the
  EEA dataset, not an operating date. EEA addresses join the published street, building-number and
  postal-code fields with semicolons.
- EPA FRS rows are discovery leads only and carry no coordinates.
- 2 registered-factory rows for sole proprietorships
  and 6 EPA leads named after private individuals or
  residences are omitted because they can identify individuals.

Reuse terms for each source are linked in `ATTRIBUTION.md` and in `manifest.json` (`licenses`).

The JSON Lines files hold values exactly. In the CSV files, a cell that would start with `=`, `+`,
`-` or `@` gets a leading apostrophe so spreadsheets do not evaluate it (one EPA postal code,
`-0`, is affected).

Files: `records.jsonl` and `records.csv` (one row per registry record or award page),
`registry_points.geojson` (MOENV rows with a source-reported point, each carrying its geometry
basis, source link and date), `us_epa_frs_leads.jsonl` and
`.csv`, `ATTRIBUTION.md`, and `manifest.json` with SHA-256 hashes for every file.
