from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from semiconductor_atlas.cli import main
from semiconductor_atlas.database import connect, initialize
from semiconductor_atlas.gleif_review import (
    GLEIF_REVIEW_FORMAT,
    read_gleif_review_file,
)
from semiconductor_atlas.gleif_snapshot import (
    canonical_lei_allowlist_bytes,
    create_gleif_snapshot,
)
from semiconductor_atlas.ingest_gleif import import_gleif_level1
from semiconductor_atlas.models import (
    ClaimKind,
    ClaimSeries,
    ClaimVersion,
    Entity,
    EntityKind,
    EvidenceLink,
    IngestionRun,
    IngestionStatus,
    OrganizationNameClaimMetadata,
    OrganizationNameType,
    ScalarType,
    ScalarValue,
    Source,
    SourceDocument,
    SourceFamily,
    SourceRecord,
    ValueKind,
    source_record_payload_sha256,
)
from semiconductor_atlas.repository import (
    add_claim_series,
    add_entity,
    add_ingestion_run,
    add_organization_name_claim_metadata,
    add_source,
    add_source_document,
    add_source_family,
    add_source_record,
    insert_claim,
    stable_id,
    validate_database,
)
from tests.test_gleif_snapshot import (
    FIRST_LEI,
    _FakeTime,
    _SequenceTransport,
    _response_raw,
)


TARGET_KEY = "organization:canonical:tsmc-arizona"
TARGET_NAME = "TSMC Arizona Corporation"
TARGET_RECORDED_AT = "2026-07-18T00:00:04Z"
FIRST_ACCEPTED_AT = "2026-07-20T12:01:00Z"
FIRST_REVIEWED_AT = "2026-07-20T12:00:10Z"


class GLEIFImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "atlas.sqlite"
        self.connection, _ = initialize(self.database_path)
        self.target_entity_id, self.target_claim_id, self.target_run_id = (
            self._seed_target()
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def _seed_target(self) -> tuple[str, str, str]:
        family_id = stable_id("source-family", "target-fixture")
        source_id = stable_id("source", "target-fixture")
        document_id = stable_id("document", "target-fixture")
        run_id = stable_id("run", "target-fixture")
        record_id = stable_id("record", "target-fixture")
        target_id = stable_id("entity", TARGET_KEY)
        claim_id = stable_id("claim", "target-fixture", "name")
        add_source_family(
            self.connection,
            SourceFamily(
                family_id,
                "target-fixture",
                "Target fixture",
                "2026-07-18T00:00:00Z",
            ),
        )
        add_source(
            self.connection,
            Source(
                source_id,
                family_id,
                "target-fixture",
                "Target fixture",
                "Test publisher",
                "https://example.test/target",
                "2026-07-18T00:00:00Z",
            ),
        )
        add_source_document(
            self.connection,
            SourceDocument(
                document_id,
                source_id,
                "https://example.test/target/name.json",
                "Target name fixture",
                "2026-07-18T00:00:00Z",
                "a" * 64,
                media_type="application/json",
            ),
        )
        add_ingestion_run(
            self.connection,
            IngestionRun(
                run_id,
                source_id,
                "2026-07-18T00:00:01Z",
                status=IngestionStatus.SUCCEEDED,
                completed_at="2026-07-18T00:00:02Z",
                input_document_id=document_id,
                code_version="target-fixture-v1",
            ),
        )
        payload = {"name": TARGET_NAME}
        add_source_record(
            self.connection,
            SourceRecord(
                record_id,
                run_id,
                document_id,
                "target:name",
                "2026-07-18T00:00:00Z",
                source_record_payload_sha256(payload),
                payload=payload,
            ),
        )
        add_entity(
            self.connection,
            Entity(
                target_id,
                EntityKind.ORGANIZATION,
                TARGET_KEY,
                "2026-07-18T00:00:03Z",
                TARGET_NAME,
                run_id,
            ),
        )
        series = ClaimSeries(
            stable_id("series", target_id, "name"),
            target_id,
            f"{TARGET_KEY}:claim:name",
            "name",
            ValueKind.SCALAR,
            "2026-07-18T00:00:03Z",
        )
        add_claim_series(self.connection, series)
        insert_claim(
            self.connection,
            ClaimVersion(
                claim_id,
                series.id,
                "2026-07-18",
                TARGET_RECORDED_AT,
                ClaimKind.SOURCE_STATEMENT,
                "target_fixture_name",
                1.0,
                created_by_run_id=run_id,
            ),
            ScalarValue(ScalarType.TEXT, TARGET_NAME),
            evidence=(
                EvidenceLink(
                    document_id,
                    source_record_id=record_id,
                    locator="/name",
                    excerpt=TARGET_NAME,
                ),
            ),
        )
        add_organization_name_claim_metadata(
            self.connection,
            OrganizationNameClaimMetadata(
                claim_id,
                OrganizationNameType.LEGAL,
                language_tag="en",
            ),
        )
        self.connection.commit()
        return target_id, claim_id, run_id

    def _snapshot(
        self,
        name: str,
        *,
        raw: bytes | None = None,
        origin: datetime | None = None,
    ):
        clock = _FakeTime()
        if origin is not None:
            clock.origin = origin
        response = raw or _response_raw(FIRST_LEI, name=TARGET_NAME)
        transport = _SequenceTransport([response], clock)
        return create_gleif_snapshot(
            canonical_lei_allowlist_bytes((FIRST_LEI,)),
            self.root / name,
            transport=transport,
            wall_clock=clock.wall_clock,
            monotonic_clock=clock.monotonic,
            sleeper=clock.sleep,
        )

    def _seed_late_completed_target_evidence(self) -> str:
        source_id = stable_id("source", "target-fixture")
        document_id = stable_id("document", "target-fixture")
        run_id = stable_id("run", "target-fixture", "late-completion")
        record_id = stable_id("record", "target-fixture", "late-completion")
        add_ingestion_run(
            self.connection,
            IngestionRun(
                run_id,
                source_id,
                "2026-07-18T00:00:01Z",
                status=IngestionStatus.SUCCEEDED,
                completed_at="2026-07-20T12:00:30Z",
                input_document_id=document_id,
                code_version="target-fixture-late-v1",
            ),
        )
        payload = {"alias": "TSMC Arizona"}
        add_source_record(
            self.connection,
            SourceRecord(
                record_id,
                run_id,
                document_id,
                "target:late-alias",
                "2026-07-18T00:00:00Z",
                source_record_payload_sha256(payload),
                payload=payload,
            ),
        )
        series = ClaimSeries(
            stable_id("series", self.target_entity_id, "late-alias"),
            self.target_entity_id,
            f"{TARGET_KEY}:claim:late-alias",
            "name",
            ValueKind.SCALAR,
            "2026-07-18T00:00:03Z",
        )
        add_claim_series(self.connection, series)
        claim_id = stable_id("claim", "target-fixture", "late-alias")
        insert_claim(
            self.connection,
            ClaimVersion(
                claim_id,
                series.id,
                "2026-07-18",
                TARGET_RECORDED_AT,
                ClaimKind.SOURCE_STATEMENT,
                "target_fixture_late_alias",
                1.0,
                created_by_run_id=run_id,
            ),
            ScalarValue(ScalarType.TEXT, "TSMC Arizona"),
            evidence=(
                EvidenceLink(
                    document_id,
                    source_record_id=record_id,
                    locator="/alias",
                    excerpt="TSMC Arizona",
                ),
            ),
        )
        self.connection.commit()
        return claim_id

    def _review(
        self,
        name: str,
        snapshot,
        *,
        reviewed_at: str = FIRST_REVIEWED_AT,
        outcome: str = "match",
        target_claim_ids: list[str] | None = None,
    ):
        payload = {
            "format": GLEIF_REVIEW_FORMAT,
            "reviewed_by": "reviewer@example.com",
            "reviewed_at": reviewed_at,
            "snapshot_manifest_sha256": snapshot.manifest_sha256,
            "golden_copy_publish_date": snapshot.golden_copy_publish_date,
            "decisions": [
                {
                    "lei": FIRST_LEI,
                    "target_entity_id": self.target_entity_id,
                    "target_entity_stable_key": TARGET_KEY,
                    "target_evidence_claim_version_ids": target_claim_ids
                    or [self.target_claim_id],
                    "outcome": outcome,
                    "reason": "Exact legal name and Arizona identity agree.",
                    "score": 1.0,
                    "candidate_rank": 1,
                    "assignment_valid_from": (
                        snapshot.golden_copy_publish_date[:10]
                        if outcome == "match"
                        else None
                    ),
                }
            ],
        }
        path = self.root / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return read_gleif_review_file(path)

    def test_imports_source_native_claims_and_reviewed_assignment_then_replays_exactly(
        self,
    ) -> None:
        snapshot = self._snapshot("snapshot-1")
        review = self._review("review-1.json", snapshot)

        result = import_gleif_level1(
            self.connection,
            snapshot,
            review,
            accepted_at=FIRST_ACCEPTED_AT,
        )
        self.connection.commit()

        self.assertFalse(result.replayed_existing_ingestion)
        self.assertFalse(result.replayed_existing_resolution)
        self.assertEqual(1, result.records_imported)
        self.assertEqual(1, result.source_records_created)
        self.assertEqual(1, result.entities_created)
        self.assertGreater(result.claims_created, 10)
        self.assertEqual(1, result.resolution_candidates_created)
        self.assertEqual(1, result.resolution_decisions_created)
        self.assertEqual(1, result.assignments_created)
        self.assertEqual("2026-07-20T12:01:06Z", result.knowledge_cutoff_at)
        self.assertEqual([], validate_database(self.connection))

        observed_entity_id = result.observed_entity_ids[0]
        self.assertNotEqual(self.target_entity_id, observed_entity_id)
        gleif_claim_subjects = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT DISTINCT series.subject_entity_id
                FROM claim_versions AS versions
                JOIN claim_series AS series ON series.id = versions.series_id
                WHERE versions.created_by_run_id = ?
                """,
                (result.ingestion_run_id,),
            )
        }
        self.assertEqual({observed_entity_id}, gleif_claim_subjects)
        self.assertEqual(
            1,
            self.connection.execute(
                """
                SELECT COUNT(*)
                FROM organization_identifier_claim_metadata AS metadata
                JOIN claim_versions AS versions
                  ON versions.id = metadata.claim_version_id
                JOIN claim_series AS series ON series.id = versions.series_id
                WHERE series.subject_entity_id = ?
                  AND metadata.scheme = 'LEI'
                  AND metadata.normalized_value = ?
                """,
                (observed_entity_id, FIRST_LEI),
            ).fetchone()[0],
        )
        assignment = self.connection.execute(
            "SELECT * FROM source_entity_assignments WHERE id = ?",
            (result.assignment_ids[0],),
        ).fetchone()
        self.assertEqual(self.target_entity_id, assignment["canonical_entity_id"])
        self.assertEqual(observed_entity_id, assignment["observed_entity_id"])
        resolution_inputs = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT ingestion_run_id FROM entity_resolution_run_inputs
                WHERE resolution_run_id = ?
                """,
                (result.resolution_run_id,),
            )
        }
        self.assertEqual(
            {self.target_run_id, result.ingestion_run_id}, resolution_inputs
        )
        roles = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT role FROM ingestion_run_documents
                WHERE ingestion_run_id = ?
                """,
                (result.ingestion_run_id,),
            )
        }
        self.assertEqual(
            {"primary", "source_record", f"raw_response:{FIRST_LEI}"}, roles
        )

        before = hashlib.sha256(self.database_path.read_bytes()).hexdigest()
        replay = import_gleif_level1(
            self.connection,
            snapshot,
            review,
            accepted_at=FIRST_ACCEPTED_AT,
        )
        self.connection.commit()
        after = hashlib.sha256(self.database_path.read_bytes()).hexdigest()

        self.assertTrue(replay.replayed_existing_ingestion)
        self.assertTrue(replay.replayed_existing_resolution)
        self.assertEqual(0, replay.source_documents_created)
        self.assertEqual(0, replay.source_records_created)
        self.assertEqual(0, replay.entities_created)
        self.assertEqual(0, replay.claim_series_created)
        self.assertEqual(0, replay.claims_created)
        self.assertEqual(0, replay.resolution_candidates_created)
        self.assertEqual(before, after)

    def test_refresh_closes_changed_and_omitted_fields_but_reuses_unchanged_claims(
        self,
    ) -> None:
        first_payload = json.loads(_response_raw(FIRST_LEI, name=TARGET_NAME))
        first_payload["data"]["attributes"]["entity"]["otherNames"] = [
            {"name": "TSMC Arizona", "language": "en", "type": "ALTERNATIVE_LANGUAGE_LEGAL_NAME"}
        ]
        first_snapshot = self._snapshot(
            "snapshot-first",
            raw=json.dumps(first_payload, separators=(",", ":")).encode("utf-8"),
        )
        first_review = self._review("review-first.json", first_snapshot)
        first = import_gleif_level1(
            self.connection,
            first_snapshot,
            first_review,
            accepted_at=FIRST_ACCEPTED_AT,
        )
        self.connection.commit()

        second_raw = _response_raw(
            FIRST_LEI,
            "2026-07-21T00:00:00Z",
            name="TSMC Arizona Corporation LLC",
        )
        second_snapshot = self._snapshot(
            "snapshot-second",
            raw=second_raw,
            origin=datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC),
        )
        second_review = self._review(
            "review-second.json",
            second_snapshot,
            reviewed_at="2026-07-21T12:00:10Z",
        )
        second = import_gleif_level1(
            self.connection,
            second_snapshot,
            second_review,
            accepted_at="2026-07-21T12:01:00Z",
        )
        self.connection.commit()

        claims_created_by_second_run = self.connection.execute(
            "SELECT COUNT(*) FROM claim_versions WHERE created_by_run_id = ?",
            (second.ingestion_run_id,),
        ).fetchone()[0]
        self.assertEqual(claims_created_by_second_run, second.claims_created)
        self.assertGreater(second.unchanged_claims_reused, 8)
        self.assertEqual(2, second.prior_open_claims_closed_or_corrected)
        self.assertEqual(1, second.assignments_superseded)
        self.assertEqual(
            "2026-07-21T12:01:06Z",
            self.connection.execute(
                "SELECT superseded_at FROM source_entity_assignments WHERE id = ?",
                (first.assignment_ids[0],),
            ).fetchone()[0],
        )
        observed_id = first.observed_entity_ids[0]
        legal_rows = self.connection.execute(
            """
            SELECT versions.valid_from, versions.valid_to, versions.recorded_at,
                   versions.superseded_at, scalar.text_value
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            JOIN scalar_values AS scalar ON scalar.claim_version_id = versions.id
            WHERE series.subject_entity_id = ?
              AND series.predicate = 'organization.name'
              AND series.stable_key LIKE '%:legal'
            ORDER BY versions.recorded_at, versions.id
            """,
            (observed_id,),
        ).fetchall()
        self.assertEqual(3, len(legal_rows))
        closure = next(row for row in legal_rows if row["valid_to"] is not None)
        current = next(
            row
            for row in legal_rows
            if row["valid_to"] is None and row["superseded_at"] is None
        )
        self.assertEqual("2026-07-21", closure["valid_to"])
        self.assertEqual("TSMC Arizona Corporation LLC", current["text_value"])
        other_current = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.subject_entity_id = ?
              AND series.stable_key LIKE '%:other:%'
              AND versions.valid_to IS NULL
              AND versions.superseded_at IS NULL
            """,
            (observed_id,),
        ).fetchone()[0]
        self.assertEqual(0, other_current)
        status_versions = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.subject_entity_id = ?
              AND series.predicate = 'gleif.entity_status'
            """,
            (observed_id,),
        ).fetchone()[0]
        self.assertEqual(1, status_versions)
        self.assertEqual([], validate_database(self.connection))

    def test_duplicate_repeated_json_values_collapse_semantically(self) -> None:
        payload = json.loads(_response_raw(FIRST_LEI, name=TARGET_NAME))
        event = {"type": "LEGAL_NAME_CHANGE", "groupId": "event-1"}
        payload["data"]["attributes"]["entity"]["eventGroups"] = [event, event]
        snapshot = self._snapshot(
            "snapshot-duplicate-events",
            raw=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        )
        review = self._review("review-duplicate-events.json", snapshot)

        result = import_gleif_level1(
            self.connection,
            snapshot,
            review,
            accepted_at=FIRST_ACCEPTED_AT,
        )
        self.connection.commit()

        self.assertEqual(
            1,
            self.connection.execute(
                """
                SELECT COUNT(*)
                FROM claim_versions AS versions
                JOIN claim_series AS series ON series.id = versions.series_id
                WHERE series.subject_entity_id = ?
                  AND series.predicate = 'gleif.legal_entity_event_group'
                """,
                (result.observed_entity_ids[0],),
            ).fetchone()[0],
        )

    def test_reject_decision_records_history_without_assignment(self) -> None:
        snapshot = self._snapshot("snapshot-reject")
        review = self._review("review-reject.json", snapshot, outcome="reject")

        result = import_gleif_level1(
            self.connection,
            snapshot,
            review,
            accepted_at=FIRST_ACCEPTED_AT,
        )
        self.connection.commit()

        self.assertEqual(1, result.resolution_decisions_created)
        self.assertEqual(0, result.assignments_created)
        self.assertEqual((), result.assignment_ids)
        self.assertEqual(
            "reject",
            self.connection.execute(
                """
                SELECT decisions.outcome
                FROM entity_resolution_decisions AS decisions
                JOIN entity_resolution_candidates AS candidates
                  ON candidates.id = decisions.candidate_id
                WHERE candidates.resolution_run_id = ?
                """,
                (result.resolution_run_id,),
            ).fetchone()[0],
        )

    def test_later_review_can_supersede_prior_match_with_reject(self) -> None:
        snapshot = self._snapshot("snapshot-correction")
        match_review = self._review("review-match.json", snapshot)
        matched = import_gleif_level1(
            self.connection,
            snapshot,
            match_review,
            accepted_at=FIRST_ACCEPTED_AT,
        )
        self.connection.commit()
        reject_review = self._review(
            "review-corrected-reject.json",
            snapshot,
            reviewed_at="2026-07-20T13:00:10Z",
            outcome="reject",
        )

        rejected = import_gleif_level1(
            self.connection,
            snapshot,
            reject_review,
            accepted_at="2026-07-20T13:01:00Z",
        )
        self.connection.commit()

        self.assertEqual(1, rejected.assignments_superseded)
        self.assertEqual(0, rejected.assignments_created)
        prior = self.connection.execute(
            "SELECT superseded_at FROM source_entity_assignments WHERE id = ?",
            (matched.assignment_ids[0],),
        ).fetchone()
        self.assertEqual("2026-07-20T13:01:06Z", prior["superseded_at"])
        self.assertEqual(
            0,
            self.connection.execute(
                """
                SELECT COUNT(*) FROM source_entity_assignments
                WHERE observed_entity_id = ? AND superseded_at IS NULL
                """,
                (matched.observed_entity_ids[0],),
            ).fetchone()[0],
        )
        self.assertEqual(2, self.connection.execute(
            "SELECT COUNT(*) FROM entity_resolution_decisions"
        ).fetchone()[0])
        self.assertEqual([], validate_database(self.connection))

    def test_rejects_refresh_inside_prior_logical_knowledge_window(self) -> None:
        snapshot = self._snapshot("snapshot-overlap")
        review = self._review("review-overlap.json", snapshot)
        import_gleif_level1(
            self.connection,
            snapshot,
            review,
            accepted_at=FIRST_ACCEPTED_AT,
        )
        self.connection.commit()

        with self.assertRaisesRegex(ValueError, "prior GLEIF knowledge cutoff"):
            import_gleif_level1(
                self.connection,
                snapshot,
                review,
                accepted_at="2026-07-20T12:01:00.500000Z",
            )

        self.assertEqual(
            1,
            self.connection.execute(
                """
                SELECT COUNT(*)
                FROM ingestion_runs AS runs
                JOIN sources ON sources.id = runs.source_id
                WHERE sources.stable_key = 'gleif-lei-api-level-1'
                """
            ).fetchone()[0],
        )

    def test_review_mutation_at_acceptance_rolls_back_all_gleif_writes(self) -> None:
        snapshot = self._snapshot("snapshot-mutation")
        review = self._review("review-mutation.json", snapshot)
        assert review.path is not None
        review.path.write_bytes(review.raw_bytes + b" ")

        with self.assertRaisesRegex(ValueError, "review changed"):
            import_gleif_level1(
                self.connection,
                snapshot,
                review,
                accepted_at=FIRST_ACCEPTED_AT,
            )

        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(
            0,
            self.connection.execute(
                "SELECT COUNT(*) FROM sources WHERE stable_key = 'gleif-lei-api-level-1'"
            ).fetchone()[0],
        )
        self.assertEqual(
            0,
            self.connection.execute(
                "SELECT COUNT(*) FROM entities WHERE stable_key LIKE 'gleif:lei:%'"
            ).fetchone()[0],
        )

    def test_rejects_unbound_review_and_non_target_evidence_before_writing(self) -> None:
        snapshot = self._snapshot("snapshot-invalid")
        review_path = self.root / "review-invalid.json"
        payload = {
            "format": GLEIF_REVIEW_FORMAT,
            "reviewed_by": "reviewer@example.com",
            "reviewed_at": FIRST_REVIEWED_AT,
            "snapshot_manifest_sha256": "f" * 64,
            "golden_copy_publish_date": snapshot.golden_copy_publish_date,
            "decisions": [
                {
                    "lei": FIRST_LEI,
                    "target_entity_id": self.target_entity_id,
                    "target_entity_stable_key": TARGET_KEY,
                    "target_evidence_claim_version_ids": [self.target_claim_id],
                    "outcome": "match",
                    "reason": "fixture",
                    "score": 1.0,
                    "candidate_rank": 1,
                    "assignment_valid_from": "2026-07-19",
                }
            ],
        }
        review_path.write_text(json.dumps(payload), encoding="utf-8")
        review = read_gleif_review_file(review_path)
        with self.assertRaisesRegex(ValueError, "does not bind"):
            import_gleif_level1(
                self.connection,
                snapshot,
                review,
                accepted_at=FIRST_ACCEPTED_AT,
            )
        self.assertEqual(
            0,
            self.connection.execute(
                "SELECT COUNT(*) FROM sources WHERE stable_key = 'gleif-lei-api-level-1'"
            ).fetchone()[0],
        )

    def test_rejects_forged_verified_objects_and_review_before_acquisition(self) -> None:
        snapshot = self._snapshot("snapshot-forged")
        review = self._review("review-forged.json", snapshot)
        forged_raw = snapshot.responses[0].raw_bytes.replace(b"TSMC", b"EVIL")
        self.assertEqual(len(snapshot.responses[0].raw_bytes), len(forged_raw))
        forged_snapshot = replace(
            snapshot,
            responses=(replace(snapshot.responses[0], raw_bytes=forged_raw),),
        )
        with self.assertRaisesRegex(ValueError, "snapshot bytes changed"):
            import_gleif_level1(
                self.connection,
                forged_snapshot,
                review,
                accepted_at=FIRST_ACCEPTED_AT,
            )

        forged_decision = replace(
            review.decisions[0], outcome="reject", assignment_valid_from=None
        )
        forged_review = replace(review, decisions=(forged_decision,))
        with self.assertRaisesRegex(ValueError, "review changed"):
            import_gleif_level1(
                self.connection,
                snapshot,
                forged_review,
                accepted_at=FIRST_ACCEPTED_AT,
            )

        early_review = self._review(
            "review-too-early.json",
            snapshot,
            reviewed_at="2026-07-20T11:59:59Z",
        )
        with self.assertRaisesRegex(ValueError, "must not predate"):
            import_gleif_level1(
                self.connection,
                snapshot,
                early_review,
                accepted_at=FIRST_ACCEPTED_AT,
            )
        self.assertEqual(
            0,
            self.connection.execute(
                "SELECT COUNT(*) FROM sources WHERE stable_key = 'gleif-lei-api-level-1'"
            ).fetchone()[0],
        )

    def test_rejects_target_evidence_from_run_completed_after_review(self) -> None:
        late_claim_id = self._seed_late_completed_target_evidence()
        snapshot = self._snapshot("snapshot-late-target-run")
        review = self._review(
            "review-late-target-run.json",
            snapshot,
            target_claim_ids=[late_claim_id],
        )

        with self.assertRaisesRegex(ValueError, "produced after the review"):
            import_gleif_level1(
                self.connection,
                snapshot,
                review,
                accepted_at=FIRST_ACCEPTED_AT,
            )

        self.assertEqual(
            0,
            self.connection.execute(
                "SELECT COUNT(*) FROM sources WHERE stable_key = 'gleif-lei-api-level-1'"
            ).fetchone()[0],
        )

    def test_cli_verifies_review_imports_and_reports_schema_v4_cutoff(self) -> None:
        snapshot = self._snapshot("snapshot-cli")
        review = self._review("review-cli.json", snapshot)
        assert review.path is not None
        self.connection.close()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                [
                    "ingest-gleif-snapshot",
                    "--database",
                    str(self.database_path),
                    "--snapshot",
                    str(snapshot.root),
                    "--review-plan",
                    str(review.path),
                    "--accepted-at",
                    FIRST_ACCEPTED_AT,
                ]
            )

        self.assertEqual(0, code)
        result = json.loads(output.getvalue())
        self.assertEqual(5, result["schema_version"])
        self.assertEqual(3, result["snapshot_inputs_verified"])
        self.assertEqual(
            "2026-07-20T12:01:06Z", result["knowledge_cutoff_at"]
        )
        self.assertEqual(1, result["gleif"]["records_imported"])
        self.assertEqual(1, result["gleif"]["assignments_created"])
        self.connection = connect(self.database_path)
        self.assertEqual([], validate_database(self.connection))


if __name__ == "__main__":
    unittest.main()
