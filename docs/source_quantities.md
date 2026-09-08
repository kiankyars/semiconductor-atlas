# Qualified source quantities

Schema 7 adds `source_quantity_metadata` to numeric source-statement scalars. This stores
publisher-reported quantities that cannot truthfully enter the five physical capacity bases.
It does not introduce a sixth capacity basis or convert shipment observations into production.

## Numeric and temporal meaning

Every quantity carries its metric, source-native accounting scope, complete calendar year or
quarter, source period label, unit, native wafer diameter where applicable, exact unit scale,
number literal, numeric qualifier, reported/estimate/guidance classification and source definition.
Publication and retrieval remain on the linked document; actual admission remains on the claim.
The quantity's reporting period is not the claim's physical effective interval.

The scalar number is a **reference number interpreted with its mandatory qualifier**. For example,
`greater_than` with reference 17,000,000 does not assert exactly 17,000,000 or an upper bound.
`approximate` carries no invented tolerance, probability interval or rounding range.
`as_reported_precision_unspecified` preserves a bare published number without asserting exact
physical precision. The original number literal and unit scale survive normalization into base
units. Different native wafer diameters and 12-inch equivalents remain distinct units.

The scalar, bulk source and historical query APIs include a `source_quantity` object inside the
value. A quantity predicate with missing or invalid metadata fails rather than returning an
unqualified scalar. Unannotated legacy scalars keep their old shape. `value_sha256` on the stored
scalar alone does not bind its external metadata; use the complete hydrated value and reviewed
source lineage for downstream provenance. This importer independently reconstructs the entire
successor database, including metadata and source-record payloads.

Quantity claims require `source_statement`, null effective dates and null confidence. They remain
outside the physical-current view and capacity exports. `physical_capacity_basis` is explicitly
null in their projection; there is no automatic promotion to announced, installed, qualified or
economically usable capacity. Numeric source quantities are not independent measured outcomes.

Series identity includes subject, metric, accounting scope and definition, period, unit/native
diameter, qualifier and assertion kind. A different period, native unit, scope, guidance kind or
qualifier cannot silently supersede an unlike claim. A later number with otherwise identical
semantics can supersede the prior source statement while preserving both knowledge vintages.
Equivalent exact scale representations share a normalized series; raw literals/scales remain
claim-specific metadata. A reported estimate following guidance is not, by itself, a forecast
realization. Metadata is immutable, like the scalar it annotates.

## TSMC retained-source admission

The first importer is deliberately scoped to four references from the retained 2025 Form 20-F:

| Metric | Year | Reference, million 12-inch-equivalent wafers | Qualifier |
| --- | --- | --- | --- |
| Annual capacity | 2024 | 17 | Approximate |
| Annual capacity | 2025 | 17 | Greater than |
| Wafer shipments | 2024 | 13 | Published precision unspecified |
| Wafer shipments | 2025 | 15 | Approximate |

These are company-reported aggregates. They do not allocate quantities to the sixteen operating
fabs, resolve the legal consolidation perimeter, establish wafer-out/production, or support a
shipment-to-capacity utilization calculation. Neither numerical capacity reference is assigned a
physical capacity basis. This adds no facility utilization, yield or qualification evidence.

The [review](../review_plans/2026-09-08-tsmc-source-quantities-v1.json) binds the complete capacity
and shipment sentences and the repeated capacity passage to exact UTF-8 spans in the verified
10,355,816-byte source. The repeated passage is same-document context, not independent
corroboration. This is all four quantities in those selected 2024/2025 passages, not a census of
every quantity in the filing. The bare 2024 shipment number is not assigned the approximate
qualifier by silently extending the grammar of the separate 2025 clause.

Source SHA-256: `c3ebd05cd8fb383f53fc21a0ac497ee12cf908b709c380f9bec4f39c4916647b`.
Published April 16, 2026; retained August 20, 2026 at `16:11:52Z`. This is not a new source
retrieval, fresh update, early-detection result, or pre-outcome system prediction.
The source-native company aggregate is a new unresolved organization entity, not an assignment
to a canonical legal parent or ownership graph. The reused document's older operating-table
title and `table_as_of` annotation are not applied to these separate company reporting periods.

## Admission and replay

The source-checkout CLI builds a new schema-7 snapshot from the immutable schema-6 parent. It
never migrates the parent or the scheduled checkout. The only new migration adds the metadata
table and guards; migrations 1-6 and existing evidence remain unchanged. The legacy realized-fab
importer stays explicitly schema-6-only; its migration-identity check now selects versions 1-6,
and its synthetic test fixture explicitly initializes that historical schema.

