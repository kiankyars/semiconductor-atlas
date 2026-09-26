# Fab 21 source-reported realization, September 7, 2026

One reviewed retrospective observation was admitted locally at
`2026-09-07T20:23:31.259022Z` and verified in a separate process against the retained filing
and original source metadata. This is a source-reported realized event, not a core-store claim,
independent physical corroboration, or a successful forecast.

| Field | Admitted meaning |
| --- | --- |
| Source-native subject | `tsmc-2025-20f:fab:21`, facility label `Fab 21` |
| Scope | Issuer operating-fab table; no canonical entity or project-phase assignment |
| Event | `commercial_production_commencement` |
| Source literal and precision | `2024`, year |
| Inclusive date bounds | `2024-01-01` through `2024-12-31` |
| Base date | Null; no invented midpoint |
| Location | Null in this table-only observation |
| Forecast vintages or scored pairs added | Zero |

## Evidence and interpretation

The [review](../review_plans/2026-09-07-tsmc-fab21-realized-milestone.json) binds the table
introduction, complete table, column header, and unique Fab 21 row. The seven physical cells
include blank spacer columns at indices 1, 3 and 5; the source-native fab identifier and
commercial-production commencement year occupy columns 0 and 2. The reviewer and a separate
read-only structural check agreed on that mapping. The review explicitly discloses prior
exposure and collaborative involvement; neither is independent physical corroboration.

The operating-table state cutoff is February 28, 2026, not the event date. The adjacent wafer
size and technology columns do not establish technology at commencement. The separate HVM
passage's vague year-end wording is not aliased to this event or converted into an exact day.
The location footnote supports Arizona at source level but is not admitted by this table rule.

The retained TSMC 2025 Form 20-F is 10,355,816 bytes, SHA-256
`c3ebd05cd8fb383f53fc21a0ac497ee12cf908b709c380f9bec4f39c4916647b`.
Publication is April 16, 2026; retained retrieval is August 20, 2026 at 16:11:52 UTC.
Original baseline metadata SHA-256 is
`8eec7a20a9d2d900161efce778532756f9224520f2ec0056bb329c5118198958`.
These older clocks are provenance, not the new observation's admission clock.

## Retained result and replay

Local artifact: `artifacts/2026-09-07-tsmc-fab21-realized-milestone-v1.json`.

- Size: 5,211 bytes.
- Exact file SHA-256: `687a5d015ab40375b21967fec67cc360c930108950b88d8ac30618c067bb3610`.
- Canonical content SHA-256, excluding its own seal:
  `0e331bc8b5828f6995e645bba2b072bd5dfb2894d5932a1782f53948ca39c831`.
- Canonical embedded review SHA-256:
  `f94a41397740992a781feb3a67b8fa44f5bb61115e9c715b6d0560e7627b6234`.
- Producer SHA-256:
  `a1359514c611455a591f95c4ee332afe5047daaba63f69d3a1ecd703bc2c4c28`.

From the repository root, replay without creating another observation:

```sh
python3 scripts/record_realized_milestone.py verify \
  --observation artifacts/2026-09-07-tsmc-fab21-realized-milestone-v1.json \
  --source-body source_snapshots/2026-08-20-ai-critical-manufacturing-v1/tsmc-2025-form-20f.html \
  --provenance baselines/ai_critical_manufacturing_v1.json
```

The [contract](realized_milestones.md) and [CLI](realized_milestone_cli.md) verify exact source
bytes, provenance, source-run lineage, table semantics, distinct clocks and producer hashes.
The artifact retains source references and original review analysis, not publisher bodies,
HTML fragments, or the complete baseline metadata file. It remains marked local-review-only with no
redistribution permission. No new source request or public release was made.

## Verification and preserved state

- 31 focused realized-observation tests passed, including three independent regressions.
- The final stable full suite passed 1,336 core tests; 12 web tests and package builds passed.
- An earlier full run correctly rejected mid-run code changes; the reported final run started
  fresh after producer bytes stabilized.
- Contradictory captions/noncell text, source capture after provenance recording, and producer
  drift during admission/replay now fail. The CLI also verifies the saved artifact's exact file
  hash rather than hashing the compact verification receipt.
- Both frozen detector code manifests rehashed without drift. No core schema, frozen study,
  source body, baseline metadata, historical observation, or legacy checksum ledger was changed.
- The working database stayed at SHA-256
  `7b60f67911a0cb73ce17e47451d534c6e5738e7cd804398734fdf652385db778`.

The core schema still cannot represent completed, year-precision milestones with a null midpoint.
This additive observation preserves the evidence pending deliberate core integration; it does
not bypass that constraint with SQL or an `expected` status. Exact identity/event linkage,
additional realized observations, cutoff-safe training data, and later honest forecast vintages
remain necessary before the forecasting gate can be met.
