"""Synthetic source-statement arithmetic; these fixtures authenticate no evidence."""

import copy
import unittest

from semiconductor_atlas.source_guidance_outcomes import BOUNDARIES, evaluate_case, score


INTERVAL = {"kind": "interval", "low": "2021-10-01", "high": "2021-12-31"}
DEADLINE = {"kind": "deadline", "through": "2021-12-31"}


def observed(low, high=None):
    return {"status": "observed", "low": low, "high": high or low}


def observed_by(through):
    return {"status": "observed_by", "through": through}


def fixture(case_id="a", *, disposition="eligible", target=DEADLINE,
            outcome=None, project="project-a", group="group-a"):
    identity = {"case_id": case_id, "project_id": project, "project_group": group,
                "event_type": "production_start", "scope_id": "phase-1"}
    case = {**identity, "disposition": disposition, "reason": "Synthetic reviewed screening decision.",
            "target": copy.deepcopy(target), "source_reference_ids": ["guidance-span-a"],
            "source_document_date": "2021-03-01"}
    outcome = copy.deepcopy(outcome if outcome is not None else {"status": "unknown"})
    review = {**identity, "reason": "Synthetic exposed outcome review, not independently verified.",
              "outcome": outcome, "source_reference_ids": [] if outcome["status"] == "unknown" else ["outcome-span-a"],
              "source_document_date": "2022-01-15"}
    return case, review


