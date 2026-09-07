# Micron Singapore HBM monitoring

The [exact MTI plan](../acquisition_plans/micron_singapore_mti_v1.json) adds one Singapore
government speech to the existing polling loop. The v4 catalog covers seven documents across
five of seven facility scopes: TSMC, Samsung, Intel, Micron and Amkor. SK hynix M15X and ASE
Kaohsiung remain unmonitored. These are bounded document checks, not complete publisher searches
or proof of current manufacturing state.

## Historical source, unchanged baseline

The [MTI speech](https://www.mti.gov.sg/newsroom/speech-by-dpm-and-minister-for-trade-and-industry-gan-kim-yong-at-the-groundbreaking-ceremony-of-micron-s-hbm-manufacturing-facility/)
is dated January 8, 2025 and concerns Micron's Singapore HBM advanced-packaging groundbreaking.
The [text adjudication](../review_plans/2026-09-07-micron-mti-text-adjudication.json) compares it
with the original baseline's June 26, 2025 evidence, which already describes the January
groundbreaking. It corroborates that historical event and project identity, not a new milestone
after the baseline or a current-state reaffirmation. The baseline and original r3 archive are
unchanged.

Publication precision is one day; no exact publication timestamp was found. Retrieval time, a
migration notice and the site's September 2026 footer are not article-update or manufacturing-event
dates. The separate January 2026 NAND-wafer-fab project is excluded, as are existing NAND operations,
company-wide investment, jobs and Singapore-wide projections. Job counts are not chip capacity.
Current HBM production, qualification, numeric capacity on all five bases, yield, utilization and
facility-safe point geometry remain unsupported.

The first observed text version was explicitly dismissed from source-text review without accepting
a manufacturing claim. Whole-page text changes, including footer changes, can still create review
work. Required identity markers are a triage guard, not proof that every matching passage concerns
this facility; a reviewer must still inspect the text and its scope.

## Access and publication boundary

The [source review](../review_plans/2026-09-07-micron-mti-source-review.json) retains MTI robots,
Terms of Use and privacy-response bytes. Robots excludes `/search`; no search, sitemap or broader
crawl is enabled. Both reviewed robots and normalized terms hashes must match before each exact
document request. The thirty-day internal review expires on October 7, 2026 and cannot renew itself.

No affirmative open-content license or blanket automation grant was identified. This is a bounded
internal local-research decision for one public government-authored speech, not publisher-granted
permission or a legal-permission determination. Raw responses stay local. Public output consists
of operational metadata and original reviewer analysis, not raw HTML, images, attachments or
substantial publisher text. No model training is approved. The earlier Micron investor-relations
restriction is unchanged; this government source does not authorize the issuer route.

## Verified pilot

The [pilot record](../review_plans/2026-09-07-micron-mti-monitoring-pilot.json) binds the inputs,
request receipts, queue decision, retained verification and schedule readback:

- One ordinary live poll made three HTTP 200 requests: robots, terms and the exact speech. It
  retained 12 managed files totaling 831,298 bytes; the minimum request gap was 1.011004 seconds.
  There were no redirects, retries or cookies. All four prior plans were not due.
- A copied capture replayed identically. The expanded queue has 16 events, 11 capture packets,
  seven candidates and two pending earlier NIST reviews, with no recheck items. Its export restores
  at every event cutoff. Source-queue replay still requires the referenced local capture packets;
  the event export alone is not a self-contained archive.
- The Micron candidate's raw/text hashes, capture manifest, plan hash and actual admission time
  match the retained capture. The dismissal binds the exact adjudication hash and expected queue
  event. The review document and queue-decision admission retain their different actual clocks.
- Historical v1/v2/v3 coverage reports remain byte-identical after the queue expansion. Two
  no-network repeats kept every due time unchanged: the first reported the review disposition
  once and the second was quiet. The sequential NIST discovery repeat was also not due and quiet.
- The original r3 archive, baseline and producer hashes are unchanged. The existing frozen Amkor
  diagnostic still produces the exact same report, and its alert-review export is unchanged.

The existing daily 08:00 America/Los_Angeles task now uses:

```sh
python3 scripts/poll_curated_sources.py --config acquisition_plans/ai_critical_poll_v4.json
```

Its identity, cadence, local queue/state, sequential discovery job and quiet-on-unchanged policy
are preserved. Following [official scheduling guidance](https://learn.chatgpt.com/docs/automations),
the commands were manually tested before the schedule update and the saved prompt was read back.
An actual scheduler-triggered run remains unobserved; a saved active task is not uptime evidence.

This advances repeatable source coverage, not the full detection goal. New-document discovery for
Micron, broader permitted coverage, reviewed source-to-claim handoffs, independently evaluated alert
performance and calibrated capacity forecasts remain open. No new data release is published.
