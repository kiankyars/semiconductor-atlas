-- Exact UTC-microsecond knowledge comparisons. Migrations 1-9 remain unchanged.
-- The runner registers atlas_timestamp_us, disables foreign keys before BEGIN,
-- and restores them only after the rebuilt graph passes foreign_key_check.
-- All original rows, IDs, literal clock strings, indexes and immutable guards survive.

-- Fail before reconstruction when old claim lineage violates the stricter clock.
-- This does not repair, drop or redate historical claims to make migration pass.
CREATE TEMP TABLE clock10_assertion (ok INTEGER NOT NULL CHECK (ok = 1));
INSERT INTO clock10_assertion SELECT NOT EXISTS (
    SELECT 1 FROM claim_versions v
    JOIN claim_series s ON s.id = v.series_id
    JOIN entities e ON e.id = s.subject_entity_id
    WHERE atlas_timestamp_us(v.recorded_at) IS NULL
       OR atlas_timestamp_us(s.created_at) IS NULL
       OR atlas_timestamp_us(e.created_at) IS NULL
       OR atlas_timestamp_us(s.created_at) > atlas_timestamp_us(v.recorded_at)
       OR atlas_timestamp_us(e.created_at) > atlas_timestamp_us(v.recorded_at)
);
INSERT INTO clock10_assertion SELECT NOT EXISTS (
    SELECT 1 FROM claim_evidence e
    JOIN claim_versions v ON v.id = e.claim_version_id
    JOIN source_documents d ON d.id = e.source_document_id
    LEFT JOIN source_records r ON r.id = e.source_record_id
    LEFT JOIN ingestion_runs runs ON runs.id = r.ingestion_run_id
    WHERE atlas_timestamp_us(d.retrieved_at) IS NULL
       OR atlas_timestamp_us(d.retrieved_at) > atlas_timestamp_us(v.recorded_at)
       OR (e.source_record_id IS NOT NULL AND (
           r.source_document_id IS NOT e.source_document_id
           OR atlas_timestamp_us(r.observed_at) IS NULL
           OR atlas_timestamp_us(runs.started_at) IS NULL
           OR atlas_timestamp_us(r.observed_at) > atlas_timestamp_us(v.recorded_at)
           OR atlas_timestamp_us(runs.started_at) > atlas_timestamp_us(v.recorded_at)))
);
INSERT INTO clock10_assertion SELECT NOT EXISTS (
    SELECT 1 FROM claim_dependencies d
    JOIN claim_versions child ON child.id = d.claim_version_id
    JOIN claim_versions parent ON parent.id = d.depends_on_claim_version_id
    WHERE atlas_timestamp_us(parent.recorded_at) > atlas_timestamp_us(child.recorded_at)
);
INSERT INTO clock10_assertion SELECT NOT EXISTS (
    SELECT 1 FROM (
        SELECT claim_version_id, object_entity_id AS target FROM relationship_values
        UNION ALL
        SELECT claim_version_id, constrained_entity_id AS target FROM constraint_values
        WHERE constrained_entity_id IS NOT NULL
    ) targets JOIN entities e ON e.id = targets.target
    JOIN claim_versions v ON v.id = targets.claim_version_id
    WHERE atlas_timestamp_us(e.created_at) IS NULL
       OR atlas_timestamp_us(e.created_at) > atlas_timestamp_us(v.recorded_at)
);
DROP TABLE clock10_assertion;

