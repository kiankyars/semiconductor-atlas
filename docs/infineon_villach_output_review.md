# Villach produced-wafer-area evidence

The September 8 review retains a five-year, site-specific output series from Infineon's
2025 environmental statement. A subsequent schema-8 admission now retains all five as qualified
source quantities, not installed capacity. [The original source review](../review_plans/2026-09-08-infineon-villach-output-source-review.json)
binds acquisition, source bytes, fiscal periods, chart coverage, definitions and remaining gates.

## What the source reports

Page 39 labels all five annual totals in **million square centimeters of produced wafer area**:

| Infineon fiscal year | Reported reference |
| --- | ---: |
| 2020/21 | 462.5 |
| 2021/22 | 488.9 |
| 2022/23 | 517.8 |
| 2023/24 | 524.0 |
| 2024/25 | 505.5 |

The accounting scope is the Villach site, not Austria-wide operations or the multi-country
One Virtual Fab network. Pages 2 and 65 establish October 1 through September 30 fiscal years.
The numbers are publisher references with unspecified physical precision, not exact measurements
certified by this project. The source cautions that wafer area does not account for process-layer
counts and is not fully comparable across production mix. Diameter-segment bar heights have no
numeric labels and were not digitized. No quarterly, wafer-count, yield, utilization, qualification
or capacity estimate is derived.

Main-agent PDF review covered complete pages 2, 24, 25, 38, 39, 65 and 68 using Poppler 26.08.0.
This was necessary: `pdftotext -layout` omitted the chart's numeric labels. The colored frame
places the chart in the report's EMAS portion. The report includes a March 19, 2026 validator
statement, but this is not a separately obtained assurance report, registry confirmation or
independent wafer-production measurement.

A second agent independently rendered the scope, site-fact, chart and assurance pages and
confirmed all five total labels. It also verified the three acquisition ledgers, ten-file
capture inventory and four retained policy-dependency copies. This checks source interpretation
and retention, not independent manufacturing truth. Earlier fiscal-year endpoints apply the
publisher's glossary convention; only FY2024/25 endpoints are explicitly printed on page 2.

## Retention and access

