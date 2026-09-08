-- Preserve reported completed-event calendar precision without inventing a midpoint.
-- Only this leaf value table is rebuilt; parent claims and historical values are unchanged.

DROP TRIGGER milestone_values_immutable_delete;
DROP TRIGGER milestone_values_immutable_update;

CREATE TABLE new_milestone_values (
    claim_version_id TEXT PRIMARY KEY,
    value_kind TEXT NOT NULL DEFAULT 'milestone' CHECK (value_kind = 'milestone'),
    milestone_type TEXT NOT NULL CHECK (length(trim(milestone_type)) > 0),
    status TEXT NOT NULL CHECK (status IN (
        'expected', 'started', 'completed', 'delayed', 'cancelled'
    )),
    date_low TEXT NOT NULL,
    date_base TEXT,
    date_high TEXT NOT NULL,
    date_precision TEXT,
    date_literal TEXT,
    FOREIGN KEY (claim_version_id, value_kind)
        REFERENCES claim_values(claim_version_id, value_kind) ON DELETE CASCADE,
    CHECK (
        (date_precision IS NULL AND date_literal IS NULL AND date_base IS NOT NULL
         AND date_low <= date_base AND date_base <= date_high)
        OR (date_precision IS NOT NULL AND date_precision IN ('day', 'month', 'quarter', 'half_year', 'year', 'range')
            AND date_literal IS NOT NULL AND length(trim(date_literal)) > 0
            AND date_base IS NULL AND status IN ('expected', 'completed') AND date_low <= date_high)
    )
);
INSERT INTO new_milestone_values (
    claim_version_id, value_kind, milestone_type, status, date_low, date_base, date_high,
    date_precision, date_literal
) SELECT claim_version_id, value_kind, milestone_type, status, date_low, date_base, date_high,
         date_precision, date_literal
FROM milestone_values;
DROP TABLE milestone_values;
ALTER TABLE new_milestone_values RENAME TO milestone_values;

CREATE TRIGGER milestone_values_immutable_delete BEFORE DELETE ON milestone_values
BEGIN SELECT RAISE(ABORT, 'milestone values are immutable'); END;

CREATE TRIGGER milestone_values_immutable_update BEFORE UPDATE ON milestone_values
BEGIN SELECT RAISE(ABORT, 'milestone values are immutable'); END;
