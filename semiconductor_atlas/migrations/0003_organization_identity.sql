CREATE TABLE ingestion_run_documents (
    ingestion_run_id TEXT NOT NULL REFERENCES ingestion_runs(id),
    source_document_id TEXT NOT NULL REFERENCES source_documents(id),
    role TEXT NOT NULL CHECK (length(trim(role)) > 0),
    PRIMARY KEY (ingestion_run_id, source_document_id, role)
);

CREATE INDEX ingestion_run_documents_document_idx
ON ingestion_run_documents(source_document_id, ingestion_run_id);

CREATE TRIGGER ingestion_run_documents_source_consistency
BEFORE INSERT ON ingestion_run_documents
WHEN NOT EXISTS (
    SELECT 1
    FROM ingestion_runs AS runs
    JOIN source_documents AS documents
      ON documents.id = NEW.source_document_id
    WHERE runs.id = NEW.ingestion_run_id
      AND runs.source_id = documents.source_id
)
BEGIN
    SELECT RAISE(ABORT, 'ingestion run document must belong to the run source');
END;

INSERT INTO ingestion_run_documents(ingestion_run_id, source_document_id, role)
SELECT id, input_document_id, 'primary'
FROM ingestion_runs
WHERE input_document_id IS NOT NULL;

INSERT OR IGNORE INTO ingestion_run_documents(
    ingestion_run_id, source_document_id, role
)
SELECT ingestion_run_id, source_document_id, 'source_record'
FROM source_records;

INSERT OR IGNORE INTO ingestion_run_documents(
    ingestion_run_id, source_document_id, role
)
SELECT runs.id, inputs.value, 'parameter:index_document_ids'
FROM ingestion_runs AS runs,
     json_each(runs.parameters_json, '$.index_document_ids') AS inputs;

INSERT OR IGNORE INTO ingestion_run_documents(
    ingestion_run_id, source_document_id, role
)
SELECT runs.id, inputs.value, 'parameter:detail_document_ids'
FROM ingestion_runs AS runs,
     json_each(runs.parameters_json, '$.detail_document_ids') AS inputs;

CREATE TRIGGER ingestion_run_documents_immutable_update
BEFORE UPDATE ON ingestion_run_documents
BEGIN SELECT RAISE(ABORT, 'ingestion run documents are immutable'); END;

CREATE TRIGGER ingestion_run_documents_immutable_delete
BEFORE DELETE ON ingestion_run_documents
BEGIN SELECT RAISE(ABORT, 'ingestion run documents are immutable'); END;

CREATE TABLE organization_name_claim_metadata (
    claim_version_id TEXT PRIMARY KEY REFERENCES scalar_values(claim_version_id),
    name_type TEXT NOT NULL CHECK (name_type IN (
        'legal', 'other', 'transliterated', 'short'
    )),
    language_tag TEXT CHECK (
        language_tag IS NULL OR length(trim(language_tag)) > 0
    ),
    script_code TEXT CHECK (
        script_code IS NULL OR (
            length(script_code) = 4
            AND script_code NOT GLOB '*[^A-Za-z]*'
        )
    )
);

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

CREATE TRIGGER organization_name_claim_metadata_immutable_update
BEFORE UPDATE ON organization_name_claim_metadata
BEGIN SELECT RAISE(ABORT, 'organization name claim metadata is immutable'); END;

CREATE TRIGGER organization_name_claim_metadata_immutable_delete
BEFORE DELETE ON organization_name_claim_metadata
BEGIN SELECT RAISE(ABORT, 'organization name claim metadata is immutable'); END;

CREATE TABLE organization_identifier_claim_metadata (
    claim_version_id TEXT PRIMARY KEY REFERENCES scalar_values(claim_version_id),
    scheme TEXT NOT NULL CHECK (length(trim(scheme)) > 0),
    normalized_value TEXT NOT NULL CHECK (length(trim(normalized_value)) > 0),
    jurisdiction TEXT CHECK (
        jurisdiction IS NULL OR length(trim(jurisdiction)) > 0
    )
);

