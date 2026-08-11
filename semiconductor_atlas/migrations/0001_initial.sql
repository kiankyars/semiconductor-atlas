CREATE TABLE source_families (
    id TEXT PRIMARY KEY,
    stable_key TEXT NOT NULL UNIQUE CHECK (length(trim(stable_key)) > 0),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    description TEXT,
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0)
);

CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES source_families(id),
    stable_key TEXT NOT NULL UNIQUE CHECK (length(trim(stable_key)) > 0),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    publisher TEXT NOT NULL CHECK (length(trim(publisher)) > 0),
    canonical_url TEXT NOT NULL CHECK (length(trim(canonical_url)) > 0),
    license TEXT,
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0)
);

CREATE INDEX sources_family_idx ON sources(family_id);

CREATE TABLE source_documents (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id),
    document_url TEXT NOT NULL CHECK (length(trim(document_url)) > 0),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    published_at TEXT,
    retrieved_at TEXT NOT NULL CHECK (length(trim(retrieved_at)) > 0),
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    media_type TEXT,
    license TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_id, document_url, retrieved_at, content_sha256)
);

CREATE INDEX source_documents_source_time_idx
ON source_documents(source_id, retrieved_at);

CREATE TRIGGER source_documents_immutable_update
BEFORE UPDATE ON source_documents
BEGIN
    SELECT RAISE(ABORT, 'source documents are immutable');
END;

CREATE TRIGGER source_documents_immutable_delete
BEFORE DELETE ON source_documents
BEGIN
    SELECT RAISE(ABORT, 'source documents are immutable');
END;

CREATE TABLE ingestion_runs (
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
        OR julianday(completed_at) > julianday(started_at)
    )
);

CREATE INDEX ingestion_runs_source_time_idx
ON ingestion_runs(source_id, started_at);

CREATE TABLE source_records (
    id TEXT PRIMARY KEY,
    ingestion_run_id TEXT NOT NULL REFERENCES ingestion_runs(id),
    source_document_id TEXT NOT NULL REFERENCES source_documents(id),
    source_record_key TEXT NOT NULL CHECK (length(trim(source_record_key)) > 0),
    observed_at TEXT NOT NULL CHECK (length(trim(observed_at)) > 0),
    record_sha256 TEXT NOT NULL CHECK (
        length(record_sha256) = 64
        AND record_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    payload_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(ingestion_run_id, source_record_key),
    UNIQUE(id, source_document_id)
);

CREATE INDEX source_records_document_idx ON source_records(source_document_id);

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

CREATE TRIGGER source_records_immutable_update
BEFORE UPDATE ON source_records
BEGIN
    SELECT RAISE(ABORT, 'source records are immutable');
END;

CREATE TRIGGER source_records_immutable_delete
BEFORE DELETE ON source_records
BEGIN
    SELECT RAISE(ABORT, 'source records are immutable');
END;

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN (
        'organization', 'site', 'facility', 'building', 'production_unit', 'project',
        'infrastructure_asset'
    )),
    stable_key TEXT NOT NULL UNIQUE CHECK (length(trim(stable_key)) > 0),
    display_name TEXT,
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
    created_by_run_id TEXT REFERENCES ingestion_runs(id)
);

CREATE INDEX entities_kind_idx ON entities(kind);

CREATE TABLE claim_series (
    id TEXT PRIMARY KEY,
    subject_entity_id TEXT NOT NULL REFERENCES entities(id),
    stable_key TEXT NOT NULL UNIQUE CHECK (length(trim(stable_key)) > 0),
    predicate TEXT NOT NULL CHECK (length(trim(predicate)) > 0),
    value_kind TEXT NOT NULL CHECK (value_kind IN (
        'scalar', 'geometry', 'relationship', 'milestone', 'capability', 'capacity',
        'resource', 'constraint'
    )),
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0)
);

CREATE INDEX claim_series_subject_predicate_idx
ON claim_series(subject_entity_id, predicate);

