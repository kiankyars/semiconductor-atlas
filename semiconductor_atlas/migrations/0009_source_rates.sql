-- Source-native monthly rate references with no invented reference interval.
CREATE TABLE source_rate_metadata (
    claim_version_id TEXT PRIMARY KEY REFERENCES scalar_values(claim_version_id),
    metric TEXT NOT NULL CHECK (metric IN ('wafer_production_rate', 'wafer_start_capacity_rate')),
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('company_aggregate', 'source_native_facility', 'source_native_site', 'source_native_project')),
    scope_label TEXT NOT NULL CHECK (length(trim(scope_label)) BETWEEN 1 AND 2000),
    numerator_unit TEXT NOT NULL CHECK (numerator_unit IN ('native_wafers', 'native_wafer_starts')),
    denominator_unit TEXT NOT NULL CHECK (denominator_unit = 'month'),
    native_wafer_diameter_inches INTEGER CHECK (native_wafer_diameter_inches IS NULL OR (typeof(native_wafer_diameter_inches) = 'integer' AND native_wafer_diameter_inches IN (6, 8, 12))),
    reference_period_kind TEXT NOT NULL CHECK (reference_period_kind = 'not_stated'),
    reference_period_start TEXT CHECK (reference_period_start IS NULL),
    reference_period_end TEXT CHECK (reference_period_end IS NULL),
    source_reference_period_literal TEXT,
    source_number_literal TEXT NOT NULL CHECK (length(trim(source_number_literal)) BETWEEN 1 AND 26),
    numeric_qualifier TEXT NOT NULL CHECK (numeric_qualifier IN ('as_reported_precision_unspecified', 'approximate', 'greater_than', 'at_least', 'less_than', 'at_most')),
    source_qualifier_literal TEXT,
    assertion_kind TEXT NOT NULL CHECK (assertion_kind IN ('reported', 'estimate', 'guidance')),
    source_wording TEXT NOT NULL CHECK (length(trim(source_wording)) BETWEEN 1 AND 2000),
    source_definition TEXT NOT NULL CHECK (length(trim(source_definition)) BETWEEN 1 AND 2000),
    CHECK ((metric = 'wafer_production_rate' AND numerator_unit = 'native_wafers')
        OR (metric = 'wafer_start_capacity_rate' AND numerator_unit = 'native_wafer_starts')),
    CHECK ((numeric_qualifier = 'as_reported_precision_unspecified' AND source_qualifier_literal IS NULL)
        OR (numeric_qualifier != 'as_reported_precision_unspecified' AND source_qualifier_literal IS NOT NULL)),
    CHECK (source_qualifier_literal IS NULL OR (length(trim(source_qualifier_literal)) BETWEEN 1 AND 2000 AND instr(source_wording, source_qualifier_literal) > 0)),
    CHECK (source_reference_period_literal IS NULL OR (length(trim(source_reference_period_literal)) BETWEEN 1 AND 2000 AND instr(source_wording, source_reference_period_literal) > 0)),
    CHECK (instr(source_wording, source_number_literal) > 0)
);

CREATE TRIGGER source_rate_metadata_type_guard BEFORE INSERT ON source_rate_metadata
WHEN NOT EXISTS (
    SELECT 1 FROM claim_versions c JOIN claim_series s ON s.id=c.series_id
    JOIN scalar_values v ON v.claim_version_id=c.id
    JOIN entities e ON e.id=s.subject_entity_id
    WHERE c.id=NEW.claim_version_id AND c.claim_kind='source_statement'
      AND c.valid_from IS NULL AND c.valid_to IS NULL AND c.confidence IS NULL
      AND s.predicate='source_rate.' || NEW.metric
      AND v.scalar_type IN ('integer', 'number')
      AND v.unit=NEW.numerator_unit || '_per_' || NEW.denominator_unit
      AND COALESCE(v.integer_value, v.number_value)=CAST(replace(NEW.source_number_literal, ',', '') AS REAL)
      AND ((NEW.scope_kind='company_aggregate' AND e.kind='organization')
        OR (NEW.scope_kind='source_native_facility' AND e.kind='facility')
        OR (NEW.scope_kind='source_native_site' AND e.kind='site')
        OR (NEW.scope_kind='source_native_project' AND e.kind='project'))
)
BEGIN SELECT RAISE(ABORT, 'source rate requires its same-unit numeric source scalar, unknown effective dates and matching subject scope'); END;

CREATE TRIGGER source_rate_metadata_immutable_update BEFORE UPDATE ON source_rate_metadata
BEGIN SELECT RAISE(ABORT, 'source rate metadata is immutable'); END;
CREATE TRIGGER source_rate_metadata_immutable_delete BEFORE DELETE ON source_rate_metadata
BEGIN SELECT RAISE(ABORT, 'source rate metadata is immutable'); END;
