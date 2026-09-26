from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from scripts import capture_curated_sources as capture_cli
from scripts import review_curated_sources as review_cli
from semiconductor_atlas import curated_review as review
from tests import test_curated_capture as capture_fixtures


class CuratedReviewCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.capture = capture_fixtures.CuratedCaptureTests()
        self.capture.setUp()
        self.addCleanup(self.capture.doCleanups)
        self.queue = self.capture.root / "queue.sqlite"
        now = patch.object(review, "_now", return_value="2026-09-07T04:00:00Z")
        now.start()
        self.addCleanup(now.stop)

    def invoke(self, module, *arguments):
        output = io.StringIO()
        with patch("sys.argv", ["command", *map(str, arguments)]), redirect_stdout(output):
            module.main()
        return json.loads(output.getvalue())

    def test_queue_cli_import_report_export_and_verify(self) -> None:
        self.invoke(review_cli, "init", "--database", self.queue)
        root, _ = self.capture.run_capture()
        imported = self.invoke(review_cli, "import", "--database", self.queue, "--capture", root)
        self.assertTrue(imported["imported"])
        report = self.invoke(review_cli, "report", "--database", self.queue)
        self.assertEqual(1, report["pending_count"])
        exported = self.invoke(review_cli, "export", "--database", self.queue)
        self.assertEqual(1, len(exported["events"]))
        verified = self.invoke(review_cli, "verify", "--database", self.queue)
        self.assertEqual(1, verified["verified_capture_count"])

    def test_capture_option_imports_completed_packet(self) -> None:
        review.initialize_queue(self.queue)
        root, captured = self.capture.run_capture()
        with patch.object(capture_cli, "capture_sources", return_value=captured):
            result = self.invoke(capture_cli, "capture", "--plan", self.capture.plan_path,
                                 "--output", root, "--review-queue", self.queue)
        self.assertEqual(captured, result["capture"])
        self.assertTrue(result["queue_import"]["imported"])
        self.assertEqual(1, review.queue_report(self.queue)["pending_count"])

    def test_invalid_queue_preflight_never_starts_capture(self) -> None:
        with patch.object(capture_cli, "capture_sources") as capture, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.invoke(capture_cli, "capture", "--plan", self.capture.plan_path,
                            "--output", self.capture.root / "output", "--review-queue", self.queue)
        capture.assert_not_called()

    def test_queue_failure_reports_retained_capture_for_retry(self) -> None:
        review.initialize_queue(self.queue)
        root, captured = self.capture.run_capture()
        stderr = io.StringIO()
        with patch.object(capture_cli, "capture_sources", return_value=captured), \
                patch.object(capture_cli, "import_capture", side_effect=ValueError("fixture failure")), \
                redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                self.invoke(capture_cli, "capture", "--plan", self.capture.plan_path,
                            "--output", root, "--review-queue", self.queue)
        self.assertIn(f"Capture retained at {root}", stderr.getvalue())
        self.assertTrue((root / "manifest.json").is_file())
        self.assertEqual(0, review.queue_report(self.queue)["run_count"])

    def test_export_restore_preserves_events_and_historical_report(self) -> None:
        review.initialize_queue(self.queue)
        root, _ = self.capture.run_capture()
        review.import_capture(self.queue, root)
        candidate = review.queue_report(self.queue)["candidates"][0]
        self.invoke(review_cli, "decide", "--database", self.queue, "--candidate", candidate["id"],
                    "--action", "dismiss", "--reviewer", "fixture", "--reason", "Fixture review",
                    "--expected-event", candidate["last_event_id"])
        exported = self.capture.root / "events.json"
        self.invoke(review_cli, "export", "--database", self.queue, "--output", exported)
        restored = self.capture.root / "restored.sqlite"
        result = self.invoke(review_cli, "restore", "--database", restored, "--events", exported)
        self.assertEqual(review.queue_report(self.queue), result)
        self.assertEqual(review.export_events(self.queue), review.export_events(restored))
        self.assertEqual(1, review.verify_queue(restored)["verified_capture_count"])
        events = json.loads(exported.read_text())
        events["events"][0]["recorded_at"] = "2026-09-07T04:00:01Z"
        exported.write_text(json.dumps(events))
        invalid = self.capture.root / "invalid.sqlite"
        with self.assertRaises(ValueError):
            review.restore_queue(invalid, exported)
        self.assertFalse(invalid.exists())


if __name__ == "__main__":
    unittest.main()