CREATE TABLE claim_versions (
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
    valid_from TEXT NOT NULL CHECK (length(trim(valid_from)) > 0),
    valid_to TEXT,
    recorded_at TEXT NOT NULL CHECK (length(trim(recorded_at)) > 0),
    superseded_at TEXT,
    claim_kind TEXT NOT NULL CHECK (claim_kind IN (
        'source_statement', 'direct_observation', 'reconciled_fact', 'derived_estimate'
    )),
    method TEXT NOT NULL CHECK (length(trim(method)) > 0),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    created_by_run_id TEXT REFERENCES ingestion_runs(id),
    notes TEXT,
    UNIQUE(id, value_kind),
    UNIQUE(series_id, valid_from, recorded_at),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (
        superseded_at IS NULL
        OR julianday(superseded_at) > julianday(recorded_at)
    )
);

CREATE INDEX claim_versions_series_valid_time_idx
ON claim_versions(series_id, valid_from, valid_to);

CREATE INDEX claim_versions_series_recorded_time_idx
ON claim_versions(series_id, recorded_at, superseded_at);

CREATE TRIGGER claim_versions_kind_insert
BEFORE INSERT ON claim_versions
WHEN NEW.value_kind != (SELECT value_kind FROM claim_series WHERE id = NEW.series_id)
BEGIN
    SELECT RAISE(ABORT, 'claim version value kind must match its series');
END;

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

CREATE TABLE claim_values (
    claim_version_id TEXT PRIMARY KEY,
    value_kind TEXT NOT NULL CHECK (value_kind IN (
        'scalar', 'geometry', 'relationship', 'milestone', 'capability', 'capacity',
        'resource', 'constraint'
    )),
    FOREIGN KEY (claim_version_id, value_kind)
        REFERENCES claim_versions(id, value_kind) ON DELETE CASCADE,
    UNIQUE(claim_version_id, value_kind)
);

CREATE TABLE scalar_values (
    claim_version_id TEXT PRIMARY KEY,
    value_kind TEXT NOT NULL DEFAULT 'scalar' CHECK (value_kind = 'scalar'),
    scalar_type TEXT NOT NULL CHECK (
        scalar_type IN ('text', 'number', 'integer', 'boolean', 'date', 'timestamp')
    ),
    text_value TEXT,
    number_value REAL,
    integer_value INTEGER,
    boolean_value INTEGER CHECK (boolean_value IN (0, 1)),
    unit TEXT,
    FOREIGN KEY (claim_version_id, value_kind)
        REFERENCES claim_values(claim_version_id, value_kind) ON DELETE CASCADE,
    CHECK (
        (scalar_type IN ('text', 'date', 'timestamp')
            AND text_value IS NOT NULL AND number_value IS NULL
            AND integer_value IS NULL AND boolean_value IS NULL)
        OR (scalar_type = 'number'
            AND text_value IS NULL AND number_value IS NOT NULL
            AND integer_value IS NULL AND boolean_value IS NULL)
        OR (scalar_type = 'integer'
            AND text_value IS NULL AND number_value IS NULL
            AND integer_value IS NOT NULL AND boolean_value IS NULL)
        OR (scalar_type = 'boolean'
            AND text_value IS NULL AND number_value IS NULL
            AND integer_value IS NULL AND boolean_value IS NOT NULL)
    )
);

CREATE TABLE geometry_values (
    claim_version_id TEXT PRIMARY KEY,
    value_kind TEXT NOT NULL DEFAULT 'geometry' CHECK (value_kind = 'geometry'),
    geometry_type TEXT NOT NULL CHECK (geometry_type IN (
        'Point', 'LineString', 'Polygon', 'MultiPoint', 'MultiLineString', 'MultiPolygon'
    )),
    geometry_json TEXT NOT NULL CHECK (length(trim(geometry_json)) > 0),
    crs TEXT NOT NULL DEFAULT 'EPSG:4326' CHECK (length(trim(crs)) > 0),
    precision_m REAL CHECK (precision_m IS NULL OR precision_m >= 0),
    FOREIGN KEY (claim_version_id, value_kind)
        REFERENCES claim_values(claim_version_id, value_kind) ON DELETE CASCADE
);

CREATE TABLE relationship_values (
    claim_version_id TEXT PRIMARY KEY,
    value_kind TEXT NOT NULL DEFAULT 'relationship' CHECK (value_kind = 'relationship'),
    object_entity_id TEXT NOT NULL REFERENCES entities(id),
    relationship_type TEXT NOT NULL CHECK (length(trim(relationship_type)) > 0),
    attributes_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (claim_version_id, value_kind)
        REFERENCES claim_values(claim_version_id, value_kind) ON DELETE CASCADE
);

