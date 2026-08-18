# Local data

The 2026-07-28 offload from the former composite workspace was reconciled into
this checkout on 2026-08-18. The 277 files that were not already present now
live directly in the repository's ignored local-data paths:

- `artifacts/`: 7 files
- `releases/`: 158 files
- `source_snapshots/`: 112 files

Together they total 7,749,952,616 bytes. `LOCAL_DATA_SHA256SUMS` records every
restored file. These payloads remain ignored by Git; the tracked checkout is
the canonical source tree.

Verify every restored file from the repository root:

```sh
shasum -a 256 -c LOCAL_DATA_SHA256SUMS
```
