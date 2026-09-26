from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from semiconductor_atlas import project_target_evaluation as evaluation
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from tests import test_project_target_population as population_fixtures


START = "2026-09-07T08:00:00Z"
END = "2026-09-07T10:00:00Z"
FREEZE = "2026-09-07T11:00:00Z"
REVIEW = "2026-09-07T12:00:00Z"
NOW = "2026-09-07T13:00:00Z"


def metric_population(cases):
    """Synthetic metric rows only; not a claim of a verified core census."""
    result = {"study_id": "synthetic-metric-fixture", "start": START, "end": END,
        "frozen_at": FREEZE, "design": "retrospective_diagnostic_not_preregistered",
        "packets": [], "opportunities": [], "predictions": [], "excluded_comparisons": [],
        "excluded_alerts": [], "cohort": [{"entity_id": "project"}]}
    for name, expected, predicted, in_window in cases:
        accepted = "2026-09-07T09:00:00Z" if in_window else "2026-09-07T07:00:00Z"
        result["packets"].append({"comparison_id": name, "accepted_at": accepted,
            "subject": {"entity_id": "project"},
            "claims": {"after": {"document": {"retrieved_at": "2026-09-06T09:00:00Z"}}}})
        row = {"comparison_id": name, "entity_id": "project", "accepted_at": accepted,
               "classification": "disjoint_earlier" if expected else "reaffirmation",
               "expected_proposal": expected, "transition_group_id": "transition-" + name}
        if in_window:
            result["opportunities"].append(row)
        else:
            result["excluded_comparisons"].append({**row, "reason": "accepted_before_window"})
        if predicted:
            result["predictions"].append({"id": "alert-" + name, "origin": "source_native_project",
                "observations": [{"comparison_id": name}], "first_recorded_at": "2026-09-07T09:30:00Z",
                "status": "pending", "decisions": []})
    return result


def judgments(**values):
    return {key: {"verdict": value} for key, value in values.items()}