DROP TRIGGER "claim_versions_content_immutable";
DROP TRIGGER "claim_versions_immutable_delete";
DROP TRIGGER "claim_versions_kind_insert";
DROP TRIGGER "claim_versions_supersession_transition";
DROP TRIGGER "entity_resolution_candidates_integrity_guard";
DROP TRIGGER "entity_resolution_decisions_time_guard";
DROP TRIGGER "entity_resolution_run_inputs_status_guard";
DROP TRIGGER "entity_resolution_runs_content_immutable";
DROP TRIGGER "entity_resolution_runs_immutable_delete";
DROP TRIGGER "entity_resolution_runs_start_guard";
DROP TRIGGER "entity_resolution_runs_terminal_transition";
DROP TRIGGER "ingestion_run_documents_source_consistency";
DROP TRIGGER "ingestion_runs_immutable_delete";
DROP TRIGGER "ingestion_runs_immutable_update";
DROP TRIGGER "organization_identifier_claim_metadata_type_guard";
DROP TRIGGER "organization_name_claim_metadata_type_guard";
DROP TRIGGER "source_entity_assignments_content_immutable";
DROP TRIGGER "source_entity_assignments_immutable_delete";
DROP TRIGGER "source_entity_assignments_integrity_guard";
DROP TRIGGER "source_entity_assignments_no_current_overlap";
DROP TRIGGER "source_entity_assignments_supersession_transition";
DROP TRIGGER "source_quantity_metadata_type_guard";
DROP TRIGGER "source_rate_metadata_type_guard";
DROP TRIGGER "source_records_source_consistency";

CREATE TABLE "clock10_claim_versions" (
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
        OR atlas_timestamp_us(superseded_at) > atlas_timestamp_us(recorded_at)
    ),
    CHECK (atlas_timestamp_us(recorded_at) IS NOT NULL),
    CHECK (superseded_at IS NULL OR atlas_timestamp_us(superseded_at) IS NOT NULL)
);
INSERT INTO "clock10_claim_versions" SELECT * FROM "claim_versions";
DROP TABLE "claim_versions";
ALTER TABLE "clock10_claim_versions" RENAME TO "claim_versions";

CREATE TABLE "clock10_ingestion_runs" (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id),
    input_document_id TEXT REFERENCES source_documents(id),
    started_at TEXT NOT NULL CHECK (length(trim(started_at)) > 0),
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    code_version TEXT,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    CHECK (
        (status = 'running' AND completed_at IS NULL)
        OR (status IN ('succeeded', 'failed') AND completed_at IS NOT NULL)
    ),
    CHECK (
        completed_at IS NULL
        OR atlas_timestamp_us(completed_at) > atlas_timestamp_us(started_at)
    ),
    CHECK (atlas_timestamp_us(started_at) IS NOT NULL),
    CHECK (completed_at IS NULL OR atlas_timestamp_us(completed_at) IS NOT NULL)
);
INSERT INTO "clock10_ingestion_runs" SELECT * FROM "ingestion_runs";
DROP TABLE "ingestion_runs";
ALTER TABLE "clock10_ingestion_runs" RENAME TO "ingestion_runs";

CREATE TABLE "clock10_entity_resolution_runs" (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL CHECK (
        length(trim(started_at)) > 0 AND atlas_timestamp_us(started_at) IS NOT NULL
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
            atlas_timestamp_us(completed_at) IS NOT NULL
            AND atlas_timestamp_us(completed_at) > atlas_timestamp_us(started_at)
        )
    ),
    CHECK (atlas_timestamp_us(started_at) IS NOT NULL),
    CHECK (completed_at IS NULL OR atlas_timestamp_us(completed_at) IS NOT NULL)
);
INSERT INTO "clock10_entity_resolution_runs" SELECT * FROM "entity_resolution_runs";
DROP TABLE "entity_resolution_runs";
ALTER TABLE "clock10_entity_resolution_runs" RENAME TO "entity_resolution_runs";

CREATE TABLE "clock10_source_entity_assignments" (
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
            atlas_timestamp_us(superseded_at) IS NOT NULL
            AND atlas_timestamp_us(superseded_at) > atlas_timestamp_us(recorded_at)
        )
    ),
    UNIQUE(source_record_id, observed_entity_id, valid_from, recorded_at),
    CHECK (atlas_timestamp_us(recorded_at) IS NOT NULL),
    CHECK (superseded_at IS NULL OR atlas_timestamp_us(superseded_at) IS NOT NULL)
);
INSERT INTO "clock10_source_entity_assignments" SELECT * FROM "source_entity_assignments";
DROP TABLE "source_entity_assignments";
ALTER TABLE "clock10_source_entity_assignments" RENAME TO "source_entity_assignments";

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

CREATE UNIQUE INDEX claim_versions_unknown_valid_time_unique
ON claim_versions(series_id, recorded_at) WHERE valid_from IS NULL;

