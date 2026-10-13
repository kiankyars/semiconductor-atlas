# Dresden and Kulim event-specific timing review

The September 9, 2026 local review adds six timing cases across two new-fab
projects to a separate evidence-bound diagnostic. Five targets have reviewed
calendar bounds; one equipment-readiness season remains unresolved. There are
**zero scored outcomes**, not six failures and not a forecasting-performance result.
The earlier Villach diagnostic and NIST PMT population remain unchanged.

| Event | Reviewed target | Accepted result |
| --- | --- | --- |
| Dresden Smart Power Fab opening | Calendar 2026 | Reported by July 2, 2026; interval containment unresolved |
| Dresden Smart Power Fab production startup | Calendar 2026 | Unknown |
| Kulim third-module construction start | June 2022, contextually derived year | Unknown |
| Kulim third-module equipment readiness | Summer 2024, no numeric bounds | Unresolved target and outcome |
| Kulim third-module first wafers leaving | H2 2024 | Unknown |
| Kulim new-plant production startup | Calendar 2024 | Unknown in accepted evidence |

## Evidence and interpretation

Infineon's [February 2025 Dresden guidance](https://www.infineon.com/press-release/2025/infxx202502-058)
targets the new fab's opening. Its [May 2024 guidance](https://www.infineon.com/press-release/2024/infxx202405-109)
instead targets manufacturing startup. The [July 2026 opening report](https://www.infineon.com/press-release/2026/ifxpr202607-117)
does not supply an explicit lower bound or exact event-day statement. The
diagnostic accepts occurrence by the report date, not an invented point date or
a production-start observation. An upper bound within a target year cannot alone
establish that the opening occurred within that year.

The [February 2022 Kulim announcement](https://www.infineon.com/press-release/2022/infxx202202-053)
identifies the third module and three separate timing targets. Its June wording
uses a contextually derived 2022 year; summer is not converted to a quarter.
The [May 2023 supplier-agreement release](https://www.infineon.com/press-release/2023/infxx202305-099)
separately gives a production-start year for the new Kulim plant. Its group-level
capacity aspirations and supplier deliveries are not facility output.

The [August 2024 opening narrative](https://www.infineon.com/press-release/2024/infxx202408-133)
describes the first phase and a symbolic launch. It is project context, not proof
of the dates of construction, equipment readiness, first wafers leaving or actual
production startup. Event-specific cases share project IDs; six cases do not
become six independent projects. The source-native correspondence is manually
reviewed and does not assign a canonical database identity or complete all phases.

A separately captured issuer debt prospectus has a source-specific notice on
printed page 5 restricting use to its published purpose. The PDF skill prompted
complete-page visual inspection of that notice. Clearance for this diagnostic
has not been established, so the prospectus is retained only for rights review
and supplies no accepted primary reference or outcome. Its startup lead is not
scored. Website-wide informational-use permission is not silently extended past
that unresolved source-specific notice. This is not a conclusion that the event
is false, nor a general prohibition on reading public issuer information.

All seven source bodies were acquired on September 9, 2026, between
11:21:25.613563 and 11:21:41.198580 UTC, using seven successful, identified HTTPS
GETs without redirects, retries, credentials or cookies. The six HTML documents
are retained for local informational noncommercial research with originals,
notices and Infineon attribution preserved. No raw publication, commercial use
or model training is approved. Source dates are publisher assertions, not proof
that these exact bytes were historically available or known to Atlas.

## Executed artifacts and verification

- Review: `review_plans/2026-09-09-infineon-dresden-kulim-review-v1.json`,
  22,414 bytes, SHA-256 `e8567584367f2aaef9499e774535078bf2fb06afcce465d66d3e33d952ce8054`.
- Diagnostic: `artifacts/2026-09-09-infineon-dresden-kulim-diagnostic-v1.json`,
  50,300 bytes, SHA-256 `ffeb409fad5dbe0cf214e890ed3e1923bd8597abe9d3df57cdfbc40298151ea9`.
- Capture manifest: `artifacts/2026-09-09-infineon-dresden-kulim-capture-v1/manifest.json`,
  SHA-256 `b3150d5831138f2873588302f2ffbcc716ce0f41370cb9a24d84240ace85164f`.
- Independent checker receipt: `artifacts/2026-09-09-infineon-timing-independent-check-v1.json`,
  SHA-256 `3624a9151d1657b491732a63f630955b6263f8caeb82013f77414dfc67c1e1d3`.

The diagnostic verifies 26 input files totaling 15,941,643 bytes. A separate
implementation, importing neither adapter nor scorer, checked every file/span,
primary capture identity and chronology, the six expected cases, zero prospectus
primary references and all 109 producer hashes against committed source. The main
reviewer authored that checker; a second agent independently checked source bytes
and interpretation. Neither is a blind outcome or independent manufacturing audit.
All 53 focused scoring/adapter tests and 47 frozen-runtime tests passed. No package
code changed. The protected parent collector, scheduled database, checksum ledger,
map template and r3 archive retained their previously pinned hashes.

Frozen producer `e33d6ccdbd40017c6c595b78952072c6b141c812` is retained in
`artifacts/2026-09-09-e33d6cc-frozen-runtime-v1.json`, SHA-256
`0eed3603b35f770b64527a572b5bb570684a75195ec99f6d46ec2a99e557517c`.
It contains 165 files / 3,468,585 source bytes. Isolated frozen replay reproduced
the full diagnostic exactly; all 16 imported project modules resolved inside
the frozen runtime. This authenticates reproducibility, not historical execution.

```sh
python3 scripts/replay_frozen_runtime.py \
  --bundle /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-09-e33d6cc-frozen-runtime-v1.json \
  --sha256 0eed3603b35f770b64527a572b5bb570684a75195ec99f6d46ec2a99e557517c \
  --commit e33d6ccdbd40017c6c595b78952072c6b141c812 \
  --working-directory /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  source-guidance-diagnostic \
  --review /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/review_plans/2026-09-09-infineon-dresden-kulim-review-v1.json \
  --review-sha256 e8567584367f2aaef9499e774535078bf2fb06afcce465d66d3e33d952ce8054 \
  --reference-root /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  --report /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-09-infineon-dresden-kulim-diagnostic-v1.json \
  --report-sha256 ffeb409fad5dbe0cf214e890ed3e1923bd8597abe9d3df57cdfbc40298151ea9
```

This result expands explicit target and evidence-gap coverage, not calibrated
forecasting. Rights-cleared same-event outcomes, broader non-survivor coverage,
pre-outcome predictions, measured alert utility and held-out predictive improvement
remain necessary. No core database, scheduled collector, original prediction
journal, baseline, checksum ledger or published release was changed.
