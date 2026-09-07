# NIST monitoring expansion

The September 7, 2026 [source review](../review_plans/2026-09-07-nist-source-expansion.json)
adds two exact NIST award-page checks to the existing Amkor loop. The new
[catalog](../acquisition_plans/ai_critical_coverage_v2.json) retains all seven baseline facility
scopes, with three configured scopes and four explicit gaps. This is document monitoring,
not a refresh of the manufacturing baseline or an operating-capacity census.

## Source scope is not facility scope

| Baseline scope | Configured source boundary | Claim-review constraint |
| --- | --- | --- |
| TSMC Fab 21, Phoenix | Whole NIST three-fab Phoenix award page | Only explicitly first-fab statements can be considered for Fab 21. Cluster totals, future schedules, and other fabs remain separate. |
| Samsung Taylor two-logic-fab project | Whole NIST Texas award page, including Austin and Taylor R&D | Changes outside the two Taylor logic fabs are context, not Taylor facility events. |
| Amkor Peoria project | Existing two issuer articles and NIST page | Existing reviewed plan and unresolved old-rate applicability are unchanged. |
| Intel Fab 52, Chandler | None | The NIST multi-fab Chandler program page does not identify Fab 52. |
| Micron Singapore; SK hynix M15X, Cheongju | None | Their U.S. NIST awards refer to different facilities and countries. |
| ASE Kaohsiung campus | None | No source-specific plan has been reviewed for this expansion. |

The [TSMC](https://www.nist.gov/chips/tsmc-arizona-phoenix) and
[Samsung](https://www.nist.gov/chips/samsung-electronics-texas-austin) pages contain earlier
forward-looking project statements. A fresh HTTP response does not make those statements current
observations. A whole-page text difference can enter the review queue even if only another fab,
Austin, navigation, or a disclaimer changed. Review must establish location, production unit,
effective date, and evidentiary meaning before changing any manufacturing claim.

## Access and retained history

The source review pins retained robots and copyright-policy bytes, their normalization rules,
and the parent capture manifest. Both exact source paths fall outside the reviewed robots
exclusions. NIST's [public-information notice](https://www.nist.gov/copyrights-disclaimers)
retains exceptions for marked copyrighted material. Raw pages remain local; this expansion
publishes metadata and original analysis, not images or linked third-party material.
Samsung and SK hynix newsroom acquisition remains disabled.

Each new plan checks the two bound same-origin policies before the source document, uses
identified serial requests with a one-second pause, and follows no redirects or links. Changed
policy text or link targets block the document pending review. The thirty-day plan window is
an internal review deadline, not publisher-granted permission. First observations remain pending
source-version review; this workflow never accepts facility claims or delivers manufacturing alerts.

Catalog/report schema v2 exposes each document's full scope in the coverage report. Schema v1
remains supported with exactly its original output shape so retained historical reports can still
replay byte-for-byte. All original plans, configs, reports, and baseline bytes remain untouched.

The expanded runner config shares the existing queue, capture root, state root, and 23-hour
per-plan cadence. It does not reset Amkor's next due time or create a second scheduler.

```sh
python3 scripts/poll_curated_sources.py \
  --config acquisition_plans/ai_critical_poll_v2.json
```

## Verified manual pilot

The [retained pilot record](../review_plans/2026-09-07-nist-monitoring-pilot.json) binds the two
successful three-request captures, coverage outputs, and nine-event queue export. The expanded
queue restored identically into a separate database. The immediate repeat made no requests,
retained both new pending items, and reported no additional semantic change. Five document
responses were recent under their configured plans; four cohort scopes remained unmonitored.
The original v1 coverage pilot also replayed byte-for-byte.

The first expansion capture exposed a 92-millisecond gap between plans despite correct pacing
inside each plan. The runner now waits for the larger configured interval between attempted
plans, including after capture failure. The same pilot record retains this limitation and a
corrected forced repeat: thirteen requests, a minimum observed gap of 1.003697 seconds, no new
text versions, and both pending items preserved. The final twelve-event queue restored identically;
an ordinary repeat made no requests. The forced validation deliberately refreshed each plan's
cadence clock; it is not part of the scheduled prompt.

The existing daily task was updated to the manually tested v2 config. An active schedule and
successful manual checks do not prove a scheduler-triggered run,
uninterrupted operation, publisher-wide discovery, early detection, or evaluated alert precision.
