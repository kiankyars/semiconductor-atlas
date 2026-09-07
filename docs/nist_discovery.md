# Bounded NIST index discovery

This collector discovers document links from the current NIST
[CHIPS news list](https://www.nist.gov/chips/chips-news-releases) and
[CHIPS Program Office awards directory](https://www.nist.gov/chips/chips-program-office-awards).
It does not fetch the linked articles or project pages, accept manufacturing claims, update the
seven-facility baseline, or resolve source-version review items. Company-name matches only route
leads for a later document-scope and access review. U.S. company mentions cannot refresh the
Singapore HBM project, M15X, or the Kaohsiung campus.

## Acquisition boundary

`acquisition_plans/nist_index_discovery_v1.json` binds the immutable baseline cohort, reviewed
aliases, exact index roots, and `review_plans/2026-09-07-nist-index-discovery.json`. Every invocation
first checks the current NIST robots and rights policy bytes against reviewed normalized hashes.
The rights comparison binds visible text and link destinations; its versioned normalization handles
rotating email-obfuscation keys without discarding a changed destination. The 30-day expiry is an
internal review deadline, not a publisher-granted permission period.

Only each exact root and explicit same-root `?page=N` links are acquired. The next page must be
sequential; credentials, foreign origins, redirects, fragments, additional queries, and ambiguous
pagination fail closed. The plan caps each chain at eight pages, each response at 2 MB and 30 seconds,
and spaces requests by at least one second. There are no automatic retries, cookies, image requests,
archive searches, linked-document acquisition, or raw-page redistribution.

Each completed packet retains its plan, review, baseline, response bodies, request receipts, derived
results, code hashes, and exact-file manifest. HTTP failures and partial bodies remain recorded.
Policy changes block index acquisition. A structure failure, duplicate across pages, or page cap
leaves that chain incomplete rather than reporting a successful empty search. Mid-run expiry or
invalid transport metadata stops the invocation and leaves incomplete receipts for inspection; such
a directory is not a completed packet and cannot enter inventory.

## Observations and replay

News observations preserve the teaser's raw publisher `datetime`, title, summary, and canonical URL.
Award observations preserve title, summary, and source-native locality and region. Award cards for
other offices are counted but excluded; recognized record and duplicate counts remain visible.
These values describe the observed index, not the contents or truth of an unacquired linked document.
The page footer's `Updated` date is not a document date or a freshness clock.

`page_chain_complete` means only that the observed chain reached an explicit terminal structure
within the request limit. Pages are fetched sequentially, not from an atomic publisher snapshot.
The separately linked older news archive, unlinked content, unrecognized markup and other publishers
are outside the denominator. Even a complete chain never sets `publisher_complete`,
`facility_coverage_complete`, `claim_acceptance`, or `absence_inference_allowed` to true.

Inventory is rebuilt from explicitly supplied completed packets. It unions canonical URLs through a
capture-finished-time cutoff, preserves every included index observation, and gives each URL a stable
SHA-256 identifier. Packet identity uses its manifest hash, not a machine-specific directory path,
so byte-identical restored packets reproduce the same inventory. Later failures or rolling-window
disappearance cannot delete earlier entries.
Future packets are excluded from the projection; mixed plan revisions and duplicate capture inputs
are rejected. The caller must supply the intended retained history: this is not automatic directory
discovery, an admission-time audit log, a resolved review queue, or a freshness guarantee. Latest
capture health is exposed separately from the surviving inventory.

## Commands

Run from the repository root with a new output directory. Parents must already exist.

```sh
python3 scripts/discover_nist_sources.py capture \
  --plan acquisition_plans/nist_index_discovery_v1.json \
  --output source_snapshots/2026-09-07-nist-index-discovery-v1

python3 scripts/discover_nist_sources.py verify \
  --run source_snapshots/2026-09-07-nist-index-discovery-v1

python3 scripts/discover_nist_sources.py inventory \
  --run source_snapshots/2026-09-07-nist-index-discovery-v1 \
  --as-of 2026-09-07T05:14:22.234813Z \
  --output artifacts/2026-09-07-nist-discovery-inventory-v1.json
```

Use the actual desired UTC cutoff, no earlier than the capture finish to include that packet. Add a
`--run` argument for every earlier packet to retain. Verification and inventory are offline and never
overwrite an existing output. Capture and verify exit 2 for a valid retained packet requiring
attention; invalid inputs or incomplete packets exit 1. A successful inventory command does not mean
its latest capture was healthy: inspect `latest_capture_attention_required` and the capture statuses.

The [subsequent discovery queue and runner](discovery_review_and_poll.md) add recoverable admission
and URL dispositions without changing this capture/inventory contract. The existing daily task now
runs both jobs sequentially after manual testing. Actual scheduler-triggered execution, subsequent
independently approved document acquisition and evaluated manufacturing alerts remain unproven.

## Verified manual pilot

`review_plans/2026-09-07-nist-discovery-pilot.json` binds the completed capture and inventory. The
2026-09-07 run made eight requests: two policy checks, two news pages (10 + 7 records), and four
award pages (15 + 15 + 15 + 2). All 64 records were eligible for index inventory, with 14 company-name
matches. The minimum observed inter-request gap was 1.007826 seconds; 733,814 response bytes were
retained locally. No linked document was acquired.

The inventory includes a July 2026 TSMC news link and a Samsung Taylor directory URL distinct from
the currently monitored Austin URL. These are review leads, not newly verified manufacturing
facts. The complete current chains do not cover the separately linked older news archive.

Offline verification reproduced the capture. A copy restored into a different directory reproduced
the 102,382-byte inventory exactly (SHA-256
`64fe483eb8bfdd4c7cd3e1323a7851df40243c9de4965b8c194fd28006a394ae`). The original v1 coverage
pilot also replayed byte-for-byte after the publisher-scoped source-check extension. Core tests
cover policy changes, transport failure, unsafe/ambiguous pagination, page limits, retained history,
cutoffs, pacing, byte tampering, and CLI behavior. The full 660 core and 12 web tests passed.
An independent extraction, without using the discovery parser, matched every retained URL, news
date and pager link. All 22 manifest payloads (879,887 bytes) passed independent hashes.
