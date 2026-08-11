from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from semiconductor_atlas import taiwan_facility_identity as identity
from semiconductor_atlas.database import initialize
from semiconductor_atlas.models import (
    ClaimKind,
    ClaimSeries,
    ClaimVersion,
    Entity,
    EntityKind,
    EvidenceLink,
    IngestionRun,
    IngestionStatus,
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
    add_source,
    add_source_document,
    add_source_family,
    add_source_record,
    insert_claim,
    stable_id,
)
from semiconductor_atlas.taiwan_facility_identity import (
    TaiwanFacilityReviewDecision,
    accept_taiwan_facility_review,
    build_taiwan_facility_review,
    parse_taiwan_facility_candidate_bytes,
    parse_taiwan_facility_review_bytes,
    propose_taiwan_facility_candidates,
    read_taiwan_facility_candidate_file,
)


BASE = "2026-01-01T00:00:00Z"
CUTOFF = "2026-01-03T00:00:00Z"
REVIEWED = "2026-01-03T01:00:00Z"
ACCEPTED = "2026-01-03T02:00:00Z"


class TaiwanFacilityIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "atlas.sqlite"
        self.connection, _ = initialize(
            self.database_path
        )
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
        self._seed()
        self.snapshot_patch = patch.multiple(
            identity,
            verify_moenv_snapshot=lambda _root: self.moenv_snapshot,
            verify_taiwan_factory_snapshot=lambda _root: self.factory_snapshot,
        )
        self.snapshot_patch.start()

    def tearDown(self) -> None:
        self.snapshot_patch.stop()
        self.connection.close()
        self.temporary_directory.cleanup()

    def _source_layer(
        self,
        source_key: str,
        candidate_sha256: str,
        manifest_sha256: str,
        raw_sha256: str,
        suffix: str,
        *,
        started_at: str,
        completed_at: str,
        parameters_are_snapshot_bound: bool,
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
                candidate_sha256,
            ),
        )
        parameters = (
            {
                "manifest_sha256": manifest_sha256,
                "candidate_derivative": {"sha256": candidate_sha256},
                "raw_archive": {"sha256": raw_sha256},
            }
            if parameters_are_snapshot_bound
            else {"fixture": suffix}
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
                parameters=parameters,
            ),
        )
        return source_id, document_id, run_id

    def _record_entity(
        self,
        *,
        run_id: str,
        document_id: str,
        stable_key: str,
        kind: EntityKind = EntityKind.FACILITY,
    ) -> tuple[str, str]:
        record_id = stable_id("source-record", run_id, stable_key)
        entity_id = stable_id("entity", stable_key)
        payload = {"stable_key": stable_key}
        observed_at = self.connection.execute(
            "SELECT retrieved_at FROM source_documents WHERE id = ?",
            (document_id,),
        ).fetchone()[0]
        created_at = self.connection.execute(
            "SELECT started_at FROM ingestion_runs WHERE id = ?",
            (run_id,),
        ).fetchone()[0]
        add_source_record(
            self.connection,
            SourceRecord(
                record_id,
                run_id,
                document_id,
                stable_key,
                observed_at,
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
                created_at,
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
        raw_value: str,
        suffix: str,
    ) -> str:
        recorded_at = self.connection.execute(
            "SELECT started_at FROM ingestion_runs WHERE id = ?",
            (run_id,),
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
                "2026-01-01",
                recorded_at,
                ClaimKind.SOURCE_STATEMENT,
                "fixture_exact_capture_v1",
                1.0,
                created_by_run_id=run_id,
            ),
            ScalarValue(ScalarType.TEXT, raw_value),
            evidence=[
                EvidenceLink(
                    document_id,
                    source_record_id=record_id,
                    locator=suffix,
                    excerpt=raw_value,
                )
            ],
        )
        return claim_id

    def _facility(
        self,
        *,
        run_id: str,
        document_id: str,
        stable_key: str,
        number_predicate: str,
        numbers: tuple[str, ...],
        name: str,
        address: str,
        ubn_predicate: str,
        ubn: str,
        status: str | None = None,
        kind: EntityKind = EntityKind.FACILITY,
    ) -> tuple[str, str]:
        record_id, entity_id = self._record_entity(
            run_id=run_id,
            document_id=document_id,
            stable_key=stable_key,
            kind=kind,
        )
        for index, number in enumerate(numbers):
            self._claim(
                run_id=run_id,
                document_id=document_id,
                record_id=record_id,
                entity_id=entity_id,
                predicate=number_predicate,
                raw_value=number,
                suffix=f"{stable_key}:number:{index}",
            )
        for predicate, value, label in (
            ("name", name, "name"),
            ("address.street", address, "address"),
            (ubn_predicate, ubn, "ubn"),
        ):
            self._claim(
                run_id=run_id,
                document_id=document_id,
                record_id=record_id,
                entity_id=entity_id,
                predicate=predicate,
                raw_value=value,
                suffix=f"{stable_key}:{label}",
            )
        if status is not None:
            self._claim(
                run_id=run_id,
                document_id=document_id,
                record_id=record_id,
                entity_id=entity_id,
                predicate="taiwan_factory_registry.registration_status",
                raw_value=status,
                suffix=f"{stable_key}:status",
            )
        return record_id, entity_id

    def _selected_record(self, stable_key: str) -> str:
        record_id = stable_id(
            "source-record", self.moenv_selected_run_id, stable_key
        )
        payload = {"stable_key": stable_key, "refresh": "selected"}
        add_source_record(
            self.connection,
            SourceRecord(
                record_id,
                self.moenv_selected_run_id,
                self.moenv_selected_document_id,
                stable_key,
                "2026-01-02T00:01:00Z",
                source_record_payload_sha256(payload),
                payload=payload,
            ),
        )
        return record_id

    def _seed(self) -> None:
        (
            self.moenv_source_id,
            self.moenv_producer_document_id,
            self.moenv_producer_run_id,
        ) = self._source_layer(
            identity.MOENV_SOURCE_KEY,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "moenv-producer",
            started_at="2026-01-01T00:01:00Z",
            completed_at="2026-01-01T00:02:00Z",
            parameters_are_snapshot_bound=False,
        )
        # A later unchanged refresh is the selected snapshot run.  It reuses the
        # producer's current claims and therefore creates no claim versions.
        self.moenv_selected_document_id = stable_id(
            "source-document", identity.MOENV_SOURCE_KEY, "moenv-selected"
        )
        add_source_document(
            self.connection,
            SourceDocument(
                self.moenv_selected_document_id,
                self.moenv_source_id,
                "https://example.com/moenv-selected.jsonl",
                "moenv-selected",
                "2026-01-02T00:01:00Z",
                self.moenv_snapshot.candidate_sha256,
            ),
        )
        self.moenv_selected_run_id = stable_id(
            "ingestion-run", identity.MOENV_SOURCE_KEY, "moenv-selected"
        )
        add_ingestion_run(
            self.connection,
            IngestionRun(
                self.moenv_selected_run_id,
                self.moenv_source_id,
                "2026-01-02T00:01:00Z",
                status=IngestionStatus.SUCCEEDED,
                completed_at="2026-01-02T00:02:00Z",
                code_version="fixture-v1",
                input_document_id=self.moenv_selected_document_id,
                parameters={
                    "manifest_sha256": self.moenv_snapshot.manifest_sha256,
                    "candidate_derivative": {
                        "sha256": self.moenv_snapshot.candidate_sha256
                    },
                    "raw_archive": {"sha256": self.moenv_snapshot.raw_sha256},
                },
            ),
        )
        (
            _factory_source_id,
            self.factory_document_id,
            self.factory_run_id,
        ) = self._source_layer(
            identity.FACTORY_SOURCE_KEY,
            self.factory_snapshot.candidate_sha256,
            self.factory_snapshot.manifest_sha256,
            self.factory_snapshot.raw_sha256,
            "factory-selected",
            started_at="2026-01-02T00:03:00Z",
            completed_at="2026-01-02T00:04:00Z",
            parameters_are_snapshot_bound=True,
        )

        self.exact_record_id, self.exact_entity_id = self._facility(
            run_id=self.moenv_producer_run_id,
            document_id=self.moenv_producer_document_id,
            stable_key=f"{identity.MOENV_SOURCE_KEY}:EXACT",
            number_predicate=identity.MOENV_FACTORY_NUMBER_PREDICATE,
            numbers=("12345678",),
            name="Observed Exact Name",
            address="Observed Exact Address",
            ubn_predicate="moenv.uniformno",
            ubn="11111111",
        )
        self.legacy_record_id, self.legacy_entity_id = self._facility(
            run_id=self.moenv_producer_run_id,
            document_id=self.moenv_producer_document_id,
            stable_key=f"{identity.MOENV_SOURCE_KEY}:LEGACY",
            number_predicate=identity.MOENV_FACTORY_NUMBER_PREDICATE,
            numbers=("99-630508-01",),
            name="Observed Legacy Name",
            address="Observed Legacy Address",
            ubn_predicate="moenv.uniformno",
            ubn="22222222",
        )
        self._facility(
            run_id=self.moenv_producer_run_id,
            document_id=self.moenv_producer_document_id,
            stable_key=f"{identity.MOENV_SOURCE_KEY}:CONFLICT",
            number_predicate=identity.MOENV_FACTORY_NUMBER_PREDICATE,
            numbers=("12345678", "99630508"),
            name="Conflicting Variant",
            address="Conflict Address",
            ubn_predicate="moenv.uniformno",
            ubn="33333333",
        )
        self._facility(
            run_id=self.moenv_producer_run_id,
            document_id=self.moenv_producer_document_id,
            stable_key=f"{identity.MOENV_SOURCE_KEY}:MISSING",
            number_predicate=identity.MOENV_FACTORY_NUMBER_PREDICATE,
            numbers=("87654321",),
            name="Missing Target",
            address="Missing Address",
            ubn_predicate="moenv.uniformno",
            ubn="44444444",
        )
        for number, name, address, ubn in (
            ("12345678", "Target Exact Name ", "Target Exact Address", "11111111"),
            ("99630508", "Target Legacy Name", "Target Legacy Address", "99999999"),
        ):
            self._facility(
                run_id=self.factory_run_id,
                document_id=self.factory_document_id,
                stable_key=f"{identity.FACTORY_SOURCE_KEY}:{number}",
                number_predicate=identity.FACTORY_NUMBER_PREDICATE,
                numbers=(number,),
                name=name,
                address=address,
                ubn_predicate="taiwan_factory_registry.unified_business_number",
                ubn=ubn,
                status="生產中",
            )
        # A cross-kind exact number must never become a facility target.
        self._facility(
            run_id=self.factory_run_id,
            document_id=self.factory_document_id,
            stable_key=f"{identity.FACTORY_SOURCE_KEY}:87654321",
            number_predicate=identity.FACTORY_NUMBER_PREDICATE,
            numbers=("87654321",),
            name="Organization Not Facility",
            address="Organization Address",
            ubn_predicate="taiwan_factory_registry.unified_business_number",
            ubn="44444444",
            status="生產中",
            kind=EntityKind.ORGANIZATION,
        )
        selected_records = {
            suffix: self._selected_record(f"{identity.MOENV_SOURCE_KEY}:{suffix}")
            for suffix in ("EXACT", "LEGACY", "CONFLICT", "MISSING")
        }
        old_exact_name_claim = self.connection.execute(
            """
            SELECT versions.id
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.subject_entity_id = ? AND series.predicate = 'name'
              AND versions.superseded_at IS NULL
            """,
            (self.exact_entity_id,),
        ).fetchone()[0]
        self.connection.execute(
            "UPDATE claim_versions SET superseded_at = ? WHERE id = ?",
            ("2026-01-02T00:01:00Z", old_exact_name_claim),
        )
        self._claim(
            run_id=self.moenv_selected_run_id,
            document_id=self.moenv_selected_document_id,
            record_id=selected_records["EXACT"],
            entity_id=self.exact_entity_id,
            predicate="name",
            raw_value="Observed Exact Name Updated",
            suffix="moenv-selected:EXACT:name-updated",
        )
        self.connection.commit()

    def _proposal(self):
        return propose_taiwan_facility_candidates(
            self.connection,
            knowledge_cutoff_at=CUTOFF,
            moenv_ingestion_run_id=self.moenv_selected_run_id,
            factory_ingestion_run_id=self.factory_run_id,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
        )

    def _review(self, proposal, outcomes=("match", "defer")):
        decisions = tuple(
            TaiwanFacilityReviewDecision(
                candidate.candidate_id,
                outcome,
                f"fixture {outcome}",
                "2026-01-01" if outcome == "match" else None,
            )
            for candidate, outcome in zip(
                proposal.candidates, outcomes, strict=True
            )
        )
        return build_taiwan_facility_review(
            proposal,
            reviewed_by="reviewer:test",
            reviewed_at=REVIEWED,
            decisions=decisions,
        )

    def test_exact_and_legacy_candidates_use_current_reused_claims(self) -> None:
        first = self._proposal()
        second = self._proposal()
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertEqual(first.canonical_sha256, second.canonical_sha256)
        self.assertEqual(2, len(first.candidates))
        by_raw = {item.raw_factory_registration_number: item for item in first.candidates}
        self.assertEqual(
            "exact_8_character_registration",
            by_raw["12345678"].normalization_method,
        )
        self.assertEqual(
            "99630508",
            by_raw["99-630508-01"].normalized_factory_registration_number,
        )
        producer_ids = tuple(
            item.ingestion_run_id
            for item in first.moenv.evidence_producer_ingestion_runs
        )
        self.assertEqual(
            tuple(sorted((self.moenv_producer_run_id, self.moenv_selected_run_id))),
            producer_ids,
        )
        self.assertEqual(
            {self.exact_entity_id, self.legacy_entity_id},
            {item.observed_entity_id for item in first.candidates},
        )
        self.assertTrue(
            all(item.target_entity_kind == "facility" for item in first.candidates)
        )
        exact = by_raw["12345678"]
        self.assertEqual(
            self.moenv_producer_run_id,
            self.connection.execute(
                "SELECT ingestion_run_id FROM source_records WHERE id = ?",
                (exact.observed_factory_registration_number.source_record_id,),
            ).fetchone()[0],
        )
        self.assertEqual(
            {"Observed Exact Name Updated"},
            {item.raw_value for item in exact.observed_names},
        )
        self.assertEqual(
            {self.moenv_selected_run_id},
            {
                self.connection.execute(
                    "SELECT ingestion_run_id FROM source_records WHERE id = ?",
                    (item.source_record_id,),
                ).fetchone()[0]
                for item in exact.observed_names
            },
        )
        self.assertIn(
            "Target Exact Name ",
            {
                value.raw_value
                for candidate in first.candidates
                for value in candidate.target_names
            },
        )

    def test_selected_run_excludes_later_same_source_state(self) -> None:
        later_document_id = stable_id("source-document", "moenv-later")
        later_run_id = stable_id("ingestion-run", "moenv-later")
        add_source_document(
            self.connection,
            SourceDocument(
                later_document_id,
                self.moenv_source_id,
                "https://example.com/moenv-later.jsonl",
                "moenv-later",
                "2026-01-02T12:00:00Z",
                "d" * 64,
            ),
        )
        add_ingestion_run(
            self.connection,
            IngestionRun(
                later_run_id,
                self.moenv_source_id,
                "2026-01-02T12:00:00Z",
                status=IngestionStatus.SUCCEEDED,
                completed_at="2026-01-02T12:01:00Z",
                code_version="fixture-v2",
                input_document_id=later_document_id,
                parameters={"fixture": "later-run-must-not-leak"},
            ),
        )
        self._facility(
            run_id=later_run_id,
            document_id=later_document_id,
            stable_key=f"{identity.MOENV_SOURCE_KEY}:LATER",
            number_predicate=identity.MOENV_FACTORY_NUMBER_PREDICATE,
            numbers=("12345678",),
            name="Later Leaking Facility",
            address="Later Address",
            ubn_predicate="moenv.uniformno",
            ubn="55555555",
        )
        self.connection.commit()
        proposal = self._proposal()
        self.assertEqual(2, len(proposal.candidates))
        self.assertNotIn(
            f"{identity.MOENV_SOURCE_KEY}:LATER",
            {item.observed_entity_stable_key for item in proposal.candidates},
        )

    def test_strict_artifacts_reject_bad_json_incomplete_review_and_symlink(self) -> None:
        proposal = self._proposal()
        payload = json.loads(proposal.canonical_bytes)
        payload["candidates"][0]["score"] = float("nan")
        raw_nan = json.dumps(payload, allow_nan=True).encode()
        with self.assertRaisesRegex(ValueError, "strict JSON"):
            parse_taiwan_facility_candidate_bytes(raw_nan)
        duplicate = proposal.canonical_bytes.replace(
            b'{"candidates":', b'{"candidates":[],"candidates":', 1
        )
        with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
            parse_taiwan_facility_candidate_bytes(duplicate)

        review = self._review(proposal)
        review_payload = json.loads(review.canonical_bytes)
        review_payload["decisions"].pop()
        with self.assertRaisesRegex(ValueError, "exactly one decision"):
            parse_taiwan_facility_review_bytes(
                json.dumps(review_payload).encode(), candidate_artifact=proposal
            )
        review_payload = json.loads(review.canonical_bytes)
        review_payload["reviewed_at"] = "2026-01-02T23:59:59Z"
        with self.assertRaisesRegex(ValueError, "must not predate"):
            parse_taiwan_facility_review_bytes(
                json.dumps(review_payload).encode(), candidate_artifact=proposal
            )
        artifact_path = Path(self.temporary_directory.name) / "candidates.json"
        artifact_path.write_bytes(proposal.canonical_bytes)
        symlink = Path(self.temporary_directory.name) / "candidate-link.json"
        symlink.symlink_to(artifact_path)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            read_taiwan_facility_candidate_file(symlink)

    def test_module_cli_writes_candidates_and_accepts_review(self) -> None:
        candidate_path = Path(self.temporary_directory.name) / "candidates.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(
                0,
                identity.main(
                    [
                        "propose",
                        "--database",
                        str(self.database_path),
                        "--moenv-snapshot",
                        str(self.moenv_snapshot.root),
                        "--factory-snapshot",
                        str(self.factory_snapshot.root),
                        "--moenv-ingestion-run-id",
                        self.moenv_selected_run_id,
                        "--factory-ingestion-run-id",
                        self.factory_run_id,
                        "--knowledge-cutoff-at",
                        CUTOFF,
                        "--output",
                        str(candidate_path),
                    ]
                ),
            )
        summary = json.loads(stdout.getvalue())
        self.assertEqual(2, summary["candidate_count"])
        self.assertEqual(0o600, candidate_path.stat().st_mode & 0o777)
        with self.assertRaisesRegex(ValueError, "already exists"):
            identity.main(
                [
                    "propose",
                    "--database",
                    str(self.database_path),
                    "--moenv-snapshot",
                    str(self.moenv_snapshot.root),
                    "--factory-snapshot",
                    str(self.factory_snapshot.root),
                    "--moenv-ingestion-run-id",
                    self.moenv_selected_run_id,
                    "--factory-ingestion-run-id",
                    self.factory_run_id,
                    "--knowledge-cutoff-at",
                    CUTOFF,
                    "--output",
                    str(candidate_path),
                ]
            )
        proposal = read_taiwan_facility_candidate_file(candidate_path)
        review = self._review(proposal)
        review_path = Path(self.temporary_directory.name) / "review.json"
        review_path.write_bytes(review.canonical_bytes)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(
                0,
                identity.main(
                    [
                        "accept",
                        "--database",
                        str(self.database_path),
                        "--moenv-snapshot",
                        str(self.moenv_snapshot.root),
                        "--factory-snapshot",
                        str(self.factory_snapshot.root),
                        "--candidates",
                        str(candidate_path),
                        "--review",
                        str(review_path),
                        "--accepted-at",
                        ACCEPTED,
                    ]
                ),
            )
        self.assertFalse(json.loads(stdout.getvalue())["replayed"])

    def test_noncanonical_raw_identity_is_preserved_across_object_bytes_and_file(self) -> None:
        canonical_proposal = self._proposal()
        candidate_raw = json.dumps(
            json.loads(canonical_proposal.canonical_bytes),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        proposal = parse_taiwan_facility_candidate_bytes(candidate_raw)
        self.assertNotEqual(proposal.raw_bytes, proposal.canonical_bytes)
        canonical_review = self._review(proposal)
        review_raw = json.dumps(
            json.loads(canonical_review.canonical_bytes),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        review = parse_taiwan_facility_review_bytes(
            review_raw, candidate_artifact=proposal
        )
        accepted = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=proposal,
            review_artifact=review,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at=ACCEPTED,
        )
        self.assertFalse(accepted.replayed)
        replay_bytes = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=candidate_raw,
            review_artifact=review_raw,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at=ACCEPTED,
        )
        self.assertTrue(replay_bytes.replayed)
        candidate_path = Path(self.temporary_directory.name) / "raw-candidates.json"
        review_path = Path(self.temporary_directory.name) / "raw-review.json"
        candidate_path.write_bytes(candidate_raw)
        review_path.write_bytes(review_raw)
        replay_files = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=candidate_path,
            review_artifact=review_path,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at=ACCEPTED,
        )
        self.assertTrue(replay_files.replayed)
        with self.assertRaisesRegex(ValueError, "inconsistent canonical identity"):
            accept_taiwan_facility_review(
                self.connection,
                candidate_artifact=replace(proposal, raw_sha256="0" * 64),
                review_artifact=review,
                moenv_snapshot=self.moenv_snapshot,
                factory_snapshot=self.factory_snapshot,
                accepted_at=ACCEPTED,
            )

    def test_match_and_defer_acceptance_seals_run_and_replays_without_writes(self) -> None:
        proposal = self._proposal()
        review = self._review(proposal)
        result = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=proposal,
            review_artifact=review,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at=ACCEPTED,
        )
        self.assertFalse(result.replayed)
        self.assertEqual(2, len(result.candidate_ids))
        self.assertEqual(2, len(result.decision_ids))
        self.assertEqual(1, len(result.assignment_ids))
        status = self.connection.execute(
            "SELECT status FROM entity_resolution_runs WHERE id = ?",
            (result.resolution_run_id,),
        ).fetchone()[0]
        self.assertEqual("succeeded", status)
        outcomes = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT decisions.outcome
                FROM entity_resolution_decisions AS decisions
                JOIN entity_resolution_candidates AS candidates
                  ON candidates.id = decisions.candidate_id
                WHERE candidates.resolution_run_id = ?
                """,
                (result.resolution_run_id,),
            )
        }
        self.assertEqual({"match", "defer"}, outcomes)
        before = self.connection.total_changes
        replay = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=proposal,
            review_artifact=review,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at=ACCEPTED,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(0, replay.rows_written)
        self.assertEqual(before, self.connection.total_changes)

    def test_later_review_supersedes_assignment_and_nonmatch_creates_none(self) -> None:
        proposal = self._proposal()
        first_review = self._review(proposal, ("match", "match"))
        first = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=proposal,
            review_artifact=first_review,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at=ACCEPTED,
        )
        second_review = build_taiwan_facility_review(
            proposal,
            reviewed_by="reviewer:correction",
            reviewed_at="2026-01-04T01:00:00Z",
            decisions=tuple(
                TaiwanFacilityReviewDecision(
                    item.candidate_id,
                    "defer",
                    "correction requires more evidence",
                    None,
                )
                for item in proposal.candidates
            ),
        )
        second = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=proposal,
            review_artifact=second_review,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at="2026-01-04T02:00:00Z",
        )
        self.assertEqual(set(first.assignment_ids), set(second.superseded_assignment_ids))
        self.assertEqual((), second.assignment_ids)
        open_count = self.connection.execute(
            """
            SELECT COUNT(*) FROM source_entity_assignments
            WHERE observed_entity_id IN (?, ?) AND superseded_at IS NULL
            """,
            (self.exact_entity_id, self.legacy_entity_id),
        ).fetchone()[0]
        self.assertEqual(0, open_count)
        before = self.connection.total_changes
        original_replay = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=proposal,
            review_artifact=first_review,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at=ACCEPTED,
        )
        self.assertTrue(original_replay.replayed)
        self.assertEqual(before, self.connection.total_changes)

    def test_stale_artifact_can_only_retract_or_reaffirm_its_open_matches(self) -> None:
        proposal = self._proposal()
        initial_review = self._review(proposal, ("match", "match"))
        initial = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=proposal,
            review_artifact=initial_review,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at=ACCEPTED,
        )
        stale_candidate = proposal.candidates[0]
        self.connection.execute(
            "UPDATE claim_versions SET superseded_at = ? WHERE id = ?",
            (
                "2026-01-04T00:00:00Z",
                stale_candidate.observed_factory_registration_number.claim_version_id,
            ),
        )
        self.connection.commit()
        correction_decisions = tuple(
            TaiwanFacilityReviewDecision(
                candidate.candidate_id,
                "defer" if candidate == stale_candidate else "match",
                "stale registration retraction"
                if candidate == stale_candidate
                else "reaffirm unchanged open assignment",
                None if candidate == stale_candidate else "2026-01-01",
            )
            for candidate in proposal.candidates
        )
        correction_review = build_taiwan_facility_review(
            proposal,
            reviewed_by="reviewer:stale-correction",
            reviewed_at="2026-01-04T01:00:00Z",
            decisions=correction_decisions,
        )
        with self.assertRaisesRegex(ValueError, "stale"):
            accept_taiwan_facility_review(
                self.connection,
                candidate_artifact=proposal,
                review_artifact=correction_review,
                moenv_snapshot=self.moenv_snapshot,
                factory_snapshot=self.factory_snapshot,
                accepted_at="2026-01-04T02:00:00Z",
            )
        correction = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=proposal,
            review_artifact=correction_review,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at="2026-01-04T02:00:00Z",
            stale_correction=True,
        )
        self.assertEqual((), correction.assignment_ids)
        self.assertEqual(1, len(correction.superseded_assignment_ids))
        self.assertEqual(1, len(correction.reaffirmed_assignment_ids))
        self.assertEqual(
            1,
            self.connection.execute(
                """
                SELECT COUNT(*) FROM source_entity_assignments
                WHERE observed_entity_id IN (?, ?) AND superseded_at IS NULL
                """,
                (self.exact_entity_id, self.legacy_entity_id),
            ).fetchone()[0],
        )
        before = self.connection.total_changes
        correction_replay = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=proposal,
            review_artifact=correction_review,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at="2026-01-04T02:00:00Z",
            stale_correction=True,
        )
        self.assertTrue(correction_replay.replayed)
        self.assertEqual(before, self.connection.total_changes)
        original_replay = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=proposal,
            review_artifact=initial_review,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at=ACCEPTED,
        )
        self.assertTrue(original_replay.replayed)
        self.assertEqual(set(initial.assignment_ids), {
            row[0]
            for row in self.connection.execute(
                "SELECT id FROM source_entity_assignments WHERE decision_id IN (?, ?)",
                tuple(initial.decision_ids),
            )
        })

    def test_replay_rejects_unproven_assignment_timestamp_tamper(self) -> None:
        proposal = self._proposal()
        review = self._review(proposal, ("match", "match"))
        accepted = accept_taiwan_facility_review(
            self.connection,
            candidate_artifact=proposal,
            review_artifact=review,
            moenv_snapshot=self.moenv_snapshot,
            factory_snapshot=self.factory_snapshot,
            accepted_at=ACCEPTED,
        )
        self.connection.execute(
            "UPDATE source_entity_assignments SET superseded_at = ? WHERE id = ?",
            ("2026-01-05T00:00:00Z", accepted.assignment_ids[0]),
        )
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, "unproven later supersession"):
            accept_taiwan_facility_review(
                self.connection,
                candidate_artifact=proposal,
                review_artifact=review,
                moenv_snapshot=self.moenv_snapshot,
                factory_snapshot=self.factory_snapshot,
                accepted_at=ACCEPTED,
            )

    def test_stale_evidence_snapshot_and_artifact_failure_write_nothing(self) -> None:
        proposal = self._proposal()
        review = self._review(proposal)
        with patch.object(
            identity,
            "_reverify_artifact_sources",
            side_effect=ValueError("artifact changed during acceptance"),
        ):
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                accept_taiwan_facility_review(
                    self.connection,
                    candidate_artifact=proposal,
                    review_artifact=review,
                    moenv_snapshot=self.moenv_snapshot,
                    factory_snapshot=self.factory_snapshot,
                    accepted_at=ACCEPTED,
                )
        self.assertEqual(
            0,
            self.connection.execute(
                "SELECT COUNT(*) FROM entity_resolution_runs"
            ).fetchone()[0],
        )

        bad_snapshot = SimpleNamespace(**vars(self.factory_snapshot))
        bad_snapshot.manifest_sha256 = "f" * 64
        with patch.object(
            identity,
            "verify_taiwan_factory_snapshot",
            return_value=bad_snapshot,
        ):
            with self.assertRaisesRegex(ValueError, "factory snapshot conflicts"):
                accept_taiwan_facility_review(
                    self.connection,
                    candidate_artifact=proposal,
                    review_artifact=review,
                    moenv_snapshot=self.moenv_snapshot,
                    factory_snapshot=bad_snapshot,
                    accepted_at=ACCEPTED,
                )

        snapshot_verifications = 0

        def mutate_after_initial_checks(_value):
            nonlocal snapshot_verifications
            snapshot_verifications += 1
            return (
                self.factory_snapshot
                if snapshot_verifications < 3
                else bad_snapshot
            )

        with patch.object(
            identity,
            "_verified_factory_snapshot",
            side_effect=mutate_after_initial_checks,
        ):
            with self.assertRaisesRegex(ValueError, "changed during acceptance"):
                accept_taiwan_facility_review(
                    self.connection,
                    candidate_artifact=proposal,
                    review_artifact=review,
                    moenv_snapshot=self.moenv_snapshot,
                    factory_snapshot=self.factory_snapshot,
                    accepted_at=ACCEPTED,
                )
        self.assertEqual(
            0,
            self.connection.execute(
                "SELECT COUNT(*) FROM entity_resolution_runs"
            ).fetchone()[0],
        )

        observed_claim_id = (
            proposal.candidates[0]
            .observed_factory_registration_number.claim_version_id
        )
        self.connection.execute(
            "UPDATE claim_versions SET superseded_at = ? WHERE id = ?",
            ("2026-01-03T01:30:00Z", observed_claim_id),
        )
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, "stale"):
            accept_taiwan_facility_review(
                self.connection,
                candidate_artifact=proposal,
                review_artifact=review,
                moenv_snapshot=self.moenv_snapshot,
                factory_snapshot=self.factory_snapshot,
                accepted_at=ACCEPTED,
            )
        self.assertEqual(
            0,
            self.connection.execute(
                "SELECT COUNT(*) FROM entity_resolution_runs"
            ).fetchone()[0],
        )


if __name__ == "__main__":
    unittest.main()
