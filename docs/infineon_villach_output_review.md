# Villach produced-wafer-area evidence

The September 8 review retains a five-year, site-specific output series from Infineon's
2025 environmental statement. This is useful new production evidence, not installed capacity
or an accepted core claim. [The complete review](../review_plans/2026-09-08-infineon-villach-output-source-review.json)
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

## Next integration gate

Schema 7 lacks produced-wafer-area units, non-calendar fiscal years and source-native site
quantity scope. A deliberate versioned extension must preserve these dimensions and all existing
quantity semantics, then admit **all five totals** into a new successor database with the actual
admission clock. The full PDF, review and acquisition-policy chain must remain verifiable offline.
No source text or chart fragment should be embedded in public derivatives under assumed rights.

The preserved schema-7 parent remains
`artifacts/2026-09-08-ase-k18b-milestones-schema7.sqlite`, SHA-256
`ca0d973348301ee5f2d8f7c7e375838f56c224f8f46a1df6351d15ec9b366af3`.
The committed `1707311` producer is already retained for isolated historical replay.
This evidence review does not modify that database, the seven-company baseline, scheduled
execution, the frozen forecast vintage, or the original checksum ledger.

## Verification

The local verification receipt is
`artifacts/2026-09-08-infineon-villach-output-review-verification-v1.json`, SHA-256
`17d66d7d6f7fde67798c0868e4f4e7b93dea2ba66a36c3d9fba58c0691fa3aee`.
It binds review SHA-256 `92ba8eaf4b9951f0b963ff9cedccbec0a83215e0ce835e6bff6b64b60402e270`,
the ten-file capture inventory (14,262,630 bytes), eight retained visual-review files
(5,532,440 bytes), all policy dependencies, source-check ledgers and five-row date/scale arithmetic.
All 129 files of the verified `1707311` frozen producer still match the working implementation.
The parent database hash is unchanged. The 44 existing source-check, capture and quantity tests
pass; no full-suite rerun or new core admission is claimed for these research/documentation edits.
