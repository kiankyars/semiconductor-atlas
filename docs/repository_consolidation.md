# Repository consolidation — October 13, 2026

Semiconductor Atlas is maintained on `main`. The completed-milestone research branch and the
producer correctness fixes from [PR 5](https://github.com/kiankyars/semiconductor-atlas/pull/5)
are integrated there. Historical evidence, research databases, and immutable release files
retain their original contents and dates.

Data Center Atlas is maintained in [its own repository](https://github.com/kiankyars/datacenter-atlas)
and published at <https://kiankyars.github.io/datacenter-atlas/>. The original `datacenters/`
inputs remain available here as an archive. Semiconductor Pages publishing uses the tracked
September 7 public semiconductor bundle, so a clean clone can reproduce the site. This
publishing change does not create new semiconductor observations or refresh their dates.

## Registered study runtime

Before integrating the producer changes, the September 8–14 source-target study was sealed
using its original code. All seven retained in-protocol document opportunities had on-time
prediction receipts. The seal was then replayed from the retained runtime. No sources were
fetched, predictions rewritten, or outcomes labelled.

The original committed runtime is retained locally at `artifacts/registered-study-runtime/`,
from commit `9dbbd9e207c80674aa683f4f0ac32c5daa37a9ac`. Its `retention.json` lists SHA-256
hashes for all 115 retained source files. This is separate from the evolving package on `main`.
The saved source-check automation remains paused; its original schedule and review boundaries
are preserved. Future verification of this registration must use the retained code.

| Local artifact | SHA-256 |
| --- | --- |
| `artifacts/2026-09-08-prospective-source-targets-v1/seal.json` | `50e17067a6310e3d9cefb98af567f18a2108868e8bd487bc029add5811ced740` |
| `artifacts/2026-09-15-prospective-target-source-only-v1.json` | `bbe1f065454d7d231640472c4eb1c4ac5bd36fba361fa87ffae0a29b7f8cb2dc` |
| `artifacts/2026-09-15-prospective-target-source-only-v1.json.receipt.json` | `920bfdb4cf46fe6d86798b81c2874a125108133429def9017fcf00c39552690c` |

The source-only packet was accepted on October 13, 2026, after closure. Its September 15
filename comes from the original scheduled workflow; it is not its creation or acceptance date.
It contains no outcome labels and establishes no detector accuracy or manufacturing outcome.

Verify the sealed population from the repository root:

```sh
python3 artifacts/registered-study-runtime/scripts/shadow_source_targets.py verify-seal \
  --registration artifacts/2026-09-07-prospective-source-targets-v1-registration.json \
  --reference-root .
```

The older local artifacts and snapshot bodies are required for this replay. A clean clone can
build the public site, but cannot reconstruct private historical evidence without those files.
Do not repin the old registration to new code or infer a new observation from a replay.