CREATE INDEX organization_identifier_lookup_idx
ON organization_identifier_claim_metadata(scheme, normalized_value);

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

CREATE TRIGGER organization_identifier_claim_metadata_immutable_update
BEFORE UPDATE ON organization_identifier_claim_metadata
BEGIN SELECT RAISE(ABORT, 'organization identifier claim metadata is immutable'); END;

CREATE TRIGGER organization_identifier_claim_metadata_immutable_delete
BEFORE DELETE ON organization_identifier_claim_metadata
BEGIN SELECT RAISE(ABORT, 'organization identifier claim metadata is immutable'); END;

CREATE TABLE entity_resolution_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL CHECK (
        length(trim(started_at)) > 0 AND julianday(started_at) IS NOT NULL
    ),
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    resolver_version TEXT NOT NULL CHECK (length(trim(resolver_version)) > 0),
    code_version TEXT,
    parameters_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(parameters_json) AND json_type(parameters_json) = 'object'
    ),
    error TEXT,
    CHECK (
        (status = 'running' AND completed_at IS NULL)
        OR (status IN ('succeeded', 'failed') AND completed_at IS NOT NULL)
    ),
    CHECK (
        (status IN ('running', 'succeeded') AND error IS NULL)
        OR (status = 'failed' AND length(trim(error)) > 0)
    ),
    CHECK (
        completed_at IS NULL
        OR (
            julianday(completed_at) IS NOT NULL
            AND julianday(completed_at) > julianday(started_at)
        )
    )
);

CREATE INDEX entity_resolution_runs_time_idx
ON entity_resolution_runs(started_at, id);

CREATE TRIGGER entity_resolution_runs_start_guard
BEFORE INSERT ON entity_resolution_runs
WHEN NEW.status != 'running'
BEGIN
    SELECT RAISE(ABORT, 'entity resolution runs must be created in running state');
END;

CREATE TRIGGER entity_resolution_runs_content_immutable
BEFORE UPDATE ON entity_resolution_runs
WHEN NEW.id IS NOT OLD.id
  OR NEW.started_at IS NOT OLD.started_at
  OR NEW.resolver_version IS NOT OLD.resolver_version
  OR NEW.code_version IS NOT OLD.code_version
  OR NEW.parameters_json IS NOT OLD.parameters_json
BEGIN SELECT RAISE(ABORT, 'entity resolution run content is immutable'); END;

CREATE TRIGGER entity_resolution_runs_terminal_transition
BEFORE UPDATE OF status, completed_at, error ON entity_resolution_runs
WHEN NEW.status IS NOT OLD.status
  OR NEW.completed_at IS NOT OLD.completed_at
  OR NEW.error IS NOT OLD.error
BEGIN
    SELECT CASE WHEN NOT (
        OLD.status = 'running'
        AND OLD.completed_at IS NULL
        AND OLD.error IS NULL
        AND NEW.status IN ('succeeded', 'failed')
        AND NEW.completed_at IS NOT NULL
    ) THEN RAISE(ABORT, 'entity resolution run may be finalized exactly once') END;
    SELECT CASE WHEN NEW.status = 'succeeded' AND NOT EXISTS (
        SELECT 1 FROM entity_resolution_run_inputs
        WHERE resolution_run_id = OLD.id
    ) THEN RAISE(ABORT, 'succeeded entity resolution run requires an input') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM entity_resolution_candidates
        WHERE resolution_run_id = OLD.id
          AND julianday(created_at) > julianday(NEW.completed_at)
    ) THEN RAISE(ABORT, 'entity resolution run completion predates a candidate') END;
END;

CREATE TRIGGER entity_resolution_runs_immutable_delete
BEFORE DELETE ON entity_resolution_runs
BEGIN SELECT RAISE(ABORT, 'entity resolution runs are immutable'); END;

