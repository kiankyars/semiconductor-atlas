# Frozen source runtimes for historical replay

Historical artifacts pin producer bytes, while Python imports and migration discovery reach
beyond those individual hashes. Editing the current package must not silently reinterpret older
evidence. This workflow retains committed Atlas source and runs selected historical verifiers
in a fresh, isolated subprocess. It is a code-isolation mechanism, not a hermetic operating
system, security sandbox, new study, database migration or scheduling change.

## Retention and trust boundary

`scripts/frozen_runtime.py` imports no Atlas package. Retention requires an explicit 40-character
Git commit, not a branch name, and reads committed blobs with `git --no-replace-objects`.
Dirty files and new working-tree migrations are not included. The selection contains:

- All tracked regular files under `semiconductor_atlas/`, including package initializers and SQL migrations.
- All tracked regular files under `scripts/` and the root `pyproject.toml`.
- `web/atlas-template.html` and `web/generate_atlas.py`, implicit baseline-validation dependencies.

Evidence data roots, source bodies, queues, databases and release payloads are not copied.
The local JSON runtime stores each selected file's bytes, Git blob identity, mode, size and
SHA-256, plus commit/tree provenance, actual retention time and controller/interpreter identities.
It rejects symlinks, gitlinks, unsafe or case-colliding paths, duplicate JSON keys, invalid content
bindings and oversized inventories. Output uses new-only publication and exact read-back;
existing files are never replaced. A failure after publication may leave a file requiring
inspection, not a reason to overwrite it.

Verification requires both an externally supplied exact runtime-file SHA-256 and expected commit.
Changing a bundle and recomputing its internal hashes cannot satisfy the original trusted hash.
Commit/tree membership is sourced from Git during retention; the offline verifier checks the
trusted bundle and contained Git blob hashes but does not independently reconstruct the commit's
tree or query Git again. These are local provenance checks, not a signed external attestation.

The interpreter record includes implementation, full version, executable hash and SQLite version.
Replay requires the same identity; it does not download or install an older interpreter. Standard
library files, dynamically linked libraries, kernel and hardware are not fully snapshotted. A
future interpreter upgrade needs an explicit compatibility review, not an ignored mismatch.

## Isolated, read-only replay

`scripts/replay_frozen_runtime.py` allows exactly these operations:

| Operation | Retained command | Validation scope |
| --- | --- | --- |
| `realized-milestone` | `record_realized_milestone.py verify` | Exact local source, provenance and prior observation |
| `realized-cohort` | `record_realized_fab_cohort.py verify` | Complete table, source bytes, anchor, clocks and cohort |
| `target-registration` | `shadow_source_targets.py verify` | Registered code and retained source-input population |
| `target-policy` | `evaluate_prospective_source_targets.py verify-policy` | Registered policy, code and inputs |
| `target-population` | `evaluate_project_targets.py verify` | Retained census consistency; not independent live-core/source authentication |
| `guidance-interval` | `guidance_interval_baseline.py verify` | Re-execute the frozen comparator using its original database, evidence and trusted vintage hash |
| `source-guidance-diagnostic` | `evaluate_source_guidance.py ... verify` | Reconstruct a previously executed exposed source-statement diagnostic from the trusted review/report hashes and retained evidence; no new assessment or historical forecast vintage |
| `legacy-pmt-outcome-diagnostic` | `evaluate_legacy_pmt_outcomes.py ... verify` | Reconstruct the full PMT population and separately pinned legacy-source outcome; preserve batch-clock provenance without synthesizing HTTP evidence |
| `tsmc-source-quantities` | `admit_tsmc_source_quantities.py verify` | Reconstruct the entire existing quantity successor from its original parent, review and source bytes |
| `ase-k18b-milestones` | `admit_ase_k18b_milestones.py verify` | Reconstruct the entire existing milestone successor from its parent and frozen handoff; no live queue dependency |
| `infineon-villach-output` | `admit_infineon_villach_output.py verify` | Reconstruct the entire existing schema-8 successor from its parent, explicit admission review and complete retained PDF/policy/visual evidence chain |
| `tower-closure-milestones` | `admit_tower_closure_milestones.py verify` | Reconstruct the entire existing schema-8 milestone successor from its parent, admission review and complete retained HTML/policy evidence graph |

