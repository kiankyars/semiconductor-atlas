from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from semiconductor_atlas import curated_observation_population as population
from semiconductor_atlas import curated_poll as poll, curated_review as queue
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from scripts import freeze_curated_observations as cli
from tests import test_curated_poll as fixtures


class CuratedObservationPopulationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CuratedPollTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        (self.root / "poll-captures").mkdir()
        (self.root / "poll-state").mkdir()
        self.study_path = self.root / "study.json"
        self.frozen_path = self.root / "frozen.json"
        self.study = {"format": population.STUDY_FORMAT, "study_id": "fixture-not-a-blind-study",
            "start": "2026-09-07T02:00:00Z", "end": "2026-09-07T06:00:00Z", "queue_path": "queue.sqlite",
            "retention_roots": [{"capture_root": "poll-captures", "poll_state_root": "poll-state"}], "seed_ledgers": []}
        clock = patch.object(population, "_now", return_value="2026-09-07T07:00:00Z")
        clock.start(); self.addCleanup(clock.stop)

    def freeze(self):
        self.study_path.write_bytes(_pretty_bytes(self.study))
        return population.freeze_population(self.study_path, reference_root=self.root)

    def verify(self, frozen):
        self.frozen_path.write_bytes(_pretty_bytes(frozen))
        return population.verify_population_sources(self.frozen_path, reference_root=self.root)

    def test_all_checks_and_not_due_receipts_are_separate_and_replay(self):
        self.fixture.tick()
        self.fixture.tick("2026-09-07T03:30:00Z")
        self.fixture.tick("2026-09-07T04:00:00Z")
        before = self.fixture.queue.read_bytes()
        calls = len(self.fixture.capture.calls)
        frozen = self.freeze()
        self.assertEqual(2, frozen["counts"]["document_checks_in_window"])
        self.assertEqual({"first_observation_requires_review": 1, "unchanged": 1}, frozen["counts"]["document_statuses_in_window"])
        self.assertEqual({"captured_imported": 2, "not_due": 1}, frozen["counts"]["completed_poll_plan_outcomes_by_end"])
        self.assertEqual(1, frozen["counts"]["paired_byte_text_comparisons_in_window"])
        self.assertEqual(0, frozen["counts"]["target_revision_labels"])
        self.assertTrue(all(value is False for value in frozen["boundaries"].values()))
        self.assertTrue(self.verify(frozen)["exact_retention_snapshot_replayed"])
        self.assertEqual(before, self.fixture.queue.read_bytes())
        self.assertEqual(calls, len(self.fixture.capture.calls))

    def test_policy_blocked_is_a_check_not_a_request_or_no_change(self):
        self.fixture.capture.bodies["rights"] += b"<p>Changed terms</p>"
        self.fixture.tick()
        frozen = self.freeze()
        self.assertEqual(1, frozen["counts"]["document_checks_in_window"])
        self.assertEqual(0, frozen["counts"]["document_requests_in_window"])
        row = frozen["observations"][0]
        self.assertEqual("not_attempted_policy_blocked", row["status"])
        self.assertIsNone(row["response_finished_at"])
        self.assertFalse(row["comparison_eligible"])

    def test_failed_document_transport_is_retained(self):
        self.fixture.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        self.fixture.tick()
        row = self.freeze()["observations"][0]
        self.assertEqual("failed_check", row["status"])
        self.assertTrue(row["attempted"])
        self.assertEqual("failed", row["transport_outcome"])
        self.assertIsNone(row["target_revision_verdict"])

    def test_completed_unimported_capture_is_in_denominator(self):
        with patch.object(poll, "import_capture", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.fixture.tick()
        frozen = self.freeze()
        self.assertEqual(1, frozen["counts"]["document_checks_in_window"])
        self.assertEqual(1, frozen["counts"]["queue_unadmitted_checks_by_end"])
        self.assertEqual(1, frozen["counts"]["poll_invocations_not_completed_by_end"])
        self.assertEqual(0, queue.queue_report(self.fixture.queue)["run_count"])

    def test_interrupted_intent_remains_uncomparable_without_fabricated_request(self):
        with patch.object(poll, "capture_sources", side_effect=ValueError("fixture interruption")):
            self.fixture.tick()
        frozen = self.freeze()
        self.assertEqual(0, frozen["counts"]["document_requests_in_window"])
        self.assertEqual(1, frozen["counts"]["incomplete_document_opportunities_at_cutoff"])
        self.assertEqual("unknown_without_completed_capture", frozen["snapshot"]["incomplete_captures"][0]["request_outcome"])
        self.assertEqual("not_completed_by_cutoff", frozen["incomplete_at_cutoff"][0]["status"])

    def test_later_completion_does_not_rewrite_incomplete_cutoff(self):
        original = self.fixture.capture.transport
        def advance(entry, destination, plan):
            result = original(entry, destination, plan)
            if entry["id"] == "document":
                self.fixture.clock = "2026-09-07T03:00:00.001000Z"
                self.fixture.capture.clock = self.fixture.clock
                self.fixture.fixture.clock = self.fixture.clock
            return result
        self.fixture.capture.transport = advance
        self.fixture.tick()
        self.study["end"] = "2026-09-07T03:00:00.000500Z"
        frozen = self.freeze()
        self.assertEqual(0, frozen["counts"]["document_checks_in_window"])
        self.assertEqual(1, frozen["counts"]["incomplete_document_opportunities_at_cutoff"])
        self.assertEqual("at_or_after_end", frozen["observations"][0]["window_membership"])

    def test_start_and_exclusive_end_preserve_microseconds(self):
        self.fixture.tick("2026-09-07T03:00:00.123456Z")
        self.study["start"] = "2026-09-07T03:00:00.123456Z"
        self.assertEqual(1, self.freeze()["counts"]["document_checks_in_window"])
        self.study["start"] = "2026-09-07T03:00:00.123457Z"
        self.assertEqual(0, self.freeze()["counts"]["document_checks_in_window"])
        self.study["start"] = "2026-09-07T02:00:00Z"
        self.study["end"] = "2026-09-07T03:00:00.123456Z"
        self.assertEqual(0, self.freeze()["counts"]["document_checks_in_window"])

    def test_actual_shared_seed_is_not_replaced_by_chronological_adjacency(self):
        capture = self.fixture.capture
        seed, _ = capture.run_capture("seed")
        self.study["seed_ledgers"] = [{"path": "seed/last_successful_checks.json",
            "sha256": population._hash((seed / "last_successful_checks.json").read_bytes())}]
        capture.clock = "2026-09-07T04:00:00Z"
        first, _ = capture.run_capture("poll-captures/first", seed)
        capture.clock = "2026-09-07T05:00:00Z"
        second, _ = capture.run_capture("poll-captures/second", seed)
        self.fixture.fixture.clock = "2026-09-07T05:10:00Z"
        queue.import_capture(self.fixture.queue, second)
        self.fixture.fixture.clock = "2026-09-07T05:20:00Z"
        queue.import_capture(self.fixture.queue, first)
        frozen = self.freeze()
        rows = frozen["observations"]
        self.assertEqual(2, len(rows))
        self.assertEqual({self.study["seed_ledgers"][0]["sha256"]}, {row["predecessor"]["ledger_sha256"] for row in rows})
        self.assertEqual({"2026-09-07T03:00:00Z"}, {row["predecessor"]["response_finished_at"] for row in rows})
        self.assertEqual(1, frozen["counts"]["seed_attempts_retained_separately"])

    def test_prior_window_predecessor_is_retained_but_not_new_request(self):
        self.fixture.tick()
        self.fixture.tick("2026-09-07T04:00:00Z")
        self.study["start"] = "2026-09-07T04:00:00Z"
        frozen = self.freeze()
        self.assertEqual(2, len(frozen["observations"]))
        self.assertEqual(1, frozen["counts"]["document_requests_in_window"])
        selected = next(row for row in frozen["observations"] if row["window_membership"] == "in_window")
        self.assertEqual("2026-09-07T03:00:00Z", selected["predecessor"]["response_finished_at"])

    def test_invalid_completed_packet_fails_instead_of_becoming_no_change(self):
        self.fixture.tick()
        packet = next((self.root / "poll-captures").iterdir())
        (packet / "responses/document.body").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            self.freeze()

    def test_retention_input_drift_during_projection_fails(self):
        self.fixture.tick()
        original = population._projection
        def mutate(*args):
            result = original(*args)
            tick = next((self.root / "poll-state").iterdir())
            (tick / "extra.json").write_text("{}")
            return result
        with patch.object(population, "_projection", side_effect=mutate), self.assertRaisesRegex(ValueError, "changed across freeze"):
            self.freeze()

    def test_external_replay_rechecks_retention_at_final_boundary(self):
        self.fixture.tick()
        frozen = self.freeze()
        original = population._projection
        def mutate(*args):
            result = original(*args)
            tick = next((self.root / "poll-state").iterdir())
            request = tick / "request.json"
            request.write_bytes(request.read_bytes() + b" ")
            return result
        with patch.object(population, "_projection", side_effect=mutate), self.assertRaisesRegex(ValueError, "changed across source verification"):
            self.verify(frozen)

    def test_future_poll_outcome_is_not_accepted_as_existing_knowledge(self):
        self.fixture.tick()
        tick = next((self.root / "poll-state").iterdir())
        outcome_path = next((tick / "jobs").iterdir()) / "outcome.json"
        outcome = json.loads(outcome_path.read_bytes())
        outcome["observed_at"] = "2026-09-07T08:00:00Z"
        outcome_path.write_bytes(_pretty_bytes(outcome))
        with self.assertRaisesRegex(ValueError, "after freeze"):
            self.freeze()

    def test_symlinks_and_overlapping_roots_are_rejected(self):
        self.fixture.tick()
        self.study["retention_roots"] *= 2
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.freeze()
        self.study["retention_roots"].pop()
        (self.root / "poll-captures/link").symlink_to(self.root / "plans", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.freeze()

    def test_frozen_metric_scope_and_code_tampering_are_rejected(self):
        self.fixture.tick()
        frozen = self.freeze()
        for field, value in (("scope", "publisher complete"), ("counts", {}), ("code_sha256", {})):
            changed = {**frozen, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(changed)

    def test_external_replay_allows_later_captures_without_rewriting_old_population(self):
        self.fixture.tick()
        frozen = self.freeze()
        self.fixture.tick("2026-09-07T04:00:00Z")
        result = self.verify(frozen)
        self.assertTrue(result["original_queue_prefix_verified"])
        self.assertTrue(result["later_retained_additions_allowed"])
        self.assertEqual(1, result["counts"]["document_checks_in_window"])

    def test_future_window_and_missing_queue_do_not_create_database(self):
        self.study["end"] = "2026-09-07T08:00:00Z"
        with self.assertRaises(ValueError):
            self.freeze()
        self.study["end"] = "2026-09-07T06:00:00Z"
        self.study["queue_path"] = "missing.sqlite"
        with self.assertRaises(ValueError):
            self.freeze()
        self.assertFalse((self.root / "missing.sqlite").exists())

    def test_cli_creates_only_new_artifacts_and_verifies_read_only(self):
        self.fixture.tick()
        self.study_path.write_bytes(_pretty_bytes(self.study))
        args = ["freeze_curated_observations.py", "freeze", "--study", str(self.study_path),
            "--reference-root", str(self.root), "--output", str(self.frozen_path)]
        with patch("sys.argv", args), redirect_stdout(io.StringIO()):
            cli.main()
        original = self.frozen_path.read_bytes()
        with patch("sys.argv", args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main()
        self.assertEqual(original, self.frozen_path.read_bytes())
        with patch("sys.argv", [args[0], "verify-sources", "--frozen", str(self.frozen_path), "--reference-root", str(self.root)]), redirect_stdout(io.StringIO()):
            cli.main()


if __name__ == "__main__":
    unittest.main()