CREATE TABLE entity_resolution_run_inputs (
    resolution_run_id TEXT NOT NULL REFERENCES entity_resolution_runs(id),
    ingestion_run_id TEXT NOT NULL REFERENCES ingestion_runs(id),
    PRIMARY KEY (resolution_run_id, ingestion_run_id)
);

CREATE INDEX entity_resolution_run_inputs_ingestion_idx
ON entity_resolution_run_inputs(ingestion_run_id, resolution_run_id);

CREATE TRIGGER entity_resolution_run_inputs_status_guard
BEFORE INSERT ON entity_resolution_run_inputs
WHEN (SELECT status FROM ingestion_runs WHERE id = NEW.ingestion_run_id)
         IS NOT 'succeeded'
  OR (SELECT status FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
         IS NOT 'running'
  OR julianday(
      (SELECT started_at FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
  ) IS NULL
  OR julianday(
      (SELECT completed_at FROM ingestion_runs WHERE id = NEW.ingestion_run_id)
  ) IS NULL
  OR julianday(
      (SELECT started_at FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
  ) < julianday(
      (SELECT completed_at FROM ingestion_runs WHERE id = NEW.ingestion_run_id)
  )
BEGIN
    SELECT RAISE(ABORT, 'entity resolution inputs require an already-succeeded ingestion run');
END;

CREATE TRIGGER entity_resolution_run_inputs_immutable_update
BEFORE UPDATE ON entity_resolution_run_inputs
BEGIN SELECT RAISE(ABORT, 'entity resolution run inputs are immutable'); END;

CREATE TRIGGER entity_resolution_run_inputs_immutable_delete
BEFORE DELETE ON entity_resolution_run_inputs
BEGIN SELECT RAISE(ABORT, 'entity resolution run inputs are immutable'); END;

CREATE TABLE entity_resolution_candidates (
    id TEXT PRIMARY KEY,
    resolution_run_id TEXT NOT NULL REFERENCES entity_resolution_runs(id),
    source_record_id TEXT NOT NULL REFERENCES source_records(id),
    observed_entity_id TEXT NOT NULL REFERENCES entities(id),
    candidate_entity_id TEXT NOT NULL REFERENCES entities(id),
    features_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(features_json) AND json_type(features_json) = 'object'
    ),
    score REAL NOT NULL CHECK (score >= 0 AND score <= 1),
    candidate_rank INTEGER NOT NULL CHECK (
        typeof(candidate_rank) = 'integer' AND candidate_rank >= 1
    ),
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
    CHECK (observed_entity_id != candidate_entity_id),
    UNIQUE(
        resolution_run_id,
        source_record_id,
        observed_entity_id,
        candidate_entity_id
    ),
    UNIQUE(
        resolution_run_id,
        source_record_id,
        observed_entity_id,
        candidate_rank
    )
);

CREATE INDEX entity_resolution_candidates_subject_idx
ON entity_resolution_candidates(
    source_record_id, observed_entity_id, score DESC, candidate_rank, id
);

CREATE TRIGGER entity_resolution_candidates_integrity_guard
BEFORE INSERT ON entity_resolution_candidates
WHEN (SELECT status FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
         IS NOT 'running'
  OR (SELECT kind FROM entities WHERE id = NEW.observed_entity_id)
         IS NOT 'organization'
  OR (SELECT kind FROM entities WHERE id = NEW.candidate_entity_id)
         IS NOT 'organization'
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
    SELECT RAISE(ABORT, 'entity resolution candidate lacks valid organization or source lineage');
END;

CREATE TRIGGER entity_resolution_candidates_immutable_update
BEFORE UPDATE ON entity_resolution_candidates
BEGIN SELECT RAISE(ABORT, 'entity resolution candidates are immutable'); END;

CREATE TRIGGER entity_resolution_candidates_immutable_delete
BEFORE DELETE ON entity_resolution_candidates
BEGIN SELECT RAISE(ABORT, 'entity resolution candidates are immutable'); END;

CREATE TABLE entity_resolution_decisions (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES entity_resolution_candidates(id),
    outcome TEXT NOT NULL CHECK (outcome IN ('match', 'reject', 'defer')),
    decided_at TEXT NOT NULL CHECK (length(trim(decided_at)) > 0),
    decided_by TEXT NOT NULL CHECK (length(trim(decided_by)) > 0),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(metadata_json) AND json_type(metadata_json) = 'object'
    )
);

CREATE INDEX entity_resolution_decisions_time_idx
ON entity_resolution_decisions(decided_at, id);

CREATE TRIGGER entity_resolution_decisions_time_guard
BEFORE INSERT ON entity_resolution_decisions
WHEN julianday(NEW.decided_at) IS NULL
  OR (
      SELECT resolution_runs.status
      FROM entity_resolution_candidates AS candidates
      JOIN entity_resolution_runs AS resolution_runs
        ON resolution_runs.id = candidates.resolution_run_id
      WHERE candidates.id = NEW.candidate_id
  ) IS NOT 'succeeded'
  OR julianday(NEW.decided_at) < julianday(
      (SELECT created_at FROM entity_resolution_candidates WHERE id = NEW.candidate_id)
  )
  OR julianday(NEW.decided_at) < julianday((
      SELECT resolution_runs.completed_at
      FROM entity_resolution_candidates AS candidates
      JOIN entity_resolution_runs AS resolution_runs
        ON resolution_runs.id = candidates.resolution_run_id
      WHERE candidates.id = NEW.candidate_id
  ))
BEGIN
    SELECT RAISE(ABORT, 'entity resolution decision requires a completed candidate run');
END;

CREATE TRIGGER entity_resolution_decisions_immutable_update
BEFORE UPDATE ON entity_resolution_decisions
BEGIN SELECT RAISE(ABORT, 'entity resolution decisions are immutable'); END;

CREATE TRIGGER entity_resolution_decisions_immutable_delete
BEFORE DELETE ON entity_resolution_decisions
BEGIN SELECT RAISE(ABORT, 'entity resolution decisions are immutable'); END;

CREATE TABLE source_entity_assignments (
    id TEXT PRIMARY KEY,
    source_record_id TEXT NOT NULL REFERENCES source_records(id),
    observed_entity_id TEXT NOT NULL REFERENCES entities(id),
    canonical_entity_id TEXT NOT NULL REFERENCES entities(id),
    decision_id TEXT NOT NULL UNIQUE REFERENCES entity_resolution_decisions(id),
    valid_from TEXT NOT NULL CHECK (
        length(valid_from) = 10
        AND date(valid_from) IS NOT NULL
        AND date(valid_from) = valid_from
    ),
    valid_to TEXT CHECK (
        valid_to IS NULL
        OR (
            length(valid_to) = 10
            AND date(valid_to) IS NOT NULL
            AND date(valid_to) = valid_to
        )
    ),
    recorded_at TEXT NOT NULL CHECK (length(trim(recorded_at)) > 0),
    superseded_at TEXT,
    CHECK (observed_entity_id != canonical_entity_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (
        superseded_at IS NULL
        OR (
            julianday(superseded_at) IS NOT NULL
            AND julianday(superseded_at) > julianday(recorded_at)
        )
    ),
    UNIQUE(source_record_id, observed_entity_id, valid_from, recorded_at)
);

CREATE UNIQUE INDEX source_entity_assignments_one_open_idx
ON source_entity_assignments(source_record_id, observed_entity_id)
WHERE valid_to IS NULL AND superseded_at IS NULL;

CREATE INDEX source_entity_assignments_cutoff_idx
ON source_entity_assignments(
    source_record_id, observed_entity_id, valid_from, valid_to,
    recorded_at, superseded_at
);

CREATE TRIGGER source_entity_assignments_integrity_guard
BEFORE INSERT ON source_entity_assignments
WHEN (SELECT kind FROM entities WHERE id = NEW.observed_entity_id)
         IS NOT 'organization'
  OR (SELECT kind FROM entities WHERE id = NEW.canonical_entity_id)
         IS NOT 'organization'
  OR NOT EXISTS (
      SELECT 1
      FROM entity_resolution_decisions AS decisions
      JOIN entity_resolution_candidates AS candidates
        ON candidates.id = decisions.candidate_id
      WHERE decisions.id = NEW.decision_id
        AND decisions.outcome = 'match'
        AND candidates.source_record_id = NEW.source_record_id
        AND candidates.observed_entity_id = NEW.observed_entity_id
        AND candidates.candidate_entity_id = NEW.canonical_entity_id
  )
  OR julianday(NEW.recorded_at) IS NULL
  OR julianday(NEW.recorded_at) < julianday(
      (SELECT decided_at FROM entity_resolution_decisions WHERE id = NEW.decision_id)
  )
  OR julianday(NEW.recorded_at) < julianday(
      (SELECT observed_at FROM source_records WHERE id = NEW.source_record_id)
  )
  OR julianday(NEW.recorded_at) < julianday(
      (SELECT created_at FROM entities WHERE id = NEW.observed_entity_id)
  )
  OR julianday(NEW.recorded_at) < julianday(
      (SELECT created_at FROM entities WHERE id = NEW.canonical_entity_id)
  )
BEGIN
    SELECT RAISE(ABORT, 'source entity assignment requires a cutoff-safe match decision');
END;

CREATE TRIGGER source_entity_assignments_no_current_overlap
BEFORE INSERT ON source_entity_assignments
WHEN EXISTS (
    SELECT 1
    FROM source_entity_assignments AS existing
    WHERE existing.source_record_id = NEW.source_record_id
      AND existing.observed_entity_id = NEW.observed_entity_id
      AND existing.id != NEW.id
      AND (
          existing.superseded_at IS NULL
          OR julianday(NEW.recorded_at) < julianday(existing.superseded_at)
      )
      AND (
          NEW.superseded_at IS NULL
          OR julianday(existing.recorded_at) < julianday(NEW.superseded_at)
      )
      AND existing.valid_from < COALESCE(NEW.valid_to, '9999-12-31')
      AND NEW.valid_from < COALESCE(existing.valid_to, '9999-12-31')
)
BEGIN
    SELECT RAISE(ABORT, 'source entity assignments must not overlap at current knowledge');
END;

CREATE TRIGGER source_entity_assignments_content_immutable
BEFORE UPDATE ON source_entity_assignments
WHEN NEW.id IS NOT OLD.id
  OR NEW.source_record_id IS NOT OLD.source_record_id
  OR NEW.observed_entity_id IS NOT OLD.observed_entity_id
  OR NEW.canonical_entity_id IS NOT OLD.canonical_entity_id
  OR NEW.decision_id IS NOT OLD.decision_id
  OR NEW.valid_from IS NOT OLD.valid_from
  OR NEW.valid_to IS NOT OLD.valid_to
  OR NEW.recorded_at IS NOT OLD.recorded_at
BEGIN
    SELECT RAISE(ABORT, 'source entity assignment content is immutable');
END;

CREATE TRIGGER source_entity_assignments_supersession_transition
BEFORE UPDATE OF superseded_at ON source_entity_assignments
WHEN NEW.superseded_at IS NOT OLD.superseded_at
 AND (OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'assignment supersession can only transition from null once');
END;

CREATE TRIGGER source_entity_assignments_immutable_delete
BEFORE DELETE ON source_entity_assignments
BEGIN SELECT RAISE(ABORT, 'source entity assignments are immutable'); END;
