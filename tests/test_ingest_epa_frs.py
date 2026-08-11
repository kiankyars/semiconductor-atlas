from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from semiconductor_atlas.adapters.epa_frs import (
    FRS_ARCHIVE_MEMBER,
    FRS_DATA_AS_OF_BASIS,
    FRS_DATA_AS_OF_SOURCE_URL,
    FRS_DOCUMENTATION_MEMBER,
    FRS_FILTER_VERSION,
    FRS_HEADERS,
    FRS_NAICS_CODES,
    FRS_OFFICIAL_ARCHIVE_URL,
    FRS_SIC_CODES,
)
from semiconductor_atlas.database import initialize
from semiconductor_atlas import ingest_epa_frs as frs_ingest
from semiconductor_atlas.coverage import coverage_report
from semiconductor_atlas.ingest_epa_frs import import_epa_frs_candidates
from semiconductor_atlas.release import write_release
from semiconductor_atlas.repository import validate_database
from semiconductor_atlas.service import claim_records, summarize, validate_semantics


RETRIEVED_AT = "2026-07-20T01:12:47Z"
AS_OF_DATE = "2026-07-01"


def _row(
    registry_id: str,
    *,
    name: str,
    naics: str = "334413",
    sic: str = "",
    latitude: str = "33.43206",
    longitude: str = "-86.88049",
) -> dict[str, str]:
    row = {field: "" for field in FRS_HEADERS}
    row.update(
        {
            "FRS_FACILITY_DETAIL_REPORT_URL": (
                "https://ofmpub.epa.gov/frs_public2/fii_query_detail."
                f"disp_program_facility?p_registry_id={registry_id}"
            ),
            "REGISTRY_ID": registry_id,
            "PRIMARY_NAME": name,
            "LOCATION_ADDRESS": "100 Registry Way",
            "CITY_NAME": "Example",
            "COUNTY_NAME": "Example County",
            "STATE_CODE": "AZ",
            "STATE_NAME": "Arizona",
            "COUNTRY_NAME": "UNITED STATES",
            "POSTAL_CODE": "85001",
            "EPA_REGION_CODE": "09",
            "SITE_TYPE_NAME": "STATIONARY",
            "LOCATION_DESCRIPTION": "private free-form field not promoted",
            "CREATE_DATE": "01-MAR-00",
            "UPDATE_DATE": "26-JAN-12",
            "PGM_SYS_ACRNMS": "AIR:123",
            "INTEREST_TYPES": "AIR MAJOR",
            "NAICS_CODES": naics,
            "SIC_CODES": sic,
            "LATITUDE83": latitude,
            "LONGITUDE83": longitude,
            "CONVEYOR": "FRS",
            "COLLECT_DESC": "ADDRESS MATCHING-HOUSE NUMBER",
            "ACCURACY_VALUE": "30",
            "REF_POINT_DESC": "ENTRANCE POINT OF A FACILITY OR STATION",
            "HDATUM_DESC": "NAD83",
            "SOURCE_DESC": "PROGRAM SYSTEM",
        }
    )
    return row


