"""Assessment recency is neither source freshness nor continuous monitoring."""

import copy
import json
import unittest

from scripts import prospective_monitoring_status as status
from tests import test_prospective_monitoring_status as helpers
from tests import test_prospective_target_review as fixtures


START = "2026-09-07T04:00:00Z"
CUTOFF = "2026-09-07T08:00:00Z"


def completed(case_id, *, document=0, assessment="2026-09-07T05:00:00Z",
              attempt="2026-09-07T04:59:50Z", outcome="succeeded", comparable=True,
              collection_status="unchanged", protocol=True):
    row = helpers.example_row(document, comparable=comparable, protocol=protocol)
    row.update(case_id=case_id, opportunity_id=f"opportunity-{case_id}")
    row["case"].update(capture_path=f"captures/{case_id}", assessment_at=assessment,
        attempted=attempt is not None, response_finished_at=attempt,
        transport_outcome=outcome, status=collection_status)
    return row


def incomplete(case_id, *, document=0, intent="2026-09-07T07:00:00Z"):
    row = helpers.example_row(document, result="abstain", comparable=False)
    row.update(case_id=case_id, opportunity_id=f"opportunity-{case_id}")
    row["case"] = {"url": f"https://example.org/{document}", "document_id": str(document),
        "capture_path": f"captures/{case_id}", "kind": "incomplete_document",
        "intent_started_at": intent, "comparison_eligible": False,
        "status": "not_completed_by_cutoff"}
    return row


def project(rows, *, count=1, cutoff=CUTOFF, start=START):
    return status.project(helpers.example_registration(count),
        {"cases": rows, "superseded_interim_case_ids": []},
        study_start=start, observation_cutoff=cutoff)


def document_activity(report, index=0):
    return report["registered_documents"][index]["activity"]


