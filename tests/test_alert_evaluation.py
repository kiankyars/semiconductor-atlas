from __future__ import annotations

import copy
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from semiconductor_atlas import ai_critical_alert_review as ledger
from semiconductor_atlas import alert_evaluation as evaluation
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from tests import test_ai_critical_alert_review as review_fixtures


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "baselines/ai_critical_manufacturing_v1.json"
START = "2026-09-07T09:00:00Z"
END = "2026-09-07T11:00:00Z"
FREEZE = "2026-09-07T12:00:00Z"
LABEL = "2026-09-07T13:00:00Z"
SCORE = "2026-09-07T14:00:00Z"


class EvaluationFixture:
    """Compose the existing release fixture without inheriting its test methods."""

    @classmethod
    def setUpClass(cls):
        review_fixtures.AICriticalAlertReviewTests.setUpClass()

    def setUp(self):
        self.fixture = review_fixtures.AICriticalAlertReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.history = self.root / "history.json"
        self.frozen = self.root / "frozen.json"
        self.labels = self.root / "labels.json"
        self.support = self.root / "label-review.json"
        self.support.write_bytes(_pretty_bytes({"fixture_only": True, "finding": "Synthetic label support"}))
        self.clock = FREEZE
        clock = patch.object(ledger, "_now", side_effect=lambda: self.clock)
        clock.start()
        self.addCleanup(clock.stop)

    def _export(self):
        self.history.write_bytes(_pretty_bytes(ledger.export_queue(self.fixture.db)))

    def _freeze(self, *, start=START, end=END, import_first=True, output=None):
        if import_first:
            self.fixture._import()
        self._export()
        self.clock = FREEZE
        return evaluation.freeze_predictions(self.history, BASELINE, output or self.frozen,
            study_id="synthetic-evaluation-fixture", start=start, end=end)

    def _label_payload(self, *, verdict="supported", complete=False, deadline="2026-09-07T10:30:00Z"):
        frozen = json.loads(self.frozen.read_bytes())
        alert = frozen["predictions"][0] if frozen["predictions"] else None
        digest = ledger._hash(self.support.read_bytes())
        truth = {"id": "fixture-truth", "facility_key": alert["subject_stable_key"] if alert else frozen["cohort"][0]["facility_key"],
                 "rule_id": alert["rule_id"] if alert else "lifecycle_positive_stage_change",
                 "available_at": "2026-09-07T09:30:00Z", "deadline_at": deadline,
                 "review_sha256": digest, "locator": "fixture-only finding"}
        judgments = [] if verdict is None or alert is None else [{
            "alert_id": alert["id"], "verdict": verdict,
            "truth_event_id": truth["id"] if verdict == "supported" else None,
            "reason": "Synthetic outcome, not evidence of manufacturing activity.", "review_sha256": digest}]
        return {"format": evaluation.LABEL_FORMAT, "frozen_sha256": ledger._hash(self.frozen.read_bytes()),
                "adjudicator": "fixture-reviewer", "reviewed_at": LABEL, "blinding": "not_blinded",
                "truth_inventory_complete": complete, "coverage_statement": "Synthetic fixture; no real coverage claim.",
                "truth_events": [truth], "adjudications": judgments,
                "supporting_reviews": [{"path": self.support.name, "sha256": digest}]}

    def _score(self, payload):
        self.labels.write_bytes(_pretty_bytes(payload))
        self.clock = SCORE
        return evaluation.evaluate(self.frozen, self.labels)

    def _four_episodes(self):
        self.fixture._import()
        prior = self.fixture.current
        for index in range(1, 4):
            current = self.fixture.fixture._write_release(f"episode-{index}", self.fixture.fixture._spec(
                f"fixture-episode-{index}", as_of=f"2026-08-{21 + index:02d}",
                recorded_at=f"2026-08-{21 + index:02d}T18:00:00Z",
                mutate=self.fixture._progress if index % 2 == 0 else None))
            bundle, admission = self.fixture._comparison(f"episode-comparison-{index}", prior, current)
            self.fixture._import(clock=f"2026-09-07T10:{index * 10:02d}:00Z", prior=prior, current=current,
                                 bundle=bundle, admission=admission)
            prior = current