The [issuer PDF](https://www.infineon.com/row/public/documents/regions/austria/2025-uwe-de-web.pdf)
is retained unchanged at
`artifacts/2026-09-08-infineon-villach-source-capture-v1/responses/infineon-villach-environment-2025.body`:
11,110,904 bytes, SHA-256
`1606c4ffdb5f4b9f5118f9f003d423c5715b3fb32a8f20d0c712219d9ecba032`.
Its exact publication day is unknown; PDF creation/modification dates are not publication dates.
Actual acquisition completed September 8, 2026 at `21:45:39.929888Z`.

One robots probe, one terms probe and two document GETs succeeded without redirects, retries,
cookies or credentials. Retained policy bodies precede the document requests. The complete
[Infineon terms](https://www.infineon.com/legal/usage-terms) permit informational non-commercial
or personal downloading with attribution and notices preserved. Raw originals remain local;
commercial use, public Atlas redistribution and model training are not approved by this review.

The separately retained December 16, 2025 issuer press release provides chip-production context,
not independent confirmation or a conversion from wafer area to chips. The existing strict filing
HTML parser rejected that page's prefix nesting; no parser-verified evidence span is claimed and
the parser was not relaxed.

## Executed core admission

Schema 8 adds produced-wafer-area units, non-calendar fiscal years and source-native site
quantity scope without changing existing quantity series keys. Migration 8 rebuilds only the
metadata table, retains every old column/value and restores the immutable and subject-type guards.
Migrations 1-7 remain byte-identical. Area/count/percent units cannot be substituted; the area
quantity has no assigned wafer diameter. Fiscal years are explicit complete non-calendar
twelve-month intervals, not inferred calendar-year totals or quarterly allocations.

The [separate admission review](../review_plans/2026-09-08-infineon-villach-output-admission-v1.json)
was completed at `2026-09-08T22:19:53.551527Z`, SHA-256
`66e4b7fa6489a96643745db103008afcaf50c8beabdf013ef1e0ac59e802b4f0`.
It selects **all five totals**, preserves the original decimal-comma labels in source records,
and binds all 28 retained evidence files, totaling 21,389,716 bytes. The original unadmitted
research review remains unchanged; acquisition alone was not retroactively relabeled admission.
Manual visual interpretation is explicit because numeric text extraction omitted the labels.

Actual admission occurred at `2026-09-08T22:21:09.548343Z`, run
`2f1f2a6f-12b5-5907-85de-2d5df8698b83`. The new local successor is
`artifacts/2026-09-08-infineon-villach-output-schema8.sqlite`: 242,372,608 bytes, SHA-256
`83f24c839dda19f3924a245f10f7bd7bb1ec9439d676fbca558da7cded0c45b6`.
It adds one source-native unresolved site, five source-statement scalars with mandatory metadata,
five source records and ten support/context links. Publication, physical effective dates,
confidence and physical capacity basis remain null. The source-reported fiscal reporting periods
do not backdate database knowledge. No source text, chart fragment or raw PDF is embedded in
the new source records or authorized for public redistribution.

The preserved schema-7 parent remains
`artifacts/2026-09-08-ase-k18b-milestones-schema7.sqlite`, SHA-256
`ca0d973348301ee5f2d8f7c7e375838f56c224f8f46a1df6351d15ec9b366af3`.
The committed `1707311` producer is already retained for isolated historical replay.
This admission does not modify that database, the seven-company baseline, scheduled
execution, the frozen forecast vintage, or the original checksum ledger.

The source-checkout CLI reconstructs a new-only output from a private parent image. Its `verify`
command independently regenerates every existing successor byte using the original admission
clock, exact review and retained evidence. Code, input inventories, parent and output are checked
again across execution. All acquisition and policy checks run offline on private retained copies;
there is no new HTTP request. Local admission/replay receipts are not external timestamp
attestation or independent manufacturing verification.

```sh
python3 scripts/admit_infineon_villach_output.py verify \
  --parent artifacts/2026-09-08-ase-k18b-milestones-schema7.sqlite \
  --review review_plans/2026-09-08-infineon-villach-output-admission-v1.json \
  --reference-root /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  --expected-review-sha256 66e4b7fa6489a96643745db103008afcaf50c8beabdf013ef1e0ac59e802b4f0 \
  --output artifacts/2026-09-08-infineon-villach-output-schema8.sqlite \
  --expected-output-sha256 83f24c839dda19f3924a245f10f7bd7bb1ec9439d676fbca558da7cded0c45b6
```

After package code evolves, use the committed retained producer through the allowlisted
`infineon-villach-output` [frozen-runtime operation](frozen_runtime.md), not the evolving package.
That route permits verification only, with absolute input paths and two mandatory literal hashes.

## Admission verification

The real CLI verification reconstructs all 242,372,608 successor bytes. A separate read-only
SQL comparison checks all 29 inherited tables: no old row is removed or changed, and every table's
delta matches the expected admission. It also hashes all 104,286 hydrated physical-current
records before and after; those records are identical. Source-only queries return zero new-site
rows before admission and all five at the actual admission cutoff.

The successor has 104,465 claims and nine source quantities: four preserved TSMC references and
five Villach totals. Its three physical-capacity rows remain unchanged and announced-only.
The independent local check is `artifacts/2026-09-08-infineon-villach-independent-check-v1.json`,
SHA-256 `5595342d7dcd20e71dbf707e3f91a0dcc6ced835b8f2394ca9bf08fcc808a807`.
It binds the separately retained checker script and exact database hashes. These are independent
engineering comparisons, not independently observed manufacturing output.

Both the K18B parent and the frozen guidance comparator passed their original-producer replay
after this schema evolution. Their new receipts are
`artifacts/2026-09-08-post-villach-k18b-replay-v1.json`, SHA-256
`20d92c247deb4fb201b132965e42abb0cea007b705672264bc4d284d4b33498c`, and
`artifacts/2026-09-08-post-villach-guidance-replay-v1.json`, SHA-256
`2dce33b5cd3b05402003e71b3d3e89722251fd08ba1b8d555378199211412add`.
No new forecast vintage, scored realization or calibrated performance follows from this admission.

The final regression run completed 1,605 core tests in 260.584 seconds: 1,604 passed and one
skipped. All 12 web tests, Python syntax checks and offline source/wheel builds passed. Twenty-four
new tests cover schema extension, migration preservation, importer evidence/chronology/tamper
boundaries and the verify-only frozen-runtime route. The built wheel contains the exact new
package/migration bytes; the importer remains a source-checkout CLI, not a new installed entry point.
The original scheduled checkout is clean and conflict-free, with its checksum ledger, baseline,
analytics producer and historical database hashes unchanged. No push or publication was performed.

## Original source-review verification

The local verification receipt is
`artifacts/2026-09-08-infineon-villach-output-review-verification-v1.json`, SHA-256
`17d66d7d6f7fde67798c0868e4f4e7b93dea2ba66a36c3d9fba58c0691fa3aee`.
It binds review SHA-256 `92ba8eaf4b9951f0b963ff9cedccbec0a83215e0ce835e6bff6b64b60402e270`,
the ten-file capture inventory (14,262,630 bytes), eight retained visual-review files
(5,532,440 bytes), all policy dependencies, source-check ledgers and five-row date/scale arithmetic.
All 129 files of the verified `1707311` frozen producer still match the working implementation.
The parent database hash is unchanged. The 44 existing source-check, capture and quantity tests
pass; no full-suite rerun or new core admission was claimed for those research/documentation edits.
