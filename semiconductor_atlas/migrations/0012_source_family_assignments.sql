-- Correct publisher grouping per source without changing immutable importer labels.
CREATE TABLE source_family_assignments (
    id TEXT PRIMARY KEY NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(id),
    family_id TEXT NOT NULL REFERENCES source_families(id),
    supersedes_id TEXT UNIQUE REFERENCES source_family_assignments(id),
    recorded_at TEXT NOT NULL CHECK (atlas_timestamp_us(recorded_at) IS NOT NULL),
    reviewed_at TEXT NOT NULL CHECK (atlas_timestamp_us(reviewed_at) IS NOT NULL),
    review_sha256 TEXT NOT NULL CHECK (length(review_sha256)=64 AND review_sha256 NOT GLOB '*[^0-9a-f]*'),
    review_json TEXT NOT NULL CHECK (json_valid(review_json)),
    CHECK (atlas_timestamp_us(reviewed_at) <= atlas_timestamp_us(recorded_at))
);
CREATE INDEX source_family_assignments_source_time_idx
ON source_family_assignments(source_id, atlas_timestamp_us(recorded_at));

CREATE TRIGGER source_family_assignments_guard BEFORE INSERT ON source_family_assignments
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM sources s JOIN source_families f ON f.id=NEW.family_id
        JOIN source_families original ON original.id=s.family_id
        WHERE s.id=NEW.source_id AND atlas_timestamp_us(s.created_at) <= atlas_timestamp_us(NEW.reviewed_at)
          AND atlas_timestamp_us(f.created_at) <= atlas_timestamp_us(NEW.reviewed_at)
          AND atlas_timestamp_us(original.created_at) <= atlas_timestamp_us(NEW.reviewed_at)
    ) THEN RAISE(ABORT, 'source family assignment endpoints must be known before review') END;
    SELECT CASE WHEN NEW.supersedes_id IS NULL AND EXISTS (
        SELECT 1 FROM source_family_assignments WHERE source_id=NEW.source_id
    ) THEN RAISE(ABORT, 'source family assignment requires its current predecessor') END;
    SELECT CASE WHEN NEW.supersedes_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM source_family_assignments p WHERE p.id=NEW.supersedes_id
          AND p.source_id=NEW.source_id AND p.family_id != NEW.family_id
          AND atlas_timestamp_us(p.recorded_at) < atlas_timestamp_us(NEW.recorded_at)
          AND atlas_timestamp_us(p.recorded_at) <= atlas_timestamp_us(NEW.reviewed_at)
          AND NOT EXISTS (SELECT 1 FROM source_family_assignments n WHERE n.supersedes_id=p.id)
    ) THEN RAISE(ABORT, 'source family assignment predecessor, chronology or change differs') END;
    SELECT CASE WHEN NEW.supersedes_id IS NULL AND EXISTS (
        SELECT 1 FROM sources WHERE id=NEW.source_id AND family_id=NEW.family_id
    ) THEN RAISE(ABORT, 'initial source family assignment must change the original label') END;
END;
CREATE TRIGGER source_family_assignments_immutable_update BEFORE UPDATE ON source_family_assignments
BEGIN SELECT RAISE(ABORT, 'source family assignments are immutable'); END;
CREATE TRIGGER source_family_assignments_immutable_delete BEFORE DELETE ON source_family_assignments
BEGIN SELECT RAISE(ABORT, 'source family assignments are immutable'); END;