class AlertEvaluationTests(EvaluationFixture, unittest.TestCase):
    def test_freeze_includes_entire_cohort_and_actual_admission_not_source_date(self):
        result = self._freeze()
        frozen, raw = evaluation.validate_frozen(self.frozen)
        self.assertEqual((7, 1), (result["cohort_count"], result["prediction_count"]))
        self.assertEqual(FREEZE, result["frozen_at"])
        self.assertEqual(ledger._hash(raw), result["sha256"])
        alert = frozen["predictions"][0]
        self.assertEqual(review_fixtures.NOW, alert["first_recorded_at"])
        self.assertNotEqual(alert["first_recorded_at"], alert["observations"][0]["release_recorded_at"])
        self.assertEqual("retrospective_diagnostic_not_preregistered", frozen["design"])

    def test_freeze_replays_after_original_release_history_and_database_are_removed(self):
        self._freeze()
        expected, raw = evaluation.validate_frozen(self.frozen)
        for path in (self.fixture.prior, self.fixture.current, self.fixture.bundle):
            shutil.rmtree(path)
        self.fixture.db.unlink()
        self.history.unlink()
        self.fixture.admission.unlink()
        (self.root / "comparison-source-review.json").unlink()
        self.assertEqual((expected, raw), evaluation.validate_frozen(self.frozen))

    def test_half_open_window_includes_start_excludes_end_and_reports_pre_window_episode(self):
        self.fixture._import()
        for name, start, end, count, excluded in (
            ("at-start", review_fixtures.NOW, END, 1, 0),
            ("at-end", START, review_fixtures.NOW, 0, 0),
            ("before-start", "2026-09-07T10:00:01Z", END, 0, 1),
        ):
            output = self.root / f"{name}.json"
            result = self._freeze(start=start, end=end, import_first=False, output=output)
            artifact, _ = evaluation.validate_frozen(output)
            with self.subTest(name=name):
                self.assertEqual(count, result["prediction_count"])
                self.assertEqual(excluded, len(artifact["excluded"]))
                if excluded:
                    self.assertEqual(["admitted_before_window"], artifact["excluded"][0]["reasons"])

    def test_cutoff_does_not_leak_later_review_status(self):
        alert = self.fixture._import()["alerts"][0]
        self.fixture._decide(alert, "acknowledge", clock=END)
        self._freeze(import_first=False)
        frozen, _ = evaluation.validate_frozen(self.frozen)
        self.assertEqual("pending", frozen["predictions"][0]["status"])
        self.assertEqual([], frozen["predictions"][0]["decisions"])
        self.assertEqual("acknowledged", ledger.queue_report(self.fixture.db)["alerts"][0]["status"])

    def test_future_or_empty_freeze_window_is_rejected_without_output(self):
        self.fixture._import()
        self._export()
        for start, end in ((END, END), (FREEZE, END), (START, SCORE)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                evaluation.freeze_predictions(self.history, BASELINE, self.frozen, study_id="fixture", start=start, end=end)
            self.assertFalse(self.frozen.exists())

    def test_history_with_events_after_actual_freeze_clock_is_rejected(self):
        self.fixture._import(clock=LABEL)
        self._export()
        with self.assertRaisesRegex(ValueError, "future knowledge"):
            evaluation.freeze_predictions(self.history, BASELINE, self.frozen, study_id="fixture", start=START, end=END)
        self.assertFalse(self.frozen.exists())

    def test_frozen_artifact_rejects_future_clock(self):
        self._freeze()
        artifact = json.loads(self.frozen.read_bytes())
        artifact["frozen_at"] = SCORE
        self.frozen.write_bytes(_pretty_bytes(artifact))
        with self.assertRaises(ValueError):
            evaluation.validate_frozen(self.frozen)

    def test_freeze_does_not_overwrite_existing_output(self):
        self._freeze()
        before = self.frozen.read_bytes()
        with self.assertRaises(FileExistsError):
            self._freeze(import_first=False)
        self.assertEqual(before, self.frozen.read_bytes())

    def test_frozen_replay_rejects_changed_predictions_cohort_history_code_and_bytes(self):
        self._freeze()
        original = json.loads(self.frozen.read_bytes())
        mutations = (
            lambda value: value["predictions"].clear(),
            lambda value: value["predictions"][0].update(delivery_eligible=0),
            lambda value: value["cohort"].pop(),
            lambda value: value["history"].update(sha256="0" * 64),
            lambda value: value["code_sha256"].update(evaluator="0" * 64),
            lambda value: value.update(history_head_at_cutoff="0" * 64),
        )
        for index, mutate in enumerate(mutations):
            value = copy.deepcopy(original)
            mutate(value)
            self.frozen.write_bytes(_pretty_bytes(value))
            with self.subTest(index=index), self.assertRaises(ValueError):
                evaluation.validate_frozen(self.frozen)
        self.frozen.write_bytes(_pretty_bytes(original) + b"\n")
        with self.assertRaisesRegex(ValueError, "canonical"):
            evaluation.validate_frozen(self.frozen)

    def test_supported_timely_score_is_deterministic_and_never_delivery_or_gate_approval(self):
        self._freeze()
        payload = self._label_payload(complete=True)
        before = ledger.export_queue(self.fixture.db)
        report = self._score(payload)
        self.assertEqual(report, evaluation.evaluate(self.frozen, self.labels))
        self.assertEqual(before, ledger.export_queue(self.fixture.db))
        self.assertEqual(7, len(report["cohort"]))
        self.assertEqual(6, sum(row["admitted_alert_count"] == 0 for row in report["cohort"]))
        self.assertEqual(1, report["counts"]["supported"])
        self.assertEqual(1800.0, report["predictions"][0]["source_to_admission_seconds"])
        self.assertTrue(report["predictions"][0]["timely"])
        self.assertEqual({"numerator": 1, "denominator": 1, "value": 1.0}, report["metrics"]["timely_event_recall"])
        self.assertFalse(report["phase3_gate_passed"])
        self.assertFalse(report["delivery_eligible"])
        self.assertFalse(report["independence_verified"])
        self.assertIsNone(report["metrics"]["false_positive_rate_over_no_change_opportunities"])

    def test_score_replays_identically_after_moving_exact_inputs_and_relative_reviews(self):
        self._freeze()
        expected = self._score(self._label_payload())
        moved = self.root / "moved-replay"
        moved.mkdir()
        for path in (self.frozen, self.labels, self.support):
            shutil.copyfile(path, moved / path.name)
            path.unlink()
        self.history.unlink()
        self.fixture.db.unlink()
        self.assertEqual(_pretty_bytes(expected), _pretty_bytes(evaluation.evaluate(
            moved / self.frozen.name, moved / self.labels.name)))

    def test_changed_label_bytes_cannot_be_scored_under_another_label_hash(self):
        self._freeze()
        original = self._label_payload()
        changed = copy.deepcopy(original)
        changed["adjudications"][0].update(verdict="false_positive", truth_event_id=None)
        self.labels.write_bytes(_pretty_bytes(original))
        self.clock = SCORE
        label_reads = iter((_pretty_bytes(original), _pretty_bytes(changed), _pretty_bytes(original)))
        read = evaluation._read

        def swap_labels(path):
            return next(label_reads) if path == self.labels else read(path)

        with patch.object(evaluation, "_read", side_effect=swap_labels), self.assertRaisesRegex(ValueError, "changed during scoring"):
            evaluation.evaluate(self.frozen, self.labels)

    def test_cohort_baseline_cannot_postdate_actual_freeze(self):
        self._export()
        baseline = json.loads(BASELINE.read_bytes())
        baseline["recorded_at"] = SCORE
        path = self.root / "future-baseline.json"
        path.write_bytes(_pretty_bytes(baseline))
        with self.assertRaisesRegex(ValueError, "cohort baseline contains future knowledge"):
            evaluation.freeze_predictions(self.history, path, self.frozen,
                study_id="fixture", start=START, end=END)
        self.assertFalse(self.frozen.exists())

    def test_oversized_retained_input_is_rejected_before_blob_encoding(self):
        with patch.object(ledger, "_blob", side_effect=AssertionError("must not encode")):
            with self.assertRaisesRegex(ValueError, "20 MB"):
                evaluation._retained_blob(b"x" * 20_000_001)

    def test_retraction_at_cutoff_is_excluded_from_scored_behavior(self):
        alert = self.fixture._import()["alerts"][0]
        self.fixture._decide(alert, "retract", clock=END)
        self._freeze(import_first=False)
        report = self._score(self._label_payload(verdict="false_positive"))
        self.assertEqual(0, report["metrics"]["retracted_episodes"])
        self.assertIsNone(report["predictions"][0]["first_admission_to_retraction_seconds"])
        self.assertEqual("pending", report["predictions"][0]["status_at_cutoff"])

    def test_known_mature_truth_without_any_prediction_is_a_conditional_miss(self):
        self._freeze(import_first=False)
        report = self._score(self._label_payload(verdict=None, complete=True))
        self.assertEqual(1, report["counts"]["mature_truth_events"])
        self.assertEqual("missed_deadline", report["truth_outcomes"][0]["status"])
        self.assertEqual(0.0, report["metrics"]["timely_event_recall"]["value"])

    def test_supported_but_late_is_not_early_detection(self):
        self._freeze()
        report = self._score(self._label_payload(complete=True, deadline="2026-09-07T09:45:00Z"))
        self.assertEqual(1.0, report["metrics"]["assessed_episode_precision"]["value"])
        self.assertFalse(report["predictions"][0]["timely"])
        self.assertEqual(0.0, report["metrics"]["timely_event_recall"]["value"])
        self.assertEqual("missed_deadline", report["truth_outcomes"][0]["status"])

    def test_incomplete_truth_inventory_suppresses_recall(self):
        self._freeze()
        report = self._score(self._label_payload(complete=False))
        self.assertIsNone(report["metrics"]["timely_event_recall"])
        self.assertEqual(1, report["counts"]["truth_events"])

    def test_partial_or_unresolved_labels_suppress_recall_and_keep_denominator(self):
        self._freeze()
        for verdict, count in ((None, "unlabelled"), ("unresolved", "unresolved")):
            report = self._score(self._label_payload(verdict=verdict, complete=True))
            with self.subTest(verdict=verdict):
                self.assertEqual(1, report["counts"][count])
                self.assertEqual(1, report["counts"]["predictions"])
                self.assertIsNone(report["metrics"]["timely_event_recall"])
                self.assertIsNone(report["metrics"]["assessed_episode_precision"]["value"])
                self.assertEqual(0.0, report["metrics"]["precision_bounds_with_unresolved"]["lower"]["value"])
                self.assertEqual(1.0, report["metrics"]["precision_bounds_with_unresolved"]["upper"]["value"])

    def test_mixed_judgments_preserve_all_four_prediction_outcomes(self):
        self._four_episodes()
        self._freeze(import_first=False)
        payload = self._label_payload(complete=True)
        alerts = json.loads(self.frozen.read_bytes())["predictions"]
        self.assertEqual(4, len(alerts))
        first_id = payload["adjudications"][0]["alert_id"]
        others = [row for row in alerts if row["id"] != first_id]
        for alert, verdict in zip(others, ("false_positive", "unresolved")):
            payload["adjudications"].append({"alert_id": alert["id"], "verdict": verdict,
                "truth_event_id": None, "reason": "Synthetic mixed-outcome fixture.",
                "review_sha256": ledger._hash(self.support.read_bytes())})
        report = self._score(payload)
        self.assertEqual([1, 1, 1, 1], [report["counts"][key] for key in ("supported", "false_positive", "unresolved", "unlabelled")])
        self.assertEqual(0.5, report["metrics"]["assessed_episode_precision"]["value"])
        self.assertEqual(0.25, report["metrics"]["confirmed_false_positive_review_burden"]["value"])
        self.assertEqual(0.75, report["metrics"]["false_positive_review_burden_bounds"]["upper"]["value"])
        self.assertEqual(0.25, report["metrics"]["precision_bounds_with_unresolved"]["lower"]["value"])
        self.assertEqual(0.75, report["metrics"]["precision_bounds_with_unresolved"]["upper"]["value"])
        self.assertIsNone(report["metrics"]["timely_event_recall"])

    def test_duplicate_supported_episodes_do_not_inflate_unique_truth_recall(self):
        self._four_episodes()
        self._freeze(import_first=False)
        payload = self._label_payload(complete=True)
        alerts = json.loads(self.frozen.read_bytes())["predictions"]
        supported_ids = {alert["id"] for alert in alerts
                         if alert["observations"][0]["after"]["value"]["value"] == "site_preparation"}
        digest = ledger._hash(self.support.read_bytes())
        payload["adjudications"] = [{"alert_id": alert["id"],
            "verdict": "supported" if alert["id"] in supported_ids else "false_positive",
            "truth_event_id": "fixture-truth" if alert["id"] in supported_ids else None,
            "reason": "Synthetic duplicate episode accounting fixture.", "review_sha256": digest}
            for alert in alerts]
        report = self._score(payload)
        self.assertEqual(2, report["counts"]["supported"])
        self.assertEqual(1, report["counts"]["unique_supported_truth_events"])
        self.assertEqual(1, report["counts"]["duplicate_supported_matches"])
        self.assertEqual(2, report["truth_outcomes"][0]["matched_alert_count"])
        self.assertEqual({"numerator": 1, "denominator": 1, "value": 1.0}, report["metrics"]["timely_event_recall"])

    def test_prior_window_source_can_explain_backfill_but_is_not_in_recall_denominator(self):
        self._freeze()
        payload = self._label_payload(complete=True)
        payload["truth_events"][0].update(available_at="2026-08-21T00:00:00Z", deadline_at="2026-08-22T00:00:00Z")
        report = self._score(payload)
        self.assertEqual(1, report["counts"]["supported"])
        self.assertEqual(1, report["counts"]["prior_window_truth_events"])
        self.assertEqual(0, report["counts"]["mature_truth_events"])
        self.assertEqual("prior_window_event", report["truth_outcomes"][0]["status"])
        self.assertFalse(report["truth_outcomes"][0]["recall_eligible"])
        self.assertGreater(report["predictions"][0]["source_to_admission_seconds"], 86400)
        self.assertIsNone(report["metrics"]["timely_event_recall"]["value"])

    def test_matched_event_with_deadline_equal_to_end_remains_censored(self):
        self._freeze()
        report = self._score(self._label_payload(complete=True, deadline=END))
        self.assertTrue(report["predictions"][0]["timely"])
        self.assertEqual(1, report["counts"]["right_censored"])
        self.assertEqual(0, report["counts"]["mature_truth_events"])
        self.assertFalse(report["truth_outcomes"][0]["recall_eligible"])
        self.assertEqual("right_censored", report["truth_outcomes"][0]["status"])

    def test_uncertain_matching_is_bounded_and_not_called_a_definite_miss(self):
        self._freeze()
        report = self._score(self._label_payload(verdict="unresolved", complete=True))
        self.assertEqual("unresolved_matching", report["truth_outcomes"][0]["status"])
        self.assertEqual(1, report["truth_outcomes"][0]["possible_timely_match_count"])
        self.assertEqual(0.0, report["metrics"]["timely_event_recall_bounds"]["lower"]["value"])
        self.assertEqual(1.0, report["metrics"]["timely_event_recall_bounds"]["upper"]["value"])

    def test_deadline_equal_to_end_is_right_censored_and_not_a_mature_miss(self):
        self._freeze()
        report = self._score(self._label_payload(verdict="false_positive", complete=True, deadline=END))
        self.assertEqual(0, report["counts"]["mature_truth_events"])
        self.assertEqual(1, report["counts"]["right_censored"])
        self.assertEqual("right_censored", report["truth_outcomes"][0]["status"])
        self.assertIsNone(report["metrics"]["timely_event_recall"]["value"])

    def test_empty_prediction_denominator_yields_null_not_perfect_precision(self):
        self._freeze(import_first=False)
        payload = self._label_payload(verdict=None, complete=True)
        payload["truth_events"] = []
        report = self._score(payload)
        self.assertEqual(0, report["counts"]["predictions"])
        self.assertIsNone(report["metrics"]["assessed_episode_precision"]["value"])
        self.assertIsNone(report["metrics"]["precision_bounds_with_unresolved"]["upper"]["value"])
        self.assertEqual(7, len(report["cohort"]))

    def test_retraction_is_retained_but_does_not_erase_false_positive(self):
        alert = self.fixture._import()["alerts"][0]
        self.fixture._decide(alert, "retract", clock="2026-09-07T10:30:00Z")
        self._freeze(import_first=False)
        report = self._score(self._label_payload(verdict="false_positive", complete=True))
        self.assertEqual(1, report["counts"]["false_positive"])
        self.assertEqual(1, report["metrics"]["retracted_episodes"])
        self.assertEqual(1, report["metrics"]["false_positive_episodes_retracted"])
        self.assertEqual("retracted", report["predictions"][0]["status_at_cutoff"])
        self.assertEqual(1800.0, report["predictions"][0]["first_admission_to_retraction_seconds"])
        self.assertIsNone(report["metrics"]["retraction_latency"])

    def test_label_review_must_be_after_freeze_not_future_and_bound_to_exact_frozen_bytes(self):
        self._freeze()
        original = self._label_payload()
        for field, value in (("reviewed_at", FREEZE), ("reviewed_at", "2026-09-07T15:00:00Z"),
                             ("frozen_sha256", "0" * 64), ("truth_inventory_complete", 1)):
            payload = copy.deepcopy(original)
            payload[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self._score(payload)

    def test_corrupt_supporting_review_is_rejected(self):
        self._freeze()
        payload = self._label_payload()
        self.support.write_bytes(b"changed fixture review")
        with self.assertRaisesRegex(ValueError, "review mismatch"):
            self._score(payload)

    def test_supported_truth_must_match_scope_rule_and_not_postdate_admission(self):
        self._freeze()
        original = self._label_payload()
        for field, value in (("facility_key", "tsmc:fab21-arizona"), ("rule_id", "fixture-not-a-rule"),
                             ("available_at", "2026-09-07T10:15:00Z")):
            payload = copy.deepcopy(original)
            payload["truth_events"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self._score(payload)

    def test_unknown_or_duplicate_judgments_and_unbound_truth_are_rejected(self):
        self._freeze()
        original = self._label_payload()
        mutations = (
            lambda value: value["adjudications"].append(copy.deepcopy(value["adjudications"][0])),
            lambda value: value["adjudications"][0].update(alert_id="unknown"),
            lambda value: value["truth_events"][0].update(review_sha256="0" * 64),
            lambda value: value["adjudications"][0].update(verdict="false_positive"),
        )
        for index, mutate in enumerate(mutations):
            payload = copy.deepcopy(original)
            mutate(payload)
            with self.subTest(index=index), self.assertRaises(ValueError):
                self._score(payload)

    def test_malformed_label_shapes_fail_with_value_error_not_type_error(self):
        self._freeze()
        original = self._label_payload()
        mutations = (
            lambda value: value.update(blinding=[]),
            lambda value: value.update(truth_events=None),
            lambda value: value.update(adjudications=[None]),
            lambda value: value["supporting_reviews"][0].update(path=[]),
            lambda value: value["supporting_reviews"][0].update(sha256=[]),
            lambda value: value["truth_events"][0].update(facility_key=[]),
            lambda value: value["truth_events"][0].update(rule_id=[]),
            lambda value: value["truth_events"][0].update(review_sha256=[]),
            lambda value: value["adjudications"][0].update(alert_id=[]),
            lambda value: value["adjudications"][0].update(verdict=[]),
            lambda value: value["adjudications"][0].update(truth_event_id=[]),
        )
        for index, mutate in enumerate(mutations):
            payload = copy.deepcopy(original)
            mutate(payload)
            with self.subTest(index=index), self.assertRaises(ValueError):
                self._score(payload)


if __name__ == "__main__":
    unittest.main()