def _candidate_bytes(rows: list[dict[str, str]]) -> bytes:
    payloads = []
    for row_number, row in enumerate(
        sorted(rows, key=lambda item: item["REGISTRY_ID"]), start=2
    ):
        payloads.append(
            json.dumps(
                {
                    "archive_member": FRS_ARCHIVE_MEMBER,
                    "row_number": row_number,
                    "row": row,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
    return "".join(payloads).encode("utf-8")


def _scope(
    candidate_count: int,
    *,
    data_as_of: str = AS_OF_DATE,
    archive_digit: str = "a",
) -> dict[str, object]:
    return {
        "complete": True,
        "coverage": "all_national_single_rows_matching_exact_naics_334413_or_sic_3674",
        "filter_version": FRS_FILTER_VERSION,
        "naics_codes": list(FRS_NAICS_CODES),
        "sic_codes": list(FRS_SIC_CODES),
        "data_as_of": data_as_of,
        "data_as_of_basis": FRS_DATA_AS_OF_BASIS,
        "data_as_of_source_url": FRS_DATA_AS_OF_SOURCE_URL,
        "retrieval_timestamp_basis": "operator_supplied_for_archived_bytes",
        "candidate_count": candidate_count,
        "upstream_total_rows": candidate_count + 10,
        "upstream_archive": {
            "url": FRS_OFFICIAL_ARCHIVE_URL,
            "sha256": archive_digit * 64,
            "bytes": 1000 + candidate_count,
            "etag": '"fixture"',
            "last_modified": "Wed, 08 Jul 2026 19:35:13 GMT",
        },
        "upstream_csv": {
            "member": FRS_ARCHIVE_MEMBER,
            "sha256": "c" * 64,
            "bytes": 5000 + candidate_count,
            "crc32": 123456,
            "row_count": candidate_count + 10,
        },
        "documentation_member": FRS_DOCUMENTATION_MEMBER,
        "raw_retention": {
            "retained_in_snapshot": True,
            "blob_locator": f"raw/sha256/{archive_digit * 64}.zip",
            "reason": None,
            "upstream_sha256": archive_digit * 64,
            "bytes": 1000 + candidate_count,
        },
        "rights": {
            "reviewed_at": "2026-07-19",
            "decision": "pass_for_exact_public_archive",
            "access_level": "public",
            "license_url": "https://edg.epa.gov/epa_data_license.html",
            "no_warranty": True,
            "scope": "exact_public_national_single_archive",
        },
    }


class EPAFRSImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        temporary = Path(self.temporary_directory.name)
        self.input_path = temporary / "candidates.jsonl"
        self.connection, _ = initialize(temporary / "atlas.sqlite")

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def _import(
        self,
        rows: list[dict[str, str]],
        *,
        retrieved_at: str = RETRIEVED_AT,
        accepted_at: str | None = None,
        as_of: str = AS_OF_DATE,
        complete: bool = True,
        archive_digit: str = "a",
    ):
        raw = _candidate_bytes(rows)
        self.input_path.write_bytes(raw)
        scope = _scope(
            len(rows), data_as_of=as_of, archive_digit=archive_digit
        )
        scope["complete"] = complete
        return import_epa_frs_candidates(
            self.connection,
            self.input_path,
            retrieved_at,
            as_of,
            accepted_at=accepted_at or retrieved_at,
            scope_metadata=scope,
            snapshot_is_complete=complete,
        )

    def test_imports_source_scoped_candidates_without_operational_claims(self) -> None:
        rows = [
            _row("110000000001", name="Direct NAICS", naics="236220, 334413"),
            _row("110000000002", name="Legacy SIC", naics="", sic="3674"),
        ]
        result = self._import(rows)

        self.assertEqual(2, result.candidates_imported)
        self.assertEqual(2, result.source_records_created)
        self.assertEqual(2, result.entities_created)
        self.assertEqual(
            {"epa:frs:110000000001", "epa:frs:110000000002"},
            {
                row[0]
                for row in self.connection.execute(
                    "SELECT stable_key FROM entities ORDER BY stable_key"
                )
            },
        )
        document = self.connection.execute(
            """
            SELECT document_url, content_sha256, media_type, metadata_json
            FROM source_documents WHERE id = ?
            """,
            (result.source_document_id,),
        ).fetchone()
        self.assertEqual(FRS_OFFICIAL_ARCHIVE_URL, document["document_url"])
        self.assertEqual("a" * 64, document["content_sha256"])
        self.assertEqual("application/zip", document["media_type"])
        metadata = json.loads(document["metadata_json"])
        parameters = json.loads(
            self.connection.execute(
                "SELECT parameters_json FROM ingestion_runs WHERE id = ?",
                (result.ingestion_run_id,),
            ).fetchone()[0]
        )
        self.assertEqual(
            hashlib.sha256(self.input_path.read_bytes()).hexdigest(),
            parameters["selected_artifact_sha256"],
        )
        self.assertIn("Raw NAD83", parameters["coordinate_policy"])
        self.assertEqual(FRS_DATA_AS_OF_BASIS, metadata["data_as_of_basis"])
        self.assertEqual(
            FRS_DATA_AS_OF_SOURCE_URL, metadata["data_as_of_source_url"]
        )
        self.assertTrue(parameters["raw_retention"]["retained_in_snapshot"])
        self.assertEqual(
            "operator_supplied_for_archived_bytes",
            metadata["retrieval_timestamp_basis"],
        )
        self.assertNotIn("importer_version", metadata)
        self.assertNotIn("selected_artifact_sha256", metadata)

        source_record = self.connection.execute(
            "SELECT payload_json FROM source_records WHERE source_record_key = ?",
            ("epa-frs:110000000001",),
        ).fetchone()
        payload = json.loads(source_record["payload_json"])
        self.assertEqual(set(FRS_HEADERS), set(payload["row"]))
        self.assertEqual(
            "private free-form field not promoted",
            payload["row"]["LOCATION_DESCRIPTION"],
        )

        predicates = {
            row[0]
            for row in self.connection.execute(
                "SELECT DISTINCT predicate FROM claim_series"
            )
        }
        self.assertIn("candidate_classification", predicates)
        self.assertIn("frs.latitude83_nad83_raw", predicates)
        self.assertIn("frs.longitude83_nad83_raw", predicates)
        self.assertNotIn("geometry", predicates)
        self.assertNotIn("lifecycle_state", predicates)
        self.assertNotIn("operator", predicates)
        self.assertNotIn("location_description", predicates)
        for table in (
            "geometry_values",
            "capacity_values",
            "milestone_values",
            "relationship_values",
            "capability_values",
            "resource_values",
        ):
            self.assertEqual(
                0,
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
            )

        classifications = self.connection.execute(
            """
            SELECT versions.id, versions.claim_kind, versions.confidence, versions.notes,
                   COUNT(evidence.id) AS evidence_count,
                   COUNT(dependencies.depends_on_claim_version_id) AS dependency_count
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            LEFT JOIN claim_evidence AS evidence
              ON evidence.claim_version_id = versions.id
            LEFT JOIN claim_dependencies AS dependencies
              ON dependencies.claim_version_id = versions.id
            WHERE series.predicate = 'candidate_classification'
            GROUP BY versions.id
            """
        ).fetchall()
        self.assertEqual(2, len(classifications))
        self.assertTrue(
            all(row["claim_kind"] == "derived_estimate" for row in classifications)
        )
        self.assertTrue(all(row["evidence_count"] == 0 for row in classifications))
        self.assertTrue(all(row["dependency_count"] >= 1 for row in classifications))
        self.assertTrue(all(row["confidence"] == 1.0 for row in classifications))
        self.assertTrue(
            all(
                "not the probability of an operating semiconductor facility"
                in row["notes"]
                for row in classifications
            )
        )
        dependency_predicates = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT DISTINCT parent_series.predicate
                FROM claim_dependencies AS dependencies
                JOIN claim_versions AS parent
                  ON parent.id = dependencies.depends_on_claim_version_id
                JOIN claim_series AS parent_series ON parent_series.id = parent.series_id
                """
            )
        }
        self.assertEqual({"frs.naics_code", "frs.sic_code"}, dependency_predicates)
        self.assertEqual([], validate_database(self.connection))
        self.assertEqual([], validate_semantics(self.connection))
        coverage = coverage_report(
            self.connection,
            as_of=AS_OF_DATE,
            recorded_at=RETRIEVED_AT,
        )["epa_frs_candidate_scope"]
        self.assertTrue(coverage["complete"])
        self.assertEqual(2, coverage["snapshot_candidate_count"])
        self.assertEqual(2, coverage["current_candidate_entity_count"])
        self.assertEqual(2, coverage["raw_nad83_coordinate_pair_count"])
        self.assertEqual(0, coverage["geojson_geometry_count"])

    def test_later_semantically_identical_snapshot_records_observation_without_claim_churn(
        self,
    ) -> None:
        first = self._import(
            [
                _row(
                    "110000000001",
                    name="  Stable proposition  ",
                    naics="334413, 236220",
                )
            ],
            accepted_at="2026-07-20T01:13:47Z",
        )
        before_versions = {
            row["id"]: row["superseded_at"]
            for row in self.connection.execute(
                "SELECT id, superseded_at FROM claim_versions"
            )
        }

        later = self._import(
            [
                _row(
                    "110000000001",
                    name="Stable proposition",
                    naics="236220,334413",
                )
            ],
            retrieved_at="2026-08-10T12:00:00Z",
            accepted_at="2026-08-10T12:05:00Z",
            as_of="2026-08-01",
            archive_digit="b",
        )

        self.assertFalse(later.replayed_existing_run)
        self.assertEqual(0, later.claims_created)
        self.assertEqual(0, later.prior_open_claims_closed_or_corrected)
        self.assertEqual(first.claims_created, later.unchanged_claims_reused)
        self.assertEqual(
            before_versions,
            {
                row["id"]: row["superseded_at"]
                for row in self.connection.execute(
                    "SELECT id, superseded_at FROM claim_versions"
                )
            },
        )
        self.assertEqual(
            2,
            self.connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0],
        )
        self.assertEqual(
            2,
            self.connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0],
        )

        release_dir = Path(self.temporary_directory.name) / "unchanged-release"
        manifest = write_release(
            self.connection,
            release_dir,
            as_of="2026-08-01",
            recorded_at="2026-08-10T12:05:00Z",
        )
        self.assertEqual(2, manifest["source_observations"])
        observations = [
            json.loads(line)
            for line in (release_dir / "source_observations.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(
            {"2026-07-20T01:13:47Z", "2026-08-10T12:05:00Z"},
            {item["database_accepted_at"] for item in observations},
        )
        self.assertEqual(
            {"epa-frs:110000000001"},
            {item["source_record_key"] for item in observations},
        )
        source_inputs = json.loads(
            (release_dir / "source_inputs.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"2026-07-20T01:13:47Z", "2026-08-10T12:05:00Z"},
            {item["database_accepted_at"] for item in source_inputs},
        )
        self.assertTrue(
            all(
                item["processing_runs"][0]["parameters"]["raw_retention"][
                    "retained_in_snapshot"
                ]
                for item in source_inputs
            )
        )

    def test_one_text_change_versions_only_that_series(self) -> None:
        self._import(
            [_row("110000000001", name="Before")],
            accepted_at="2026-07-20T01:13:47Z",
        )
        before_count = self.connection.execute(
            "SELECT COUNT(*) FROM claim_versions"
        ).fetchone()[0]
        classification_id = self.connection.execute(
            """
            SELECT versions.id
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.predicate = 'candidate_classification'
              AND versions.superseded_at IS NULL
            """
        ).fetchone()[0]

        later = self._import(
            [_row("110000000001", name="After")],
            retrieved_at="2026-08-10T12:00:00Z",
            accepted_at="2026-08-10T12:05:00Z",
            as_of="2026-08-01",
            archive_digit="b",
        )

        self.assertEqual(1, later.claims_created)
        self.assertEqual(1, later.prior_open_claims_closed_or_corrected)
        self.assertEqual(
            before_count + 2,
            self.connection.execute("SELECT COUNT(*) FROM claim_versions").fetchone()[0],
        )
        self.assertEqual(
            classification_id,
            self.connection.execute(
                """
                SELECT versions.id
                FROM claim_versions AS versions
                JOIN claim_series AS series ON series.id = versions.series_id
                WHERE series.predicate = 'candidate_classification'
                  AND versions.superseded_at IS NULL
                """
            ).fetchone()[0],
        )

    def test_qualifying_code_removal_versions_derived_dependency_lineage(self) -> None:
        self._import(
            [_row("110000000001", name="Dual qualifier", naics="334413", sic="3674")],
            accepted_at="2026-07-20T01:13:47Z",
        )
        before_count = self.connection.execute(
            "SELECT COUNT(*) FROM claim_versions"
        ).fetchone()[0]

        later = self._import(
            [_row("110000000001", name="Dual qualifier", naics="", sic="3674")],
            retrieved_at="2026-08-10T12:00:00Z",
            accepted_at="2026-08-10T12:05:00Z",
            as_of="2026-08-01",
            archive_digit="b",
        )

        self.assertEqual(1, later.claims_created)
        self.assertEqual(2, later.prior_open_claims_closed_or_corrected)
        self.assertEqual(
            before_count + 3,
            self.connection.execute("SELECT COUNT(*) FROM claim_versions").fetchone()[0],
        )
        derived = self.connection.execute(
            """
            SELECT versions.id, versions.valid_to
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.predicate = 'candidate_classification'
              AND versions.recorded_at = '2026-08-10T12:05:00Z'
            ORDER BY versions.valid_to IS NULL
            """
        ).fetchall()
        self.assertEqual(2, len(derived))
        closure, current = derived
        self.assertEqual("2026-08-01", closure["valid_to"])
        self.assertIsNone(current["valid_to"])
        closure_parents = {
            (row["predicate"], row["value"], row["valid_to"])
            for row in self.connection.execute(
                """
                SELECT parent_series.predicate,
                       scalar.text_value AS value,
                       parent.valid_to
                FROM claim_dependencies AS dependencies
                JOIN claim_versions AS parent
                  ON parent.id = dependencies.depends_on_claim_version_id
                JOIN claim_series AS parent_series
                  ON parent_series.id = parent.series_id
                JOIN scalar_values AS scalar
                  ON scalar.claim_version_id = parent.id
                WHERE dependencies.claim_version_id = ?
                """,
                (closure["id"],),
            )
        }
        self.assertEqual(
            {
                ("frs.naics_code", "334413", "2026-08-01"),
                ("frs.sic_code", "3674", None),
            },
            closure_parents,
        )
        current_parents = {
            (row["predicate"], row["value"])
            for row in self.connection.execute(
                """
                SELECT parent_series.predicate, scalar.text_value AS value
                FROM claim_dependencies AS dependencies
                JOIN claim_versions AS parent
                  ON parent.id = dependencies.depends_on_claim_version_id
                JOIN claim_series AS parent_series
                  ON parent_series.id = parent.series_id
                JOIN scalar_values AS scalar
                  ON scalar.claim_version_id = parent.id
                WHERE dependencies.claim_version_id = ?
                """,
                (current["id"],),
            )
        }
        self.assertEqual({("frs.sic_code", "3674")}, current_parents)

    def test_nonqualifying_code_change_does_not_version_classification(self) -> None:
        self._import(
            [_row("110000000001", name="Stable qualifier", naics="236220,334413")],
            accepted_at="2026-07-20T01:13:47Z",
        )
        classification_id = self.connection.execute(
            """
            SELECT versions.id
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.predicate = 'candidate_classification'
              AND versions.superseded_at IS NULL
            """
        ).fetchone()[0]

        later = self._import(
            [_row("110000000001", name="Stable qualifier", naics="334413,541512")],
            retrieved_at="2026-08-10T12:00:00Z",
            accepted_at="2026-08-10T12:05:00Z",
            as_of="2026-08-01",
            archive_digit="b",
        )

        self.assertEqual(1, later.claims_created)
        self.assertEqual(1, later.prior_open_claims_closed_or_corrected)
        self.assertEqual(
            classification_id,
            self.connection.execute(
                """
                SELECT versions.id
                FROM claim_versions AS versions
                JOIN claim_series AS series ON series.id = versions.series_id
                WHERE series.predicate = 'candidate_classification'
                  AND versions.superseded_at IS NULL
                """
            ).fetchone()[0],
        )

    def test_acceptance_clock_controls_knowledge_time_independently(self) -> None:
        accepted_at = "2026-07-20T02:00:00Z"
        result = self._import(
            [_row("110000000001", name="Accepted later")],
            accepted_at=accepted_at,
        )

        self.assertEqual(accepted_at, result.accepted_at)
        self.assertEqual(
            [],
            claim_records(
                self.connection,
                as_of=AS_OF_DATE,
                recorded_at="2026-07-20T01:59:59Z",
            ),
        )
        self.assertGreater(
            len(
                claim_records(
                    self.connection,
                    as_of=AS_OF_DATE,
                    recorded_at=accepted_at,
                )
            ),
            0,
        )
        before_acceptance = summarize(
            self.connection,
            as_of=AS_OF_DATE,
            recorded_at="2026-07-20T01:59:59Z",
        )
        at_acceptance = summarize(
            self.connection,
            as_of=AS_OF_DATE,
            recorded_at=accepted_at,
        )
        self.assertEqual(0, before_acceptance["source_document_count"])
        self.assertEqual(1, at_acceptance["source_document_count"])
        before_coverage = coverage_report(
            self.connection,
            as_of=AS_OF_DATE,
            recorded_at="2026-07-20T01:59:59Z",
        )
        at_coverage = coverage_report(
            self.connection,
            as_of=AS_OF_DATE,
            recorded_at=accepted_at,
        )
        self.assertIsNone(before_coverage["epa_frs_candidate_scope"])
        self.assertEqual(
            accepted_at,
            at_coverage["epa_frs_candidate_scope"]["database_accepted_at"],
        )
        clocks = self.connection.execute(
            """
            SELECT documents.retrieved_at, records.observed_at,
                   runs.started_at, runs.parameters_json
            FROM ingestion_runs AS runs
            JOIN source_documents AS documents
              ON documents.id = runs.input_document_id
            JOIN source_records AS records
              ON records.ingestion_run_id = runs.id
            WHERE runs.id = ?
            """,
            (result.ingestion_run_id,),
        ).fetchone()
        self.assertEqual(RETRIEVED_AT, clocks["retrieved_at"])
        self.assertEqual(RETRIEVED_AT, clocks["observed_at"])
        self.assertEqual(accepted_at, clocks["started_at"])
        parameters = json.loads(clocks["parameters_json"])
        self.assertEqual(accepted_at, parameters["accepted_at"])
        self.assertEqual(
            "explicit_operator_supplied",
            parameters["acceptance_timestamp_basis"],
        )

    def test_existing_run_replay_after_later_run_is_verification_only(self) -> None:
        self._import(
            [_row("110000000001", name="A")],
            accepted_at="2026-07-20T01:13:47Z",
        )
        second = self._import(
            [_row("110000000001", name="B")],
            retrieved_at="2026-08-10T12:00:00Z",
            accepted_at="2026-08-10T12:05:00Z",
            as_of="2026-08-01",
            archive_digit="b",
        )
        self._import(
            [_row("110000000001", name="C")],
            retrieved_at="2026-09-10T12:00:00Z",
            accepted_at="2026-09-10T12:05:00Z",
            as_of="2026-09-01",
            archive_digit="c",
        )
        before = "\n".join(self.connection.iterdump())

        replay = self._import(
            [_row("110000000001", name="B")],
            retrieved_at="2026-08-10T12:00:00Z",
            accepted_at="2026-08-10T12:05:00Z",
            as_of="2026-08-01",
            archive_digit="b",
        )

        self.assertEqual(second.ingestion_run_id, replay.ingestion_run_id)
        self.assertTrue(replay.replayed_existing_run)
        self.assertEqual(0, replay.source_records_created)
        self.assertEqual(0, replay.entities_created)
        self.assertEqual(0, replay.claim_series_created)
        self.assertEqual(0, replay.claims_created)
        self.assertEqual(0, replay.prior_open_claims_closed_or_corrected)
        self.assertEqual(before, "\n".join(self.connection.iterdump()))
        with self.assertRaisesRegex(ValueError, "accepted_at conflicts"):
            self._import(
                [_row("110000000001", name="B")],
                retrieved_at="2026-08-10T12:00:00Z",
                accepted_at="2026-08-10T12:06:00Z",
                as_of="2026-08-01",
                archive_digit="b",
            )
        self.assertEqual(before, "\n".join(self.connection.iterdump()))

    def test_repeat_is_idempotent(self) -> None:
        first_rows = [
            _row("110000000001", name="Original name"),
            _row("110000000002", name="Disappearing candidate", sic="3674"),
        ]
        first = self._import(first_rows)
        counts = {
            table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "source_documents",
                "ingestion_runs",
                "source_records",
                "entities",
                "claim_series",
                "claim_versions",
            )
        }
        repeated = self._import(first_rows)
        self.assertEqual(first.ingestion_run_id, repeated.ingestion_run_id)
        self.assertEqual(0, repeated.source_records_created)
        self.assertEqual(0, repeated.entities_created)
        self.assertEqual(0, repeated.claim_series_created)
        self.assertEqual(0, repeated.claims_created)
        self.assertEqual(0, repeated.prior_open_claims_closed_or_corrected)
        self.assertEqual(
            counts,
            {
                table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in counts
            },
        )

    def test_importer_upgrade_reprocesses_same_source_document_without_claim_churn(
        self,
    ) -> None:
        rows = [_row("110000000001", name="Stable candidate")]
        real_ensure = frs_ingest._ensure_source_document

        def ensure_legacy_document(connection, document):
            legacy_metadata = dict(document.metadata)
            legacy_metadata.update(
                {
                    "importer_version": "epa-frs-candidate-import-v1",
                    "selected_artifact_sha256": hashlib.sha256(
                        self.input_path.read_bytes()
                    ).hexdigest(),
                    "raw_retention": {
                        "retained_in_snapshot": False,
                        "reason": "legacy snapshot retained only the derivative",
                    },
                }
            )
            return real_ensure(
                connection, replace(document, metadata=legacy_metadata)
            )

        with (
            mock.patch(
                "semiconductor_atlas.ingest_epa_frs.IMPORTER_VERSION",
                "epa-frs-candidate-import-v1",
            ),
            mock.patch(
                "semiconductor_atlas.ingest_epa_frs._ensure_source_document",
                side_effect=ensure_legacy_document,
            ),
        ):
            first = self._import(
                rows,
                accepted_at="2026-07-20T02:00:00Z",
            )
        claim_count = self.connection.execute(
            "SELECT COUNT(*) FROM claim_versions"
        ).fetchone()[0]

        second = self._import(
            rows,
            accepted_at="2026-07-20T03:00:00Z",
        )

        self.assertNotEqual(first.ingestion_run_id, second.ingestion_run_id)
        self.assertEqual(first.source_document_id, second.source_document_id)
        self.assertEqual(1, self.connection.execute(
            "SELECT COUNT(*) FROM source_documents"
        ).fetchone()[0])
        self.assertEqual(2, self.connection.execute(
            "SELECT COUNT(*) FROM ingestion_runs"
        ).fetchone()[0])
        self.assertEqual(claim_count, self.connection.execute(
            "SELECT COUNT(*) FROM claim_versions"
        ).fetchone()[0])
        self.assertEqual(1, second.source_records_created)
        self.assertEqual(0, second.claims_created)
        self.assertEqual(claim_count, second.unchanged_claims_reused)
        self.assertEqual(
            ["epa-frs-candidate-import-v1", "epa-frs-candidate-import-v2"],
            [
                row[0]
                for row in self.connection.execute(
                    "SELECT code_version FROM ingestion_runs ORDER BY started_at"
                )
            ],
        )
        release_dir = Path(self.temporary_directory.name) / "upgrade-release"
        write_release(
            self.connection,
            release_dir,
            as_of=AS_OF_DATE,
            recorded_at="2026-07-20T03:00:00Z",
        )
        source_input = json.loads(
            (release_dir / "source_inputs.json").read_text(encoding="utf-8")
        )[0]
        self.assertEqual(2, len(source_input["processing_runs"]))
        self.assertEqual(
            ["epa-frs-candidate-import-v1", "epa-frs-candidate-import-v2"],
            [run["code_version"] for run in source_input["processing_runs"]],
        )
        self.assertTrue(
            source_input["processing_runs"][1]["parameters"]["raw_retention"][
                "retained_in_snapshot"
            ]
        )
        coverage = json.loads(
            (release_dir / "coverage.json").read_text(encoding="utf-8")
        )["epa_frs_candidate_scope"]
        self.assertEqual(
            "2026-07-20T02:00:00Z",
            coverage["source_document_first_accepted_at"],
        )
        self.assertEqual(
            "2026-07-20T03:00:00Z",
            coverage["processing_run_accepted_at"],
        )
        self.assertEqual(second.ingestion_run_id, coverage["processing_run_id"])
        self.assertEqual([], validate_semantics(self.connection))

    def test_complete_snapshot_closes_nonselection(self) -> None:
        self._import(
            [
                _row("110000000001", name="Original name"),
                _row("110000000002", name="Disappearing candidate", sic="3674"),
            ]
        )
        later = self._import(
            [_row("110000000001", name="Corrected name")],
            retrieved_at="2026-08-10T12:00:00Z",
            as_of="2026-08-01",
            archive_digit="b",
        )
        self.assertGreater(later.prior_open_claims_closed_or_corrected, 0)
        self.assertEqual(1, later.candidate_entities_no_longer_selected)
        self.assertEqual(0, later.entities_created)
        july_names = {
            claim["value"]["value"]
            for claim in claim_records(
                self.connection,
                as_of="2026-07-17",
                recorded_at="2026-08-10T12:00:00Z",
            )
            if claim["predicate"] == "name"
        }
        august_names = {
            claim["value"]["value"]
            for claim in claim_records(
                self.connection,
                as_of="2026-08-01",
                recorded_at="2026-08-10T12:00:00Z",
            )
            if claim["predicate"] == "name"
        }
        self.assertEqual({"Original name", "Disappearing candidate"}, july_names)
        self.assertEqual({"Corrected name"}, august_names)
        july_coverage = coverage_report(
            self.connection,
            as_of="2026-07-17",
            recorded_at="2026-08-10T12:00:00Z",
        )["epa_frs_candidate_scope"]
        august_coverage = coverage_report(
            self.connection,
            as_of="2026-08-01",
            recorded_at="2026-08-10T12:00:00Z",
        )["epa_frs_candidate_scope"]
        self.assertEqual("2026-07-01", july_coverage["data_as_of"])
        self.assertEqual(2, july_coverage["snapshot_candidate_count"])
        self.assertEqual(2, july_coverage["current_candidate_entity_count"])
        self.assertEqual("2026-08-01", august_coverage["data_as_of"])
        self.assertEqual(1, august_coverage["snapshot_candidate_count"])
        self.assertEqual(1, august_coverage["current_candidate_entity_count"])
        self.assertTrue(august_coverage["complete"])
        self.assertEqual(FRS_NAICS_CODES, tuple(august_coverage["naics_codes"]))
        self.assertEqual(FRS_SIC_CODES, tuple(august_coverage["sic_codes"]))
        disappeared_active = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            JOIN entities ON entities.id = series.subject_entity_id
            WHERE entities.stable_key = 'epa:frs:110000000002'
              AND versions.superseded_at IS NULL
              AND versions.valid_from <= '2026-08-01'
              AND (versions.valid_to IS NULL OR '2026-08-01' < versions.valid_to)
            """
        ).fetchone()[0]
        self.assertEqual(0, disappeared_active)
        history_text = " ".join(
            str(row[0] or "")
            for row in self.connection.execute(
                "SELECT notes FROM claim_versions"
            )
        ).casefold()
        self.assertNotIn("closed", history_text)
        self.assertNotIn("cancelled", history_text)
        self.assertEqual([], validate_semantics(self.connection))

    def test_partial_snapshot_updates_seen_entity_but_preserves_unseen_entity(self) -> None:
        self._import(
            [
                _row("110000000001", name="First"),
                _row("110000000002", name="Second"),
            ]
        )
        later = self._import(
            [_row("110000000001", name="First revised")],
            retrieved_at="2026-08-10T12:00:00Z",
            as_of="2026-08-01",
            complete=False,
            archive_digit="b",
        )
        self.assertEqual(0, later.candidate_entities_no_longer_selected)
        active_names = {
            claim["value"]["value"]
            for claim in claim_records(
                self.connection,
                as_of="2026-08-01",
                recorded_at="2026-08-10T12:00:00Z",
            )
            if claim["predicate"] == "name"
        }
        self.assertEqual({"First revised", "Second"}, active_names)

    def test_partial_snapshot_preserves_blank_seen_fields_and_qualifying_evidence(
        self,
    ) -> None:
        self._import(
            [
                _row(
                    "110000000001",
                    name="Prior name",
                    naics="334413",
                    sic="3674",
                )
            ]
        )
        before_classifications = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.predicate = 'candidate_classification'
            """
        ).fetchone()[0]

        later = self._import(
            [
                _row(
                    "110000000001",
                    name="",
                    naics="334413",
                    sic="",
                )
            ],
            retrieved_at="2026-08-10T12:00:00Z",
            accepted_at="2026-08-10T12:05:00Z",
            as_of="2026-08-01",
            complete=False,
            archive_digit="b",
        )

        self.assertEqual(0, later.prior_open_claims_closed_or_corrected)
        active = claim_records(
            self.connection,
            as_of="2026-08-01",
            recorded_at="2026-08-10T12:05:00Z",
        )
        self.assertIn(
            "Prior name",
            {
                claim["value"]["value"]
                for claim in active
                if claim["predicate"] == "name"
            },
        )
        self.assertIn(
            "3674",
            {
                claim["value"]["value"]
                for claim in active
                if claim["predicate"] == "frs.sic_code"
            },
        )
        self.assertEqual(
            before_classifications,
            self.connection.execute(
                """
                SELECT COUNT(*)
                FROM claim_versions AS versions
                JOIN claim_series AS series ON series.id = versions.series_id
                WHERE series.predicate = 'candidate_classification'
                """
            ).fetchone()[0],
        )

    def test_registry_ids_remain_separate_despite_same_name_and_address(self) -> None:
        result = self._import(
            [
                _row("110000000001", name="Same"),
                _row("110000000002", name="Same"),
            ]
        )
        self.assertEqual(2, len(set(result.site_entity_ids)))
        self.assertEqual(2, self.connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0])

    def test_same_world_date_is_a_transaction_correction_not_a_zero_length_interval(self) -> None:
        self._import(
            [
                _row("110000000001", name="Original"),
                _row("110000000002", name="Removed by correction"),
            ]
        )
        result = self._import(
            [_row("110000000001", name="Corrected")],
            retrieved_at="2026-07-21T12:00:00Z",
            as_of=AS_OF_DATE,
            archive_digit="b",
        )
        self.assertGreater(result.prior_open_claims_closed_or_corrected, 0)
        names = {
            claim["value"]["value"]
            for claim in claim_records(
                self.connection,
                as_of=AS_OF_DATE,
                recorded_at="2026-07-21T12:00:00Z",
            )
            if claim["predicate"] == "name"
        }
        self.assertEqual({"Corrected"}, names)
        self.assertEqual([], validate_semantics(self.connection))

    def test_out_of_order_import_rolls_back(self) -> None:
        self._import(
            [_row("110000000001", name="Later")],
            retrieved_at="2026-08-10T12:00:00Z",
            as_of="2026-08-01",
            archive_digit="b",
        )
        counts = {
            table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("source_documents", "ingestion_runs", "source_records", "claim_versions")
        }
        with self.assertRaisesRegex(ValueError, "retrieval order"):
            self._import(
                [_row("110000000001", name="Earlier")],
                retrieved_at="2026-07-20T01:12:47Z",
                as_of="2026-07-01",
                archive_digit="a",
            )
        self.assertEqual(
            counts,
            {
                table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in counts
            },
        )

    def test_acceptance_order_is_independent_of_later_source_retrieval(self) -> None:
        self._import(
            [_row("110000000001", name="Accepted late")],
            retrieved_at="2026-07-20T01:00:00Z",
            accepted_at="2026-07-22T01:00:00Z",
        )
        with self.assertRaisesRegex(ValueError, "database-acceptance order"):
            self._import(
                [_row("110000000001", name="Retrieved later, accepted earlier")],
                retrieved_at="2026-07-21T01:00:00Z",
                accepted_at="2026-07-21T02:00:00Z",
                archive_digit="b",
            )

    def test_source_retrieval_order_is_independent_of_later_acceptance(self) -> None:
        self._import(
            [_row("110000000001", name="Retrieved later first")],
            retrieved_at="2026-07-21T01:00:00Z",
            accepted_at="2026-07-21T02:00:00Z",
            archive_digit="b",
        )
        with self.assertRaisesRegex(ValueError, "source-retrieval order"):
            self._import(
                [_row("110000000001", name="Older source bytes")],
                retrieved_at="2026-07-20T01:00:00Z",
                accepted_at="2026-07-22T01:00:00Z",
                archive_digit="a",
            )

    def test_rejects_acceptance_before_retrieval(self) -> None:
        with self.assertRaisesRegex(ValueError, "accepted_at must not be earlier"):
            self._import(
                [_row("110000000001", name="Impossible clock")],
                retrieved_at="2026-07-20T02:00:00Z",
                accepted_at="2026-07-20T01:00:00Z",
            )
        self.assertEqual(
            0,
            self.connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0],
        )

    def test_later_retrieval_with_earlier_data_date_rolls_back(self) -> None:
        self._import(
            [_row("110000000001", name="August")],
            retrieved_at="2026-08-10T12:00:00Z",
            as_of="2026-08-01",
            archive_digit="b",
        )
        counts = {
            table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("source_documents", "ingestion_runs", "claim_versions")
        }
        with self.assertRaisesRegex(ValueError, "non-decreasing"):
            self._import(
                [_row("110000000001", name="July received late")],
                retrieved_at="2026-08-11T12:00:00Z",
                as_of="2026-07-01",
                archive_digit="c",
            )
        self.assertEqual(
            counts,
            {
                table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in counts
            },
        )

    def test_data_date_order_survives_complete_empty_snapshot(self) -> None:
        self._import([_row("110000000001", name="July")])
        self._import(
            [],
            retrieved_at="2026-08-10T12:00:00Z",
            as_of="2026-08-01",
            archive_digit="b",
        )
        counts = {
            table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("source_documents", "ingestion_runs", "claim_versions")
        }
        with self.assertRaisesRegex(ValueError, "non-decreasing"):
            self._import(
                [_row("110000000001", name="Backdated")],
                retrieved_at="2026-09-10T12:00:00Z",
                as_of="2026-07-15",
                archive_digit="c",
            )
        self.assertEqual(
            counts,
            {
                table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in counts
            },
        )

    def test_rejects_data_date_later_than_retrieval_date(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be later than retrieved_at"):
            self._import(
                [_row("110000000001", name="Future")],
                retrieved_at="2026-07-20T01:12:47Z",
                as_of="2099-01-01",
            )
        self.assertEqual(
            0,
            self.connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0],
        )

    def test_rejects_unapproved_license_url(self) -> None:
        raw = _candidate_bytes([_row("110000000001", name="Rights drift")])
        self.input_path.write_bytes(raw)
        scope = _scope(1)
        scope["rights"]["license_url"] = "https://example.com/"
        with self.assertRaisesRegex(ValueError, "approved license"):
            import_epa_frs_candidates(
                self.connection,
                self.input_path,
                RETRIEVED_AT,
                AS_OF_DATE,
                accepted_at=RETRIEVED_AT,
                scope_metadata=scope,
                snapshot_is_complete=True,
            )
        self.assertEqual(
            0,
            self.connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0],
        )


if __name__ == "__main__":
    unittest.main()
