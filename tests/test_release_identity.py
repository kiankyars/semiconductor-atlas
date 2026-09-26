from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from tests._legacy_schema_fixture import historical_fixture

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
from semiconductor_atlas.release import FORMAT, write_release
from semiconductor_atlas.repository import (
    add_claim_series,
    add_entity,
    add_entity_resolution_candidate,
    add_entity_resolution_decision,
    add_entity_resolution_run,
    add_entity_resolution_run_input,
    add_ingestion_run,
    add_organization_identifier_claim_metadata,
    add_organization_name_claim_metadata,
    add_source,
    add_source_document,
    add_source_entity_assignment,
    add_source_family,
    add_source_record,
    finalize_entity_resolution_run,
    insert_claim,
    stable_id,
)


AS_OF = "2026-01-01"
SOURCE_RETRIEVED_AT = "2026-01-01T00:00:00Z"
INGESTION_STARTED_AT = "2026-01-01T00:01:00Z"
INGESTION_COMPLETED_AT = "2026-01-01T00:02:00Z"
ENTITY_CREATED_AT = "2026-01-01T00:03:00Z"
CLAIM_RECORDED_AT = "2026-01-01T00:04:00Z"
RESOLUTION_STARTED_AT = "2026-01-02T00:00:00Z"
CANDIDATE_CREATED_AT = "2026-01-02T00:01:00Z"
RESOLUTION_COMPLETED_AT = "2026-01-02T00:02:00Z"
DECIDED_AT = "2026-01-02T00:03:00Z"
ASSIGNED_AT = "2026-01-02T00:04:00Z"
RELEASE_CUTOFF = "2026-01-02T00:05:00Z"
SUPERSEDED_AT = "2026-01-03T00:00:00Z"

