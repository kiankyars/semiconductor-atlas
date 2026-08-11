from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas.adapters.taiwan_factory_registry import (
    TAIWAN_FACTORY_ATTRIBUTION,
    TAIWAN_FACTORY_FILTER_VERSION,
)
from semiconductor_atlas.coverage import coverage_report
from semiconductor_atlas.database import initialize
from semiconductor_atlas.ingest_taiwan_factory import import_taiwan_factory_candidates
from semiconductor_atlas.release import write_release
from semiconductor_atlas.taiwan_factory_snapshot import TAIWAN_FACTORY_COVERAGE
from tests.test_ingest_taiwan_factory import ACCEPTED_AT, _row, _snapshot


AS_OF = "2026-07-20"
RECORDED_AT = "2026-07-20T10:00:00Z"
BASELINE_RECORDED_AT = "2026-07-20T08:00:00Z"


class TaiwanFactoryReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_scope_namespace_attribution_privacy_and_methodology(self) -> None:
        connection, _ = initialize(":memory:")
        try:
            snapshot = _snapshot(self.root, "snapshot", [_row()])
            import_taiwan_factory_candidates(
                connection,
                snapshot,
                accepted_at=ACCEPTED_AT,
                complete_refresh=True,
            )
            report = coverage_report(
                connection,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
            self.assertEqual(
                {"taiwan_ida_factory": 1}, report["entity_namespace_counts"]
            )
            scope = report["taiwan_factory_candidate_scope"]
            self.assertTrue(scope["metadata_metrics_complete"])
            self.assertTrue(scope["metadata_metrics_consistent"])
            self.assertTrue(scope["exact_filter_complete_within_archived_package"])
            self.assertTrue(scope["source_assertion_interval_closure_enabled"])
            self.assertEqual(TAIWAN_FACTORY_COVERAGE, scope["coverage"])
            self.assertEqual(TAIWAN_FACTORY_FILTER_VERSION, scope["filter_version"])
            self.assertEqual("261半導體", scope["exact_principal_product_token"])
            self.assertEqual(1, scope["upstream_row_count"])
            self.assertEqual(1, scope["raw_matching_row_count"])
            self.assertEqual(1, scope["snapshot_candidate_count"])
            self.assertEqual(1, scope["distinct_unified_business_number_count"])
            self.assertEqual({"生產中": 1}, scope["registration_status_counts"])
            gaps = "\n".join(report["known_gaps"])
            for phrase in (
                "administrative source field",
                "does not establish observed operation",
                "closes only a prior source-assertion interval",
                "not a complete Taiwan semiconductor facility census",
            ):
                self.assertIn(phrase, gaps)

            output = self.root / "release"
            write_release(
                connection,
                output,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
            self.assertEqual(
                TAIWAN_FACTORY_ATTRIBUTION + "\n",
                (output / "ATTRIBUTION.txt").read_text(encoding="utf-8"),
            )
            readme = (output / "README.md").read_text(encoding="utf-8")
            for phrase in (
                "source-native administrative registry candidates",
                "exact token 261半導體",
                "responsible person's name",
                "omitted from the privacy-minimized derivative",
                "neither observed operation, output, lifecycle, ownership, capacity",
                "partial refresh omissions remain open",
                "no absence becomes facility-closure evidence",
            ):
                self.assertIn(phrase, readme)
            release_text = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in output.iterdir()
                if path.is_file()
            )
            self.assertNotIn("不應進入資料庫的人名", release_text)
            second_output = self.root / "release-replay"
            write_release(
                connection,
                second_output,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
            self.assertEqual(
                {path.name: path.read_bytes() for path in output.iterdir()},
                {path.name: path.read_bytes() for path in second_output.iterdir()},
            )
        finally:
            connection.close()

    def test_no_factory_source_retains_schema_v4_baseline_bytes(self) -> None:
        connection, _ = initialize(":memory:")
        output = self.root / "empty-release"
        try:
            write_release(
                connection,
                output,
                as_of=AS_OF,
                recorded_at=BASELINE_RECORDED_AT,
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
            self.assertNotIn("taiwan_factory_candidate_scope", coverage)
            self.assertNotIn(
                "Taiwan registered-factory",
                (output / "README.md").read_text(encoding="utf-8"),
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
