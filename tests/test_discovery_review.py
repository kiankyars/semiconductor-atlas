from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

from semiconductor_atlas import discovery_review as review
from semiconductor_atlas import curated_review
from tests import test_nist_discovery as fixtures
from tests.test_nist_discovery_parser import document, news, pager


class DiscoveryReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NISTDiscoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.queue = self.root / "discovery.sqlite"
        patched = patch.object(review, "_now", side_effect=self.fixture.clock)
        patched.start()
        self.addCleanup(patched.stop)
        review.initialize_queue(self.queue)

    def capture(self, name="capture"):
        path, _ = self.fixture.run_capture(name)
        return path

    def admit(self, name="capture"):
        path = self.capture(name)
        return path, review.import_capture(self.queue, path)

    def candidate(self, company="TSMC"):
        return next(row for row in review.queue_report(self.queue)["candidates"] if company in row["matched_companies"])

    def decide(self, action, company="TSMC", **kwargs):
        row = self.candidate(company)
        return review.record_decision(self.queue, row["id"], action=action, reviewer="fixture reviewer",
            reason="fixture scoped review", expected_event_id=row["last_event_id"], **kwargs)

    def test_idempotence_routing_and_separate_queue_contract(self):
        self.fixture.bodies["news-page-0"] = document(news(title="Unmatched organization") + pager())
        path, imported = self.admit()
        self.assertTrue(imported["imported"])
        self.assertFalse(review.import_capture(self.queue, path)["imported"])
        report = review.verify_queue(self.queue)
        self.assertEqual((1, 4, 3, 1), tuple(report[key] for key in ("run_count", "candidate_count", "pending_count", "unrouted_pending_count")))
        self.assertFalse(report["claim_acceptance"])
        self.assertFalse(report["linked_document_acquisition_allowed"])
        with self.assertRaises(ValueError):
            curated_review.queue_report(self.queue)
        other = self.root / "old.sqlite"
        curated_review.initialize_queue(other)
        with self.assertRaises(ValueError):
            review.queue_report(other)

    def test_admission_clock_not_capture_clock_controls_history(self):
        path = self.capture()
        cutoff = self.fixture.clock()
        self.fixture.advance(60)
        review.import_capture(self.queue, path)
        self.assertEqual(0, review.queue_report(self.queue, as_of=cutoff)["candidate_count"])
        self.assertEqual(4, review.queue_report(self.queue)["candidate_count"])
        with self.assertRaises(ValueError):
            review.queue_report(self.queue, as_of="not a clock")

    def test_unchanged_capture_does_not_reopen_or_invalidate_review_token(self):
        self.admit()
        closed = self.decide("dismiss")
        self.fixture.advance(3600)
        self.admit("repeat")
        row = self.candidate()
        self.assertEqual(closed["last_event_id"], row["last_event_id"])
        self.assertFalse(row["requires_reopen"])
        self.assertEqual("dismissed", row["status"])
        self.assertEqual(2, len(row["observations"]))

    def test_new_metadata_and_observed_return_require_explicit_reopen(self):
        self.admit()
        closed = self.decide("handoff", evidence_ref="review.json#scope-only")
        self.fixture.advance(3600)
        self.fixture.bodies["news-page-0"] = document(news(title="TSMC changed project context") + pager())
        self.admit("changed")
        row = self.candidate()
        self.assertEqual(closed["id"], row["id"])
        self.assertTrue(row["requires_reopen"])
        self.assertNotEqual(closed["last_event_id"], row["last_event_id"])
        self.assertEqual(2, row["metadata_version_count"])
        with self.assertRaises(ValueError):
            self.decide("dismiss")
        self.decide("reopen")
        self.decide("dismiss")
        self.fixture.advance(3600)
        self.fixture.bodies["news-page-0"] = document(news() + pager())
        self.admit("return")
        self.assertTrue(self.candidate()["requires_reopen"])

    def test_failures_and_rolling_window_do_not_erase_candidates(self):
        self.admit()
        first = self.candidate()
        self.fixture.advance(3600)
        self.fixture.bodies["news-page-0"] = document(news(url="/news-events/news/2026/07/new-announcement", title="Amkor announcement"))
        self.admit("rolling")
        self.fixture.advance(3600)
        self.fixture.bodies["nist-rights"] += b"<p>Changed rights</p>"
        self.admit("failed")
        report = review.verify_queue(self.queue)
        self.assertEqual(5, report["candidate_count"])
        self.assertTrue(report["latest_capture_attention_required"])
        self.assertEqual(first["observations"], self.candidate()["observations"])

    def test_stale_decisions_invalid_actions_and_handoff_without_reference_reject(self):
        self.admit()
        original = self.candidate()
        self.decide("acknowledge")
        with self.assertRaises(ValueError):
            review.record_decision(self.queue, original["id"], action="dismiss", reviewer="reviewer", reason="reason", expected_event_id=original["last_event_id"])
        for action in ("approve", "reopen", "handoff"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.decide(action)

    def test_export_restore_replays_decisions_and_all_cutoffs_exactly(self):
        self.admit()
        cutoff = self.fixture.clock()
        self.fixture.advance(1)
        self.decide("defer")
        artifact = review.export_events(self.queue)
        path = self.root / "events.json"
        path.write_bytes(review._pretty_bytes(artifact))
        restored = self.root / "restored.sqlite"
        review.restore_queue(restored, path)
        self.assertEqual(artifact, review.export_events(restored))
        self.assertEqual(review.verify_queue(self.queue), review.verify_queue(restored))
        self.assertEqual(review.queue_report(self.queue, as_of=cutoff), review.queue_report(restored, as_of=cutoff))
        artifact["events"][0]["payload"]["result"]["claim_acceptance"] = True
        path.write_text(json.dumps(artifact))
        with self.assertRaises(ValueError):
            review.restore_queue(self.root / "tampered.sqlite", path)

    def test_retained_packet_tampering_and_append_only_database(self):
        path, _ = self.admit()
        with closing(sqlite3.connect(self.queue)) as connection:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("UPDATE review_events SET recorded_at='changed'")
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("DELETE FROM review_events")
        (path / "responses/news-page-0.body").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            review.verify_queue(self.queue)

    def test_future_capture_admission_and_backwards_decision_reject(self):
        path = self.capture()
        self.fixture.advance(-60)
        with self.assertRaises(ValueError):
            review.import_capture(self.queue, path)
        self.assertEqual(0, review.queue_report(self.queue)["event_count"])
        self.fixture.advance(120)
        review.import_capture(self.queue, path)
        self.fixture.advance(-1)
        with self.assertRaises(ValueError):
            self.decide("defer")

    def test_out_of_order_admission_uses_observation_clocks(self):
        first = self.capture("first")
        self.fixture.advance(3600)
        second = self.capture("second")
        review.import_capture(self.queue, second)
        review.import_capture(self.queue, first)
        row = self.candidate()
        self.assertLess(review._instant(row["first_seen_at"]), review._instant(row["last_seen_at"]))
        self.assertEqual(1, row["metadata_version_count"])

    def test_equal_time_health_keeps_both_runs_and_any_failure_requires_attention(self):
        start = self.fixture.now
        first = self.capture("first")
        self.fixture.now = start
        self.fixture.overrides["news-page-1"] = {"http_code": 500}
        second = self.capture("second")
        review.import_capture(self.queue, first)
        review.import_capture(self.queue, second)
        report = review.verify_queue(self.queue)
        self.assertEqual(2, len(report["latest_run_ids"]))
        self.assertTrue(report["latest_capture_attention_required"])


if __name__ == "__main__":
    unittest.main()
