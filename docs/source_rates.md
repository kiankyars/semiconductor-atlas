# Source-reported monthly rates

Schema 9 adds typed source-rate metadata. A rate denominator is not a reporting interval:
"per month" does not mean that the publisher measured a particular calendar month.
This representation does not change schema-7/8 complete-period quantities or the five physical
capacity bases.

The initial contract supports source-reported wafer production and wafer-start capacity rates,
with distinct numerator units. Diameter may be unknown; no wafer-equivalence conversion follows.
It retains exact source wording, grouped number literals, numeric qualifiers, the publisher's
accounting scope, and any literal relative-time phrase. Reference interval bounds remain null.
Known reporting intervals and other denominators need a separately reviewed contract extension.

`source_rate.*` claims require immutable metadata bound to their scalar value, subject kind,
semantic series key, null physical validity dates, and null uncalibrated confidence. Queries and
source-history exports hydrate the qualifiers and scope. Missing or inconsistent metadata fails
validation and hydration. These rates do not enter physical-current claims or capacity exports.

## Polar admission

One source-native facility statement is admitted from the [September 24, 2024 senator release](https://www.klobuchar.senate.gov/public/index.cfm/2024/9/klobuchar-announces-major-funding-for-polar-semiconductor-s-bloomington-manufacturing-facility):
the reported production rate is **21,000 wafer semiconductors per month**.

The complete paragraph first identifies Polar's Bloomington facility; its next sentence has
"company" as its grammatical subject. The facility scope is an explicit paragraph-context
interpretation, not canonical identity resolution, independent site allocation, or an established
company-wide accounting perimeter. Diameter, measurement period, process-stage boundary,
finished-wafer accounting, yield, utilization and qualification remain unknown. The source says
neither "approximately" nor "currently" nor "up to". No annualization is performed.

Government publication is not demonstrated independent measurement. NIST's different wafer-start
capacity figures are not corroborating measurements of this production rate. The release's 2024
dateline is publication context, not physical applicability at the 2026 retrieval/admission date.

The [research review](../review_plans/2026-09-08-polar-production-rate-source-review.json) retains
the original interpretation and strict-parser failure. The [separate admission review](../review_plans/2026-09-08-polar-source-rate-admission-v1.json)
binds 43 input files across five acquisition packets, all request receipts, and four complete
original HTML evidence blocks. Source acquisition made seven requests: two HTTP 302 robots
responses, the explicitly requested error-page destination, three policy pages, and one release.
The error page has HTTP 200 but visibly reports 404; no HTTP status is rewritten. The explicit
senator-to-central policy link uses HTTP, and the reviewed capture upgrades only its scheme to
HTTPS. This manual policy-chain review does not relax the existing same-origin collector.
No recurring collector, raw redistribution, linked-asset use, or publication is enabled.

The source contains six unmatched closing anchors in preceding navigation. Admission ignores
only their reviewed offsets in the exact hash-bound original document, with no open anchor on
the stack. All are before every evidence block. Original bytes, ancestor/hidden-state tracking,
and all other nesting checks remain unchanged. The existing strict parser is not modified;
browser-rendered visibility is not claimed.

| Artifact | SHA-256 |
| --- | --- |
| Original 40,085-byte source | `201e16ff5e47b1aae7d042941f263bec1be3e73e583351c6abfb4faefbe8bda3` |
| Research review | `75c80f0f9c88dd523a8a5f85de43c3e00537feb4fc73550c4483e96bf1c85638` |
| Admission review | `83856f759fd7741350539d68e5871616895de58f74f5bb95234fe12ea0b21823` |
| Parent schema-8 database | `b2445daa4cc4dfb4ff56617ad2199ed9a7993f950794f5bf45a184b1d0974bea` |
| Successor schema-9 database | `1b9bae15c988e69479f4d82e70bbb6f0a95f89a4d9b5bbe73ac878b9873ee51a` |

The new image is `artifacts/2026-09-08-polar-production-rate-schema9.sqlite`, 242,692,096 bytes.
Actual admission is `2026-09-08T23:49:55.389755Z`; claim ID is
`d08d6805-67eb-5cf4-949b-94f4b7906fb7`. It contains 104,469 claims, nine prior source-quantity
rows and one source-rate row. Numeric physical-capacity rows remain three, all announced.
This is not a new production measurement, forecast/outcome pair or passed forecasting gate.

The separate read-only SQL audit preserves every row in all 29 parent tables and every original
`sqlite_master` row. Fifteen historical source cutoffs, including one microsecond before admission,
are unchanged. Both complete physical-current exports contain 104,286 records and have compact
sorted-key UTF-8 JSONL SHA-256
`df7620a1181e1d3f8978f84799404e4b3384781deec83e79ae1b408c82f88cfe`.
Receipt `artifacts/2026-09-08-polar-independent-check-v1.json` is 3,809 bytes, SHA-256
`916aa0f82abd8f1cff27a9d35aaadc91f5ad78c68e6589d9b7ff6fa8f3dc7632`.
This audit does not import the admission implementation and is distinct from manufacturing
verification of the government-published number.

Validation completed with 1,657 core tests (1,656 passed and one expected optional-payload skip),
12 passing web tests, 77 passing focused rate/quantity/replay tests, and successful offline sdist
and wheel builds. The first full run exposed four old schema-version assertions; their expected
latest version now includes migration 9. Historical migration tests remain explicitly pinned to
their original target versions. Migrations 1–8, the published baseline and the strict historical
HTML parser are byte-unchanged.

## Local verification

The admission CLI constructs only a new image from the hash-bound schema-8 parent. It refuses an
existing output. Verification reconstructs the entire successor using its retained actual clocks
and compares all SQLite bytes, with no network or writes to either database.

```sh
python3 scripts/admit_reviewed_source_rates.py verify \
  --parent artifacts/2026-09-08-tower-closure-milestones-schema8.sqlite \
  --review review_plans/2026-09-08-polar-source-rate-admission-v1.json \
  --reference-root /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  --expected-review-sha256 83856f759fd7741350539d68e5871616895de58f74f5bb95234fe12ea0b21823 \
  --output artifacts/2026-09-08-polar-production-rate-schema9.sqlite \
  --expected-output-sha256 1b9bae15c988e69479f4d82e70bbb6f0a95f89a4d9b5bbe73ac878b9873ee51a
```

The evolving checkout must not be substituted for an older snapshot's frozen producer. The
`reviewed-source-rates` frozen-runtime operation executes only the retained verify command.
The scheduled parent checkout, published baseline, older source images and forecast vintages
remain separate and unchanged.
