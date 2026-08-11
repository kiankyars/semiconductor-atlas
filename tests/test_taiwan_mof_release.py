from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas.adapters.taiwan_mof_tax_registry import (
    TAIWAN_MOF_ATTRIBUTION,
    TAIWAN_MOF_FILTER_VERSION,
)
from semiconductor_atlas.coverage import coverage_report
from semiconductor_atlas.database import initialize
from semiconductor_atlas.ingest_taiwan_mof import import_taiwan_mof_snapshot
from semiconductor_atlas.release import write_release
from semiconductor_atlas.taiwan_mof_snapshot import TAIWAN_MOF_COVERAGE
from tests._taiwan_mof_fixtures import create_source_snapshots
from tests.test_ingest_taiwan_mof import ACCEPTED_AT, _row, _snapshot


AS_OF = "2026-07-20"
RECORDED_AT = "2026-07-20T10:00:00Z"


class TaiwanMOFReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.moenv, self.factory = create_source_snapshots(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_scope_namespace_attribution_and_methodology(self) -> None:
        connection, _ = initialize(":memory:")
        try:
            snapshot = _snapshot(
                self.root,
                "snapshot",
                [_row("00123456")],
                moenv=self.moenv,
                factory=self.factory,
            )
            import_taiwan_mof_snapshot(
                connection,
                snapshot,
                moenv_snapshot_dir=self.moenv,
                factory_snapshot_dir=self.factory,
                accepted_at=ACCEPTED_AT,
                complete_refresh=True,
            )
            report = coverage_report(
                connection,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
            self.assertEqual({"taiwan_mof_tax": 1}, report["entity_namespace_counts"])
            scope = report["taiwan_mof_tax_registration_scope"]
            self.assertTrue(scope["metadata_metrics_complete"])
            self.assertTrue(scope["metadata_metrics_consistent"])
            self.assertTrue(
                scope["exact_allowlist_filter_complete_within_archived_package"]
            )
            self.assertTrue(scope["source_assertion_interval_closure_enabled"])
            self.assertTrue(scope["active_tax_registrations_only"])
            self.assertEqual(TAIWAN_MOF_COVERAGE, scope["coverage"])
            self.assertEqual(TAIWAN_MOF_FILTER_VERSION, scope["filter_version"])
            self.assertEqual(3, scope["allowlist_count"])
            self.assertEqual(1, scope["snapshot_matched_count"])
            self.assertEqual(2, scope["snapshot_missing_count"])
            self.assertEqual(
                {"股份有限公司": 1}, scope["matched_organization_type_counts"]
            )
            self.assertEqual(1, scope["active_tax_registration_row_count"])
            self.assertEqual(1, scope["current_tax_unit_entity_count"])
            self.assertIn("sources", scope["allowlist"]["derivation"])
            self.assertIsNotNone(scope["matched_derivative"])
            self.assertIsNotNone(scope["raw_retention"])
            gaps = "\n".join(report["known_gaps"])
            for phrase in (
                "not a legal-company, parent, owner, operator, or facility census",
                "head-office UBN is retained only as a contextual source scalar",
                "does not establish legal closure, tax inactivity, facility closure",
                "exact UBN allowlist is derived from the accepted MOENV",
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
                TAIWAN_MOF_ATTRIBUTION + "\n",
                (output / "ATTRIBUTION.txt").read_text(encoding="utf-8"),
            )
            readme = (output / "README.md").read_text(encoding="utf-8")
            for phrase in (
                "source-native active tax-registration organizations",
                "exact UBN allowlist re-derived from the accepted MOENV",
                "head-office UBN remains contextual source data",
                "Capital and invoice-use fields are omitted",
                "partial omissions remain open",
                "absence never establishes legal closure",
                "raw archive remain bound by content hash",
            ):
                self.assertIn(phrase, readme)
        finally:
            connection.close()

    def test_no_mof_source_omits_conditional_release_content(self) -> None:
        connection, _ = initialize(":memory:")
        output = self.root / "empty-release"
        try:
            report = coverage_report(
                connection,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
            self.assertNotIn("taiwan_mof_tax_registration_scope", report)
            self.assertFalse(any("Taiwan MOF" in gap for gap in report["known_gaps"]))
            write_release(
                connection,
                output,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
            self.assertNotIn(
                "Taiwan MOF BGMOPEN1",
                (output / "README.md").read_text(encoding="utf-8"),
            )
            self.assertNotIn(
                TAIWAN_MOF_ATTRIBUTION,
                (output / "ATTRIBUTION.txt").read_text(encoding="utf-8"),
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
