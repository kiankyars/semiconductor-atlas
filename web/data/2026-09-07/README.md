# Semiconductor Atlas — AI-Critical Manufacturing Baseline v1

This deterministic release contains 7 bounded facility or project-site
records across all seven required companies, 73 atomic claims,
12 evidence fragments, and
10 official source documents dated 2024 or later.

The release is not a global census and is not proof that an announced project is operating.
Yield, utilization, and qualification remain unknown for every row. Capacity remains unknown unless
an exact claim appears in `capacity.csv`; the five bases are never collapsed.

Files:

- `facilities.csv`: facility-centric consumer table with explicit unknown states;
- `claims.jsonl` and `evidence.jsonl`: atomic claims and verified evidence lineage;
- `capacity.csv`: supported capacity claims only;
- `atlas.geojson` and `atlas.html`: GeoJSON plus a standalone dependency-free interface;
- `source_inputs.json`: archived-byte, clock, rights, and attribution metadata;
- `source_ingestion_runs.json`: immutable capture-run and adapter lineage;
- `producing_run.json`: succeeded run and version/configuration provenance;
- `coverage.json`: achieved coverage and known gaps;
- `supply_intelligence.jsonl`: eligible quarter-total capacity rows (zero in v1);
- `supply_intelligence_contract.json`: eligibility and excluded-claim reasons;
- `supply_intelligence_facilities.jsonl`: separately versioned facility-scope rows;
- `METHODOLOGY.md` and `ATTRIBUTION.md`: interpretation and reuse boundary; and
- `manifest.json`: deterministic file sizes and SHA-256 hashes.

- Release ID: `2026-09-07-ai-critical-manufacturing-amkor-successor-v1-r2`
- World-state cutoff: `2026-09-07`
- Knowledge cutoff: `2026-09-07T02:31:58Z`
