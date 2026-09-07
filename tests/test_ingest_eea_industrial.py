from __future__ import annotations

import contextlib
import base64
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import zlib
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
from semiconductor_atlas.repository import current_claims, known_source_claims, validate_database


RETRIEVED_AT = "2026-07-20T10:37:16Z"
SNAPSHOT_ACCEPTED_AT = "2026-07-20T16:42:04Z"
QUEUE_GENERATED_AT = "2026-07-20T17:00:00Z"
QUEUE_CUTOFF_AT = "2026-07-20T17:00:00Z"
REVIEW_CUTOFF_AT = "2026-07-20T18:00:00Z"
REVIEWED_AT = "2026-07-20T18:30:00Z"
ACCEPTED_AT = "2026-07-20T19:00:00Z"
STARTED_AT = "2026-07-20T18:59:58Z"
VERIFIED_AT = "2026-07-20T19:00:02Z"

# Synthetic semantic rows captured from the unchanged v1 importer before v2.
# Retaining the old output tests legacy replay independently of the new writer.
LEGACY_V1_SQL = (
    "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"
    "4P//AZnn5fM="
)


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
        accepted_at: str | None = None,
        verifier=None,
        clocks=None,
    ):
        selected_snapshot = snapshot or self.snapshot
        selected_verifier = verifier or (lambda _root: selected_snapshot)
        with mock.patch(
            "semiconductor_atlas.ingest_eea_industrial.verify_eea_industrial_snapshot",
            side_effect=selected_verifier,
        ), mock.patch(
            "semiconductor_atlas.ingest_eea_industrial._now",
            side_effect=clocks or [STARTED_AT, ACCEPTED_AT, VERIFIED_AT],
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
            (row["claim_kind"], row["value_kind"], row["confidence"])
            for row in self.connection.execute(
                """
                SELECT versions.claim_kind, series.value_kind, versions.confidence
                FROM claim_versions AS versions
                JOIN claim_series AS series ON series.id = versions.series_id
                """
            )
        }
        self.assertEqual({("source_statement", "scalar", None)}, claim_shapes)
        self.assertEqual(
            {None},
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
            mock.patch(
                "semiconductor_atlas.ingest_eea_industrial._now",
                side_effect=[STARTED_AT, ACCEPTED_AT, VERIFIED_AT],
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
                    "--accept-now",
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


    def test_v2_claims_are_unknown_effective_and_visible_only_at_actual_admission(self):
        result = self._import()
        for world_time in ("2000-01-01", "2026-02-20", "2030-01-01"):
            self.assertEqual([], current_claims(
                self.connection, as_of=world_time, recorded_at=VERIFIED_AT))
        self.assertEqual([], known_source_claims(
            self.connection, recorded_at="2026-07-20T18:59:59.999999Z"))
        claims = known_source_claims(self.connection, recorded_at=result.accepted_at)
        self.assertEqual(9, len(claims))
        self.assertTrue(all(row["valid_from"] is None and row["valid_to"] is None
                            and row["confidence"] is None for row in claims))
        run = self.connection.execute("SELECT * FROM ingestion_runs").fetchone()
        self.assertEqual(STARTED_AT, run["started_at"])
        self.assertEqual(ACCEPTED_AT, run["completed_at"])
        self.assertNotEqual("2026-07-20T19:00:01Z", run["completed_at"])
        self.assertTrue(run["code_version"].endswith("-v2"))
        parameters = json.loads(run["parameters_json"])
        self.assertEqual("unknown_claim_effective_time", parameters["source_valid_from_basis"])
        self.assertFalse(parameters["import_policy"]["claim_confidence_calibrated"])
        self.assertEqual({"2026-02-20"}, {row[0] for row in self.connection.execute(
            "SELECT published_at FROM source_documents")})

    def test_v2_exact_replay_denies_all_database_writes(self):
        first = self._import()
        before = "\n".join(self.connection.iterdump())
        changes = self.connection.total_changes
        self.connection.set_authorizer(lambda action, *_:
            sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
                                              sqlite3.SQLITE_DELETE) else sqlite3.SQLITE_OK)
        try:
            replay = self._import(accepted_at=first.accepted_at)
        finally:
            self.connection.set_authorizer(None)
        self.assertTrue(replay.replayed_existing_run)
        self.assertEqual(changes, self.connection.total_changes)
        self.assertEqual(before, "\n".join(self.connection.iterdump()))

    def test_original_v1_exact_replay_preserves_every_row_and_denies_writes(self):
        self._seed_legacy_v1()
        before = "\n".join(self.connection.iterdump())
        changes = self.connection.total_changes
        self.connection.set_authorizer(lambda action, *_:
            sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
                                              sqlite3.SQLITE_DELETE) else sqlite3.SQLITE_OK)
        try:
            replay = self._import(accepted_at=ACCEPTED_AT)
        finally:
            self.connection.set_authorizer(None)
        self.assertTrue(replay.replayed_existing_run)
        self.assertEqual(0, replay.claims_created)
        self.assertEqual(changes, self.connection.total_changes)
        self.assertEqual(before, "\n".join(self.connection.iterdump()))
        self.assertEqual({("2026-02-20", 1.0)}, {tuple(row) for row in
            self.connection.execute("SELECT valid_from, confidence FROM claim_versions")})
        run = self.connection.execute("SELECT * FROM ingestion_runs").fetchone()
        self.assertTrue(run["code_version"].endswith("-v1"))
        self.assertEqual("2026-07-20T19:00:01Z", run["completed_at"])
        with self.assertRaisesRegex(ValueError, "different review"):
            self._import(review=_review(self.queue, reviewed_by="another-reviewer"))
        self.assertEqual(before, "\n".join(self.connection.iterdump()))

    def test_new_admission_cannot_use_operator_supplied_or_future_clock(self):
        for timestamp in (ACCEPTED_AT, "2099-01-01T00:00:00Z"):
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(ValueError, "replay-only"):
                self._import(accepted_at=timestamp)
        self.assertEqual(0, self.connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0])

    def test_bad_actual_clocks_and_future_review_roll_back(self):
        cases = [
            [STARTED_AT, STARTED_AT],
            [STARTED_AT, "2026-07-20T18:59:57Z"],
            [STARTED_AT, "2026-07-20T19:00:00+00:00"],
            ["2026-07-20T17:00:00Z", "2026-07-20T17:01:00Z"],
            [STARTED_AT, ACCEPTED_AT, "2026-07-20T18:59:59Z"],
        ]
        before = "\n".join(self.connection.iterdump())
        for clocks in cases:
            with self.subTest(clocks=clocks), self.assertRaises(ValueError):
                self._import(clocks=clocks)
            self.assertEqual(before, "\n".join(self.connection.iterdump()))

    def test_final_input_drift_after_rows_written_rolls_back(self):
        changed = replace(self.snapshot, manifest_sha256="e" * 64)
        reads = iter((self.snapshot, self.snapshot, changed))
        before = "\n".join(self.connection.iterdump())
        with self.assertRaisesRegex(ValueError, "changed during"):
            self._import(verifier=lambda _root: next(reads))
        self.assertEqual(before, "\n".join(self.connection.iterdump()))

    def test_new_admission_requires_schema5_without_migration(self):
        legacy_connection, _ = initialize(self.root / "schema4.sqlite", target_version=4)
        current = self.connection
        self.connection = legacy_connection
        try:
            before = "\n".join(legacy_connection.iterdump())
            with self.assertRaisesRegex(ValueError, "schema-5"):
                self._import()
            self.assertEqual(before, "\n".join(legacy_connection.iterdump()))
        finally:
            self.connection = current
            legacy_connection.close()

    def test_mixed_version_runs_are_not_replayed_or_duplicated(self):
        self._seed_legacy_v1()
        self.connection.execute("""INSERT INTO ingestion_runs
            SELECT 'mixed-v2-run', source_id, input_document_id, started_at, completed_at,
                   status, 'eea-industrial-reviewed-facility-import-v2', parameters_json, error
            FROM ingestion_runs""")
        before = "\n".join(self.connection.iterdump())
        with self.assertRaisesRegex(ValueError, "mixed-version"):
            self._import()
        self.assertEqual(before, "\n".join(self.connection.iterdump()))

    def test_exact_replay_rejects_extra_run_document_lineage_without_writes(self):
        original = self.connection
        for mode in ("legacy", "v2", "all-deferred"):
            with self.subTest(mode=mode):
                self.connection, _ = initialize(self.root / (mode + ".sqlite"))
                try:
                    review = self.review
                    if mode == "legacy":
                        self._seed_legacy_v1()
                    else:
                        if mode == "all-deferred":
                            review = _review(self.queue, {
                                row.facility_inspire_id: "defer"
                                for row in self.queue.candidates
                            })
                        self._import(review=review)
                    replay = self._import(review=review, accepted_at=ACCEPTED_AT)
                    expected_links = 3 if mode == "all-deferred" else 4
                    self.assertEqual(expected_links, self.connection.execute(
                        "SELECT COUNT(*) FROM ingestion_run_documents"
                    ).fetchone()[0])
                    self.connection.execute(
                        "INSERT INTO ingestion_run_documents VALUES (?, ?, ?)",
                        (replay.ingestion_run_id, replay.candidate_document_id,
                         "unexpected_extra_role"),
                    )
                    self.connection.commit()
                    self.assertEqual([], validate_database(self.connection))
                    before = "\n".join(self.connection.iterdump())
                    changes = self.connection.total_changes
                    self.connection.set_authorizer(lambda action, *_:
                        sqlite3.SQLITE_DENY if action in (
                            sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
                            sqlite3.SQLITE_DELETE) else sqlite3.SQLITE_OK)
                    try:
                        with self.assertRaisesRegex(ValueError, "document lineage"):
                            self._import(review=review, accepted_at=ACCEPTED_AT)
                    finally:
                        self.connection.set_authorizer(None)
                    self.assertEqual(changes, self.connection.total_changes)
                    self.assertEqual(before, "\n".join(self.connection.iterdump()))
                finally:
                    self.connection.close()
                    self.connection = original

    def _seed_legacy_v1(self):
        tables = ["source_families", "sources", "source_documents", "ingestion_runs",
                  "ingestion_run_documents", "source_records", "entities", "claim_series",
                  "claim_versions", "claim_values", "scalar_values", "claim_evidence"]
        statements = zlib.decompress(base64.b64decode(LEGACY_V1_SQL)).decode().splitlines()
        statements.sort(key=lambda line: tables.index(line.split('"')[1]))
        self.connection.executescript(
            "BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;")
        self.assertEqual([], validate_database(self.connection))


if __name__ == "__main__":
    unittest.main()
