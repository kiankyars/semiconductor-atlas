from __future__ import annotations

import copy
import io
from datetime import timedelta
import json
from pathlib import Path
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from semiconductor_atlas import prospective_target_review as shadow
from semiconductor_atlas import curated_observation_population as population, curated_poll as poll
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from tests import test_curated_observation_population as fixtures
from scripts import shadow_source_targets as cli


class ProspectiveTargetReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CuratedObservationPopulationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.poll = self.fixture.root, self.fixture.fixture
        self.clock = "2026-09-07T03:40:00Z"
        for module in (shadow, population):
            clock = patch.object(module, "_now", side_effect=lambda: self.clock)
            clock.start()
            self.addCleanup(clock.stop)
        self.poll.tick()
        self.fixture.study["end"] = "2026-09-07T03:30:00Z"
        self.baseline = self.root / "baseline-population.json"
        self.baseline.write_bytes(_pretty_bytes(self.fixture.freeze()))
        self.prediction_root = self.root / "predictions"
        self.prediction_root.mkdir()
        self.study_path = self.root / "prospective-study.json"
        self.registration_path = self.root / "registration.json"
        self.study = {"format": shadow.STUDY_FORMAT, "study_id": "synthetic-prospective-study",
            "start": "2026-09-07T04:00:00Z", "end": "2026-09-07T08:00:00Z",
            "config": {"path": str(self.poll.config_path.relative_to(self.root)),
                       "sha256": shadow._hash(self.poll.config_path.read_bytes())},
            "baseline_population": {"path": self.baseline.name, "sha256": shadow._hash(self.baseline.read_bytes())},
            "prediction_root": "predictions", "prediction_deadline_seconds": 3600,
            "target_scope": shadow.TARGET_SCOPE, "stopping_rule": shadow.STOPPING_RULE,
            "prior_exposure": "Synthetic fixtures, not unseen real-world evaluation."}
        self.clock = "2026-09-07T03:50:00Z"

    def register(self):
        self.study_path.write_bytes(_pretty_bytes(self.study))
        shadow.accept_registration(self.study_path, self.registration_path, reference_root=self.root)
        return json.loads(self.registration_path.read_bytes())

    def record(self, clock="2026-09-07T04:10:00Z"):
        self.clock = clock
        return shadow.record(self.registration_path, reference_root=self.root)

    def seal(self):
        self.clock = "2026-09-07T09:00:00Z"
        return shadow.seal(self.registration_path, reference_root=self.root)

    def test_registration_before_window_and_actual_end_clock(self):
        result = self.register()
        self.assertEqual(1, len(result["inputs"]["documents"]))
        self.assertEqual(6, len(result["inputs"]["unmonitored_facilities"]))
        self.assertTrue(all(value is False for value in result["boundaries"].values()))
        self.clock = self.study["start"]
        with self.assertRaises(ValueError):
            shadow.register(self.study_path, reference_root=self.root)
        with patch.object(shadow, "_now", side_effect=["2026-09-07T03:50:00Z", "2026-09-07T04:00:00Z"]):
            with self.assertRaises(ValueError):
                shadow.register(self.study_path, reference_root=self.root)
        with patch.object(shadow, "_now", side_effect=["2026-09-07T03:50:00Z", "2026-09-07T03:49:00Z"]):
            with self.assertRaises(ValueError):
                shadow.register(self.study_path, reference_root=self.root)

    def test_all_configured_documents_are_derived_without_allowlist(self):
        document = self.poll.capture.plan["documents"][0]
        for index in range(1, 7):
            item = {**copy.deepcopy(document), "id": f"document-{index}", "url": f"https://example.org/source-{index}"}
            self.poll.capture.plan["documents"].append(item)
            self.poll.capture.bodies[item["id"]] = self.poll.capture.bodies[document["id"]]
        self.poll.repin_plan()
        self.study["config"]["sha256"] = shadow._hash(self.poll.config_path.read_bytes())
        registration = self.register()
        self.assertEqual(7, len(registration["inputs"]["documents"]))
        self.assertEqual(7, len({row["url"] for row in registration["inputs"]["documents"]}))

    def test_registration_replays_after_later_captures_and_queue_events(self):
        original = self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.clock = "2026-09-07T04:10:00Z"
        loaded, _ = shadow.validate_registration(self.registration_path, reference_root=self.root)
        self.assertEqual(original, loaded)

    def test_config_scope_or_code_tampering_fails(self):
        self.register()
        original = self.poll.config_path.read_bytes()
        self.poll.config_path.write_bytes(original + b" ")
        with self.assertRaises(ValueError):
            shadow.validate_registration(self.registration_path, reference_root=self.root)
        self.poll.config_path.write_bytes(original)
        with patch.object(shadow, "_code_hashes", return_value={}):
            with self.assertRaises(ValueError):
                shadow.validate_registration(self.registration_path, reference_root=self.root)

    def test_future_window_cannot_outlive_plan_approval(self):
        self.study["end"] = (shadow._instant(self.poll.capture.plan["expires_at"]) + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        with self.assertRaises(ValueError):
            self.register()

    def test_prediction_root_rejects_sources_traversal_alias_and_nonempty(self):
        (self.root / "source-alias").symlink_to(self.root / "poll-captures", target_is_directory=True)
        for name in ("poll-captures", "poll-state", "source-alias", "predictions/../poll-captures", "."):
            self.study["prediction_root"] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.register()
        self.study["prediction_root"] = "predictions"
        (self.prediction_root / "existing.json").write_bytes(b"{}")
        with self.assertRaises(ValueError):
            self.register()

    def test_prestart_record_is_no_work_without_network_or_queue_writes(self):
        self.register()
        before, calls = self.poll.queue.read_bytes(), len(self.poll.capture.calls)
        result = shadow.record(self.registration_path, reference_root=self.root)
        self.assertEqual("not_started", result["status"])
        self.assertEqual([], list(self.prediction_root.iterdir()))
        self.assertEqual(before, self.poll.queue.read_bytes())
        self.assertEqual(calls, len(self.poll.capture.calls))

    def test_record_receipt_is_idempotent_and_uses_actual_predecessor(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        original_queue, calls = self.poll.queue.read_bytes(), len(self.poll.capture.calls)
        recorded = self.record()
        batch_path = self.root / "predictions" / (recorded["output"].split("/")[-1])
        batch = json.loads(batch_path.read_bytes())
        row = batch["predictions"][0]
        self.assertTrue(row["in_protocol"])
        self.assertEqual("2026-09-07T03:00:00Z", row["case"]["predecessor"]["response_finished_at"])
        retained = {path: path.read_bytes() for path in self.prediction_root.iterdir()}
        self.assertEqual("no_new_opportunities", self.record("2026-09-07T04:20:00Z")["status"])
        self.assertEqual(retained, {path: path.read_bytes() for path in self.prediction_root.iterdir()})
        final = self.seal()
        self.assertEqual({"on_time": 1}, final["counts"]["recording_statuses"])
        self.assertEqual("2026-09-07T04:10:00Z", final["cases"][0]["first_recorded_at"])
        self.assertTrue(all(value is None for value in final["metrics"].values()))
        self.assertEqual(original_queue, self.poll.queue.read_bytes())
        self.assertEqual(calls, len(self.poll.capture.calls))

    def test_failed_and_policy_blocked_checks_are_abstentions(self):
        self.register()
        self.poll.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        self.poll.tick("2026-09-07T04:00:00Z")
        self.record()
        self.poll.capture.bodies["rights"] += b"<p>Changed access policy</p>"
        self.poll.tick("2026-09-07T05:00:00Z")
        self.record("2026-09-07T05:10:00Z")
        final = self.seal()
        self.assertEqual(2, final["counts"]["final_document_opportunities"])
        self.assertEqual({"abstain"}, {row["prediction"]["result"] for row in final["cases"]})
        self.assertEqual({"failed_check", "not_attempted_policy_blocked"}, {row["case"]["status"] for row in final["cases"]})

    def test_first_observation_abstains_without_substituting_registration_anchor(self):
        document = {**self.poll.capture.plan["documents"][0], "id": "first", "url": "https://example.org/new-document"}
        self.poll.capture.plan["documents"].append(document)
        self.poll.capture.bodies["first"] = self.poll.capture.bodies["document"]
        self.poll.repin_plan()
        self.study["config"]["sha256"] = shadow._hash(self.poll.config_path.read_bytes())
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.record()
        row = next(row for row in self.seal()["cases"] if row["case"]["document_id"] == "first")
        self.assertEqual("abstain", row["prediction"]["result"])
        self.assertIsNone(row["case"]["predecessor"])

    def test_missing_late_and_incomplete_opportunities_do_not_become_quiet(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.record("2026-09-07T05:10:00Z")
        self.poll.tick("2026-09-07T06:00:00Z")
        with patch.object(poll, "capture_sources", side_effect=ValueError("synthetic interruption")):
            self.poll.tick("2026-09-07T07:00:00Z")
        final = self.seal()
        self.assertEqual({"late": 1, "missing": 2}, final["counts"]["recording_statuses"])
        self.assertEqual(3, final["counts"]["final_document_opportunities"])
        self.assertEqual(1, sum(row["case"]["kind"] == "incomplete_document" for row in final["cases"]))

    def test_forced_check_remains_outside_registered_ordinary_protocol(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z", force=True)
        self.record()
        final = self.seal()
        self.assertEqual(1, final["counts"]["outside_protocol_opportunities"])
        self.assertEqual("forced_poll_capture", final["cases"][0]["prediction"]["reason"])

    def test_later_import_does_not_erase_existing_prediction(self):
        self.register()
        previous = set((self.root / "poll-captures").iterdir())
        original_import = poll.import_capture
        def interrupt_new_capture(queue_path, capture_path):
            if capture_path not in previous:
                raise KeyboardInterrupt
            return original_import(queue_path, capture_path)
        with patch.object(poll, "import_capture", side_effect=interrupt_new_capture):
            with self.assertRaises(KeyboardInterrupt):
                self.poll.tick("2026-09-07T04:00:00Z")
        self.record()
        self.poll.tick("2026-09-07T04:20:00Z")
        final = self.seal()
        self.assertEqual({"on_time": 1}, final["counts"]["recording_statuses"])
        self.assertEqual("2026-09-07T04:10:00Z", final["cases"][0]["first_recorded_at"])

    def test_prediction_body_and_receipt_clock_tamper_fail_replay(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        result = self.record()
        receipt_path = next(self.prediction_root.glob("*.receipt.json"))
        original = receipt_path.read_bytes()
        receipt = json.loads(original)
        receipt["accepted_at"] = "2026-09-07T04:09:59Z"
        receipt_path.write_bytes(_pretty_bytes(receipt))
        with self.assertRaises(ValueError):
            self.seal()
        receipt_path.write_bytes(original)
        batch_path = self.root / "predictions" / result["output"].split("/")[-1]
        batch = json.loads(batch_path.read_bytes())
        batch["predictions"][0]["result"] = "revision_candidate"
        batch_path.write_bytes(_pretty_bytes(batch))
        with self.assertRaises(ValueError):
            self.seal()

    def test_uncommitted_batch_does_not_supply_verified_timing(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.clock = "2026-09-07T04:10:00Z"
        batch = shadow.predict(self.registration_path, reference_root=self.root)
        (self.prediction_root / (shadow._hash(batch) + ".json")).write_bytes(_pretty_bytes(batch))
        final = self.seal()
        self.assertEqual(1, final["counts"]["uncommitted_batches"])
        self.assertEqual({"missing": 1}, final["counts"]["recording_statuses"])

    def test_early_seal_rejected_and_no_check_is_not_a_no_change_label(self):
        self.register()
        self.clock = "2026-09-07T08:59:59Z"
        with self.assertRaises(ValueError):
            shadow.seal(self.registration_path, reference_root=self.root)
        final = self.seal()
        self.assertEqual(0, final["counts"]["final_document_opportunities"])
        self.assertEqual(1, final["counts"]["documents_without_checks"])
        self.assertTrue(all(value is None for value in final["metrics"].values()))

    def test_acceptance_clock_is_sampled_after_durable_batch_write(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        original = shadow.vintage.write_new
        def delayed_write(path, data, **kwargs):
            result = original(path, data, **kwargs)
            if data.get("format") == shadow.BATCH_FORMAT:
                self.clock = "2026-09-07T06:00:00Z"
            return result
        with patch.object(shadow.vintage, "write_new", side_effect=delayed_write):
            self.record()
        final = self.seal()
        self.assertEqual({"late": 1}, final["counts"]["recording_statuses"])
        self.assertEqual("2026-09-07T06:00:00Z", final["cases"][0]["first_recorded_at"])

    def test_sealed_source_and_prediction_replay_allows_later_captures(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.record()
        final = self.seal()
        seal_path = self.prediction_root / "seal.json"
        seal_path.write_bytes(_pretty_bytes(final))
        self.poll.tick("2026-09-07T10:00:00Z")
        self.clock = "2026-09-07T10:10:00Z"
        verified = shadow.validate_seal(self.registration_path, reference_root=self.root)
        self.assertTrue(verified["exact_source_and_prediction_replay"])
        self.assertEqual(1, verified["counts"]["final_document_opportunities"])
        with self.assertRaises(ValueError):
            self.record("2026-09-07T10:20:00Z")
        final["counts"]["final_document_opportunities"] = 0
        seal_path.write_bytes(_pretty_bytes(final))
        with self.assertRaises(ValueError):
            shadow.validate_seal(self.registration_path, reference_root=self.root)

    def test_seal_rejects_omitted_batch_and_extra_journal_content(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.record()
        final = self.seal()
        final["batches"] = []
        (self.prediction_root / "seal.json").write_bytes(_pretty_bytes(final))
        with self.assertRaises(ValueError):
            shadow.validate_seal(self.registration_path, reference_root=self.root)

    def test_detector_exception_is_retained_as_error_not_quiet(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        with patch.object(shadow.detector, "analyze", side_effect=RuntimeError("synthetic parser error")):
            self.record()
        final = self.seal()
        self.assertEqual("error", final["cases"][0]["prediction"]["result"])
        (self.prediction_root / "seal.json").write_bytes(_pretty_bytes(final))
        self.assertTrue(shadow.validate_seal(self.registration_path, reference_root=self.root)["exact_source_and_prediction_replay"])

    def test_lock_blocks_competing_record_without_touching_sources(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        original = self.poll.queue.read_bytes()
        with poll._lock(self.prediction_root):
            with self.assertRaisesRegex(ValueError, "lock"):
                self.record()
        self.assertEqual([], list(self.prediction_root.iterdir()))
        self.assertEqual(original, self.poll.queue.read_bytes())

    def test_not_due_invocations_are_not_document_predictions(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.record()
        self.poll.tick("2026-09-07T04:30:00Z")
        self.assertEqual("no_new_opportunities", self.record("2026-09-07T04:40:00Z")["status"])
        final = self.seal()
        self.assertEqual(1, final["counts"]["final_document_opportunities"])
        self.assertEqual(1, final["final_population"]["counts"]["completed_poll_plan_outcomes_by_end"]["not_due"])

    def test_source_tampering_fails_instead_of_silent_denominator_loss(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        result = self.record()
        batch = json.loads(Path(result["output"]).read_bytes())
        body_path = self.root / batch["predictions"][0]["case"]["body_path"]
        body_path.write_bytes(b"tampered retained body")
        with self.assertRaises(ValueError):
            self.seal()

    def invoke(self, *args):
        output = io.StringIO()
        with patch("sys.argv", ["shadow_source_targets.py", *map(str, args)]), redirect_stdout(output), redirect_stderr(io.StringIO()):
            cli.main()
        return json.loads(output.getvalue())

    def test_registration_acceptance_requires_durable_prestart_draft(self):
        self.study_path.write_bytes(_pretty_bytes(self.study))
        original = shadow.vintage.write_new
        def delayed_write(path, data, **kwargs):
            result = original(path, data, **kwargs)
            if data.get("format") == shadow.REGISTRATION_FORMAT:
                self.clock = self.study["start"]
            return result
        with patch.object(shadow.vintage, "write_new", side_effect=delayed_write):
            with self.assertRaisesRegex(ValueError, "acceptance crossed"):
                shadow.accept_registration(self.study_path, self.registration_path, reference_root=self.root)
        self.assertTrue(self.registration_path.exists())
        self.assertFalse(shadow._registration_receipt_path(self.registration_path).exists())
        with self.assertRaises((ValueError, OSError)):
            shadow.validate_registration(self.registration_path, reference_root=self.root)

    def test_registration_receipt_binding_and_clock_are_verified(self):
        self.register()
        path = shadow._registration_receipt_path(self.registration_path)
        original = json.loads(path.read_bytes())
        for key, value in (("accepted_at", self.study["start"]), ("registration_sha256", "0" * 64)):
            path.write_bytes(_pretty_bytes({**original, key: value}))
            with self.subTest(key=key), self.assertRaises(ValueError):
                shadow.validate_registration(self.registration_path, reference_root=self.root)

    def test_code_drift_after_batch_write_prevents_receipt_acceptance(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        original = shadow.vintage.write_new
        def changed_write(path, data, **kwargs):
            result = original(path, data, **kwargs)
            if data.get("format") == shadow.BATCH_FORMAT:
                changed = patch.object(shadow, "_code_hashes", return_value={})
                changed.start()
                self.addCleanup(changed.stop)
            return result
        with patch.object(shadow.vintage, "write_new", side_effect=changed_write):
            with self.assertRaises(ValueError):
                self.record()
        self.assertEqual(1, len(list(self.prediction_root.glob("*.json"))))
        self.assertEqual([], list(self.prediction_root.glob("*.receipt.json")))

    def test_seal_rejects_a_concurrently_expanded_source_denominator(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.record()
        original = shadow._freeze
        def expanding_freeze(*args):
            result = original(*args)
            self.poll.tick("2026-09-07T07:00:00Z")
            return result
        with patch.object(shadow, "_freeze", side_effect=expanding_freeze):
            with self.assertRaisesRegex(ValueError, "retention population changed"):
                self.seal()

    def test_seal_replay_rejects_late_disclosed_in_window_observation(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.record()
        final = self.seal()
        (self.prediction_root / "seal.json").write_bytes(_pretty_bytes(final))
        self.poll.tick("2026-09-07T07:00:00Z")
        with self.assertRaisesRegex(ValueError, "in-window opportunity population"):
            shadow.validate_seal(self.registration_path, reference_root=self.root)

    def test_cli_seal_holds_the_collector_lock(self):
        self.register()
        self.clock = "2026-09-07T09:00:00Z"
        with poll._lock(self.poll.queue):
            with self.assertRaises(SystemExit):
                self.invoke("seal", "--registration", self.registration_path, "--reference-root", self.root)
        self.assertFalse((self.prediction_root / "seal.json").exists())

    def test_advance_records_closes_and_replays_without_new_collection(self):
        self.register()
        args = ("advance", "--registration", self.registration_path, "--reference-root", self.root)
        self.assertEqual("not_started", self.invoke(*args)["status"])
        self.poll.tick("2026-09-07T04:00:00Z")
        self.clock = "2026-09-07T04:10:00Z"
        self.assertEqual("recorded", self.invoke(*args)["status"])
        queue_bytes, calls = self.poll.queue.read_bytes(), len(self.poll.capture.calls)
        self.clock = "2026-09-07T09:00:00Z"
        self.assertEqual(1, self.invoke(*args)["counts"]["final_document_opportunities"])
        self.assertTrue(self.invoke(*args)["exact_source_and_prediction_replay"])
        self.assertEqual(queue_bytes, self.poll.queue.read_bytes())
        self.assertEqual(calls, len(self.poll.capture.calls))

    def test_cli_register_verify_record_seal_and_verify_seal(self):
        self.study_path.write_bytes(_pretty_bytes(self.study))
        common = ("--reference-root", self.root)
        self.invoke("register", "--study", self.study_path, "--output", self.registration_path, *common)
        with self.assertRaises(SystemExit):
            self.invoke("register", "--study", self.study_path, "--output", self.registration_path, *common)
        self.assertTrue(self.invoke("verify", "--registration", self.registration_path, *common)["registered_inputs_replayed"])
        self.poll.tick("2026-09-07T04:00:00Z")
        self.clock = "2026-09-07T04:10:00Z"
        self.invoke("record", "--registration", self.registration_path, *common)
        self.clock = "2026-09-07T09:00:00Z"
        self.invoke("seal", "--registration", self.registration_path, *common)
        self.assertTrue(self.invoke("verify-seal", "--registration", self.registration_path, *common)["exact_source_and_prediction_replay"])
        with self.assertRaises(SystemExit):
            self.invoke("seal", "--registration", self.registration_path, *common)


if __name__ == "__main__":
    unittest.main()
