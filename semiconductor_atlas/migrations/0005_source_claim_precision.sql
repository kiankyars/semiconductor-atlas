-- Core source statements may retain unknown effective time and source target precision.
-- Parent-table reconstruction requires the migration runner to disable foreign keys
-- before BEGIN, then validate every foreign key before COMMIT.

DROP TRIGGER claim_versions_content_immutable;
DROP TRIGGER claim_versions_immutable_delete;
DROP TRIGGER claim_versions_kind_insert;
DROP TRIGGER claim_versions_supersession_transition;
DROP TRIGGER entity_resolution_candidates_integrity_guard;
DROP TRIGGER milestone_values_immutable_delete;
DROP TRIGGER milestone_values_immutable_update;
DROP TRIGGER organization_identifier_claim_metadata_type_guard;
DROP TRIGGER organization_name_claim_metadata_type_guard;

CREATE TABLE new_claim_versions (
    id TEXT PRIMARY KEY,
    series_id TEXT NOT NULL REFERENCES claim_series(id),
    value_kind TEXT NOT NULL CHECK (value_kind IN (
        'scalar', 'geometry', 'relationship', 'milestone', 'capability', 'capacity',
        'resource', 'constraint'
    )),
    value_sha256 TEXT NOT NULL CHECK (
        length(value_sha256) = 64
        AND value_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    valid_from TEXT CHECK (valid_from IS NULL OR length(trim(valid_from)) > 0),
    valid_to TEXT,
    recorded_at TEXT NOT NULL CHECK (length(trim(recorded_at)) > 0),
    superseded_at TEXT,
    claim_kind TEXT NOT NULL CHECK (claim_kind IN (
        'source_statement', 'direct_observation', 'reconciled_fact', 'derived_estimate'
    )),
    method TEXT NOT NULL CHECK (length(trim(method)) > 0),
    confidence REAL CHECK (confidence >= 0 AND confidence <= 1),
    created_by_run_id TEXT REFERENCES ingestion_runs(id),
    notes TEXT,
    UNIQUE(id, value_kind),
    UNIQUE(series_id, valid_from, recorded_at),
    CHECK ((valid_from IS NULL AND valid_to IS NULL AND claim_kind = 'source_statement')
        OR (valid_from IS NOT NULL AND (valid_to IS NULL OR valid_to > valid_from))),
    CHECK (
        superseded_at IS NULL
        OR julianday(superseded_at) > julianday(recorded_at)
    )
);
INSERT INTO new_claim_versions SELECT * FROM claim_versions;
DROP TABLE claim_versions;
ALTER TABLE new_claim_versions RENAME TO claim_versions;

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
            AND date_base IS NULL AND status = 'expected' AND date_low <= date_high)
    )
);
INSERT INTO new_milestone_values(
    claim_version_id, value_kind, milestone_type, status, date_low, date_base, date_high
) SELECT claim_version_id, value_kind, milestone_type, status, date_low, date_base, date_high
FROM milestone_values;
DROP TABLE milestone_values;
ALTER TABLE new_milestone_values RENAME TO milestone_values;

CREATE INDEX claim_versions_open_by_run_idx
ON claim_versions(created_by_run_id, series_id)
WHERE superseded_at IS NULL AND valid_to IS NULL;

CREATE INDEX claim_versions_replay_by_run_idx
ON claim_versions(
    created_by_run_id,
    recorded_at,
    superseded_at,
    valid_to,
    series_id
);

CREATE INDEX claim_versions_series_recorded_time_idx
ON claim_versions(series_id, recorded_at, superseded_at);

CREATE INDEX claim_versions_series_valid_time_idx
ON claim_versions(series_id, valid_from, valid_to);

CREATE TRIGGER claim_versions_content_immutable
BEFORE UPDATE ON claim_versions
WHEN NEW.id IS NOT OLD.id
  OR NEW.series_id IS NOT OLD.series_id
  OR NEW.value_kind IS NOT OLD.value_kind
  OR NEW.value_sha256 IS NOT OLD.value_sha256
  OR NEW.valid_from IS NOT OLD.valid_from
  OR NEW.valid_to IS NOT OLD.valid_to
  OR NEW.recorded_at IS NOT OLD.recorded_at
  OR NEW.claim_kind IS NOT OLD.claim_kind
  OR NEW.method IS NOT OLD.method
  OR NEW.confidence IS NOT OLD.confidence
  OR NEW.created_by_run_id IS NOT OLD.created_by_run_id
  OR NEW.notes IS NOT OLD.notes
BEGIN
    SELECT RAISE(ABORT, 'claim version content is immutable');
END;

CREATE TRIGGER claim_versions_immutable_delete BEFORE DELETE ON claim_versions
BEGIN SELECT RAISE(ABORT, 'claim versions are immutable'); END;

CREATE TRIGGER claim_versions_kind_insert
BEFORE INSERT ON claim_versions
WHEN NEW.value_kind != (SELECT value_kind FROM claim_series WHERE id = NEW.series_id)
BEGIN
    SELECT RAISE(ABORT, 'claim version value kind must match its series');
END;

