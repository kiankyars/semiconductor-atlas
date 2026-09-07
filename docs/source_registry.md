# Source registry and rights policy

Snapshot date: 2026-07-20. This is an operating registry, not legal advice. Terms and technical
policies must be rechecked before an adapter is enabled and at each material source change.

## Status meanings

- **First wave:** official access and relevant reuse terms have been identified; implement with the
  listed limits and attribution.
- **Evaluation:** useful official access exists, but the adapter or release treatment still needs a
  dataset-specific rights and quality review.
- **Disabled:** do not automate until terms, permission, credentials, or a contract are approved.
- **Prohibited:** do not use by the described method.

Publicly viewable does not mean licensed for bulk collection, model training, or redistribution.
Unknown terms are recorded as unknown, never guessed.

## AI-critical v1 curated-source boundary

Review date: 2026-08-20.

The v1 cohort pins nine official documents and twelve precise evidence fragments. This is a curated
release input, not a general authorization to crawl the publishers. Exact archived bytes are kept
locally for integrity and replay, while the public release carries source metadata, hashes, precise
locators, bounded excerpts, attribution, fragment-verification method and clock, and the
per-document redistribution decision. HTML and JSON excerpt segments are resolved against
normalized text from the locally verified bytes; ASE PDF excerpts require dated manual visual
review. It does not
package `source_snapshots/` payloads.

| Pinned source family | v1 use | Rights and scope rule |
| --- | --- | --- |
| NIST CHIPS award pages for TSMC Arizona, Samsung Texas, and Amkor Peoria | Location-specific project, technology, lifecycle, and announced future-throughput statements | Treat NIST text as credited U.S. government public information while preserving exceptions for embedded or third-party material. V1 conservatively releases metadata and bounded excerpts, not archived pages. A multi-site page must be cited at the same named site subsection as the row; never carry an Austin statement into Taylor, or one project's location into another. |
| Intel and SK hynix official newsroom records and the ASE Kaohsiung sustainability report | Named-facility or campus identity, lifecycle, HBM, leading-edge logic, advanced-packaging, and test evidence | Publisher copyright applies and no blanket redistribution license is inferred. Retain exact bytes locally; publish metadata, hashes, attribution, locators, and short excerpts only. ASE campus evidence remains campus-scoped and cannot be allocated to a plant without separate evidence. |
| Micron Form 10-Q and TSMC Form 20-F via SEC EDGAR | HBM-packaging project and named-facility operating evidence | Public EDGAR access does not make all issuer-authored filing content public domain. Follow SEC fair-access and attribution guidance, retain the recorded underlying-rights decision, and do not treat filing availability as permission to redistribute unrelated exhibits or a full local archive. |

For `leading_edge_logic`, the official source must explicitly characterize the same facility or
project as leading-edge, most-advanced logic, or equivalent. V1 does not infer eligibility from a
numeric node. All nine records retain publication precision and a 2024-or-later date; a retrieval or
archive timestamp cannot substitute for publication evidence.

## Official and open first-wave candidates

The September 7, 2026 [Amkor successor review](amkor_peoria_successor_review_2026-09-07.md)
adds exactly two individually selected Amkor Company News documents under the same local-byte,
metadata-and-short-excerpt boundary. Current robots and website terms checks are retained in the
source packet. The NIST award page was rechecked as review context; its old rates remain visible,
but their applicability to the expanded project is unresolved. Failed IR and municipal access
checks are retained, and those document bodies were not acquired. This review does not authorize
a general publisher crawler or certify complete update coverage.