class MonitoringActivityProjectionTests(unittest.TestCase):
    def test_lifetime_recording_coverage_does_not_refresh_old_assessments(self):
        rows = [completed(str(index), document=index) for index in range(7)]
        first = project(rows, count=7)
        later = project(rows, count=7, cutoff="2026-09-08T08:00:00Z")
        self.assertEqual(status.ratio(7, 7), first["in_protocol"]["timely_recording_coverage"])
        self.assertEqual(first["in_protocol"], later["in_protocol"])
        self.assertEqual(0, later["counts"]["registered_documents_without_completed_collector_assessments"])
        for index in range(7):
            before, after = document_activity(first, index), document_activity(later, index)
            self.assertEqual(10800, before["last_completed_assessment"]["age_seconds"])
            self.assertEqual(97200, after["last_completed_assessment"]["age_seconds"])
            self.assertEqual(1, after["completed_assessments"])
            self.assertEqual(97200, after["assessment_gap_to_cutoff_seconds"])
        self.assertNotIn("missed_runs", json.dumps(later))

    def test_policy_block_advances_assessment_but_not_document_attempt(self):
        rows = [completed("old"), completed("blocked", assessment="2026-09-07T07:00:00Z",
            attempt=None, outcome=None, comparable=False,
            collection_status="not_attempted_policy_blocked")]
        result = project(rows)
        activity = document_activity(result)
        self.assertEqual(2, activity["completed_assessments"])
        self.assertEqual(1, activity["source_attempts_in_completed_assessments"])
        self.assertEqual(["blocked"], activity["last_completed_assessment"]["case_ids"])
        self.assertEqual(3600, activity["last_completed_assessment"]["age_seconds"])
        self.assertEqual({"not_attempted_policy_blocked": 1},
                         activity["last_completed_assessment"]["collection_statuses"])
        self.assertEqual({"not_attempted": 1},
                         activity["last_completed_assessment"]["transport_outcomes"])
        self.assertEqual(["old"], activity["last_source_attempt"]["case_ids"])
        self.assertEqual(10810, activity["last_source_attempt"]["age_seconds"])

    def test_failed_transport_is_a_completed_attempt_not_successful_content(self):
        result = project([completed("old"), completed("failure",
            assessment="2026-09-07T07:00:00Z", attempt="2026-09-07T06:59:50Z",
            outcome="failed", comparable=False, collection_status="failed_check")])
        activity = document_activity(result)
        self.assertEqual(2, activity["source_attempts_in_completed_assessments"])
        self.assertEqual(["failure"], activity["last_source_attempt"]["case_ids"])
        self.assertEqual(3610, activity["last_source_attempt"]["age_seconds"])
        self.assertEqual({"failed": 1}, activity["last_source_attempt"]["transport_outcomes"])
        self.assertEqual({"failed_check": 1}, activity["last_source_attempt"]["collection_statuses"])
        self.assertEqual(1, result["in_protocol"]["collector_comparable_opportunities"])
        self.assertNotIn("last_eligible_response_at", json.dumps(result))
        self.assertNotIn("freshness_seconds", json.dumps(result))

    def test_transport_success_retains_identity_and_content_failures(self):
        for rejected in ("document_identity_requires_review", "unexpected_media_type",
                         "unsupported_encoding", "empty_visible_text"):
            with self.subTest(rejected=rejected):
                result = project([completed("rejected", comparable=False,
                    collection_status=rejected)])
                attempt = document_activity(result)["last_source_attempt"]
                self.assertEqual({"succeeded": 1}, attempt["transport_outcomes"])
                self.assertEqual({rejected: 1}, attempt["collection_statuses"])
                self.assertEqual(0, result["in_protocol"]["collector_comparable_opportunities"])

    def test_first_eligible_observation_need_not_be_comparable(self):
        result = project([completed("first", comparable=False,
            collection_status="first_observation_requires_review")])
        self.assertEqual(1, document_activity(result)["source_attempts_in_completed_assessments"])
        self.assertEqual(0, result["in_protocol"]["collector_comparable_opportunities"])
        self.assertEqual({"first_observation_requires_review": 1},
            document_activity(result)["last_source_attempt"]["collection_statuses"])

    def test_new_incomplete_intent_does_not_refresh_completed_activity(self):
        original = document_activity(project([completed("old")]))
        result = project([completed("old"), incomplete("new-intent")])
        self.assertEqual(original, document_activity(result))
        pending = next(row for row in result["cases"] if row["case_id"] == "new-intent")
        self.assertEqual("2026-09-07T07:00:00Z", pending["intent_started_at"])
        for field in ("assessment_at", "source_request_attempted", "source_attempt_finished_at",
                      "transport_outcome"):
            self.assertIsNone(pending[field], field)

    def test_only_incomplete_intent_has_unknown_request_execution_and_full_gap(self):
        result = project([incomplete("pending")])
        activity = document_activity(result)
        self.assertEqual(0, activity["completed_assessments"])
        self.assertEqual(0, activity["source_attempts_in_completed_assessments"])
        self.assertIsNone(activity["last_completed_assessment"]["at"])
        self.assertIsNone(activity["last_source_attempt"]["at"])
        self.assertIsNone(result["cases"][0]["source_request_attempted"])
        self.assertEqual(14400, activity["assessment_gap_to_cutoff_seconds"])
        self.assertEqual("study_start_no_completed_assessment", activity["assessment_gap_basis"])

    def test_outside_protocol_cases_do_not_fill_registered_recency(self):
        for mode in ("forced_poll_capture", "unregistered_poll_configuration",
                     "registered_document_or_plan_changed", "outside_registered_documents"):
            with self.subTest(mode=mode):
                row = completed("outside", assessment="2026-09-07T07:00:00Z", protocol=False)
                row["collection_mode"] = mode
                if mode == "outside_registered_documents":
                    row["case"].update(url="https://example.org/outside", document_id="outside")
                result = project([row])
                self.assertEqual(1, result["outside_protocol"]["opportunities"])
                self.assertEqual(0, document_activity(result)["completed_assessments"])
                self.assertEqual(14400, document_activity(result)["assessment_gap_to_cutoff_seconds"])

    def test_prediction_recording_clocks_and_results_do_not_refresh_collection(self):
        initial = completed("one")
        expected = document_activity(project([initial]))
        for recording, prediction in (("missing", None),
                ("late", {"result": "revision_candidate", "reason": "source_target_changed"}),
                ("on_time", {"result": "abstain", "reason": "unsupported_url"}),
                ("on_time", {"result": "error", "reason": "private exception text"})):
            with self.subTest(recording=recording, prediction=prediction):
                row = copy.deepcopy(initial)
                row.update(recording_status=recording, prediction=prediction,
                    first_recorded_at="2026-09-07T07:59:59Z", assessment_to_prediction_seconds=10799)
                self.assertEqual(expected, document_activity(project([row])))

    def test_tied_latest_outcomes_and_input_order_are_preserved(self):
        rows = [completed("old"), completed("success", assessment="2026-09-07T07:00:00Z",
                attempt="2026-09-07T06:59:50Z"),
            completed("failed", assessment="2026-09-07T07:00:00Z", attempt="2026-09-07T06:59:50Z",
                outcome="failed", comparable=False, collection_status="failed_check"),
            completed("blocked", assessment="2026-09-07T07:00:00Z", attempt=None,
                outcome=None, comparable=False, collection_status="not_attempted_policy_blocked")]
        expected = project(rows)
        self.assertEqual(expected, project(list(reversed(rows))))
        activity = document_activity(expected)
        self.assertEqual(["blocked", "failed", "success"], activity["last_completed_assessment"]["case_ids"])
        self.assertEqual({"failed": 1, "not_attempted": 1, "succeeded": 1},
                         activity["last_completed_assessment"]["transport_outcomes"])
        self.assertEqual(["failed", "success"], activity["last_source_attempt"]["case_ids"])
        self.assertEqual({"failed_check": 1, "unchanged": 1},
                         activity["last_source_attempt"]["collection_statuses"])
        self.assertEqual(7200, activity["largest_assessment_gap_seconds"])

    def test_exact_start_assessment_and_pre_window_attempt_are_preserved(self):
        result = project([completed("boundary", assessment=START,
            attempt="2026-09-07T03:59:50Z")])
        activity = document_activity(result)
        self.assertEqual(14400, activity["last_completed_assessment"]["age_seconds"])
        self.assertEqual(14410, activity["last_source_attempt"]["age_seconds"])
        self.assertTrue(result["activity_basis"]["source_attempts_may_predate_window"])

    def test_projection_rejects_completed_cases_outside_half_open_window(self):
        for clock in ("2026-09-07T03:59:59.999999Z", CUTOFF, "2026-09-07T08:00:00.000001Z"):
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                project([completed("boundary", assessment=clock, attempt=clock)])

    def test_projection_rejects_incomplete_intents_outside_half_open_window(self):
        for clock in ("2026-09-07T03:59:59.999999Z", CUTOFF):
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                project([incomplete("boundary", intent=clock)])

    def test_empty_population_has_unknown_last_activity_and_full_elapsed_gap(self):
        result = project([], count=7)
        for document in result["registered_documents"]:
            activity = document["activity"]
            for field in ("last_completed_assessment", "last_source_attempt"):
                self.assertEqual({"at": None, "age_seconds": None, "case_ids": [],
                    "collection_statuses": {}, "transport_outcomes": {}}, activity[field])
            self.assertEqual(14400, activity["assessment_gap_to_cutoff_seconds"])
            self.assertEqual(14400, activity["largest_assessment_gap_seconds"])
        self.assertEqual(7, result["counts"]["registered_documents_without_source_attempts_in_completed_assessments"])

    def test_largest_gap_includes_leading_trailing_and_internal_intervals(self):
        for clocks, largest in ((["2026-09-07T07:00:00Z"], 10800),
                (["2026-09-07T05:00:00Z"], 10800),
                (["2026-09-07T04:30:00Z", "2026-09-07T07:30:00Z"], 10800)):
            with self.subTest(clocks=clocks):
                rows = [completed(str(index), assessment=clock, attempt=clock)
                        for index, clock in enumerate(clocks)]
                self.assertEqual(largest, document_activity(project(rows))["largest_assessment_gap_seconds"])

    def test_invalid_completed_case_clocks_and_attempt_states_fail_closed(self):
        mutations = ({"assessment_at": "invalid"}, {"assessment_at": None},
            {"attempted": 1}, {"attempted": None}, {"response_finished_at": None},
            {"response_finished_at": "invalid"}, {"response_finished_at": "2026-09-07T05:00:00.000001Z"},
            {"transport_outcome": None}, {"transport_outcome": "unknown"},
            {"attempted": False}, {"attempted": False, "response_finished_at": None})
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, TypeError, KeyError)):
                row = completed("invalid")
                row["case"].update(mutation)
                project([row])
        for missing in ("assessment_at", "attempted", "response_finished_at", "transport_outcome"):
            with self.subTest(missing=missing), self.assertRaises((ValueError, TypeError, KeyError)):
                row = completed("missing")
                del row["case"][missing]
                project([row])

    def test_nonpositive_window_and_unknown_case_kind_fail_closed(self):
        for cutoff in (START, "2026-09-07T03:59:59Z"):
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                project([], cutoff=cutoff)
        row = completed("unknown")
        row["case"]["kind"] = "prediction_receipt"
        with self.assertRaises(ValueError):
            project([row])

    def test_activity_projection_does_not_disclose_source_or_target_text(self):
        row = completed("private")
        row["case"].update(body="PRIVATE SOURCE BODY", scope="PRIVATE SOURCE SCOPE",
            predecessor={"body": "PRIVATE PREDECESSOR"})
        row["prediction"] = {"result": "error", "reason": "PRIVATE EXCEPTION",
            "analysis": {"target": "PRIVATE TARGET"}}
        rendered = json.dumps(project([row]))
        self.assertNotIn("PRIVATE", rendered)
        self.assertIn("detector_error", rendered)


class MonitoringActivityAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProspectiveTargetReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.register()
        self.fixture.poll.tick(START)
        self.fixture.record()

    def audit(self):
        return status.audit(self.fixture.registration_path, self.fixture.root)

    def test_frozen_population_excludes_assessment_exactly_at_audit_cutoff(self):
        boundary = "2026-09-07T05:00:00Z"
        self.fixture.poll.tick(boundary)
        self.fixture.clock = boundary
        exact = self.audit()
        self.assertEqual(1, exact["counts"]["interim_document_opportunities"])
        self.assertEqual(START, document_activity(exact)["last_completed_assessment"]["at"])
        self.fixture.clock = "2026-09-07T05:00:00.000001Z"
        after = self.audit()
        self.assertEqual(2, after["counts"]["interim_document_opportunities"])
        self.assertEqual(boundary, document_activity(after)["last_completed_assessment"]["at"])
        self.assertEqual(0.000001, document_activity(after)["last_completed_assessment"]["age_seconds"])
        self.assertEqual({"missing": 1, "on_time": 1}, after["in_protocol"]["recording_statuses"])

    def test_closed_window_activity_does_not_age_beyond_registered_end(self):
        self.fixture.clock = self.fixture.study["end"]
        at_end = self.audit()
        self.fixture.clock = "2026-09-07T09:00:00Z"
        later = self.audit()
        self.assertEqual(at_end["observation_cutoff"], later["observation_cutoff"])
        self.assertNotEqual(at_end["audit_started_at"], later["audit_started_at"])
        self.assertEqual(document_activity(at_end), document_activity(later))
        self.assertEqual("closed_window_unsealed", later["status"])

    def test_absent_prediction_receipt_does_not_remove_collector_activity(self):
        recorded = self.audit()
        for path in self.fixture.prediction_root.glob("*.receipt.json"):
            path.unlink()
        unaccepted = self.audit()
        self.assertEqual(document_activity(recorded), document_activity(unaccepted))
        self.assertEqual({"accepted": 0, "unaccepted": 1}, unaccepted["batches"])
        self.assertEqual({"missing": 1}, unaccepted["in_protocol"]["recording_statuses"])

    def test_audit_reads_retained_assessment_clocks_without_network_or_persistent_writes(self):
        before = {str(path): path.read_bytes() for path in self.fixture.root.rglob("*") if path.is_file()}
        requests = len(self.fixture.poll.capture.calls)
        result = self.audit()
        after = {str(path): path.read_bytes() for path in self.fixture.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(requests, len(self.fixture.poll.capture.calls))
        case = result["cases"][0]
        row = document_activity(result)
        self.assertEqual(case["assessment_at"], row["last_completed_assessment"]["at"])
        self.assertEqual(case["source_attempt_finished_at"], row["last_source_attempt"]["at"])
        self.assertTrue(all(value is False for value in result["boundaries"].values()))
        self.assertTrue(all(value is None for value in result["unscored"].values()))


if __name__ == "__main__":
    unittest.main()
