-- Extend source-native numeric accounting without creating physical capacity.
CREATE TABLE source_quantity_metadata_v8 (
    claim_version_id TEXT PRIMARY KEY REFERENCES scalar_values(claim_version_id),
    metric TEXT NOT NULL CHECK (metric IN ('wafer_capacity', 'wafer_shipments', 'wafer_output', 'capacity_utilization', 'produced_wafer_area')),
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('company_aggregate', 'source_native_facility', 'source_native_project', 'source_native_site')),
    scope_label TEXT NOT NULL CHECK (length(trim(scope_label)) > 0),
    period_kind TEXT NOT NULL CHECK (period_kind IN ('year', 'quarter', 'fiscal_year')),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL CHECK (period_end >= period_start),
    source_period_label TEXT NOT NULL CHECK (length(trim(source_period_label)) > 0),
    unit TEXT NOT NULL CHECK (unit IN ('12_inch_equivalent_wafers', 'native_wafers', 'percent', 'square_centimeters')),
    native_wafer_diameter_inches INTEGER CHECK (native_wafer_diameter_inches IN (6, 8, 12)),
    source_number_literal TEXT NOT NULL CHECK (length(trim(source_number_literal)) > 0),
    source_unit_scale INTEGER NOT NULL CHECK (typeof(source_unit_scale) = 'integer' AND source_unit_scale IN (1, 1000, 1000000)),
    numeric_qualifier TEXT NOT NULL CHECK (numeric_qualifier IN ('as_reported_precision_unspecified', 'approximate', 'greater_than', 'at_least', 'less_than', 'at_most')),
    assertion_kind TEXT NOT NULL CHECK (assertion_kind IN ('reported', 'estimate', 'guidance')),
    source_definition TEXT NOT NULL CHECK (length(trim(source_definition)) > 0),
    CHECK ((unit = 'native_wafers' AND native_wafer_diameter_inches IS NOT NULL)
        OR (unit != 'native_wafers' AND native_wafer_diameter_inches IS NULL)),
    CHECK ((metric = 'capacity_utilization' AND unit = 'percent')
        OR (metric != 'capacity_utilization' AND unit != 'percent')),
    CHECK ((metric = 'produced_wafer_area' AND unit = 'square_centimeters')
        OR (metric != 'produced_wafer_area' AND unit != 'square_centimeters')),
    CHECK (period_kind != 'fiscal_year' OR COALESCE(
        period_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-01'
        AND CAST(substr(period_start, 1, 4) AS INTEGER) BETWEEN 1 AND 9998
        AND CAST(substr(period_start, 6, 2) AS INTEGER) BETWEEN 2 AND 12
        AND period_end = date(period_start, '+1 year', '-1 day'), 0))
);

INSERT INTO source_quantity_metadata_v8 (
    claim_version_id, metric, scope_kind, scope_label, period_kind, period_start,
    period_end, source_period_label, unit, native_wafer_diameter_inches,
    source_number_literal, source_unit_scale, numeric_qualifier, assertion_kind,
    source_definition
)
SELECT claim_version_id, metric, scope_kind, scope_label, period_kind, period_start,
    period_end, source_period_label, unit, native_wafer_diameter_inches,
    source_number_literal, source_unit_scale, numeric_qualifier, assertion_kind,
    source_definition
FROM source_quantity_metadata;

DROP TABLE source_quantity_metadata;
ALTER TABLE source_quantity_metadata_v8 RENAME TO source_quantity_metadata;

CREATE TRIGGER source_quantity_metadata_type_guard BEFORE INSERT ON source_quantity_metadata
WHEN NOT EXISTS (
    SELECT 1 FROM claim_versions c JOIN claim_series s ON s.id=c.series_id
    JOIN scalar_values v ON v.claim_version_id=c.id
    JOIN entities e ON e.id=s.subject_entity_id
    WHERE c.id=NEW.claim_version_id AND c.claim_kind='source_statement'
      AND c.valid_from IS NULL AND c.valid_to IS NULL AND c.confidence IS NULL
      AND s.predicate='source_quantity.' || NEW.metric
      AND v.scalar_type IN ('integer', 'number') AND v.unit=NEW.unit
      AND ((NEW.scope_kind='company_aggregate' AND e.kind='organization')
        OR (NEW.scope_kind='source_native_facility' AND e.kind='facility')
        OR (NEW.scope_kind='source_native_project' AND e.kind='project')
        OR (NEW.scope_kind='source_native_site' AND e.kind='site'))
)
BEGIN SELECT RAISE(ABORT, 'source quantity requires a same-unit source scalar with unknown effective time and matching subject scope'); END;

CREATE TRIGGER source_quantity_metadata_immutable_update BEFORE UPDATE ON source_quantity_metadata
BEGIN SELECT RAISE(ABORT, 'source quantity metadata is immutable'); END;
CREATE TRIGGER source_quantity_metadata_immutable_delete BEFORE DELETE ON source_quantity_metadata
BEGIN SELECT RAISE(ABORT, 'source quantity metadata is immutable'); END;
