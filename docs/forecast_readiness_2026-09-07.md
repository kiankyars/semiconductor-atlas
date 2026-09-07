# Forecast-readiness audit, September 7, 2026

The next forecasting gate is a leakage-safe outcome dataset and frozen forecast vintages, not
another capacity-ramp formula. This audit is about the retained working evidence, not worldwide
manufacturing or the absence of physical capacity.

## Verified evidence inventory

Read-only inspection of `artifacts/2026-09-07-eea-scope-revisions-v3.sqlite` at the retained head
`4f808891-967d-506d-98d7-d7b8c762d438` found:

| Evidence | Stored records |
| --- | --- |
| Numeric capacity | Three announced claims: two Amkor absolute rates and one Rocket Lab relative increase |
| Expected production milestones | Six `production_start` claims and two `mass_production` target statements |
| Construction observations | Two `construction_observed` / `started` claims |
| Completed production milestones | None |
| Non-announced numeric capacity | None |
| Yield or utilization predicates | None |

The EEA admission adds 132 source-native scalars, not production outcomes. The retained AI-critical
baseline separately contains outcome-like evidence such as TSMC Fab 21 HVM at end-2024. Those
passages are not yet a reviewed realization dataset matched to forecasts that the system retained
before the outcomes. In particular, a target first admitted in July 2026 cannot become a genuine
pre-2025 system prediction by moving its knowledge clock backwards.

### First retrospective outcome lead

The retained `source_snapshots/2026-08-20-ai-critical-manufacturing-v1/tsmc-2025-form-20f.html`
is 10,355,816 bytes with SHA-256
`c3ebd05cd8fb383f53fc21a0ac497ee12cf908b709c380f9bec4f39c4916647b`.
Its operating-fab table explicitly reports the year of commencement of commercial production;
the Fab 21 row gives 2024. The reviewed raw UTF-8 spans are end-exclusive:

- Table introduction: `[1122163,1124646)`.
- Column headers: `[1125000,1128438)`.
- Fab 21 row: `[1156665,1158829)`.

This supports a year-precision commercial-production outcome lead without an invented midpoint.
The separate first-Arizona-facility HVM passage has only a vague year-end literal; do not make
commercial-production commencement and HVM interchangeable. The table's February 28, 2026
operating-state cutoff is not a production-start date. A separately reviewed source-native
identity/event definition and actual outcome-admission clock are still required. No eligible
pre-outcome forecast vintage or forecast score follows from this lead. No new SEC request or raw
publisher redistribution was performed.

## Existing scenario boundary

`forecast.py` computes twenty-quarter scenarios from fixed realization and ramp assumptions.
The `p10`/`p50`/`p90` labels are not empirically validated probability quantiles; matching labels
are summed without a fitted dependence model. Existing disclosures correctly identify the
assumptions and lack of calibration. Engineering tests establish deterministic calculations,
not predictive accuracy.

The current database yields two Amkor scenario series and excludes the relative Rocket Lab
capacity. The newer Fab 2 source statements retain unknown effective dates and confidence and
do not supply attained production or capacity.

The optional `claims=` argument to `analytics.forecast_current_capacity` trusts the caller's
selection. Supplying current Amkor claims with year-2000 world and knowledge cutoffs still returns
two results, whereas querying those cutoffs directly returns none. The release CLI currently
preselects its claims; this audit does not establish leakage in an existing release. A new
evaluation workflow must independently verify every supplied input against its frozen vintage
and exact source lineage. The registered study's pinned analytics code remains unchanged.

## Required next evidence and evaluation

1. Define an exact event and project phase. Opening, production start, high-volume manufacturing
   and qualification must not be substituted for one another.
2. Freeze the eligibility roster, project grouping, geographic holdout and chronological splits
   before scoring. Keep unresolved, delayed and cancelled projects in the denominator.
3. Retain model/configuration/input hashes and actual forecast-admission clocks. Reconstruct
   source and claim availability at each vintage; later-collected historical evidence is a
   retrospective reconstruction, not an earlier system prediction.
4. Separately review later outcomes with source hashes, event intervals, reporting/retrieval/
   admission clocks and exact project-phase identity. Preserve unknown and right-censored cases.
5. Score only supported forecast/outcome pairs. Report the missing and excluded denominator even
   when there are zero eligible pairs. Do not convert company guidance into realized truth.

This is the missing evaluation substrate for [roadmap Phase 4](roadmap.md#phase-4-facility-level-forecasts).
It does not establish calibration, close the source-collection coverage gaps, or change the
prospective source-target detector study. Economic scenarios remain downstream of these gates.
