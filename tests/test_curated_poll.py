from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from semiconductor_atlas import curated_poll as poll
from semiconductor_atlas import curated_review as review
from tests import test_curated_coverage as fixtures


class CuratedPollTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.CuratedCoverageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.capture = self.fixture.capture
        self.queue = self.fixture.queue
        self.config_path = self.root / "plans/poll.json"
        self.config = {
            "format": poll.FORMAT, "recorded_at": "2026-09-07T02:30:00Z",
            "catalog": {"path": "plans/coverage.json", "sha256": poll._sha(self.fixture.catalog_path.read_bytes())},
            "queue_path": "queue.sqlite", "capture_root": "poll-captures", "state_root": "poll-state",
            "interval_seconds": 3600, "notes": "Offline fixture, not source permission",
        }
        self.write_config()
        self.clock = "2026-09-07T03:00:00Z"
        now = patch.object(poll, "_now", side_effect=lambda: self.clock)
        now.start()
        self.addCleanup(now.stop)

    def write_config(self) -> None:
        self.config_path.write_text(json.dumps(self.config))

    def tick(self, clock: str | None = None, **kwargs) -> dict:
        self.clock = clock or self.clock
        self.capture.clock = self.clock
        self.fixture.clock = self.clock
        return poll.poll_once(self.config_path, repository_root=self.root, transport=self.capture.transport, **kwargs)

    def repin_plan(self) -> None:
        self.capture.plan_path.write_text(json.dumps(self.capture.plan))
        self.fixture.catalog["facilities"][5]["plan"]["sha256"] = poll._sha(self.capture.plan_path.read_bytes())
        self.fixture.write_catalog()
        self.config["catalog"]["sha256"] = poll._sha(self.fixture.catalog_path.read_bytes())
        self.write_config()

    def add_tsmc_plan(self, interval: int) -> None:
        plan = copy.deepcopy(self.capture.plan)
        plan.update(plan_id="tsmc-fixture", checked_facility_key="tsmc:fab21-arizona",
                    minimum_interval_seconds=interval)
        for entry in plan["policies"] + plan["documents"]:
            entry["company"] = "TSMC"
        path = self.root / "plans/tsmc.json"
        path.write_text(json.dumps(plan))
        self.fixture.catalog["facilities"][0].update(
            plan={"path": "plans/tsmc.json", "sha256": poll._sha(path.read_bytes())},
            unmonitored_reason=None)
        self.repin_plan()

    def test_inter_plan_pacing_honors_both_intervals_and_skips_do_not_sleep(self) -> None:
        self.capture.plan["minimum_interval_seconds"] = 2
        self.add_tsmc_plan(7)
        with patch.object(poll.time, "sleep") as pause:
            result = self.tick()
            self.assertEqual([7, 7, 7, 2, 2], [call.args[0] for call in pause.call_args_list])
            self.assertEqual(["captured_imported"] * 2, [row["status"] for row in result["results"]])
            pause.reset_mock()
            skipped = self.tick("2026-09-07T03:30:00Z")
            self.assertEqual(["not_due"] * 2, [row["status"] for row in skipped["results"]])
            pause.assert_not_called()

    def test_next_plan_still_waits_after_failed_capture(self) -> None:
        self.capture.plan["minimum_interval_seconds"] = 7
        self.add_tsmc_plan(2)
        original = self.capture.transport

        def fail_first(entry, destination, plan):
            if entry["company"] == "TSMC":
                raise ValueError("interrupted request")
            return original(entry, destination, plan)

        self.capture.transport = fail_first
        with patch.object(poll.time, "sleep") as pause:
            result = self.tick()
        self.assertEqual(["capture_failed", "captured_imported"], [row["status"] for row in result["results"]])
        self.assertEqual([7, 7, 7], [call.args[0] for call in pause.call_args_list])

    def test_capture_then_not_due_then_due_without_skip_postponement(self) -> None:
        first = self.tick()
        self.assertEqual("captured_imported", first["results"][0]["status"])
        self.assertEqual(3, len(self.capture.calls))
        self.assertEqual(1, first["coverage_summary"]["pending_count"])
        skipped = self.tick("2026-09-07T03:30:00Z")
        self.assertEqual("not_due", skipped["results"][0]["status"])
        self.assertFalse(skipped["reportable_change"])
        self.assertEqual("2026-09-07T04:00:00Z", skipped["results"][0]["due_at"])
        self.tick("2026-09-07T03:59:00Z")
        again = self.tick("2026-09-07T04:00:00Z")
        self.assertEqual("captured_imported", again["results"][0]["status"])
        self.assertEqual(6, len(self.capture.calls))
        self.assertFalse(again["reportable_change"])
        self.assertEqual(1, again["coverage_summary"]["pending_count"])
        self.assertEqual(6, again["coverage_summary"]["unmonitored_facility_count"])

    def test_completed_failed_checks_also_satisfy_cadence(self) -> None:
        self.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        first = self.tick()
        self.assertTrue(first["reportable_change"])
        self.assertEqual("captured_imported", first["results"][0]["status"])
        self.assertTrue(first["results"][0]["capture_attention_required"])
        self.assertEqual(0, first["coverage_summary"]["fresh_document_count"])
        again = self.tick("2026-09-07T03:30:00Z")
        self.assertEqual("not_due", again["results"][0]["status"])
        self.assertEqual(3, len(self.capture.calls))
        self.assertFalse(again["reportable_change"])

    def test_recovery_after_capture_before_import_even_after_expiry(self) -> None:
        self.capture.plan["expires_at"] = "2026-09-07T03:20:00Z"
        self.repin_plan()
        with patch.object(poll, "import_capture", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.tick()
        self.assertEqual(0, review.queue_report(self.queue)["run_count"])
        recovered = self.tick("2026-09-07T03:30:00Z")
        self.assertEqual("review_expired", recovered["results"][0]["status"])
        self.assertEqual(1, len(recovered["recovery"]))
        self.assertEqual(1, review.queue_report(self.queue)["run_count"])
        self.assertEqual(3, len(self.capture.calls))

    def test_recovery_after_queue_commit_does_not_duplicate_or_refetch(self) -> None:
        real_import = poll.import_capture

        def interrupted(*args, **kwargs):
            real_import(*args, **kwargs)
            raise KeyboardInterrupt

        with patch.object(poll, "import_capture", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.tick()
        self.assertEqual(1, review.queue_report(self.queue)["run_count"])
        recovered = self.tick("2026-09-07T03:30:00Z")
        self.assertFalse(recovered["recovery"][0]["new_queue_import"])
        self.assertEqual(1, review.queue_report(self.queue)["run_count"])
        self.assertEqual(3, len(self.capture.calls))

    def test_incomplete_failure_remains_visible_during_cooldown(self) -> None:
        with patch.object(poll, "capture_sources", side_effect=ValueError("incomplete fixture")):
            failed = self.tick()
        self.assertEqual("capture_failed", failed["results"][0]["status"])
        again = self.tick("2026-09-07T03:30:00Z")
        self.assertEqual("not_due", again["results"][0]["status"])
        self.assertTrue(again["results"][0]["unresolved_interrupted_capture"])
        self.assertFalse(again["reportable_change"])
        self.assertEqual([], self.capture.calls)
        recovered = self.tick("2026-09-07T04:00:00Z")
        self.assertEqual("captured_imported", recovered["results"][0]["status"])
        self.assertTrue(recovered["reportable_change"])

    def test_invalid_completed_unimported_packet_blocks_refetch(self) -> None:
        with patch.object(poll, "import_capture", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.tick()
        packet = next((self.root / "poll-captures").iterdir())
        (packet / "responses/document.body").write_bytes(b"corrupted")
        blocked = self.tick("2026-09-07T04:00:00Z", force=True)
        self.assertEqual("recovery_blocked", blocked["results"][0]["status"])
        self.assertEqual(0, review.queue_report(self.queue)["run_count"])
        self.assertEqual(3, len(self.capture.calls))

    def test_lock_contention_refuses_work_and_unheld_lock_file_is_not_a_process(self) -> None:
        with poll._lock(self.queue):
            with self.assertRaisesRegex(ValueError, "holds the queue lock"):
                self.tick()
        self.assertFalse((self.root / "poll-state").exists())
        self.assertEqual("captured_imported", self.tick()["results"][0]["status"])

    def test_private_plan_snapshot_is_used_after_original_plan_changes(self) -> None:
        real_capture = poll.capture_sources
        observed = []

        def mutate(plan_path, *args, **kwargs):
            original = self.capture.plan_path.read_bytes()
            self.capture.plan_path.write_bytes(b"unreviewed modification")
            try:
                observed.append(Path(plan_path) != self.capture.plan_path)
                return real_capture(plan_path, *args, **kwargs)
            finally:
                self.capture.plan_path.write_bytes(original)

        with patch.object(poll, "capture_sources", side_effect=mutate):
            self.assertEqual("captured_imported", self.tick()["results"][0]["status"])
        self.assertEqual([True], observed)
        self.assertEqual(["robots", "rights", "document"], self.capture.calls)

    def test_cadence_is_independent_of_eligible_freshness(self) -> None:
        self.config["interval_seconds"] = 86400
        self.write_config()
        self.tick()
        later = self.tick("2026-09-07T05:00:00Z")
        self.assertEqual("not_due", later["results"][0]["status"])
        self.assertEqual(1, later["coverage_summary"]["stale_document_count"])
        self.assertTrue(later["reportable_change"])

    def test_future_queue_clock_rejects_before_network(self) -> None:
        self.fixture.clock = "2026-09-07T06:00:00Z"
        root, _ = self.capture.run_capture()
        review.import_capture(self.queue, root)
        calls = len(self.capture.calls)
        with self.assertRaisesRegex(ValueError, "predates existing queue"):
            self.tick()
        self.assertEqual(calls, len(self.capture.calls))

    def test_latest_incomplete_manual_history_does_not_erase_known_success(self) -> None:
        self.tick()
        self.capture.clock = "2026-09-07T04:00:00Z"
        self.fixture.clock = self.capture.clock
        self.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        packet, _ = self.capture.run_capture("unchained-manual")
        review.import_capture(self.queue, packet)
        self.capture.overrides.clear()
        calls = len(self.capture.calls)
        blocked = self.tick("2026-09-07T05:00:00Z")
        self.assertEqual("prior_history_requires_review", blocked["results"][0]["status"])
        self.assertEqual(calls, len(self.capture.calls))

    def test_equal_clock_prior_packets_require_review(self) -> None:
        self.tick()
        self.fixture.clock = self.capture.clock
        self.capture.bodies["document"] += b"<p>Divergent same-time fixture.</p>"
        packet, _ = self.capture.run_capture("parallel-manual")
        review.import_capture(self.queue, packet)
        calls = len(self.capture.calls)
        blocked = self.tick("2026-09-07T04:00:00Z")
        self.assertEqual("prior_history_requires_review", blocked["results"][0]["status"])
        self.assertEqual(calls, len(self.capture.calls))

    def test_exported_queue_matches_final_cutoff_and_restores(self) -> None:
        result = self.tick()
        tick = Path(result["invocation_path"])
        exported = json.loads((tick / "queue-events.json").read_bytes())
        self.assertEqual(result["queue_head_event_id"], exported["events"][-1]["event_id"])
        restored = review.restore_queue(self.root / "restored.sqlite", tick / "queue-events.json")
        self.assertEqual(result["queue_head_event_id"], restored["head_event_id"])
        self.assertEqual(result["coverage_sha256"], poll._sha((tick / "coverage.json").read_bytes()))
        self.fixture.clock = "2026-09-07T04:00:00Z"
        candidate = review.queue_report(self.queue)["candidates"][0]
        review.record_decision(self.queue, candidate["id"], action="dismiss", reviewer="fixture",
                               reason="Later review", expected_event_id=candidate["last_event_id"])
        self.assertEqual(exported, review.export_events(self.queue, as_of=result["finished_at"]))


if __name__ == "__main__":
    unittest.main()
