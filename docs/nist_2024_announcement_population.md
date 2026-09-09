# NIST 2024 announcement-population evidence

The September 9, 2026 work freezes a broader source population before project and
outcome adjudication: all 65 calendar-2024 articles currently listed in NIST's
CHIPS-filtered archive, with all 65 complete HTML bodies retained. It is not a
65-project benchmark, a historical announcement census, or a forecast result.

## Population and retained evidence

The [current CHIPS news page](https://www.nist.gov/chips/chips-news-releases)
links to the [filtered archive](https://www.nist.gov/news-events/news-updates/archive/search?combine=&o%5B0%5D=2810151&tag=).
Four pages contain 89 distinct articles, 65 explicitly dated 2024 and 24 outside
that calendar window. Visible dates match HTML datetime fields; footer update
dates and URL years are not substitutes. All complete article-element spans are
bound to their exact source bytes.

| Page | Displayed positions | All rows | Dated 2024 |
| --- | --- | ---: | ---: |
| Base | 1-25 | 25 | 14 |
| page=1 | 26-50 | 25 | 25 |
| page=2 | 51-75 | 25 | 25 |
| page=3 | 76-89 | 14 | 1 |

The [index roster](../review_plans/2026-09-09-nist-archive-index-roster-v1.json)
is 125,702 bytes, SHA-256
01e78176d40796dfc4e652f7e554fd115198be5d32cee74eb8d57d5fe6a51bd6.
It preserves every indexed title, date, summary, URL, position, capture clock and
source span. Its zero body-reviewed count remains correct: later retention does
not rewrite this earlier artifact.

Twenty-five titles contain preliminary-terms or preliminary-memorandum wording.
That flag only prioritizes review. All 65 articles remain in the population,
including mixed award/PMT announcements and articles without those title phrases.
The current award-recipient directory is not the denominator.

The [article-retention roster](../review_plans/2026-09-09-nist-2024-article-retention-roster-v1.json)
is 66,966 bytes, SHA-256
93f4687efb0c627f75a65e157cf012bb23f27bd01bd118bc995e2ab4f2f3e921.
It binds all 65 unique index URLs to exact bodies and retrieval clocks:
6,095,074 article bytes, zero missing bodies, zero adjudicated project rosters and
zero scored cases. Every response was HTTP 200 without redirects and passed its
index-title identity check. All require semantic review.

## Access, clocks and limits

The [index review](../review_plans/2026-09-09-nist-archive-source-acquisition.json)
approves only four archive pages. The later
[article review](../review_plans/2026-09-09-nist-2024-article-source-review.json)
separately approves all 65 canonical URLs, split 50/15 only to fit the existing
capture tool's bound. Each capture rechecked both normalized NIST policies.
Requests were two seconds apart, identified and size/time bounded, without
cookies, redirects, retries or linked-resource traversal.

NIST's public-information notice retains marked-copyright and product-specific
exceptions. Originals and attribution remain local; no raw publication, model
training, issuer-site collector, core admission or scheduled transition follows.
The [Intel lead decision](../review_plans/2026-09-09-project-population-source-screen.md)
remains separate and unapproved for acquisition.

Index bodies were retrieved September 9 at 02:49:52-02:50:01 UTC. Article requests
finished by 02:58:19.118759 UTC. These are current retrieval clocks, not verified
2024 availability. The archive can omit removed or differently tagged items.
Known project outcomes were already exposed. This reduces selection from today's
award portfolio but cannot establish global, independent or blind coverage.

## Reproduction and checks

Retained packet manifest SHA-256 values:

- artifacts/2026-09-09-nist-archive-capture-v1/manifest.json:
  0f68f581a295505b05474b1a08984739e761b357e3f22fa108d8e7db1093cccc.
- artifacts/2026-09-09-nist-2024-articles-batch-1/manifest.json:
  a08746e1be7bbe82edc19567ef8d4aa20c66eaf3efe2a7cf75fedc4eeb9d010a.
- artifacts/2026-09-09-nist-2024-articles-batch-2/manifest.json:
  064373410f225cf52783335b566f8af49a2cef1108431e2954268d3e0186cb28.

The index inventory replays byte-for-byte. A separate stdlib HTML parser,
importing no Atlas parser, checked all 89 URLs, titles, dates and complete source
spans and independently counted 14/25/25/1 dated-2024 rows. Its receipt at
artifacts/2026-09-09-independent-nist-index-check-v1.json has SHA-256
228126aff7e0fcf154e69361f9f5eccada2226f62ea4c436fe50a2182dfd2d11.
This checks implementation, not independent project evidence.

Read-only reproduction from the isolated checkout:

    python3 artifacts/2026-09-09-nist-archive-inventory.py --verify
    python3 scripts/capture_curated_sources.py verify --run artifacts/2026-09-09-nist-2024-articles-batch-1
    python3 scripts/capture_curated_sources.py verify --run artifacts/2026-09-09-nist-2024-articles-batch-2

Do not rerun acquisition or new-only roster generation against existing outputs.
Exact source bodies remain outside Git and public releases.

## Next evidence gate

Review every 2024 article's full body. Record all PMT-bearing documents, non-PMT
exclusions, manufacturing projects/phases, literal events and conditions,
unresolved dates and identity boundaries. Equipment, materials, fabrication,
packaging and R&D are distinct scopes. Then review later outcomes against that
complete roster, preserving cancellations, unknowns and unsuccessful projects.

The generic diagnostic now requires primary evidence for every substantive
outcome, even on unresolved or excluded cases. Its original three-project report
still replays under its unchanged frozen producer. Validation ran 1,699 core tests
(1,698 passed, one expected optional-data skip), 12 web tests and 70 focused tests;
offline sdist and wheel builds passed. Parent schedule/code/database hashes, the
isolated schema-9 database and existing diagnostic/forecast artifacts are
unchanged. No merge conflict, push, publication, numeric capacity, calibration or
completed north-star claim follows.