There is no arbitrary script/command route, acquisition, admission, study advancement, scoring or
migration operation. Optional `--receipt` saves only a new local verification receipt. Each
operation requires its exact named input fields and absolute evidence paths. Evidence remains at
its original location; `--reference-root` points to the original repository, not the code runtime.
The guidance operation additionally requires `--trusted-vintage-sha256` as an exact lowercase
64-character digest, not a path. It exposes only `verify`, never `freeze`, outcome admission or scoring.

Verified source bytes are materialized into a private temporary directory. Python runs with
`-I -S -B`, a sanitized environment and the retained root explicitly added to its import path.
This excludes the current working directory, `PYTHONPATH`, site startup hooks and bytecode caches
from package selection. Existing Atlas modules in the controller process are not reused. Loaded
Atlas, scripts and web module origins are audited against the retained inventory; source bytes,
inventory, original bundle, interpreter and controller hashes are checked again after execution.
Timeouts, excessive output, nonzero exit, changed source files and missing/escaped module origins
cannot produce a success receipt. Temporary copies are removed after replay; retained evidence
and the runtime bundle remain unchanged.

## Verified September 7 runtime

Retained local file: `artifacts/2026-09-07-f901691-frozen-runtime-v1.json`.

- Commit: `f901691f9fba998d1e59b450e1f24aaba265f875`.
- Git tree: `9ce70d73ea3bebe2aa9c89d4b063dd0ce5894350`.
- Runtime file: 3,818,374 bytes; 113 files containing 2,838,856 source bytes.
- Exact file SHA-256: `4e1c106943fd38c0a531e428325b9fb06585d3f6a25c26c92a21a92e5965ed38`.
- Actual retention: `2026-09-07T21:09:22.491237Z`.
- Interpreter: CPython 3.14.7, SQLite 3.53.4; executable SHA-256
  `87d4df53fd91304be5bac391fb204643c36b7df2023c04a0953bcbc7d4fdf634`.

All five operations passed against existing real local artifacts. The import audit found 20
project modules for Fab 21, 21 for the cohort, 34 for registration, 35 for policy and 32 for the
historical population; every recorded origin is inside the retained runtime. Cohort replay
preserves 16 rows, 15 new admissions and the one original Fab 21 admission. Registration retains
seven documents; the historical population retains one opportunity and one prediction without
claiming independent source authentication or predictive accuracy.

The five new local receipts have prefix `artifacts/2026-09-07-frozen-runtime-`:

| Receipt suffix | Exact file SHA-256 |
| --- | --- |
| `fab21-replay-v1.json` | `d7601245b96e3f6eeca5ef4bdc6db2344f7ae4b061311c43071d95fd7e08bb17` |
| `cohort-replay-v1.json` | `cc5b9fd9a1fc8b6c6d48b1a8b3084498795e69c7b6678a6e3707e0285d55952b` |
| `registration-replay-v1.json` | `46a2bab6466339de9045e5b5084772c5d3aa52e94549f6f635af8a67c6a6bfe2` |
| `policy-replay-v1.json` | `309576c6b64cfc9bbdc63aa1f51d459af174c32775bd200d3869b3fbf75ff267` |
| `population-replay-v1.json` | `440ab5c9560d6dec25b15ac76663a8c0edc80ecd3778305c88d6a8371cf66cda` |

Replay the existing cohort without creating another observation or overwriting a receipt:

```sh
python3 scripts/replay_frozen_runtime.py \
  --bundle /Users/kian/Developer/semiconductor_atlas/artifacts/2026-09-07-f901691-frozen-runtime-v1.json \
  --sha256 4e1c106943fd38c0a531e428325b9fb06585d3f6a25c26c92a21a92e5965ed38 \
  --commit f901691f9fba998d1e59b450e1f24aaba265f875 \
  --working-directory /Users/kian/Developer/semiconductor_atlas \
  realized-cohort \
  --cohort /Users/kian/Developer/semiconductor_atlas/artifacts/2026-09-07-tsmc-operating-fab-realizations-v1.json \
  --source-body /Users/kian/Developer/semiconductor_atlas/source_snapshots/2026-08-20-ai-critical-manufacturing-v1/tsmc-2025-form-20f.html \
  --provenance /Users/kian/Developer/semiconductor_atlas/baselines/ai_critical_manufacturing_v1.json
```

## Remaining core-integration gate

Sixteen synthetic tests cover committed-vs-dirty code, newly added migrations, hostile import
paths and startup hooks, source/input/output changes, trusted hashes, output races, interpreter
drift, allowed operations, process failure, timeout and output bounds. These engineering checks
do not establish manufacturing truth or forecasting performance.

