# Retained-source realized milestone commands

This workflow records a reviewed, source-reported realized event independently of a forecast.
It does not write the core database, assign a source-native facility to a canonical entity,
change the registered detector study, or create an earlier prediction vintage. A record is
evidence of what the retained source reports, not independent physical corroboration.

The [realized milestone contract](realized_milestones.md) defines the accepted review schema
and evidence checks. The command only reads explicitly provided existing local files:

```sh
python3 scripts/record_realized_milestone.py admit \
  --review /absolute/path/to/review.json \
  --source-body /absolute/path/to/retained-source.html \
  --provenance /absolute/path/to/retained-source-metadata.json \
  --output /absolute/path/to/new-local-observation.json

python3 scripts/record_realized_milestone.py verify \
  --observation /absolute/path/to/new-local-observation.json \
  --source-body /absolute/path/to/retained-source.html \
  --provenance /absolute/path/to/retained-source-metadata.json
```

These paths are placeholders, not instructions to download publisher material or register an
unreviewed observation. The output directory must already exist, with no symlink components.

Admission first validates the retained evidence, obtains an actual current admission clock,
verifies the resulting artifact, and publishes it using a no-replace writer. Existing outputs
are never overwritten. Verification repeats the source and semantic checks without creating
an output or database. Both commands print compact hash and byte-count receipts, not publisher
bodies. If publication fails after a complete file has been linked, verify that file before
deciding what to do; do not replace it merely because the command reported an error.

All file reads are bounded and reject symlinks, duplicate JSON keys, and files or parent paths
that change during the read. Publisher bodies and acquisition metadata remain local inputs;
the new artifact's rights boundaries do not grant redistribution or additional acquisition
permission. No network request, SQL statement, migration, or scheduled task is performed.

## What this does not establish

A year-precision commercial-production commencement is not an exact-day production start,
high-volume manufacturing, qualification, yield, utilization, or usable capacity. Even an
accepted realized observation has no forecast error unless an exact identity/event match and
an honestly retained pre-outcome prediction exist. Later core-store integration requires an
explicit precision-preserving data contract; raw SQL or a fabricated midpoint is not a migration.