CREATE TRIGGER claim_versions_supersession_transition
BEFORE UPDATE OF superseded_at ON claim_versions
WHEN NEW.superseded_at IS NOT OLD.superseded_at
 AND (OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL)
BEGIN SELECT RAISE(ABORT, 'claim supersession can only transition from null once'); END;

CREATE TRIGGER entity_resolution_candidates_integrity_guard
BEFORE INSERT ON entity_resolution_candidates
WHEN (SELECT status FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
         IS NOT 'running'
  OR (SELECT kind FROM entities WHERE id = NEW.observed_entity_id)
         NOT IN ('organization', 'facility')
  OR (SELECT kind FROM entities WHERE id = NEW.candidate_entity_id)
         IS NOT (SELECT kind FROM entities WHERE id = NEW.observed_entity_id)
  OR NOT EXISTS (
      SELECT 1
      FROM source_records AS records
      JOIN entity_resolution_run_inputs AS inputs
        ON inputs.ingestion_run_id = records.ingestion_run_id
       AND inputs.resolution_run_id = NEW.resolution_run_id
      WHERE records.id = NEW.source_record_id
  )
  OR NOT EXISTS (
      SELECT 1
      FROM claim_evidence AS evidence
      JOIN claim_versions AS versions
        ON versions.id = evidence.claim_version_id
      JOIN claim_series AS series ON series.id = versions.series_id
      JOIN entity_resolution_runs AS resolution_runs
        ON resolution_runs.id = NEW.resolution_run_id
      WHERE evidence.source_record_id = NEW.source_record_id
        AND series.subject_entity_id = NEW.observed_entity_id
        AND julianday(versions.recorded_at)
            <= julianday(resolution_runs.started_at)
  )
  OR julianday(NEW.created_at) IS NULL
  OR julianday(NEW.created_at) < julianday(
      (SELECT started_at FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
  )
  OR julianday(NEW.created_at) < julianday(
      (SELECT observed_at FROM source_records WHERE id = NEW.source_record_id)
  )
  OR julianday(
      (SELECT observed_at FROM source_records WHERE id = NEW.source_record_id)
  ) IS NULL
  OR julianday(
      (SELECT observed_at FROM source_records WHERE id = NEW.source_record_id)
  ) > julianday(
      (SELECT started_at FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
  )
  OR julianday(
      (SELECT created_at FROM entities WHERE id = NEW.observed_entity_id)
  ) IS NULL
  OR julianday(
      (SELECT created_at FROM entities WHERE id = NEW.observed_entity_id)
  ) > julianday(
      (SELECT started_at FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
  )
  OR julianday(
      (SELECT created_at FROM entities WHERE id = NEW.candidate_entity_id)
  ) IS NULL
  OR julianday(
      (SELECT created_at FROM entities WHERE id = NEW.candidate_entity_id)
  ) > julianday(
      (SELECT started_at FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
  )
BEGIN
    SELECT RAISE(
        ABORT,
        'entity resolution candidate lacks valid same-kind identity or source lineage'
    );
END;

CREATE TRIGGER milestone_values_immutable_delete BEFORE DELETE ON milestone_values
BEGIN SELECT RAISE(ABORT, 'milestone values are immutable'); END;

CREATE TRIGGER milestone_values_immutable_update BEFORE UPDATE ON milestone_values
BEGIN SELECT RAISE(ABORT, 'milestone values are immutable'); END;

CREATE TRIGGER organization_identifier_claim_metadata_type_guard
BEFORE INSERT ON organization_identifier_claim_metadata
WHEN (SELECT scalar_type FROM scalar_values WHERE claim_version_id = NEW.claim_version_id)
         IS NOT 'text'
  OR (SELECT entities.kind
      FROM claim_versions
      JOIN claim_series ON claim_series.id = claim_versions.series_id
      JOIN entities ON entities.id = claim_series.subject_entity_id
      WHERE claim_versions.id = NEW.claim_version_id) IS NOT 'organization'
  OR EXISTS (
      SELECT 1 FROM organization_name_claim_metadata
      WHERE claim_version_id = NEW.claim_version_id
  )
BEGIN
    SELECT RAISE(ABORT, 'organization identifier metadata requires an organization text claim');
END;

CREATE TRIGGER organization_name_claim_metadata_type_guard
BEFORE INSERT ON organization_name_claim_metadata
WHEN (SELECT scalar_type FROM scalar_values WHERE claim_version_id = NEW.claim_version_id)
         IS NOT 'text'
  OR (SELECT entities.kind
      FROM claim_versions
      JOIN claim_series ON claim_series.id = claim_versions.series_id
      JOIN entities ON entities.id = claim_series.subject_entity_id
      WHERE claim_versions.id = NEW.claim_version_id) IS NOT 'organization'
  OR EXISTS (
      SELECT 1 FROM organization_identifier_claim_metadata
      WHERE claim_version_id = NEW.claim_version_id
  )
BEGIN
    SELECT RAISE(ABORT, 'organization name metadata requires an organization text claim');
END;

CREATE UNIQUE INDEX claim_versions_unknown_valid_time_unique
ON claim_versions(series_id, recorded_at) WHERE valid_from IS NULL;
