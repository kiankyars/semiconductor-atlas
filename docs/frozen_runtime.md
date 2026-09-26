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

There is no arbitrary script/command route, acquisition, admission, study advancement, scoring or
migration operation. Optional `--receipt` saves only a new local verification receipt. Each
operation requires its exact named input fields and absolute evidence paths. Evidence remains at
its original location; `--reference-root` points to the original repository, not the code runtime.

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

The completed/year/null-midpoint schema gap is still open. Keep schema-5 evidence databases,
migrations 1–5, old artifacts and current scheduled execution unchanged. A code bundle alone
does not reroute an existing schedule: its original script paths still load the original checkout.
Evolve schema 6 in a separately isolated working checkout/database until the execution-routing
transition has been explicitly handled. Do not repin an active study to new code, alter its
window, or backdate historical observations. This runtime removes a replay obstacle; it does not
complete core integration, add forecast/outcome pairs or satisfy the north-star.
