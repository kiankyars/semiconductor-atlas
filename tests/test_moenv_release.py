from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas.adapters.moenv_ems import (
    MOENV_ATTRIBUTION,
    MOENV_DATASET_URL,
    MOENV_FILTER_VERSION,
    MOENV_INDUSTRY_LABELS,
    MOENV_LICENSE,
)
from semiconductor_atlas.coverage import TAIWAN_MOENV_FAMILY_KEY, coverage_report
from semiconductor_atlas.database import initialize
from semiconductor_atlas.models import (
    ClaimKind,
    ClaimSeries,
    ClaimVersion,
    Entity,
    EntityKind,
    EvidenceLink,
    GeometryValue,
    IngestionRun,
    IngestionStatus,
    ScalarType,
    ScalarValue,
    Source,
    SourceDocument,
    SourceFamily,
    ValueKind,
)
from semiconductor_atlas.moenv_snapshot import MOENV_COVERAGE
from semiconductor_atlas.release import write_release
from semiconductor_atlas.repository import (
    add_claim_series,
    add_entity,
    add_ingestion_run,
    add_source,
    add_source_document,
    add_source_family,
    insert_claim,
    stable_id,
)


AS_OF = "2026-07-20"
RETRIEVED_AT = "2026-07-20T06:57:50Z"
DATASET_UPDATED_AT = "2026-07-19T23:15:13Z"
ACCEPTED_AT = "2026-07-20T07:00:00Z"
RECORDED_AT = "2026-07-20T08:00:00Z"


class MOENVReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _moenv_connection(self):
        connection, _ = initialize(":memory:")
        family_id = stable_id("source-family", TAIWAN_MOENV_FAMILY_KEY)
        source_key = "taiwan-moenv-ems:ems_s_01"
        source_id = stable_id("source", source_key)
        document_id = stable_id("source-document", source_id, "fixture")
        run_id = stable_id("ingestion-run", document_id, "fixture")
        exact_code_counts = {"2611": 2, "2612": 0, "2613": 1}
        summary_counts = {
            "upstream_row_count": 8,
            "industry_group_row_count": 5,
            "raw_matching_row_count": 4,
            "deduplicated_variant_count": 3,
            "facility_count": 2,
            "conflicting_facility_count": 1,
            "current_regulation_count": 1,
            "valid_coordinate_count": 1,
            "exact_industry_code_variant_counts": exact_code_counts,
        }
        common_metadata = {
            "attribution": MOENV_ATTRIBUTION,
            "coverage": MOENV_COVERAGE,
            "dataset_updated_at": DATASET_UPDATED_AT,
            "dataset_updated_at_basis": (
                "official_dataset_page_displayed_asia_taipei"
            ),
            "filter_version": MOENV_FILTER_VERSION,
            "industry_group": "261",
            "exact_industry_codes": dict(sorted(MOENV_INDUSTRY_LABELS.items())),
            "retrieval_timestamp_basis": "upstream_download_completion",
            **summary_counts,
        }
        add_source_family(
            connection,
            SourceFamily(
                family_id,
                TAIWAN_MOENV_FAMILY_KEY,
                "Taiwan MOENV EMS_S_01",
                ACCEPTED_AT,
            ),
        )
        add_source(
            connection,
            Source(
                source_id,
                family_id,
                source_key,
                "Taiwan MOENV EMS_S_01 semiconductor candidates",
                "Taiwan Ministry of Environment",
                MOENV_DATASET_URL,
                ACCEPTED_AT,
                license=MOENV_LICENSE,
            ),
        )
        add_source_document(
            connection,
            SourceDocument(
                document_id,
                source_id,
                MOENV_DATASET_URL,
                "Taiwan MOENV EMS_S_01 candidate fixture",
                RETRIEVED_AT,
                "a" * 64,
                published_at=DATASET_UPDATED_AT,
                media_type="application/x-ndjson",
                license=MOENV_LICENSE,
                metadata=common_metadata,
            ),
        )
        add_ingestion_run(
            connection,
            IngestionRun(
                run_id,
                source_id,
                ACCEPTED_AT,
                status=IngestionStatus.SUCCEEDED,
                completed_at="2026-07-20T07:00:01Z",
                code_version="fixture-v1",
                input_document_id=document_id,
                parameters={
                    **common_metadata,
                    "acceptance_timestamp_basis": "explicit_operator_supplied",
                    "complete_refresh": True,
                    "raw_archive": {
                        "bytes": 10,
                        "path": "raw/sha256/example.zip",
                        "sha256": "b" * 64,
                    },
                    "source_retrieved_at": RETRIEVED_AT,
                    "variant_count": 3,
                },
            ),
        )

        for index, emsno in enumerate(("A0000001", "A0000002")):
            entity_key = f"taiwan-moenv-ems:ems_s_01:{emsno}"
            entity_id = stable_id("entity", entity_key)
            add_entity(
                connection,
                Entity(
                    entity_id,
                    EntityKind.FACILITY,
                    entity_key,
                    ACCEPTED_AT,
                    display_name=f"MOENV candidate {index + 1}",
                    created_by_run_id=run_id,
                ),
            )
            if index == 0:
                predicate = "geometry"
                value_kind = ValueKind.GEOMETRY
                value = GeometryValue(
                    {"type": "Point", "coordinates": [121.0, 24.8]},
                    "EPSG:4326",
                )
            else:
                predicate = "name"
                value_kind = ValueKind.SCALAR
                value = ScalarValue(ScalarType.TEXT, "MOENV candidate 2")
            series = ClaimSeries(
                stable_id("claim-series", entity_id, predicate),
                entity_id,
                f"{entity_key}:claim:{predicate}",
                predicate,
                value_kind,
                ACCEPTED_AT,
            )
            add_claim_series(connection, series)
            insert_claim(
                connection,
                ClaimVersion(
                    stable_id("claim-version", series.id),
                    series.id,
                    AS_OF,
                    ACCEPTED_AT,
                    ClaimKind.SOURCE_STATEMENT,
                    "moenv_release_fixture",
                    1.0,
                    created_by_run_id=run_id,
                ),
                value,
                evidence=[EvidenceLink(document_id)],
            )
        return connection

    def test_moenv_scope_metrics_gaps_methodology_and_attribution(self) -> None:
        connection = self._moenv_connection()
        try:
            report = coverage_report(
                connection,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
            self.assertEqual({"taiwan_moenv": 2}, report["entity_namespace_counts"])
            scope = report["taiwan_moenv_candidate_scope"]
            self.assertTrue(scope["metadata_metrics_complete"])
            self.assertTrue(scope["metadata_metrics_consistent"])
            self.assertTrue(scope["exact_filter_complete_within_archived_package"])
            self.assertTrue(scope["source_assertion_interval_closure_enabled"])
            self.assertEqual(MOENV_COVERAGE, scope["coverage"])
            self.assertEqual(MOENV_FILTER_VERSION, scope["filter_version"])
            self.assertEqual("261", scope["industry_group"])
            self.assertEqual(dict(sorted(MOENV_INDUSTRY_LABELS.items())), scope[
                "exact_industry_codes"
            ])
            self.assertEqual(8, scope["upstream_row_count"])
            self.assertEqual(5, scope["industry_group_row_count"])
            self.assertEqual(4, scope["raw_matching_row_count"])
            self.assertEqual(3, scope["deduplicated_variant_count"])
            self.assertEqual(2, scope["facility_count"])
            self.assertEqual(2, scope["snapshot_facility_count"])
            self.assertEqual(1, scope["current_regulation_count"])
            self.assertEqual(1, scope["current_regulation_facility_count"])
            self.assertEqual(1, scope["valid_coordinate_count"])
            self.assertEqual(1, scope["valid_coordinate_facility_count"])
            self.assertEqual(1, scope["conflicting_facility_count"])
            self.assertEqual(
                {"2611": 2, "2612": 0, "2613": 1},
                scope["exact_industry_code_variant_counts"],
            )
            self.assertEqual(2, scope["current_candidate_entity_count"])
            self.assertEqual(1, scope["current_materialized_geometry_count"])
            gaps = "\n".join(report["known_gaps"])
            for phrase in (
                "registry membership does not establish facility operation",
                "production, ownership, operator relationships, or capacity",
                "release-from-environmental-control dates",
                "not a national semiconductor facility census",
            ):
                self.assertIn(phrase, gaps)

            output = self.root / "moenv-release"
            write_release(
                connection,
                output,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
            self.assertEqual(
                MOENV_ATTRIBUTION + "\n",
                (output / "ATTRIBUTION.txt").read_text(encoding="utf-8"),
            )
            readme = (output / "README.md").read_text(encoding="utf-8")
            for phrase in (
                "source-native environmental-control registry candidates",
                "exact industry codes 2611, 2612, and 2613",
                "neither operation, production, ownership, facility closure, nor capacity",
                "any source environmental-control flag equals 1",
                "not an operating-status estimate",
                "not a national semiconductor facility census",
                "Valid invariant WGS84 points alone become geometry",
                "conflicting source variants remain parallel claims",
            ):
                self.assertIn(phrase, readme)
        finally:
            connection.close()

    def test_non_moenv_release_retains_schema_v4_baseline_bytes(self) -> None:
        connection, _ = initialize(":memory:", target_version=4)
        output = self.root / "legacy-release"
        try:
            write_release(
                connection,
                output,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
            expected_sha256 = {
                "coverage.json": (
                    "2efef72df847b986a500897ae558ce343760f383914b6bb0a9a33c6133f511a0"
                ),
                "README.md": (
                    "86557561aa2ef644332eaa56e5f8aa9fa7be629bd19ac5d3b9b8f8f90032ef19"
                ),
                "ATTRIBUTION.txt": (
                    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
                ),
                "manifest.json": (
                    "2c5c27b605dc24ead75f2ebf2d658c6d84559637a8e19a33ff279bbe7ab626ce"
                ),
            }
            for name, expected in expected_sha256.items():
                self.assertEqual(
                    expected,
                    hashlib.sha256((output / name).read_bytes()).hexdigest(),
                    name,
                )
            coverage = json.loads(
                (output / "coverage.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("taiwan_moenv_candidate_scope", coverage)
            self.assertNotIn(
                "Taiwan MOENV",
                (output / "README.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(b"", (output / "ATTRIBUTION.txt").read_bytes())
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