| Source | Intended use | Access and rights status | Adapter status and rule |
| --- | --- | --- | --- |
| [Taiwan MOENV EMS_S_01](https://data.moenv.gov.tw/dataset/detail/EMS_S_01) | Taiwan source-native facility identities, exact semiconductor industry classes, full addresses, environmental-registry fields, and WGS84 point candidates | **Reviewed and accepted 2026-07-20.** The official catalogue assigns [Taiwan Open Government Data License 1.0](https://data.gov.tw/license), which permits reuse and derivatives with explicit attribution. The dataset is published by the Resource Circulation Administration and declares daily refresh. | **First non-U.S. facility adapter accepted.** The retained full package is filtered locally to exact `industryid` `2611`, `2612`, or `2613` after `industrygroup=261`; its publisher checksum and every distinct same-`emsno` variant replay offline. Environmental-control flags mean registry inclusion only, never operating status. Valid WGS84 coordinates are source points, not site boundaries. Business and factory-registration identifiers remain resolution evidence, not automatic organization or facility merges. The accepted 2026-07-20 snapshot contains 724 source-native facility identities and 798 distinct variants. |
| [Taiwan national registered-factory publication](https://data.gov.tw/dataset/6569) | Independent Taiwan registered-factory identities, exact factory and business registration identifiers, registered addresses, administrative status, industry classes, and principal products | **Reviewed and accepted 2026-07-20.** The Industrial Development Administration publishes a free, no-auth national ZIP under [Taiwan Open Government Data License 1.0](https://data.gov.tw/license). The declared cadence is irregular. The current package is an active-registration roster, not a full history. | **Adapter, source-native import, and reviewed MOENV facility resolution accepted.** The full package is scanned before retaining only exact line-delimited principal-product token `261半導體`. The accepted snapshot has 553 facilities; `生產中` remains administrative registration status only, and the responsible-person field is omitted from derivatives, claims, and releases. Review accepted 142 same-kind facility assignments and deferred one succession conflict. `uniformno` never merges facilities or proves ownership. See `docs/taiwan_factory_identity_gate_2026-07-20.md`. |
| [Taiwan MOF active tax-registration dataset (BGMOPEN1)](https://data.gov.tw/dataset/9400) | Source-native Taiwan tax-unit organization evidence, exact UBN and head-office UBN context, names, registered business addresses, organization type, establishment date, and industry activities | **Reviewed and accepted 2026-07-20.** The Fiscal Information Agency publishes the fixed daily national ZIP under [Taiwan Open Government Data License 1.0](https://data.gov.tw/license), without authentication. It is an active tax-registration population, not legal-entity or closure history. | **Bounded acquisition, source-native importer, and separate relationship gate accepted.** The exact allowlist from the two accepted Taiwan facility snapshots produced 390 matches and 76 misses among 466 UBNs across 1,709,795 business rows. Review accepted 944 exact same-time UBN links only as `registered_tax_unit_reference` claims; they do not establish identity, ownership, parentage, operator, activity, or lifecycle, and conflicting source references may coexist. Capital and invoice-use fields are omitted. See `docs/taiwan_mof_snapshot_audit_2026-07-20.md` and `docs/taiwan_mof_relationship_gate_2026-07-20.md`. |
| [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | U.S.-listed company filings, capex, facility plans, risks, subsidies, customers, and suppliers | Official public APIs and archives; [SEC fair-access guidance](https://www.sec.gov/about/developer-resources) currently limits aggregate automated access to 10 requests/second and requires an identified user agent. Individual filing exhibits can contain third-party material, so no blanket content license is inferred. | **First wave.** Use APIs, feeds, and archives rather than scraping search pages. Cache by accession and content hash; quote minimally in public releases. |
| [CHIPS for America awards](https://www.nist.gov/chips/chips-america-awards) | Official U.S. award recipient, location, amount, expected investment, technology, project, and timeline statements | NIST's [copyright notice](https://www.nist.gov/copyrights-disclaimers) says information presented on its sites is considered public information unless marked otherwise and may be distributed or copied, with credit requested. Linked third-party material and marked exceptions retain their own rights. | **First wave.** Follow the paginated canonical index and exact CHIPS Program Office detail pages. Preserve `proposed` versus `final`, `up to`, and multi-site totals; never invent a per-site allocation or infer construction, completion, or realized capacity. |
| [EPA Facility Registry Service National Single File](https://www.epa.gov/frs/epa-frs-facilities-state-single-file-csv-download) | U.S. registry candidate identities, raw coordinates, program identifiers, and discovery joins | **Reviewed 2026-07-19.** EPA publishes an [official monthly public archive](https://ordsext.epa.gov/FLA/www3/state_files/national_single.zip). EPA's [data license](https://edg.epa.gov/EPA_Data_License.html) says EPA-produced data is public domain by default and provided without warranty; third-party material, if identified, retains its terms. EPA [seal and logo rules](https://www.epa.gov/aboutepa/using-epa-seal-and-logo) prohibit implying EPA endorsement. | **First wave as a candidate source only.** Filter exact NAICS `334413` or SIC `3674`; exclude adjacent codes. Use source family `epa-frs`, source key `epa-frs-national-single:epa-frs-semiconductor-direct-v1`, and entity prefix `epa:frs:`. Each immutable snapshot stores the raw ZIP at `raw/sha256/<digest>.zip`, rescans it, and verifies the derivative hash. Preserve raw NAD83 scalars without creating GeoJSON geometry or claiming a CRS transform. Rows do not establish activity, operation, lifecycle, capacity, or global coverage; absence, reassignment, or Registry ID merge is not closure. Attribute: `Source: U.S. Environmental Protection Agency, Facility Registry Service (public-domain U.S. Government data; no EPA endorsement)`. Do not use an EPA seal or logo. |
| [EEA Industrial Emissions Portal dataset](https://industry.eea.europa.eu/industrial-emissions/dataset) | European industrial sites/facilities, releases, transfers, and permit context | **Reviewed and accepted 2026-07-20.** Version 16 is pinned by [DOI 10.2909/657ac3cb-affa-4295-a4a9-27b4f539adab](https://doi.org/10.2909/657ac3cb-affa-4295-a4a9-27b4f539adab), and its metadata assigns CC BY 4.0 to the European Environment Agency. The complete EEA-reported production-facility hierarchy and NACE functions are retained from the 2.03 GB Access database; the convenient CSV bundle is not used as a completeness denominator. | **Candidate adapter, immutable snapshot, review queue, and importer boundary implemented.** The exact v16 extraction covers 99,275 EEA-reported facilities and produces 108 manual-review leads from exact raw NACE `26.11` or bounded explicit facility/site name terms. No candidate review has been accepted or imported. Queue priority is not classification; only evidence-backed `accept_in_scope` decisions can create source-native facility records. Exact INSPIRE identifiers remain case-sensitive; 2,637 non-exact historical detail keys are never repaired. Confidential fields fail closed. Raw point scalars stay in retained records, but the importer emits no geometry, operating-status, ownership, output, or capacity claim. The separate [v16 spatial companion](https://doi.org/10.2909/3bbf28cb-70e8-4073-8fe9-8c1d9c513f52) declares EPSG:4326, but tabular-spatial correspondence is unverified. See `docs/eea_industrial_v16_acceptance_audit_2026-07-20.md`. |
| [GLEIF LEI data](https://www.gleif.org/en/lei-data/access-and-use-lei-data) | Legal-entity identifiers, names, jurisdictions, and disclosed accounting parents | **Reviewed 2026-07-19.** GLEIF's [data terms](https://www.gleif.org/en/meta/lei-data-terms-of-use) state that Access Service data is CC0. The official API is based on the current Golden Copy; pinned Golden Copy and delta files are available in JSON and XML. | **One reviewed Level 1 pilot accepted.** The fetcher archives exact API bytes for a strict exact-LEI allowlist, requires one Golden Copy publication, and replays a link-free derivative offline. The importer re-verifies a separate review plan and preserves claims on the GLEIF-native entity; only an explicit reviewed `match` creates an assignment. The accepted scope is one TSMC Arizona LEI, not broad organization or facility coverage. Name or fuzzy search creates candidates only. Registration status is not operating status. Direct and ultimate parents are accounting-consolidation relationships, not facility ownership. See `docs/gleif_adapter_plan.md`. |
| [OpenStreetMap planet and diffs](https://planet.openstreetmap.org/) | Facility and industrial-land leads, roads, buildings, utilities, and candidate geometry | OSM data is [ODbL 1.0](https://www.openstreetmap.org/copyright) with attribution and share-alike obligations. Public API and [Nominatim](https://operations.osmfoundation.org/policies/nominatim/) services are not bulk-ingestion endpoints. Semiconductor tagging is not a controlled ontology; values such as [`product=*`](https://wiki.openstreetmap.org/wiki/Key:product) are contributor text. | **First wave as a candidate seed only.** Use planet or regional extracts and replication diffs for production; a bounded saved Overpass result is acceptable for a seed fixture. Preserve `© OpenStreetMap contributors`, source object/version, and license. Treat tags as leads, not proof of identity, operation, process, or capacity. |
| [Wikidata dumps](https://www.wikidata.org/wiki/Wikidata:Data_access) | Organization aliases, identifiers, headquarters, and discovery links | Structured Wikidata content is [CC0](https://www.wikidata.org/wiki/Wikidata:Licensing). | **First wave.** Prefer dumps and bounded SPARQL queries. Retain references where present; Wikidata is a discovery and resolution layer, not ground truth. |
| [Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu/) Sentinel-1 and Sentinel-2 | Optical and radar change detection | The [CDSE terms](https://dataspace.copernicus.eu/terms-and-conditions) state Sentinel data is available on a free, full, and open basis under the Sentinel legal notice. Portal content and third-party datasets have separate terms. Public derivatives require the prescribed Copernicus source notice. | **First wave.** Query Sentinel items through supported catalog/data APIs, store acquisition IDs, and use `Contains modified Copernicus Sentinel data [year]` where applicable. Do not copy unrelated portal content. |
| [USGS Landsat](https://www.usgs.gov/landsat-missions/landsat-data-access) | Historical construction context and thermal corroboration | USGS states Landsat data is in the [public domain](https://www.usgs.gov/faqs/are-landsat-data-cloud-still-considered-be-within-public-domain), permission is not required, and source acknowledgement is requested. | **First wave.** Retain product IDs and cite USGS and the relevant dataset DOI. Thermal signal is corroboration, not proof of production. |
| [Microsoft Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints) | Baseline building geometry and new-shell discovery context | Dataset documentation states CDLA Permissive 2.0. Underlying imagery is not included and footprints have geographic and vintage limitations. | **First wave.** Record partition, release, confidence, and license; treat machine detections as candidates. Do not imply rights to underlying imagery. |
| [Google Open Buildings](https://sites.research.google/gr/open-buildings/) | Building-footprint context in covered regions | Google offers the dataset under CC BY 4.0 or ODbL 1.0 at the user's choice. | **First wave.** Select and record one license path per release, retain attribution, confidence, and dataset version, and review detections before canonical use. |

## Official sources requiring adapter-specific review

The [September 7 NIST expansion review](../review_plans/2026-09-07-nist-source-expansion.json)
approves exact repeatable checks of the government TSMC Phoenix and Samsung Texas award pages
under retained, hash-bound NIST access and rights policies. It does not authorize their issuer
newsrooms. The whole-page scopes include other fabs and, for Samsung, Austin and Taylor R&D;
these remain source-version review candidates, not automatically attributed facility changes.
Raw pages stay local, with marked copyrighted and third-party material excluded from this
publication scope. See [monitoring boundaries](nist_monitoring_expansion.md).

The separate [NIST index review](../review_plans/2026-09-07-nist-index-discovery.json) approves only
the current [news index](https://www.nist.gov/chips/chips-news-releases),
[Program Office awards index](https://www.nist.gov/chips/chips-program-office-awards), and bounded
same-root sequential pagination after both policy checks pass. The [discovery pilot](nist_discovery.md)
retained 64 links across six pages, including 14 company-name matches. This does not grant access to
the linked documents, the older search-based archive, issuer sites, images or attachments. Raw
index pages remain local. A matched company name is not a matched baseline facility, and a completed
current page chain is not complete publisher history. The daily task does not yet run this collector.

September 7, 2026 discovery review: Samsung's [June 10 issuer article](https://semiconductor.samsung.com/sas/local-news/samsung-austin-semiconductors-two-campuses-inject-10-9b-into-central-texas-economy-in-2025/)
is a Taylor construction lead, not accepted baseline evidence. Its [US Austin website terms](https://semiconductor.samsung.com/legal/)
limit the stated grant to personal display/use and require prior written consent for other use.
The `semiconductor.samsung.com` collector remains disabled pending an appropriate rights decision;
public visibility alone does not activate it. No raw article was retained or cohort claim changed.
Pursue independently reviewed governmental evidence or permission before this acquisition. Any
later review must distinguish the first fab and office activity from the baseline's two-fab project
aggregate; neither office occupancy nor expected opening establishes chip production.

The same discovery pass checked SK hynix's [newsroom terms](https://news.skhynix.com/en/terms-of-use/)
(page last-modified label March 7, 2025). They expressly restrict automated monitoring and copying,
with personal/non-commercial exceptions that do not establish this project's publishing rights.
Do not enable a newsroom collector or treat the earlier v1 excerpt decision as approval for new
acquisition. Future publication that carries inherited newsroom excerpts needs a renewed
source-specific rights decision; immutable historical artifacts are not rewritten by this finding.
The [1Q26](https://news.skhynix.com/en/q1-2026-business-results/) and
[2Q26](https://news.skhynix.com/en/q2-2026-business-results/) releases remain discovery leads only.
Their M15X investment and schedule language does not by itself prove a realized production ramp,
and company-wide HBM shipments cannot be allocated to M15X. No new raw documents were archived or
facility claims accepted in this discovery pass. Use an independently approved disclosure source
or obtain permission before acquiring evidence for a successor.

| Source | Intended use | Access and rights status | Adapter status and rule |
| --- | --- | --- | --- |
| [Overture Maps](https://docs.overturemaps.org/) | Base geography, buildings, places, and source-linked identifiers | Overture publishes GeoParquet/STAC releases, but attribution and license can vary by theme and contributing source; see its [attribution guidance](https://docs.overturemaps.org/attribution/). | **Evaluation.** Retain record-level source and license metadata. Do not flatten mixed-license data into one assumed license. Mirror releases needed for reproducibility because hosted retention is limited. |
| [EPA ECHO data downloads](https://echo.epa.gov/tools/data-downloads) and [web services](https://echo.epa.gov/tools/web-services) | U.S. air, water, waste, compliance, emissions, and regulated-facility evidence | EPA publishes bulk downloads and public services; service documentation directs large-volume users to downloads. External geospatial fields may be unverified. No license beyond the published dataset terms is invented. | **Evaluation.** Confirm dataset-specific public redistribution treatment before activation. Use bulk files for national refreshes and distinguish permit limits from measured use or production. |
| [USAspending API](https://api.usaspending.gov/) | U.S. federal awards, loans, grants, and contractor or subsidy leads | Official no-auth API and bulk data are available. No blanket license is asserted here. Award descriptions may be incomplete or duplicated across modifications. | **Evaluation.** Verify public-release treatment, deduplicate award modifications, and treat awards as funding evidence rather than construction proof. |
| [Tenders Electronic Daily data reuse](https://ted.europa.eu/en/help/data-reuse) | EU procurement notices for construction, tools, utilities, and services | TED offers daily XML and APIs under its [legal notice](https://ted.europa.eu/en/legal-notice); notices can include third-party attachments and personal data. | **Evaluation.** Implement only after field-level attribution, attachment, retention, and personal-data rules are tested. Procurement is not delivery. |
| [NASA Earthdata](https://www.earthdata.nasa.gov/engage/open-data-services-software/data-use-policy), including Black Marble candidates | Night-time light and thermal corroboration | NASA's policy says NASA-led mission data without a marked restriction is CC0 and requests citation, but every product must still be checked for a specific restriction and citation. | **Evaluation per product.** Enable only a pinned product/version after its data-use record is stored. Night lights cannot independently establish commissioning or output. |
| Company investor-relations sites and official subsidy, permit, environmental, utility, water, land, and planning portals | Facility-specific statements, permits, schedules, resource limits, and project milestones | Rights, robots policies, APIs, authentication, document copyright, and personal-data rules vary by publisher and jurisdiction. | **Evaluation one adapter at a time.** Prefer official APIs, feeds, or offline documents. Record query coverage; a failed or unavailable portal is not negative evidence. |
| [OpenDART](https://opendart.fss.or.kr/) and [EDINET](https://disclosure2.edinet-fsa.go.jp/) | Korean and Japanese regulatory filings | Both provide official keyed APIs. Registration, rate, copyright, redistribution, and language-specific terms require a reviewed implementation; OpenDART's terms do not imply an unrestricted content license. | **Disabled pending terms review and credentials.** Manual source-linked research may be curated when lawful. |

## Disabled or prohibited source classes

| Source class | Status | Rule |
| --- | --- | --- |
| Commercial satellite imagery from Planet, Airbus, Vantor/Maxar, ICEYE, Capella, or similar providers | **Disabled pending contract.** | A contract must explicitly cover intended computer vision, derived coordinates and features, retention, contractors, public outputs, and model weights. Store restricted pixels separately. |
| Customs, bills-of-lading, shipment-intelligence, equipment-install-base, and paid market datasets | **Disabled pending license.** | Public search interfaces are not scrape authorization. Procure a license covering database construction, modelling, retention, and derived/public outputs. |
| Job boards, professional-network profiles, social media, traffic/mobile-location data, and employee-level data | **Disabled pending source-specific permission and privacy review.** | Prefer aggregate official company feeds or lawful licensed data. Do not publish personal profiles or track named employees. |
| Google Maps, Google Earth, Bing Maps, or other consumer basemap tiles and imagery | **Prohibited for automated scraping, bulk extraction, or model training without an express license.** | A visual basemap may be used only under its presentation terms. Open footprint datasets do not grant rights to the proprietary imagery from which they were derived. |
| SemiAnalysis, SEMI, TechInsights, TrendForce, Omdia, and other competitive products | **Benchmark or licensed input only.** | Never scrape. Comparison requires a purchased or otherwise authorized snapshot and a common entity, capacity-basis, geography, and date ontology. |
| Leaks, credential-gated systems without authorization, unlawfully acquired imagery, or material obtained by bypassing access controls | **Prohibited.** | Do not acquire, retain, transform, or publish. |

## Activation checklist

Before changing a source from evaluation or disabled to first wave, record:

1. official access endpoint and permitted automated method;
2. current terms URL and review date;
3. license, attribution, rate, registration, retention, training, derivative, and redistribution rules;
4. personal-data and sensitive-infrastructure treatment;
5. source family and expected correlation with other sources;
6. stable IDs, refresh cadence, coverage, and deletion or correction behavior;
7. offline replay fixture and idempotency test; and
8. public release filter and required attribution test.
