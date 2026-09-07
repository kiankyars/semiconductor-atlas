# Amkor Peoria successor review — 2026-09-07

The new seven-company input updates only Amkor. It contains 73 claims, 12 evidence fragments and
10 primary documents. The other six facility objects and their inherited sources, evidence and
ingestion runs are exactly unchanged and were not refreshed in this pass.

Amkor now has a dated `site_preparation` milestone from its October 6, 2025 groundbreaking and a
planned, two-phase packaging/test scope. This is newly ingested evidence of an earlier event. It
does not demonstrate that the system detected the event early, or that site preparation remains
the maximum current construction stage. Yield, utilization, qualification and point geometry
remain unknown.

## Evidence and scope decisions

The retained [Amkor groundbreaking article](https://amkor.com/blog/amkor-semiconductor-packaging-facility-peoria-arizona/)
identifies the Peoria packaging/test project and its two-phase plan. The publication byline and
publication metadata agree on October 6, 2025; its later page modification is not substituted for
the event date. The [May 13, 2026 issuer article](https://amkor.com/blog/amkor-peoria-number-one-deal-north-america/)
describes the expanded campus and expects Phase One opening during 2028. The two released excerpts
contain 19 and 21 words, excluding the ellipsis separators. Both match independent visible-text
parsing and the existing producer's ordered-segment verifier.

The stable key `amkor:peoria-advanced-packaging-project` continues to identify the bounded Peoria
project. It does not assert continuity of a particular parcel, building or phase. The investor-
relations relocation release and municipality chronology were discovered as research leads, but
their exact documents were not captured after access-policy checks failed. The successor therefore
does not publish the reported parcel swap, acreage, physical-site relocation or any point geometry.
Physical-site correspondence remains unresolved.

The [current NIST award page](https://www.nist.gov/chips/amkor-technology-inc-arizona-peoria)
was checked successfully and still contains the two old announced future rates: 3,700,000
units/month and 14,500 wafers/month. Its page-modification metadata does not establish a new
substantive affirmation or allocate those rates to the expanded campus or its first phase. The
review preserves both original claims in r3 and defers their current applicability. Neither rate
is withdrawn or set to zero. The successor has zero numeric capacity claims on every basis because
the current scoped projection abstains; this is not a capacity-loss measurement.

NIST still expects end-2027 mass production, while the issuer article expects Phase One opening in
2028. The milestone definitions and phase/project correspondence are unresolved. The packet records
that disagreement without calculating a comparable delay or issuing a forecast.

## Access, retention and coverage

The acquisition was limited to individually selected URLs. Amkor's current robots file disallows
`/search/` but not the two requested article paths. Its website disclaimer and copyright/trademark
treatment were reviewed. No blanket reuse, training or general crawling authorization is inferred.
Exact HTTP bodies remain local; the successor publishes metadata, attribution, facts and short
excerpts. The successful current NIST copyright page preserves the public-information and third-
party exception distinction.

The local `source_checks.json` contains 11 network attempts: seven successes and four failures.
Three successful attempts fetched source documents; the remaining attempts checked access or
rights policy. The failures are the IR robots HTTP/2 transport error, its HTTP/1.1 timeout, the
city robots HTTP 403 and the old NIST copyright URL's HTTP 404. The current NIST copyright URL
succeeded. The timeout record has an observed completion clock and measured duration, but its
invocation start/end clocks were not retained; they are explicitly null. Other attempt clocks have
second precision, so a subsecond request can have equal start and finish timestamps.

The IR and city source documents were not fetched with curl after their access-policy checks
failed. An earlier research web open of the IR article also returned 403. These failures do not
prove missing documents, cancelled projects or absent activity. The packet does not claim a complete
publisher search, a complete time window, or a new review of TSMC, Samsung, Intel, Micron, SK hynix
or ASE.

## Artifacts and validation

- Successor input: `baselines/ai_critical_manufacturing_v1_amkor_2026-09-07.json`.
- Full review decisions: `review_plans/2026-09-07-amkor-peoria-successor.json`.
- Local source packet: `source_snapshots/2026-09-07-amkor-peoria-successor/`.
- Public attempt metadata: `review_plans/2026-09-07-amkor-peoria-source-checks.json`, byte-identical
  to the local `source_checks.json`. HTTP bodies remain local.
- Publication candidate: `releases/2026-09-07-ai-critical-manufacturing-amkor-successor-v1-r2`.
- Change bundle: `releases/2026-09-07-ai-critical-manufacturing-amkor-changes-v1-r2`.

The final r2 acceptance clock is 02:31:58 UTC, after the completed review at 02:30:24 UTC.
Earlier v1 validation candidates predate that completed review and must not be published. They are
retained locally, not overwritten. The event and source publication dates remain unchanged.

The existing exact-byte loader, standalone-atlas builder and release validator pass. All 21
declared payloads, the manifest and archive matched an independent rebuild byte-for-byte.
The archive is 84,682 bytes with SHA-256
`357f1d0122cb5ce7a66af5c0ee15b575dec619a4d2547756d3ea74122293d191`.
The manifest SHA-256 is
`e71158131075f70b299fae78951da4ba2c98473d4c14f791d63bf7ce47aac044`.

The original baseline input remains SHA-256
`8eec7a20a9d2d900161efce778532756f9224520f2ec0056bb329c5118198958`, and the producer
remains `98f97bd49cfcfb2d350742f422e43892c9b55c170ecbe73c93538bdbc1c38969`.
Independent evidence review and regression tests check the new input and retained source evidence.
No source collector framework was introduced: acquisition used bounded single-URL curl requests,
and the existing release builder can reproduce the successor from the retained bytes.

The r3-to-successor comparison has 62 reaffirmed series, nine revisions, two additions and four
series not carried forward. It emits one uncalibrated, non-deliverable lifecycle proposal, not an
operational alert. The omissions include two superseded capability descriptions and the two old
capacity rates; none is negative evidence.

Verify check metadata and all retained response bodies with:

```sh
python3 scripts/verify_source_checks.py \
  --ledger review_plans/2026-09-07-amkor-peoria-source-checks.json \
  --archive-root source_snapshots/2026-09-07-amkor-peoria-successor
```

The report separates document successes from policy checks and retains last successful checks
alongside later failures. This verifies recorded metadata and bytes, not claim truth or publisher
completeness. Tests run without local raw documents; exact-byte verification additionally requires
the retained local source packet.

The data and review are committed for review; the successor archive is not yet publicly released.
Publication must carry the review and coverage context alongside the successor, because the v1 release schema does not itself encode
the physical-site correspondence decision or the distinction between this partial refresh and a
complete company update.