CREATE INDEX relationship_values_object_idx ON relationship_values(object_entity_id);

CREATE TABLE milestone_values (
    claim_version_id TEXT PRIMARY KEY,
    value_kind TEXT NOT NULL DEFAULT 'milestone' CHECK (value_kind = 'milestone'),
    milestone_type TEXT NOT NULL CHECK (length(trim(milestone_type)) > 0),
    status TEXT NOT NULL CHECK (status IN (
        'expected', 'started', 'completed', 'delayed', 'cancelled'
    )),
    date_low TEXT NOT NULL,
    date_base TEXT NOT NULL,
    date_high TEXT NOT NULL,
    FOREIGN KEY (claim_version_id, value_kind)
        REFERENCES claim_values(claim_version_id, value_kind) ON DELETE CASCADE,
    CHECK (date_low <= date_base AND date_base <= date_high)
);

CREATE TABLE capability_values (
    claim_version_id TEXT PRIMARY KEY,
    value_kind TEXT NOT NULL DEFAULT 'capability' CHECK (value_kind = 'capability'),
    capability_type TEXT NOT NULL CHECK (length(trim(capability_type)) > 0),
    text_value TEXT,
    number_value REAL,
    unit TEXT,
    qualifier TEXT,
    FOREIGN KEY (claim_version_id, value_kind)
        REFERENCES claim_values(claim_version_id, value_kind) ON DELETE CASCADE,
    CHECK ((text_value IS NULL) != (number_value IS NULL))
);

CREATE TABLE capacity_values (
    claim_version_id TEXT PRIMARY KEY,
    value_kind TEXT NOT NULL DEFAULT 'capacity' CHECK (value_kind = 'capacity'),
    metric TEXT NOT NULL CHECK (length(trim(metric)) > 0),
    basis TEXT NOT NULL CHECK (basis IN (
        'announced', 'physical_construction', 'tool_installed', 'qualified',
        'economically_usable'
    )),
    unit TEXT NOT NULL CHECK (length(trim(unit)) > 0),
    low REAL NOT NULL CHECK (low >= 0),
    base REAL NOT NULL,
    high REAL NOT NULL,
    period_start TEXT,
    period_end TEXT,
    FOREIGN KEY (claim_version_id, value_kind)
        REFERENCES claim_values(claim_version_id, value_kind) ON DELETE CASCADE,
    CHECK (low <= base AND base <= high),
    CHECK (period_end IS NULL OR period_start IS NULL OR period_end > period_start)
);

CREATE INDEX capacity_values_basis_metric_idx ON capacity_values(basis, metric);

CREATE TABLE resource_values (
    claim_version_id TEXT PRIMARY KEY,
    value_kind TEXT NOT NULL DEFAULT 'resource' CHECK (value_kind = 'resource'),
    resource_type TEXT NOT NULL CHECK (length(trim(resource_type)) > 0),
    unit TEXT NOT NULL CHECK (length(trim(unit)) > 0),
    low REAL NOT NULL CHECK (low >= 0),
    base REAL NOT NULL,
    high REAL NOT NULL,
    period_start TEXT,
    period_end TEXT,
    FOREIGN KEY (claim_version_id, value_kind)
        REFERENCES claim_values(claim_version_id, value_kind) ON DELETE CASCADE,
    CHECK (low <= base AND base <= high),
    CHECK (period_end IS NULL OR period_start IS NULL OR period_end > period_start)
);

CREATE TABLE constraint_values (
    claim_version_id TEXT PRIMARY KEY,
    value_kind TEXT NOT NULL DEFAULT 'constraint' CHECK (value_kind = 'constraint'),
    constraint_type TEXT NOT NULL CHECK (length(trim(constraint_type)) > 0),
    status TEXT NOT NULL CHECK (status IN ('potential', 'binding', 'mitigated', 'resolved')),
    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical', 'unknown')),
    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
    constrained_entity_id TEXT REFERENCES entities(id),
    attributes_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (claim_version_id, value_kind)
        REFERENCES claim_values(claim_version_id, value_kind) ON DELETE CASCADE
);

