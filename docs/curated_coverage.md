# Curated-source coverage and freshness

An empty review queue does not establish complete monitoring. This offline report starts with the
exact seven-facility AI-critical cohort, keeps unmonitored facilities in the denominator, and shows
document-check age separately from acquisition health and review backlog.

```sh
python3 scripts/report_curated_coverage.py \
  --catalog acquisition_plans/ai_critical_coverage_v1.json \
  --database artifacts/2026-09-07-amkor-source-review.sqlite
```

Use `--as-of` for a fixed knowledge cutoff and `--output` for a new JSON artifact. An existing output
is never overwritten. The command makes no network requests and does not modify the queue, source
packets, baseline, acquisition permissions, or manufacturing claims.

## What is bound and counted

The versioned catalog pins the baseline and every configured acquisition plan by exact hash.
It must enumerate all baseline facilities in order, with either a pinned plan or a nonempty
unmonitored reason. The plan must match the facility, company, and geography, and its bound review
record must match. Missing rows, inconsistent bindings, and changed files fail closed.

The catalog's recording time cannot precede its baseline or plan review. A report cutoff earlier
than the catalog is rejected: a later monitoring configuration must not leak into a historical
coverage assessment. Use the configuration known at the requested time instead.

Queue admission determines when an observation became known. The report verifies the selected
queue history and its retained capture bytes at that cutoff. Only captures matching the exact
configured plan hash count toward that plan's document coverage. Older plan versions remain in
the queue history but do not silently satisfy a newly configured review boundary.

Company, country, and source-family groups show configured document counts and fresh, stale,
never-observed, and unhealthy counts. Source families represented in the baseline remain visible
even if no current plan covers them. Historical baseline source metadata is included for context,
not counted as a new check; this command validates the baseline contract but does not reverify its
raw publisher files. `baseline_source_bytes_verified` is therefore false.

## Separate clocks and states

- **Freshness:** age of the latest content-eligible document response, not queue admission or packet
  completion. At the configured age limit the document becomes stale. The initial seven-day limit
  is a local review threshold, not a validated collection cadence or publisher update schedule.
- **Check health:** the latest failed, policy-blocked, or content-ineligible check remains unhealthy
  even if an earlier eligible response is recent. Failed checks never reset the eligible age.
- **Policy-blocked assessment:** a blocked document has an assessment time and zero document
  attempts; policy requests are not silently counted as document retrievals.
- **Plan state:** an expired internal review window requires attention independently of document
  freshness. The report does not renew the plan or grant permission for another request.
- **Review backlog:** pending, acknowledged, deferred, and recurrence work remain visible separately
  from collection health. Dismissing a candidate does not refresh a source or fill a coverage gap.

Equal-time contradictory eligible observations remain parallel and require attention. Backfilled
older captures cannot replace a newer response in the age calculation. Neither missing checks nor
an expired plan establish a facility closure, cancellation, or loss of capacity.

`attention_required` combines queue attention, missing plans, expired review windows, stale or
unobserved documents, and unhealthy checks. It is a work indicator, not an alert delivered to anyone.
`coverage_complete`, `continuous_operation_proven`, `delivery_eligible`, `claim_acceptance`, and
`absence_inference_allowed` remain false. Exact-URL monitoring is not company-wide discovery, and
the catalog does not enumerate every relevant publisher or prove an uninterrupted capture chain.

## Retained September 7 pilot

The [pilot record](../review_plans/2026-09-07-coverage-pilot.json) pins the report produced at the
fixed cutoff `2026-09-07T03:47:33.621918Z`. It verifies three retained queue packets and shows:

| Cohort scope | Configured document checks | Coverage result |
| --- | ---: | --- |
| Amkor Peoria | 3 | Recent eligible responses under the configured plan |
| TSMC, Samsung, Intel, Micron, SK hynix, ASE | 0 | Six explicitly unmonitored facilities |

There are no pending or recurrence-review items, but report attention remains true because of the
six monitoring gaps. Samsung and SK hynix newsroom collectors remain disabled as recorded in the
source registry. The report makes no new rights decision.

The next operational gates are scheduled execution with retained run outcomes, approved expansion
of source coverage, new-document discovery, and complete collection-chain accounting. Measured
alert performance and calibrated forecasts remain separate later gates.
