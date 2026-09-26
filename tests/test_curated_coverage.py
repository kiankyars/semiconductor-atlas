from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import report_curated_coverage as cli
from semiconductor_atlas import curated_coverage as coverage
from semiconductor_atlas import curated_review as review
from tests import test_curated_capture as fixtures


class CuratedCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capture = fixtures.CuratedCaptureTests()
        self.capture.setUp()
        self.addCleanup(self.capture.doCleanups)
        self.root = self.capture.root
        self.repository = Path(__file__).resolve().parents[1]
        self.catalog = json.loads((self.repository / "acquisition_plans/ai_critical_coverage_v1.json").read_text())
        self.catalog["recorded_at"] = "2026-09-07T02:00:00Z"
        self.catalog["freshness_seconds"] = 3600
        self.capture.plan["checked_facility_key"] = "amkor:peoria-advanced-packaging-project"
        self.capture.plan_path.write_text(json.dumps(self.capture.plan))
        self.catalog["facilities"][5]["plan"] = {
            "path": "plans/plan.json", "sha256": coverage._sha(self.capture.plan_path.read_bytes())}
        baseline = self.root / "baselines/ai_critical_manufacturing_v1.json"
        baseline.parent.mkdir()
        baseline.write_bytes((self.repository / self.catalog["baseline"]["path"]).read_bytes())
        self.catalog_path = self.root / "plans/coverage.json"
        self.write_catalog()
        self.queue = self.root / "queue.sqlite"
        review.initialize_queue(self.queue)
        self.clock = "2026-09-07T03:10:00Z"
        now = patch.object(review, "_now", side_effect=lambda: self.clock)
        now.start()
        self.addCleanup(now.stop)

    def write_catalog(self) -> None:
        self.catalog_path.write_text(json.dumps(self.catalog))

    def report(self, at: str = "2026-09-07T03:30:00Z") -> dict:
        return coverage.coverage_report(self.catalog_path, self.queue, as_of=at, repository_root=self.root)

    def import_capture(self, name: str = "first", prior: Path | None = None) -> Path:
        root, _ = self.capture.run_capture(name, prior)
        review.import_capture(self.queue, root)
        return root

    def test_empty_queue_retains_whole_cohort_and_source_family_denominators(self) -> None:
        report = self.report()
        self.assertEqual(7, report["summary"]["cohort_facility_count"])
        self.assertEqual(6, report["summary"]["unmonitored_facility_count"])
        self.assertEqual(1, report["summary"]["never_observed_document_count"])
        self.assertEqual(0, report["summary"]["pending_count"])
        self.assertEqual({"US", "SG", "KR", "TW"}, {row["country_code"] for row in report["groups"]})
        self.assertEqual(9, sum(row["baseline_document_count"] for row in report["groups"]))
        self.assertTrue(report["attention_required"])
        self.assertFalse(report["coverage_complete"])

    def test_dismissed_queue_does_not_hide_six_unmonitored_facilities(self) -> None:
        self.import_capture()
        candidate = review.queue_report(self.queue)["candidates"][0]
        review.record_decision(self.queue, candidate["id"], action="dismiss", reviewer="fixture",
                               reason="Review complete", expected_event_id=candidate["last_event_id"])
        report = self.report()
        self.assertEqual(0, report["summary"]["pending_count"])
        self.assertEqual(1, report["summary"]["fresh_document_count"])
        self.assertEqual(6, report["summary"]["unmonitored_facility_count"])
        self.assertTrue(report["attention_required"])
        self.assertFalse(report["facilities"][5]["attention_required"])
        self.assertTrue(all(row["unmonitored_reason"] for row in report["facilities"] if row["plan_id"] is None))

    def test_late_queue_admission_cannot_backdate_coverage(self) -> None:
        self.clock = "2026-09-07T04:00:00Z"
        self.import_capture()
        before = self.report("2026-09-07T03:30:00Z")
        after = self.report("2026-09-07T04:00:00Z")
        self.assertEqual(0, before["queue_verified_capture_count"])
        self.assertEqual(1, before["summary"]["never_observed_document_count"])
        self.assertEqual(1, after["queue_verified_capture_count"])
        self.assertEqual(1, after["summary"]["stale_document_count"])
        self.assertEqual(3600, after["facilities"][5]["documents"][0]["eligible_age_seconds"])

    def test_failed_check_retains_prior_age_and_does_not_become_healthy(self) -> None:
        first = self.import_capture()
        self.capture.clock = "2026-09-07T03:20:00Z"
        self.clock = "2026-09-07T03:25:00Z"
        self.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        self.import_capture("failed", first)
        report = self.report()
        doc = report["facilities"][5]["documents"][0]
        self.assertEqual("fresh", doc["freshness"])
        self.assertEqual("requires_attention", doc["check_health"])
        self.assertEqual(1800, doc["eligible_age_seconds"])
        self.assertEqual(2, doc["attempt_count"])
        self.assertEqual(1, report["summary"]["unhealthy_document_count"])
        self.assertEqual(1, self.report("2026-09-07T04:00:00Z")["summary"]["stale_document_count"])

    def test_policy_block_is_an_assessment_not_a_document_attempt(self) -> None:
        self.capture.bodies["rights"] += b"<p>New terms</p>"
        self.import_capture()
        doc = self.report()["facilities"][5]["documents"][0]
        self.assertEqual("never_observed", doc["freshness"])
        self.assertEqual(0, doc["attempt_count"])
        self.assertEqual(1, doc["policy_blocked_count"])
        self.assertIsNone(doc["latest_checks"][0]["response_finished_at"])
        self.assertIsNotNone(doc["latest_checks"][0]["assessment_at"])

    def test_expired_plan_requires_attention_even_with_recent_success(self) -> None:
        self.capture.plan["expires_at"] = "2026-09-07T03:30:00Z"
        self.capture.plan_path.write_text(json.dumps(self.capture.plan))
        self.catalog["facilities"][5]["plan"]["sha256"] = coverage._sha(self.capture.plan_path.read_bytes())
        self.write_catalog()
        self.import_capture()
        report = self.report()
        self.assertEqual(1, report["summary"]["fresh_document_count"])
        self.assertEqual(1, report["summary"]["expired_plan_count"])
        self.assertEqual("review_expired", report["facilities"][5]["plan_state"])
        self.assertTrue(report["facilities"][5]["attention_required"])

    def test_wrong_plan_capture_is_not_current_configured_coverage(self) -> None:
        self.capture.plan["plan_id"] = "other-reviewed-plan"
        self.import_capture()
        # Restore the separately pinned active plan; the packet keeps the other plan.
        self.capture.plan["plan_id"] = "fixture"
        self.capture.plan_path.write_text(json.dumps(self.capture.plan))
        report = self.report()
        self.assertEqual(1, report["queue_verified_capture_count"])
        self.assertEqual(0, report["summary"]["fresh_document_count"])
        self.assertEqual(1, report["summary"]["never_observed_document_count"])

    def test_invalid_catalog_denominator_bindings_and_cutoffs_fail_closed(self) -> None:
        original = copy.deepcopy(self.catalog)
        for mutation in (
            lambda value: value["facilities"].pop(),
            lambda value: value["facilities"].reverse(),
            lambda value: value["baseline"].update(sha256="0" * 64),
            lambda value: value["facilities"][5]["plan"].update(sha256="0" * 64),
            lambda value: value.update(freshness_seconds=True),
            lambda value: value.update(recorded_at="2026-09-07T05:00:00Z"),
        ):
            self.catalog = copy.deepcopy(original)
            mutation(self.catalog)
            self.write_catalog()
            with self.assertRaises(ValueError):
                self.report()

    def test_capture_tampering_fails_and_output_is_deterministic_read_only(self) -> None:
        packet = self.import_capture()
        queue_bytes = self.queue.read_bytes()
        first = self.report()
        self.assertEqual(first, self.report())
        self.assertEqual(queue_bytes, self.queue.read_bytes())
        (packet / "responses/document.body").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.report()

    def test_equal_time_conflicting_versions_remain_visible(self) -> None:
        self.import_capture()
        self.capture.bodies["document"] += b"<p>Additional project statement</p>"
        self.import_capture("parallel")
        doc = self.report()["facilities"][5]["documents"][0]
        self.assertEqual("fresh", doc["freshness"])
        self.assertEqual("conflicting_observations", doc["check_health"])
        self.assertEqual(2, len(doc["last_eligible_observations"]))
        self.assertTrue(doc["attention_required"])

    def test_latest_response_not_import_order_governs_freshness(self) -> None:
        first, _ = self.capture.run_capture("older")
        self.capture.clock = "2026-09-07T03:20:00Z"
        newer, _ = self.capture.run_capture("newer", first)
        self.clock = "2026-09-07T03:25:00Z"
        review.import_capture(self.queue, newer)
        self.clock = "2026-09-07T03:26:00Z"
        review.import_capture(self.queue, first)
        doc = self.report()["facilities"][5]["documents"][0]
        self.assertEqual("2026-09-07T03:20:00Z", doc["last_eligible_response_at"])
        self.assertEqual(600, doc["eligible_age_seconds"])

    def test_cli_exports_exact_report_without_overwriting(self) -> None:
        self.import_capture()
        output_path = self.root / "coverage-report.json"
        stdout = io.StringIO()
        arguments = ["report", "--catalog", str(self.catalog_path), "--database", str(self.queue),
                     "--repository-root", str(self.root), "--as-of", "2026-09-07T03:30:00Z",
                     "--output", str(output_path)]
        with patch("sys.argv", arguments), redirect_stdout(stdout):
            cli.main()
        self.assertEqual(self.report(), json.loads(stdout.getvalue()))
        self.assertEqual(self.report(), json.loads(output_path.read_bytes()))
        original = output_path.read_bytes()
        with patch("sys.argv", arguments), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main()
        self.assertEqual(original, output_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
