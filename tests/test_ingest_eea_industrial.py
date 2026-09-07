from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from semiconductor_atlas.adapters.eea_industrial import (
    EEA_FILTER_VERSION,
    EEA_METADATA_TABLE,
    EEA_RECORD_TYPE,
)
from semiconductor_atlas.database import initialize
from semiconductor_atlas.cli import main
from semiconductor_atlas.eea_industrial_review import (
    EEA_REVIEW_FORMAT,
    build_eea_industrial_review_queue,
    parse_eea_industrial_review_bytes,
    parse_eea_industrial_review_queue_bytes,
)
from semiconductor_atlas.eea_industrial_snapshot import (
    VerifiedEEAIndustrialSnapshot,
)
from semiconductor_atlas.ingest_eea_industrial import (
    ENTITY_KEY_PREFIX,
    accept_eea_industrial_review,
)
from semiconductor_atlas.repository import validate_database


RETRIEVED_AT = "2026-07-20T10:37:16Z"
SNAPSHOT_ACCEPTED_AT = "2026-07-20T16:42:04Z"
QUEUE_GENERATED_AT = "2026-07-20T17:00:00Z"
QUEUE_CUTOFF_AT = "2026-07-20T17:00:00Z"
REVIEW_CUTOFF_AT = "2026-07-20T18:00:00Z"
REVIEWED_AT = "2026-07-20T18:30:00Z"
ACCEPTED_AT = "2026-07-20T19:00:00Z"


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _compact(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _projection(
    values: dict[str, object], *, redacted_fields: tuple[str, ...] = ()
) -> dict[str, object]:
    result: dict[str, object] = {
        "projected_values_sha256": hashlib.sha256(_compact(values)).hexdigest(),
        "values": values,
    }
    if redacted_fields:
        result["redacted_fields"] = sorted(redacted_fields)
    return result


def _candidate(
    facility_id: str,
    *,
    country: str,
    name: str | None,
    confidential: bool = False,
) -> dict[str, object]:
    protected_values: dict[str, str | None] = {
        "buildingNumber": None if confidential else "42",
        "city": None if confidential else "Dresden",
        "nameOfFeature": None if confidential else name,
        "postalCode": None if confidential else "01067",
        "streetName": None if confidential else "Source Way",
    }
    facility_values: dict[str, object] = {
        "Facility_INSPIRE_ID": facility_id,
        "NUTSRegionSourceCode": "DED21",
        "NUTSRegionSourceName": "Dresden",
        "Parent_Site_INSPIRE_ID": f"{facility_id}.PARENT",
        "ProductionFacility_thematicId": "publisher-mapped-123",
        "ProductionFacility_thematicIdScheme": "publisher-scheme",
        "RBDSourceCode": "RBD-1",
        "RBDSourceName": "River basin",
        "addressDetails_confidentialityReasonCode": (
            "confidential" if confidential else None
        ),
        "countryCode": country,
        "dateOfStartOfOperation": "1900-01-01",
        "facilityName_confidentialityReasonCode": (
            "confidential" if confidential else None
        ),
        "facilityType": "EPRTR",
        "fileId_EUReg": "7001",
        "mainActivityCode": "source-main-activity",
        "mainActivityName": "Source main activity",
        "parentCompanyName": "Parent Company That Must Not Be Promoted",
        "parentCompany_confidentialityReasonCode": None,
        "pointGeometryLat": "0",
        "pointGeometryLon": "0",
        **protected_values,
    }
    redacted = (
        (
            "addressDetails_confidentialityReasonName",
            "buildingNumber",
            "city",
            "facilityName_confidentialityReasonName",
            "nameOfFeature",
            "parentCompanyURL",
            "parentCompany_confidentialityReasonName",
            "postalCode",
            "streetName",
        )
        if confidential
        else (
            "addressDetails_confidentialityReasonName",
            "facilityName_confidentialityReasonName",
            "parentCompanyURL",
            "parentCompany_confidentialityReasonName",
        )
    )
    site_name = name or "Confidential parent semiconductor site"
    function_values = {
        "Facility_INSPIRE_ID": facility_id,
        "FunctionId": "80001",
        "NACEMainEconomicActivityCode": "26.11",
        "NACEMainEconomicActivityName": "Manufacture of electronic components",
    }
    detail_values = {
        "Facility_INSPIRE_ID": facility_id,
        "ProductionFacilityDetailsID": "90001",
        "confidentialityReasonCode": None,
        "fileId_EPRTR_LCP": None,
        "fileId_EUReg": "7001",
        "numberOfEmployees": "1700",
        "numberOfOperatingHours": "8424",
        "reportingYear": "2024",
        "representativeStackHeightM": "10",
        "stackHeightClass": "source-stack",
        "status": "functional",
    }
    metadata_values = {
        "countryCode": country,
        "dateImported": "2025-11-25T17:15:12",
        "dateReleased": "2025-11-25T15:28:04",
        "dateSubmitted": "2025-11-25T13:42:10",
        "fileId": "7001",
        "fileSHA256Hash": "f" * 64,
        "filename": "EUReg_2024.xml",
        "obligation": "721",
        "reportingYear": "2024",
    }
    return {
        "candidate_reasons": [
            {
                "kind": "electronic_components_nace_26_11_candidate",
                "raw_value": "26.11",
                "source_field": "NACEMainEconomicActivityCode",
                "source_projected_values_sha256": hashlib.sha256(
                    _compact(function_values)
                ).hexdigest(),
                "source_table": "2c_Function",
            }
        ],
        "facility_detail_rows": [_projection(detail_values)],
        "facility_inspire_id": facility_id,
        "facility_rows": [
            _projection(facility_values, redacted_fields=tuple(sorted(redacted)))
        ],
        "function_rows": [_projection(function_values)],
        "join_anomalies": [],
        "latest_source_detail_reporting_year": "2024",
        "metadata_rows": [
            {
                **_projection(
                    metadata_values,
                    redacted_fields=("envelopeUrl",),
                ),
                "source_table": EEA_METADATA_TABLE,
            }
        ],
        "record_type": EEA_RECORD_TYPE,
        "site_rows": [
            _projection(
                {
                    "ProductionSite_thematicId": "site-theme-1",
                    "ProductionSite_thematicIdScheme": "site-scheme",
                    "Site_INSPIRE_ID": f"{facility_id}.PARENT",
                    "countryCode": country,
                    "fileId_EUReg": "7001",
                    "nameOfFeature": site_name,
                    "pointGeometryLat": "51.0",
                    "pointGeometryLon": "13.7",
                }
            )
        ],
    }


def _candidate_jsonl(candidates: tuple[dict[str, object], ...]) -> bytes:
    return b"\n".join(_compact(item) for item in candidates) + b"\n"


def _snapshot(
    root: Path,
    candidates: tuple[dict[str, object], ...] | None = None,
) -> VerifiedEEAIndustrialSnapshot:
    selected = candidates or (
        _candidate(
            "DE.EEA/MixedCase-1.FACILITY",
            country="DE",
            name="Accepted Semiconductor Facility",
        ),
        _candidate(
            "FR.EEA/Deferred-2.FACILITY",
            country="FR",
            name="Deferred Semiconductor Facility",
        ),
        _candidate(
            "AT.EEA/Rejected-3.FACILITY",
            country="AT",
            name="Rejected Electronic Component Facility",
        ),
    )
    candidate_raw = _candidate_jsonl(selected)
    manifest_raw = b'{"fixture":"eea-v16"}\n'
    return VerifiedEEAIndustrialSnapshot(
        root=root,
        manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        manifest_size=len(manifest_raw),
        manifest_bytes=manifest_raw,
        retrieved_at=RETRIEVED_AT,
        metadata_retrieved_at="2026-07-20T10:51:14Z",
        accepted_at=SNAPSHOT_ACCEPTED_AT,
        candidate_sha256=hashlib.sha256(candidate_raw).hexdigest(),
        candidate_size=len(candidate_raw),
        candidate_count=len(selected),
        raw_sha256="c" * 64,
        raw_size=2_031_214_592,
        extraction_metadata_sha256="d" * 64,
        scan=SimpleNamespace(candidates=selected),
    )


def _queue(snapshot: VerifiedEEAIndustrialSnapshot):
    return build_eea_industrial_review_queue(
        snapshot,
        generated_at=QUEUE_GENERATED_AT,
        knowledge_cutoff_at=QUEUE_CUTOFF_AT,
    )


def _evidence(candidate_id: str) -> dict[str, object]:
    return {
        "accessed_at": "2026-07-20T17:30:00Z",
        "excerpt": "External evidence resolves only the atlas facility scope.",
        "title": "Official facility activity page",
        "url": f"https://example.org/facilities/{candidate_id}",
    }


def _review(
    queue,
    outcomes_by_facility: dict[str, str] | None = None,
    *,
    reviewed_by: str = "reviewer@example.org",
):
    default_outcomes = {
        "DE.EEA/MixedCase-1.FACILITY": "accept_in_scope",
        "FR.EEA/Deferred-2.FACILITY": "defer",
        "AT.EEA/Rejected-3.FACILITY": "reject_out_of_scope",
    }
    outcomes = outcomes_by_facility or default_outcomes
    decisions = []
    for candidate in sorted(queue.candidates, key=lambda item: item.candidate_id):
        outcome = outcomes[candidate.facility_inspire_id]
        decisions.append(
            {
                "candidate_id": candidate.candidate_id,
                "evidence": (
                    [] if outcome == "defer" else [_evidence(candidate.candidate_id)]
                ),
                "outcome": outcome,
                "reason": "Reviewed disposition is limited to atlas facility scope.",
            }
        )
    raw = _canonical(
        {
            "candidate_count": len(decisions),
            "candidate_queue_sha256": queue.raw_sha256,
            "decisions": decisions,
            "filter_version": EEA_FILTER_VERSION,
            "format": EEA_REVIEW_FORMAT,
            "knowledge_cutoff_at": REVIEW_CUTOFF_AT,
            "reviewed_at": REVIEWED_AT,
            "reviewed_by": reviewed_by,
            "snapshot_manifest_sha256": queue.snapshot_manifest_sha256,
            "source_candidate_sha256": queue.source_candidate_sha256,
        }
    )
    return parse_eea_industrial_review_bytes(raw, queue=queue)


class EEAIndustrialImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.connection, _ = initialize(self.root / "atlas.sqlite")
        self.snapshot = _snapshot(self.root / "snapshot")
        self.queue = _queue(self.snapshot)
        self.review = _review(self.queue)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def _import(
        self,
        *,
        snapshot=None,
        queue=None,
        review=None,
        accepted_at: str = ACCEPTED_AT,
        verifier=None,
    ):
        selected_snapshot = snapshot or self.snapshot
        selected_verifier = verifier or (lambda _root: selected_snapshot)
        with mock.patch(
            "semiconductor_atlas.ingest_eea_industrial.verify_eea_industrial_snapshot",
            side_effect=selected_verifier,
        ):
            return accept_eea_industrial_review(
                self.connection,
                snapshot=selected_snapshot,
                candidate_queue=queue or self.queue,
                review=review or self.review,
                accepted_at=accepted_at,
            )

    def test_imports_only_accepted_source_native_scalar_whitelist(self) -> None:
        result = self._import()

        self.assertEqual(1, result.accepted_candidates)
        self.assertEqual(1, result.deferred_candidates)
        self.assertEqual(1, result.rejected_candidates)
        self.assertEqual(2, result.source_documents_created)
        self.assertEqual(1, result.source_records_created)
        self.assertEqual(1, result.entities_created)
        self.assertEqual(9, result.claims_created)
        entity = self.connection.execute(
            "SELECT * FROM entities WHERE id = ?", (result.facility_entity_ids[0],)
        ).fetchone()
        self.assertEqual(
            f"{ENTITY_KEY_PREFIX}DE.EEA/MixedCase-1.FACILITY",
            entity["stable_key"],
        )
        self.assertIsNone(entity["display_name"])
        self.assertEqual("facility", entity["kind"])

        source_record = self.connection.execute(
            "SELECT * FROM source_records WHERE id = ?", (result.source_record_ids[0],)
        ).fetchone()
        queued = next(
            item
            for item in self.queue.candidates
            if item.facility_inspire_id == "DE.EEA/MixedCase-1.FACILITY"
        )
        self.assertEqual(queued.source_candidate_sha256, source_record["record_sha256"])
        self.assertEqual(
            self.snapshot.scan.candidates[0], json.loads(source_record["payload_json"])
        )
        self.assertEqual(SNAPSHOT_ACCEPTED_AT, source_record["observed_at"])

        predicates = {
            row[0]
            for row in self.connection.execute(
                "SELECT predicate FROM claim_series ORDER BY predicate"
            )
        }
        self.assertEqual(
            {
                "address.building_number",
                "address.city",
                "address.country_code",
                "address.postal_code",
                "address.street",
                "eea_industrial.facility_inspire_id",
                "eea_industrial.nace_main_economic_activity_code",
                "eea_industrial.nace_main_economic_activity_name",
                "name",
            },
            predicates,
        )
        claim_shapes = {
            (row["claim_kind"], row["value_kind"], float(row["confidence"]))
            for row in self.connection.execute(
                """
                SELECT versions.claim_kind, series.value_kind, versions.confidence
                FROM claim_versions AS versions
                JOIN claim_series AS series ON series.id = versions.series_id
                """
            )
        }
        self.assertEqual({("source_statement", "scalar", 1.0)}, claim_shapes)
        self.assertEqual(
            {"2026-02-20"},
            {
                row[0]
                for row in self.connection.execute(
                    "SELECT DISTINCT valid_from FROM claim_versions"
                )
            },
        )
        for table in (
            "geometry_values",
            "relationship_values",
            "milestone_values",
            "capability_values",
            "capacity_values",
            "resource_values",
            "constraint_values",
            "claim_dependencies",
            "source_entity_assignments",
            "entity_resolution_candidates",
            "entity_resolution_decisions",
        ):
            self.assertEqual(
                0,
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                table,
            )
        self.assertEqual([], validate_database(self.connection))

    def test_documents_and_run_parameters_preserve_all_artifact_bindings(self) -> None:
        result = self._import()

        documents = {
            row["id"]: row
            for row in self.connection.execute("SELECT * FROM source_documents")
        }
        self.assertEqual(
            self.snapshot.raw_sha256,
            documents[result.raw_document_id]["content_sha256"],
        )
        self.assertEqual(
            self.snapshot.candidate_sha256,
            documents[result.candidate_document_id]["content_sha256"],
        )
        self.assertEqual(
            SNAPSHOT_ACCEPTED_AT,
            documents[result.candidate_document_id]["retrieved_at"],
        )
        parameters = json.loads(
            self.connection.execute(
                "SELECT parameters_json FROM ingestion_runs WHERE id = ?",
                (result.ingestion_run_id,),
            ).fetchone()[0]
        )
        self.assertEqual(
            self.snapshot.manifest_sha256, parameters["snapshot"]["manifest_sha256"]
        )
        self.assertEqual(
            self.snapshot.extraction_metadata_sha256,
            parameters["snapshot"]["extraction_metadata_sha256"],
        )
        self.assertEqual(self.queue.raw_sha256, parameters["candidate_queue"]["sha256"])
        self.assertEqual(
            self.review.raw_sha256, parameters["review_artifact"]["sha256"]
        )
        self.assertEqual(3, len(parameters["decisions"]))
        self.assertEqual(
            [
                next(
                    item.candidate_id
                    for item in self.queue.candidates
                    if item.facility_inspire_id == "DE.EEA/MixedCase-1.FACILITY"
                )
            ],
            parameters["review_artifact"]["accepted_candidate_ids"],
        )
        self.assertTrue(parameters["import_policy"]["source_native_only"])
        roles = {
            row[0]
            for row in self.connection.execute(
                "SELECT role FROM ingestion_run_documents WHERE ingestion_run_id = ?",
                (result.ingestion_run_id,),
            )
        }
        self.assertEqual(
            {"candidate_derivative", "primary", "raw_accdb", "source_record"},
            roles,
        )

    def test_zero_accept_review_records_ledger_without_semantic_rows(self) -> None:
        outcomes = {item.facility_inspire_id: "defer" for item in self.queue.candidates}
        review = _review(self.queue, outcomes)

        result = self._import(review=review)

        self.assertEqual(0, result.accepted_candidates)
        self.assertEqual(3, result.deferred_candidates)
        self.assertEqual(0, result.source_records_created)
        self.assertEqual(0, result.entities_created)
        self.assertEqual(0, result.claims_created)
        self.assertEqual(
            1,
            self.connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[
                0
            ],
        )
        self.assertEqual(
            2,
            self.connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[
                0
            ],
        )

    def test_exact_replay_is_write_free_and_other_acceptance_clock_fails(self) -> None:
        first = self._import()
        before = "\n".join(self.connection.iterdump())

        replay = self._import()

        self.assertEqual(first.ingestion_run_id, replay.ingestion_run_id)
        self.assertTrue(replay.replayed_existing_run)
        self.assertEqual(0, replay.source_documents_created)
        self.assertEqual(0, replay.source_records_created)
        self.assertEqual(0, replay.entities_created)
        self.assertEqual(0, replay.claim_series_created)
        self.assertEqual(0, replay.claims_created)
        self.assertEqual(before, "\n".join(self.connection.iterdump()))

        with self.assertRaisesRegex(ValueError, "accepted_at conflicts"):
            self._import(accepted_at="2026-07-20T19:00:01Z")
        self.assertEqual(before, "\n".join(self.connection.iterdump()))

    def test_changed_review_is_rejected_after_first_import(self) -> None:
        self._import()
        before = "\n".join(self.connection.iterdump())
        changed = _review(self.queue, reviewed_by="second-reviewer@example.org")

        with self.assertRaisesRegex(ValueError, "different review"):
            self._import(review=changed)

        self.assertEqual(before, "\n".join(self.connection.iterdump()))

    def test_self_consistent_forged_queue_fails_snapshot_rebuild_before_writes(
        self,
    ) -> None:
        payload = json.loads(self.queue.raw_bytes)
        payload["candidates"][0]["source_values"]["facility_name"] = "Forged"
        payload["candidates"][0]["source_candidate_sha256"] = "e" * 64
        forged = parse_eea_industrial_review_queue_bytes(_canonical(payload))

        with self.assertRaisesRegex(ValueError, "does not replay"):
            self._import(queue=forged)

        self.assertEqual(
            0,
            self.connection.execute("SELECT COUNT(*) FROM source_families").fetchone()[
                0
            ],
        )

    def test_confidential_and_forbidden_source_values_remain_record_only(self) -> None:
        confidential = _candidate(
            "DE.EEA/Confidential-4.FACILITY",
            country="DE",
            name=None,
            confidential=True,
        )
        snapshot = _snapshot(self.root / "confidential", (confidential,))
        queue = _queue(snapshot)
        review = _review(
            queue,
            {"DE.EEA/Confidential-4.FACILITY": "accept_in_scope"},
        )

        result = self._import(snapshot=snapshot, queue=queue, review=review)

        predicates = {
            row[0]
            for row in self.connection.execute("SELECT predicate FROM claim_series")
        }
        self.assertEqual(
            {
                "address.country_code",
                "eea_industrial.facility_inspire_id",
                "eea_industrial.nace_main_economic_activity_code",
                "eea_industrial.nace_main_economic_activity_name",
            },
            predicates,
        )
        excerpts = "\n".join(
            str(row[0])
            for row in self.connection.execute("SELECT excerpt FROM claim_evidence")
        )
        for forbidden in (
            "Parent Company That Must Not Be Promoted",
            "Confidential parent semiconductor site",
            "functional",
            "1900-01-01",
            "1700",
            "8424",
            '"pointGeometryLat":"0"',
        ):
            self.assertNotIn(forbidden, excerpts)
        record = json.loads(
            self.connection.execute(
                "SELECT payload_json FROM source_records WHERE id = ?",
                (result.source_record_ids[0],),
            ).fetchone()[0]
        )
        self.assertIsNone(record["facility_rows"][0]["values"]["nameOfFeature"])
        self.assertEqual("0", record["facility_rows"][0]["values"]["pointGeometryLat"])

    def test_snapshot_mutation_during_final_verification_rolls_back(self) -> None:
        changed = replace(self.snapshot, manifest_sha256="e" * 64)
        calls = iter((self.snapshot, changed))

        with self.assertRaisesRegex(ValueError, "changed during"):
            self._import(verifier=lambda _root: next(calls))

        for table in (
            "source_families",
            "sources",
            "source_documents",
            "ingestion_runs",
            "source_records",
            "entities",
            "claim_series",
            "claim_versions",
        ):
            self.assertEqual(
                0,
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                table,
            )

    def test_forced_database_validation_failure_rolls_back(self) -> None:
        with mock.patch(
            "semiconductor_atlas.ingest_eea_industrial.validate_database",
            return_value=["forced failure"],
        ):
            with self.assertRaisesRegex(ValueError, "forced failure"):
                self._import()

        self.assertEqual(
            0,
            self.connection.execute("SELECT COUNT(*) FROM source_families").fetchone()[
                0
            ],
        )
        self.assertEqual(
            0,
            self.connection.execute("SELECT COUNT(*) FROM claim_versions").fetchone()[
                0
            ],
        )

    def test_acceptance_cannot_predate_review_or_use_noncanonical_clock(self) -> None:
        for value in (
            "2026-07-20T18:29:59Z",
            "2026-07-20T19:00:00+00:00",
            "2026-07-20T12:00:00-07:00",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self._import(accepted_at=value)
        self.assertEqual(
            0,
            self.connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[
                0
            ],
        )

    def test_cli_verifies_bound_artifacts_imports_and_reports_hashes(self) -> None:
        queue_path = self.root / "candidate-queue.json"
        review_path = self.root / "review.json"
        queue_path.write_bytes(self.queue.raw_bytes)
        review_path.write_bytes(self.review.raw_bytes)
        self.connection.close()

        output = io.StringIO()
        with (
            mock.patch(
                "semiconductor_atlas.cli.verify_eea_industrial_snapshot",
                return_value=self.snapshot,
            ),
            mock.patch(
                "semiconductor_atlas.ingest_eea_industrial.verify_eea_industrial_snapshot",
                return_value=self.snapshot,
            ),
            contextlib.redirect_stdout(output),
        ):
            code = main(
                [
                    "ingest-eea-industrial-snapshot",
                    "--database",
                    str(self.root / "atlas.sqlite"),
                    "--snapshot",
                    str(self.snapshot.root),
                    "--candidate-queue",
                    str(queue_path),
                    "--review",
                    str(review_path),
                    "--accepted-at",
                    ACCEPTED_AT,
                ]
            )

        self.assertEqual(0, code)
        result = json.loads(output.getvalue())
        self.assertEqual(5, result["schema_version"])
        self.assertEqual(self.queue.raw_sha256, result["candidate_queue_sha256"])
        self.assertEqual(self.review.raw_sha256, result["review_sha256"])
        self.assertEqual(
            self.snapshot.manifest_sha256, result["snapshot_manifest_sha256"]
        )
        self.assertEqual(1, result["eea_industrial"]["accepted_candidates"])
        self.assertEqual(1, result["eea_industrial"]["entities_created"])
        self.assertEqual(9, result["eea_industrial"]["claims_created"])
        self.connection, _ = initialize(self.root / "atlas.sqlite")
        self.assertEqual([], validate_database(self.connection))


if __name__ == "__main__":
    unittest.main()
