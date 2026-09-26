from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from semiconductor_atlas import curated_review as review
from tests import test_curated_capture as capture_fixtures


class CuratedReviewTests(unittest.TestCase):
    """Capture-to-review tests use actual, offline-validated collector bundles."""

    def setUp(self) -> None:
        # Composition avoids rediscovering the collector's test cases here.
        self.capture = capture_fixtures.CuratedCaptureTests()
        self.capture.setUp()
        self.addCleanup(self.capture.doCleanups)
        self.root = self.capture.root
        self.queue = self.root / "review.sqlite"
        self.clock = "2026-09-07T04:00:00Z"
        now = patch.object(review, "_now", side_effect=lambda: self.clock)
        now.start()
        self.addCleanup(now.stop)
        review.initialize_queue(self.queue)

    def report(self, *, as_of: str | None = None) -> dict:
        return review.queue_report(self.queue, as_of=as_of)

    def candidate(self) -> dict:
        report = self.report()
        self.assertEqual(1, report["candidate_count"])
        return report["candidates"][0]

    def changed_capture(self) -> tuple[Path, Path, dict]:
        prior, _ = self.capture.run_capture("prior")
        self.capture.bodies["document"] = self.capture.bodies["document"].replace(
            b"announced", b"groundbreaking"
        )
        changed, result = self.capture.run_capture("changed", prior)
        self.assertTrue(result["documents"][0]["review_required"])
        return prior, changed, result

    def decide(self, action: str, **kwargs: object) -> dict:
        candidate = self.candidate()
        return review.record_decision(
            self.queue, candidate["id"], action=action,
            reviewer="fixture-reviewer", reason="Recorded fixture review",
            expected_event_id=candidate["last_event_id"], **kwargs,
        )

    def test_initialization_is_new_file_only_and_foreign_database_is_rejected(self) -> None:
        original = self.queue.read_bytes()
        with self.assertRaises(FileExistsError):
            review.initialize_queue(self.queue)
        self.assertEqual(original, self.queue.read_bytes())
        foreign = self.root / "foreign.sqlite"
        with closing(sqlite3.connect(foreign)) as connection:
            with connection:
                connection.execute("CREATE TABLE unrelated (value TEXT)")
                connection.execute("INSERT INTO unrelated VALUES ('preserve me')")
        foreign_bytes = foreign.read_bytes()
        with self.assertRaises(ValueError):
            review.queue_report(foreign)
        with self.assertRaises(FileExistsError):
            review.initialize_queue(foreign)
        self.assertEqual(foreign_bytes, foreign.read_bytes())

    def test_first_observation_creates_pending_candidate_without_accepting_claims(self) -> None:
        root, captured = self.capture.run_capture()
        imported = review.import_capture(self.queue, root)
        self.assertTrue(imported["imported"])
        report = self.report()
        candidate = self.candidate()
        self.assertEqual(1, report["pending_count"])
        self.assertEqual(1, report["run_count"])
        self.assertFalse(report["claim_acceptance"])
        self.assertFalse(report["delivery_eligible"])
        self.assertEqual("pending", candidate["status"])
        self.assertEqual("https://example.org/document", candidate["source_url"])
        self.assertEqual("amkor:peoria", candidate["facility_key"])
        self.assertIsNone(candidate["prior_text_sha256"])
        self.assertEqual(captured["documents"][0]["current_text_sha256"], candidate["current_text_sha256"])
        self.assertTrue(candidate["last_event_id"])
        self.assertTrue(candidate["observations"])

    def test_duplicate_import_is_idempotent(self) -> None:
        root, _ = self.capture.run_capture()
        review.import_capture(self.queue, root)
        before = self.report()
        self.clock = "2026-09-07T04:01:00Z"
        duplicate = review.import_capture(self.queue, root)
        after = self.report()
        self.assertFalse(duplicate["imported"])
        self.assertEqual(before["candidates"], after["candidates"])
        self.assertEqual(1, after["run_count"])
        self.assertEqual(1, after["pending_count"])

    def test_changed_then_unchanged_keeps_unresolved_change_and_decision_token(self) -> None:
        _, changed, captured = self.changed_capture()
        review.import_capture(self.queue, changed)
        before = self.candidate()
        same, same_result = self.capture.run_capture("same", changed)
        self.assertEqual("unchanged", same_result["documents"][0]["status"])
        self.clock = "2026-09-07T04:01:00Z"
        review.import_capture(self.queue, same)
        after = self.candidate()
        self.assertEqual("pending", after["status"])
        self.assertEqual(before["id"], after["id"])
        self.assertEqual(before["last_event_id"], after["last_event_id"])
        self.assertEqual(captured["documents"][0]["prior_text_sha256"], after["prior_text_sha256"])
        self.assertEqual(before["first_seen_at"], after["first_seen_at"])
        self.assertEqual(1, self.report()["pending_count"])
        self.assertEqual(2, self.report()["run_count"])

    def test_policy_block_and_failed_check_do_not_clear_pending_candidate(self) -> None:
        _, changed, _ = self.changed_capture()
        review.import_capture(self.queue, changed)
        before = self.candidate()
        original_policy = self.capture.bodies["rights"]
        self.capture.bodies["rights"] += b"<p>Changed terms.</p>"
        blocked, result = self.capture.run_capture("blocked", changed)
        self.assertEqual("not_attempted_policy_blocked", result["documents"][0]["status"])
        self.capture.bodies["rights"] = original_policy
        self.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        failed, result = self.capture.run_capture("failed", blocked)
        self.assertEqual("failed_check", result["documents"][0]["status"])
        self.capture.overrides.clear()
        same, result = self.capture.run_capture("same", failed)
        self.assertEqual("unchanged", result["documents"][0]["status"])
        for minute, root in enumerate((blocked, failed, same), start=1):
            self.clock = f"2026-09-07T04:0{minute}:00Z"
            review.import_capture(self.queue, root)
            after = self.candidate()
            self.assertEqual("pending", after["status"])
            self.assertEqual(before["id"], after["id"])
            self.assertEqual(before["last_event_id"], after["last_event_id"])
            self.assertEqual(1, self.report()["pending_count"])
        self.assertEqual(4, self.report()["run_count"])

    def test_unchanged_and_markup_only_bootstrap_one_pending_admission_candidate(self) -> None:
        prior, _ = self.capture.run_capture("prior")
        same, _ = self.capture.run_capture("same", prior)
        self.capture.bodies["document"] = self.capture.bodies["document"].replace(b"<p>", b'<p class="new">')
        markup, result = self.capture.run_capture("markup", same)
        self.assertEqual("raw_bytes_only", result["documents"][0]["status"])
        for root in (same, markup):
            review.import_capture(self.queue, root)
        self.assertEqual(1, self.report()["candidate_count"])
        self.assertEqual(1, self.report()["pending_count"])
        self.assertEqual(2, self.report()["run_count"])
        candidate = self.candidate()
        self.assertEqual("pending", candidate["status"])
        self.assertEqual("unreviewed_version_at_queue_admission", candidate["trigger"])
        self.assertEqual(result["documents"][0]["current_text_sha256"], candidate["current_text_sha256"])
        self.assertEqual(2, len(candidate["observations"]))

    def test_skipped_change_packet_still_admits_unreviewed_version_on_unchanged_check(self) -> None:
        prior, _ = self.capture.run_capture("prior")
        review.import_capture(self.queue, prior)
        self.decide("dismiss")
        initial = self.candidate()
        self.capture.clock = "2026-09-07T03:01:00Z"
        self.capture.bodies["document"] = self.capture.bodies["document"].replace(b"announced", b"groundbreaking")
        skipped, changed = self.capture.run_capture("skipped-change", prior)
        self.assertTrue(changed["documents"][0]["review_required"])
        self.capture.clock = "2026-09-07T03:02:00Z"
        same, unchanged = self.capture.run_capture("same", skipped)
        self.assertEqual("unchanged", unchanged["documents"][0]["status"])
        self.clock = "2026-09-07T04:01:00Z"
        review.import_capture(self.queue, same)
        report = self.report()
        self.assertEqual(2, report["run_count"])
        self.assertEqual(2, report["candidate_count"])
        self.assertEqual(1, report["pending_count"])
        admitted = next(candidate for candidate in report["candidates"] if candidate["id"] != initial["id"])
        self.assertEqual("pending", admitted["status"])
        self.assertEqual("unreviewed_version_at_queue_admission", admitted["trigger"])
        self.assertEqual(changed["documents"][0]["current_text_sha256"], admitted["current_text_sha256"])
        self.assertEqual(unchanged["documents"][0]["prior_text_sha256"], admitted["observations"][0]["prior_text_sha256"])

    def test_out_of_order_import_does_not_regress_latest_check_or_last_eligible_content(self) -> None:
        prior, _ = self.capture.run_capture("prior")
        self.capture.clock = "2026-09-07T03:01:00Z"
        self.capture.bodies["document"] = self.capture.bodies["document"].replace(b"announced", b"groundbreaking")
        changed, changed_result = self.capture.run_capture("changed", prior)
        self.capture.clock = "2026-09-07T03:02:00Z"
        self.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        failed, _ = self.capture.run_capture("failed", changed)
        for minute, root in enumerate((failed, changed, prior)):
            self.clock = f"2026-09-07T04:0{minute}:00Z"
            review.import_capture(self.queue, root)
        source = self.report()["sources"][0]
        self.assertEqual(1, len(source["latest_checks"]))
        self.assertEqual("failed_check", source["latest_checks"][0]["status"])
        self.assertEqual("2026-09-07T03:02:00Z", source["latest_checks"][0]["captured_at"])
        self.assertEqual(1, len(source["last_eligible_observations"]))
        last_eligible = source["last_eligible_observations"][0]
        self.assertEqual("2026-09-07T03:01:00Z", last_eligible["captured_at"])
        self.assertEqual(changed_result["documents"][0]["current_text_sha256"], last_eligible["current_text_sha256"])
        self.assertTrue(source["attention_required"])

    def test_source_freshness_and_candidate_clocks_use_response_not_packet_finish(self) -> None:
        def capture_with_finish(name: str, response_at: str, packet_finished_at: str) -> tuple[Path, dict]:
            self.capture.clock = response_at
            advance_after_response = False
            original_transport = self.capture.transport

            def transport(entry: dict, destination: Path, plan: dict) -> dict:
                nonlocal advance_after_response
                metadata = original_transport(entry, destination, plan)
                if entry["id"] == "document":
                    advance_after_response = True
                return metadata

            def now() -> str:
                nonlocal advance_after_response
                clock = self.capture.clock
                if advance_after_response:
                    advance_after_response = False
                    self.capture.clock = packet_finished_at
                return clock

            with patch.object(self.capture, "transport", side_effect=transport), patch.object(capture_fixtures.capture, "_now", side_effect=now):
                return self.capture.run_capture(name)

        older, old_result = capture_with_finish("older", "2026-09-07T03:00:00Z", "2026-09-07T03:10:00Z")
        self.capture.bodies["document"] = self.capture.bodies["document"].replace(b"announced", b"groundbreaking")
        newer, new_result = capture_with_finish("newer", "2026-09-07T03:05:00Z", "2026-09-07T03:06:00Z")
        review.import_capture(self.queue, newer)
        self.clock = "2026-09-07T04:01:00Z"
        review.import_capture(self.queue, older)
        report = self.report()
        source = report["sources"][0]
        self.assertEqual(1, len(source["latest_checks"]))
        self.assertEqual(new_result["documents"][0]["current_text_sha256"], source["latest_checks"][0]["current_text_sha256"])
        self.assertEqual("2026-09-07T03:05:00Z", source["latest_checks"][0]["captured_at"])
        self.assertEqual(new_result["documents"][0]["current_text_sha256"], source["last_eligible_observations"][0]["current_text_sha256"])
        candidates = {candidate["current_text_sha256"]: candidate for candidate in report["candidates"]}
        for result, response_at in ((old_result, "2026-09-07T03:00:00Z"), (new_result, "2026-09-07T03:05:00Z")):
            candidate = candidates[result["documents"][0]["current_text_sha256"]]
            self.assertEqual(response_at, candidate["first_seen_at"])
            self.assertEqual(response_at, candidate["last_seen_at"])
            self.assertEqual(response_at, candidate["observations"][0]["captured_at"])

    def test_equal_capture_clock_observations_are_preserved_without_arbitrary_latest_choice(self) -> None:
        prior, _ = self.capture.run_capture("prior")
        self.capture.clock = "2026-09-07T03:01:00Z"
        self.capture.bodies["document"] = self.capture.bodies["document"].replace(b"announced", b"groundbreaking")
        first, _ = self.capture.run_capture("first", prior)
        self.capture.bodies["document"] += b"<p>Second distinct update.</p>"
        second, _ = self.capture.run_capture("second", first)
        self.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        failed, _ = self.capture.run_capture("failed", second)
        for minute, root in enumerate((failed, second, first)):
            self.clock = f"2026-09-07T04:0{minute}:00Z"
            review.import_capture(self.queue, root)
        source = self.report()["sources"][0]
        self.assertEqual(3, len(source["latest_checks"]))
        self.assertEqual(2, len(source["last_eligible_observations"]))
        self.assertEqual(2, len({row["current_text_sha256"] for row in source["last_eligible_observations"]}))
        self.assertTrue(source["attention_required"])

    def test_reviewer_actions_preserve_history_and_reopen_corrects_resolution(self) -> None:
        root, _ = self.capture.run_capture()
        review.import_capture(self.queue, root)
        original = self.candidate()
        tokens = {original["last_event_id"]}
        for minute, action, status, pending in (
            (1, "acknowledge", "acknowledged", 1),
            (2, "defer", "deferred", 1),
            (3, "dismiss", "dismissed", 0),
            (4, "reopen", "pending", 1),
        ):
            self.clock = f"2026-09-07T04:0{minute}:00Z"
            self.decide(action)
            candidate = self.candidate()
            self.assertEqual(status, candidate["status"])
            self.assertEqual(pending, self.report()["pending_count"])
            self.assertEqual(original["id"], candidate["id"])
            self.assertEqual(original["first_seen_at"], candidate["first_seen_at"])
            self.assertNotIn(candidate["last_event_id"], tokens)
            tokens.add(candidate["last_event_id"])
        historical = self.report(as_of="2026-09-07T04:03:00Z")
        self.assertEqual("dismissed", historical["candidates"][0]["status"])
        self.assertEqual(0, historical["pending_count"])

    def test_invalid_decisions_and_stale_reviewer_token_are_atomic(self) -> None:
        root, _ = self.capture.run_capture()
        review.import_capture(self.queue, root)
        original = self.candidate()
        valid = dict(action="acknowledge", reviewer="reviewer", reason="Review note", expected_event_id=original["last_event_id"])
        for changes in (
            {"action": "accept"}, {"reviewer": " "}, {"reason": ""},
            {"expected_event_id": "not-the-current-event"}, {"action": "handoff"},
            {"action": "handoff", "evidence_ref": " "},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    review.record_decision(self.queue, original["id"], **{**valid, **changes})
                self.assertEqual(original, self.candidate())
        with self.assertRaises(ValueError):
            review.record_decision(self.queue, "unknown-candidate", **valid)
        self.clock = "2026-09-07T04:01:00Z"
        self.decide("acknowledge")
        acknowledged = self.candidate()
        with self.assertRaises(ValueError):
            review.record_decision(self.queue, original["id"], **{**valid, "action": "dismiss"})
        self.assertEqual(acknowledged, self.candidate())

    def test_handoff_resolves_queue_item_without_claim_or_alert_acceptance(self) -> None:
        root, _ = self.capture.run_capture()
        review.import_capture(self.queue, root)
        self.clock = "2026-09-07T04:01:00Z"
        self.decide("handoff", evidence_ref="review_plans/fixture-review.json")
        report = self.report()
        self.assertEqual("handed_off", report["candidates"][0]["status"])
        self.assertEqual(0, report["pending_count"])
        self.assertEqual(1, report["candidate_count"])
        self.assertFalse(report["claim_acceptance"])
        self.assertFalse(report["delivery_eligible"])

    def test_as_of_replays_import_time_not_earlier_capture_time(self) -> None:
        root, _ = self.capture.run_capture()
        self.clock = "2026-09-07T04:01:00Z"
        review.import_capture(self.queue, root)
        self.clock = "2026-09-07T04:02:00Z"
        self.decide("dismiss")
        before_import = self.report(as_of="2026-09-07T04:00:59Z")
        self.assertEqual(0, before_import["candidate_count"])
        self.assertEqual(0, before_import["run_count"])
        after_import = self.report(as_of="2026-09-07T04:01:00Z")
        self.assertEqual(1, after_import["pending_count"])
        self.assertEqual("pending", after_import["candidates"][0]["status"])
        self.assertEqual("dismissed", self.report(as_of="2026-09-07T04:02:00Z")["candidates"][0]["status"])

    def test_tampered_capture_is_rejected_without_partially_importing(self) -> None:
        root, _ = self.capture.run_capture()
        body = root / "responses/document.body"
        original = body.read_bytes()
        body.write_bytes(original + b" changed")
        with self.assertRaises(ValueError):
            review.import_capture(self.queue, root)
        self.assertEqual(0, self.report()["candidate_count"])
        self.assertEqual(0, self.report()["run_count"])
        body.write_bytes(original)
        self.assertTrue(review.import_capture(self.queue, root)["imported"])
        self.assertEqual(1, self.report()["candidate_count"])

    def test_verification_detects_retained_capture_tampering_after_import(self) -> None:
        root, _ = self.capture.run_capture()
        review.import_capture(self.queue, root)
        self.assertEqual(1, review.verify_queue(self.queue)["verified_capture_count"])
        body = root / "responses/document.body"
        body.write_bytes(body.read_bytes() + b" changed")
        with self.assertRaises(ValueError):
            review.verify_queue(self.queue)
        # Event replay remains available, but is not evidence-byte verification.
        self.assertEqual(1, self.report()["candidate_count"])

    def test_export_restore_roundtrip_preserves_events_decisions_and_historical_replay(self) -> None:
        root, _ = self.capture.run_capture()
        review.import_capture(self.queue, root)
        self.clock = "2026-09-07T04:01:00Z"
        self.decide("acknowledge")
        self.clock = "2026-09-07T04:02:00Z"
        self.decide("dismiss")
        exported = review.export_events(self.queue)
        events_path = self.root / "events.json"
        events_path.write_text(json.dumps(exported), encoding="utf-8")
        restored = self.root / "restored.sqlite"
        review.restore_queue(restored, events_path)
        self.assertEqual(self.report(), review.queue_report(restored))
        self.assertEqual(exported, review.export_events(restored))
        self.assertEqual(1, review.verify_queue(restored)["verified_capture_count"])
        self.assertEqual(self.report(as_of="2026-09-07T04:01:00Z"), review.queue_report(restored, as_of="2026-09-07T04:01:00Z"))

    def test_corrupt_export_is_rejected_before_creating_restored_database(self) -> None:
        root, _ = self.capture.run_capture()
        review.import_capture(self.queue, root)
        exported = review.export_events(self.queue)
        exported["events"][0]["payload"]["documents"][0]["current_text_sha256"] = "0" * 64
        events_path = self.root / "corrupt-events.json"
        events_path.write_text(json.dumps(exported), encoding="utf-8")
        restored = self.root / "new-directory/restored.sqlite"
        with self.assertRaises(ValueError):
            review.restore_queue(restored, events_path)
        self.assertFalse(restored.exists())
        self.assertFalse(restored.parent.exists())

    def test_unchanged_return_to_resolved_version_requests_reopen_when_change_packet_was_omitted(self) -> None:
        original_body = self.capture.bodies["document"]
        first, _ = self.capture.run_capture("first-a")
        review.import_capture(self.queue, first)
        self.decide("dismiss")
        resolved_a = self.candidate()
        self.capture.clock = "2026-09-07T03:01:00Z"
        self.capture.bodies["document"] = original_body.replace(b"announced", b"groundbreaking")
        second, _ = self.capture.run_capture("second-b", first)
        self.clock = "2026-09-07T04:01:00Z"
        review.import_capture(self.queue, second)
        candidate_b = next(candidate for candidate in self.report()["candidates"] if candidate["id"] != resolved_a["id"])
        review.record_decision(
            self.queue, candidate_b["id"], action="dismiss", reviewer="fixture-reviewer",
            reason="Reviewed B fixture", expected_event_id=candidate_b["last_event_id"],
        )
        self.assertEqual(0, self.report()["pending_count"])
        self.capture.clock = "2026-09-07T03:02:00Z"
        self.capture.bodies["document"] = original_body
        skipped, changed = self.capture.run_capture("omitted-return-a", second)
        self.assertTrue(changed["documents"][0]["review_required"])
        self.capture.clock = "2026-09-07T03:03:00Z"
        later, unchanged = self.capture.run_capture("later-unchanged-a", skipped)
        self.assertEqual("unchanged", unchanged["documents"][0]["status"])
        self.clock = "2026-09-07T04:02:00Z"
        review.import_capture(self.queue, later)
        report = self.report()
        candidate_a = next(candidate for candidate in report["candidates"] if candidate["id"] == resolved_a["id"])
        self.assertEqual(2, report["candidate_count"])
        self.assertEqual(3, report["run_count"])
        self.assertEqual("dismissed", candidate_a["status"])
        self.assertEqual(resolved_a["last_event_id"], candidate_a["last_event_id"])
        self.assertTrue(candidate_a["requires_reopen"])
        self.assertEqual(1, report["recheck_count"])
        self.assertEqual(0, report["pending_count"])
        self.assertTrue(report["attention_required"])

    def test_backfilled_middle_version_surfaces_return_and_preserves_later_disposition(self) -> None:
        original = self.capture.bodies["document"]
        first, _ = self.capture.run_capture("first-a")
        review.import_capture(self.queue, first)
        self.decide("dismiss")
        resolved_a = self.candidate()
        self.capture.clock = "2026-09-07T03:01:00Z"
        self.capture.bodies["document"] = original.replace(b"announced", b"groundbreaking")
        middle, _ = self.capture.run_capture("middle-b", first)
        self.capture.clock = "2026-09-07T03:02:00Z"
        self.capture.bodies["document"] = original
        omitted, _ = self.capture.run_capture("return-a", middle)
        self.capture.clock = "2026-09-07T03:03:00Z"
        later, unchanged = self.capture.run_capture("unchanged-a", omitted)
        self.assertEqual("unchanged", unchanged["documents"][0]["status"])
        self.clock = "2026-09-07T04:01:00Z"
        review.import_capture(self.queue, later)
        self.assertFalse(self.report()["attention_required"])
        self.clock = "2026-09-07T04:02:00Z"
        review.import_capture(self.queue, middle)
        candidate_b = next(row for row in self.report()["candidates"] if row["id"] != resolved_a["id"])
        review.record_decision(self.queue, candidate_b["id"], action="dismiss", reviewer="fixture",
                               reason="Review B", expected_event_id=candidate_b["last_event_id"])
        self.assertEqual(0, self.report()["pending_count"])
        self.assertEqual(1, self.report()["recheck_count"])
        self.assertTrue(self.report()["attention_required"])
        self.assertEqual(0, self.report(as_of="2026-09-07T04:01:00Z")["recheck_count"])
        self.clock = "2026-09-07T04:03:00Z"
        reopened = review.record_decision(self.queue, resolved_a["id"], action="reopen", reviewer="fixture",
                                          reason="Review newly known recurrence", expected_event_id=resolved_a["last_event_id"])
        review.record_decision(self.queue, reopened["id"], action="dismiss", reviewer="fixture",
                               reason="Recurrence reviewed", expected_event_id=reopened["last_event_id"])
        self.assertEqual(0, self.report()["recheck_count"])
        self.capture.clock = "2026-09-07T03:04:00Z"
        same, _ = self.capture.run_capture("same-after-review", later)
        self.clock = "2026-09-07T04:04:00Z"
        review.import_capture(self.queue, same)
        self.assertFalse(self.report()["attention_required"])
        exported = self.root / "backfilled-events.json"
        exported.write_text(json.dumps(review.export_events(self.queue)))
        restored = self.root / "backfilled-restored.sqlite"
        self.assertEqual(self.report(), review.restore_queue(restored, exported))

    def test_identical_transition_recurrence_retains_resolution_until_explicit_reopen(self) -> None:
        prior, changed, _ = self.changed_capture()
        review.import_capture(self.queue, changed)
        self.decide("dismiss")
        dismissed = self.candidate()
        # Re-observing the same bound transition is not a new review decision.
        self.capture.clock = "2026-09-07T03:01:00Z"
        recurrence, _ = self.capture.run_capture("recurrence", prior)
        self.clock = "2026-09-07T04:01:00Z"
        review.import_capture(self.queue, recurrence)
        recurrent = self.candidate()
        self.assertEqual(dismissed["id"], recurrent["id"])
        self.assertEqual("dismissed", recurrent["status"])
        self.assertEqual(dismissed["last_event_id"], recurrent["last_event_id"])
        self.assertEqual(0, self.report()["pending_count"])
        self.assertTrue(recurrent["requires_reopen"])
        self.assertEqual(1, self.report()["recheck_count"])
        self.assertTrue(self.report()["attention_required"])
        self.clock = "2026-09-07T04:02:00Z"
        self.decide("reopen")
        self.assertEqual("pending", self.candidate()["status"])
        self.assertEqual(1, self.report()["pending_count"])
        self.assertFalse(self.candidate()["requires_reopen"])
        self.assertEqual(0, self.report()["recheck_count"])


if __name__ == "__main__":
    unittest.main()
