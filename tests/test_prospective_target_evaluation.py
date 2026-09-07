from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from semiconductor_atlas import curated_poll as poll
from semiconductor_atlas import prospective_target_evaluation as evaluation
from semiconductor_atlas import prospective_target_review as shadow
from semiconductor_atlas import source_statement_review as statements
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from scripts import evaluate_prospective_source_targets as cli
from tests import test_curated_capture as capture_fixtures
from tests import test_prospective_target_review as fixtures


BODY_2028 = b"<h1>Project Update</h1><p>Project Alpha production is planned for 2028.</p>"
BODY_2027 = b"<h1>Project Update</h1><p>Project Alpha production is planned for 2027.</p>"


class ProspectiveTargetEvaluationTests(unittest.TestCase):
    """Synthetic retained sources exercise real registration, journal, seal and span replay."""

    def setUp(self):
        original = capture_fixtures.CuratedCaptureTests.setUp

        def source_setup(fixture):
            original(fixture)
            fixture.bodies["document"] = BODY_2028

        with patch.object(capture_fixtures.CuratedCaptureTests, "setUp", source_setup):
            self.fixture = fixtures.ProspectiveTargetReviewTests()
            self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.poll = self.fixture.root, self.fixture.poll
        self.capture = self.poll.capture
        self.registration = self.fixture.registration_path
        self.policy = self.root / "evaluation-policy.json"
        self.packet_path = self.root / "source-only-packet.json"
        self.labels_path = self.root / "outcome-labels.json"
        self.support_path = self.root / "outcome-review.json"
        self.support_path.write_bytes(_pretty_bytes({
            "synthetic_only": True,
            "prior_exposure": "The test author knows these artificial examples and scripted predictions.",
            "scope": "Source-stated targets, not actual production or independently adjudicated truth.",
        }))
        for module in (evaluation, statements):
            clock = patch.object(module, "_now", side_effect=lambda: self.fixture.clock)
            clock.start()
            self.addCleanup(clock.stop)

    def register(self):
        self.fixture.register()
        return evaluation.register_policy(self.registration, self.policy, reference_root=self.root)

    def scripted_detector(self, function):
        def analyze(url, before, after):
            result = function(before, after)
            if result == "error":
                raise RuntimeError("synthetic detector execution failure")
            return {"result": result, "reason": "synthetic evaluator accounting fixture",
                    "private_prediction_marker": "never in source-only packet"}

        detector = patch.object(shadow.detector, "analyze", side_effect=analyze)
        detector.start()
        self.addCleanup(detector.stop)

    def seal(self):
        sealed = self.fixture.seal()
        self.seal_path = self.fixture.prediction_root / "seal.json"
        self.seal_path.write_bytes(_pretty_bytes(sealed))
        self.sealed = sealed
        return sealed

    def packet(self):
        self.fixture.clock = "2026-09-07T09:10:00Z"
        evaluation.accept_packet(self.registration, self.policy, self.packet_path, reference_root=self.root)
        self.packet_data = json.loads(self.packet_path.read_bytes())
        self.cases = list(statements._cases(self.sealed["final_population"]).values())
        self.labels = {
            "format": evaluation.LABEL_FORMAT,
            "packet_sha256": evaluation._hash(self.packet_path.read_bytes()),
            "seal_sha256": evaluation._hash(self.seal_path.read_bytes()),
            "policy_sha256": evaluation._hash(self.policy.read_bytes()),
            "reviewer": "Synthetic evidence reviewer",
            "reviewed_at": "2026-09-07T09:20:00Z",
            "prior_exposure": "Artificial fixture targets and predictions were known to this test author.",
            "coverage_statement": "All retained fixture opportunities, not all publisher changes.",
            "target_scope": shadow.TARGET_SCOPE,
            "supporting_reviews": [{"path": self.support_path.name,
                                    "sha256": evaluation._hash(self.support_path.read_bytes())}],
            "adjudications": [],
        }
        self.fixture.clock = "2026-09-07T09:30:00Z"
        return self.packet_data

    def paired(self, *, after=BODY_2028, outcome=None, recorded="2026-09-07T04:10:00Z"):
        if outcome is not None:
            self.scripted_detector(lambda before, body: outcome)
        self.register()
        self.capture.bodies["document"] = after
        self.poll.tick("2026-09-07T04:00:00Z")
        if recorded is not None:
            self.fixture.record(recorded)
        self.seal()
        self.packet()
        return self.cases[0]

    def body(self, case, side):
        source = case["predecessor"] if side == "before" else case
        return (self.root / source["body_path"]).read_bytes()

    @staticmethod
    def span(body, needle=None):
        fragment = body if needle is None else needle.encode("utf-8")
        start = body.index(fragment)
        return {"start": start, "end": start + len(fragment),
                "sha256": evaluation._hash(fragment), "locator": "original synthetic UTF-8 bytes"}

    def target(self, case, *, before="2028", after="2028", verdict="no_revision"):
        sides = {}
        for side, literal in (("before", before), ("after", after)):
            body = self.body(case, side)
            sides[side] = {"literal": literal, "fragment": self.span(body, literal),
                           "context": [self.span(body)]}
        return {"source_native_subject": "Project Alpha", "milestone": "production",
                "formulation": "source-stated planning target", "scope": "Synthetic project only",
                "verdict": verdict, "reason": "Explicit artificial source interpretation, not attainment.", **sides}

    def annotate(self, case, *, disposition="reviewed_targets", targets=None, complete=False):
        regions = []
        if disposition in {"reviewed_targets", "no_relevant_scoped_target"}:
            regions = [{"side": side, "span": self.span(self.body(case, side))}
                       for side in ("before", "after")]
        row = {"case_id": case["case_id"], "disposition": disposition,
               "reason": "Synthetic source evidence only; no independence assertion.",
               "review_sha256": evaluation._hash(self.support_path.read_bytes()),
               "locator": "scope", "reviewed_regions": regions,
               "targets": targets or [], "complete_scope_review": complete}
        self.labels["adjudications"].append(row)
        return row

    def score(self):
        self.labels_path.write_bytes(_pretty_bytes(self.labels))
        return evaluation.evaluate(self.registration, self.policy, self.packet_path,
                                   self.labels_path, reference_root=self.root)

    def validate_packet(self):
        return evaluation.validate_packet(self.registration, self.policy, self.packet_path,
                                          reference_root=self.root)

    def invoke(self, command, *arguments, success=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["evaluate_prospective_source_targets.py", command,
                "--registration", str(self.registration), "--reference-root", str(self.root), *map(str, arguments)]
        with patch("sys.argv", argv), redirect_stdout(stdout), redirect_stderr(stderr):
            if success:
                cli.main()
            else:
                with self.assertRaises(SystemExit) as error:
                    cli.main()
                self.assertEqual(1, error.exception.code)
        return json.loads(stdout.getvalue()) if success else stderr.getvalue()

    def test_source_only_schema_and_exact_body_inventory_exclude_all_predictions(self):
        self.paired(after=BODY_2027, outcome="revision_candidate")
        packet = self.packet_data
        self.assertEqual({"format", "rule_version", "registration_sha256", "policy_sha256",
            "seal_sha256", "started_at", "packaged_at", "target_scope", "study", "selection_rule",
            "registered_documents", "cases", "bodies", "boundaries"}, set(packet))
        self.assertEqual("all_final_document_opportunities_no_prediction_filter", packet["selection_rule"])
        self.assertEqual(1, len(packet["registered_documents"]))
        self.assertEqual({"case_id", "kind", "url", "scope", "document_id", "assessment_at",
            "intent_started_at", "comparison_available", "unavailable_reason", "before", "after"}, set(packet["cases"][0]))
        raw = self.packet_path.read_bytes()
        for secret in (b"private_prediction_marker", b"revision_candidate", b"recording_status",
                       b"parser_route", b"analysis", b"first_recorded_at", b"assessment_to_prediction_seconds"):
            self.assertNotIn(secret, raw)
        self.assertEqual({BODY_2028, BODY_2027},
                         {shadow.population.blobs._unblob(blob) for blob in packet["bodies"].values()})
        self.assertEqual(packet, self.validate_packet()["packet"])

    def test_complete_retained_denominator_missing_labels_repeat_and_no_writes(self):
        self.register()
        for hour in (4, 5, 6):
            self.poll.tick(f"2026-09-07T0{hour}:00:00Z")
        self.seal()
        self.packet()
        self.annotate(self.cases[0], targets=[self.target(self.cases[0])], complete=True)
        self.labels_path.write_bytes(_pretty_bytes(self.labels))
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        calls = list(self.capture.calls)
        report = self.score()
        self.assertEqual((3, 3, 0), tuple(report["metrics"][key] for key in (
            "total_retained_cases", "eligible_document_opportunities", "excluded_cases")))
        self.assertEqual({"negative": 1, "unlabelled": 2}, report["metrics"]["truth_counts"])
        self.assertEqual({"missing": 3}, report["metrics"]["decision_counts"])
        self.assertEqual((3, 1, 2), tuple(report["repetition"][key] for key in (
            "comparable_checks", "unique_exact_source_pairs", "repeated_exact_source_pairs")))
        self.assertIsNone(report["repetition"]["event_count"])
        self.assertTrue(all(value is None for value in report["unscored_metrics"].values()))
        self.assertTrue(all(value is False for value in report["boundaries"].values()))
        self.assertEqual(report, self.score())
        self.assertEqual(calls, self.capture.calls)
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_empty_population_keeps_registered_url_and_null_denominators(self):
        self.register()
        self.seal()
        self.packet()
        report = self.score()
        self.assertEqual([], report["cases"])
        self.assertEqual(1, len(report["by_exact_url"]))
        self.assertEqual(0, report["metrics"]["eligible_document_opportunities"])
        self.assertIsNone(report["metrics"]["timely_document_sensitivity_among_resolved_positives"]["value"])
        self.assertIsNone(report["metrics"]["candidate_document_support_bounds"]["upper"]["value"])

    def test_first_observation_retains_after_body_without_fabricated_before(self):
        added = {**self.capture.plan["documents"][0], "id": "first", "url": "https://example.org/new-document"}
        self.capture.plan["documents"].append(added)
        self.capture.bodies["first"] = BODY_2028
        self.poll.repin_plan()
        self.fixture.study["config"]["sha256"] = evaluation._hash(self.poll.config_path.read_bytes())
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.seal()
        self.packet()
        first = next(row for row in self.cases if row["document_id"] == "first")
        source = next(row for row in self.packet_data["cases"] if row["case_id"] == first["case_id"])
        self.assertFalse(source["comparison_available"])
        self.assertIsNone(source["before"])
        self.assertEqual(evaluation._hash(BODY_2028), source["after"]["body_sha256"])
        self.annotate(first, disposition="uncomparable")
        metrics = self.score()["metrics"]
        # The new plan hash also prevents reuse of the old document's plan-bound predecessor.
        self.assertEqual((2, 0, 2), tuple(metrics[key] for key in (
            "total_retained_cases", "eligible_document_opportunities", "excluded_cases")))

    def test_failed_blocked_and_incomplete_cases_are_retained_and_excluded(self):
        self.register()
        self.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        self.poll.tick("2026-09-07T04:00:00Z")
        self.capture.bodies["rights"] += b"<p>Changed terms</p>"
        self.poll.tick("2026-09-07T05:00:00Z")
        with patch.object(poll, "capture_sources", side_effect=ValueError("synthetic interrupted acquisition")):
            self.poll.tick("2026-09-07T06:00:00Z")
        self.seal()
        self.packet()
        self.assertEqual(3, len(self.cases))
        for case in self.cases:
            self.annotate(case, disposition="uncomparable")
        metrics = self.score()["metrics"]
        self.assertEqual((3, 0, 3), tuple(metrics[key] for key in (
            "total_retained_cases", "eligible_document_opportunities", "excluded_cases")))
        self.labels["adjudications"][0].update(disposition="no_relevant_scoped_target", complete_scope_review=True)
        with self.assertRaises(ValueError):
            self.score()

    def test_forced_pair_retained_but_outside_accuracy_denominator(self):
        self.register()
        self.poll.tick("2026-09-07T04:00:00Z", force=True)
        self.fixture.record()
        self.seal()
        self.packet()
        self.annotate(self.cases[0], targets=[self.target(self.cases[0])], complete=True)
        row = self.score()["cases"][0]
        self.assertFalse(row["evaluation_eligible"])
        self.assertEqual("forced_poll_capture", row["exclusion_reason"])
        self.assertEqual("negative", row["truth"])

    def test_genuine_bound_revision_is_document_support_not_physical_truth(self):
        case = self.paired(after=BODY_2027, outcome="revision_candidate")
        self.annotate(case, targets=[self.target(case, after="2027", verdict="revision")])
        report = self.score()
        self.assertEqual({"positive": 1}, report["metrics"]["truth_counts"])
        self.assertEqual(1, report["metrics"]["confusion_on_resolved_timely_decisions"]["true_positive"])
        self.assertEqual(1.0, report["metrics"]["candidate_document_support_among_resolved"]["value"])
        self.assertIsNone(report["unscored_metrics"]["physical_outcome_accuracy"])
        self.assertEqual("Project Alpha", report["targets"][0]["source_native_subject"])

    def test_quiet_prediction_is_a_miss_when_source_revision_is_labelled(self):
        case = self.paired(after=BODY_2027, outcome="no_candidate")
        self.annotate(case, targets=[self.target(case, after="2027", verdict="revision")])
        metrics = self.score()["metrics"]
        self.assertEqual(1, metrics["confusion_on_resolved_timely_decisions"]["false_negative"])
        self.assertEqual({"quiet": 1}, metrics["positive_nondetection_by_decision"])
        self.assertEqual(0.0, metrics["timely_document_sensitivity_among_resolved_positives"]["value"])

    def test_real_unsupported_parser_abstention_does_not_erase_labelled_positive(self):
        case = self.paired(after=BODY_2027)
        self.annotate(case, targets=[self.target(case, after="2027", verdict="revision")])
        metrics = self.score()["metrics"]
        self.assertEqual({"abstain": 1}, metrics["positive_nondetection_by_decision"])
        self.assertEqual(1, metrics["timely_document_sensitivity_among_resolved_positives"]["denominator"])

    def test_execution_error_and_missing_output_remain_positive_nondetections(self):
        self.scripted_detector(lambda before, after: "error")
        self.register()
        self.capture.bodies["document"] = BODY_2027
        self.poll.tick("2026-09-07T04:00:00Z")
        self.fixture.record()
        self.capture.bodies["document"] = BODY_2028
        self.poll.tick("2026-09-07T05:00:00Z")
        self.seal()
        self.packet()
        for case in self.cases:
            before = "2028" if b"2028" in self.body(case, "before") else "2027"
            after = "2028" if b"2028" in self.body(case, "after") else "2027"
            self.annotate(case, targets=[self.target(case, before=before, after=after, verdict="revision")])
        metrics = self.score()["metrics"]
        self.assertEqual({"error": 1, "missing": 1}, metrics["positive_nondetection_by_decision"])
        self.assertEqual(2, metrics["positive_without_timely_candidate"])

    def test_late_false_positive_counts_as_workload_not_timely_candidate(self):
        case = self.paired(outcome="revision_candidate", recorded="2026-09-07T05:10:00Z")
        self.annotate(case, targets=[self.target(case)], complete=True)
        metrics = self.score()["metrics"]
        self.assertEqual({"late": 1}, metrics["decision_counts"])
        self.assertEqual(0, metrics["confusion_on_resolved_timely_decisions"]["false_positive"])
        self.assertEqual(1, metrics["all_recorded_candidate_counts"]["false_positive"])
        self.assertEqual(1.0, metrics["confirmed_false_positive_document_burden"]["value"])
        self.assertEqual(0.0, metrics["confirmed_timely_false_positive_document_burden"]["value"])

    def test_partial_no_revision_is_unknown_and_complete_assertion_changes_denominator(self):
        case = self.paired(outcome="revision_candidate")
        label = self.annotate(case, targets=[self.target(case)])
        metrics = self.score()["metrics"]
        self.assertEqual({"unresolved": 1}, metrics["truth_counts"])
        self.assertEqual(1, metrics["unknown_candidate_labels"])
        self.assertEqual((0.0, 1.0), tuple(metrics["candidate_document_support_bounds"][side]["value"]
                                              for side in ("lower", "upper")))
        label["complete_scope_review"] = True
        metrics = self.score()["metrics"]
        self.assertEqual({"negative": 1}, metrics["truth_counts"])
        self.assertEqual(1, metrics["confusion_on_resolved_timely_decisions"]["false_positive"])

    def test_no_target_negative_requires_explicit_complete_scope_boolean(self):
        case = self.paired(outcome="no_candidate")
        label = self.annotate(case, disposition="no_relevant_scoped_target")
        self.assertEqual("unresolved", self.score()["cases"][0]["truth"])
        label["complete_scope_review"] = True
        self.assertEqual("negative", self.score()["cases"][0]["truth"])
        label["complete_scope_review"] = 1
        with self.assertRaises(ValueError):
            self.score()

    def test_unresolved_targets_cannot_become_complete_negative(self):
        case = self.paired()
        target = self.target(case, verdict="unresolved")
        self.annotate(case, targets=[target], complete=True)
        self.assertEqual("unresolved", self.score()["cases"][0]["truth"])

    def test_duplicate_unknown_and_out_of_window_labels_are_rejected(self):
        case = self.paired()
        row = self.annotate(case, targets=[self.target(case)])
        self.labels["adjudications"].append(copy.deepcopy(row))
        with self.assertRaises(ValueError):
            self.score()
        self.labels["adjudications"].pop()
        for identifier in ("unknown-case", next(iter(statements._cases(json.loads(self.fixture.baseline.read_bytes()))))):
            row["case_id"] = identifier
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.score()

    def test_target_evidence_hash_literal_visibility_and_containment_are_checked(self):
        case = self.paired(after=BODY_2027)
        label = self.annotate(case, targets=[self.target(case, after="2027", verdict="revision")])
        original = copy.deepcopy(label)
        mutations = (
            lambda row: row["targets"][0]["after"]["fragment"].update(sha256="0" * 64),
            lambda row: row["targets"][0]["after"].update(literal="2035"),
            lambda row: row.update(reviewed_regions=[region for region in row["reviewed_regions"] if region["side"] == "before"]),
            lambda row: row["targets"][0]["before"]["fragment"].update(start=0, end=4, sha256=evaluation._hash(b"<h1>")),
        )
        for mutate in mutations:
            self.labels["adjudications"] = [copy.deepcopy(original)]
            mutate(self.labels["adjudications"][0])
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                self.score()

    def test_same_source_evidence_cannot_be_labelled_revision(self):
        case = self.paired()
        self.annotate(case, targets=[self.target(case, verdict="revision")])
        with self.assertRaises(ValueError):
            self.score()

    def test_hidden_calendar_literal_is_not_valid_visible_evidence(self):
        body = BODY_2027 + b"<script>window.withdrawnTarget = 2035;</script>"
        case = self.paired(after=body)
        self.annotate(case, targets=[self.target(case, after="2035", verdict="revision")])
        with self.assertRaises(ValueError):
            self.score()

    def test_utf8_spans_bind_byte_offsets_not_character_offsets(self):
        body = "<h1>Project Update</h1><p>Project Alpha’s production is planned for 2027.</p>".encode("utf-8")
        case = self.paired(after=body)
        label = self.annotate(case, targets=[self.target(case, after="2027", verdict="revision")])
        self.assertEqual("positive", self.score()["cases"][0]["truth"])
        fragment = label["targets"][0]["after"]["fragment"]
        self.assertEqual(body.index(b"2027"), fragment["start"])
        fragment["start"] = body.decode("utf-8").index("2027")
        fragment["end"] = fragment["start"] + 4
        fragment["sha256"] = evaluation._hash(body[fragment["start"]:fragment["end"]])
        with self.assertRaises(ValueError):
            self.score()

    def test_labels_bind_exact_policy_packet_seal_and_support_bytes(self):
        case = self.paired()
        self.annotate(case, targets=[self.target(case)])
        for field in ("policy_sha256", "packet_sha256", "seal_sha256"):
            original = self.labels[field]
            self.labels[field] = "0" * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.score()
            self.labels[field] = original
        self.support_path.write_bytes(self.support_path.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            self.score()

    def test_labels_must_follow_packet_acceptance_not_just_generation(self):
        self.paired()
        receipt_path = evaluation._receipt_path(self.packet_path)
        receipt = json.loads(receipt_path.read_bytes())
        receipt["accepted_at"] = "2026-09-07T09:20:00Z"
        receipt_path.write_bytes(_pretty_bytes(receipt))
        for timestamp in ("2026-09-07T09:15:00Z", "2026-09-07T09:20:00Z", "2026-09-07T09:31:00Z"):
            self.labels["reviewed_at"] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                self.score()

    def test_policy_registration_requires_prestart_durable_acceptance(self):
        self.fixture.register()
        self.fixture.clock = self.fixture.study["start"]
        with self.assertRaises(ValueError):
            evaluation.register_policy(self.registration, self.policy, reference_root=self.root)
        self.assertFalse(self.policy.exists())
        self.fixture.clock = "2026-09-07T03:50:00Z"
        original = evaluation.vintage.write_new

        def delayed(path, data, **kwargs):
            result = original(path, data, **kwargs)
            if data.get("format") == evaluation.POLICY_FORMAT:
                self.fixture.clock = self.fixture.study["start"]
            return result

        with patch.object(evaluation.vintage, "write_new", side_effect=delayed), self.assertRaises(ValueError):
            evaluation.register_policy(self.registration, self.policy, reference_root=self.root)
        self.assertTrue(self.policy.exists())
        self.assertFalse(evaluation._receipt_path(self.policy).exists())

    def test_policy_code_receipt_and_canonical_byte_tampering_fail(self):
        self.register()
        with patch.object(evaluation, "_code_hashes", return_value={}):
            with self.assertRaises(ValueError):
                evaluation.validate_policy(self.registration, self.policy, reference_root=self.root)
        original = self.policy.read_bytes()
        self.policy.write_bytes(original + b" ")
        with self.assertRaises(ValueError):
            evaluation.validate_policy(self.registration, self.policy, reference_root=self.root)
        self.policy.write_bytes(original)
        receipt_path = evaluation._receipt_path(self.policy)
        receipt = json.loads(receipt_path.read_bytes())
        receipt["accepted_at"] = self.fixture.study["start"]
        receipt_path.write_bytes(_pretty_bytes(receipt))
        with self.assertRaises(ValueError):
            evaluation.validate_policy(self.registration, self.policy, reference_root=self.root)

    def test_packet_requires_seal_and_durable_receipt(self):
        self.register()
        with self.assertRaises((ValueError, OSError)):
            evaluation.make_packet(self.registration, self.policy, reference_root=self.root)
        self.seal()
        self.fixture.clock = "2026-09-07T09:10:00Z"
        draft = evaluation.make_packet(self.registration, self.policy, reference_root=self.root)
        self.packet_path.write_bytes(_pretty_bytes(draft))
        with self.assertRaises((ValueError, OSError)):
            self.validate_packet()

    def test_packet_acceptance_clock_is_sampled_after_durable_write(self):
        self.register()
        self.seal()
        self.fixture.clock = "2026-09-07T09:10:00Z"
        original = evaluation.vintage.write_new

        def delayed(path, data, **kwargs):
            result = original(path, data, **kwargs)
            if data.get("format") == evaluation.PACKET_FORMAT:
                self.fixture.clock = "2026-09-07T09:15:00Z"
            return result

        with patch.object(evaluation.vintage, "write_new", side_effect=delayed):
            result = evaluation.accept_packet(self.registration, self.policy, self.packet_path, reference_root=self.root)
        self.assertEqual("2026-09-07T09:15:00Z", result["accepted_at"])
        context = self.validate_packet()
        self.assertEqual("2026-09-07T09:10:00Z", context["packet"]["packaged_at"])
        self.assertEqual(result["accepted_at"], context["packet_accepted_at"])

    def test_packet_case_deletion_or_prediction_field_injection_rejected_even_when_rehashed(self):
        self.paired()
        original = copy.deepcopy(self.packet_data)
        for mutation in (lambda packet: packet["cases"].clear(),
                         lambda packet: packet["cases"][0].update(prediction="no_candidate"),
                         lambda packet: packet["registered_documents"].clear()):
            packet = copy.deepcopy(original)
            mutation(packet)
            self.packet_path.write_bytes(_pretty_bytes(packet))
            receipt_path = evaluation._receipt_path(self.packet_path)
            receipt = json.loads(receipt_path.read_bytes())
            receipt["packet_sha256"] = evaluation._hash(self.packet_path.read_bytes())
            receipt_path.write_bytes(_pretty_bytes(receipt))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.validate_packet()

    def test_packet_preseal_or_future_acceptance_clocks_fail(self):
        self.paired()
        receipt_path = evaluation._receipt_path(self.packet_path)
        original_receipt = receipt_path.read_bytes()
        for timestamp in ("2026-09-07T09:09:59Z", "2026-09-07T09:31:00Z"):
            receipt = json.loads(original_receipt)
            receipt["accepted_at"] = timestamp
            receipt_path.write_bytes(_pretty_bytes(receipt))
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                self.validate_packet()

    def test_body_drift_rejected_at_source_replay(self):
        case = self.paired()
        path = self.root / case["body_path"]
        path.write_bytes(path.read_bytes().replace(b"2028", b"2035"))
        with self.assertRaises(ValueError):
            self.score()

    def test_labels_review_and_code_drift_during_scoring_fail_final_recheck(self):
        self.paired()
        original_labels = copy.deepcopy(self.labels)
        original_review = self.support_path.read_bytes()
        real_metrics = evaluation._metrics
        for target in ("labels", "support", "code"):
            changed = False
            real_codes = evaluation._code_hashes

            def mutate(rows):
                nonlocal changed
                if not changed:
                    changed = True
                    if target == "labels":
                        self.labels_path.write_bytes(self.labels_path.read_bytes() + b" ")
                    elif target == "support":
                        self.support_path.write_bytes(original_review + b" ")
                return real_metrics(rows)

            def code_hashes():
                return {} if target == "code" and changed else real_codes()

            with self.subTest(target=target), patch.object(evaluation, "_metrics", side_effect=mutate), \
                    patch.object(evaluation, "_code_hashes", side_effect=code_hashes), self.assertRaises(ValueError):
                self.score()
            self.labels = copy.deepcopy(original_labels)
            self.support_path.write_bytes(original_review)

    def test_later_out_of_window_capture_does_not_change_replay(self):
        self.paired()
        before = self.score()
        packet_raw = self.packet_path.read_bytes()
        self.capture.bodies["document"] = BODY_2027
        self.poll.tick("2026-09-07T10:00:00Z")
        self.fixture.clock = "2026-09-07T10:30:00Z"
        self.assertEqual(before, self.score())
        self.assertEqual(packet_raw, self.packet_path.read_bytes())

    def test_prepare_review_no_work_then_accept_then_verify_without_labels(self):
        self.register()
        calls = list(self.capture.calls)
        result = evaluation.prepare_review(self.registration, self.policy, self.packet_path, reference_root=self.root)
        self.assertEqual("awaiting_sealed_population", result["status"])
        self.assertFalse(self.packet_path.exists())
        self.seal()
        self.fixture.clock = "2026-09-07T09:10:00Z"
        result = evaluation.prepare_review(self.registration, self.policy, self.packet_path, reference_root=self.root)
        self.assertEqual("source_only_packet_created", result["status"])
        snapshot = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result = evaluation.prepare_review(self.registration, self.policy, self.packet_path, reference_root=self.root)
        self.assertEqual("source_only_packet_verified", result["status"])
        self.assertEqual(0, result["labels_created"])
        self.assertFalse(self.labels_path.exists())
        self.assertEqual(calls, self.capture.calls)
        self.assertEqual(snapshot, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_cli_policy_packet_verify_and_score_are_new_only(self):
        self.fixture.register()
        self.invoke("register-policy", "--output", self.policy)
        self.assertTrue(self.invoke("verify-policy", "--policy", self.policy)["registered_code_and_inputs_replayed"])
        self.invoke("register-policy", "--output", self.policy, success=False)
        self.seal()
        self.fixture.clock = "2026-09-07T09:10:00Z"
        self.invoke("packet", "--policy", self.policy, "--output", self.packet_path)
        self.assertEqual(0, self.invoke("verify-packet", "--policy", self.policy, "--packet", self.packet_path)["cases"])
        self.invoke("packet", "--policy", self.policy, "--output", self.packet_path, success=False)
        self.packet_data = json.loads(self.packet_path.read_bytes())
        self.labels = {"format": evaluation.LABEL_FORMAT, "packet_sha256": evaluation._hash(self.packet_path.read_bytes()),
            "seal_sha256": evaluation._hash(self.seal_path.read_bytes()), "policy_sha256": evaluation._hash(self.policy.read_bytes()),
            "reviewer": "Synthetic reviewer", "reviewed_at": "2026-09-07T09:20:00Z",
            "prior_exposure": "Fixture known to author.", "coverage_statement": "Empty synthetic retained cohort.",
            "target_scope": shadow.TARGET_SCOPE, "supporting_reviews": [], "adjudications": []}
        self.fixture.clock = "2026-09-07T09:30:00Z"
        self.labels_path.write_bytes(_pretty_bytes(self.labels))
        output = self.root / "evaluation-report.json"
        args = ("--policy", self.policy, "--packet", self.packet_path, "--labels", self.labels_path, "--output", output)
        self.invoke("score", *args)
        original = output.read_bytes()
        self.invoke("score", *args, success=False)
        self.assertEqual(original, output.read_bytes())

    def test_cli_protects_sources_journal_traversal_and_symlink_parents(self):
        self.paired()
        self.score()
        (self.root / "source-alias").symlink_to(self.root / "poll-captures", target_is_directory=True)
        for relative in ("poll-captures/injected.json", "predictions/injected.json",
                         "artifacts/../poll-captures/injected.json", "source-alias/injected.json"):
            output = self.root / relative
            with self.subTest(relative=relative):
                self.invoke("score", "--policy", self.policy, "--packet", self.packet_path,
                            "--labels", self.labels_path, "--output", output, success=False)
                self.assertFalse(output.exists())

    def test_cli_help_is_read_only(self):
        output = io.StringIO()
        with patch("sys.argv", ["evaluate_prospective_source_targets.py", "--help"]), \
                redirect_stdout(output), self.assertRaises(SystemExit) as exit_code:
            cli.main()
        self.assertEqual(0, exit_code.exception.code)
        for command in ("register-policy", "prepare-review", "verify-packet", "score"):
            self.assertIn(command, output.getvalue())


class ProspectiveTargetEvaluationMetricTests(unittest.TestCase):
    @staticmethod
    def row(truth, decision, *, recorded=None, eligible=True):
        result = recorded or {"candidate": "revision_candidate", "quiet": "no_candidate"}.get(decision, decision)
        return {"truth": truth, "decision": decision, "recorded_result": result,
                "evaluation_eligible": eligible}

    def test_complete_confusion_matrix_keeps_all_positive_nondecisions(self):
        rows = [self.row("positive", decision) for decision in
                ("candidate", "quiet", "abstain", "error", "late", "missing")]
        rows += [self.row("negative", "candidate"), self.row("negative", "quiet"),
                 self.row("unlabelled", "candidate"), self.row("unresolved", "candidate"),
                 self.row("negative", "late", recorded="revision_candidate"),
                 self.row("positive", "candidate", eligible=False)]
        metrics = evaluation._metrics(rows)
        self.assertEqual((12, 11, 1), tuple(metrics[key] for key in (
            "total_retained_cases", "eligible_document_opportunities", "excluded_cases")))
        self.assertEqual(dict.fromkeys(("true_positive", "false_positive", "true_negative", "false_negative"), 1),
                         metrics["confusion_on_resolved_timely_decisions"])
        self.assertEqual(5, metrics["positive_without_timely_candidate"])
        self.assertEqual(1 / 6, metrics["timely_document_sensitivity_among_resolved_positives"]["value"])
        self.assertEqual((0.25, 0.75), tuple(metrics["candidate_document_support_bounds"][side]["value"]
                                            for side in ("lower", "upper")))
        self.assertEqual(2 / 11, metrics["confirmed_false_positive_document_burden"]["value"])
        self.assertEqual(4 / 11, metrics["false_positive_document_burden_upper_with_unresolved_candidates"]["value"])
        self.assertEqual(1 / 11, metrics["confirmed_timely_false_positive_document_burden"]["value"])

    def test_unknowns_do_not_become_negative_and_any_supported_revision_is_positive(self):
        partial = {"disposition": "reviewed_targets", "complete_scope_review": False,
                   "targets": [{"verdict": "no_revision"}]}
        self.assertEqual("unlabelled", evaluation._truth(None))
        self.assertEqual("unresolved", evaluation._truth(partial))
        partial["targets"].append({"verdict": "revision"})
        self.assertEqual("positive", evaluation._truth(partial))
        partial["targets"] = [{"verdict": "no_revision"}, {"verdict": "unresolved"}]
        partial["complete_scope_review"] = True
        self.assertEqual("unresolved", evaluation._truth(partial))


if __name__ == "__main__":
    unittest.main()
