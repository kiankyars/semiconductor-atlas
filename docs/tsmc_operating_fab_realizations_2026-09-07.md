# TSMC operating-fab source reports, September 7, 2026

A complete reviewed cohort of the retained issuer operating-fab table was admitted locally
at `2026-09-07T20:46:52.197851Z` and verified in a separate process. It contains 15 newly
accepted retrospective source reports and the unchanged prior Fab 21 observation: 16 distinct
table rows, not 17 observations. There are no deferred rows at this narrow source-report scope.

## Reviewed population

The [review](../review_plans/2026-09-07-tsmc-operating-fab-realizations.json) enumerates all
16 data rows in source order. Each row binds an exact UTF-8 byte span and hash to the source-native
fab label and commercial-production commencement year. The complete table also contains one
blank sizing row and one header row, neither of which is a facility record.

| Source-native fab | Reported commencement year | Cohort decision |
| --- | --- | --- |
| 2 | 1990 | New source report |
| 3 | 1995 | New source report |
| 5 | 1997 | New source report |
| 6 | 2000 | New source report |
| 8 | 1998 | New source report |
| 10 | 2004 | New source report |
| 11 | 1998 | New source report |
| 12 | 2001 | New source report |
| 14 | 2004 | New source report |
| 15 | 2012 | New source report |
| 16 | 2018 | New source report |
| 18 | 2020 | New source report |
| 20 | 2025 | New source report |
| 21 | 2024 | Carry forward original observation |
| 22 | 2025 | New source report |
| 23 | 2024 | New source report |

The population is conditioned on the issuer listing these fabs as operating on February 28,
2026. It excludes projects absent from that table and does not enumerate historical failures,
delays, cancellations or closures. It is therefore survivor-selected, not a training denominator
or a historical project census. The review discloses collaborative involvement and prior exposure;
the separate implementation audit is not independent physical corroboration.

Every accepted event retains `commercial_production_commencement`, year precision and a null
midpoint. No canonical identity, geography, phase, HVM alias, technology at commencement, usable
capacity, forecast vintage or forecast score was added. The table's Fab 2 is not linked to the
separate NIST Arizona Fab 2 project. This cohort does not expand or modify the published
seven-company AI-critical baseline.

## Exact retained record

Local cohort: `artifacts/2026-09-07-tsmc-operating-fab-realizations-v1.json`.

- Size: 35,467 bytes.
- Exact file SHA-256:
  `74c6b9aea5e789c2bb0e802090f97ba2eb83e8b637c583ececc9b861b01aa776`.
- Content SHA-256, excluding its own seal:
  `ea6b886b7e967ff088922415ea852f35595b421cf57ac82b0051c638eba80989`.
- Review file SHA-256:
  `c9ac7bf37d7f336dea04991e6664c857a3484f87a250f21d66bd43ce069d491b`.
- Embedded canonical review SHA-256:
  `ab71fd7a6565cc9f9742ce3e9f2cd07709d4fda142f1fa48a8f874a938fdac77`.
- Cohort producer SHA-256:
  `a23b60284d09e117d439edd12c514d30a04b02e9b6340a3112380774ad3b6875`.

The new validation began at `2026-09-07T20:46:52.075846Z`, after the review's
`2026-09-07T20:41:54Z` timestamp. Fab 21 retains `2026-09-07T20:23:31.259022Z`
from its original observation, whose exact file SHA-256 remains
`687a5d015ab40375b21967fec67cc360c930108950b88d8ac30618c067bb3610`.
None of the historical commencement years is a historical system-knowledge clock.

Verification used all 10,355,816 retained source bytes, SHA-256
`c3ebd05cd8fb383f53fc21a0ac497ee12cf908b709c380f9bec4f39c4916647b`, and the unchanged
baseline metadata, SHA-256
`8eec7a20a9d2d900161efce778532756f9224520f2ec0056bb329c5118198958`.
No new network acquisition or publisher-body redistribution occurred. The local cohort carries
source references, original review analysis and the reference-only anchor; source bodies,
excerpts and full baseline metadata are not embedded.

Replay using the [cohort CLI](realized_fab_cohort.md#local-commands), without readmission:

```sh
python3 scripts/record_realized_fab_cohort.py verify \
  --cohort artifacts/2026-09-07-tsmc-operating-fab-realizations-v1.json \
  --source-body source_snapshots/2026-08-20-ai-critical-manufacturing-v1/tsmc-2025-form-20f.html \
  --provenance baselines/ai_critical_manufacturing_v1.json
```

## Verification and remaining integration

Forty focused tests pass, including 13 independently authored regressions. They exercise complete
row enumeration, deferred cases, duplicate non-anchor labels, future or non-ASCII years, exact
source/anchor/provenance bytes, preserved prior clocks, re-sealed corruption, producer changes
during materialization and replay, and new-only output/read-back behavior. A separate direct field
check confirms the 16 expected fab/year pairs, unique report keys, counts and preserved anchor.

The full suite passed 1,376 core tests in 234.290 seconds; all 12 web tests, Python syntax checks,
and source/wheel builds passed. Both frozen detector code manifests rehashed without drift.
The original checksum ledger and baseline remain unchanged. The working database remains at
SHA-256 `7b60f67911a0cb73ce17e47451d534c6e5738e7cd804398734fdf652385db778`;
this workflow performs no database writes.

The [workflow contract](realized_fab_cohort.md) makes selection bias and evidence limitations
machine-readable. This expands the reviewed realization evidence, but core integration remains
open: the frozen schema cannot represent completed year-precision milestones with a null midpoint.
Its producers remain unchanged to preserve the registered detector study and historical replay.
Deliberate runtime-version isolation is needed before evolving that schema. Exact project/event
linkage, non-survivor coverage, justified cutoff-safe predictions and later evaluated outcomes
are still required for the forecasting gate.