CREATE TABLE claim_evidence (
    id TEXT PRIMARY KEY,
    claim_version_id TEXT NOT NULL REFERENCES claim_versions(id) ON DELETE CASCADE,
    source_document_id TEXT NOT NULL REFERENCES source_documents(id),
    source_record_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('support', 'refute', 'context')),
    locator TEXT,
    excerpt TEXT,
    FOREIGN KEY (source_record_id, source_document_id)
        REFERENCES source_records(id, source_document_id),
    UNIQUE(id, claim_version_id)
);

CREATE INDEX claim_evidence_claim_idx ON claim_evidence(claim_version_id, role);
CREATE INDEX claim_evidence_document_idx ON claim_evidence(source_document_id);

CREATE TABLE claim_dependencies (
    claim_version_id TEXT NOT NULL REFERENCES claim_versions(id) ON DELETE CASCADE,
    depends_on_claim_version_id TEXT NOT NULL REFERENCES claim_versions(id),
    dependency_kind TEXT NOT NULL CHECK (
        dependency_kind IN ('derived_from', 'aggregates', 'transforms')
    ),
    PRIMARY KEY (claim_version_id, depends_on_claim_version_id, dependency_kind),
    CHECK (claim_version_id != depends_on_claim_version_id)
);

CREATE INDEX claim_dependencies_parent_idx
ON claim_dependencies(depends_on_claim_version_id);

CREATE TRIGGER claim_dependencies_no_cycle
BEFORE INSERT ON claim_dependencies
BEGIN
    SELECT CASE WHEN EXISTS (
        WITH RECURSIVE ancestry(claim_version_id) AS (
            SELECT NEW.depends_on_claim_version_id
            UNION
            SELECT dependencies.depends_on_claim_version_id
            FROM claim_dependencies AS dependencies
            JOIN ancestry
              ON dependencies.claim_version_id = ancestry.claim_version_id
        )
        SELECT 1
        FROM ancestry
        WHERE claim_version_id = NEW.claim_version_id
    ) THEN RAISE(ABORT, 'claim dependency cycle') END;
END;

CREATE TRIGGER claim_dependencies_immutable_update
BEFORE UPDATE ON claim_dependencies
BEGIN
    SELECT RAISE(ABORT, 'claim dependencies are immutable');
END;

CREATE TRIGGER claim_dependencies_immutable_delete
BEFORE DELETE ON claim_dependencies
BEGIN
    SELECT RAISE(ABORT, 'claim dependencies are immutable');
END;

CREATE TRIGGER source_families_immutable_update BEFORE UPDATE ON source_families
BEGIN SELECT RAISE(ABORT, 'source families are immutable'); END;
CREATE TRIGGER source_families_immutable_delete BEFORE DELETE ON source_families
BEGIN SELECT RAISE(ABORT, 'source families are immutable'); END;
CREATE TRIGGER sources_immutable_update BEFORE UPDATE ON sources
BEGIN SELECT RAISE(ABORT, 'sources are immutable'); END;
CREATE TRIGGER sources_immutable_delete BEFORE DELETE ON sources
BEGIN SELECT RAISE(ABORT, 'sources are immutable'); END;
CREATE TRIGGER ingestion_runs_immutable_update BEFORE UPDATE ON ingestion_runs
BEGIN SELECT RAISE(ABORT, 'ingestion runs are immutable'); END;
CREATE TRIGGER ingestion_runs_immutable_delete BEFORE DELETE ON ingestion_runs
BEGIN SELECT RAISE(ABORT, 'ingestion runs are immutable'); END;
CREATE TRIGGER entities_immutable_update BEFORE UPDATE ON entities
BEGIN SELECT RAISE(ABORT, 'entities are immutable'); END;
CREATE TRIGGER entities_immutable_delete BEFORE DELETE ON entities
BEGIN SELECT RAISE(ABORT, 'entities are immutable'); END;
CREATE TRIGGER claim_series_immutable_update BEFORE UPDATE ON claim_series
BEGIN SELECT RAISE(ABORT, 'claim series are immutable'); END;
CREATE TRIGGER claim_series_immutable_delete BEFORE DELETE ON claim_series
BEGIN SELECT RAISE(ABORT, 'claim series are immutable'); END;
CREATE TRIGGER claim_versions_immutable_delete BEFORE DELETE ON claim_versions
BEGIN SELECT RAISE(ABORT, 'claim versions are immutable'); END;
CREATE TRIGGER claim_versions_supersession_transition
BEFORE UPDATE OF superseded_at ON claim_versions
WHEN NEW.superseded_at IS NOT OLD.superseded_at
 AND (OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL)