class SourceTargetArithmeticTests(unittest.TestCase):
    def test_early_attainment_meets_deadline_but_misses_interval(self):
        outcome = observed("2021-08-01", "2021-08-31")
        deadline = evaluate_case(DEADLINE, outcome)
        interval = evaluate_case(INTERVAL, outcome)
        self.assertEqual("deadline_met", deadline["classification"])
        self.assertIs(deadline["target_met"], True)
        self.assertEqual("interval_missed_early", interval["classification"])
        self.assertIs(interval["target_met"], False)
        self.assertNotIn("low", DEADLINE)

    def test_deadline_endpoint_inclusive_late_and_straddling_distinct(self):
        cases = [(observed("2021-12-31"), "deadline_met", True),
                 (observed("2022-01-01"), "deadline_missed_late", False),
                 (observed("2021-12-01", "2022-01-01"), "deadline_straddling", None),
                 (observed("2021-12-31", "2022-01-01"), "deadline_straddling", None)]
        for outcome, classification, result in cases:
            with self.subTest(outcome=outcome):
                actual = evaluate_case(DEADLINE, outcome)
                self.assertEqual(classification, actual["classification"])
                self.assertIs(actual["target_met"], result)

    def test_interval_inclusive_endpoints_and_equal_bounds(self):
        for outcome in (observed("2021-10-01"), observed("2021-12-31"),
                        observed("2021-10-01", "2021-12-31")):
            with self.subTest(outcome=outcome):
                actual = evaluate_case(INTERVAL, outcome)
                self.assertEqual("interval_contained", actual["classification"])
                self.assertIs(actual["target_met"], True)

    def test_overlap_and_superset_never_become_success(self):
        for outcome in (observed("2021-09-01", "2021-10-01"),
                        observed("2021-12-31", "2022-01-01"),
                        observed("2021-01-01", "2022-12-31")):
            with self.subTest(outcome=outcome):
                actual = evaluate_case(INTERVAL, outcome)
                self.assertEqual("interval_overlapping", actual["classification"])
                self.assertIsNone(actual["target_met"])
                self.assertIs(actual["possible_satisfaction"], True)

    def test_late_interval_is_distinct_from_early_interval(self):
        actual = evaluate_case(INTERVAL, observed("2022-01-01"))
        self.assertEqual("interval_missed_late", actual["classification"])
        self.assertIs(actual["target_met"], False)

    def test_observed_by_deadline_upper_bound_is_inclusive_without_a_false_late_miss(self):
        for through, classification, met in (
                ("2021-01-01", "deadline_met", True),
                ("2021-12-31", "deadline_met", True),
                ("2022-01-01", "deadline_upper_bound_unresolved", None),
                ("2099-12-31", "deadline_upper_bound_unresolved", None)):
            with self.subTest(through=through):
                actual = evaluate_case(DEADLINE, observed_by(through))
                self.assertEqual({"target_kind": "deadline", "outcome_status": "observed_by",
                    "classification": classification, "target_met": met,
                    "possible_satisfaction": True}, actual)

    def test_observed_by_interval_never_invents_containment_or_a_lower_bound(self):
        for through, classification, met, possible in (
                ("2021-09-30", "interval_missed_early", False, False),
                ("2021-10-01", "interval_upper_bound_unresolved", None, True),
                ("2021-11-15", "interval_upper_bound_unresolved", None, True),
                ("2021-12-31", "interval_upper_bound_unresolved", None, True),
                ("2022-01-01", "interval_upper_bound_unresolved", None, True),
                ("2099-12-31", "interval_upper_bound_unresolved", None, True)):
            with self.subTest(through=through):
                actual = evaluate_case(INTERVAL, observed_by(through))
                self.assertEqual({"target_kind": "interval", "outcome_status": "observed_by",
                    "classification": classification, "target_met": met,
                    "possible_satisfaction": possible}, actual)

    def test_observed_by_is_not_an_exact_occurrence_on_its_upper_bound(self):
        for target, through in ((INTERVAL, "2021-11-15"), (DEADLINE, "2022-01-01")):
            with self.subTest(target=target):
                exact = evaluate_case(target, observed(through))
                bounded = evaluate_case(target, observed_by(through))
                self.assertIsNotNone(exact["target_met"])
                self.assertIsNone(bounded["target_met"])
                self.assertNotEqual(exact["classification"], bounded["classification"])

    def test_explicit_nonoccurrence_requires_through_upper_bound_for_miss(self):
        for target in (DEADLINE, INTERVAL):
            for through, missed in (("2021-01-01", False), ("2021-12-30", False),
                                    ("2021-12-31", True), ("2023-01-01", True)):
                with self.subTest(target=target, through=through):
                    actual = evaluate_case(target, {"status": "explicit_nonoccurrence_through", "through": through})
                    self.assertIs(actual["target_met"], False if missed else None)
                    self.assertEqual(f"censored_{target['kind']}_miss" if missed else "explicit_nonoccurrence_through",
                                     actual["classification"])

    def test_unknown_and_cancellation_never_become_nonoccurrence_or_misses(self):
        for target in (DEADLINE, INTERVAL):
            for status in ("unknown", "cancelled"):
                with self.subTest(target=target, status=status):
                    actual = evaluate_case(target, {"status": status})
                    self.assertEqual(status, actual["classification"])
                    self.assertIsNone(actual["target_met"])
                    self.assertIsNone(actual["possible_satisfaction"])

    def test_no_target_preserves_every_outcome_status(self):
        outcomes = [observed("2021-10-01"), observed_by("2021-10-01"),
                    {"status": "cancelled"}, {"status": "unknown"},
                    {"status": "explicit_nonoccurrence_through", "through": "2022-01-01"}]
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                actual = evaluate_case(None, outcome)
                self.assertEqual("no_target", actual["classification"])
                self.assertEqual(outcome["status"], actual["outcome_status"])
                self.assertIsNone(actual["target_met"])
                self.assertIsNone(actual["possible_satisfaction"])

    def test_exact_valid_days_include_leap_day(self):
        target = {"kind": "deadline", "through": "2024-02-29"}
        self.assertIs(evaluate_case(target, observed("2024-02-29"))["target_met"], True)
        self.assertIs(evaluate_case(target, observed_by("2024-02-29"))["target_met"], True)

    def test_dates_reject_invalid_noncanonical_and_non_string_inputs(self):
        invalid = [None, False, 20211231, "20211231", "2021-2-01", "2021-02-29", "0000-01-01",
                   "2021-12-31T00:00:00Z", " 2021-12-31", "2021-12-31\n"]
        for value in invalid:
            with self.subTest(target=value), self.assertRaises(ValueError):
                evaluate_case({"kind": "deadline", "through": value}, {"status": "unknown"})
            with self.subTest(outcome=value), self.assertRaises(ValueError):
                evaluate_case(DEADLINE, {"status": "explicit_nonoccurrence_through", "through": value})
            with self.subTest(observed_by=value), self.assertRaises(ValueError):
                evaluate_case(DEADLINE, observed_by(value))

    def test_observed_by_strict_shape_rejects_imputed_bounds_and_extra_fields_even_without_target(self):
        outcomes = [{"status": "observed_by"},
                    {"status": "observed_by", "high": "2021-12-31"},
                    {"status": "observed_by", "low": "2021-01-01", "high": "2021-12-31"},
                    *({**observed_by("2021-12-31"), key: value} for key, value in (
                        ("low", None), ("high", "2021-12-31"), ("base", None),
                        ("probability", 1), ("date", "2021-12-31"), ("precision", "day")))]
        for target in (None, DEADLINE, INTERVAL):
            for outcome in outcomes:
                with self.subTest(target=target, outcome=outcome), self.assertRaises(ValueError):
                    evaluate_case(target, outcome)

    def test_strict_tagged_shapes_reject_midpoints_probabilities_and_deadline_lower_bound(self):
        targets = [False, [], {}, {"kind": []}, {"kind": "before", "through": "2021-12-31"},
                   {**DEADLINE, "low": "2021-01-01"}, {**DEADLINE, "base": None},
                   {**INTERVAL, "base": "2021-11-15"}, {**INTERVAL, "probability": 0.9},
                   {**INTERVAL, "low": "2022-01-01"}]
        for target in targets:
            with self.subTest(target=target), self.assertRaises(ValueError):
                evaluate_case(target, {"status": "unknown"})
        outcomes = [None, [], {}, {"status": []}, {"status": "right_censored", "through": "2021-12-31"},
                    {"status": "unknown", "through": "2021-12-31"},
                    {"status": "cancelled", "low": "2021-01-01", "high": "2021-12-31"},
                    observed("2022-01-01", "2021-01-01"), {**observed("2021-10-01"), "base": None}]
        for outcome in outcomes:
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                evaluate_case(DEADLINE, outcome)