The full suite passed 1,392 core tests in 234.668 seconds, plus 12 web tests, Python syntax checks
and source/wheel builds. A separate read-only check matched the complete 113-file selection,
modes, Git blob identities and decoded bytes directly against the recorded commit. The old
realization artifacts, source metadata, working database, legacy checksum ledger and frozen
detector producer manifests remain unchanged.

The later isolated [schema-6 workflow](completed_milestone_core.md) closes the completed/year/
null-midpoint storage gap and admits the reviewed cohort as source statements. Keep schema-5 evidence databases,
migrations 1–5, old artifacts and current scheduled execution unchanged. A code bundle alone
does not reroute an existing schedule: its original script paths still load the original checkout.
Evolve schema 6 in a separately isolated working checkout/database until the execution-routing
transition has been explicitly handled. Do not repin an active study to new code, alter its
window, or backdate historical observations. This runtime removes a replay obstacle; it does not
establish canonical identity, add forecast/outcome pairs or satisfy the north-star.

## September 8 guidance-producer retention

Before further source-quantity model evolution, the isolated successor retained committed producer
`0a78d259d4134eb47276bc8afecbed05b1f9cd51` in
`artifacts/2026-09-08-0a78d25-frozen-runtime-v1.json`:

- Whole-file SHA-256: `822159a351602a49f6a46b0a2e9460028e5e4a595dd11e2e729a19721b1b3b4a`.
- 3,973,073 bundle bytes; 123 files containing 2,952,711 source bytes.
- No database, source body, review plan or forecast artifact is embedded.

The new allowlisted `guidance-interval` operation replayed the real September 8 vintage in a
fresh retained-code process. All 871,408 vintage bytes matched its independently retained SHA-256
`d7d0b539717b3c51799710190e21c40247e8d62b20908d7035992a1ba7de4b00`.
The audit resolved all 22 imported project modules inside the retained runtime. Counts remain
seven screened, six retained, three issued, three abstained and one excluded. No facts, new
prediction vintage or outcome score was created.

Receipt: `artifacts/2026-09-08-frozen-guidance-replay-v1.json`, 3,450 bytes, SHA-256
`e5b22f292a03972e99edc8159a259e493cf228098c3d1c37298cb49f60a88d30`.

```sh
python3 scripts/replay_frozen_runtime.py \
  --bundle /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-08-0a78d25-frozen-runtime-v1.json \
  --sha256 822159a351602a49f6a46b0a2e9460028e5e4a595dd11e2e729a19721b1b3b4a \
  --commit 0a78d259d4134eb47276bc8afecbed05b1f9cd51 \
  --working-directory /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  guidance-interval \
  --database /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-08-tsmc-fab-correspondence-schema6.sqlite \
  --vintage /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-08-guidance-interval-baseline-v1.json \
  --trusted-vintage-sha256 d7d0b539717b3c51799710190e21c40247e8d62b20908d7035992a1ba7de4b00
```

The original comparator code inventory remains strict, including every package Python/SQL file.
Evolving that inventory must use this old runtime for the old vintage, not weaken its fingerprint
check. The current interpreter must still match. The original parent checkout, detector runtime
and scheduled execution are not repinned by this retention.

All 123 decoded runtime members were independently compared with `git show` at the retained
commit and matched. Twenty runtime tests pass, including four new tests for literal digest
handling, malformed/missing inputs, the verify-only CLI and replay after current-code changes.
These tests and the real replay establish producer isolation, not forecasting performance.

That September 8 regression run completed 1,523 core tests in 241.024 seconds: 1,522 passed
and one expected optional-payload test skipped. All 12 web tests and Python syntax checks passed.
The parent checksum ledger, baseline, analytics producer and schema-5 database hashes, plus the
child schema-6 database and frozen forecast hashes, remained unchanged.

## September 8 source-quantity producer retention

The committed schema-7 producer `1944fcc73ff882d20cdee9d340cf1a598bef76e8` was retained before
evolving acquisition code. Its runtime is `artifacts/2026-09-08-1944fcc-frozen-runtime-v1.json`,
4,012,151 bytes, SHA-256 `71a8fbf7f773274d2818ea5cb59ceab525dc6a4e4013a19994312e51a3f86b14`.
All 127 members (2,981,143 source bytes) were separately compared with their committed Git blobs.