IDENTITY_FILES = (
    "organization_claim_metadata.jsonl",
    "entity_resolution_runs.jsonl",
    "entity_resolution_candidates.jsonl",
    "entity_resolution_decisions.jsonl",
    "source_entity_assignments.jsonl",
)


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class ReleaseIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.connection, _ = initialize(self.root / "atlas.sqlite", target_version=4)

        self.family_id = stable_id("source-family", "release-identity")
        self.source_id = stable_id("source", "release-identity")
        self.document_id = stable_id("document", "release-identity")
        self.ingestion_run_id = stable_id("ingestion-run", "release-identity")
        self.source_record_id = stable_id("source-record", "release-identity")
        self.observed_entity_id = stable_id(
            "entity", "source:organization:release-observed"
        )
        self.canonical_entity_id = stable_id(
            "entity", "gleif:lei:529900T8BM49AURSDO55"
        )
        self.resolution_run_id = stable_id("resolution-run", "release-identity")
        self.candidate_id = stable_id("resolution-candidate", "release-identity")
        self.decision_id = stable_id("resolution-decision", "release-identity")
        self.assignment_id = stable_id("source-assignment", "release-identity")
        self._seed_fixture()

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def _add_text_claim(
        self,
        *,
        suffix: str,
        predicate: str,
        value: str,
        recorded_at: str = CLAIM_RECORDED_AT,
        valid_from: str = AS_OF,
        subject_entity_id: str | None = None,
    ) -> str:
        subject_entity_id = subject_entity_id or self.observed_entity_id
        series = ClaimSeries(
            stable_id("claim-series", "release-identity", suffix),
            subject_entity_id,
            f"release-identity:{suffix}",
            predicate,
            ValueKind.SCALAR,
            ENTITY_CREATED_AT,
        )
        add_claim_series(self.connection, series)
        claim = ClaimVersion(
            stable_id("claim-version", "release-identity", suffix),
            series.id,
            valid_from,
            recorded_at,
            ClaimKind.SOURCE_STATEMENT,
            "release_identity_fixture",
            1.0,
            created_by_run_id=self.ingestion_run_id,
        )
        insert_claim(
            self.connection,
            claim,
            ScalarValue(ScalarType.TEXT, value),
            evidence=[
                EvidenceLink(
                    self.document_id,
                    source_record_id=self.source_record_id,
                    locator=suffix,
                    excerpt=value,
                )
            ],
        )
        return claim.id

    def _seed_fixture(self) -> None:
        add_source_family(
            self.connection,
            SourceFamily(
                self.family_id,
                "release-identity",
                "Release identity fixture",
                SOURCE_RETRIEVED_AT,
            ),
        )
        add_source(
            self.connection,
            Source(
                self.source_id,
                self.family_id,
                "release-identity",
                "Release identity fixture",
                "Fixture publisher",
                "https://example.com/release-identity",
                SOURCE_RETRIEVED_AT,
            ),
        )
        add_source_document(
            self.connection,
            SourceDocument(
                self.document_id,
                self.source_id,
                "https://example.com/release-identity/record",
                "Release identity record",
                SOURCE_RETRIEVED_AT,
                "a" * 64,
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
                code_version="release-identity-fixture-v1",
                input_document_id=self.document_id,
                parameters={
                    "acceptance_timestamp_basis": "explicit_operator_supplied"
                },
            ),
        )
        payload = {"lei": "529900T8BM49AURSDO55", "name": "Observed Semiconductor"}
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
        add_entity(
            self.connection,
            Entity(
                self.observed_entity_id,
                EntityKind.ORGANIZATION,
                "source:organization:release-observed",
                ENTITY_CREATED_AT,
                "Observed Semiconductor",
                self.ingestion_run_id,
            ),
        )
        add_entity(
            self.connection,
            Entity(
                self.canonical_entity_id,
                EntityKind.ORGANIZATION,
                "gleif:lei:529900T8BM49AURSDO55",
                ENTITY_CREATED_AT,
                "Canonical Semiconductor LLC",
            ),
        )

        name_claim_id = self._add_text_claim(
            suffix="legal-name",
            predicate="organization.name",
            value="Observed Semiconductor",
        )
        identifier_claim_id = self._add_text_claim(
            suffix="lei",
            predicate="organization.identifier.lei",
            value="529900T8BM49AURSDO55",
        )
        future_name_claim_id = self._add_text_claim(
            suffix="future-alias",
            predicate="organization.name",
            value="Future Alias",
            recorded_at="2026-01-04T00:00:00Z",
        )
        self.target_evidence_claim_id = self._add_text_claim(
            suffix="canonical-future-world-name",
            predicate="organization.name",
            value="Canonical Semiconductor LLC",
            valid_from="2099-01-01",
            subject_entity_id=self.canonical_entity_id,
        )
        add_organization_name_claim_metadata(
            self.connection,
            OrganizationNameClaimMetadata(
                name_claim_id,
                OrganizationNameType.LEGAL,
                language_tag="en",
                script_code="Latn",
            ),
        )
        add_organization_identifier_claim_metadata(
            self.connection,
            OrganizationIdentifierClaimMetadata(
                identifier_claim_id,
                "lei",
                "529900T8BM49AURSDO55",
                "US",
            ),
        )
        add_organization_name_claim_metadata(
            self.connection,
            OrganizationNameClaimMetadata(
                future_name_claim_id,
                OrganizationNameType.OTHER,
            ),
        )

        add_entity_resolution_run(
            self.connection,
            EntityResolutionRun(
                self.resolution_run_id,
                RESOLUTION_STARTED_AT,
                "release-resolver-v1",
                code_version="fixture-code-v1",
                parameters={"weights": {"lei": 1.0}, "threshold": 0.9},
            ),
        )
        add_entity_resolution_run_input(
            self.connection,
            EntityResolutionRunInput(
                self.resolution_run_id,
                self.ingestion_run_id,
            ),
        )
        add_entity_resolution_candidate(
            self.connection,
            EntityResolutionCandidate(
                self.candidate_id,
                self.resolution_run_id,
                self.source_record_id,
                self.observed_entity_id,
                self.canonical_entity_id,
                0.99,
                1,
                CANDIDATE_CREATED_AT,
                features={
                    "name_exact": True,
                    "lei_exact": True,
                    "target_evidence_claim_version_ids": [
                        self.target_evidence_claim_id
                    ],
                },
            ),
        )
        finalize_entity_resolution_run(
            self.connection,
            self.resolution_run_id,
            status=EntityResolutionStatus.SUCCEEDED,
            completed_at=RESOLUTION_COMPLETED_AT,
        )
        add_entity_resolution_decision(
            self.connection,
            EntityResolutionDecision(
                self.decision_id,
                self.candidate_id,
                ResolutionDecisionOutcome.MATCH,
                DECIDED_AT,
                "reviewer:fixture",
                "Exact LEI match",
                metadata={"reviewed": True},
            ),
        )
        add_source_entity_assignment(
            self.connection,
            SourceEntityAssignment(
                self.assignment_id,
                self.source_record_id,
                self.observed_entity_id,
                self.canonical_entity_id,
                self.decision_id,
                AS_OF,
                ASSIGNED_AT,
                superseded_at=SUPERSEDED_AT,
            ),
        )

    def test_schema_v4_release_exports_cutoff_correct_identity_provenance(self) -> None:
        first_output = self.root / "release-first"
        second_output = self.root / "release-second"

        manifest = write_release(
            self.connection,
            first_output,
            as_of=AS_OF,
            recorded_at=RELEASE_CUTOFF,
        )
        second_manifest = write_release(
            self.connection,
            second_output,
            as_of=AS_OF,
            recorded_at=RELEASE_CUTOFF,
        )

        self.assertEqual(manifest, second_manifest)
        self.assertEqual(4, manifest["schema_version"])
        self.assertEqual(
            {
                "organization_claim_metadata": 2,
                "entity_resolution_runs": 1,
                "entity_resolution_candidates": 1,
                "entity_resolution_decisions": 1,
                "source_entity_assignments": 1,
            },
            {
                name.removesuffix(".jsonl"): manifest[name.removesuffix(".jsonl")]
                for name in IDENTITY_FILES
            },
        )
        for name in IDENTITY_FILES:
            self.assertIn(name, manifest["files"])
            self.assertEqual(
                (first_output / name).read_bytes(),
                (second_output / name).read_bytes(),
            )
        self.assertEqual(
            (first_output / "manifest.json").read_bytes(),
            (second_output / "manifest.json").read_bytes(),
        )

        metadata = _jsonl(first_output / "organization_claim_metadata.jsonl")
        self.assertEqual(2, len(metadata))
        self.assertEqual(
            {"identifier", "name"},
            {row["metadata_kind"] for row in metadata},
        )
        name_metadata = next(row for row in metadata if row["metadata_kind"] == "name")
        self.assertEqual("legal", name_metadata["name_type"])
        self.assertEqual("en", name_metadata["language_tag"])
        self.assertEqual("Latn", name_metadata["script_code"])
        identifier_metadata = next(
            row for row in metadata if row["metadata_kind"] == "identifier"
        )
        self.assertEqual("lei", identifier_metadata["scheme"])
        self.assertEqual(
            "529900T8BM49AURSDO55", identifier_metadata["normalized_value"]
        )
        self.assertEqual("US", identifier_metadata["jurisdiction"])

        resolution_run = _jsonl(first_output / "entity_resolution_runs.jsonl")[0]
        self.assertEqual(self.resolution_run_id, resolution_run["id"])
        self.assertEqual(
            [self.ingestion_run_id], resolution_run["input_ingestion_run_ids"]
        )
        self.assertEqual(["organization_identity"], resolution_run["identity_scopes"])
        self.assertEqual(
            {"threshold": 0.9, "weights": {"lei": 1.0}},
            resolution_run["parameters"],
        )

        candidate = _jsonl(first_output / "entity_resolution_candidates.jsonl")[0]
        self.assertEqual("organization:observed", candidate["source_record_key"])
        self.assertEqual(
            "source:organization:release-observed",
            candidate["observed_entity_stable_key"],
        )
        self.assertEqual("organization", candidate["observed_entity_kind"])
        self.assertEqual(
            "gleif:lei:529900T8BM49AURSDO55",
            candidate["candidate_entity_stable_key"],
        )
        self.assertEqual("organization", candidate["candidate_entity_kind"])
        self.assertEqual("organization_identity", candidate["identity_scope"])
        self.assertEqual(
            {
                "lei_exact": True,
                "name_exact": True,
                "target_evidence_claim_version_ids": [
                    self.target_evidence_claim_id
                ],
            },
            candidate["features"],
        )
        claim_history_ids = {
            row["id"] for row in _jsonl(first_output / "claim_history.jsonl")
        }
        self.assertIn(self.target_evidence_claim_id, claim_history_ids)

        decision = _jsonl(first_output / "entity_resolution_decisions.jsonl")[0]
        self.assertEqual(self.resolution_run_id, decision["resolution_run_id"])
        self.assertEqual("organization", decision["observed_entity_kind"])
        self.assertEqual("organization", decision["candidate_entity_kind"])
        self.assertEqual("organization_identity", decision["identity_scope"])
        self.assertEqual({"reviewed": True}, decision["metadata"])

        assignment = _jsonl(first_output / "source_entity_assignments.jsonl")[0]
        self.assertEqual(self.canonical_entity_id, assignment["canonical_entity_id"])
        self.assertEqual("organization", assignment["observed_entity_kind"])
        self.assertEqual("organization", assignment["canonical_entity_kind"])
        self.assertEqual("organization_identity", assignment["identity_scope"])
        self.assertIsNone(assignment["superseded_at"])
        entity_ids = {
            row["entity_id"] for row in _jsonl(first_output / "entities.jsonl")
        }
        self.assertIn(self.canonical_entity_id, entity_ids)

        source_inputs = json.loads(
            (first_output / "source_inputs.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            ["primary", "source_record"],
            source_inputs[0]["processing_runs"][0]["document_roles"],
        )
        readme = (first_output / "README.md").read_text(encoding="utf-8")
        for name in IDENTITY_FILES:
            self.assertIn(name, readme)
        self.assertIn("source-native organizations", readme)
        self.assertIn("facility-to-facility identity", readme)
        self.assertIn("facility-to-organization links", readme)
        self.assertIn("not evidence of facility ownership or operation", readme)
        coverage = json.loads(
            (first_output / "coverage.json").read_text(encoding="utf-8")
        )
        self.assertEqual(1, coverage["entity_namespace_counts"]["gleif"])
        self.assertTrue(
            any(
                "1 explicitly reviewed exact-LEI source record" in gap
                for gap in coverage["known_gaps"]
            )
        )

    def test_schema_v4_identity_exports_obey_knowledge_cutoffs(self) -> None:
        before_completion = self.root / "before-completion"
        write_release(
            self.connection,
            before_completion,
            as_of=AS_OF,
            recorded_at="2026-01-02T00:01:30Z",
        )

        self.assertEqual(
            2,
            len(_jsonl(before_completion / "organization_claim_metadata.jsonl")),
        )
        for name in IDENTITY_FILES[1:]:
            self.assertEqual([], _jsonl(before_completion / name))
        early_manifest = json.loads(
            (before_completion / "manifest.json").read_text(encoding="utf-8")
        )
        for name in IDENTITY_FILES[1:]:
            self.assertEqual(0, early_manifest[name.removesuffix(".jsonl")])

        after_supersession = self.root / "after-supersession"
        write_release(
            self.connection,
            after_supersession,
            as_of=AS_OF,
            recorded_at=SUPERSEDED_AT,
        )
        assignment = _jsonl(
            after_supersession / "source_entity_assignments.jsonl"
        )[0]
        self.assertEqual(SUPERSEDED_AT, assignment["superseded_at"])

    def test_schema_v4_release_exports_facility_identity_scope(self) -> None:
        record_id = stable_id("source-record", "release-facility-identity")
        payload = {"factory_id": "F-001", "name": "Source Fab"}
        add_source_record(
            self.connection,
            SourceRecord(
                record_id,
                self.ingestion_run_id,
                self.document_id,
                "facility:F-001",
                SOURCE_RETRIEVED_AT,
                source_record_payload_sha256(payload),
                payload=payload,
            ),
        )
        observed_id = stable_id("entity", "source:facility:F-001")
        canonical_id = stable_id("entity", "canonical:facility:F-001")
        add_entity(
            self.connection,
            Entity(
                observed_id,
                EntityKind.FACILITY,
                "source:facility:F-001",
                ENTITY_CREATED_AT,
                "Source Fab",
                self.ingestion_run_id,
            ),
        )
        add_entity(
            self.connection,
            Entity(
                canonical_id,
                EntityKind.FACILITY,
                "canonical:facility:F-001",
                ENTITY_CREATED_AT,
                "Canonical Fab",
            ),
        )
        series = ClaimSeries(
            stable_id("claim-series", "release-facility-identity"),
            observed_id,
            "release-facility-identity:name",
            "name",
            ValueKind.SCALAR,
            ENTITY_CREATED_AT,
        )
        add_claim_series(self.connection, series)
        insert_claim(
            self.connection,
            ClaimVersion(
                stable_id("claim-version", "release-facility-identity"),
                series.id,
                AS_OF,
                CLAIM_RECORDED_AT,
                ClaimKind.SOURCE_STATEMENT,
                "release_facility_identity_fixture",
                1.0,
                created_by_run_id=self.ingestion_run_id,
            ),
            ScalarValue(ScalarType.TEXT, "Source Fab"),
            evidence=(
                EvidenceLink(
                    self.document_id,
                    source_record_id=record_id,
                    locator="name",
                    excerpt="Source Fab",
                ),
            ),
        )
        run_id = stable_id("resolution-run", "release-facility-identity")
        candidate_id = stable_id(
            "resolution-candidate", "release-facility-identity"
        )
        decision_id = stable_id("resolution-decision", "release-facility-identity")
        assignment_id = stable_id("source-assignment", "release-facility-identity")
        add_entity_resolution_run(
            self.connection,
            EntityResolutionRun(
                run_id,
                RESOLUTION_STARTED_AT,
                "release-facility-resolver-v1",
            ),
        )
        add_entity_resolution_run_input(
            self.connection,
            EntityResolutionRunInput(run_id, self.ingestion_run_id),
        )
        add_entity_resolution_candidate(
            self.connection,
            EntityResolutionCandidate(
                candidate_id,
                run_id,
                record_id,
                observed_id,
                canonical_id,
                1.0,
                1,
                CANDIDATE_CREATED_AT,
                features={"exact_factory_id": True},
            ),
        )
        finalize_entity_resolution_run(
            self.connection,
            run_id,
            status=EntityResolutionStatus.SUCCEEDED,
            completed_at=RESOLUTION_COMPLETED_AT,
        )
        add_entity_resolution_decision(
            self.connection,
            EntityResolutionDecision(
                decision_id,
                candidate_id,
                ResolutionDecisionOutcome.MATCH,
                DECIDED_AT,
                "reviewer:fixture",
                "Exact factory identifier",
            ),
        )
        add_source_entity_assignment(
            self.connection,
            SourceEntityAssignment(
                assignment_id,
                record_id,
                observed_id,
                canonical_id,
                decision_id,
                AS_OF,
                ASSIGNED_AT,
            ),
        )

        output = self.root / "facility-identity-release"
        manifest = write_release(
            self.connection,
            output,
            as_of=AS_OF,
            recorded_at=RELEASE_CUTOFF,
        )

        self.assertEqual(4, manifest["schema_version"])
        run = next(
            row
            for row in _jsonl(output / "entity_resolution_runs.jsonl")
            if row["id"] == run_id
        )
        self.assertEqual(["facility_identity"], run["identity_scopes"])
        candidate = next(
            row
            for row in _jsonl(output / "entity_resolution_candidates.jsonl")
            if row["id"] == candidate_id
        )
        self.assertEqual("facility", candidate["observed_entity_kind"])
        self.assertEqual("facility", candidate["candidate_entity_kind"])
        self.assertEqual("facility_identity", candidate["identity_scope"])
        decision = next(
            row
            for row in _jsonl(output / "entity_resolution_decisions.jsonl")
            if row["id"] == decision_id
        )
        self.assertEqual("facility_identity", decision["identity_scope"])
        assignment = next(
            row
            for row in _jsonl(output / "source_entity_assignments.jsonl")
            if row["id"] == assignment_id
        )
        self.assertEqual("facility", assignment["observed_entity_kind"])
        self.assertEqual("facility", assignment["canonical_entity_kind"])
        self.assertEqual("facility_identity", assignment["identity_scope"])

    def test_schema_v2_release_keeps_the_legacy_artifact_shape(self) -> None:
        legacy = historical_fixture(self.connection, 2)
        self.connection.close()
        self.connection = legacy

        output = self.root / "schema-v2-release"
        manifest = write_release(
            self.connection,
            output,
            as_of=AS_OF,
            recorded_at=RELEASE_CUTOFF,
        )

        self.assertEqual(FORMAT, manifest["format"])
        self.assertEqual(
            {
                "format",
                "as_of",
                "recorded_at",
                "entities",
                "claims",
                "claim_history",
                "evidence_links",
                "capacity_claims",
                "source_documents",
                "source_observations",
                "files",
            },
            set(manifest),
        )
        for name in IDENTITY_FILES:
            self.assertNotIn(name, manifest["files"])
            self.assertFalse((output / name).exists())
        source_inputs = json.loads(
            (output / "source_inputs.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("document_roles", source_inputs[0]["processing_runs"][0])
        readme = (output / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("organization_claim_metadata.jsonl", readme)


if __name__ == "__main__":
    unittest.main()
