# Complete retained NIST 2024 PMT body review

This is a manual, outcome-exposed review of all 65 complete retained articles in
the [frozen current archive population](nist_2024_announcement_population.md).
The population is the current NIST CHIPS-filtered archive, not a global or
historically complete announcement census. Source publication dates do not
prove when these exact bytes first became available.

The inclusion rule is a newly announced, signed, nonbinding manufacturing
incentive preliminary memorandum of terms (PMT), described in an article dated
2024. Commercial semiconductor fabrication, packaging, materials and equipment
manufacturing qualify. Final awards alone, research-only programs, workforce
initiatives and governance news do not. Mixed articles are reviewed within the
body rather than assigned one indiscriminate project scope.

## Complete source and scope accounting

| Article decision | Count |
| --- | ---: |
| New manufacturing PMT only | 24 |
| Final award plus a separately new manufacturing PMT | 1 |
| Final award only | 11 |
| Research only | 17 |
| Other, including workforce and governance | 12 |
| All reviewed articles | 65 |

The 25 PMT-bearing articles contain 45 included source-native manufacturing
groups. Three additional research scopes are retained but excluded: Samsung's
Taylor R&D fab, Intel's Hillsboro technology-development branch, and Entegris'
geographically unspecified U.S. R&D program. Thus 48 scope rows are not 48
facilities, independent projects, or outcome cases. No component counts are
summed into a facility denominator.

Important allocation decisions include:

- The December Micron article finalizes Idaho/New York funding but separately
  announces a new Manassas modernization PMT. Only Manassas enters from that
  article; Idaho/New York enter from their April PMT article.
- GlobalWafers' July PMT includes three manufacturing components, including an
  existing Sherman epitaxy-facility conversion alongside two new facilities.
- Entegris' two explicitly described Colorado Springs phases remain distinct;
  neither is assumed to be an independently named building.
- TI's two unnamed Sherman fabs stay grouped with multiplicity two. Micron's
  first two Clay fabs, Samsung's two Taylor logic fabs and Intel's unnamed fab
  groups likewise retain explicit multiplicity without invented identities.
- Intel's Chandler new-fab construction and existing-fab modernization remain
  separate interventions. Its Rio Rancho scope explicitly includes two fabs.
- GlobalFoundries' new Malta fab, existing Malta expansion and Burlington
  revitalization remain three groups. The shared Malta timing statements are
  associations with both projects, not two independent assertions or quantities.
- HP includes commercial silicon-device manufacturing alongside R&D. Equipment
  manufacturing at Edwards Vacuum and lithography materials at Corning are
  included without relabeling them as wafer fabs.

## Timing and conditions

Only three included groups have calendar-normalizable manufacturing targets in
these retained original PMT articles:

| Source-native group | Exact event | Source timing | Reviewed representation |
| --- | --- | --- | --- |
| Bosch, Roseville | First-chip production on 200mm wafers | Starting in 2026 | 2026 calendar-year interval; not HVM |
| TSMC, Phoenix first fab | High-volume production | By the first half of 2025 | Deadline through 2025-06-30; no invented lower bound |
| SK hynix, West Lafayette | Mass production | Second half of 2028 | 2028-07-01 through 2028-12-31; no midpoint |

All 42 other manufacturing groups remain in the population. Empty timing
arrays mean no manufacturing-timing assertion was identified in that article,
not an absent project or zero capacity. Other groups have relative, vague or
undated timing that does not receive invented calendar bounds. RVM's first
wafer shipment in early 2025 and final completion by mid-2025 are different
events. Rocket Lab's relative production-growth horizon, Polar's relative
capacity-doubling horizon and the joint Malta growth statements are not
commissioning dates. Investment, employment, childcare and sustainability dates
are separately identified as non-target context.

Amkor's original PMT supplies no end-2027 calendar target. Infinera's original
PMT supplies no summer-2026 production target, Hemlock's no 2028 startup target,
and TSMC's original PMT supplies no second-fab production date. Later award or
detail-page guidance must not be backfilled into these articles.

All manufacturing PMTs retain general funding qualifications. Proposed awards,
due diligence, negotiations and milestone-dependent disbursements are not
automatically physical prerequisites for production. Wolfspeed's private-capital
and balance-sheet conditions remain specific funding conditions; TSMC's
third-fab technology choice depends on customer demand. Neither is marked
fulfilled by this review.