class ProjectMetricTests(unittest.TestCase):
    def test_supported_precision_and_mechanical_pipeline_are_distinct(self):
        frozen = metric_population([("admitted", True, True, True), ("missing", True, False, True)])
        report = evaluation._score_population(frozen, judgments(admitted="supported_revision", missing="supported_revision"))
        self.assertEqual({"numerator": 1, "denominator": 1, "value": 1.0}, report["metrics"]["conditional_supported_proposal_precision"])
        self.assertEqual(0.5, report["metrics"]["accepted_expected_comparison_admission_coverage"]["value"])
        self.assertEqual(1, report["counts"]["not_admitted_by_cutoff"])
        self.assertEqual("not_admitted_by_cutoff", report["opportunities"][1]["admission_status"])
        self.assertIsNone(report["metrics"]["detection_recall"])

    def test_verified_no_change_denominator_excludes_unsupported_comparisons(self):
        frozen = metric_population([("false", True, True, True), ("quiet", False, False, True),
                                    ("unsupported", True, True, True)])
        report = evaluation._score_population(frozen, judgments(false="no_revision", quiet="no_revision", unsupported="unsupported_comparison"))
        self.assertEqual(0.5, report["metrics"]["conditional_no_change_false_positive_burden"]["value"])
        self.assertEqual(2, report["metrics"]["conditional_no_change_false_positive_burden"]["denominator"])
        self.assertEqual(1, report["counts"]["unsupported_comparisons_excluded_from_no_change_denominator"])
        self.assertEqual(0.0, report["metrics"]["conditional_supported_proposal_precision"]["value"])

    def test_unlabelled_and_unresolved_remain_separate_and_in_bounds(self):
        frozen = metric_population([("supported", True, True, True), ("unresolved", True, True, True),
                                    ("unlabelled", True, True, True), ("quiet", False, False, True)])
        report = evaluation._score_population(frozen, judgments(supported="supported_revision", unresolved="unresolved"))
        counts = report["counts"]["prediction_labels"]
        self.assertEqual((1, 1, 1), (counts["supported_revision"], counts["unresolved"], counts["unlabelled"]))
        bounds = report["metrics"]["supported_proposal_precision_bounds"]
        self.assertEqual(1 / 3, bounds["lower"]["value"])
        self.assertEqual(1.0, bounds["upper"]["value"])
        self.assertEqual(3, report["counts"]["unknown_opportunity_labels"])

    def test_no_change_unknown_bounds_distinguish_silent_and_alerted_opportunities(self):
        frozen = metric_population([("known", True, True, True), ("alerted", True, True, True), ("silent", False, False, True)])
        report = evaluation._score_population(frozen, judgments(known="no_revision"))
        bounds = report["metrics"]["no_change_false_positive_bounds"]
        self.assertEqual(0.5, bounds["lower"]["value"])
        self.assertEqual(1.0, bounds["upper"]["value"])
        self.assertFalse(bounds["undefined_if_no_negatives_possible"])

    def test_no_resolved_negative_denominator_never_becomes_zero_rate(self):
        for predicted, expected in ((True, 1.0), (False, 0.0)):
            with self.subTest(predicted=predicted):
                frozen = metric_population([("unknown", predicted, predicted, True)])
                metrics = evaluation._score_population(frozen, {})["metrics"]
                self.assertIsNone(metrics["conditional_no_change_false_positive_burden"]["value"])
                bounds = metrics["no_change_false_positive_bounds"]
                self.assertTrue(bounds["undefined_if_no_negatives_possible"])
                self.assertEqual(expected, bounds["lower"]["value"])
                self.assertEqual(expected, bounds["upper"]["value"])

    def test_empty_population_has_null_rates(self):
        report = evaluation._score_population(metric_population([]), {})
        for key in ("conditional_supported_proposal_precision", "conditional_no_change_false_positive_burden",
                    "accepted_expected_comparison_admission_coverage"):
            self.assertEqual({"numerator": 0, "denominator": 0, "value": None}, report["metrics"][key])

    def test_prior_comparison_backfill_is_prediction_not_current_opportunity(self):
        frozen = metric_population([("prior", True, True, False)])
        report = evaluation._score_population(frozen, judgments(prior="supported_revision"))
        self.assertEqual(1, report["counts"]["predictions_in_window"])
        self.assertEqual(0, report["counts"]["accepted_opportunities_in_window"])
        self.assertIsNone(report["metrics"]["accepted_expected_comparison_admission_coverage"]["value"])
        self.assertEqual(9000.0, report["predictions"][0]["claim_to_alert_seconds"])
        self.assertIsNone(report["predictions"][0]["publication_to_alert_seconds"])

    def test_lags_are_observed_pipeline_clocks_and_no_publication_date_is_imputed(self):
        frozen = metric_population([("target", True, True, True)])
        row = evaluation._score_population(frozen, {})["predictions"][0]
        self.assertEqual(86400.0, row["retrieval_to_claim_seconds"])
        self.assertEqual(1800.0, row["claim_to_alert_seconds"])
        self.assertIsNone(row["publication_to_alert_seconds"])
        frozen["predictions"][0]["first_recorded_at"] = "2026-09-07T08:00:00Z"
        with self.assertRaises(ValueError):
            evaluation._score_population(frozen, {})

    def test_repeated_transitions_not_claimed_as_independent_cases(self):
        frozen = metric_population([("first", True, True, True), ("repeat", True, False, True)])
        frozen["opportunities"][1]["transition_group_id"] = frozen["opportunities"][0]["transition_group_id"]
        counts = evaluation._score_population(frozen, {})["counts"]
        self.assertEqual(1, counts["unique_opportunity_transition_groups"])
        self.assertEqual(1, counts["repeated_transition_opportunities"])


