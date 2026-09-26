from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from semiconductor_atlas import discovery_poll as poll
from semiconductor_atlas import discovery_review as review
from tests import test_nist_discovery as fixtures
from tests.test_nist_discovery_parser import document, news, pager


class DiscoveryPollTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NISTDiscoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.queue = self.root / "queue.sqlite"
        review.initialize_queue(self.queue)
        for target in (poll, review):
            clock = patch.object(target, "_now", side_effect=self.fixture.clock)
            clock.start()
            self.addCleanup(clock.stop)
        self.config_path = self.root / "plans/poll.json"
        self.config = {"format": poll.FORMAT, "recorded_at": "2026-09-07T04:00:00Z",
            "plan": {"path": "plans/discovery.json", "sha256": ""}, "queue_path": "queue.sqlite",
            "capture_root": "poll-captures", "state_root": "poll-state", "interval_seconds": 3600,
            "notes": "Fixture discovery poll, no permission for linked acquisition"}
        self.save_config()

    def save_config(self):
        self.fixture.plan_path.write_text(json.dumps(self.fixture.plan))
        self.config["plan"]["sha256"] = poll._sha(self.fixture.plan_path.read_bytes())
        self.config_path.write_text(json.dumps(self.config))

    def tick(self, **kwargs):
        return poll.poll_once(self.config_path, repository_root=self.root, transport=self.fixture.transport, **kwargs)

    def test_capture_not_due_repeat_and_due_without_skip_postponement(self):
        first = self.tick()
        self.assertEqual("captured_imported", first["result"]["status"])
        self.assertEqual(6, len(self.fixture.calls))
        self.assertTrue(first["reportable_change"])
        self.fixture.advance(60)
        second = self.tick()
        self.assertEqual("not_due", second["result"]["status"])
        self.assertFalse(second["reportable_change"])
        due = second["due_at"]
        self.fixture.advance(60)
        self.assertEqual(due, self.tick()["due_at"])
        self.assertEqual(6, len(self.fixture.calls))
        self.fixture.now = poll._instant(due)
        repeated = self.tick()
        self.assertEqual("captured_imported", repeated["result"]["status"])
        self.assertFalse(repeated["reportable_change"])
        self.assertEqual(4, repeated["queue_summary"]["candidate_count"])

    def test_policy_failure_enforces_cadence_and_remains_visible(self):
        self.fixture.bodies["nist-rights"] += b"<p>new policy</p>"
        first = self.tick()
        self.assertTrue(first["health"]["capture_attention_required"])
        self.assertTrue(first["reportable_change"])
        self.fixture.advance(60)
        repeat = self.tick()
        self.assertEqual("not_due", repeat["result"]["status"])
        self.assertFalse(repeat["reportable_change"])
        self.assertTrue(repeat["attention_required"])
        self.assertEqual(2, len(self.fixture.calls))

    def test_completed_manifest_recovers_before_expiry_and_without_refetch(self):
        self.fixture.plan["expires_at"] = "2026-09-07T05:00:30Z"
        self.save_config()
        with patch.object(poll, "import_capture", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.tick()
        self.assertEqual(0, review.queue_report(self.queue)["run_count"])
        self.fixture.advance(60)
        result = self.tick()
        self.assertEqual("review_expired", result["result"]["status"])
        self.assertEqual(1, len(result["recovery"]))
        self.assertTrue(result["recovery"][0]["new_queue_import"])
        self.assertEqual(1, review.verify_queue(self.queue)["run_count"])
        self.assertEqual(6, len(self.fixture.calls))

    def test_queue_commit_before_outcome_recovers_idempotently(self):
        original = poll.import_capture

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise KeyboardInterrupt

        with patch.object(poll, "import_capture", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.tick()
        self.fixture.advance(60)
        result = self.tick()
        self.assertEqual("not_due", result["result"]["status"])
        self.assertFalse(result["recovery"][0]["new_queue_import"])
        self.assertTrue(result["reportable_change"])
        self.assertEqual(1, review.verify_queue(self.queue)["run_count"])
        self.assertEqual(6, len(self.fixture.calls))

        self.assertFalse(self.tick()["reportable_change"])

    def test_incomplete_intent_cooldown_then_success_clears_problem(self):
        with patch.object(poll, "capture_indexes", side_effect=ValueError("incomplete")):
            first = self.tick()
        self.assertEqual("capture_failed", first["result"]["status"])
        self.assertTrue(first["reportable_change"])
        self.fixture.advance(60)
        repeat = self.tick()
        self.assertEqual("not_due", repeat["result"]["status"])
        self.assertTrue(repeat["result"]["unresolved_interrupted_capture"])
        self.assertFalse(repeat["reportable_change"])
        self.assertEqual([], self.fixture.calls)
        self.fixture.now = poll._instant(repeat["due_at"])
        self.assertEqual("captured_imported", self.tick()["result"]["status"])
        self.assertFalse(self.tick()["result"]["unresolved_interrupted_capture"])

    def test_invalid_completed_unimported_capture_blocks_even_forced_refetch(self):
        with patch.object(poll, "import_capture", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.tick()
        packet = next((self.root / "poll-captures").iterdir())
        (packet / "responses/news-page-0.body").write_bytes(b"tampered")
        self.fixture.advance(3601)
        result = self.tick(force=True)
        self.assertEqual("recovery_blocked", result["result"]["status"])
        self.assertEqual(6, len(self.fixture.calls))
        self.assertEqual(0, review.queue_report(self.queue)["run_count"])

    def test_private_plan_review_and_baseline_survive_mutated_originals(self):
        original = self.fixture.transport

        def mutate(entry, destination, plan):
            result = original(entry, destination, plan)
            if entry["id"] == "nist-robots":
                for name in ("plans/discovery.json", "review.json", "baseline.json"):
                    (self.root / name).write_bytes(b"mutated original")
            return result

        self.fixture.transport = mutate
        self.assertEqual("captured_imported", self.tick()["result"]["status"])
        self.assertEqual(4, review.verify_queue(self.queue)["candidate_count"])

    def test_plan_revision_does_not_reset_publisher_cadence(self):
        self.tick()
        self.fixture.advance(60)
        self.fixture.plan["plan_id"] = "revised fixture"
        self.save_config()
        result = self.tick()
        self.assertEqual("not_due", result["result"]["status"])
        self.assertEqual(6, len(self.fixture.calls))

    def test_lock_future_clock_and_protected_paths_fail_before_fetch(self):
        with poll._lock(self.queue):
            with self.assertRaises(ValueError):
                self.tick()
        self.assertFalse((self.root / "poll-state").exists())
        self.config["recorded_at"] = "2026-09-08T00:00:00Z"
        self.save_config()
        with self.assertRaises(ValueError):
            self.tick()
        self.config["recorded_at"] = "2026-09-07T04:00:00Z"
        self.config["capture_root"] = "plans/captures"
        self.save_config()
        with self.assertRaises(ValueError):
            self.tick()
        self.assertEqual([], self.fixture.calls)

    def test_metadata_change_not_raw_repeat_drives_reportable_signal(self):
        self.tick()
        self.fixture.advance(60)
        self.fixture.bodies["news-page-0"] = self.fixture.bodies["news-page-0"].replace(b"<body>", b"<body>\n<!-- cosmetic -->")
        self.assertFalse(self.tick(force=True)["reportable_change"])
        self.fixture.advance(60)
        self.fixture.bodies["news-page-0"] = document(news(title="TSMC changed project context") + pager())
        self.assertTrue(self.tick(force=True)["reportable_change"])

    def test_first_expired_check_reports_once_without_fetching(self):
        self.fixture.plan["expires_at"] = "2026-09-07T04:30:00Z"
        self.save_config()
        first = self.tick()
        self.assertEqual("review_expired", first["result"]["status"])
        self.assertTrue(first["reportable_change"])
        self.assertFalse(self.tick()["reportable_change"])
        self.assertEqual([], self.fixture.calls)


if __name__ == "__main__":
    unittest.main()
