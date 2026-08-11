from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas.database import initialize
from semiconductor_atlas.models import (
    ClaimKind,
    ClaimSeries,
    ClaimVersion,
    Entity,
    EntityKind,
    EntityResolutionCandidate,
    EntityResolutionDecision,
    EntityResolutionRun,
    EntityResolutionRunInput,
    EntityResolutionStatus,
    EvidenceLink,
    IngestionRun,
    IngestionRunDocument,
    IngestionStatus,
    OrganizationIdentifierClaimMetadata,
    OrganizationNameClaimMetadata,
    OrganizationNameType,
    ResolutionDecisionOutcome,
    ScalarType,
    ScalarValue,
    Source,
    SourceDocument,
    SourceEntityAssignment,
    SourceFamily,
    SourceRecord,
    ValueKind,
    source_record_payload_sha256,
)
from semiconductor_atlas.repository import (
    add_claim_series,
    add_entity,
    add_entity_resolution_candidate,
    add_entity_resolution_decision,
    add_entity_resolution_run,
    add_entity_resolution_run_input,
    add_ingestion_run,
    add_ingestion_run_document,
    add_organization_identifier_claim_metadata,
    add_organization_name_claim_metadata,
    add_source,
    add_source_document,
    add_source_entity_assignment,
    add_source_family,
    add_source_record,
    current_source_entity_assignments,
    finalize_entity_resolution_run,
    insert_claim,
    stable_id,
    supersede_source_entity_assignment,
    validate_database,
)


SOURCE_RETRIEVED_AT = "2026-01-01T00:00:00Z"
INGESTION_STARTED_AT = "2026-01-01T00:01:00Z"
INGESTION_COMPLETED_AT = "2026-01-01T00:02:00Z"
ENTITY_CREATED_AT = "2026-01-01T00:03:00Z"
CLAIM_RECORDED_AT = "2026-01-01T00:04:00Z"
RESOLUTION_STARTED_AT = "2026-01-02T00:00:00Z"
RESOLUTION_COMPLETED_AT = "2026-01-02T00:02:30Z"
CANDIDATE_CREATED_AT = "2026-01-02T00:02:00Z"
DECIDED_AT = "2026-01-02T00:03:00Z"
ASSIGNED_AT = "2026-01-02T00:04:00Z"


class IdentityResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.connection, _ = initialize(
            Path(self.temporary_directory.name) / "atlas.sqlite"
        )
        self.family_id = stable_id("source-family", "identity-fixture")
        self.source_id = stable_id("source", "identity-fixture")
        self.other_source_id = stable_id("source", "other-identity-fixture")
        self.document_id = stable_id("document", "identity-fixture")
        self.other_document_id = stable_id("document", "other-identity-fixture")
        self.ingestion_run_id = stable_id("ingestion-run", "identity-fixture")
        self.source_record_id = stable_id("source-record", "identity-fixture")
        self.observed_entity_id = stable_id("entity", "source:organization:observed")
        self.canonical_entity_id = stable_id("entity", "gleif:lei:canonical")
        self.alternate_entity_id = stable_id("entity", "gleif:lei:alternate")
        self.site_id = stable_id("entity", "source:site:not-an-organization")
        self.name_claim_id = self._seed_source_layer()

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def _seed_source_layer(self) -> str:
        add_source_family(
            self.connection,
            SourceFamily(
                self.family_id,
                "identity-fixture",
                "Identity fixture",
                SOURCE_RETRIEVED_AT,
            ),
        )
        for source_id, stable_key in (
            (self.source_id, "identity-fixture"),
            (self.other_source_id, "other-identity-fixture"),
        ):
            add_source(
                self.connection,
                Source(
                    source_id,
                    self.family_id,
                    stable_key,
                    stable_key,
                    "Fixture publisher",
                    f"https://example.com/{stable_key}",
                    SOURCE_RETRIEVED_AT,
                ),
            )
        add_source_document(
            self.connection,
            SourceDocument(
                self.document_id,
                self.source_id,
                "https://example.com/identity/record",
                "Identity record",
                SOURCE_RETRIEVED_AT,
                "a" * 64,
            ),
        )
        add_source_document(
            self.connection,
            SourceDocument(
                self.other_document_id,
                self.other_source_id,
                "https://example.com/other/record",
                "Other identity record",
                SOURCE_RETRIEVED_AT,
                "b" * 64,
            ),
        )
        add_ingestion_run(
            self.connection,
            IngestionRun(
                self.ingestion_run_id,
                self.source_id,
                INGESTION_STARTED_AT,
                status=IngestionStatus.SUCCEEDED,
                completed_at=INGESTION_COMPLETED_AT,
                code_version="identity-fixture-v1",
                input_document_id=self.document_id,
            ),
        )
        payload = {"name": "Observed Semiconductor"}
        add_source_record(
            self.connection,
            SourceRecord(
                self.source_record_id,
                self.ingestion_run_id,
                self.document_id,
                "organization:observed",
                SOURCE_RETRIEVED_AT,
                source_record_payload_sha256(payload),
                payload=payload,
            ),
        )
        for entity_id, kind, stable_key, display_name in (
            (
                self.observed_entity_id,
                EntityKind.ORGANIZATION,
                "source:organization:observed",
                "Observed Semiconductor",
            ),
            (
                self.canonical_entity_id,
                EntityKind.ORGANIZATION,
                "gleif:lei:canonical",
                "Canonical Semiconductor LLC",
            ),
            (
                self.alternate_entity_id,
                EntityKind.ORGANIZATION,
                "gleif:lei:alternate",
                "Alternate Semiconductor LLC",
            ),
            (
                self.site_id,
                EntityKind.SITE,
                "source:site:not-an-organization",
                "Not an organization",
            ),
        ):
            add_entity(
                self.connection,
                Entity(
                    entity_id,
                    kind,
                    stable_key,
                    ENTITY_CREATED_AT,
                    display_name,
                    self.ingestion_run_id if entity_id == self.observed_entity_id else None,
                ),
            )
        return self._text_claim(
            self.observed_entity_id,
            "organization.name",
            "Observed Semiconductor",
            "name",
        )

    def _text_claim(
        self,
        entity_id: str,
        predicate: str,
        value: str,
        suffix: str,
        *,
        source_record_id: str | None = None,
        recorded_at: str = CLAIM_RECORDED_AT,
    ) -> str:
        series = ClaimSeries(
            stable_id("series", suffix),
            entity_id,
            f"identity-fixture:{suffix}",
            predicate,
            ValueKind.SCALAR,
            ENTITY_CREATED_AT,
        )
        add_claim_series(self.connection, series)
        version = ClaimVersion(
            stable_id("claim", suffix),
            series.id,
            "2026-01-01",
            recorded_at,
            ClaimKind.SOURCE_STATEMENT,
            "identity_fixture",
            1.0,
            created_by_run_id=self.ingestion_run_id,
        )
        insert_claim(
            self.connection,
            version,
            ScalarValue(ScalarType.TEXT, value),
            evidence=[
                EvidenceLink(
                    self.document_id,
                    source_record_id=source_record_id or self.source_record_id,
                    locator=suffix,
                    excerpt=value,
                )
            ],
        )
        return version.id

    def _resolution_run(self, suffix: str = "primary") -> str:
        run_id = stable_id("resolution-run", suffix)
        add_entity_resolution_run(
            self.connection,
            EntityResolutionRun(
                run_id,
                RESOLUTION_STARTED_AT,
                "identity-resolver-v1",
                code_version="test",
                parameters={"z": 2, "a": 1},
            ),
        )
        add_entity_resolution_run_input(
            self.connection,
            EntityResolutionRunInput(run_id, self.ingestion_run_id),
        )
        return run_id

    def _candidate(
        self,
        run_id: str,
        candidate_entity_id: str,
        suffix: str,
        rank: int,
    ) -> EntityResolutionCandidate:
        candidate = EntityResolutionCandidate(
            stable_id("resolution-candidate", suffix),
            run_id,
            self.source_record_id,
            self.observed_entity_id,
            candidate_entity_id,
            0.95,
            rank,
            CANDIDATE_CREATED_AT,
            features={"z_name": "Observed", "a_identifier": None},
        )
        add_entity_resolution_candidate(self.connection, candidate)
        return candidate

    def _decision(
        self,
        candidate: EntityResolutionCandidate,
        outcome: ResolutionDecisionOutcome,
        suffix: str,
        decided_at: str = DECIDED_AT,
    ) -> EntityResolutionDecision:
        decision = EntityResolutionDecision(
            stable_id("resolution-decision", suffix),
            candidate.id,
            outcome,
            decided_at,
            "reviewer:test",
            "Fixture decision",
            metadata={"z": True, "a": "reviewed"},
        )
        add_entity_resolution_decision(self.connection, decision)
        return decision

    def test_run_documents_are_generic_source_consistent_and_immutable(self) -> None:
        roles = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT role FROM ingestion_run_documents
                WHERE ingestion_run_id = ? AND source_document_id = ?
                """,
                (self.ingestion_run_id, self.document_id),
            )
        }
        self.assertEqual({"primary", "source_record"}, roles)
        link = IngestionRunDocument(
            self.ingestion_run_id,
            self.document_id,
            "identity_aliases",
        )
        self.assertTrue(add_ingestion_run_document(self.connection, link))
        self.assertFalse(add_ingestion_run_document(self.connection, link))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "run source"):
            add_ingestion_run_document(
                self.connection,
                IngestionRunDocument(
                    self.ingestion_run_id,
                    self.other_document_id,
                    "invalid",
                ),
            )
        invalid_run_id = stable_id("ingestion-run", "cross-source-primary")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "run source"):
            add_ingestion_run(
                self.connection,
                IngestionRun(
                    invalid_run_id,
                    self.source_id,
                    INGESTION_STARTED_AT,
                    status=IngestionStatus.SUCCEEDED,
                    completed_at=INGESTION_COMPLETED_AT,
                    input_document_id=self.other_document_id,
                ),
            )
        self.assertIsNone(
            self.connection.execute(
                "SELECT id FROM ingestion_runs WHERE id = ?",
                (invalid_run_id,),
            ).fetchone()
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.connection.execute(
                """
                UPDATE ingestion_run_documents SET role = 'changed'
                WHERE ingestion_run_id = ? AND source_document_id = ?
                  AND role = 'identity_aliases'
                """,
                (self.ingestion_run_id, self.document_id),
            )

    def test_organization_claim_metadata_is_typed_nonunique_and_immutable(self) -> None:
        name_metadata = OrganizationNameClaimMetadata(
            self.name_claim_id,
            OrganizationNameType.LEGAL,
            language_tag="en-US",
            script_code="latn",
        )
        self.assertEqual("Latn", name_metadata.script_code)
        self.assertTrue(
            add_organization_name_claim_metadata(self.connection, name_metadata)
        )
        self.assertFalse(
            add_organization_name_claim_metadata(self.connection, name_metadata)
        )
        identifier_claim = self._text_claim(
            self.observed_entity_id,
            "organization.identifier.lei",
            "529900T8BM49AURSDO55",
            "observed-lei",
        )
        self.assertTrue(
            add_organization_identifier_claim_metadata(
                self.connection,
                OrganizationIdentifierClaimMetadata(
                    identifier_claim,
                    "lei",
                    "529900T8BM49AURSDO55",
                    "US",
                ),
            )
        )
        duplicate_claim = self._text_claim(
            self.canonical_entity_id,
            "organization.identifier.lei",
            "529900T8BM49AURSDO55",
            "canonical-lei",
        )
        add_organization_identifier_claim_metadata(
            self.connection,
            OrganizationIdentifierClaimMetadata(
                duplicate_claim,
                "lei",
                "529900T8BM49AURSDO55",
                "US",
            ),
        )
        self.assertEqual(
            2,
            self.connection.execute(
                """
                SELECT COUNT(*) FROM organization_identifier_claim_metadata
                WHERE scheme = 'lei' AND normalized_value = '529900T8BM49AURSDO55'
                """
            ).fetchone()[0],
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "organization identifier"):
            add_organization_identifier_claim_metadata(
                self.connection,
                OrganizationIdentifierClaimMetadata(
                    self.name_claim_id,
                    "lei",
                    "529900T8BM49AURSDO55",
                ),
            )
        site_claim = self._text_claim(
            self.site_id,
            "name",
            "Not an organization",
            "site-name",
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "organization name"):
            add_organization_name_claim_metadata(
                self.connection,
                OrganizationNameClaimMetadata(site_claim, OrganizationNameType.OTHER),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.connection.execute(
                """
                UPDATE organization_name_claim_metadata SET name_type = 'other'
                WHERE claim_version_id = ?
                """,
                (self.name_claim_id,),
            )
        self.assertEqual([], validate_database(self.connection))

    def test_candidates_require_succeeded_input_and_direct_source_lineage(self) -> None:
        run_id = self._resolution_run()
        candidate = self._candidate(
            run_id,
            self.canonical_entity_id,
            "canonical",
            1,
        )
        self.assertFalse(add_entity_resolution_candidate(self.connection, candidate))
        stored = self.connection.execute(
            "SELECT features_json FROM entity_resolution_candidates WHERE id = ?",
            (candidate.id,),
        ).fetchone()[0]
        self.assertEqual(
            '{"a_identifier":null,"z_name":"Observed"}',
            stored,
        )
        run_parameters = self.connection.execute(
            "SELECT parameters_json FROM entity_resolution_runs WHERE id = ?",
            (run_id,),
        ).fetchone()[0]
        self.assertEqual('{"a":1,"z":2}', run_parameters)

        no_input_run = stable_id("resolution-run", "no-input")
        add_entity_resolution_run(
            self.connection,
            EntityResolutionRun(
                no_input_run,
                RESOLUTION_STARTED_AT,
                "identity-resolver-v1",
            ),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "source lineage"):
            add_entity_resolution_candidate(
                self.connection,
                EntityResolutionCandidate(
                    stable_id("resolution-candidate", "no-input"),
                    no_input_run,
                    self.source_record_id,
                    self.observed_entity_id,
                    self.canonical_entity_id,
                    0.5,
                    1,
                    CANDIDATE_CREATED_AT,
                ),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "source lineage"):
            add_entity_resolution_candidate(
                self.connection,
                EntityResolutionCandidate(
                    stable_id("resolution-candidate", "site-target"),
                    run_id,
                    self.source_record_id,
                    self.observed_entity_id,
                    self.site_id,
                    0.5,
                    2,
                    CANDIDATE_CREATED_AT,
                ),
            )
        future_entity_id = stable_id("entity", "future-resolution-candidate")
        add_entity(
            self.connection,
            Entity(
                future_entity_id,
                EntityKind.ORGANIZATION,
                "future-resolution-candidate",
                "2026-01-02T00:01:00Z",
                "Future candidate",
            ),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "source lineage"):
            self._candidate(run_id, future_entity_id, "future-entity", 3)
        self.assertEqual([], validate_database(self.connection))

    def test_facility_identity_is_same_kind_and_current_across_source_records(self) -> None:
        first_record_id = stable_id("source-record", "facility-identity-first")
        first_payload = {"emsno": "A0000001", "name": "Observed Fab"}
        add_source_record(
            self.connection,
            SourceRecord(
                first_record_id,
                self.ingestion_run_id,
                self.document_id,
                "facility:A0000001",
                SOURCE_RETRIEVED_AT,
                source_record_payload_sha256(first_payload),
                payload=first_payload,
            ),
        )
        observed_id = stable_id("entity", "source:facility:A0000001")
        canonical_id = stable_id("entity", "canonical:facility:fab-one")
        alternate_id = stable_id("entity", "canonical:facility:fab-two")
        for entity_id, stable_key, display_name, created_by_run_id in (
            (
                observed_id,
                "source:facility:A0000001",
                "Observed Fab",
                self.ingestion_run_id,
            ),
            (canonical_id, "canonical:facility:fab-one", "Canonical Fab One", None),
            (alternate_id, "canonical:facility:fab-two", "Canonical Fab Two", None),
        ):
            add_entity(
                self.connection,
                Entity(
                    entity_id,
                    EntityKind.FACILITY,
                    stable_key,
                    ENTITY_CREATED_AT,
                    display_name,
                    created_by_run_id,
                ),
            )
        self._text_claim(
            observed_id,
            "name",
            "Observed Fab",
            "facility-observed-name",
            source_record_id=first_record_id,
        )
        target_claim_id = self._text_claim(
            canonical_id,
            "name",
            "Canonical Fab One",
            "facility-canonical-name",
            source_record_id=first_record_id,
        )

        first_run_id = self._resolution_run("facility-identity-first")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "same-kind identity"):
            add_entity_resolution_candidate(
                self.connection,
                EntityResolutionCandidate(
                    stable_id("resolution-candidate", "facility-to-organization"),
                    first_run_id,
                    first_record_id,
                    observed_id,
                    self.canonical_entity_id,
                    0.5,
                    2,
                    CANDIDATE_CREATED_AT,
                ),
            )
        first_candidate = EntityResolutionCandidate(
            stable_id("resolution-candidate", "facility-identity-first"),
            first_run_id,
            first_record_id,
            observed_id,
            canonical_id,
            1.0,
            1,
            CANDIDATE_CREATED_AT,
            features={
                "target_evidence_claim_version_ids": [target_claim_id],
            },
        )
        self.assertTrue(
            add_entity_resolution_candidate(self.connection, first_candidate)
        )
        finalize_entity_resolution_run(
            self.connection,
            first_run_id,
            status=EntityResolutionStatus.SUCCEEDED,
            completed_at=RESOLUTION_COMPLETED_AT,
        )
        first_decision = self._decision(
            first_candidate,
            ResolutionDecisionOutcome.MATCH,
            "facility-identity-first",
        )
        first_assignment = SourceEntityAssignment(
            stable_id("assignment", "facility-identity-first"),
            first_record_id,
            observed_id,
            canonical_id,
            first_decision.id,
            "2026-01-01",
            ASSIGNED_AT,
        )
        self.assertTrue(
            add_source_entity_assignment(self.connection, first_assignment)
        )

        second_record_id = stable_id("source-record", "facility-identity-second")
        second_payload = {"emsno": "A0000001", "name": "Observed Fab Refresh"}
        add_source_record(
            self.connection,
            SourceRecord(
                second_record_id,
                self.ingestion_run_id,
                self.document_id,
                "facility:A0000001:refresh",
                SOURCE_RETRIEVED_AT,
                source_record_payload_sha256(second_payload),
                payload=second_payload,
            ),
        )
        self._text_claim(
            observed_id,
            "name",
            "Observed Fab Refresh",
            "facility-observed-name-refresh",
            source_record_id=second_record_id,
        )
        second_run_id = self._resolution_run("facility-identity-second")
        second_candidate = EntityResolutionCandidate(
            stable_id("resolution-candidate", "facility-identity-second"),
            second_run_id,
            second_record_id,
            observed_id,
            alternate_id,
            0.9,
            1,
            CANDIDATE_CREATED_AT,
        )
        add_entity_resolution_candidate(self.connection, second_candidate)
        finalize_entity_resolution_run(
            self.connection,
            second_run_id,
            status=EntityResolutionStatus.SUCCEEDED,
            completed_at=RESOLUTION_COMPLETED_AT,
        )
        second_decision = self._decision(
            second_candidate,
            ResolutionDecisionOutcome.MATCH,
            "facility-identity-second",
        )
        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "one observed entity must not overlap",
        ):
            add_source_entity_assignment(
                self.connection,
                SourceEntityAssignment(
                    stable_id("assignment", "facility-identity-second"),
                    second_record_id,
                    observed_id,
                    alternate_id,
                    second_decision.id,
                    "2026-01-01",
                    ASSIGNED_AT,
                ),
            )

        current = current_source_entity_assignments(
            self.connection,
            as_of="2026-01-01",
            recorded_at=ASSIGNED_AT,
            observed_entity_id=observed_id,
        )
        self.assertEqual([canonical_id], [row["canonical_entity_id"] for row in current])
        self.assertEqual([], validate_database(self.connection))

    def test_candidates_reject_evidence_recorded_after_the_run_cutoff(self) -> None:
        late_record_id = stable_id("source-record", "late-resolution-evidence")
        payload = {"name": "Late Evidence Semiconductor"}
        add_source_record(
            self.connection,
            SourceRecord(
                late_record_id,
                self.ingestion_run_id,
                self.document_id,
                "organization:late-evidence",
                SOURCE_RETRIEVED_AT,
                source_record_payload_sha256(payload),
                payload=payload,
            ),
        )
        late_observed_id = stable_id("entity", "source:organization:late-evidence")
        add_entity(
            self.connection,
            Entity(
                late_observed_id,
                EntityKind.ORGANIZATION,
                "source:organization:late-evidence",
                ENTITY_CREATED_AT,
                "Late Evidence Semiconductor",
                self.ingestion_run_id,
            ),
        )
        self._text_claim(
            late_observed_id,
            "organization.name",
            "Late Evidence Semiconductor",
            "late-evidence-name",
            source_record_id=late_record_id,
            recorded_at="2026-01-02T00:01:00Z",
        )
        run_id = self._resolution_run("late-evidence")

        with self.assertRaisesRegex(sqlite3.IntegrityError, "source lineage"):
            add_entity_resolution_candidate(
                self.connection,
                EntityResolutionCandidate(
                    stable_id("resolution-candidate", "late-evidence"),
                    run_id,
                    late_record_id,
                    late_observed_id,
                    self.canonical_entity_id,
                    0.9,
                    1,
                    CANDIDATE_CREATED_AT,
                ),
            )

    def test_candidate_target_evidence_rejects_claim_recorded_after_run_cutoff(
        self,
    ) -> None:
        run_id = self._resolution_run("late-target-evidence")
        late_claim_id = self._text_claim(
            self.canonical_entity_id,
            "organization.name",
            "Late target evidence",
            "late-target-evidence",
            recorded_at="2026-01-02T00:00:01Z",
        )
        candidate_id = stable_id(
            "resolution-candidate", "late-target-evidence"
        )

        with self.assertRaisesRegex(
            ValueError, "target evidence claim .* lacks cutoff-safe candidate lineage"
        ):
            add_entity_resolution_candidate(
                self.connection,
                EntityResolutionCandidate(
                    candidate_id,
                    run_id,
                    self.source_record_id,
                    self.observed_entity_id,
                    self.canonical_entity_id,
                    0.95,
                    1,
                    CANDIDATE_CREATED_AT,
                    features={
                        "target_evidence_claim_version_ids": [late_claim_id]
                    },
                ),
            )
        self.assertIsNone(
            self.connection.execute(
                "SELECT id FROM entity_resolution_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
        )

    def test_validator_flags_raw_candidate_with_post_cutoff_target_evidence(
        self,
    ) -> None:
        run_id = self._resolution_run("raw-late-target-evidence")
        late_claim_id = self._text_claim(
            self.canonical_entity_id,
            "organization.name",
            "Raw late target evidence",
            "raw-late-target-evidence",
            recorded_at="2026-01-02T00:00:01Z",
        )
        candidate_id = stable_id(
            "resolution-candidate", "raw-late-target-evidence"
        )
        features_json = json.dumps(
            {"target_evidence_claim_version_ids": [late_claim_id]},
            sort_keys=True,
            separators=(",", ":"),
        )
        self.connection.execute(
            """
            INSERT INTO entity_resolution_candidates(
                id, resolution_run_id, source_record_id, observed_entity_id,
                candidate_entity_id, features_json, score, candidate_rank,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                run_id,
                self.source_record_id,
                self.observed_entity_id,
                self.canonical_entity_id,
                features_json,
                0.95,
                1,
                CANDIDATE_CREATED_AT,
            ),
        )

        self.assertIn(
            f"entity resolution candidate {candidate_id} has invalid target evidence: "
            f"target evidence claim {late_claim_id} lacks cutoff-safe candidate lineage",
            validate_database(self.connection),
        )

    def test_candidates_reject_unparseable_cutoff_timestamps(self) -> None:
        run_id = self._resolution_run("invalid-cutoff-clock")
        invalid_entity_id = stable_id("entity", "invalid-cutoff-clock")
        self.connection.execute(
            """
            INSERT INTO entities(id, kind, stable_key, created_at, display_name)
            VALUES (?, 'organization', ?, 'not-a-time', 'Invalid clock target')
            """,
            (invalid_entity_id, "invalid-cutoff-clock"),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "source lineage"):
            self._candidate(run_id, invalid_entity_id, "invalid-cutoff-clock", 1)

        self.connection.execute("DROP TRIGGER source_records_immutable_update")
        self.connection.execute(
            "UPDATE source_records SET observed_at = 'not-a-time' WHERE id = ?",
            (self.source_record_id,),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "source lineage"):
            self._candidate(
                run_id,
                self.canonical_entity_id,
                "invalid-observation-clock",
                2,
            )

    def test_validator_flags_legacy_candidate_with_invalid_cutoff_clock(self) -> None:
        run_id = self._resolution_run("legacy-invalid-cutoff-clock")
        candidate = self._candidate(
            run_id,
            self.canonical_entity_id,
            "legacy-invalid-cutoff-clock",
            1,
        )
        self.connection.execute("DROP TRIGGER entities_immutable_update")
        self.connection.execute(
            "UPDATE entities SET created_at = 'not-a-time' WHERE id = ?",
            (self.canonical_entity_id,),
        )

        self.assertIn(
            f"entity resolution candidate {candidate.id} has invalid source lineage",
            validate_database(self.connection),
        )

    def test_resolution_clocks_and_candidate_ranks_are_database_invariants(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO entity_resolution_runs(
                    id, started_at, completed_at, status, resolver_version
                ) VALUES (?, ?, ?, 'succeeded', 'raw-test')
                """,
                ("malformed-clock-run", "not-a-time", "also-not-a-time"),
            )

        run_id = self._resolution_run("rank-invariant")
        self._candidate(
            run_id,
            self.canonical_entity_id,
            "rank-invariant-first",
            1,
        )
        with self.assertRaisesRegex(ValueError, "stable key already belongs"):
            self._candidate(
                run_id,
                self.alternate_entity_id,
                "rank-invariant-second",
                1,
            )

    def test_match_assignments_are_bitemporal_single_open_and_append_only(self) -> None:
        run_id = self._resolution_run()
        sealed_candidate_entity_id = stable_id(
            "entity", "gleif:lei:sealed-late-candidate"
        )
        add_entity(
            self.connection,
            Entity(
                sealed_candidate_entity_id,
                EntityKind.ORGANIZATION,
                "gleif:lei:sealed-late-candidate",
                ENTITY_CREATED_AT,
                "Sealed late candidate",
            ),
        )
        first_candidate = self._candidate(
            run_id,
            self.canonical_entity_id,
            "canonical",
            1,
        )
        second_candidate = self._candidate(
            run_id,
            self.alternate_entity_id,
            "alternate",
            2,
        )
        self.assertTrue(
            finalize_entity_resolution_run(
                self.connection,
                run_id,
                status=EntityResolutionStatus.SUCCEEDED,
                completed_at=RESOLUTION_COMPLETED_AT,
            )
        )
        self.assertFalse(
            finalize_entity_resolution_run(
                self.connection,
                run_id,
                status=EntityResolutionStatus.SUCCEEDED,
                completed_at=RESOLUTION_COMPLETED_AT,
            )
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "source lineage"):
            self._candidate(
                run_id,
                sealed_candidate_entity_id,
                "sealed-late-candidate",
                3,
            )
        first_decision = self._decision(
            first_candidate,
            ResolutionDecisionOutcome.MATCH,
            "canonical",
        )
        first_assignment = SourceEntityAssignment(
            stable_id("assignment", "canonical"),
            self.source_record_id,
            self.observed_entity_id,
            self.canonical_entity_id,
            first_decision.id,
            "2026-01-01",
            ASSIGNED_AT,
        )
        self.assertTrue(
            add_source_entity_assignment(self.connection, first_assignment)
        )
        self.assertFalse(
            add_source_entity_assignment(self.connection, first_assignment)
        )
        self.assertEqual(
            [],
            current_source_entity_assignments(
                self.connection,
                as_of="2026-01-01",
                recorded_at="2026-01-02T00:03:59Z",
            ),
        )
        self.assertEqual(
            [self.canonical_entity_id],
            [
                row["canonical_entity_id"]
                for row in current_source_entity_assignments(
                    self.connection,
                    as_of="2026-01-01",
                    recorded_at=ASSIGNED_AT,
                )
            ],
        )

        second_decision = self._decision(
            second_candidate,
            ResolutionDecisionOutcome.MATCH,
            "alternate",
            decided_at="2026-01-03T00:03:00Z",
        )
        second_assignment = SourceEntityAssignment(
            stable_id("assignment", "alternate"),
            self.source_record_id,
            self.observed_entity_id,
            self.alternate_entity_id,
            second_decision.id,
            "2026-01-01",
            "2026-01-03T00:04:00Z",
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "must not overlap"):
            add_source_entity_assignment(self.connection, second_assignment)

        superseded_at = "2026-01-03T00:04:00Z"
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                UPDATE source_entity_assignments SET superseded_at = 'not-a-time'
                WHERE id = ?
                """,
                (first_assignment.id,),
            )
        self.assertTrue(
            supersede_source_entity_assignment(
                self.connection,
                first_assignment.id,
                superseded_at,
            )
        )
        self.assertFalse(
            supersede_source_entity_assignment(
                self.connection,
                first_assignment.id,
                superseded_at,
            )
        )
        early_replacement = SourceEntityAssignment(
            second_assignment.id,
            second_assignment.source_record_id,
            second_assignment.observed_entity_id,
            second_assignment.canonical_entity_id,
            second_assignment.decision_id,
            second_assignment.valid_from,
            "2026-01-03T00:03:59Z",
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "must not overlap"):
            add_source_entity_assignment(self.connection, early_replacement)
        self.assertTrue(
            add_source_entity_assignment(self.connection, second_assignment)
        )
        self.assertEqual(
            [self.canonical_entity_id],
            [
                row["canonical_entity_id"]
                for row in current_source_entity_assignments(
                    self.connection,
                    as_of="2026-01-01",
                    recorded_at="2026-01-03T00:03:59Z",
                )
            ],
        )
        self.assertEqual(
            [self.alternate_entity_id],
            [
                row["canonical_entity_id"]
                for row in current_source_entity_assignments(
                    self.connection,
                    as_of="2026-01-01",
                    recorded_at=superseded_at,
                )
            ],
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "content is immutable"):
            self.connection.execute(
                """
                UPDATE source_entity_assignments SET canonical_entity_id = ?
                WHERE id = ?
                """,
                (self.canonical_entity_id, second_assignment.id),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.connection.execute(
                "DELETE FROM source_entity_assignments WHERE id = ?",
                (second_assignment.id,),
            )
        self.assertEqual([], validate_database(self.connection))

    def test_reject_and_defer_decisions_cannot_create_assignments(self) -> None:
        run_id = self._resolution_run()
        candidates = []
        for index, outcome in enumerate(
            (ResolutionDecisionOutcome.REJECT, ResolutionDecisionOutcome.DEFER),
            start=1,
        ):
            candidate_entity_id = (
                self.canonical_entity_id
                if outcome is ResolutionDecisionOutcome.REJECT
                else self.alternate_entity_id
            )
            candidate = self._candidate(
                run_id,
                candidate_entity_id,
                outcome.value,
                index,
            )
            candidates.append((outcome, candidate_entity_id, candidate))
        finalize_entity_resolution_run(
            self.connection,
            run_id,
            status=EntityResolutionStatus.SUCCEEDED,
            completed_at=RESOLUTION_COMPLETED_AT,
        )
        for outcome, candidate_entity_id, candidate in candidates:
            decision = self._decision(candidate, outcome, outcome.value)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "match decision"):
                add_source_entity_assignment(
                    self.connection,
                    SourceEntityAssignment(
                        stable_id("assignment", outcome.value),
                        self.source_record_id,
                        self.observed_entity_id,
                        candidate_entity_id,
                        decision.id,
                        "2026-01-01",
                        ASSIGNED_AT,
                    ),
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                self.connection.execute(
                    "UPDATE entity_resolution_decisions SET outcome = 'match' WHERE id = ?",
                    (decision.id,),
                )
        self.assertEqual([], validate_database(self.connection))

    def test_assignment_history_is_insertion_order_independent(self) -> None:
        run_id = self._resolution_run("reverse-history")
        historical_candidate = self._candidate(
            run_id,
            self.canonical_entity_id,
            "reverse-history-old",
            1,
        )
        current_candidate = self._candidate(
            run_id,
            self.alternate_entity_id,
            "reverse-history-current",
            2,
        )
        finalize_entity_resolution_run(
            self.connection,
            run_id,
            status=EntityResolutionStatus.SUCCEEDED,
            completed_at=RESOLUTION_COMPLETED_AT,
        )
        historical_decision = self._decision(
            historical_candidate,
            ResolutionDecisionOutcome.MATCH,
            "reverse-history-old",
        )
        current_decision = self._decision(
            current_candidate,
            ResolutionDecisionOutcome.MATCH,
            "reverse-history-current",
            decided_at="2026-01-03T00:03:00Z",
        )
        current = SourceEntityAssignment(
            stable_id("assignment", "reverse-history-current"),
            self.source_record_id,
            self.observed_entity_id,
            self.alternate_entity_id,
            current_decision.id,
            "2026-01-01",
            "2026-01-03T00:04:00Z",
        )
        historical = SourceEntityAssignment(
            stable_id("assignment", "reverse-history-old"),
            self.source_record_id,
            self.observed_entity_id,
            self.canonical_entity_id,
            historical_decision.id,
            "2026-01-01",
            ASSIGNED_AT,
            superseded_at="2026-01-03T00:04:00Z",
        )

        self.assertTrue(add_source_entity_assignment(self.connection, current))
        self.assertTrue(add_source_entity_assignment(self.connection, historical))
        self.assertEqual([], validate_database(self.connection))


if __name__ == "__main__":
    unittest.main()