BEGIN SELECT RAISE(ABORT, 'claim supersession can only transition from null once'); END;
CREATE TRIGGER claim_values_immutable_update BEFORE UPDATE ON claim_values
BEGIN SELECT RAISE(ABORT, 'claim values are immutable'); END;
CREATE TRIGGER claim_values_immutable_delete BEFORE DELETE ON claim_values
BEGIN SELECT RAISE(ABORT, 'claim values are immutable'); END;
CREATE TRIGGER scalar_values_immutable_update BEFORE UPDATE ON scalar_values
BEGIN SELECT RAISE(ABORT, 'scalar values are immutable'); END;
CREATE TRIGGER scalar_values_immutable_delete BEFORE DELETE ON scalar_values
BEGIN SELECT RAISE(ABORT, 'scalar values are immutable'); END;
CREATE TRIGGER geometry_values_immutable_update BEFORE UPDATE ON geometry_values
BEGIN SELECT RAISE(ABORT, 'geometry values are immutable'); END;
CREATE TRIGGER geometry_values_immutable_delete BEFORE DELETE ON geometry_values
BEGIN SELECT RAISE(ABORT, 'geometry values are immutable'); END;
CREATE TRIGGER relationship_values_immutable_update BEFORE UPDATE ON relationship_values
BEGIN SELECT RAISE(ABORT, 'relationship values are immutable'); END;
CREATE TRIGGER relationship_values_immutable_delete BEFORE DELETE ON relationship_values
BEGIN SELECT RAISE(ABORT, 'relationship values are immutable'); END;
CREATE TRIGGER milestone_values_immutable_update BEFORE UPDATE ON milestone_values
BEGIN SELECT RAISE(ABORT, 'milestone values are immutable'); END;
CREATE TRIGGER milestone_values_immutable_delete BEFORE DELETE ON milestone_values
BEGIN SELECT RAISE(ABORT, 'milestone values are immutable'); END;
CREATE TRIGGER capability_values_immutable_update BEFORE UPDATE ON capability_values
BEGIN SELECT RAISE(ABORT, 'capability values are immutable'); END;
CREATE TRIGGER capability_values_immutable_delete BEFORE DELETE ON capability_values
BEGIN SELECT RAISE(ABORT, 'capability values are immutable'); END;
CREATE TRIGGER capacity_values_immutable_update BEFORE UPDATE ON capacity_values
BEGIN SELECT RAISE(ABORT, 'capacity values are immutable'); END;
CREATE TRIGGER capacity_values_immutable_delete BEFORE DELETE ON capacity_values
BEGIN SELECT RAISE(ABORT, 'capacity values are immutable'); END;
CREATE TRIGGER resource_values_immutable_update BEFORE UPDATE ON resource_values
BEGIN SELECT RAISE(ABORT, 'resource values are immutable'); END;
CREATE TRIGGER resource_values_immutable_delete BEFORE DELETE ON resource_values
BEGIN SELECT RAISE(ABORT, 'resource values are immutable'); END;
CREATE TRIGGER constraint_values_immutable_update BEFORE UPDATE ON constraint_values
BEGIN SELECT RAISE(ABORT, 'constraint values are immutable'); END;
CREATE TRIGGER constraint_values_immutable_delete BEFORE DELETE ON constraint_values
BEGIN SELECT RAISE(ABORT, 'constraint values are immutable'); END;
CREATE TRIGGER claim_evidence_immutable_update BEFORE UPDATE ON claim_evidence
BEGIN SELECT RAISE(ABORT, 'claim evidence is immutable'); END;
CREATE TRIGGER claim_evidence_immutable_delete BEFORE DELETE ON claim_evidence
BEGIN SELECT RAISE(ABORT, 'claim evidence is immutable'); END;
