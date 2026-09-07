# Source-reported realized milestone observations

`semiconductor_atlas.realized_milestones` admits a bounded, retrospective source
statement without requiring a forecast or inventing a midpoint. It creates a
reference-only sidecar, never a core database claim. The initial supported meaning
is the issuer operating-fab table's **Fab 21 commercial-production commencement
year**, not an Arizona project phase, first-production date or HVM date.

## API and supported rule

```python
validate_review(review, *, body: bytes, provenance: bytes) -> dict
admit(review, *, body: bytes, provenance: bytes) -> dict
verify(artifact, *, body: bytes, provenance: bytes) -> dict
canonical_bytes(value: dict) -> bytes
write_new(path, artifact) -> None
```

Review inputs can be dictionaries or UTF-8 JSON bytes. Duplicate keys, extra review
fields and nonfinite numbers fail. `verify` accepts a sealed dictionary or exact
canonical artifact bytes. All operations are local; none accesses a database or
network. `write_new` is the existing milestone-benchmark no-replace writer, reused
unchanged. See [the local commands](realized_milestone_cli.md) for file handling.

The event vocabulary distinguishes `commercial_production_commencement`,
`production_start` and `high_volume_production`. Only the first is admitted by
`tsmc_operating_fab_commercial_production_year_v1`. Other event types, narrative
claims, other subjects and day/month/quarter/half-year precision need a separately
reviewed semantic rule. This is deliberate abstention, not an event alias.

The exact review fields are:

| Field | Required content |
| --- | --- |
| `format` | `semiconductor-atlas-realized-milestone-review-v1` |
| `validation_rule` | `tsmc_operating_fab_commercial_production_year_v1` |
| `subject` | Exact object below; no canonical entity or inferred location |
| `event` | `event_type`, `low`, `base`, `high`, `precision`, `literal` |
| `source` | `source_id`, `url`, `content_sha256`, `bytes`, `published_at`, `published_at_precision`, `retrieved_at` |
| `provenance` | Exact raw metadata `sha256` and `reference` of `baselines/ai_critical_manufacturing_v1.json` |
| `evidence` | Exactly `intro`, `table`, `header`, `row`, each with `start`, `end`, `sha256` |
| `review` | `reviewed_by`, `reviewed_at`, substantive `prior_exposure` and `rationale` |

```json
{
  "source_native_id": "tsmc-2025-20f:fab:21",
  "kind": "facility",
  "label": "Fab 21",
  "scope": "issuer_operating_fab_table",
  "canonical_entity_id": null,
  "location": null
}
```

A literal year such as `2024` requires `low="2024-01-01"`, `base=null`,
`high="2024-12-31"`, `precision="year"`. Bounds are inclusive calendar bounds,
not claimed exact dates. A phrase such as “at the end of 2024” is not accepted as
this literal and must not be converted into December 31 or a fourth-quarter claim.

## Source, layout and provenance binding

The source identity is exactly `tsmc-2025-20f` and its named SEC accession URL.
There is no fixed observed-body hash whitelist. Supplied retained bytes must match
both the review hash/size and the unique selected `sources` row in the supplied
baseline. The baseline itself is bound by exact raw-byte hash, including whitespace.
Its source ingestion run must uniquely match the source and primary input digest,
be successful, and have consistent capture/start/finish/recorded/review clocks.
Complete source-row and ingestion-run canonical hashes are retained in the result;
the full baseline is not copied into it. These hashes use the shared pretty-JSON
canonicalization, not compact JSON or the baseline's normalized excerpt hashes.

Evidence spans are end-exclusive UTF-8 **byte** offsets. All four span hashes are
checked. The parser reads the actual preceding HTML to check ancestors; detached
fragments do not suffice. Explicit hidden/collapsed ancestors fail. External CSS
or browser-rendered visibility is not certified.

The complete bounded table must immediately follow its reviewed introduction.
The introduction must identify the operating-fab table and its separate as-of date.
The selected header and row must be actual rows within that same table. The parser
requires the seven-column layout including empty spacer columns 1, 3 and 5,
unmerged cells, the exact commercial-production commencement header, and a unique
Fab 21 row with its year in column 2. Other data rows must match the recognized
layout. Nested tables, captions, unparsed text outside cells, changed predicates,
duplicate Fab 21 identities, borrowed rows/years and unsupported layouts fail.
The wafer-size and technology cells are layout checks, not admitted measurements.

This rule bounds a reviewed source statement; it is not whole-document claim
recall or source authenticity verification. A caller can construct synthetic bytes
and matching metadata, as the engineering tests do. Those are not real publisher
evidence. A substantive source review remains necessary.

## Clocks, replay and rights

Event calendar bounds, source publication day, retrieval/acquisition, provenance
recording, declared review, validation start and actual admission are distinct.
The complete realized year must precede publication. Capture cannot postdate the
ingestion run or baseline recording. A declared review cannot predate those inputs
or lie in the future. `admit` obtains its own current clocks, checks monotonicity,
and does not allow the caller to choose a historical admission date. The old
baseline recording is provenance, not this observation's admission.

The artifact seals its review, source/run hashes, clocks, rights, boundaries and
producer/dependency hashes. Replay repeats source and semantic validation and
rejects mismatched code, resealed subject/event substitutions and impossible
clocks. Code bytes must match those present when the module was loaded and remain
unchanged across admission/replay. Local clocks/hashes are not external timestamp
attestations: a forged fully resealed past history cannot be disproved without
separately retained trusted receipts.

`validate_review` returns `local_evidence_text` for local inspection only. Admission
and verification outputs do **not** contain that text, publisher HTML, body encodings
or baseline bytes. They retain only reference metadata, evidence offsets/hashes,
the date literal, the facility label and the review declaration. Review rationales
should be original analysis, not copied source excerpts. The artifact explicitly
grants no redistribution permission, independent-review certification or physical
operation corroboration.

No forecast score, canonical identity, lifecycle enum, usable capacity, qualification,
yield, utilization or technology-at-commencement is inferred. Linking an observation
to a pre-outcome forecast requires a separate exact-identity/event and vintage review;
this module does not perform that linkage. Its tests are synthetic engineering
fixtures, not historical outcomes or evidence of calibrated forecasting.

The separately executed [September 7 Fab 21 admission](fab21_realized_observation_2026-09-07.md)
records the real local artifact and replay hashes; testing alone did not create that observation.