The added `tsmc-source-quantities` operation permits only `verify`. It requires absolute `parent`,
`review`, `source-body` and `output` paths; **output is an existing trusted successor to read, not
a new write destination**. Both `expected-review-sha256` and `expected-output-sha256` are mandatory
literal lowercase digests. No admission or migration command is exposed. Reconstruction applies
the historical migration in private memory, never to the input database.

Real isolated replay reproduced all 241,917,952 schema-7 database bytes and the four source quantities
after current acquisition code changed. All 19 loaded project modules resolved inside retained
code. Receipt `artifacts/2026-09-08-frozen-source-quantity-replay-v1.json` is 7,463 bytes, SHA-256
`68b72ebbbb30f7dd281d5c8a8fa1e5787376218d3fa1d4cef1ddfc90058d0147`.
The current evolved package is not substituted for that historical producer.

The subsequent full regression run completed 1,550 core tests in 241.484 seconds: 1,549 passed
and one expected optional-payload test skipped. All 12 web tests, Python syntax checks and both
package builds passed. Twenty-one runtime tests include the new verify-only quantity operation.

## September 8 K18B milestone evolution

Before adding the K18B importer, committed acquisition producer
`8b13432d6edffee6c1d7c2e73e888e51134cc6fb` was retained at
`artifacts/2026-09-08-8b13432-frozen-runtime-v1.json`: 4,015,807 bytes, SHA-256
`b0ab7411a3ff007d1a7daf2420d61baaf02ed9ea11544d350a0bb3a0377cbe61`.
All 127 members were separately compared to that commit's Git blobs during the K18B core audit.

The added `ase-k18b-milestones` operation accepts absolute `parent`, `review`, `reference-root` and
`output` paths plus literal `expected-review-sha256` and `expected-output-sha256`. It runs only
the existing-successor verifier: no queue argument, new admission, migration, acquisition or
forecast operation is available. Later source-review events therefore cannot prevent historical
reconstruction from the frozen reviewed history. The [core admission note](ase_k18b_core_admission.md)
records exact successor hashes and the source-to-core boundaries.

After adding the importer, the original quantity successor again reconstructed with producer
`1944fcc`, and the earlier guidance comparator again re-executed with producer `0a78d25`.
Their original database/vintage bytes remain unchanged. The new receipts are
`artifacts/2026-09-08-post-k18b-source-quantity-replay-v1.json` (7,463 bytes, SHA-256
`81db1932b9a59f63a0f59c53134dae5303f525b21a502569b96516c148caa80e`) and
`artifacts/2026-09-08-post-k18b-guidance-replay-v1.json` (3,450 bytes, SHA-256
`d2ad2ee41473d059316a2ae8ea78a76a90f9e0497dc80526141fff1dd9c0b72e`).

## September 8 Villach schema-8 producer retention

Committed producer `af5cde6a7c3606f809f7fdb28dd82d339e381529` is retained at
`artifacts/2026-09-08-af5cde6-frozen-runtime-v1.json`: 4,092,963 bytes, 132 files and
3,040,645 source bytes. Its exact file SHA-256 is
`500901b2ab1777ccea06e90d636fea27edd1e736cb09f6ae5d69b58f6892e733`.

The `infineon-villach-output` operation allows only verification, with absolute `parent`, `review`,
`reference-root` and existing `output` paths plus literal `expected-review-sha256` and
`expected-output-sha256`. It reconstructs the [complete schema-8 successor](infineon_villach_output_review.md)
from its schema-7 parent, separate claim review and all 28 retained PDF/visual/acquisition/policy
inputs. No HTTP request, live queue, new admission, scheduled migration or forecast operation is
exposed. The original admission clock is read from the independently hash-bound successor.

Isolated replay matched all 242,372,608 database bytes and the five source-native site quantities.
All 23 imported project modules resolved inside the retained runtime. Receipt
`artifacts/2026-09-08-frozen-villach-output-replay-v1.json` is 10,579 bytes, SHA-256
`64a1d23d24b7e92bf8b00845d9a08b42ac4fe3fd03cde8ed91ad5701288fd891`.
The earlier K18B and guidance producers also replayed their original outputs after schema-8
evolution. Those older artifacts remain unchanged and are not repinned to this producer.

## September 8 Tower historical-event producer retention

Committed producer `16dc0f66c84d93d9d3042adc6ca6fbb967d3ee3e` is retained at
`artifacts/2026-09-08-16dc0f6-frozen-runtime-v1.json`: 4,132,116 bytes, 134 files and
3,069,571 source bytes. Its exact file SHA-256 is
`8f24010fa92f61a123c8d7a5891088eb40b5d218bbb6bafc9ceba704781c9dc4`.
All decoded member bytes, modes and Git blob identities were separately compared with that
commit. All 93 fingerprints recorded by the admission matched the committed producing checkout.

