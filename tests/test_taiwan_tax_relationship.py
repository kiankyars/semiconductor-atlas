from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from semiconductor_atlas import taiwan_tax_relationship as relationship
from semiconductor_atlas.database import initialize
from semiconductor_atlas.models import (
    ClaimKind,
    ClaimSeries,
    ClaimVersion,
    DependencyLink,
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
    ResolutionDecisionOutcome,
    RelationshipValue,
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
    add_source,
    add_source_document,
    add_source_entity_assignment,
    add_source_family,
    add_source_record,
    finalize_entity_resolution_run,
    insert_claim,
    stable_id,
)
from semiconductor_atlas.release import write_release
from semiconductor_atlas.taiwan_tax_relationship import (
    TaiwanTaxRelationshipReviewDecision,
    accept_taiwan_tax_relationship_review,
    build_taiwan_tax_relationship_review,
    parse_taiwan_tax_relationship_candidate_bytes,
    parse_taiwan_tax_relationship_review_bytes,
    propose_taiwan_tax_relationship_candidates,
    read_taiwan_tax_relationship_candidate_file,
)


BASE = "2026-01-01T00:00:00Z"
CUTOFF = "2026-01-03T00:00:00Z"
REVIEWED = "2026-01-03T01:00:00Z"
ACCEPTED = "2026-01-03T02:00:00Z"


class TaiwanTaxRelationshipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "atlas.sqlite"
        self.connection, _ = initialize(self.database_path)
        self.moenv_snapshot = SimpleNamespace(
            root=Path(self.temporary_directory.name) / "moenv",
            manifest_sha256="1" * 64,
            candidate_sha256="2" * 64,
            raw_sha256="3" * 64,
            retrieved_at="2026-01-02T00:00:00Z",
        )
        self.factory_snapshot = SimpleNamespace(
            root=Path(self.temporary_directory.name) / "factory",
            manifest_sha256="4" * 64,
            candidate_sha256="5" * 64,
            raw_sha256="6" * 64,
            retrieved_at="2026-01-02T00:00:00Z",
        )
        self.mof_snapshot = SimpleNamespace(
            root=Path(self.temporary_directory.name) / "mof",
            manifest_sha256="7" * 64,
            matched_sha256="8" * 64,
            raw_sha256="9" * 64,
            retrieved_at="2026-01-02T00:00:00Z",
        )
        self.snapshot_patch = patch.multiple(
            relationship,
            verify_moenv_snapshot=lambda _root: self.moenv_snapshot,
            verify_taiwan_factory_snapshot=lambda _root: self.factory_snapshot,
            verify_taiwan_mof_snapshot=lambda _root: self.mof_snapshot,
            verify_taiwan_mof_allowlist_sources=(
                lambda _root, _moenv, _factory: self.mof_snapshot
            ),
        )
        self.snapshot_patch.start()
        self._seed()

    def tearDown(self) -> None:
        self.snapshot_patch.stop()
        self.connection.close()
        self.temporary_directory.cleanup()

    def _source_layer(
        self,
        *,
        source_key: str,
        manifest_sha256: str,
        data_sha256: str,
        raw_sha256: str,
        suffix: str,
        started_at: str,
        completed_at: str,
    ) -> tuple[str, str, str]:
        family_key = f"fixture-family:{source_key}"
        family_id = stable_id("source-family", family_key)
        source_id = stable_id("source", source_key)
        document_id = stable_id("source-document", source_key, suffix)
        run_id = stable_id("ingestion-run", source_key, suffix)
        add_source_family(
            self.connection,
            SourceFamily(family_id, family_key, family_key, BASE),
        )
        add_source(
            self.connection,
            Source(
                source_id,
                family_id,
                source_key,
                source_key,
                "Fixture publisher",
                f"https://example.com/{suffix}",
                BASE,
            ),
        )
        add_source_document(
            self.connection,
            SourceDocument(
                document_id,
                source_id,
                f"https://example.com/{suffix}.jsonl",
                suffix,
                started_at,
                data_sha256,
            ),
        )
        add_ingestion_run(
            self.connection,
            IngestionRun(
                run_id,
                source_id,
                started_at,
                status=IngestionStatus.SUCCEEDED,
                completed_at=completed_at,
                code_version="fixture-v1",
                input_document_id=document_id,
                parameters={
                    "manifest_sha256": manifest_sha256,
                    (
                        "matched_derivative"
                        if source_key == relationship.MOF_SOURCE_KEY
                        else "candidate_derivative"
                    ): {"sha256": data_sha256},
                    "raw_archive": {"sha256": raw_sha256},
                },
            ),
        )
        return source_id, document_id, run_id

    def _entity_record(
        self,
        *,
        run_id: str,
        document_id: str,
        stable_key: str,
        kind: EntityKind,
    ) -> tuple[str, str]:
        record_id = stable_id("source-record", run_id, stable_key)
        entity_id = stable_id("entity", stable_key)
        payload = {"stable_key": stable_key}
        started_at = self.connection.execute(
            "SELECT started_at FROM ingestion_runs WHERE id = ?", (run_id,)
        ).fetchone()[0]
        add_source_record(
            self.connection,
            SourceRecord(
                record_id,
                run_id,
                document_id,
                stable_key,
                started_at,
                source_record_payload_sha256(payload),
                payload=payload,
            ),
        )
        add_entity(
            self.connection,
            Entity(
                entity_id,
                kind,
                stable_key,
                started_at,
                stable_key,
                run_id,
            ),
        )
        return record_id, entity_id

    def _claim(
        self,
        *,
        run_id: str,
        document_id: str,
        record_id: str,
        entity_id: str,
        predicate: str,
        value: str,
        suffix: str,
        valid_from: str = "2026-01-01",
        valid_to: str | None = None,
    ) -> str:
        recorded_at = self.connection.execute(
            "SELECT started_at FROM ingestion_runs WHERE id = ?", (run_id,)
        ).fetchone()[0]
        series_id = stable_id("claim-series", suffix)
        add_claim_series(
            self.connection,
            ClaimSeries(
                series_id,
                entity_id,
                f"fixture:{suffix}",
                predicate,
                ValueKind.SCALAR,
                recorded_at,
            ),
        )
        claim_id = stable_id("claim-version", suffix)
        insert_claim(
            self.connection,
            ClaimVersion(
                claim_id,
                series_id,
                valid_from,
                recorded_at,
                ClaimKind.SOURCE_STATEMENT,
                "fixture_exact_capture_v1",
                1.0,
                valid_to=valid_to,
                created_by_run_id=run_id,
            ),
            ScalarValue(ScalarType.TEXT, value),
            evidence=[
                EvidenceLink(
                    document_id,
                    source_record_id=record_id,
                    locator=predicate,
                    excerpt=value,
                )
            ],
        )
        return claim_id

    def _source_entity(
        self,
        *,
        run_id: str,
        document_id: str,
        stable_key: str,
        kind: EntityKind,
        predicate: str,
        ubns: tuple[str, ...],
        valid_from: str = "2026-01-01",
        valid_to: str | None = None,
    ) -> tuple[str, str, tuple[str, ...]]:
        record_id, entity_id = self._entity_record(
            run_id=run_id,
            document_id=document_id,
            stable_key=stable_key,
            kind=kind,
        )
        claims = tuple(
            self._claim(
                run_id=run_id,
                document_id=document_id,
                record_id=record_id,
                entity_id=entity_id,
                predicate=predicate,
                value=ubn,
                suffix=f"{stable_key}:ubn:{index}",
                valid_from=valid_from,
                valid_to=valid_to,
            )
            for index, ubn in enumerate(ubns)
        )
        return record_id, entity_id, claims

    def _assignment(
        self,
        *,
        observed_record_id: str,
        observed_entity_id: str,
        canonical_entity_id: str,
        input_run_id: str,
        suffix: str,
        valid_from: str = "2026-01-01",
    ) -> str:
        run_id = stable_id("resolution-run", suffix)
        candidate_id = stable_id("resolution-candidate", suffix)
        decision_id = stable_id("resolution-decision", suffix)
        assignment_id = stable_id("source-entity-assignment", suffix)
        add_entity_resolution_run(
            self.connection,
            EntityResolutionRun(
                run_id,
                "2026-01-02T01:00:00Z",
                "fixture-resolution-v1",
            ),
        )
        add_entity_resolution_run_input(
            self.connection, EntityResolutionRunInput(run_id, input_run_id)
        )
        add_entity_resolution_candidate(
            self.connection,
            EntityResolutionCandidate(
                candidate_id,
                run_id,
                observed_record_id,
                observed_entity_id,
                canonical_entity_id,
                1.0,
                1,
                "2026-01-02T01:00:00Z",
            ),
        )
        finalize_entity_resolution_run(
            self.connection,
            run_id,
            status=EntityResolutionStatus.SUCCEEDED,
            completed_at="2026-01-02T01:00:01Z",
        )
        add_entity_resolution_decision(
            self.connection,
            EntityResolutionDecision(
                decision_id,
                candidate_id,
                ResolutionDecisionOutcome.MATCH,
                "2026-01-02T01:00:02Z",
                "reviewer:fixture",
                "exact fixture identity",
            ),
        )
        add_source_entity_assignment(
            self.connection,
            SourceEntityAssignment(
                assignment_id,
                observed_record_id,
                observed_entity_id,
                canonical_entity_id,
                decision_id,
                valid_from,
                "2026-01-02T01:00:03Z",
            ),
        )
        return assignment_id

    def _seed(self) -> None:
        _, self.moenv_document_id, self.moenv_run_id = self._source_layer(
            source_key=relationship.MOENV_SOURCE_KEY,
            manifest_sha256=self.moenv_snapshot.manifest_sha256,
            data_sha256=self.moenv_snapshot.candidate_sha256,
            raw_sha256=self.moenv_snapshot.raw_sha256,
            suffix="moenv-selected",
            started_at="2026-01-02T00:01:00Z",
            completed_at="2026-01-02T00:01:01Z",
        )
        _, self.factory_document_id, self.factory_run_id = self._source_layer(
            source_key=relationship.FACTORY_SOURCE_KEY,
            manifest_sha256=self.factory_snapshot.manifest_sha256,
            data_sha256=self.factory_snapshot.candidate_sha256,
            raw_sha256=self.factory_snapshot.raw_sha256,
            suffix="factory-selected",
            started_at="2026-01-02T00:02:00Z",
            completed_at="2026-01-02T00:02:01Z",
        )
        _, self.mof_document_id, self.mof_run_id = self._source_layer(
            source_key=relationship.MOF_SOURCE_KEY,
            manifest_sha256=self.mof_snapshot.manifest_sha256,
            data_sha256=self.mof_snapshot.matched_sha256,
            raw_sha256=self.mof_snapshot.raw_sha256,
            suffix="mof-selected",
            started_at="2026-01-02T00:03:00Z",
            completed_at="2026-01-02T00:03:01Z",
        )

        self.f1_record, self.f1_entity, _ = self._source_entity(
            run_id=self.factory_run_id,
            document_id=self.factory_document_id,
            stable_key=f"{relationship.FACTORY_SOURCE_KEY}:F1",
            kind=EntityKind.FACILITY,
            predicate=relationship.FACTORY_UBN_PREDICATE,
            ubns=("11111111",),
        )
        self.m1_record, self.m1_entity, self.m1_claims = self._source_entity(
            run_id=self.moenv_run_id,
            document_id=self.moenv_document_id,
            stable_key=f"{relationship.MOENV_SOURCE_KEY}:M1",
            kind=EntityKind.FACILITY,
            predicate=relationship.MOENV_UBN_PREDICATE,
            ubns=("11111111",),
        )
        self.assignment1 = self._assignment(
            observed_record_id=self.m1_record,
            observed_entity_id=self.m1_entity,
            canonical_entity_id=self.f1_entity,
            input_run_id=self.moenv_run_id,
            suffix="m1-f1",
        )

        self.f2_record, self.f2_entity, _ = self._source_entity(
            run_id=self.factory_run_id,
            document_id=self.factory_document_id,
            stable_key=f"{relationship.FACTORY_SOURCE_KEY}:F2",
            kind=EntityKind.FACILITY,
            predicate=relationship.FACTORY_UBN_PREDICATE,
            ubns=("22222222",),
            valid_to="2026-12-31",
        )
        self.m2_record, self.m2_entity, _ = self._source_entity(
            run_id=self.moenv_run_id,
            document_id=self.moenv_document_id,
            stable_key=f"{relationship.MOENV_SOURCE_KEY}:M2",
            kind=EntityKind.FACILITY,
            predicate=relationship.MOENV_UBN_PREDICATE,
            ubns=("33333333",),
        )
        self.assignment2 = self._assignment(
            observed_record_id=self.m2_record,
            observed_entity_id=self.m2_entity,
            canonical_entity_id=self.f2_entity,
            input_run_id=self.moenv_run_id,
            suffix="m2-f2",
            valid_from="2026-03-01",
        )
        self.m3_record, self.m3_entity, _ = self._source_entity(
            run_id=self.moenv_run_id,
            document_id=self.moenv_document_id,
            stable_key=f"{relationship.MOENV_SOURCE_KEY}:M3",
            kind=EntityKind.FACILITY,
            predicate=relationship.MOENV_UBN_PREDICATE,
            ubns=("44444444",),
        )
        self._source_entity(
            run_id=self.moenv_run_id,
            document_id=self.moenv_document_id,
            stable_key=f"{relationship.MOENV_SOURCE_KEY}:MALFORMED",
            kind=EntityKind.FACILITY,
            predicate=relationship.MOENV_UBN_PREDICATE,
            ubns=("1234-567",),
        )
        self._source_entity(
            run_id=self.moenv_run_id,
            document_id=self.moenv_document_id,
            stable_key=f"{relationship.MOENV_SOURCE_KEY}:CONFLICT",
            kind=EntityKind.FACILITY,
            predicate=relationship.MOENV_UBN_PREDICATE,
            ubns=("55555555", "66666666"),
        )

        self.tax_entities: dict[str, str] = {}
        for index, (ubn, valid_from) in enumerate(
            (
                ("11111111", "2026-01-01"),
                ("22222222", "2026-02-01"),
                ("33333333", "2026-01-01"),
                ("44444444", "2026-01-01"),
                ("55555555", "2026-01-01"),
                ("66666666", "2026-01-01"),
            )
        ):
            _record, entity, _claims = self._source_entity(
                run_id=self.mof_run_id,
                document_id=self.mof_document_id,
                stable_key=f"{relationship.MOF_SOURCE_KEY}:{ubn}",
                kind=EntityKind.ORGANIZATION,
                predicate=relationship.MOF_UBN_PREDICATE,
                ubns=(ubn,),
                valid_from=valid_from,
            )
            self.tax_entities[ubn] = entity
        self.connection.commit()

    def _proposal(self):
        return propose_taiwan_tax_relationship_candidates(
            self.connection,
            knowledge_cutoff_at=CUTOFF,
            moenv_ingestion_run_id=self.moenv_run_id,
            factory_ingestion_run_id=self.factory_run_id,
            mof_ingestion_run_id=self.mof_run_id,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            mof_snapshot=self.mof_snapshot,
        )

    def _review(self, proposal, *, match_ubns: frozenset[str]):
        decisions = []
        for candidate in proposal.candidates:
            match = candidate.unified_business_number in match_ubns
            decisions.append(
                TaiwanTaxRelationshipReviewDecision(
                    candidate.candidate_id,
                    "match" if match else "defer",
                    "reviewed fixture",
                    candidate.evidence_valid_from if match else None,
                    candidate.evidence_valid_to if match else None,
                )
            )
        return build_taiwan_tax_relationship_review(
            proposal,
            reviewed_by="reviewer:test",
            reviewed_at=REVIEWED,
            decisions=decisions,
        )

    def _accept(self, proposal, review, accepted_at=ACCEPTED):
        return accept_taiwan_tax_relationship_review(
            self.connection,
            candidate_artifact=proposal,
            review_artifact=review,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            mof_snapshot=self.mof_snapshot,
            accepted_at=accepted_at,
        )

    def test_proposal_deduplicates_and_preserves_conflicting_evidence(self) -> None:
        first = self._proposal()
        second = self._proposal()
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertEqual(4, len(first.candidates))
        by_ubn = {candidate.unified_business_number: candidate for candidate in first.candidates}
        self.assertEqual(
            {
                "11111111",
                "22222222",
                "33333333",
                "44444444",
            },
            set(by_ubn),
        )
        aggregated = by_ubn["11111111"]
        self.assertEqual(self.f1_entity, aggregated.subject_entity_id)
        self.assertEqual(
            "factory_native_with_assigned_moenv_provenance",
            aggregated.canonical_subject_rule,
        )
        self.assertEqual(
            {relationship.MOENV_SOURCE_KEY, relationship.FACTORY_SOURCE_KEY},
            {item.source_key for item in aggregated.facility_evidence},
        )
        self.assertEqual((self.assignment1,), tuple(item.assignment_id for item in aggregated.canonical_assignments))
        self.assertEqual(self.f2_entity, by_ubn["22222222"].subject_entity_id)
        self.assertEqual(self.f2_entity, by_ubn["33333333"].subject_entity_id)
        self.assertEqual(self.m3_entity, by_ubn["44444444"].subject_entity_id)
        self.assertEqual("2026-02-01", by_ubn["22222222"].evidence_valid_from)
        self.assertEqual("2026-03-01", by_ubn["33333333"].evidence_valid_from)

    def test_strict_artifacts_require_complete_bounded_review(self) -> None:
        proposal = self._proposal()
        payload = json.loads(proposal.canonical_bytes)
        payload["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "exactly"):
            parse_taiwan_tax_relationship_candidate_bytes(
                json.dumps(payload).encode()
            )
        duplicate_key = proposal.canonical_bytes.replace(
            b'{"candidates"', b'{"candidates":[],"candidates"', 1
        )
        with self.assertRaisesRegex(ValueError, "duplicate JSON"):
            parse_taiwan_tax_relationship_candidate_bytes(duplicate_key)
        review = self._review(proposal, match_ubns=frozenset())
        review_payload = json.loads(review.canonical_bytes)
        review_payload["decisions"].pop()
        with self.assertRaisesRegex(ValueError, "every candidate"):
            parse_taiwan_tax_relationship_review_bytes(
                json.dumps(review_payload).encode(), candidate_artifact=proposal
            )
        candidate_path = Path(self.temporary_directory.name) / "candidate.json"
        candidate_path.write_bytes(proposal.canonical_bytes)
        symlink = Path(self.temporary_directory.name) / "candidate-link.json"
        symlink.symlink_to(candidate_path)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            read_taiwan_tax_relationship_candidate_file(symlink)

    def test_accept_match_only_writes_explicit_reference_with_full_lineage(self) -> None:
        proposal = self._proposal()
        review = self._review(proposal, match_ubns=frozenset({"11111111"}))
        result = self._accept(proposal, review)
        self.assertFalse(result.replayed)
        self.assertEqual(1, len(result.relationship_claim_ids))
        row = self.connection.execute(
            """
            SELECT series.subject_entity_id, series.predicate,
                   relationships.object_entity_id, relationships.relationship_type,
                   relationships.attributes_json, versions.claim_kind,
                   versions.method, versions.valid_from, versions.valid_to
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            JOIN relationship_values AS relationships
              ON relationships.claim_version_id = versions.id
            WHERE versions.id = ?
            """,
            (result.relationship_claim_ids[0],),
        ).fetchone()
        self.assertEqual(self.f1_entity, row["subject_entity_id"])
        self.assertEqual(self.tax_entities["11111111"], row["object_entity_id"])
        self.assertEqual(
            relationship.TAIWAN_TAX_RELATIONSHIP_TYPE,
            row["relationship_type"],
        )
        attributes = json.loads(row["attributes_json"])
        self.assertFalse(attributes["asserts_identity"])
        self.assertFalse(attributes["asserts_ownership"])
        self.assertFalse(attributes["asserts_parentage"])
        self.assertFalse(attributes["asserts_operator_or_operation"])
        self.assertEqual("reconciled_fact", row["claim_kind"])
        evidence_count = self.connection.execute(
            "SELECT COUNT(*) FROM claim_evidence WHERE claim_version_id = ?",
            (result.relationship_claim_ids[0],),
        ).fetchone()[0]
        dependency_count = self.connection.execute(
            "SELECT COUNT(*) FROM claim_dependencies WHERE claim_version_id = ?",
            (result.relationship_claim_ids[0],),
        ).fetchone()[0]
        self.assertEqual(3, evidence_count)
        self.assertEqual(3, dependency_count)
        self.assertEqual(
            {relationship.TAIWAN_TAX_RELATIONSHIP_TYPE},
            {
                value[0]
                for value in self.connection.execute(
                    "SELECT relationship_type FROM relationship_values"
                )
            },
        )

    def test_accept_allows_multiple_support_fragments_on_bound_record(self) -> None:
        claim_id = self.m1_claims[0]
        locator = "variants[1].uniformno"
        excerpt = "11111111"
        evidence_id = stable_id(
            "claim-evidence",
            claim_id,
            self.moenv_document_id,
            self.m1_record,
            "support",
            locator,
            excerpt,
        )
        self.connection.execute(
            """
            INSERT INTO claim_evidence(
                id, claim_version_id, source_document_id, source_record_id,
                role, locator, excerpt
            ) VALUES (?, ?, ?, ?, 'support', ?, ?)
            """,
            (
                evidence_id,
                claim_id,
                self.moenv_document_id,
                self.m1_record,
                locator,
                excerpt,
            ),
        )
        self.connection.commit()
        self.assertEqual(
            2,
            self.connection.execute(
                """
                SELECT COUNT(*) FROM claim_evidence
                WHERE claim_version_id = ? AND source_document_id = ?
                  AND source_record_id = ?
                """,
                (claim_id, self.moenv_document_id, self.m1_record),
            ).fetchone()[0],
        )

        proposal = self._proposal()
        review = self._review(proposal, match_ubns=frozenset({"11111111"}))
        result = self._accept(proposal, review)

        self.assertEqual(1, len(result.relationship_claim_ids))
        self.assertEqual(
            1,
            self.connection.execute(
                """
                SELECT COUNT(*) FROM claim_dependencies
                WHERE claim_version_id = ? AND depends_on_claim_version_id = ?
                """,
                (result.relationship_claim_ids[0], claim_id),
            ).fetchone()[0],
        )

    def test_release_exports_review_run_lineage_and_bounded_semantics(self) -> None:
        proposal = self._proposal()
        review = self._review(proposal, match_ubns=frozenset({"11111111"}))
        result = self._accept(proposal, review)
        output = Path(self.temporary_directory.name) / "reviewed-release"

        manifest = write_release(
            self.connection,
            output,
            as_of="2026-01-03",
            recorded_at="2026-01-03T02:00:03Z",
        )

        self.assertEqual(1, manifest["reviewed_relationship_runs"])
        self.assertIn("reviewed_relationship_runs.jsonl", manifest["files"])
        rows = [
            json.loads(line)
            for line in (output / "reviewed_relationship_runs.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(1, len(rows))
        exported = rows[0]
        self.assertEqual(
            result.review_ingestion_run_id,
            exported["review_ingestion_run_id"],
        )
        self.assertEqual(
            relationship.REVIEW_SOURCE_FAMILY_KEY,
            exported["source_family"],
        )
        self.assertEqual(
            relationship.TAIWAN_TAX_RELATIONSHIP_REVIEW_VERSION,
            exported["code_version"],
        )
        parameters = exported["parameters"]
        stored_parameters = json.loads(
            self.connection.execute(
                "SELECT parameters_json FROM ingestion_runs WHERE id = ?",
                (result.review_ingestion_run_id,),
            ).fetchone()[0]
        )
        self.assertEqual(stored_parameters, parameters)
        self.assertEqual(proposal.raw_sha256, parameters["candidate_artifact"]["raw_sha256"])
        self.assertEqual(review.raw_sha256, parameters["review_artifact"]["raw_sha256"])
        self.assertEqual(4, len(parameters["decisions"]))
        self.assertEqual(1, len(parameters["created_claims"]))
        self.assertEqual(0, len(parameters["reaffirmed_claims"]))
        self.assertEqual(0, len(parameters["superseded_claim_ids"]))
        self.assertEqual(
            {
                "factory_registry",
                "moenv",
                "mof_tax_registry",
            },
            set(parameters["source_bindings"]),
        )

        coverage = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
        scope = coverage["taiwan_tax_relationship_review_scope"]
        self.assertEqual(result.review_ingestion_run_id, scope["review_ingestion_run_id"])
        self.assertEqual(4, scope["candidate_decision_count"])
        self.assertEqual(
            {"defer": 3, "match": 1, "reject": 0},
            scope["decision_counts"],
        )
        self.assertEqual(
            {"created": 1, "reaffirmed": 0, "superseded": 0},
            scope["lineage_action_counts"],
        )
        self.assertEqual(1, scope["current_relationship_claim_count"])
        self.assertEqual(1, scope["distinct_facility_subject_count"])
        self.assertEqual(1, scope["distinct_tax_unit_object_count"])
        self.assertEqual(
            0,
            scope["facility_subjects_with_multiple_current_tax_unit_references"],
        )
        self.assertFalse(scope["semantics"]["asserts_identity"])
        self.assertFalse(scope["semantics"]["asserts_ownership"])
        self.assertTrue(
            scope["semantics"]["allows_multiple_source_references_per_facility"]
        )
        self.assertTrue(
            any(
                "Multiple source references may coexist" in gap
                for gap in coverage["known_gaps"]
            )
        )
        readme = (output / "README.md").read_text(encoding="utf-8")
        self.assertIn("exact same-time UBN references only", readme)
        self.assertIn("reviewed_relationship_runs.jsonl", readme)
        self.assertIn("do not assert identity", readme)
        history_ids = {
            json.loads(line)["id"]
            for line in (output / "claim_history.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        }
        action_claim_ids = {
            descriptor["claim_id"]
            for field in ("created_claims", "reaffirmed_claims")
            for descriptor in parameters[field]
        } | set(parameters["superseded_claim_ids"])
        self.assertLessEqual(action_claim_ids, history_ids)

    def test_release_hides_review_until_all_transaction_effects_are_visible(self) -> None:
        proposal = self._proposal()
        review = self._review(proposal, match_ubns=frozenset({"11111111"}))
        self._accept(proposal, review)
        output = Path(self.temporary_directory.name) / "temporal-release"

        for recorded_at in (
            "2026-01-03T02:00:01Z",
            "2026-01-03T02:00:02Z",
        ):
            manifest = write_release(
                self.connection,
                output,
                as_of="2026-01-03",
                recorded_at=recorded_at,
            )
            self.assertNotIn("reviewed_relationship_runs", manifest)
            self.assertFalse((output / "reviewed_relationship_runs.jsonl").exists())
            coverage = json.loads(
                (output / "coverage.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("taiwan_tax_relationship_review_scope", coverage)

        manifest = write_release(
            self.connection,
            output,
            as_of="2026-01-03",
            recorded_at="2026-01-03T02:00:03Z",
        )
        self.assertEqual(1, manifest["reviewed_relationship_runs"])
        self.assertTrue((output / "reviewed_relationship_runs.jsonl").exists())
        coverage = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
        self.assertIn("taiwan_tax_relationship_review_scope", coverage)

    def test_coverage_counts_conflicting_current_references_per_facility(self) -> None:
        proposal = self._proposal()
        review = self._review(
            proposal,
            match_ubns=frozenset({"22222222", "33333333"}),
        )
        self._accept(proposal, review)
        output = Path(self.temporary_directory.name) / "conflict-release"

        write_release(
            self.connection,
            output,
            as_of="2026-03-01",
            recorded_at="2026-01-03T02:00:03Z",
        )

        coverage = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
        scope = coverage["taiwan_tax_relationship_review_scope"]
        self.assertEqual(2, scope["current_relationship_claim_count"])
        self.assertEqual(1, scope["distinct_facility_subject_count"])
        self.assertEqual(2, scope["distinct_tax_unit_object_count"])
        self.assertEqual(
            1,
            scope["facility_subjects_with_multiple_current_tax_unit_references"],
        )

    def test_coverage_excludes_other_methods_using_same_relationship_type(self) -> None:
        proposal = self._proposal()
        review = self._review(proposal, match_ubns=frozenset({"11111111"}))
        self._accept(proposal, review)
        series_id = stable_id("claim-series", "alternative-tax-reference")
        claim_id = stable_id("claim-version", "alternative-tax-reference")
        add_claim_series(
            self.connection,
            ClaimSeries(
                series_id,
                self.f1_entity,
                "fixture:alternative-tax-reference",
                relationship.TAIWAN_TAX_RELATIONSHIP_PREDICATE,
                ValueKind.RELATIONSHIP,
                "2026-01-03T02:00:03Z",
            ),
        )
        insert_claim(
            self.connection,
            ClaimVersion(
                claim_id,
                series_id,
                "2026-01-01",
                "2026-01-03T02:00:03Z",
                ClaimKind.RECONCILED_FACT,
                "fixture_alternative_tax_reference_v1",
                0.5,
                created_by_run_id=self.moenv_run_id,
            ),
            RelationshipValue(
                self.tax_entities["11111111"],
                relationship.TAIWAN_TAX_RELATIONSHIP_TYPE,
                {"fixture_alternative_method": True},
            ),
            dependencies=[DependencyLink(self.m1_claims[0])],
        )
        self.connection.commit()
        output = Path(self.temporary_directory.name) / "method-isolation-release"

        write_release(
            self.connection,
            output,
            as_of="2026-01-03",
            recorded_at="2026-01-03T02:00:03Z",
        )

        claims = [
            json.loads(line)
            for line in (output / "claims.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            2,
            sum(
                claim["predicate"] == relationship.TAIWAN_TAX_RELATIONSHIP_PREDICATE
                for claim in claims
            ),
        )
        coverage = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(
            1,
            coverage["taiwan_tax_relationship_review_scope"][
                "current_relationship_claim_count"
            ],
        )

    def test_release_without_review_run_omits_additive_lineage_surface(self) -> None:
        output = Path(self.temporary_directory.name) / "unreviewed-release"

        manifest = write_release(
            self.connection,
            output,
            as_of="2026-01-03",
            recorded_at=CUTOFF,
        )

        self.assertNotIn("reviewed_relationship_runs", manifest)
        self.assertNotIn("reviewed_relationship_runs.jsonl", manifest["files"])
        self.assertFalse((output / "reviewed_relationship_runs.jsonl").exists())
        coverage = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
        self.assertNotIn("taiwan_tax_relationship_review_scope", coverage)
        self.assertFalse(
            any(
                "registered-tax-unit references" in gap
                for gap in coverage["known_gaps"]
            )
        )
        readme = (output / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("reviewed_relationship_runs.jsonl", readme)
        self.assertNotIn("exact same-time UBN references only", readme)

    def test_exact_replay_is_zero_write_and_tamper_is_rejected(self) -> None:
        proposal = self._proposal()
        review = self._review(proposal, match_ubns=frozenset({"11111111"}))
        first = self._accept(proposal, review)
        changes = self.connection.total_changes
        replay = self._accept(proposal, review)
        self.assertTrue(replay.replayed)
        self.assertEqual(0, replay.rows_written)
        self.assertEqual(changes, self.connection.total_changes)
        self.connection.execute("DROP TRIGGER claim_versions_supersession_transition")
        self.connection.execute(
            "UPDATE claim_versions SET superseded_at = ? WHERE id = ?",
            ("2026-01-04T00:00:00Z", first.relationship_claim_ids[0]),
        )
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, "unproven supersession"):
            self._accept(proposal, review)

    def test_later_review_can_correct_match_to_defer(self) -> None:
        proposal = self._proposal()
        initial_defer = self._review(proposal, match_ubns=frozenset())
        zero_match = self._accept(
            proposal, initial_defer, accepted_at="2026-01-03T01:30:00Z"
        )
        self.assertEqual((), zero_match.relationship_claim_ids)
        self.assertIsNotNone(
            self.connection.execute(
                "SELECT 1 FROM ingestion_runs WHERE id = ?",
                (zero_match.review_ingestion_run_id,),
            ).fetchone()
        )
        first_review = self._review(
            proposal, match_ubns=frozenset({"11111111"})
        )
        first = self._accept(proposal, first_review)
        reaffirmed = self._accept(
            proposal, first_review, accepted_at="2026-01-03T03:00:00Z"
        )
        self.assertEqual(first.relationship_claim_ids, reaffirmed.reaffirmed_claim_ids)
        self.assertEqual((), reaffirmed.relationship_claim_ids)
        deferred = self._review(proposal, match_ubns=frozenset())
        second = self._accept(
            proposal, deferred, accepted_at="2026-01-04T02:00:00Z"
        )
        self.assertEqual((), second.relationship_claim_ids)
        self.assertEqual(first.relationship_claim_ids, second.superseded_claim_ids)
        superseded_at = self.connection.execute(
            "SELECT superseded_at FROM claim_versions WHERE id = ?",
            (first.relationship_claim_ids[0],),
        ).fetchone()[0]
        self.assertEqual("2026-01-04T02:00:03Z", superseded_at)

    def test_acceptance_rolls_back_if_end_verification_fails(self) -> None:
        proposal = self._proposal()
        review = self._review(proposal, match_ubns=frozenset({"11111111"}))
        before = {
            table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "source_families",
                "sources",
                "ingestion_runs",
                "claim_series",
                "claim_versions",
                "relationship_values",
            )
        }
        with patch.object(
            relationship,
            "_reverify_artifact_files",
            side_effect=ValueError("fixture end verification failure"),
        ):
            with self.assertRaisesRegex(ValueError, "end verification"):
                self._accept(proposal, review)
        after = {
            table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        self.assertEqual(before, after)

    def test_selected_run_freezes_state_against_later_source_refresh(self) -> None:
        before = self._proposal()
        source_id = self.connection.execute(
            "SELECT source_id FROM ingestion_runs WHERE id = ?", (self.moenv_run_id,)
        ).fetchone()[0]
        later_document_id = stable_id("source-document", "later-moenv")
        later_run_id = stable_id("ingestion-run", "later-moenv")
        add_source_document(
            self.connection,
            SourceDocument(
                later_document_id,
                source_id,
                "https://example.com/later-moenv.jsonl",
                "later-moenv",
                "2026-01-02T12:00:00Z",
                "a" * 64,
            ),
        )
        add_ingestion_run(
            self.connection,
            IngestionRun(
                later_run_id,
                source_id,
                "2026-01-02T12:00:00Z",
                status=IngestionStatus.SUCCEEDED,
                completed_at="2026-01-02T12:00:01Z",
                input_document_id=later_document_id,
                parameters={"fixture": "later"},
            ),
        )
        later_record_id = stable_id("source-record", later_run_id, "later-m1")
        payload = {"refresh": "later"}
        add_source_record(
            self.connection,
            SourceRecord(
                later_record_id,
                later_run_id,
                later_document_id,
                "later-m1",
                "2026-01-02T12:00:00Z",
                source_record_payload_sha256(payload),
                payload=payload,
            ),
        )
        old = self.connection.execute(
            """
            SELECT versions.id, versions.series_id, versions.valid_from
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.subject_entity_id = ? AND series.predicate = ?
              AND versions.superseded_at IS NULL
            """,
            (self.m1_entity, relationship.MOENV_UBN_PREDICATE),
        ).fetchone()
        insert_claim(
            self.connection,
            ClaimVersion(
                stable_id("claim-version", "later-m1"),
                old["series_id"],
                old["valid_from"],
                "2026-01-02T12:00:00Z",
                ClaimKind.SOURCE_STATEMENT,
                "fixture_later_correction_v1",
                1.0,
                created_by_run_id=later_run_id,
            ),
            ScalarValue(ScalarType.TEXT, "99999999"),
            evidence=[
                EvidenceLink(
                    later_document_id,
                    source_record_id=later_record_id,
                    locator=relationship.MOENV_UBN_PREDICATE,
                    excerpt="99999999",
                )
            ],
        )
        self.connection.commit()
        after = self._proposal()
        self.assertEqual(before.canonical_bytes, after.canonical_bytes)


if __name__ == "__main__":
    unittest.main()
