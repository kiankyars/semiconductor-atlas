"""Operational coverage is not prediction accuracy or a completed study."""

import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import prospective_monitoring_status as status
from scripts import audit_prospective_monitoring as controller
from tests import test_prospective_target_review as fixtures
from tests import test_frozen_runtime as runtime_fixtures


def example_row(index, *, result="no_candidate", comparable=True, recording="on_time", protocol=True):
    return {"case_id": str(index), "opportunity_id": f"opportunity-{index}",
        "case": {"url": f"https://example.org/{index}", "document_id": str(index),
            "capture_path": f"captures/{index}", "kind": "document_check",
            "assessment_at": "2026-09-07T05:00:00Z", "attempted": comparable,
            "response_finished_at": "2026-09-07T04:59:50Z" if comparable else None,
            "transport_outcome": "succeeded" if comparable else None,
            "status": "unchanged" if comparable else "not_attempted_policy_blocked",
            "comparison_eligible": comparable},
        "prediction": {"result": result, "reason": "unchanged"} if result else None,
        "in_protocol": protocol, "collection_mode": "ordinary_registered_poll" if protocol else "forced_poll_capture",
        "recording_status": recording, "first_recorded_at": None,
        "assessment_to_prediction_seconds": 20 if recording == "on_time" else None}


def example_registration(count=7):
    return {"inputs": {"documents": [{"url": f"https://example.org/{i}", "document_id": str(i),
        "facility_key": f"facility-{i}"} for i in range(count)],
        "parser_support": [{"url": f"https://example.org/{i}", "route_implemented": i < 4} for i in range(count)],
        "unmonitored_facilities": [{"facility_key": "unmonitored-a"}, {"facility_key": "unmonitored-b"}]}}


class MonitoringProjectionTests(unittest.TestCase):
    def project(self, rows, count=7):
        return status.project(example_registration(count), {"cases": rows, "superseded_interim_case_ids": []},
                              study_start="2026-09-07T04:00:00Z", observation_cutoff="2026-09-07T08:00:00Z")

    def test_first_batch_denominators_keep_abstentions_and_policy_block(self):
        rows = [example_row(i) for i in range(4)] + [example_row(i, result="abstain") for i in (4, 5)]
        rows.append(example_row(6, result="abstain", comparable=False))
        report = self.project(rows)
        summary = report["in_protocol"]
        self.assertEqual(status.ratio(7, 7), summary["timely_recording_coverage"])
        self.assertEqual(status.ratio(4, 7), summary["timely_decision_coverage_all_opportunities"])
        self.assertEqual(status.ratio(4, 6), summary["timely_decision_coverage_comparable_opportunities"])
        self.assertEqual(7, len(report["registered_documents"]))
        self.assertEqual(2, report["counts"]["unmonitored_facilities"])
        self.assertEqual(1, summary["collection_statuses"]["not_attempted_policy_blocked"])

    def test_missing_late_error_and_abstention_are_not_quiet_decisions(self):
        rows = [example_row(0, result=None, recording="missing"),
                example_row(1, result="revision_candidate", recording="late"),
                example_row(2, result="error"), example_row(3, result="abstain")]
        rows[2]["prediction"]["reason"] = "do not copy arbitrary source text from exceptions"
        report = self.project(rows)
        self.assertEqual(status.ratio(0, 4), report["in_protocol"]["timely_decision_coverage_comparable_opportunities"])
        self.assertEqual(3, report["counts"]["registered_documents_without_in_protocol_opportunities"])
        self.assertNotIn("arbitrary source text", json.dumps(report))
        self.assertEqual("detector_error", report["cases"][2]["recorded_reason"])

    def test_empty_population_preserves_roster_and_null_ratios(self):
        report = self.project([])
        self.assertEqual(7, report["counts"]["registered_documents_without_completed_collector_assessments"])
        self.assertEqual(status.ratio(0, 0), report["in_protocol"]["timely_recording_coverage"])
        self.assertEqual(7, len(report["registered_documents"]))

    def test_outside_protocol_does_not_fill_a_registered_route(self):
        report = self.project([example_row(0, protocol=False)])
        self.assertEqual(1, report["outside_protocol"]["opportunities"])
        self.assertEqual(0, report["in_protocol"]["opportunities"])
        self.assertEqual(7, report["counts"]["registered_documents_without_in_protocol_opportunities"])

    def test_incomplete_intent_is_not_a_completed_check(self):
        row = example_row(0, result="abstain", comparable=False)
        row["case"]["kind"] = "incomplete_document"
        row["case"]["intent_started_at"] = "2026-09-07T05:00:00Z"
        del row["case"]["status"]
        report = self.project([row])
        self.assertEqual(6, report["counts"]["registered_documents_without_in_protocol_opportunities"])
        self.assertEqual(7, report["counts"]["registered_documents_without_completed_collector_assessments"])
        self.assertEqual("incomplete_document", report["cases"][0]["collection_status"])

    def test_projection_does_not_emit_detector_analysis_or_source_bodies(self):
        row = example_row(0)
        row["case"]["body"] = "private body"
        row["prediction"]["analysis"] = {"targets": "source-derived targets"}
        raw = json.dumps(self.project([row]))
        self.assertNotIn("private body", raw)
        self.assertNotIn("source-derived targets", raw)


class MonitoringAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProspectiveTargetReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.register()
        self.fixture.poll.tick("2026-09-07T04:00:00Z")
        self.fixture.record()

    def audit(self):
        return status.audit(self.fixture.registration_path, self.fixture.root)

    def test_actual_fixture_replays_without_persistent_writes(self):
        before = {str(path): path.read_bytes() for path in self.fixture.root.rglob("*") if path.is_file()}
        calls = len(self.fixture.poll.capture.calls)
        result = self.audit()
        after = {str(path): path.read_bytes() for path in self.fixture.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(calls, len(self.fixture.poll.capture.calls))
        self.assertEqual({"accepted": 1, "unaccepted": 0}, result["batches"])
        self.assertEqual(1, result["counts"]["interim_document_opportunities"])
        self.assertTrue(all(value is False for value in result["boundaries"].values()))
        self.assertTrue(all(value is None for value in result["unscored"].values()))

    def test_missing_receipt_is_unaccepted_not_a_recorded_decision(self):
        for path in self.fixture.prediction_root.glob("*.receipt.json"):
            path.unlink()
        result = self.audit()
        self.assertEqual({"accepted": 0, "unaccepted": 1}, result["batches"])
        self.assertEqual({"missing": 1}, result["in_protocol"]["recording_statuses"])

    def test_future_relative_to_start_receipt_is_rejected(self):
        original = status.shadow._journal
        def changed(*args, **kwargs):
            records, retained = original(*args, **kwargs)
            records[0]["receipt"]["accepted_at"] = "2026-09-07T04:10:00.000001Z"
            return records, retained
        with patch.object(status.shadow, "_journal", side_effect=changed), self.assertRaisesRegex(ValueError, "crossed the audit start"):
            self.audit()

    def test_receipt_after_end_within_grace_is_not_future_to_audit(self):
        original = status.shadow._journal
        self.fixture.clock = "2026-09-07T08:30:00Z"
        def changed(*args, **kwargs):
            records, retained = original(*args, **kwargs)
            records[0]["receipt"]["accepted_at"] = "2026-09-07T08:15:00Z"
            return records, retained
        with patch.object(status.shadow, "_journal", side_effect=changed):
            result = self.audit()
        self.assertEqual("closed_window_unsealed", result["status"])
        self.assertEqual("2026-09-07T08:00:00Z", result["observation_cutoff"])
        self.assertEqual({"late": 1}, result["in_protocol"]["recording_statuses"])

    def test_exact_study_end_is_closed_not_open(self):
        self.fixture.clock = self.fixture.study["end"]
        result = self.audit()
        self.assertEqual("closed_window_unsealed", result["status"])
        self.assertEqual(self.fixture.study["end"], result["observation_cutoff"])

    def test_journal_append_during_audit_fails(self):
        original = status.shadow._verify_current_population
        def changed(*args):
            original(*args)
            (self.fixture.prediction_root / "seal.json").write_text("{}")
        with patch.object(status.shadow, "_verify_current_population", side_effect=changed), self.assertRaisesRegex(ValueError, "inputs changed"):
            self.audit()

    def test_journal_receipt_mutation_during_audit_fails(self):
        original = status.shadow._verify_current_population
        def changed(*args):
            original(*args)
            path = next(self.fixture.prediction_root.glob("*.receipt.json"))
            path.write_bytes(path.read_bytes() + b" ")
        with patch.object(status.shadow, "_verify_current_population", side_effect=changed), self.assertRaisesRegex(ValueError, "inputs changed"):
            self.audit()

    def test_valid_registration_receipt_replacement_still_fails(self):
        original = status.shadow._verify_current_population
        def changed(*args):
            original(*args)
            path = status.shadow._registration_receipt_path(self.fixture.registration_path)
            receipt = json.loads(path.read_bytes())
            receipt["accepted_at"] = "2026-09-07T03:50:01Z"
            path.write_bytes(status.shadow._pretty_bytes(receipt))
        with patch.object(status.shadow, "_verify_current_population", side_effect=changed), self.assertRaisesRegex(ValueError, "inputs changed"):
            self.audit()

    def test_source_population_race_is_not_swallowed(self):
        with patch.object(status.shadow, "_verify_current_population", side_effect=ValueError("source retention changed")):
            with self.assertRaisesRegex(ValueError, "source retention changed"):
                self.audit()

    def test_clock_regression_fails(self):
        original = status.shadow._verify_current_population
        def changed(*args):
            original(*args)
            self.fixture.clock = "2026-09-07T04:09:59Z"
        with patch.object(status.shadow, "_verify_current_population", side_effect=changed), self.assertRaises(ValueError):
            self.audit()

    def test_sealed_journal_refuses_an_interim_report(self):
        (self.fixture.prediction_root / "seal.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "already sealed"):
            self.audit()

    def test_exact_recording_deadline_and_first_acceptance_are_preserved(self):
        shadow = status.shadow
        registration, raw = shadow.validate_registration(self.fixture.registration_path, reference_root=self.fixture.root)
        records, _ = shadow._journal(self.fixture.prediction_root, registration, raw, self.fixture.root)
        population = records[0]["batch"]["population"]
        case = records[0]["batch"]["predictions"][0]["case"]
        deadline = shadow._instant(case["assessment_at"]) + timedelta(seconds=3600)
        records[0]["receipt"]["accepted_at"] = deadline.isoformat().replace("+00:00", "Z")
        records[0]["batch"]["predictions"][0]["result"] = "no_candidate"
        first = shadow._reconcile(registration, population, records)
        self.assertEqual("on_time", first["cases"][0]["recording_status"])
        later = copy.deepcopy(records[0])
        later["receipt"]["accepted_at"] = (deadline + timedelta(microseconds=1)).isoformat().replace("+00:00", "Z")
        later["batch"]["predictions"][0]["result"] = "revision_candidate"
        alone = shadow._reconcile(registration, population, [later])
        self.assertEqual("late", alone["cases"][0]["recording_status"])
        combined = status.project(registration, shadow._reconcile(registration, population, [later, *records]),
                                  study_start=self.fixture.study["start"], observation_cutoff=self.fixture.study["end"])
        self.assertEqual(1, combined["counts"]["interim_document_opportunities"])
        self.assertEqual("no_candidate", combined["cases"][0]["recorded_result"])


class MonitoringOutputTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="atlas-monitoring-output-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.result = {"protected_files": [str(self.root / "config.json")],
            "protected_directories": [str(self.root / "predictions")],
            "journal_and_registration_files": {}, "source_snapshot_files": {}}

    def test_new_only_report_output_and_exact_bytes(self):
        path = self.root / "report.json"
        saved = controller.save_report(path, self.result, reference_root=self.root)
        self.assertEqual(saved["sha256"], controller.runtime.digest(path.read_bytes()))
        with self.assertRaises(FileExistsError):
            controller.save_report(path, self.result, reference_root=self.root)

    def test_new_file_inside_prediction_root_is_not_allowed(self):
        with self.assertRaisesRegex(ValueError, "protected study inputs"):
            controller.save_report(self.root / "predictions/new.json", self.result, reference_root=self.root)

    def test_protected_input_and_parent_traversal_rejected(self):
        for path in (self.root / "config.json", self.root / "x/../report.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                controller.save_report(path, self.result, reference_root=self.root)

    def test_symlink_output_directory_rejected(self):
        (self.root / "predictions").mkdir()
        (self.root / "alias").symlink_to(self.root / "predictions", target_is_directory=True)
        with self.assertRaises(OSError):
            controller.save_report(self.root / "alias/report.json", self.result, reference_root=self.root)


class MonitoringControllerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_fixtures.FrozenRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.registration = self.fixture.root / "registration.json"
        self.registration.write_text("{}")
        self.options = {"expected_sha256": self.fixture.receipt["sha256"], "expected_commit": self.fixture.commit,
                        "registration": self.registration, "reference_root": self.fixture.root}

    def child(self, command, **kwargs):
        self.staged_root = Path(command[6])
        self.assertEqual(["-I", "-S", "-B"], command[1:4])
        self.assertEqual(controller.WORKER, command[7])
        self.assertEqual(self.staged_root, kwargs["cwd"])
        imports = {"modules": {"semiconductor_atlas.prospective_target_review": "semiconductor_atlas/models.py"},
                   "violations": []}
        (self.staged_root / ".runtime-imports.json").write_text(json.dumps(imports))
        return 0, json.dumps({"format": status.FORMAT}).encode(), b""

    def run_audit(self, **changes):
        return controller.run_audit(self.fixture.bundle, **{**self.options, **changes})

    def test_fixed_worker_is_staged_separately_and_receipt_pins_its_code(self):
        with patch.object(controller.replay, "_capture", side_effect=self.child):
            result = self.run_audit()
        self.assertIn(controller.WORKER, result["audit_code_sha256"])
        self.assertEqual(self.fixture.commit, result["runtime_commit"])
        self.assertFalse(self.staged_root.exists())

    def test_wrong_bundle_and_relative_inputs_fail_before_execution(self):
        for changes in ({"expected_sha256": "0" * 64}, {"registration": Path("relative.json")},
                        {"reference_root": Path("relative")}, {"expected_commit": "0" * 40}):
            with self.subTest(changes=changes), patch.object(controller.replay, "_capture") as child:
                with self.assertRaises(ValueError):
                    self.run_audit(**changes)
                child.assert_not_called()

    def test_import_origin_outside_inventory_fails(self):
        def child(*args, **kwargs):
            result = self.child(*args, **kwargs)
            path = self.staged_root / ".runtime-imports.json"
            imports = json.loads(path.read_bytes())
            imports["modules"]["semiconductor_atlas.prospective_target_review"] = "outside.py"
            path.write_text(json.dumps(imports))
            return result
        with patch.object(controller.replay, "_capture", side_effect=child), self.assertRaisesRegex(ValueError, "imports escaped"):
            self.run_audit()

    def test_staged_code_mutation_fails(self):
        def child(*args, **kwargs):
            result = self.child(*args, **kwargs)
            path = self.staged_root / controller.WORKER
            path.chmod(0o600)
            path.write_text("changed")
            return result
        with patch.object(controller.replay, "_capture", side_effect=child), self.assertRaisesRegex(ValueError, "source bytes"):
            self.run_audit()

    def test_registration_mutation_after_worker_fails(self):
        def child(*args, **kwargs):
            result = self.child(*args, **kwargs)
            self.registration.write_text("changed")
            return result
        with patch.object(controller.replay, "_capture", side_effect=child), self.assertRaisesRegex(ValueError, "registration changed"):
            self.run_audit()

    def test_failed_or_invalid_worker_output_fails(self):
        with patch.object(controller.replay, "_capture", return_value=(1, b"", b"worker failed")):
            with self.assertRaisesRegex(ValueError, "worker failed"):
                self.run_audit()
        def child(*args, **kwargs):
            self.child(*args, **kwargs)
            return 0, b"{}", b""
        with patch.object(controller.replay, "_capture", side_effect=child), self.assertRaisesRegex(ValueError, "operational report"):
            self.run_audit()


if __name__ == "__main__":
    unittest.main()
