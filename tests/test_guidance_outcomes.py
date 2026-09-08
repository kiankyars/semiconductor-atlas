"""Future-clock synthetic outcome admission and complete-denominator evaluation."""

import copy
import unittest
from unittest import mock

from semiconductor_atlas import guidance_baseline as baseline
from semiconductor_atlas import milestone_benchmark as benchmark
from tests.test_guidance_baseline import _Fixture, CLOCKS, CUTOFF


REVIEW_TIME = "2029-01-10T00:00:00Z"


class GuidanceOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = _Fixture()
        self.addCleanup(self.fixture.connection.close)
        for case in self.fixture.screen["candidates"]:
            case["scope"] = self.fixture.scopes[case["case_id"]]
        for name, value in (("_codes", {"test": "c" * 64}), ("_runtime", {"test": True})):
            patch = mock.patch.object(baseline, name, return_value=value)
            patch.start(); self.addCleanup(patch.stop)
        with mock.patch.object(baseline, "_now", side_effect=CLOCKS):
            self.vintage = baseline.freeze(*self.fixture.materialize(), evidence_cutoff_at=CUTOFF)
        self.raw = benchmark.canonical_bytes(self.vintage)
        self.sha = benchmark._hash(self.raw)
        self.bodies = {}
        self.review = {"reviewed_by": "Synthetic exposed outcome reviewer",
                       "prior_exposure": "Fixture author saw all predictions, not blind.",
                       "evidence_cutoff_at": "2028-12-31T23:59:59Z", "outcomes": []}
        for frozen in self.vintage["cases"]:
            case = frozen["case"]
            self.review["outcomes"].append({key: case[key] for key in ("case_id", "project_entity_id", "scope", "event_type")})
            self.review["outcomes"][-1].update(status="unknown", event_interval=None, censor_at=None,
                cancellation_interval=None, reason="Engineering source review, not manufacturing truth.",
                evidence=[], evidence_kind="unresolved", evidence_interpretation="No supported event outcome.")

    def outcome(self, case_id, status, *, low=None, high=None, through=None, retrieved="2028-12-31T12:00:00Z"):
        row = next(row for row in self.review["outcomes"] if row["case_id"] == case_id)
        text = f"é 工厂 Project {case_id} {row['event_type']} {status} {low or through} through {high or through}."
        body = ("Engineering evidence: " + text).encode()
        digest = benchmark._hash(body)
        self.bodies[digest] = body
        start = body.index(text.encode())
        interval = {"low": low, "high": high, "literal": low} if low else None
        row.update(status=status,
            event_interval=interval if status == "observed" else None,
            cancellation_interval=interval if status == "cancelled" else None,
            censor_at=through if status == "right_censored" else None,
            evidence_kind={"observed": "attainment", "cancelled": "project_cancellation", "right_censored": "explicit_nonoccurrence"}[status],
            evidence_interpretation="Synthetic exact event/scope interpretation for this test only.",
            evidence=[{"document_sha256": digest, "document_url": "https://example.test/outcomes",
                "published_at": retrieved[:10], "retrieved_at": retrieved, "start": start,
                "end": start + len(text.encode()), "excerpt": text, "locator": "Synthetic literal event paragraph"}])

    def admit(self, now=REVIEW_TIME):
        with mock.patch.object(baseline, "_now", return_value=now):
            return baseline.review_outcomes(self.fixture.database, self.raw, self.fixture.bodies,
                trusted_vintage_sha256=self.sha, review=self.review, bodies=self.bodies)

    def score(self, review, now=REVIEW_TIME, trusted=None):
        raw = benchmark.canonical_bytes(review)
        with mock.patch.object(baseline, "_now", return_value=now):
            return baseline.score(self.fixture.database, self.raw, self.fixture.bodies, raw,
                trusted_vintage_sha256=self.sha, trusted_sha256=trusted or benchmark._hash(raw), bodies=self.bodies)

    def test_complete_review_counts_containment_censor_miss_and_cancellation(self):
        self.outcome("a", "observed", low="2028-03-01", high="2028-03-31")
        self.outcome("b", "right_censored", through="2028-12-31")
        self.outcome("c", "cancelled", low="2027-03-01", high="2027-03-31")
        self.outcome("e", "observed", low="2026-07-01", high="2026-07-31")
        report = self.score(self.admit())
        self.assertEqual(self.vintage["denominators"], report["denominators"])
        self.assertEqual(7, len(report["cases"]))
        self.assertEqual(1, report["issued_guaranteed_containments"])
        self.assertEqual(1, report["issued_proven_window_misses"])
        self.assertEqual(1, report["issued_window_result_unresolved"])
        self.assertEqual({"abstained": 3, "cancelled": 1, "censored_window_miss": 1,
                          "contained": 1, "screened_out": 1}, report["classification_counts"])
        self.assertEqual(6, sum(report["retained_outcome_status_counts"].values()))
        self.assertEqual("pre_vintage", report["cases"][4]["temporal_classification"])
        self.assertIsNone(report["point_error"])
        self.assertIsNone(report["probability_calibration"])

    def test_all_unknowns_keep_issued_denominator_without_success_score(self):
        report = self.score(self.admit())
        self.assertEqual(3, report["issued_window_result_unresolved"])
        self.assertEqual(0, report["issued_guaranteed_containments"])
        self.assertEqual({"unknown": 6}, report["retained_outcome_status_counts"])

    def test_outcome_scope_or_event_alias_cannot_be_accepted(self):
        for key, value in (("event_type", "high_volume_production"), ("scope", "Different project phase")):
            with self.subTest(key=key):
                prior = self.review["outcomes"][0][key]
                self.review["outcomes"][0][key] = value
                with self.assertRaisesRegex(ValueError, "exact frozen"):
                    self.admit()
                self.review["outcomes"][0][key] = prior

    def test_missing_abstention_or_screen_exclusion_is_rejected(self):
        self.review["outcomes"].pop()
        with self.assertRaisesRegex(ValueError, "all screened"):
            self.admit()

    def test_source_guidance_is_not_an_attainment_evidence_kind(self):
        self.outcome("a", "observed", low="2028-01-01", high="2028-01-31")
        self.review["outcomes"][0]["evidence_kind"] = "guidance"
        with self.assertRaisesRegex(ValueError, "explicit attainment"):
            self.admit()

    def test_nonoccurrence_needs_positive_evidence_not_missing_report(self):
        self.review["outcomes"][0].update(status="right_censored", censor_at="2028-12-31", evidence_kind="explicit_nonoccurrence")
        with self.assertRaisesRegex(ValueError, "require bounded source evidence"):
            self.admit()

    def test_future_checkpoint_and_unregistered_cutoff_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "has not occurred"):
            self.admit(now="2028-12-01T00:00:00Z")
        self.review["evidence_cutoff_at"] = "2028-12-30T23:59:59Z"
        with self.assertRaisesRegex(ValueError, "prespecified"):
            self.admit()

    def test_evidence_retrieved_after_checkpoint_cannot_enter_earlier_review(self):
        self.outcome("a", "observed", low="2028-01-01", high="2028-01-31", retrieved="2029-01-01T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "retrieval.*after"):
            self.admit()

    def test_realized_event_or_censor_cannot_postdate_evidence_retrieval(self):
        self.outcome("a", "right_censored", through="2029-01-01")
        with self.assertRaisesRegex(ValueError, "postdates source retrieval"):
            self.admit()

    def test_altered_exact_body_excerpt_or_literal_is_rejected(self):
        self.outcome("a", "observed", low="2028-01-01", high="2028-01-31")
        row = self.review["outcomes"][0]
        row["evidence"][0]["excerpt"] += " forged"
        with self.assertRaisesRegex(ValueError, "differs from exact"):
            self.admit()

    def test_review_does_not_allow_allocating_excluded_aggregate(self):
        self.outcome("g", "observed", low="2028-01-01", high="2028-01-31")
        with self.assertRaisesRegex(ValueError, "aggregate exclusion"):
            self.admit()

    def test_original_external_hash_rejects_resealed_outcome(self):
        review = self.admit()
        trusted = benchmark._hash(benchmark.canonical_bytes(review))
        payload = copy.deepcopy(review); payload.pop("sha256")
        payload["review"]["outcomes"][0]["reason"] = "Altered reviewed interpretation"
        with self.assertRaisesRegex(ValueError, "independently retained"):
            self.score(benchmark._seal(payload), trusted=trusted)

    def test_even_resealed_review_must_revalidate_its_actual_evidence(self):
        self.outcome("a", "observed", low="2028-01-01", high="2028-01-31")
        review = self.admit()
        review.pop("sha256")
        review["review"]["outcomes"][0]["evidence"][0]["end"] -= 1
        with self.assertRaisesRegex(ValueError, "differs from exact"):
            self.score(benchmark._seal(review))

    def test_future_review_rejected_and_late_review_flagged(self):
        review = self.admit()
        with self.assertRaisesRegex(ValueError, "chronology"):
            self.score(review, now="2029-01-09T00:00:00Z")
        self.assertTrue(self.score(self.admit(now="2029-02-01T00:00:00Z"), now="2029-02-01T00:00:00Z")["review_late"])
