from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from semiconductor_atlas import curated_observation_population as population
from semiconductor_atlas import curated_poll as poll, curated_review as queue
from semiconductor_atlas import source_statement_review as review
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from scripts import review_source_statements as cli
from tests import test_curated_observation_population as fixtures


BODY_2028 = b"<h1>Project Update</h1><p>Project Alpha production is planned for 2028.</p>"
BODY_2027 = b"<h1>Project Update</h1><p>Project Alpha production is planned for 2027.</p>"


class SourceStatementReviewTests(unittest.TestCase):
    """Synthetic captures use the real census, source replay and review pipeline."""

    def setUp(self):
        self.fixture = fixtures.CuratedObservationPopulationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.poll = self.fixture.fixture
        self.capture = self.poll.capture
        self.capture.bodies["document"] = BODY_2028
        self.frozen_path = self.fixture.frozen_path
        self.labels_path = self.root / "statement-labels.json"
        self.support_path = self.root / "statement-support.json"
        self.support_path.write_bytes(_pretty_bytes({
            "fixture_only": True,
            "scope": "Source-stated target formulations only, not manufacturing attainment.",
            "prior_exposure": "Fixture author saw the artificial statements before freeze.",
        }))
        now = patch.object(review, "_now", return_value="2026-09-07T09:00:00Z")
        now.start()
        self.addCleanup(now.stop)

    def freeze(self):
        self.frozen = self.fixture.freeze()
        self.frozen_path.write_bytes(_pretty_bytes(self.frozen))
        self.cases = list(review._cases(self.frozen).values())
        self.labels = {
            "format": review.LABEL_FORMAT,
            "frozen_sha256": population._hash(self.frozen_path.read_bytes()),
            "reviewer": "Synthetic fixture reviewer",
            "reviewed_at": "2026-09-07T08:00:00Z",
            "prior_exposure": "The fixture author saw these artificial cases before freezing; not blinded.",
            "coverage_statement": "All retained in-window fixture cases, not publisher completeness.",
            "supporting_reviews": [{"path": self.support_path.name,
                                    "sha256": population._hash(self.support_path.read_bytes())}],
            "target_scope": "Source-native synthetic production and packaging planning statements only.",
            "adjudications": [],
        }
        return self.cases

    def paired(self, after=BODY_2028):
        self.poll.tick()
        self.capture.bodies["document"] = after
        self.poll.tick("2026-09-07T04:00:00Z")
        self.freeze()
        return next(row for row in self.cases if row.get("comparison_eligible"))

    def body(self, case, side):
        name = case["body_path"] if side == "after" else case["predecessor"]["body_path"]
        return (self.root / name).read_bytes()

    @staticmethod
    def span(body, needle=None):
        selected = body if needle is None else needle.encode("utf-8")
        start = body.index(selected)
        return {"start": start, "end": start + len(selected),
                "sha256": population._hash(selected), "locator": "synthetic original UTF-8 body"}

    def regions(self, case):
        return [{"side": side, "span": self.span(self.body(case, side))} for side in ("before", "after")]

    def statement(self, case, *, before="2028", after="2028", verdict="no_revision",
                  subject="Project Alpha", milestone="production"):
        sides = {}
        for side, literal in (("before", before), ("after", after)):
            body = self.body(case, side)
            sides[side] = {"literal": literal, "fragment": self.span(body, literal),
                           "context": [self.span(body)]}
        return {"source_native_subject": subject, "milestone": milestone,
                "formulation": "source-stated planning target", "scope": "Named synthetic project only",
                "verdict": verdict, "reason": "Manual fixture interpretation of the source-stated target.", **sides}

    def annotate(self, case, disposition="uncomparable", *, statements=None):
        regions = self.regions(case) if disposition in {"reviewed_targets", "no_relevant_scoped_target"} else []
        annotation = {"case_id": case["case_id"], "disposition": disposition,
            "reason": "Synthetic evidence review; no physical or external-completeness conclusion.",
            "review_sha256": population._hash(self.support_path.read_bytes()), "locator": "scope",
            "reviewed_regions": regions, "targets": statements or []}
        self.labels["adjudications"].append(annotation)
        return annotation

    def run_review(self):
        self.labels_path.write_bytes(_pretty_bytes(self.labels))
        return review.review(self.frozen_path, self.labels_path, reference_root=self.root)

    def test_full_denominator_missing_labels_no_writes_and_deterministic_repeat(self):
        paired = self.paired()
        self.annotate(paired, "reviewed_targets", statements=[self.statement(paired)])
        before = {path: path.read_bytes() for path in self.root.rglob("*")
                  if path.is_file() and path != self.labels_path}
        network_calls = list(self.capture.calls)
        report = self.run_review()
        self.assertEqual(review.REPORT_FORMAT, report["format"])
        self.assertEqual((2, 0, 2, 1, 1), tuple(report["counts"][key] for key in (
            "document_checks", "incomplete_document_cases", "total_cases", "labelled_cases", "unlabelled_cases")))
        self.assertEqual(2, len(report["cases"]))
        self.assertEqual(1, sum(row["adjudication"] is None for row in report["cases"]))
        self.assertEqual(1, report["counts"]["target_pairs"])
        self.assertEqual(1, report["counts"]["target_verdicts"]["no_revision"])
        self.assertTrue(all(value is False for value in report["boundaries"].values()))
        for metric in ("detection_recall", "alert_precision", "forecast_calibration"):
            self.assertIsNone(report["metrics"][metric])
        self.assertEqual(report, self.run_review())
        self.assertEqual(network_calls, self.capture.calls)
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual(set(before) | {self.labels_path}, {path for path in self.root.rglob("*") if path.is_file()})

    def test_first_observation_is_uncomparable_not_a_negative(self):
        self.poll.tick()
        case = self.freeze()[0]
        self.annotate(case)
        report = self.run_review()
        self.assertEqual(1, report["counts"]["dispositions"]["uncomparable"])
        self.assertEqual(0, report["counts"]["target_pairs"])
        self.assertIsNone(report["cases"][0]["predecessor"])
        for disposition in ("reviewed_targets", "no_relevant_scoped_target", "unresolved"):
            self.labels["adjudications"][0]["disposition"] = disposition
            with self.subTest(disposition=disposition), self.assertRaises(ValueError):
                self.run_review()

    def test_policy_blocked_check_remains_labelled_without_fabricated_body(self):
        self.capture.bodies["rights"] += b"<p>Different policy</p>"
        self.poll.tick()
        case = self.freeze()[0]
        self.assertEqual("not_attempted_policy_blocked", case["status"])
        self.annotate(case)
        report = self.run_review()
        self.assertEqual(1, report["counts"]["total_cases"])
        self.assertEqual(0, report["counts"]["target_pairs"])
        self.assertIsNone(report["cases"][0]["body_path"])

    def test_failed_transport_partial_body_does_not_enable_comparison(self):
        self.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        self.poll.tick()
        case = self.freeze()[0]
        self.assertEqual("failed_check", case["status"])
        annotation = self.annotate(case)
        annotation["reviewed_regions"].append({"side": "after", "span": self.span(self.body(case, "after"))})
        report = self.run_review()
        self.assertEqual(1, report["counts"]["dispositions"]["uncomparable"])
        annotation["disposition"] = "no_relevant_scoped_target"
        with self.assertRaises(ValueError):
            self.run_review()

    def test_incomplete_intent_is_a_case_without_invented_request_or_target(self):
        with patch.object(poll, "capture_sources", side_effect=ValueError("synthetic interruption")):
            self.poll.tick()
        case = self.freeze()[0]
        self.assertEqual("incomplete_document", case["kind"])
        self.annotate(case)
        report = self.run_review()
        self.assertEqual((0, 1, 1), tuple(report["counts"][key] for key in (
            "document_checks", "incomplete_document_cases", "total_cases")))
        self.assertEqual(0, report["counts"]["target_pairs"])

    def test_completed_unimported_capture_is_not_filtered_out(self):
        with patch.object(poll, "import_capture", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.poll.tick()
        case = self.freeze()[0]
        self.assertFalse(case["queue_admitted_before_end"])
        self.annotate(case)
        self.assertEqual(1, self.run_review()["counts"]["total_cases"])

    def test_no_relevant_target_and_unresolved_are_distinct_from_no_revision(self):
        case = self.paired()
        annotation = self.annotate(case, "no_relevant_scoped_target")
        report = self.run_review()
        self.assertEqual(1, report["counts"]["dispositions"]["no_relevant_scoped_target"])
        self.assertEqual(0, report["counts"]["target_pairs"])
        annotation["disposition"] = "unresolved"
        report = self.run_review()
        self.assertEqual(1, report["counts"]["dispositions"]["unresolved"])
        self.assertEqual(0, report["counts"]["target_pairs"])

    def test_missing_all_labels_keeps_every_case_unlabelled(self):
        self.paired()
        self.labels["supporting_reviews"] = []
        report = self.run_review()
        self.assertEqual((2, 0, 2), tuple(report["counts"][key] for key in ("total_cases", "labelled_cases", "unlabelled_cases")))
        self.assertEqual([], report["targets"])

    def test_unknown_duplicate_and_out_of_window_annotations_fail(self):
        self.poll.tick()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.fixture.study["start"] = "2026-09-07T04:00:00Z"
        case = self.freeze()[0]
        annotation = self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        prior_id = next(row["observation_id"] for row in self.frozen["observations"] if row["window_membership"] == "before_window")
        for invalid_id in ("not-a-retained-case", prior_id):
            annotation["case_id"] = invalid_id
            with self.subTest(identifier=invalid_id), self.assertRaises(ValueError):
                self.run_review()
        annotation["case_id"] = case["case_id"]
        self.labels["adjudications"].append(copy.deepcopy(annotation))
        with self.assertRaises(ValueError):
            self.run_review()

    def test_exact_frozen_support_binding_and_post_freeze_clock_required(self):
        case = self.paired()
        self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        original = copy.deepcopy(self.labels)
        for field, value in (("frozen_sha256", "0" * 64), ("reviewed_at", self.frozen["frozen_at"]),
                             ("reviewed_at", "2026-09-07T10:00:00Z"), ("prior_exposure", "")):
            self.labels = {**copy.deepcopy(original), field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.run_review()
        self.labels = copy.deepcopy(original)
        self.labels["adjudications"][0]["review_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.run_review()
        self.labels = copy.deepcopy(original)
        self.support_path.write_bytes(self.support_path.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            self.run_review()

    def test_strict_annotation_and_statement_fields_prevent_label_chosen_bodies(self):
        case = self.paired()
        annotation = self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        statement = annotation["targets"][0]
        for row, key, value in ((annotation, "body_path", "unrelated.html"),
                                (statement, "canonical_facility_id", "intel:fab52-chandler"),
                                (statement["before"], "path", case["body_path"])):
            row[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.run_review()
            row.pop(key)

    def test_exact_span_bounds_hash_and_literal_are_enforced(self):
        case = self.paired()
        annotation = self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        side = annotation["targets"][0]["after"]
        original = copy.deepcopy(side)
        for patch_value in ({"start": -1}, {"start": True}, {"end": 100000},
                            {"end": original["fragment"]["start"]}, {"sha256": "0" * 64}, {"locator": ""}):
            side["fragment"] = {**original["fragment"], **patch_value}
            with self.subTest(value=patch_value), self.assertRaises(ValueError):
                self.run_review()
        side.update(copy.deepcopy(original))
        side["literal"] = "2039"
        with self.assertRaises(ValueError):
            self.run_review()

    def test_context_and_reviewed_regions_are_required_and_byte_bound(self):
        case = self.paired()
        annotation = self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        side = annotation["targets"][0]["before"]
        contexts = side["context"]
        side["context"] = []
        with self.assertRaises(ValueError):
            self.run_review()
        side["context"] = contexts
        before = annotation["reviewed_regions"].pop(0)
        with self.assertRaises(ValueError):
            self.run_review()
        annotation["reviewed_regions"].insert(0, before)
        before["span"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.run_review()

    def test_revision_and_unresolved_preserve_reviewer_judgment(self):
        case = self.paired(BODY_2027)
        annotation = self.annotate(case, "reviewed_targets", statements=[self.statement(case, after="2027", verdict="revision")])
        self.assertEqual(1, self.run_review()["counts"]["target_verdicts"]["revision"])
        annotation["targets"][0]["verdict"] = "unresolved"
        self.assertEqual(1, self.run_review()["counts"]["target_verdicts"]["unresolved"])

    def test_equivalent_different_wording_can_be_reviewed_no_revision(self):
        case = self.paired(BODY_2028.replace(b"2028", b"the year 2028"))
        statement = self.statement(case, after="the year 2028")
        statement["reason"] = "The explicit year qualifier leaves the named source-stated planning year unchanged."
        self.annotate(case, "reviewed_targets", statements=[statement])
        self.assertEqual(1, self.run_review()["counts"]["target_verdicts"]["no_revision"])

    def test_same_literal_can_have_reviewed_context_revision(self):
        case = self.paired(BODY_2028.replace(b"is planned for", b"will not start before"))
        statement = self.statement(case, verdict="revision")
        statement["reason"] = "The source changed the temporal qualification while retaining the year literal."
        self.annotate(case, "reviewed_targets", statements=[statement])
        self.assertEqual(1, self.run_review()["counts"]["target_verdicts"]["revision"])

    def test_unchanged_collector_status_does_not_automatically_create_a_target_verdict(self):
        case = self.paired()
        self.assertEqual("unchanged", case["status"])
        self.assertEqual(0, self.run_review()["counts"]["target_pairs"])
        annotation = self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        annotation["targets"][0]["verdict"] = "revision"
        with self.assertRaises(ValueError):
            self.run_review()

    def test_multiple_subjects_or_milestones_per_document_are_preserved(self):
        self.capture.bodies["document"] += b"<p>Project Beta packaging is planned for 2029.</p>"
        self.poll.tick()
        self.poll.tick("2026-09-07T04:00:00Z")
        case = next(row for row in self.freeze() if row.get("comparison_eligible"))
        first = self.statement(case)
        second = self.statement(case, subject="Project Beta", milestone="packaging", before="2029", after="2029")
        annotation = self.annotate(case, "reviewed_targets", statements=[first, second])
        report = self.run_review()
        self.assertEqual((2, 2), (report["counts"]["target_pairs"], report["counts"]["unique_url_subject_milestone_formulation_scope_groups"]))
        self.assertEqual({"Project Alpha", "Project Beta"}, {row["source_native_subject"] for row in report["targets"]})
        annotation["targets"].append(copy.deepcopy(first))
        with self.assertRaises(ValueError):
            self.run_review()

    def test_shared_embedded_predecessor_is_used_not_nearest_observation(self):
        seed, _ = self.capture.run_capture("seed")
        self.fixture.study["seed_ledgers"] = [{"path": "seed/last_successful_checks.json",
            "sha256": population._hash((seed / "last_successful_checks.json").read_bytes())}]
        self.capture.bodies["document"] = BODY_2027
        self.capture.clock = "2026-09-07T04:00:00Z"
        first, _ = self.capture.run_capture("poll-captures/first", seed)
        self.capture.bodies["document"] = b"<article>" + BODY_2027 + b"</article>"
        self.capture.clock = "2026-09-07T05:00:00Z"
        second, _ = self.capture.run_capture("poll-captures/second", seed)
        self.poll.fixture.clock = "2026-09-07T05:10:00Z"
        queue.import_capture(self.poll.queue, second)
        self.poll.fixture.clock = "2026-09-07T05:20:00Z"
        queue.import_capture(self.poll.queue, first)
        for case in self.freeze():
            self.annotate(case, "reviewed_targets", statements=[self.statement(case, after="2027", verdict="revision")])
        report = self.run_review()
        self.assertEqual((2, 1, 1, 1), tuple(report["counts"][key] for key in (
            "target_pairs", "unique_url_subject_milestone_formulation_scope_groups", "repeated_group_observations", "unique_normalized_evidence_pairs")))
        self.assertEqual(2, len({row["case_id"] for row in report["targets"]}))
        self.assertEqual(1, len({row["group_id"] for row in report["targets"]}))
        self.assertEqual(1, len({row["evidence_pair_id"] for row in report["targets"]}))
        self.labels["adjudications"][1]["targets"][0]["before"] = copy.deepcopy(
            self.labels["adjudications"][1]["targets"][0]["after"])
        with self.assertRaises(ValueError):
            self.run_review()

    def test_tampered_retained_body_is_rejected_before_semantic_report(self):
        case = self.paired()
        self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        path = self.root / case["body_path"]
        path.write_bytes(path.read_bytes() + b" changed")
        with self.assertRaises(ValueError):
            self.run_review()

    def test_empty_census_has_no_fabricated_cases_or_coverage_rate(self):
        self.freeze()
        self.labels["supporting_reviews"] = []
        report = self.run_review()
        self.assertEqual(0, report["counts"]["total_cases"])
        self.assertEqual([], report["cases"])
        self.assertEqual([], report["targets"])
        self.assertEqual({"numerator": 0, "denominator": 0, "value": None}, report["coverage"])

    def test_literal_is_matched_against_bound_html_visible_text(self):
        self.capture.bodies["document"] = BODY_2028.replace(b"2028", b"<strong>2028</strong>")
        case = self.paired(self.capture.bodies["document"])
        statement = self.statement(case)
        for side in ("before", "after"):
            statement[side]["fragment"] = self.span(self.body(case, side), "<strong>2028</strong>")
            statement[side]["literal"] = " 2028\n"
        self.annotate(case, "reviewed_targets", statements=[statement])
        report = self.run_review()
        self.assertEqual(1, report["counts"]["target_verdicts"]["no_revision"])
        self.assertEqual(" 2028\n", report["targets"][0]["after"]["literal"])

    def test_utf8_offsets_are_bytes_and_split_codepoints_are_rejected(self):
        self.capture.bodies["document"] = BODY_2028.replace(b"Project Alpha", "Project Étoile".encode())
        case = self.paired(self.capture.bodies["document"])
        annotation = self.annotate(case, "reviewed_targets", statements=[self.statement(case, subject="Project Étoile")])
        self.assertEqual(1, self.run_review()["counts"]["target_pairs"])
        raw = self.body(case, "after")
        start = raw.index("É".encode())
        annotation["reviewed_regions"].append({"side": "after", "span": {
            "start": start, "end": start + 1, "sha256": population._hash(raw[start:start + 1]),
            "locator": "Artificial invalid half-codepoint span"}})
        with self.assertRaises(ValueError):
            self.run_review()

    def test_no_target_finding_requires_actual_regions_on_both_sides(self):
        case = self.paired()
        annotation = self.annotate(case, "no_relevant_scoped_target")
        annotation["reviewed_regions"] = [region for region in annotation["reviewed_regions"] if region["side"] == "after"]
        with self.assertRaises(ValueError):
            self.run_review()

    def test_ineligible_case_cannot_invent_a_before_side_or_target_pair(self):
        self.poll.tick()
        case = self.freeze()[0]
        annotation = self.annotate(case)
        annotation["reviewed_regions"] = [{"side": "before", "span": self.span(self.body(case, "after"))}]
        with self.assertRaises(ValueError):
            self.run_review()
        annotation["reviewed_regions"] = []
        annotation["targets"] = [{"invented": "target"}]
        with self.assertRaises(ValueError):
            self.run_review()

    def test_supporting_review_references_are_relative_exact_and_unique(self):
        self.paired()
        reference = copy.deepcopy(self.labels["supporting_reviews"][0])
        for invalid in ({**reference, "path": str(self.support_path)},
                        {**reference, "path": "../statement-support.json"},
                        {**reference, "sha256": "0" * 64}):
            self.labels["supporting_reviews"] = [invalid]
            with self.subTest(reference=invalid), self.assertRaises(ValueError):
                self.run_review()
        self.labels["supporting_reviews"] = [reference, copy.deepcopy(reference)]
        with self.assertRaises(ValueError):
            self.run_review()

    def test_label_review_and_frozen_bytes_rechecked_after_annotation(self):
        case = self.paired()
        self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        original = review._review_cases
        for path in (self.labels_path, self.support_path, self.frozen_path):
            retained = path.read_bytes() if path.exists() else None
            def mutate(*args):
                result = original(*args)
                path.write_bytes(path.read_bytes() + b" ")
                return result
            with self.subTest(path=path.name), patch.object(review, "_review_cases", side_effect=mutate), self.assertRaises(ValueError):
                self.run_review()
            if retained is not None:
                path.write_bytes(retained)

    def test_body_rechecked_after_annotation(self):
        case = self.paired()
        self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        path = self.root / case["predecessor"]["body_path"]
        original = review._review_cases
        def mutate(*args):
            result = original(*args)
            path.write_bytes(path.read_bytes() + b" changed after binding")
            return result
        with patch.object(review, "_review_cases", side_effect=mutate), self.assertRaises(ValueError):
            self.run_review()

    def test_queue_prefix_is_rechecked_after_annotation(self):
        self.paired()
        original = review._review_cases
        def mutate(*args):
            result = original(*args)
            with queue._connection(self.poll.queue, write=True) as connection:
                connection.execute("DROP TRIGGER no_event_update")
                connection.execute("UPDATE review_events SET payload='{}' WHERE sequence=1")
            return result
        with patch.object(review, "_review_cases", side_effect=mutate), self.assertRaises(ValueError):
            self.run_review()

    def test_review_code_drift_is_rejected(self):
        self.paired()
        with patch.object(review, "_code_hashes", side_effect=[{"review": "a"}, {"review": "b"}]), self.assertRaisesRegex(ValueError, "code changed"):
            self.run_review()

    def test_later_retained_additions_do_not_expand_the_frozen_denominator(self):
        case = self.paired()
        self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        original = self.run_review()
        self.poll.tick("2026-09-07T05:00:00Z")
        self.assertEqual(3, queue.queue_report(self.poll.queue)["run_count"])
        repeated = self.run_review()
        self.assertEqual(original, repeated)
        self.assertEqual(2, repeated["counts"]["total_cases"])

    def invoke_cli(self, output):
        self.labels_path.write_bytes(_pretty_bytes(self.labels))
        stdout, stderr = io.StringIO(), io.StringIO()
        args = ["review_source_statements.py", "--frozen", str(self.frozen_path), "--labels", str(self.labels_path),
                "--reference-root", str(self.root), "--output", str(output)]
        with patch("sys.argv", args), redirect_stdout(stdout), redirect_stderr(stderr):
            cli.main()
        return json.loads(stdout.getvalue())

    def test_cli_creates_only_a_new_report_and_preserves_existing_output(self):
        case = self.paired()
        self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        output = self.root / "statement-report.json"
        summary = self.invoke_cli(output)
        raw = output.read_bytes()
        self.assertEqual(population._hash(raw), summary["sha256"])
        self.assertEqual(2, json.loads(raw)["counts"]["total_cases"])
        with self.assertRaises(SystemExit):
            self.invoke_cli(output)
        self.assertEqual(raw, output.read_bytes())

    def test_cli_invalid_labels_do_not_create_report(self):
        self.paired()
        self.labels["frozen_sha256"] = "0" * 64
        output = self.root / "statement-report.json"
        with self.assertRaises(SystemExit):
            self.invoke_cli(output)
        self.assertFalse(output.exists())

    def test_hidden_html_slices_cannot_manufacture_visible_target_evidence(self):
        for hidden in (b"<script>2028</script>", b"<style>2028</style>",
                       b"<!-- 2028 -->", b'<div data-target="2028">Unrelated text</div>'):
            body = b"<h1>Project Update</h1>" + hidden + b"<p>Public context</p>"
            side = {"literal": "2028", "fragment": self.span(body, "2028"),
                    "context": [self.span(body)]}
            with self.subTest(hidden=hidden), self.assertRaisesRegex(ValueError, "literal"):
                review._side(side, body)

    def test_full_body_visibility_preserves_unicode_entities_and_markup_boundaries(self):
        body = '<p>α &amp; β<br>Opening <strong>by 2028</strong>.</p>'.encode("utf-8")
        span = self.span(body)
        self.assertEqual("α & β Opening by 2028.", review._span(span, body))
        self.assertEqual("by 2028", review._span(self.span(body, "by 2028"), body))

    def test_context_order_and_preexisting_targets_cannot_invent_a_revision(self):
        self.capture.bodies["document"] += b"<p>Separate project target is 2029.</p>"
        self.poll.tick()
        self.poll.tick("2026-09-07T04:00:00Z")
        case = next(row for row in self.freeze() if row["comparison_eligible"])
        statement = self.statement(case, verdict="revision")
        body = self.body(case, "before")
        contexts = [self.span(body, "Project Alpha production is planned for 2028."),
                    self.span(body, "Separate project target is 2029.")]
        statement["before"]["context"] = contexts
        statement["after"]["context"] = list(reversed(contexts))
        self.annotate(case, "reviewed_targets", statements=[statement])
        with self.assertRaisesRegex(ValueError, "identical"):
            self.run_review()
        statement["after"].update(literal="2029", fragment=self.span(body, "2029"))
        with self.assertRaisesRegex(ValueError, "identical"):
            self.run_review()

    def test_cosmetic_group_labels_do_not_inflate_evidence_uniqueness(self):
        case = self.paired()
        statement = self.statement(case)
        duplicate = {**copy.deepcopy(statement), "source_native_subject": " Project Alpha "}
        self.annotate(case, "reviewed_targets", statements=[statement, duplicate])
        with self.assertRaisesRegex(ValueError, "whitespace"):
            self.run_review()
        duplicate["source_native_subject"] = "Reviewer second source-native subject description"
        report = self.run_review()
        self.assertEqual(2, report["counts"]["unique_url_subject_milestone_formulation_scope_groups"])
        self.assertEqual(1, report["counts"]["unique_normalized_evidence_pairs"])
        self.assertEqual(1, report["counts"]["repeated_normalized_evidence_pairs"])

    def test_target_evidence_must_be_in_the_declared_reviewed_regions(self):
        case = self.paired()
        row = self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        row["reviewed_regions"] = [{"side": side, "span": self.span(self.body(case, side), "Project Update")}
                                   for side in ("before", "after")]
        with self.assertRaisesRegex(ValueError, "contained"):
            self.run_review()

    def test_partial_character_reference_cannot_invent_source_literal(self):
        body = b"<p>Project target: &#50;&#48;&#50;&#56;</p>"
        self.assertEqual("Project target: 2028", review._span(self.span(body), body))
        with self.assertRaisesRegex(ValueError, "character reference"):
            review._span(self.span(body, "50"), body)

    def test_cli_cannot_write_into_retained_source_or_poll_directories(self):
        case = self.paired()
        self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        packet = self.root / case["capture_path"]
        state = next((self.root / "poll-state").iterdir())
        for directory in (packet, packet / "responses", state, self.root / "poll-captures"):
            output = directory / "injected-review.json"
            with self.subTest(path=output), self.assertRaises(SystemExit):
                self.invoke_cli(output)
            self.assertFalse(output.exists())
        self.assertTrue(population.verify_population_sources(self.frozen_path, reference_root=self.root)["exact_retention_snapshot_replayed"])

    def test_cli_rejects_symlink_output_parent_and_changed_freeze_at_installation(self):
        case = self.paired()
        self.annotate(case, "reviewed_targets", statements=[self.statement(case)])
        alias = self.root / "packet-alias"
        alias.symlink_to(self.root / case["capture_path"], target_is_directory=True)
        with self.assertRaises(SystemExit):
            self.invoke_cli(alias / "injected-review.json")
        self.assertFalse((alias / "injected-review.json").exists())
        output = self.root / "statement-report.json"
        with self.assertRaisesRegex(ValueError, "changed before report"):
            cli._write_report(output, b"{}", self.frozen_path, self.root, "0" * 64)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
