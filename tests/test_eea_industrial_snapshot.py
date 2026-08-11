from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas.eea_industrial_snapshot import (
    EEA_COVERAGE,
    EEA_EXTRACTION_IMAGE_ID,
    EEA_FILTER_VERSION,
    EEA_INVENTORY_TABLES,
    EEA_SCOPE,
    EEA_SPATIAL_DOI_URL,
    _SnapshotReader,
    _artifact_map,
    _empty_scan_for_policy,
    _manifest,
    _source_clocks,
    _validate_clocks,
)


def _clocks() -> dict[str, str]:
    return _source_clocks(
        download_started_at="2026-07-20T10:35:04.194155Z",
        retrieved_at="2026-07-20T10:37:16.510781Z",
        metadata_retrieved_at="2026-07-20T10:51:14Z",
        primary_extraction_started_at="2026-07-20T11:04:46Z",
        primary_extraction_completed_at="2026-07-20T11:04:59Z",
        independent_extraction_started_at="2026-07-20T11:05:00Z",
        independent_extraction_completed_at="2026-07-20T11:05:14Z",
        candidate_generated_at="2026-07-20T16:26:42Z",
        accepted_at="2026-07-20T16:30:00Z",
    )


class EEAIndustrialSnapshotTests(unittest.TestCase):
    def test_manifest_policy_separates_clocks_scope_crs_and_capacity(self) -> None:
        clocks = _clocks()
        manifest = _manifest(
            scan=_empty_scan_for_policy(),
            candidate_raw=b"",
            artifacts=(),
            clocks=clocks,
        )
        scope = manifest["source_scopes"][EEA_SCOPE]

        self.assertEqual(EEA_COVERAGE, scope["coverage"])
        self.assertTrue(
            scope["completeness"][
                "candidate_filter_complete_within_pinned_accdb_reported_population"
            ]
        )
        self.assertFalse(
            scope["completeness"][
                "european_semiconductor_facility_census_complete"
            ]
        )
        self.assertEqual(
            EEA_SPATIAL_DOI_URL,
            scope["coordinate_policy"]["spatial_companion_doi_url"],
        )
        self.assertEqual(
            "EPSG:4326",
            scope["coordinate_policy"]["spatial_companion_declared_crs"],
        )
        self.assertFalse(scope["coordinate_policy"]["geometry_emitted"])
        self.assertFalse(scope["capacity_policy"]["capacity_claims_emitted"])
        self.assertEqual(
            0, scope["capacity_policy"]["production_volume_table_row_count"]
        )
        self.assertEqual(
            ["ME", "NO", "SK"],
            scope["geographic_coverage_discrepancy"][
                "pdf_list_omits_observed_country_codes"
            ],
        )
        self.assertEqual(
            EEA_EXTRACTION_IMAGE_ID,
            scope["extraction"]["toolchain"]["container_image_id"],
        )
        self.assertEqual(33, len(EEA_INVENTORY_TABLES))
        self.assertFalse(
            scope["acquisition"][
                "credential_or_ephemeral_access_material_retained"
            ]
        )
        self.assertEqual(EEA_FILTER_VERSION, scope["filter"]["filter_version"])
        self.assertEqual(
            clocks,
            _validate_clocks(
                clocks,
                clocks["download_completed_at"],
                clocks["accepted_at"],
            ),
        )

    def test_clock_inversion_is_rejected(self) -> None:
        clocks = _clocks()
        clocks["metadata_retrieved_at"] = "2026-07-20T10:30:00Z"
        with self.assertRaisesRegex(ValueError, "not monotonically ordered"):
            _validate_clocks(
                clocks,
                clocks["download_completed_at"],
                clocks["accepted_at"],
            )

    def test_input_urls_cannot_retain_queries_or_credentials(self) -> None:
        entry = {
            "artifact_kind": "fixture",
            "bytes": 0,
            "content_type": "application/octet-stream",
            "filter_version": None,
            "path": "fixture.bin",
            "record_count": None,
            "record_type": "fixture",
            "sha256": "0" * 64,
            "url": "https://example.test/source?access=secret",
        }
        with self.assertRaisesRegex(ValueError, "credential-free"):
            _artifact_map([entry])

    def test_snapshot_reader_rejects_symlink_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.txt"
            target.write_text("fixture", encoding="utf-8")
            (root / "link.txt").symlink_to(target)
            with _SnapshotReader(root) as reader:
                with self.assertRaisesRegex(ValueError, "contains a symlink"):
                    reader.inventory()


if __name__ == "__main__":
    unittest.main()