## Provenance and verification boundaries

The [review manifest](../review_plans/2026-09-09-nist-pmt-body-review-v1.json)
pins three manual review fragments, the earlier index/retention rosters and the
complete narrative packet. Every article, project, timing and condition record
references exact numbered blocks from the same retained article. Full bodies,
complete narrative spans and all 942 block spans remain hash-bound. The narrative
packet is a static HTML text projection, not a browser rendering or CSS/JavaScript
visibility audit; overlapping block context is not independent evidence.

All 65 retained articles have October 6, 2025 modification metadata alongside
their 2024 publication metadata. Publication metadata agrees with the visible
article and index dates. The modification timestamps do not establish whether
content changed or only publishing infrastructure changed. These are September
2026 retained bytes, not authenticated 2024 snapshots. Known outcomes were
already exposed before this population review; no blind evaluation is claimed.

The read-only verifier reparses all four retained archive pages and reconciles
all 89 article spans, displayed pagination totals, titles, URLs, summaries and
dates before selecting the 65 articles. It then checks capture identity and
chronology, complete narrative/block membership, all 65 decisions, project
inclusion, evidence allocation, nonbinding conditions and timing field shapes.
Semantic interpretations, complete project enumeration and calendar normalization
remain manual review; the verifier does not independently prove them. Shared
timing rows are reported as project associations and distinct evidence-bound
statement keys, neither of which is an independent-outcome denominator.

The earlier zero-adjudication index and retention snapshots remain unchanged.
No core claims, numeric capacity, physical realizations, forecast scores, fitted
models, runtime-routing transitions or public releases result from this review.

## Reproduction and validation

The 4,099-byte review manifest has SHA-256
714ddc8a245efe714294241d0b5fc493d956168f6071cf9164b3f1c79b0a1aec.
Run from the isolated repository checkout with retained local inputs; this is
not an installed-wheel command and performs no acquisition or writes:

    python3 scripts/verify_pmt_population_review.py \
      --review review_plans/2026-09-09-nist-pmt-body-review-v1.json \
      --review-sha256 714ddc8a245efe714294241d0b5fc493d956168f6071cf9164b3f1c79b0a1aec \
      --reference-root .

The executed check verified 77 files totaling 8,412,257 bytes, all 65 article
decisions and 942 narrative blocks. The 45 manufacturing groups contain 15
project-timing associations referring to 13 distinct evidence-bound statements;
the two differences are the shared Malta statements. Thirty-four groups contain
no identified timing assertion at all. There are three calendar-target groups
and zero scored outcomes.

The local receipt is
`artifacts/2026-09-09-nist-pmt-population-verification-v1.json`, 12,221 bytes,
SHA-256 3d3fcc5b256ac27b9d3964889d39ddf2453349307497931dc79ea58f8187ba36.
It records the actual verification time and verifier hash, not a frozen complete
interpreter/runtime or independent semantic judgment.

Twenty-eight new synthetic regressions cover complete article/index membership,
index-source corruption, publication-date conflict, dropped narrative blocks,
source mutation, cross-article evidence, funding-condition preservation, unnamed
component multiplicity, research exclusions, shared timing counts and timing
field shapes. They do not independently judge the source's meaning. The final
core suite ran 1,727 tests in 256.765 seconds: 1,726 passed and one expected optional
local-source-data test was skipped. All 12 web tests and the 70 focused existing
source-guidance/frozen-runtime tests passed; offline sdist and wheel builds passed.
Parent protected files, the current isolated database, existing prospective
comparator and earlier source-guidance report still match their pinned hashes.

## Next evidence gate

Review later primary evidence against the complete 45-group manufacturing
population, retaining unknown, delayed, cancelled and unmatched scopes. An award
is not production, an undated expectation is not attainment, and a missing later
report is not nonoccurrence. Distinguish first chips, first shipment, construction
completion, commercial production and HVM when matching events. Shared award,
campus, phase and project dependencies must remain visible in any later diagnostic
or evaluation split. Do not fit a forecast or claim predictive improvement from
this population inventory alone.
