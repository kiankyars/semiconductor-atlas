# Local data

The local payload from the former composite workspace was preserved on 2026-07-28 and reconciled
into this checkout on 2026-08-18. The frozen inventory through the AI-critical baseline r3 records
353 files in Git-ignored data paths:

- `artifacts/`: 7 files
- `releases/`: 227 files
- `source_snapshots/`: 119 files

Together they total 7,800,454,831 bytes. `LOCAL_DATA_SHA256SUMS` preserves that historical inventory;
it is not a census of later monitoring packets, review queues, working databases or pilot exports.
These files are not part of a Git clone; the tracked checkout contains the source code and the
checksum inventory only.

Later work retains separate content-hashed capture manifests, queue-event exports and pilot
verification records, referenced from its tracked review plans and documentation. In particular,
the [source-native project target gate](docs/source_project_targets.md) uses a new schema-5 working
copy; it does not migrate the original checksum-listed database. A Git clone alone cannot replay
these local-evidence workflows without their retained inputs and pinned producer dependencies.

Check the integrity of the local payload from the repository root:

```sh
shasum -a 256 -c LOCAL_DATA_SHA256SUMS
```