class SourceGuidanceRosterTests(unittest.TestCase):
    def test_observed_by_retains_all_cases_but_scores_only_established_boolean_results(self):
        pairs = [fixture("a", outcome=observed_by("2021-12-31")),
                 fixture("b", outcome=observed_by("2022-01-01")),
                 fixture("c", target=INTERVAL, outcome=observed_by("2021-09-30")),
                 fixture("d", target=INTERVAL, outcome=observed_by("2021-11-15")),
                 fixture("e", disposition="unresolved", target=None, outcome=observed_by("2021-12-31")),
                 fixture("f", disposition="excluded", outcome=observed_by("2021-12-31")),
                 fixture("g")]
        report = score([pair[0] for pair in pairs], [pair[1] for pair in pairs])
        self.assertEqual("semiconductor-atlas-exposed-source-guidance-outcomes-v2", report["format"])
        self.assertEqual({"screened_cases": 7, "eligible_cases": 5, "unresolved_cases": 1,
            "excluded_cases": 1, "scored_cases": 2, "eligible_result_unresolved_cases": 3,
            "scored_interval_cases": 1, "scored_deadline_cases": 1,
            "screened_projects": 1, "screened_project_groups": 1,
            "eligible_projects": 1, "eligible_project_groups": 1,
            "scored_projects": 1, "scored_project_groups": 1}, report["denominators"])
        self.assertEqual({"observed_by": 6, "unknown": 1}, report["screened_outcome_status_counts"])
        self.assertEqual({"observed_by": 4, "unknown": 1}, report["eligible_outcome_status_counts"])
        self.assertEqual({"observed_by": 2}, report["scored_outcome_status_counts"])
        for row in report["cases"][4:6]:
            self.assertEqual("screened_" + row["case"]["disposition"], row["classification"])
            self.assertIsNone(row["target_met"])
            self.assertIsNone(row["possible_satisfaction"])
        self.assertEqual(BOUNDARIES, report["boundaries"])

    def test_v1_format_remains_for_every_legacy_status_and_v2_for_any_observed_by_case(self):
        legacy = [observed("2021-12-31"), {"status": "unknown"}, {"status": "cancelled"},
                  {"status": "explicit_nonoccurrence_through", "through": "2021-12-31"}]
        for disposition in ("eligible", "unresolved", "excluded"):
            for outcome in [*legacy, observed_by("2021-12-31")]:
                with self.subTest(disposition=disposition, outcome=outcome):
                    case, review = fixture(disposition=disposition, outcome=outcome)
                    report = score([case], [review])
                    version = "v2" if outcome["status"] == "observed_by" else "v1"
                    self.assertEqual("semiconductor-atlas-exposed-source-guidance-outcomes-" + version,
                                     report["format"])

    def test_legacy_v1_report_retains_existing_complete_shape_and_semantics(self):
        case, review = fixture(outcome=observed("2021-09-01"))
        self.assertEqual({
            "format": "semiconductor-atlas-exposed-source-guidance-outcomes-v1",
            "design": "exposed_retrospective_source_statement_comparison",
            "boundaries": dict(BOUNDARIES),
            "denominators": {"screened_cases": 1, "eligible_cases": 1, "unresolved_cases": 0,
                "excluded_cases": 0, "scored_cases": 1, "eligible_result_unresolved_cases": 0,
                "scored_interval_cases": 0, "scored_deadline_cases": 1,
                "screened_projects": 1, "screened_project_groups": 1,
                "eligible_projects": 1, "eligible_project_groups": 1,
                "scored_projects": 1, "scored_project_groups": 1},
            "cases": [{"case": case, "review": review,
                "source_date_context": {"source_document_order": "guidance_before_outcome_document",
                    "outcome_relative_to_guidance_document": "after_reported_document_day",
                    "outcome_relative_to_outcome_document": "before_reported_document_day"},
                "target_kind": "deadline", "outcome_status": "observed", "classification": "deadline_met",
                "target_met": True, "possible_satisfaction": True}],
            "classification_counts": {"deadline_met": 1},
            "screened_outcome_status_counts": {"observed": 1},
            "eligible_outcome_status_counts": {"observed": 1},
            "scored_outcome_status_counts": {"observed": 1}}, score([case], [review]))

    def test_every_screened_case_status_project_and_group_stays_counted(self):
        pairs = [fixture("a", outcome=observed("2021-08-01")),
                 fixture("b", target=INTERVAL, outcome=observed("2021-08-01")),
                 fixture("c", outcome=observed("2021-12-01", "2022-01-01"), project="project-b"),
                 fixture("d", outcome={"status": "cancelled"}, project="project-c", group="group-b"),
                 fixture("e", project="project-d", group="group-b"),
                 fixture("f", outcome={"status": "explicit_nonoccurrence_through", "through": "2021-12-31"}),
                 fixture("g", disposition="unresolved", target=None, outcome=observed("2021-09-01")),
                 fixture("h", disposition="excluded", outcome=observed("2021-09-01"), project="project-e", group="group-c")]
        report = score([pair[0] for pair in pairs], [pair[1] for pair in pairs])
        counts = report["denominators"]
        self.assertEqual({"screened_cases": 8, "eligible_cases": 6, "unresolved_cases": 1,
            "excluded_cases": 1, "scored_cases": 3, "eligible_result_unresolved_cases": 3,
            "scored_interval_cases": 1, "scored_deadline_cases": 2,
            "screened_projects": 5, "screened_project_groups": 3,
            "eligible_projects": 4, "eligible_project_groups": 2,
            "scored_projects": 1, "scored_project_groups": 1}, counts)
        self.assertEqual(8, len(report["cases"]))
        self.assertEqual(8, sum(report["screened_outcome_status_counts"].values()))
        self.assertEqual(6, sum(report["eligible_outcome_status_counts"].values()))
        self.assertEqual(3, sum(report["scored_outcome_status_counts"].values()))
        for row in report["cases"][-2:]:
            self.assertTrue(row["classification"].startswith("screened_"))
            self.assertEqual("observed", row["outcome_status"])
            self.assertIsNone(row["target_met"])

    def test_all_unknowns_and_all_exclusions_return_zero_scores_not_a_rate(self):
        for disposition in ("eligible", "unresolved", "excluded"):
            case, review = fixture(disposition=disposition)
            with self.subTest(disposition=disposition):
                report = score([case], [review])
                self.assertEqual(1, report["denominators"]["screened_cases"])
                self.assertEqual(0, report["denominators"]["scored_cases"])
                self.assertNotIn("success_rate", report)

    def test_dropped_extra_duplicate_and_reordered_outcomes_rejected(self):
        first, second = fixture("a"), fixture("b")
        roster = [first[0], second[0]]
        variants = [[first[1]], [first[1], second[1], second[1]],
                    [first[1], first[1]], [second[1], first[1]]]
        for reviews in variants:
            with self.subTest(reviews=reviews), self.assertRaises(ValueError):
                score(roster, reviews)

    def test_duplicate_unsorted_empty_and_oversized_rosters_rejected(self):
        first, second = fixture("a"), fixture("b")
        for roster, reviews in (([], []), ([first[0], first[0]], [first[1], first[1]]),
                                ([second[0], first[0]], [second[1], first[1]]),
                                ([first[0]] * 1_001, [first[1]] * 1_001)):
            with self.subTest(length=len(roster)), self.assertRaises(ValueError):
                score(roster, reviews)

    def test_exact_project_group_event_scope_and_case_identity_required(self):
        for field in ("case_id", "project_id", "project_group", "event_type", "scope_id"):
            case, review = fixture()
            review[field] = "different"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "exact screened"):
                score([case], [review])

    def test_missing_fields_extra_fields_empty_ids_and_reasons_rejected(self):
        for side in (0, 1):
            for field in ("source_document_date", "source_reference_ids", "reason", "scope_id"):
                pair = list(fixture())
                del pair[side][field]
                with self.subTest(side=side, missing=field), self.assertRaises(ValueError):
                    score([pair[0]], [pair[1]])
            for field in ("reason", "case_id"):
                pair = list(fixture()); pair[side][field] = " "
                with self.subTest(side=side, blank=field), self.assertRaises(ValueError):
                    score([pair[0]], [pair[1]])
            pair = list(fixture()); pair[side]["vintage_day"] = "2021-01-01"
            with self.subTest(side=side, extra=True), self.assertRaises(ValueError):
                score([pair[0]], [pair[1]])

    def test_eligible_requires_target_and_disposition_is_strict(self):
        case, review = fixture(target=None)
        with self.assertRaisesRegex(ValueError, "eligible cases require"):
            score([case], [review])
        for disposition in (None, [], False, "accepted"):
            case, review = fixture(disposition=disposition)
            with self.subTest(disposition=disposition), self.assertRaises(ValueError):
                score([case], [review])

    def test_resolved_outcomes_and_all_roster_cases_require_source_references(self):
        for status in ("observed", "observed_by", "cancelled", "explicit_nonoccurrence_through"):
            outcome = observed("2021-09-01") if status == "observed" else {"status": status}
            if status in {"observed_by", "explicit_nonoccurrence_through"}:
                outcome["through"] = "2022-01-01"
            case, review = fixture(outcome=outcome); review["source_reference_ids"] = []
            with self.subTest(status=status), self.assertRaises(ValueError):
                score([case], [review])
        case, review = fixture(); case["source_reference_ids"] = []
        with self.assertRaises(ValueError):
            score([case], [review])

    def test_reference_ids_are_bounded_unique_sorted_strings(self):
        for refs in (None, "ref", [False], [""], ["b", "a"], ["a", "a"], ["a"] * 101):
            case, review = fixture(); case["source_reference_ids"] = refs
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                score([case], [review])

    def test_publication_dates_are_context_not_backdated_issue_clocks_or_availability_proof(self):
        case, review = fixture(outcome=observed("2021-02-01"))
        review["source_document_date"] = "2021-02-15"
        report = score([case], [review])
        row = report["cases"][0]
        self.assertEqual("outcome_document_before_guidance", row["source_date_context"]["source_document_order"])
        self.assertEqual("before_reported_document_day", row["source_date_context"]["outcome_relative_to_guidance_document"])
        self.assertIs(row["target_met"], True)
        self.assertEqual(BOUNDARIES, report["boundaries"])
        self.assertTrue(all(value is False for value in report["boundaries"].values()))
        self.assertNotIn("vintage", report)
        self.assertNotIn("prediction", row)

    def test_unknown_same_day_and_straddling_source_date_contexts_retained(self):
        case, review = fixture(outcome=observed("2021-02-01", "2021-04-01"))
        review["source_document_date"] = case["source_document_date"]
        context = score([case], [review])["cases"][0]["source_date_context"]
        self.assertEqual("same_reported_document_day", context["source_document_order"])
        self.assertEqual("spans_reported_document_day", context["outcome_relative_to_guidance_document"])
        case["source_document_date"] = None; review["source_document_date"] = None
        self.assertEqual({"source_document_order": "unknown", "outcome_relative_to_guidance_document": "unknown",
            "outcome_relative_to_outcome_document": "unknown"}, score([case], [review])["cases"][0]["source_date_context"])
        case["source_document_date"] = "2021-03-01"; review["outcome"] = observed("2021-03-01")
        self.assertEqual("on_reported_document_day", score([case], [review])["cases"][0]["source_date_context"]["outcome_relative_to_guidance_document"])

    def test_observed_by_document_context_does_not_imply_an_exact_start_or_future_occurrence(self):
        for document_day, relation in (("2021-02-28", "unknown"),
                                       ("2021-03-01", "on_or_before_reported_document_day"),
                                       ("2021-03-02", "before_reported_document_day"),
                                       (None, "unknown")):
            case, review = fixture(outcome=observed_by("2021-03-01"))
            case["source_document_date"] = document_day
            review["source_document_date"] = document_day
            with self.subTest(document_day=document_day):
                context = score([case], [review])["cases"][0]["source_date_context"]
                self.assertEqual("unknown" if document_day is None else "same_reported_document_day",
                                 context["source_document_order"])
                self.assertEqual(relation, context["outcome_relative_to_guidance_document"])
                self.assertEqual(relation, context["outcome_relative_to_outcome_document"])

    def test_observed_by_keeps_distinct_document_clocks_without_claiming_historical_availability(self):
        case, review = fixture(outcome=observed_by("2021-03-01"))
        review["source_document_date"] = "2021-02-28"
        report = score([case], [review])
        self.assertEqual({"source_document_order": "outcome_document_before_guidance",
            "outcome_relative_to_guidance_document": "on_or_before_reported_document_day",
            "outcome_relative_to_outcome_document": "unknown"}, report["cases"][0]["source_date_context"])
        self.assertTrue(all(value is False for value in report["boundaries"].values()))
        self.assertNotIn("vintage", report)

    def test_observed_by_no_target_and_one_sided_shape_survive_without_alias_mutation(self):
        case, review = fixture(disposition="unresolved", target=None, outcome=observed_by("2021-03-01"))
        before = copy.deepcopy(([case], [review]))
        report = score([case], [review])
        row = report["cases"][0]
        self.assertEqual("screened_unresolved", row["classification"])
        self.assertIsNone(row["target_kind"])
        self.assertIsNone(row["target_met"])
        self.assertEqual({"status": "observed_by", "through": "2021-03-01"}, row["review"]["outcome"])
        self.assertEqual(before, ([case], [review]))
        row["review"]["outcome"]["through"] = "2099-12-31"
        self.assertEqual(before, ([case], [review]))

    def test_source_dates_must_retain_exact_day_precision_or_null(self):
        for side in (0, 1):
            for invalid in ("2021", "2021-03", "2021-02-29", "2021-03-01T00:00:00Z", False):
                pair = list(fixture()); pair[side]["source_document_date"] = invalid
                with self.subTest(side=side, invalid=invalid), self.assertRaises(ValueError):
                    score([pair[0]], [pair[1]])

    def test_deterministic_output_and_no_input_or_output_alias_mutation(self):
        case, review = fixture(outcome=observed("2021-09-01"))
        before = copy.deepcopy(([case], [review]))
        report = score([case], [review])
        self.assertEqual(report, score([case], [review]))
        self.assertEqual(before, ([case], [review]))
        report["cases"][0]["case"]["target"]["through"] = "2099-12-31"
        report["cases"][0]["review"]["source_reference_ids"].append("new-ref")
        report["boundaries"]["evidence_verified"] = True
        self.assertEqual(before, ([case], [review]))
        self.assertIs(BOUNDARIES["evidence_verified"], False)


if __name__ == "__main__":
    unittest.main()
