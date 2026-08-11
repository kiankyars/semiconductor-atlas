DROP TRIGGER entity_resolution_candidates_integrity_guard;

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

DROP INDEX source_entity_assignments_one_open_idx;

CREATE UNIQUE INDEX source_entity_assignments_one_open_idx
ON source_entity_assignments(observed_entity_id)
WHERE valid_to IS NULL AND superseded_at IS NULL;

DROP TRIGGER source_entity_assignments_integrity_guard;

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
    SELECT RAISE(ABORT, 'source entity assignment requires a cutoff-safe same-kind match decision');
END;

DROP TRIGGER source_entity_assignments_no_current_overlap;

CREATE TRIGGER source_entity_assignments_no_current_overlap
BEFORE INSERT ON source_entity_assignments
WHEN EXISTS (
    SELECT 1
    FROM source_entity_assignments AS existing
    WHERE existing.observed_entity_id = NEW.observed_entity_id
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
    SELECT RAISE(
        ABORT,
        'source entity assignments for one observed entity must not overlap at current knowledge'
    );
END;