class ProjectEvaluationBoundaryTests(unittest.TestCase):
    """Isolate label/scoring guards; census verification is not mocked in integration tests."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.frozen = metric_population([("target", True, True, True), ("quiet", False, False, True)])
        self.frozen_path = self.root / "frozen.json"
        self.labels_path = self.root / "labels.json"
        self.review_path = self.root / "support.json"
        self.frozen_path.write_bytes(_pretty_bytes(self.frozen))
        self.review_path.write_bytes(_pretty_bytes({"fixture_only": True, "scope": "source statement support"}))
        self.labels = {"format": evaluation.LABEL_FORMAT, "frozen_sha256": evaluation.legacy._hash(self.frozen_path.read_bytes()),
            "adjudicator": "fixture", "reviewed_at": REVIEW,
            "prior_exposure": "Reviewer previously saw the synthetic target; not blinded.",
            "coverage_statement": "Synthetic accepted-comparison metric fixture, not external census.",
            "adjudications": [{"comparison_id": "target", "verdict": "supported_revision", "reason": "Fixture source support",
                "review_sha256": evaluation.legacy._hash(self.review_path.read_bytes()), "locator": "fixture scope"}],
            "supporting_reviews": [{"path": self.review_path.name, "sha256": evaluation.legacy._hash(self.review_path.read_bytes())}]}
        now = patch.object(evaluation, "_now", return_value=NOW)
        now.start(); self.addCleanup(now.stop)

    def validate_labels(self):
        return evaluation._labels(self.labels_path, _pretty_bytes(self.labels), self.frozen, self.frozen_path.read_bytes())

    def score(self):
        self.labels_path.write_bytes(_pretty_bytes(self.labels))
        with patch.object(evaluation.population, "validate_population", return_value=(self.frozen, self.frozen_path.read_bytes())):
            return evaluation.evaluate(self.frozen_path, self.labels_path)

    def test_post_freeze_reviews_bind_exact_bytes_and_preserve_prior_exposure(self):
        report = self.score()
        self.assertEqual(self.labels["prior_exposure"], report["prior_exposure"])
        self.assertTrue(all(report[key] is False for key in ("blinding_verified", "independence_verified", "calibration_established", "phase3_gate_passed", "delivery_eligible")))
        self.assertEqual(self.review_path.read_bytes(), evaluation.legacy._unblob(report["supporting_reviews"][0]["content"]))
        self.assertEqual(self.labels["adjudications"], report["adjudications"])
        self.assertEqual(report, self.score())

    def test_labels_artifact_is_bounded_before_json_parse(self):
        with self.assertRaisesRegex(ValueError, "bounded 20 MB"):
            evaluation._labels(self.labels_path, b" " * 20_000_001, self.frozen, self.frozen_path.read_bytes())

    def test_exact_freeze_binding_and_post_freeze_clock_required(self):
        original = copy.deepcopy(self.labels)
        for key, value in (("frozen_sha256", "0" * 64), ("reviewed_at", FREEZE), ("reviewed_at", "2099-01-01T00:00:00Z"), ("prior_exposure", "")):
            self.labels = {**original, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.validate_labels()

    def test_unknown_duplicate_excluded_and_bad_verdict_labels_fail(self):
        original = copy.deepcopy(self.labels)
        for change in ({"comparison_id": "unknown"}, {"verdict": "completed_production"}, {"review_sha256": "f" * 64}, {"locator": ""}):
            self.labels = copy.deepcopy(original)
            self.labels["adjudications"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate_labels()
        self.labels = copy.deepcopy(original)
        self.labels["adjudications"].append(copy.deepcopy(self.labels["adjudications"][0]))
        with self.assertRaises(ValueError):
            self.validate_labels()

    def test_review_hash_duplicate_and_mutation_fail(self):
        self.labels["supporting_reviews"].append(copy.deepcopy(self.labels["supporting_reviews"][0]))
        with self.assertRaises(ValueError):
            self.validate_labels()
        self.labels["supporting_reviews"].pop()
        self.review_path.write_bytes(self.review_path.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            self.validate_labels()

    def test_missing_labels_stay_unlabelled_without_requiring_fake_reviews(self):
        self.labels["adjudications"] = []
        self.labels["supporting_reviews"] = []
        report = self.score()
        self.assertEqual(1, report["counts"]["prediction_labels"]["unlabelled"])
        self.assertEqual(2, report["counts"]["opportunity_labels"]["unlabelled"])
        self.assertIsNone(report["metrics"]["conditional_supported_proposal_precision"]["value"])

    def test_scoring_boundary_rejects_supporting_review_drift(self):
        original = evaluation._score_population
        def mutate(*args):
            report = original(*args)
            self.review_path.write_bytes(self.review_path.read_bytes() + b" ")
            return report
        with patch.object(evaluation, "_score_population", side_effect=mutate), self.assertRaisesRegex(ValueError, "inputs changed"):
            self.score()

    def test_scoring_boundary_rejects_code_drift(self):
        with patch.object(evaluation, "_code_hashes", side_effect=[{"evaluator": "a"}, {"evaluator": "b"}]), self.assertRaisesRegex(ValueError, "code changed"):
            self.score()

    def test_aliased_review_cannot_replace_original_frozen_byte_binding(self):
        original = self.frozen_path.read_bytes()
        changed = _pretty_bytes({**self.frozen, "changed_after_validation": True})
        changed_sha = evaluation.legacy._hash(changed)
        self.labels["supporting_reviews"] = [{"path": self.frozen_path.name, "sha256": changed_sha}]
        self.labels["adjudications"][0]["review_sha256"] = changed_sha
        self.labels_path.write_bytes(_pretty_bytes(self.labels))
        def validate_then_replace(path):
            self.frozen_path.write_bytes(changed)
            return self.frozen, original
        with patch.object(evaluation.population, "validate_population", side_effect=validate_then_replace), self.assertRaisesRegex(ValueError, "inputs changed"):
            evaluation.evaluate(self.frozen_path, self.labels_path)


class ProjectPopulationEvaluationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = population_fixtures.ProjectTargetPopulationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.labels_path = self.root / "evaluation-labels.json"
        self.review_path = self.root / "evaluation-review.json"
        self.review_path.write_bytes(_pretty_bytes({"fixture_only": True, "reviewed_at": "2026-09-07T19:00:00Z",
            "prior_exposure": "Synthetic case was seen before freeze", "finding": "Source-stated revision support only"}))

    def evaluate(self, frozen, comparison_ids):
        self.fixture.frozen_path.write_bytes(_pretty_bytes(frozen))
        support_sha = evaluation.legacy._hash(self.review_path.read_bytes())
        labels = {"format": evaluation.LABEL_FORMAT,
            "frozen_sha256": evaluation.legacy._hash(self.fixture.frozen_path.read_bytes()),
            "adjudicator": "integration fixture", "reviewed_at": "2026-09-07T19:00:00Z",
            "prior_exposure": "Fixture author reviewed these synthetic comparisons before freeze; not blinded.",
            "coverage_statement": "All accepted supported-route fixture comparisons, not external source completeness.",
            "adjudications": [{"comparison_id": comparison_id, "verdict": "supported_revision",
                "reason": "The fixture's two source versions support the stated target comparison.",
                "review_sha256": support_sha, "locator": "finding"} for comparison_id in comparison_ids],
            "supporting_reviews": [{"path": self.review_path.name, "sha256": support_sha}]}
        self.labels_path.write_bytes(_pretty_bytes(labels))
        with patch.object(evaluation, "_now", return_value="2026-09-07T20:00:00Z"), \
             patch.object(evaluation.population, "_now", return_value="2026-09-07T20:00:00Z"):
            return evaluation.evaluate(self.fixture.frozen_path, self.labels_path)

    def test_real_census_freeze_to_labels_to_report_without_mutating_core_or_queue(self):
        self.fixture.admit()
        second = self.fixture.second_review()
        frozen = self.fixture.freeze()
        core_changes = self.fixture.connection.total_changes
        history_bytes = self.fixture.history_path.read_bytes()
        report = self.evaluate(frozen, [self.fixture.first["run_id"], second["run_id"]])
        self.assertEqual(2, report["counts"]["accepted_opportunities_in_window"])
        self.assertEqual(1, report["counts"]["predictions_in_window"])
        self.assertEqual(0.5, report["metrics"]["accepted_expected_comparison_admission_coverage"]["value"])
        self.assertEqual(1.0, report["metrics"]["conditional_supported_proposal_precision"]["value"])
        self.assertEqual(1, report["counts"]["not_admitted_by_cutoff"])
        self.assertEqual(core_changes, self.fixture.connection.total_changes)
        self.assertEqual(history_bytes, self.fixture.history_path.read_bytes())
        self.assertEqual(report, self.evaluate(frozen, [self.fixture.first["run_id"], second["run_id"]]))

    def test_unadmitted_real_comparison_stays_in_pipeline_denominator(self):
        frozen = self.fixture.freeze()
        report = self.evaluate(frozen, [self.fixture.first["run_id"]])
        self.assertEqual(1, report["counts"]["accepted_opportunities_in_window"])
        self.assertEqual(0, report["counts"]["predictions_in_window"])
        self.assertIsNone(report["metrics"]["conditional_supported_proposal_precision"]["value"])
        self.assertEqual(0.0, report["metrics"]["accepted_expected_comparison_admission_coverage"]["value"])

    def test_real_population_tampering_rejected_before_scoring(self):
        frozen = self.fixture.freeze()
        frozen["opportunities"] = []
        with self.assertRaises(ValueError):
            self.evaluate(frozen, [])

if __name__ == "__main__":
    unittest.main()