CREATE INDEX entity_resolution_runs_time_idx
ON entity_resolution_runs(started_at, id);

CREATE INDEX ingestion_runs_source_time_idx
ON ingestion_runs(source_id, started_at);

CREATE INDEX source_entity_assignments_cutoff_idx
ON source_entity_assignments(
    source_record_id, observed_entity_id, valid_from, valid_to,
    recorded_at, superseded_at
);

CREATE UNIQUE INDEX source_entity_assignments_one_open_idx
ON source_entity_assignments(observed_entity_id)
WHERE valid_to IS NULL AND superseded_at IS NULL;

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
        AND atlas_timestamp_us(versions.recorded_at)
            <= atlas_timestamp_us(resolution_runs.started_at)
  )
  OR atlas_timestamp_us(NEW.created_at) IS NULL
  OR atlas_timestamp_us(NEW.created_at) < atlas_timestamp_us(
      (SELECT started_at FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
  )
  OR atlas_timestamp_us(NEW.created_at) < atlas_timestamp_us(
      (SELECT observed_at FROM source_records WHERE id = NEW.source_record_id)
  )
  OR atlas_timestamp_us(
      (SELECT observed_at FROM source_records WHERE id = NEW.source_record_id)
  ) IS NULL
  OR atlas_timestamp_us(
      (SELECT observed_at FROM source_records WHERE id = NEW.source_record_id)
  ) > atlas_timestamp_us(
      (SELECT started_at FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
  )
  OR atlas_timestamp_us(
      (SELECT created_at FROM entities WHERE id = NEW.observed_entity_id)
  ) IS NULL
  OR atlas_timestamp_us(
      (SELECT created_at FROM entities WHERE id = NEW.observed_entity_id)
  ) > atlas_timestamp_us(
      (SELECT started_at FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
  )
  OR atlas_timestamp_us(
      (SELECT created_at FROM entities WHERE id = NEW.candidate_entity_id)
  ) IS NULL
  OR atlas_timestamp_us(
      (SELECT created_at FROM entities WHERE id = NEW.candidate_entity_id)
  ) > atlas_timestamp_us(
      (SELECT started_at FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
  )
BEGIN
    SELECT RAISE(
        ABORT,
        'entity resolution candidate lacks valid same-kind identity or source lineage'
    );
END;

CREATE TRIGGER entity_resolution_decisions_time_guard
BEFORE INSERT ON entity_resolution_decisions
WHEN atlas_timestamp_us(NEW.decided_at) IS NULL
  OR (
      SELECT resolution_runs.status
      FROM entity_resolution_candidates AS candidates
      JOIN entity_resolution_runs AS resolution_runs
        ON resolution_runs.id = candidates.resolution_run_id
      WHERE candidates.id = NEW.candidate_id
  ) IS NOT 'succeeded'
  OR atlas_timestamp_us(NEW.decided_at) < atlas_timestamp_us(
      (SELECT created_at FROM entity_resolution_candidates WHERE id = NEW.candidate_id)
  )
  OR atlas_timestamp_us(NEW.decided_at) < atlas_timestamp_us((
      SELECT resolution_runs.completed_at
      FROM entity_resolution_candidates AS candidates
      JOIN entity_resolution_runs AS resolution_runs
        ON resolution_runs.id = candidates.resolution_run_id
      WHERE candidates.id = NEW.candidate_id
  ))
BEGIN
    SELECT RAISE(ABORT, 'entity resolution decision requires a completed candidate run');
END;

CREATE TRIGGER entity_resolution_run_inputs_status_guard
BEFORE INSERT ON entity_resolution_run_inputs
WHEN (SELECT status FROM ingestion_runs WHERE id = NEW.ingestion_run_id)
         IS NOT 'succeeded'
  OR (SELECT status FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
         IS NOT 'running'
  OR atlas_timestamp_us(
      (SELECT started_at FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
  ) IS NULL
  OR atlas_timestamp_us(
      (SELECT completed_at FROM ingestion_runs WHERE id = NEW.ingestion_run_id)
  ) IS NULL
  OR atlas_timestamp_us(
      (SELECT started_at FROM entity_resolution_runs WHERE id = NEW.resolution_run_id)
  ) < atlas_timestamp_us(
      (SELECT completed_at FROM ingestion_runs WHERE id = NEW.ingestion_run_id)
  )
BEGIN
    SELECT RAISE(ABORT, 'entity resolution inputs require an already-succeeded ingestion run');
END;

CREATE TRIGGER entity_resolution_runs_content_immutable
BEFORE UPDATE ON entity_resolution_runs
WHEN NEW.id IS NOT OLD.id
  OR NEW.started_at IS NOT OLD.started_at
  OR NEW.resolver_version IS NOT OLD.resolver_version
  OR NEW.code_version IS NOT OLD.code_version
  OR NEW.parameters_json IS NOT OLD.parameters_json
BEGIN SELECT RAISE(ABORT, 'entity resolution run content is immutable'); END;

CREATE TRIGGER entity_resolution_runs_immutable_delete
BEFORE DELETE ON entity_resolution_runs
BEGIN SELECT RAISE(ABORT, 'entity resolution runs are immutable'); END;

CREATE TRIGGER entity_resolution_runs_start_guard
BEFORE INSERT ON entity_resolution_runs
WHEN NEW.status != 'running'
BEGIN
    SELECT RAISE(ABORT, 'entity resolution runs must be created in running state');
END;

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
          AND atlas_timestamp_us(created_at) > atlas_timestamp_us(NEW.completed_at)
    ) THEN RAISE(ABORT, 'entity resolution run completion predates a candidate') END;
END;

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

CREATE TRIGGER ingestion_runs_immutable_delete BEFORE DELETE ON ingestion_runs
BEGIN SELECT RAISE(ABORT, 'ingestion runs are immutable'); END;

CREATE TRIGGER ingestion_runs_immutable_update BEFORE UPDATE ON ingestion_runs
BEGIN SELECT RAISE(ABORT, 'ingestion runs are immutable'); END;

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

CREATE TRIGGER source_entity_assignments_immutable_delete
BEFORE DELETE ON source_entity_assignments
BEGIN SELECT RAISE(ABORT, 'source entity assignments are immutable'); END;

CREATE TRIGGER source_entity_assignments_integrity_guard
BEFORE INSERT ON source_entity_assignments
WHEN (SELECT kind FROM entities WHERE id = NEW.observed_entity_id)
         NOT IN ('organization', 'facility')
  OR (SELECT kind FROM entities WHERE id = NEW.canonical_entity_id)
         IS NOT (SELECT kind FROM entities WHERE id = NEW.observed_entity_id)
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
  OR atlas_timestamp_us(NEW.recorded_at) IS NULL
  OR atlas_timestamp_us(NEW.recorded_at) < atlas_timestamp_us(
      (SELECT decided_at FROM entity_resolution_decisions WHERE id = NEW.decision_id)
  )
  OR atlas_timestamp_us(NEW.recorded_at) < atlas_timestamp_us(
      (SELECT observed_at FROM source_records WHERE id = NEW.source_record_id)
  )
  OR atlas_timestamp_us(NEW.recorded_at) < atlas_timestamp_us(
      (SELECT created_at FROM entities WHERE id = NEW.observed_entity_id)
  )
  OR atlas_timestamp_us(NEW.recorded_at) < atlas_timestamp_us(
      (SELECT created_at FROM entities WHERE id = NEW.canonical_entity_id)
  )
BEGIN
    SELECT RAISE(ABORT, 'source entity assignment requires a cutoff-safe same-kind match decision');
END;

CREATE TRIGGER source_entity_assignments_no_current_overlap
BEFORE INSERT ON source_entity_assignments
WHEN EXISTS (
    SELECT 1
    FROM source_entity_assignments AS existing
    WHERE existing.observed_entity_id = NEW.observed_entity_id
      AND existing.id != NEW.id
      AND (
          existing.superseded_at IS NULL
          OR atlas_timestamp_us(NEW.recorded_at) < atlas_timestamp_us(existing.superseded_at)
      )
      AND (
          NEW.superseded_at IS NULL
          OR atlas_timestamp_us(existing.recorded_at) < atlas_timestamp_us(NEW.superseded_at)
      )
      AND existing.valid_from < COALESCE(NEW.valid_to, '9999-12-31')
      AND NEW.valid_from < COALESCE(existing.valid_to, '9999-12-31')
)
BEGIN
    SELECT RAISE(
        ABORT,
        'source entity assignments for one observed entity must not overlap at current knowledge'
    );
END;

CREATE TRIGGER source_entity_assignments_supersession_transition
BEFORE UPDATE OF superseded_at ON source_entity_assignments
WHEN NEW.superseded_at IS NOT OLD.superseded_at
 AND (OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'assignment supersession can only transition from null once');
END;

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

CREATE TRIGGER source_records_source_consistency
BEFORE INSERT ON source_records
WHEN (
    SELECT source_id FROM ingestion_runs WHERE id = NEW.ingestion_run_id
) != (
    SELECT source_id FROM source_documents WHERE id = NEW.source_document_id
)
BEGIN
    SELECT RAISE(ABORT, 'source record run and document must belong to the same source');
END;

-- Direct SQL writes receive the same claim-availability gates as the repository.
CREATE TRIGGER claim_versions_knowledge_guard
BEFORE INSERT ON claim_versions
WHEN NOT EXISTS (
    SELECT 1 FROM claim_series s JOIN entities e ON e.id = s.subject_entity_id
    WHERE s.id = NEW.series_id
      AND atlas_timestamp_us(s.created_at) <= atlas_timestamp_us(NEW.recorded_at)
      AND atlas_timestamp_us(e.created_at) <= atlas_timestamp_us(NEW.recorded_at)
)
BEGIN SELECT RAISE(ABORT, 'claim predates its series or subject entity'); END;

CREATE TRIGGER claim_evidence_knowledge_guard
BEFORE INSERT ON claim_evidence
WHEN NOT EXISTS (
    SELECT 1 FROM claim_versions v JOIN source_documents d ON d.id = NEW.source_document_id
    WHERE v.id = NEW.claim_version_id
      AND atlas_timestamp_us(d.retrieved_at) <= atlas_timestamp_us(v.recorded_at)
) OR (NEW.source_record_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM source_records r JOIN ingestion_runs runs ON runs.id = r.ingestion_run_id
    JOIN claim_versions v ON v.id = NEW.claim_version_id
    WHERE r.id = NEW.source_record_id AND r.source_document_id = NEW.source_document_id
      AND atlas_timestamp_us(r.observed_at) <= atlas_timestamp_us(v.recorded_at)
      AND atlas_timestamp_us(runs.started_at) <= atlas_timestamp_us(v.recorded_at)
))
BEGIN SELECT RAISE(ABORT, 'claim evidence is unavailable at its admission clock'); END;

CREATE TRIGGER claim_dependencies_knowledge_guard
BEFORE INSERT ON claim_dependencies
WHEN NOT EXISTS (
    SELECT 1 FROM claim_versions child JOIN claim_versions parent
      ON parent.id = NEW.depends_on_claim_version_id
    WHERE child.id = NEW.claim_version_id
      AND atlas_timestamp_us(parent.recorded_at) <= atlas_timestamp_us(child.recorded_at)
)
BEGIN SELECT RAISE(ABORT, 'claim dependency is unavailable at its admission clock'); END;

CREATE TRIGGER relationship_values_knowledge_guard
BEFORE INSERT ON relationship_values
WHEN NOT EXISTS (
    SELECT 1 FROM entities e JOIN claim_versions v ON v.id = NEW.claim_version_id
    WHERE e.id = NEW.object_entity_id
      AND atlas_timestamp_us(e.created_at) <= atlas_timestamp_us(v.recorded_at)
)
BEGIN SELECT RAISE(ABORT, 'relationship target is unavailable at claim admission'); END;

CREATE TRIGGER constraint_values_knowledge_guard
BEFORE INSERT ON constraint_values
WHEN NEW.constrained_entity_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM entities e JOIN claim_versions v ON v.id = NEW.claim_version_id
    WHERE e.id = NEW.constrained_entity_id
      AND atlas_timestamp_us(e.created_at) <= atlas_timestamp_us(v.recorded_at)
)
BEGIN SELECT RAISE(ABORT, 'constraint target is unavailable at claim admission'); END;