```sh
python3 scripts/admit_tsmc_source_quantities.py admit \
  --parent artifacts/2026-09-08-tsmc-fab-correspondence-schema6.sqlite \
  --review review_plans/2026-09-08-tsmc-source-quantities-v1.json \
  --source-body /Users/kian/Developer/semiconductor_atlas/source_snapshots/2026-08-20-ai-critical-manufacturing-v1/tsmc-2025-form-20f.html \
  --expected-review-sha256 45c0635c7401d481ce480c34b6c6fb1412a4c7852c58e4b323eec4f7a1535183 \
  --output NEW_SUCCESSOR_PATH
```

For read-only replay, use `verify` with the same inputs/output and add the independently retained
`--expected-output-sha256`. The verifier reads the original admission clock from the trusted
successor and regenerates every database byte from the parent, review and exact source body.
New admission uses the actual execution clock, not a caller-provided historical admission date.
The run fingerprints all package Python/SQL files, the CLI, the runtime controller and interpreter;
source, parent, review, code and successor are checked again across execution. Output installation
is new-only. These are local reproducibility checks, not independent truth or external timestamp
attestation. The core has a typed metadata contract, but source interpretation still needs review.

The inherited source rights remain local-review-only. Raw filing bytes and excerpts are not
embedded in these new source records or evidence links. No public release, training permission,
collector activation or sibling Supply Intelligence compatibility is established here.

## Executed September 8 admission

The real review was completed at `2026-09-08T20:33:43.640089Z`; its SHA-256 is
`45c0635c7401d481ce480c34b6c6fb1412a4c7852c58e4b323eec4f7a1535183`.
Admission used the actual clock `2026-09-08T20:37:23.565881Z`, run
`af9abea5-6a8d-5933-9d2a-bd88173b17df`.

The new local snapshot is `artifacts/2026-09-08-tsmc-source-quantities-schema7.sqlite`:

- 241,917,952 bytes; SHA-256 `131bcc1b1b6727cfefd4f02e7bbeeb90bdd168e528f5e78d7393892d3160a186`.
- Four new quantity claims and metadata rows, one source-native aggregate entity and one ingestion run.
- 104,458 total claims; all 104,286 inherited physical-current claims remain identical.
- Zero quantity rows before admission and four at the actual admission cutoff for the new subject.

A fresh CLI verification regenerated the complete successor and matched all bytes. A separate
read-only SQL comparison checked all 28 inherited tables and found no removed or changed old row.
It also compared the complete physical-current claim records before and after and found equality.
The machine-readable check is `artifacts/2026-09-08-source-quantities-independent-check-v1.json`,
SHA-256 `591a1849ae7b1ca4dedb91ff616465e13cbfb77c83b938975e7a99df1e507682`.
These checks verify storage/replay and scope boundaries, not independent manufacturing truth.

Validation: 19 new quantity/importer tests pass. The final full run completed 1,542 core tests
in 245.071 seconds: 1,541 passed and one expected optional-payload test skipped. All 12 web tests,
Python syntax checks and sdist/wheel builds passed. The importer remains a source-checkout CLI,
not a newly installed console entry point. The original three numeric capacity claims remain
`announced`; none of the four source quantities changes the five-basis numeric inventory.

## Remaining evidence gates

The [UMC source lead](../review_plans/2026-09-08-umc-capacity-source-review.json) still requires
permitted exact-byte acquisition and complete visual table/footnote review. TSMC company totals
do not substitute for that facility-level evidence. A future importer must retain native-unit
tables, issuer totals, estimate definitions and guidance markers before any reviewed admission.

Non-announced physical capacity, facility-specific utilization/yield/qualification, forecast
outcome pairs and calibrated predictive performance remain separate unfinished north-star gates.
The [Villach output review](infineon_villach_output_review.md) now retains and visually checks
five site-level fiscal-year produced-wafer-area totals from an issuer environmental statement.
These are not yet core quantities: schema 7 cannot express their area unit, fiscal periods or
source-native site scope. Do not coerce them into calendar-year wafer-count capacity.
The earlier frozen guidance vintage must still use its
[original retained producer](frozen_runtime.md#september-8-guidance-producer-retention), not this
evolved package. Its exact isolated replay passed again after the schema-7 code changes.
