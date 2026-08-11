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
