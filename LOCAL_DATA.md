# Local data

The local payload from the former composite workspace was preserved on 2026-07-28 and reconciled
into this checkout on 2026-08-18. This checkout contains 353 files in Git-ignored data paths:

- `artifacts/`: 7 files
- `releases/`: 227 files
- `source_snapshots/`: 119 files

Together they total 7,800,454,831 bytes. `LOCAL_DATA_SHA256SUMS` records every local payload file.
These files are not part of a Git clone; the tracked checkout contains the source code and the
checksum inventory only.

Check the integrity of the local payload from the repository root:

```sh
shasum -a 256 -c LOCAL_DATA_SHA256SUMS
```
