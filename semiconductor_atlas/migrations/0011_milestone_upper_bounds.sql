-- Add one-sided source-reported occurrences without changing legacy value payloads.
DROP TRIGGER milestone_values_immutable_delete;
DROP TRIGGER milestone_values_immutable_update;

CREATE TABLE new_milestone_values (
    claim_version_id TEXT PRIMARY KEY,
    value_kind TEXT NOT NULL DEFAULT 'milestone' CHECK (value_kind = 'milestone'),
    milestone_type TEXT NOT NULL CHECK (length(trim(milestone_type)) > 0),
    status TEXT NOT NULL CHECK (status IN ('expected', 'started', 'completed', 'delayed', 'cancelled')),
    date_low TEXT,
    date_base TEXT,
    date_high TEXT NOT NULL,
    date_precision TEXT,
    date_literal TEXT,
    date_relation TEXT,
    FOREIGN KEY (claim_version_id, value_kind)
        REFERENCES claim_values(claim_version_id, value_kind) ON DELETE CASCADE,
    CHECK (
        (date_relation IS NULL AND date_low IS NOT NULL AND date_precision IS NULL
         AND date_literal IS NULL AND date_base IS NOT NULL
         AND date_low <= date_base AND date_base <= date_high)
        OR (date_relation IS NULL AND date_low IS NOT NULL AND date_precision IS NOT NULL
            AND date_precision IN ('day', 'month', 'quarter', 'half_year', 'year', 'range')
            AND date_literal IS NOT NULL AND length(trim(date_literal)) > 0
            AND date_base IS NULL AND status IN ('expected', 'completed') AND date_low <= date_high)
        OR (date_relation IS NOT NULL AND date_relation = 'on_or_before'
            AND typeof(milestone_type) = 'text' AND typeof(date_literal) = 'text'
            AND length(trim(milestone_type, char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288))) > 0
            AND date_low IS NULL AND date_base IS NULL AND status = 'completed'
            AND date_precision IS NOT NULL AND date_precision = 'day'
            AND date_literal IS NOT NULL
            AND length(trim(date_literal, char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288))) > 0
            AND length(date_high) = 10
            AND date_high GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
            AND date_high >= '0001-01-01' AND date_high <= '9999-12-31'
            AND date(date_high, '+0 days') IS NOT NULL AND date(date_high, '+0 days') = date_high)
    )
);
INSERT INTO new_milestone_values (
    claim_version_id, value_kind, milestone_type, status, date_low, date_base, date_high,
    date_precision, date_literal
) SELECT claim_version_id, value_kind, milestone_type, status, date_low, date_base, date_high,
         date_precision, date_literal FROM milestone_values;
DROP TABLE milestone_values;
ALTER TABLE new_milestone_values RENAME TO milestone_values;

CREATE TRIGGER milestone_values_immutable_delete BEFORE DELETE ON milestone_values
BEGIN SELECT RAISE(ABORT, 'milestone values are immutable'); END;
CREATE TRIGGER milestone_values_immutable_update BEFORE UPDATE ON milestone_values
BEGIN SELECT RAISE(ABORT, 'milestone values are immutable'); END;
CREATE TRIGGER milestone_upper_bound_source_guard BEFORE INSERT ON milestone_values
WHEN NEW.date_relation IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM claim_versions WHERE id = NEW.claim_version_id
        AND claim_kind = 'source_statement' AND valid_from IS NULL AND valid_to IS NULL
        AND confidence IS NULL
    ) THEN RAISE(ABORT, 'milestone upper bound requires source-only unknown effective dates and confidence') END;
END;
