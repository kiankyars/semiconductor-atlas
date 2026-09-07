from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from semiconductor_atlas import curated_review as queue, curated_poll as poll
from semiconductor_atlas import source_vintage_review as vintage, source_statement_review as statements
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from scripts import review_source_vintages as cli
from tests import test_curated_observation_population as fixtures


URL = "https://www.nist.gov/chips/synthetic-alpha"
OLD_BODY = b"<h1>Project Update</h1><p>Project Alpha production is planned for 2028.</p>"
NEW_BODY = OLD_BODY.replace(b"2028", b"2027")


class SourceVintageReviewTests(unittest.TestCase):
    """Real synthetic capture/census replay; no production corpus or queue writes."""

    def setUp(self):
        self.fixture = fixtures.CuratedObservationPopulationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.poll = self.fixture.fixture
        self.capture = self.poll.capture
        for entry in self.capture.plan["policies"]:
            entry["url"] = "https://www.nist.gov/" + ("robots.txt" if entry["purpose"] == "access_policy" else "terms")
        self.capture.plan["documents"][0]["url"] = URL
        self.capture.bodies["document"] = NEW_BODY
        self.poll.repin_plan()
        self.historical = self.root / "historical"
        self.historical.mkdir()
        self.manifest_path = self.historical / "manifest.json"
        self.after_path = self.root / "after-population.json"
        self.study_path = self.root / "vintage-study.json"
        self.frozen_path = self.root / "vintage-frozen.json"
        self.labels_path = self.root / "vintage-labels.json"
        self.support_path = self.root / "vintage-support.json"
        self.support_path.write_bytes(_pretty_bytes({"fixture_only": True,
            "scope": "Manual synthetic source-native target review, not manufacturing attainment or independent truth."}))
        self.manifest = {"format": "semiconductor-atlas-source-inputs-v1",
            "retrieved_at": "2026-07-18T01:54:47Z",
            "retrieval_timestamp_basis": "Synthetic conservative batch completion, not per-URL HTTP time",
            "source_scopes": {}, "inputs": []}
        self.add_historical(URL, OLD_BODY)
        for module, clock in ((vintage, "2026-09-07T08:00:00Z"), (statements, "2026-09-07T10:00:00Z")):
            now = patch.object(module, "_now", return_value=clock)
            now.start()
            self.addCleanup(now.stop)

    def add_historical(self, url, body=OLD_BODY, record_type="award_detail_page"):
        name = f"document-{len(self.manifest['inputs'])}.html"
        (self.historical / name).write_bytes(body)
        self.manifest["inputs"].append({"path": name, "url": url, "record_type": record_type,
            "content_type": "text/html", "bytes": len(body), "sha256": vintage._hash(body)})
        self.manifest_path.write_bytes(_pretty_bytes(self.manifest))

    def add_current(self, identifier, url, body=NEW_BODY):
        entry = {**copy.deepcopy(self.capture.plan["documents"][0]), "id": identifier, "url": url}
        self.capture.plan["documents"].append(entry)
        self.capture.bodies[identifier] = body
        self.poll.repin_plan()

    def prepare_study(self):
        self.after = self.fixture.freeze()
        self.after_path.write_bytes(_pretty_bytes(self.after))
        self.study = {"format": vintage.STUDY_FORMAT, "study_id": "synthetic-retrospective-cross-vintage",
            "before_manifest": {"path": "historical/manifest.json", "sha256": vintage._hash(self.manifest_path.read_bytes())},
            "after_population": {"path": self.after_path.name, "sha256": vintage._hash(self.after_path.read_bytes())},
            "selection_rule": vintage.RULE_VERSION,
            "selection_rationale": "All exact shared synthetic NIST award URLs; previously exposed, not blinded."}
        self.study_path.write_bytes(_pretty_bytes(self.study))

    def freeze(self):
        self.prepare_study()
        self.frozen = vintage.freeze(self.study_path, reference_root=self.root)
        self.frozen_path.write_bytes(_pretty_bytes(self.frozen))
        self.labels = {"format": statements.LABEL_FORMAT, "frozen_sha256": vintage._hash(self.frozen_path.read_bytes()),
            "reviewer": "Synthetic fixture reviewer", "reviewed_at": "2026-09-07T09:00:00Z",
            "prior_exposure": "Fixture author saw source statements before this retrospective freeze.",
            "coverage_statement": "Declared retained exact-URL intersection only, not publisher completeness.",
            "target_scope": "Source-native synthetic production planning statements only.",
            "supporting_reviews": [{"path": self.support_path.name, "sha256": vintage._hash(self.support_path.read_bytes())}],
            "adjudications": []}
        return self.frozen["snapshot"]["cases"]

    def body(self, case, side):
        return (self.root / case[side]["body_path"]).read_bytes()

    @staticmethod
    def span(raw, needle=None):
        selected = raw if needle is None else needle.encode("utf-8")
        start = raw.index(selected)
        return {"start": start, "end": start + len(selected), "sha256": vintage._hash(selected),
                "locator": "Original synthetic UTF-8 body span"}

    def annotate(self, case, disposition="reviewed_targets", *, before="2028", after="2027", verdict="revision"):
        regions, targets = [], []
        if disposition in {"reviewed_targets", "no_relevant_scoped_target"}:
            regions = [{"side": side, "span": self.span(self.body(case, side))} for side in ("before", "after")]
        if disposition == "reviewed_targets":
            target = {"source_native_subject": "Project Alpha", "milestone": "production",
                "formulation": "planned production year", "scope": "Named synthetic source project only",
                "verdict": verdict, "reason": "Manual interpretation of this synthetic source-native target pair."}
            for side, literal in (("before", before), ("after", after)):
                raw = self.body(case, side)
                target[side] = {"literal": literal, "fragment": self.span(raw, literal), "context": [self.span(raw)]}
            targets = [target]
        annotation = {"case_id": case["case_id"], "disposition": disposition,
            "reason": "Synthetic scoped comparison only; no collector detection or physical attainment inference.",
            "review_sha256": vintage._hash(self.support_path.read_bytes()), "locator": "scope",
            "reviewed_regions": regions, "targets": targets}
        self.labels["adjudications"].append(annotation)
        return annotation

    def run_review(self):
        self.labels_path.write_bytes(_pretty_bytes(self.labels))
        return vintage.review(self.frozen_path, self.labels_path, reference_root=self.root)

    def test_first_observation_pairs_retrospectively_without_rewriting_collector(self):
        self.poll.tick()
        case = self.freeze()[0]
        original_after = self.after_path.read_bytes()
        original_queue = self.poll.queue.read_bytes()
        self.assertEqual("first_observation_requires_review", case["after"]["status"])
        self.assertFalse(case["after"]["comparison_eligible"])
        self.assertIsNone(case["after"]["predecessor"])
        self.assertTrue(case["comparison_eligible"])
        self.assertEqual("retrospective_cross_vintage_document_pair", case["case_kind"])
        self.annotate(case)
        report = self.run_review()
        self.assertEqual(vintage.REPORT_FORMAT, report["format"])
        self.assertEqual(1, report["counts"]["url_cases_with_reviewed_revision"])
        self.assertTrue(all(value is False for value in report["boundaries"].values()))
        self.assertTrue(all(value is None for value in report["metrics"].values()))
        self.assertEqual(original_after, self.after_path.read_bytes())
        self.assertEqual(original_queue, self.poll.queue.read_bytes())

    def test_all_shared_urls_include_failure_only_and_exclusions_reconcile(self):
        beta, old_only, new_only = ("https://www.nist.gov/chips/" + name for name in ("synthetic-beta", "historical-only", "current-only"))
        self.add_historical(beta)
        self.add_historical(old_only)
        self.add_historical("https://www.nist.gov/chips/chips-america-awards", b"<p>Fixture index</p>", "award_index_page")
        self.add_current("beta", beta)
        self.add_current("current-only", new_only)
        self.capture.overrides["beta"] = {"http_code": None, "curl_exit_code": 28}
        self.poll.tick()
        cases = self.freeze()
        counts = self.frozen["snapshot"]["counts"]
        self.assertEqual((3, 2, 1, 1), tuple(counts[key] for key in ("historical_award_urls", "shared_urls", "paired_urls", "uncomparable_urls")))
        self.assertEqual(counts["historical_award_urls"], counts["shared_urls"] + counts["excluded_historical_award_urls"])
        self.assertEqual(counts["after_document_checks"], counts["shared_after_checks"] + counts["excluded_after_checks"])
        self.assertEqual(counts["shared_after_checks"], counts["selected_after_observations"] + counts["unselected_eligible_after_observations"] + counts["ineligible_shared_after_checks"])
        self.assertEqual(1, len(self.frozen["snapshot"]["excluded_historical_inputs"]))
        failed = next(case for case in cases if case["url"] == beta)
        self.assertEqual("no_eligible_successful_after", failed["status"])
        self.assertIsNone(failed["after"])
        self.assertEqual("failed_check", failed["after_checks"][0]["status"])
        self.annotate(failed, "uncomparable")
        report = self.run_review()
        self.assertEqual((2, 1, 1), tuple(report["counts"][key] for key in ("selected_url_cases", "labelled_cases", "unlabelled_cases")))

    def test_latest_eligible_response_survives_a_later_failed_check(self):
        self.poll.tick()
        self.capture.bodies["document"] = NEW_BODY.replace(b"2027", b"2026")
        self.poll.tick("2026-09-07T04:00:00Z")
        self.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        self.poll.tick("2026-09-07T05:00:00Z")
        case = self.freeze()[0]
        self.assertEqual("2026-09-07T04:00:00Z", case["after"]["response_finished_at"])
        self.assertEqual(3, len(case["after_checks"]))
        self.assertEqual(1, self.frozen["snapshot"]["counts"]["ineligible_shared_after_checks"])
        actual = next(row for row in self.after["observations"] if row["observation_id"] == case["after"]["observation_id"])
        self.assertEqual(actual, case["after"])
        self.assertNotEqual(case["before"]["body_sha256"], case["after"]["predecessor"]["body_sha256"])
        self.annotate(case, after="2026")
        self.assertEqual(1, self.run_review()["counts"]["target_pairs"])

    def test_import_order_does_not_choose_the_latest_after(self):
        first, _ = self.capture.run_capture("poll-captures/first")
        self.capture.clock = "2026-09-07T04:00:00Z"
        self.capture.bodies["document"] = NEW_BODY.replace(b"2027", b"2026")
        second, _ = self.capture.run_capture("poll-captures/second")
        self.poll.fixture.clock = "2026-09-07T05:00:00Z"
        queue.import_capture(self.poll.queue, second)
        self.poll.fixture.clock = "2026-09-07T05:10:00Z"
        queue.import_capture(self.poll.queue, first)
        case = self.freeze()[0]
        self.assertEqual("2026-09-07T04:00:00Z", case["after"]["response_finished_at"])
        self.assertEqual("2026-09-07T05:00:00Z", case["after"]["queue_imported_at"])

    def test_taylor_and_austin_urls_are_not_aliases(self):
        austin = "https://www.nist.gov/chips/samsung-electronics-texas-austin"
        taylor = "https://www.nist.gov/chips/samsung-electronics-texas-taylor"
        self.manifest["inputs"][0]["url"] = austin
        self.manifest_path.write_bytes(_pretty_bytes(self.manifest))
        self.capture.plan["documents"][0]["url"] = taylor
        self.poll.repin_plan()
        self.poll.tick()
        self.freeze()
        counts = self.frozen["snapshot"]["counts"]
        self.assertEqual((0, 1, 1), tuple(counts[key] for key in ("shared_urls", "excluded_historical_award_urls", "excluded_after_checks")))
        self.assertEqual([], self.run_review()["cases"])

    def selection_fixture(self):
        """Pure selection checks use altered rows, not purported valid census artifacts."""
        self.poll.tick()
        case = self.freeze()[0]
        return {URL: case["before"]}, copy.deepcopy(self.after)

    def test_equal_time_conflicting_bodies_remain_uncomparable(self):
        before, after = self.selection_fixture()
        conflict = {**copy.deepcopy(after["observations"][0]), "observation_id": "conflicting-tie", "body_sha256": "0" * 64}
        after["observations"].append(conflict)
        selected = vintage._select(self.study, self.manifest, before, after, self.root)
        case = selected["cases"][0]
        self.assertEqual("ambiguous_latest_successful_bodies", case["status"])
        self.assertFalse(case["comparison_eligible"])
        self.assertIsNone(case["after"])
        self.assertEqual(2, len(case["latest_tied_observation_ids"]))

    def test_equal_time_identical_bodies_keep_every_tied_observation_id(self):
        before, after = self.selection_fixture()
        duplicate = {**copy.deepcopy(after["observations"][0]), "observation_id": "identical-tie"}
        after["observations"].append(duplicate)
        result = vintage._select(self.study, self.manifest, before, after, self.root)
        case = result["cases"][0]
        self.assertTrue(case["comparison_eligible"])
        self.assertEqual(2, len(case["latest_tied_observation_ids"]))
        self.assertEqual(1, result["counts"]["selected_after_observations"])
        self.assertEqual(1, result["counts"]["unselected_eligible_after_observations"])
        self.assertEqual(result, vintage._select(self.study, self.manifest, before, {**after, "observations": after["observations"][::-1]}, self.root))

    def test_exact_comparison_read_buffer_drift_is_rejected_without_file_mutation(self):
        before, after = self.selection_fixture()
        old_path = self.root / before[URL]["body_path"]
        new_path = self.root / after["observations"][0]["body_path"]
        old_raw, new_raw = old_path.read_bytes(), new_path.read_bytes()
        original_read = vintage._read

        def drift(path):
            return new_raw if path == old_path else original_read(path)

        with patch.object(vintage, "_read", side_effect=drift):
            with self.assertRaisesRegex(ValueError, "comparison body changed after source verification"):
                vintage._select(self.study, self.manifest, before, after, self.root)
        self.assertEqual(old_raw, old_path.read_bytes())
        self.assertEqual(new_raw, new_path.read_bytes())

    def test_latest_response_selection_preserves_microsecond_precision(self):
        before, after = self.selection_fixture()
        first = after["observations"][0]
        first["response_finished_at"] = "2026-09-07T03:00:00.000001Z"
        latest = {**copy.deepcopy(first), "observation_id": "microsecond-later",
                  "response_finished_at": "2026-09-07T03:00:00.000002Z"}
        after["observations"].append(latest)
        result = vintage._select(self.study, self.manifest, before, after, self.root)
        case = result["cases"][0]
        self.assertEqual(latest, case["after"])
        self.assertEqual(["microsecond-later"], case["latest_tied_observation_ids"])
        self.assertEqual(result, vintage._select(self.study, self.manifest, before,
            {**after, "observations": after["observations"][::-1]}, self.root))

    def test_selection_rejects_noncanonical_response_clocks(self):
        before, after = self.selection_fixture()
        after["observations"][0]["response_finished_at"] = "2026-09-07T03:00:00.000000Z"
        with self.assertRaises(ValueError):
            vintage._select(self.study, self.manifest, before, after, self.root)

    def test_policy_blocked_and_incomplete_shared_urls_remain_uncomparable(self):
        self.capture.bodies["rights"] += b"<p>Changed policy</p>"
        self.poll.tick()
        case = self.freeze()[0]
        self.assertEqual("not_attempted_policy_blocked", case["after_checks"][0]["status"])
        self.annotate(case, "uncomparable")
        self.assertEqual(1, self.run_review()["counts"]["dispositions"]["uncomparable"])

    def test_incomplete_intent_alone_defines_shared_url_without_completed_body(self):
        with patch.object(poll, "capture_sources", side_effect=ValueError("synthetic interrupted capture")):
            self.poll.tick()
        case = self.freeze()[0]
        self.assertEqual([], case["after_checks"])
        self.assertEqual(1, len(case["incomplete_after_cases"]))
        self.assertFalse(case["comparison_eligible"])
        self.assertEqual(1, self.frozen["snapshot"]["counts"]["shared_incomplete_document_cases"])
        self.annotate(case, "uncomparable")
        self.assertEqual(0, self.run_review()["counts"]["target_pairs"])

    def test_cutoff_and_prior_window_observations_are_not_selected(self):
        self.poll.tick()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.poll.tick("2026-09-07T05:00:00Z")
        self.fixture.study.update(start="2026-09-07T04:00:00Z", end="2026-09-07T05:00:00Z")
        case = self.freeze()[0]
        self.assertEqual(1, len(case["after_checks"]))
        self.assertEqual("2026-09-07T04:00:00Z", case["after"]["response_finished_at"])

    def test_nonincreasing_historical_clock_is_not_a_valid_pair(self):
        self.poll.tick()
        self.manifest["retrieved_at"] = "2026-09-07T03:00:00Z"
        self.manifest_path.write_bytes(_pretty_bytes(self.manifest))
        case = self.freeze()[0]
        self.assertEqual("nonincreasing_knowledge_clocks", case["status"])
        self.assertFalse(case["comparison_eligible"])
        self.annotate(case, "uncomparable")
        self.assertEqual(0, self.run_review()["counts"]["target_pairs"])

    def test_historical_batch_basis_is_preserved_not_replaced_by_directory_name(self):
        self.poll.tick()
        case = self.freeze()[0]
        self.assertEqual(self.manifest["retrieved_at"], case["before"]["knowledge_clock"])
        self.assertEqual(self.manifest["retrieval_timestamp_basis"], case["before"]["knowledge_clock_basis"])
        with patch.object(vintage, "_now", return_value="2026-09-07T06:00:00Z"), self.assertRaises(ValueError):
            vintage.freeze(self.study_path, reference_root=self.root)

    def test_duplicate_historical_url_and_bad_source_bytes_fail_closed(self):
        self.add_historical(URL)
        self.poll.tick()
        self.prepare_study()
        with self.assertRaises(ValueError):
            vintage.freeze(self.study_path, reference_root=self.root)
        self.manifest["inputs"].pop()
        self.manifest_path.write_bytes(_pretty_bytes(self.manifest))
        self.prepare_study()
        (self.historical / self.manifest["inputs"][0]["path"]).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            vintage.freeze(self.study_path, reference_root=self.root)

    def test_missing_labels_do_not_drop_cases_and_unknown_duplicates_are_rejected(self):
        self.poll.tick()
        case = self.freeze()[0]
        report = self.run_review()
        self.assertEqual((1, 0, 1), tuple(report["counts"][key] for key in ("selected_url_cases", "labelled_cases", "unlabelled_cases")))
        annotation = self.annotate(case)
        self.labels["adjudications"].append(copy.deepcopy(annotation))
        with self.assertRaises(ValueError):
            self.run_review()
        self.labels["adjudications"].pop()
        annotation["case_id"] = "not-a-frozen-case"
        with self.assertRaises(ValueError):
            self.run_review()

    def test_exact_spans_and_body_choice_are_bound_to_selected_pair(self):
        self.poll.tick()
        case = self.freeze()[0]
        annotation = self.annotate(case)
        target = annotation["targets"][0]
        original = copy.deepcopy(target["before"])
        target["before"] = copy.deepcopy(target["after"])
        with self.assertRaises(ValueError):
            self.run_review()
        target["before"] = original
        target["after"]["fragment"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.run_review()

    def test_hidden_script_literal_cannot_be_reviewed_as_visible_source_text(self):
        hidden = b"<script>Hidden target 2099</script>"
        old_path = self.historical / self.manifest["inputs"][0]["path"]
        old_path.write_bytes(OLD_BODY + hidden)
        self.manifest["inputs"][0].update(bytes=len(OLD_BODY + hidden), sha256=vintage._hash(OLD_BODY + hidden))
        self.manifest_path.write_bytes(_pretty_bytes(self.manifest))
        self.capture.bodies["document"] = NEW_BODY + hidden
        self.poll.tick()
        case = self.freeze()[0]
        self.annotate(case, before="2099", after="2099", verdict="no_revision")
        with self.assertRaises(ValueError):
            self.run_review()

    def test_utf8_literal_spans_use_exact_bytes_and_reject_split_codepoints(self):
        old = OLD_BODY.replace(b"2028", "été 2028".encode())
        new = NEW_BODY.replace(b"2027", "été 2027".encode())
        (self.historical / self.manifest["inputs"][0]["path"]).write_bytes(old)
        self.manifest["inputs"][0].update(bytes=len(old), sha256=vintage._hash(old))
        self.manifest_path.write_bytes(_pretty_bytes(self.manifest))
        self.capture.bodies["document"] = new
        self.poll.tick()
        case = self.freeze()[0]
        annotation = self.annotate(case, before="été 2028", after="été 2027")
        self.assertEqual(1, self.run_review()["counts"]["target_pairs"])
        fragment = annotation["targets"][0]["after"]["fragment"]
        fragment["start"] += 1
        fragment["sha256"] = vintage._hash(new[fragment["start"]:fragment["end"]])
        with self.assertRaises(ValueError):
            self.run_review()

    def test_markup_only_document_change_does_not_establish_target_revision(self):
        self.capture.bodies["document"] = OLD_BODY.replace(b"2028", b"<strong>2028</strong>")
        self.poll.tick()
        case = self.freeze()[0]
        self.assertEqual("raw_bytes_only", case["document_change"])
        annotation = self.annotate(case, after="2028")
        with self.assertRaises(ValueError):
            self.run_review()
        annotation["targets"][0]["verdict"] = "no_revision"
        report = self.run_review()
        self.assertEqual({"no_revision": 1}, report["counts"]["target_verdicts"])
        self.assertEqual(0, report["counts"]["url_cases_with_reviewed_revision"])

    def test_reviewer_no_revision_judgment_is_not_literal_string_equality(self):
        self.capture.bodies["document"] = OLD_BODY.replace(b"2028", b"the year 2028")
        self.poll.tick()
        case = self.freeze()[0]
        self.annotate(case, after="the year 2028", verdict="no_revision")
        self.assertEqual({"no_revision": 1}, self.run_review()["counts"]["target_verdicts"])

    def test_multiple_formulations_do_not_multiply_url_or_subject_denominators(self):
        self.poll.tick()
        case = self.freeze()[0]
        annotation = self.annotate(case)
        additional = {**copy.deepcopy(annotation["targets"][0]), "formulation": "alternate reviewer framing"}
        annotation["targets"].append(additional)
        report = self.run_review()
        counts = report["counts"]
        self.assertEqual((2, 2, 1, 1, 1), tuple(counts[field] for field in (
            "target_pairs", "unique_target_formulation_groups", "unique_normalized_evidence_pairs",
            "url_cases_with_reviewed_revision", "reviewer_described_revised_subject_scopes")))
        annotation["targets"].append(copy.deepcopy(additional))
        with self.assertRaises(ValueError):
            self.run_review()

    def test_target_fragments_must_be_inside_reviewed_regions(self):
        self.poll.tick()
        case = self.freeze()[0]
        annotation = self.annotate(case)
        for region in annotation["reviewed_regions"]:
            region["span"] = self.span(self.body(case, region["side"]), "Project Update")
        with self.assertRaises(ValueError):
            self.run_review()

    def test_labels_and_review_clocks_bind_the_cross_vintage_freeze(self):
        self.poll.tick()
        self.freeze()
        original = copy.deepcopy(self.labels)
        for key, value in (("reviewed_at", self.frozen["frozen_at"]), ("reviewed_at", "2026-09-07T11:00:00Z"),
                           ("frozen_sha256", vintage._hash(self.after_path.read_bytes())), ("prior_exposure", "")):
            self.labels = {**copy.deepcopy(original), key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.run_review()

    def test_deterministic_review_after_later_captures_preserves_frozen_selection(self):
        self.poll.tick()
        case = self.freeze()[0]
        self.annotate(case)
        original = self.run_review()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.assertEqual(2, queue.queue_report(self.poll.queue)["run_count"])
        self.assertEqual(original, self.run_review())
        self.assertEqual(1, original["selection_counts"]["after_document_checks"])

    def test_before_and_after_body_drift_during_review_fail_closed(self):
        self.poll.tick()
        case = self.freeze()[0]
        self.annotate(case)
        original = vintage._adjudicate
        for side in ("before", "after"):
            path = self.root / case[side]["body_path"]
            retained = path.read_bytes()
            def mutate(*args):
                result = original(*args)
                path.write_bytes(retained + b" changed across annotation")
                return result
            with self.subTest(side=side), patch.object(vintage, "_adjudicate", side_effect=mutate), self.assertRaises(ValueError):
                self.run_review()
            path.write_bytes(retained)

    def test_manifest_and_after_population_drift_during_freeze_fail_closed(self):
        self.poll.tick()
        self.prepare_study()
        original = vintage._select
        for path in (self.manifest_path, self.after_path):
            retained = path.read_bytes()
            def mutate(*args):
                result = original(*args)
                path.write_bytes(retained + b" ")
                return result
            with self.subTest(path=path.name), patch.object(vintage, "_select", side_effect=mutate), self.assertRaises(ValueError):
                vintage.freeze(self.study_path, reference_root=self.root)
            path.write_bytes(retained)

    def test_code_drift_during_freeze_and_review_is_rejected(self):
        self.poll.tick()
        self.prepare_study()
        with patch.object(vintage, "_code_hashes", side_effect=[{"code": "a"}, {"code": "b"}]), self.assertRaises(ValueError):
            vintage.freeze(self.study_path, reference_root=self.root)
        self.freeze()
        actual = vintage._code_hashes()
        calls = 0
        def drift():
            nonlocal calls
            calls += 1
            return actual if calls < 6 else {**actual, "injected-drift": "0" * 64}
        with patch.object(vintage, "_code_hashes", side_effect=drift), self.assertRaises(ValueError):
            self.run_review()

    def test_freeze_rejects_clock_moving_backwards_before_returning_artifact(self):
        self.poll.tick()
        self.prepare_study()
        with patch.object(vintage, "_now", side_effect=["2026-09-07T08:00:00Z", "2026-09-07T07:30:00Z"]):
            with self.assertRaises(ValueError):
                vintage.freeze(self.study_path, reference_root=self.root)

    def test_label_and_supporting_review_drift_are_rechecked(self):
        self.poll.tick()
        case = self.freeze()[0]
        self.annotate(case)
        original = vintage._adjudicate
        for path in (self.labels_path, self.support_path):
            retained = path.read_bytes() if path.exists() else None
            def mutate(*args):
                result = original(*args)
                path.write_bytes(path.read_bytes() + b" ")
                return result
            with self.subTest(path=path.name), patch.object(vintage, "_adjudicate", side_effect=mutate), self.assertRaises(ValueError):
                self.run_review()
            if retained is not None:
                path.write_bytes(retained)

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["review_source_vintages.py", *map(str, args)]), redirect_stdout(stdout), redirect_stderr(stderr):
            cli.main()
        return json.loads(stdout.getvalue())

    def test_cli_freeze_verify_review_are_new_only_and_do_not_modify_sources(self):
        self.poll.tick()
        self.prepare_study()
        sources = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        summary = self.invoke("freeze", "--study", self.study_path, "--reference-root", self.root, "--output", self.frozen_path)
        raw = self.frozen_path.read_bytes()
        self.assertEqual(vintage._hash(raw), summary["sha256"])
        with self.assertRaises(SystemExit):
            self.invoke("freeze", "--study", self.study_path, "--reference-root", self.root, "--output", self.frozen_path)
        self.assertEqual(raw, self.frozen_path.read_bytes())
        self.assertTrue(self.invoke("verify", "--frozen", self.frozen_path, "--reference-root", self.root)["exact_source_replay"])
        self.frozen = json.loads(raw)
        self.labels = {"format": statements.LABEL_FORMAT, "frozen_sha256": vintage._hash(raw),
            "reviewer": "CLI synthetic reviewer", "reviewed_at": "2026-09-07T09:00:00Z",
            "prior_exposure": "Synthetic fixtures were previously seen; not blinded.",
            "coverage_statement": "All declared fixture URLs only.", "target_scope": "Source-native planning only.",
            "supporting_reviews": [], "adjudications": []}
        self.labels_path.write_bytes(_pretty_bytes(self.labels))
        report_path = self.root / "vintage-report.json"
        self.invoke("review", "--frozen", self.frozen_path, "--labels", self.labels_path, "--reference-root", self.root, "--output", report_path)
        report_raw = report_path.read_bytes()
        with self.assertRaises(SystemExit):
            self.invoke("review", "--frozen", self.frozen_path, "--labels", self.labels_path, "--reference-root", self.root, "--output", report_path)
        self.assertEqual(report_raw, report_path.read_bytes())
        self.assertEqual(sources, {path: path.read_bytes() for path in sources})

    def test_cli_refuses_outputs_inside_historical_capture_and_poll_roots(self):
        self.poll.tick()
        self.prepare_study()
        capture = next((self.root / "poll-captures").iterdir())
        for directory in (self.historical, self.root / "poll-state", capture):
            output = directory / "must-not-create.json"
            with self.subTest(directory=directory.name), self.assertRaises(SystemExit):
                self.invoke("freeze", "--study", self.study_path, "--reference-root", self.root, "--output", output)
            self.assertFalse(output.exists())

    def test_cli_and_writer_refuse_parent_traversal_and_symlink_aliases(self):
        self.poll.tick()
        self.prepare_study()
        (self.root / "artifacts").mkdir()
        (self.root / "historical-alias").symlink_to(self.historical, target_is_directory=True)
        (self.root / "output-directory").mkdir()
        (self.root / "output-alias").symlink_to(self.root / "output-directory", target_is_directory=True)
        for output in (self.root / "artifacts" / ".." / "historical" / "injected.json",
                       self.root / "historical-alias" / "injected.json",
                       self.root / "output-alias" / "injected.json"):
            with self.subTest(output=str(output)):
                with self.assertRaises((ValueError, OSError)):
                    vintage.write_new(output, {"fixture": True}, reference_root=self.root,
                                      protected_directories=["historical"])
                self.assertFalse(output.exists())
                with self.assertRaises(SystemExit):
                    self.invoke("freeze", "--study", self.study_path, "--reference-root", self.root, "--output", output)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
