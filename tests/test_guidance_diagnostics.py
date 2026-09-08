"""Synthetic interval arithmetic, never an outcome-evidence attestation."""

import copy
import unittest

from semiconductor_atlas.guidance_diagnostics import evaluate_case


PREDICTION = {"low": "2027-07-01", "base": None, "high": "2027-12-31"}
VINTAGE = "2026-09-08"
HORIZON = "2028-12-31"


class GuidanceDiagnosticsTests(unittest.TestCase):
    def evaluate(self, outcome, prediction=PREDICTION, *, vintage=VINTAGE, horizon=HORIZON):
        return evaluate_case(prediction, outcome, vintage, horizon)

    def test_containment_retains_null_midpoint_and_has_no_point_or_probability_metric(self):
        outcome = {"status": "observed", "low": "2027-10-01", "high": "2027-10-31"}
        before = copy.deepcopy((PREDICTION, outcome))
        self.assertEqual({"classification": "contained", "prediction_issued": True, "outcome_status": "observed",
            "temporal_classification": "within_horizon", "guaranteed_containment": True,
            "possible_overlap": True, "window_miss": False}, self.evaluate(outcome))
        self.assertEqual(before, (PREDICTION, outcome))

    def test_inclusive_prediction_endpoints_and_equal_intervals_are_contained(self):
        for low, high in (("2027-07-01", "2027-07-01"), ("2027-12-31", "2027-12-31"),
                          ("2027-07-01", "2027-12-31")):
            with self.subTest(low=low, high=high):
                result = self.evaluate({"status": "observed", "low": low, "high": high})
                self.assertEqual("contained", result["classification"])
                self.assertFalse(result["window_miss"])

    def test_partial_overlap_and_superset_do_not_become_guaranteed_hits(self):
        for low, high in (("2027-06-01", "2027-08-31"), ("2027-11-01", "2028-01-31"),
                          ("2027-01-01", "2028-01-01"), ("2027-06-01", "2027-07-01"),
                          ("2027-12-31", "2028-01-31")):
            with self.subTest(low=low, high=high):
                result = self.evaluate({"status": "observed", "low": low, "high": high})
                self.assertEqual("overlapping", result["classification"])
                self.assertFalse(result["guaranteed_containment"])
                self.assertTrue(result["possible_overlap"])
                self.assertIsNone(result["window_miss"])

    def test_disjoint_observed_intervals_are_early_or_late_misses(self):
        for classification, low, high in (("missed_early", "2027-01-01", "2027-06-30"),
                                           ("missed_late", "2028-01-01", "2028-12-31")):
            with self.subTest(classification=classification):
                result = self.evaluate({"status": "observed", "low": low, "high": high})
                self.assertEqual(classification, result["classification"])
                self.assertTrue(result["window_miss"])
                self.assertFalse(result["guaranteed_containment"])
                self.assertFalse(result["possible_overlap"])

    def test_pre_vintage_and_same_day_outcomes_never_receive_future_timing_scores(self):
        for low, high in (("2024-01-01", "2024-12-31"), (VINTAGE, VINTAGE), ("2026-01-01", VINTAGE)):
            with self.subTest(low=low, high=high):
                result = self.evaluate({"status": "observed", "low": low, "high": high})
                self.assertEqual("pre_vintage", result["classification"])
                self.assertIsNone(result["guaranteed_containment"])
                self.assertIsNone(result["possible_overlap"])
                self.assertIsNone(result["window_miss"])

    def test_vintage_straddling_outcomes_are_retained_not_truncated_to_future(self):
        for low, high in (("2026-01-01", "2026-12-31"), (VINTAGE, "2027-08-01"),
                          ("2026-01-01", "2029-12-31")):
            with self.subTest(low=low, high=high):
                result = self.evaluate({"status": "observed", "low": low, "high": high})
                self.assertEqual("vintage_straddling", result["classification"])
                self.assertIsNone(result["window_miss"])

    def test_after_horizon_and_horizon_straddling_outcomes_remain_distinct(self):
        for classification, low, high in (("after_horizon", "2029-01-01", "2029-12-31"),
            ("horizon_straddling", "2028-01-01", "2029-01-01"),
            ("horizon_straddling", HORIZON, "2029-01-01")):
            with self.subTest(classification=classification, low=low):
                result = self.evaluate({"status": "observed", "low": low, "high": high})
                self.assertEqual(classification, result["classification"])
                self.assertEqual(classification, result["temporal_classification"])
                self.assertIsNone(result["window_miss"])

    def test_explicit_nonoccurrence_through_window_end_proves_miss_without_event_date(self):
        for through in ("2027-12-31", "2028-03-01", "2029-03-01"):
            with self.subTest(through=through):
                result = self.evaluate({"status": "right_censored", "through": through})
                self.assertEqual("censored_window_miss", result["classification"])
                self.assertEqual("right_censored", result["outcome_status"])
                self.assertTrue(result["window_miss"])
                self.assertIsNone(result["guaranteed_containment"])
                self.assertIsNone(result["possible_overlap"])
                self.assertEqual("after_horizon" if through > HORIZON else "within_horizon",
                                 result["temporal_classification"])

    def test_partial_censoring_does_not_prove_interval_hit_or_miss(self):
        for through in ("2026-09-09", "2027-06-30", "2027-12-30"):
            with self.subTest(through=through):
                result = self.evaluate({"status": "right_censored", "through": through})
                self.assertEqual("right_censored", result["classification"])
                self.assertIsNone(result["window_miss"])

    def test_censoring_on_or_before_vintage_is_explicitly_pre_vintage(self):
        for through in ("2024-12-31", VINTAGE):
            with self.subTest(through=through):
                result = self.evaluate({"status": "right_censored", "through": through})
                self.assertEqual("pre_vintage", result["classification"])
                self.assertEqual("right_censored", result["outcome_status"])
                self.assertIsNone(result["window_miss"])

    def test_missing_reports_remain_unknown_not_censored(self):
        result = self.evaluate({"status": "unknown"})
        self.assertEqual("unknown", result["classification"])
        self.assertIsNone(result["temporal_classification"])
        self.assertIsNone(result["window_miss"])

    def test_cancellation_is_separate_from_attainment_and_timing_misses(self):
        result = self.evaluate({"status": "cancelled", "low": "2027-10-01", "high": "2027-10-31"})
        self.assertEqual("cancelled", result["classification"])
        self.assertIsNone(result["window_miss"])
        self.assertIsNone(result["guaranteed_containment"])
        for classification, low, high in (("pre_vintage", "2025-01-01", "2025-12-31"),
            ("vintage_straddling", "2026-01-01", "2026-12-31"),
            ("horizon_straddling", "2028-01-01", "2029-12-31"),
            ("after_horizon", "2029-01-01", "2029-12-31")):
            with self.subTest(classification=classification):
                result = self.evaluate({"status": "cancelled", "low": low, "high": high})
                self.assertEqual(classification, result["classification"])
                self.assertEqual("cancelled", result["outcome_status"])
                self.assertIsNone(result["window_miss"])

    def test_abstention_keeps_every_outcome_status_and_its_temporal_scope(self):
        outcomes = [{"status": "unknown"}, {"status": "observed", "low": "2027-10-01", "high": "2027-10-31"},
            {"status": "observed", "low": "2024-01-01", "high": "2024-12-31"},
            {"status": "right_censored", "through": "2029-01-01"},
            {"status": "cancelled", "low": "2027-01-01", "high": "2027-12-31"}]
        results = [self.evaluate(outcome, None) for outcome in outcomes]
        self.assertEqual(len(outcomes), len(results))
        for outcome, result in zip(outcomes, results, strict=True):
            with self.subTest(outcome=outcome):
                self.assertEqual("abstained", result["classification"])
                self.assertEqual(outcome["status"], result["outcome_status"])
                self.assertFalse(result["prediction_issued"])
                self.assertIsNone(result["window_miss"])
        self.assertEqual([None, "within_horizon", "pre_vintage", "after_horizon", "within_horizon"],
            [result["temporal_classification"] for result in results])

    def test_prediction_requires_exact_shape_null_base_and_strictly_future_ordered_bounds(self):
        variants = [False, [], {}, {"low": "2027-07-01", "high": "2027-12-31"},
            {**PREDICTION, "base": "2027-10-01"}, {**PREDICTION, "base": False},
            {**PREDICTION, "literal": "second half of 2027"}, {**PREDICTION, "low": VINTAGE},
            {**PREDICTION, "low": "2028-01-01"}, {**PREDICTION, "high": "2029-01-01"}]
        for prediction in variants:
            with self.subTest(prediction=prediction), self.assertRaises(ValueError):
                self.evaluate({"status": "unknown"}, prediction)

    def test_invalid_or_noncanonical_dates_and_invalid_horizon_are_rejected(self):
        invalid = [None, False, 20270101, "20270101", "2027-7-01", "2027-02-29", "0000-01-01",
                   "2027-07-01T00:00:00Z", " 2027-07-01", "2027-07-01\n"]
        for value in invalid:
            with self.subTest(field="prediction", value=value), self.assertRaises(ValueError):
                self.evaluate({"status": "unknown"}, {**PREDICTION, "low": value})
            with self.subTest(field="outcome", value=value), self.assertRaises(ValueError):
                self.evaluate({"status": "right_censored", "through": value})
            with self.subTest(field="vintage", value=value), self.assertRaises(ValueError):
                self.evaluate({"status": "unknown"}, vintage=value)
            with self.subTest(field="horizon", value=value), self.assertRaises(ValueError):
                self.evaluate({"status": "unknown"}, horizon=value)
        for horizon in (VINTAGE, "2025-01-01"):
            with self.subTest(horizon=horizon), self.assertRaisesRegex(ValueError, "horizon_end must follow"):
                self.evaluate({"status": "unknown"}, None, horizon=horizon)

    def test_strict_outcome_shapes_cannot_mix_status_date_semantics(self):
        variants = [None, [], {}, {"status": False}, {"status": "missing"}, {"status": "observed"},
            {"status": "cancelled", "through": "2027-01-01"},
            {"status": "unknown", "low": None, "high": None},
            {"status": "unknown", "through": "2027-01-01"},
            {"status": "right_censored", "through": "2027-12-31", "low": "2027-01-01"},
            {"status": "observed", "low": "2028-01-01", "high": "2027-01-01"},
            {"status": "cancelled", "low": "2028-01-01", "high": "2027-01-01"},
            {"status": "observed", "low": "2027-10-01", "high": "2027-10-31", "base": None}]
        for outcome in variants:
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                self.evaluate(outcome)
            with self.subTest(abstained_outcome=outcome), self.assertRaises(ValueError):
                self.evaluate(outcome, None)

    def test_valid_leap_day_and_exact_horizon_day_are_supported(self):
        prediction = {"low": "2028-02-29", "base": None, "high": HORIZON}
        for day in ("2028-02-29", HORIZON):
            with self.subTest(day=day):
                result = self.evaluate({"status": "observed", "low": day, "high": day}, prediction)
                self.assertEqual("contained", result["classification"])


if __name__ == "__main__":
    unittest.main()