The `tower-closure-milestones` operation accepts the same literal-digest and absolute-path
contract as the Villach verifier. It reconstructs the [three-event successor](tower_closure_source_review.md)
from the original schema-8 parent, separate admission review and complete twenty-file retained
HTML/policy graph. It performs no migration, acquisition, new admission or forecast operation.

Isolated replay matched all 242,585,600 database bytes. All 23 imported project modules resolved
inside the retained runtime. Receipt `artifacts/2026-09-08-frozen-tower-closure-replay-v1.json`
is 5,860 bytes, SHA-256 `768a9c614b72751814b5dcb9a820728f2ebba94c4e8afc32a5b3a9c55487dace`.
The old Villach and guidance outputs also replayed under their unchanged original producers;
no historical artifact or scheduled execution was repinned.

```sh
python3 scripts/replay_frozen_runtime.py \
  --bundle /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-08-16dc0f6-frozen-runtime-v1.json \
  --sha256 8f24010fa92f61a123c8d7a5891088eb40b5d218bbb6bafc9ceba704781c9dc4 \
  --commit 16dc0f66c84d93d9d3042adc6ca6fbb967d3ee3e \
  --working-directory /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  tower-closure-milestones \
  --parent /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-08-infineon-villach-output-schema8.sqlite \
  --review /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/review_plans/2026-09-08-tower-closure-admission-v1.json \
  --reference-root /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  --output /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-08-tower-closure-milestones-schema8.sqlite \
  --expected-review-sha256 d4355211ab593216b6fb4012ee83ff28454909c7da5329f2c5047afc6f5db96b \
  --expected-output-sha256 b2445daa4cc4dfb4ff56617ad2199ed9a7993f950794f5bf45a184b1d0974bea
```

## September 8 source-rate schema-9 producer retention

Committed producer `56c900f9b85d87fcd8c655aeb0627e2097b67dec` is retained at
`artifacts/2026-09-08-56c900f-frozen-runtime-v1.json`: 4,186,146 bytes, 138 files and
3,109,225 source bytes. Its SHA-256 is
`ea8e91f39fd22bfaa94e51adb8ba8cecf779b6a96c5f4f3178e145fee5d8762e`.
All decoded member bytes, modes and Git blob identities were independently compared with the
commit; all 96 code fingerprints recorded by the actual admission matched that committed producer.

The `reviewed-source-rates` operation accepts only the retained verifier. It reconstructs the
[complete Polar schema-9 successor](source_rates.md) from the original Tower schema-8 parent,
explicit admission review and 43-file acquisition/evidence graph. Isolated replay matched all
242,692,096 database bytes; all 24 imported project modules resolved inside the frozen runtime.
Receipt `artifacts/2026-09-08-frozen-polar-source-rate-replay-v1.json` is 5,969 bytes, SHA-256
`4d431c6a8cae1b7772326435af7e9d5a9361021a93589504f6bf74779adcae69`.

The older Tower producer also replayed its unchanged schema-8 image after rate implementation.
Receipt `artifacts/2026-09-08-post-rates-frozen-tower-replay-v1.json` is 5,860 bytes, SHA-256
`127556ee9d34c543755f316f46863caee36cbf9c8fda0ce5aae28a269549fdc5`.
No old producer, forecast vintage or scheduled execution was repinned.

```sh
python3 scripts/replay_frozen_runtime.py \
  --bundle /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-08-56c900f-frozen-runtime-v1.json \
  --sha256 ea8e91f39fd22bfaa94e51adb8ba8cecf779b6a96c5f4f3178e145fee5d8762e \
  --commit 56c900f9b85d87fcd8c655aeb0627e2097b67dec \
  --working-directory /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  reviewed-source-rates \
  --parent /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-08-tower-closure-milestones-schema8.sqlite \
  --review /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/review_plans/2026-09-08-polar-source-rate-admission-v1.json \
  --reference-root /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution \
  --output /Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution/artifacts/2026-09-08-polar-production-rate-schema9.sqlite \
  --expected-review-sha256 83856f759fd7741350539d68e5871616895de58f74f5bb95234fe12ea0b21823 \
  --expected-output-sha256 1b9bae15c988e69479f4d82e70bbb6f0a95f89a4d9b5bbe73ac878b9873ee51a
```
