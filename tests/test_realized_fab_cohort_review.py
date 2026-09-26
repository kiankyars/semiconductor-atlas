"""Independent synthetic replay and population checks; no publisher bodies."""

import copy
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semiconductor_atlas import realized_fab_cohort as cohort
from semiconductor_atlas import realized_milestones as realized
from tests import test_realized_milestones as source_fixtures


NOW = "2026-09-08T00:00:00Z"
ADMISSION_CLOCKS = ["2026-09-07T21:00:00.000001Z",
                    "2026-09-07T21:00:00.000002Z",
                    "2026-09-07T21:00:00.000003Z"]


class RealizedFabCohortReviewTests(unittest.TestCase):
    def setUp(self):
        self.data = [["2", "", "1990", "", "6-inch", "", "500"],
                     ["21", "", "2024", "", "12-inch", "", "5"],
                     ["23", "", "2024", "", "12-inch", "", "28"]]
        self.source_review, self.body, self.provenance = source_fixtures.fixture(
            rows=self.data, selected=1)
        with mock.patch.object(realized, "_now", side_effect=[
                "2026-09-07T20:00:00Z", "2026-09-07T20:00:01Z", "2026-09-07T20:00:02Z"]):
            self.prior = realized.admit(self.source_review, body=self.body, provenance=self.provenance)
        self.anchor = realized.canonical_bytes(self.prior)
        self.review = self.review_for(self.data, self.body, self.anchor)

    @staticmethod
    def review_for(data, body, anchor):
        data_spans = list(re.finditer(rb"<tr>.*?</tr>", body, re.DOTALL))[2:]
        assert len(data_spans) == len(data)
        rows = []
        for cells, span in zip(data, data_spans):
            rows.append({"fab": cells[0], "year": cells[2],
                         "evidence": {"start": span.start(), "end": span.end(),
                                      "sha256": realized._hash(span.group())},
                         "decision": "carry_forward" if cells[0] == "21" else "accept_source_report",
                         "reason": "Synthetic exact row and year reviewed without physical corroboration."})
        return {"format": cohort.REVIEW_FORMAT, "scope": cohort.SCOPE,
                "anchor_file_sha256": realized._hash(anchor),
                "reviewed_by": "Independent engineering test author",
                "reviewed_at": "2026-09-07T20:10:00Z",
                "prior_exposure": "Fully exposed synthetic fixtures; this is not independent source evidence.",
                "rationale": "Every synthetic data row is considered in exact source order, without forecasting.",
                "rows": rows}

    def validate(self, review=None, **inputs):
        with mock.patch.object(realized, "_now", return_value=NOW), \
                mock.patch.object(cohort, "_now", return_value=NOW):
            return cohort.validate_review(self.review if review is None else review,
                anchor=inputs.get("anchor", self.anchor), body=inputs.get("body", self.body),
                provenance=inputs.get("provenance", self.provenance))

    def admit(self, review=None, clocks=ADMISSION_CLOCKS):
        with mock.patch.object(realized, "_now", return_value=NOW), \
                mock.patch.object(cohort, "_now", side_effect=clocks):
            return cohort.admit(self.review if review is None else review,
                anchor=self.anchor, body=self.body, provenance=self.provenance)

    def verify(self, artifact):
        with mock.patch.object(realized, "_now", return_value=NOW), \
                mock.patch.object(cohort, "_now", return_value=NOW):
            return cohort.verify(artifact, body=self.body, provenance=self.provenance)

    @staticmethod
    def reseal(value):
        value.pop("sha256", None)
        return cohort.benchmark._seal(value)

    def test_population_and_old_clock_are_retained_without_event_aliases(self):
        before = (copy.deepcopy(self.review), self.anchor, self.body, self.provenance)
        artifact = self.admit()
        self.assertEqual(3, artifact["table_rows"])
        self.assertEqual({"newly_accepted": 2, "carried_forward": 1, "deferred": 0}, artifact["counts"])
        self.assertEqual(3, artifact["accepted_source_reports"])
        self.assertEqual(["Fab 2", "Fab 21", "Fab 23"], [row["subject"]["label"] for row in artifact["cases"]])
        self.assertEqual(3, len({row["source_report_key"] for row in artifact["cases"]}))
        for index, case in enumerate(artifact["cases"]):
            self.assertEqual("commercial_production_commencement", case["event"]["event_type"])
            self.assertEqual("year", case["event"]["precision"])
            self.assertIsNone(case["event"]["base"])
            self.assertIsNone(case["subject"]["canonical_entity_id"])
            self.assertIsNone(case["subject"]["location"])
            self.assertEqual(self.prior["admitted_at"] if index == 1 else ADMISSION_CLOCKS[-1], case["recorded_at"])
        self.assertEqual(self.prior["sha256"], artifact["cases"][1]["prior_artifact_sha256"])
        self.assertEqual(before, (self.review, self.anchor, self.body, self.provenance))

    def test_deferred_row_stays_in_denominator_without_an_event_or_admission(self):
        review = copy.deepcopy(self.review)
        review["rows"][2]["decision"] = "defer"
        artifact = self.admit(review)
        self.assertEqual(3, artifact["table_rows"])
        self.assertEqual(2, artifact["accepted_source_reports"])
        self.assertEqual({"newly_accepted": 1, "carried_forward": 1, "deferred": 1}, artifact["counts"])
        self.assertIsNone(artifact["cases"][2]["event"])
        self.assertIsNone(artifact["cases"][2]["recorded_at"])
        self.assertTrue(self.verify(artifact)["verified"])

    def test_missing_duplicate_reordered_and_extra_rows_fail_closed(self):
        for replacement in (self.review["rows"][:-1], self.review["rows"] + [self.review["rows"][0]],
                            self.review["rows"][::-1], [], [self.review["rows"][1]]):
            review = copy.deepcopy(self.review)
            review["rows"] = copy.deepcopy(replacement)
            with self.subTest(rows=len(replacement)), self.assertRaises(ValueError):
                self.validate(review)

    def test_fab21_cannot_be_readmitted_deferred_or_reassigned(self):
        for index, decision in ((1, "accept_source_report"), (1, "defer"), (0, "carry_forward")):
            review = copy.deepcopy(self.review)
            review["rows"][index]["decision"] = decision
            with self.subTest(index=index, decision=decision), self.assertRaises(ValueError):
                self.validate(review)

    def test_duplicate_nonanchor_subject_fails_even_when_anchor_accepts_table(self):
        data = self.data + [self.data[2]]
        source_review, body, provenance = source_fixtures.fixture(rows=data, selected=1)
        with mock.patch.object(realized, "_now", return_value=NOW):
            prior = realized.admit(source_review, body=body, provenance=provenance)
        anchor = realized.canonical_bytes(prior)
        review = self.review_for(data, body, anchor)
        review["reviewed_at"] = NOW
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.validate(review, anchor=anchor, body=body, provenance=provenance)

    def test_future_and_non_ascii_realized_years_fail_without_touching_anchor(self):
        for year in ("2026", "1٩٩٩"):
            data = copy.deepcopy(self.data)
            data[0][2] = year
            source_review, body, provenance = source_fixtures.fixture(rows=data, selected=1)
            with mock.patch.object(realized, "_now", return_value=NOW):
                prior = realized.admit(source_review, body=body, provenance=provenance)
            anchor = realized.canonical_bytes(prior)
            review = self.review_for(data, body, anchor)
            review["reviewed_at"] = NOW
            with self.subTest(year=year), self.assertRaises(ValueError):
                self.validate(review, anchor=anchor, body=body, provenance=provenance)

    def test_exact_body_provenance_anchor_and_row_spans_bind(self):
        for key, value in (("anchor", self.anchor + b" "), ("body", self.body + b" "),
                           ("provenance", self.provenance + b" ")):
            with self.subTest(input=key), self.assertRaises(ValueError):
                self.validate(**{key: value})
        for field, value in (("start", self.review["rows"][0]["evidence"]["start"] - 5),
                             ("start", True), ("end", 10**12), ("sha256", "f" * 64)):
            review = copy.deepcopy(self.review)
            review["rows"][0]["evidence"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(review)

    def test_frozen_materialized_values_and_denominators_cannot_be_resealed(self):
        artifact = self.admit()
        paths = [(["counts", "newly_accepted"], 99), (["accepted_source_reports"], 0),
                 (["table_rows"], 1), (["table_as_of"], "2024-12-31"),
                 (["cases", 0, "event", "event_type"], "high_volume_production"),
                 (["cases", 0, "event", "base"], "1990-07-01"),
                 (["cases", 1, "recorded_at"], artifact["admitted_at"]),
                 (["cases", 0, "subject", "canonical_entity_id"], "invented-facility"),
                 (["cases", 0, "source_report_key"], "0" * 64),
                 (["selection", "survivorship_bias"], False),
                 (["rights", "source_bodies_embedded"], 0),
                 (["boundaries", "forecast_score"], True),
                 (["review_sha256"], "0" * 64)]
        for path, replacement in paths:
            changed = copy.deepcopy(artifact)
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = replacement
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.verify(self.reseal(changed))

    def test_anchor_semantics_cannot_be_laundered_through_cohort_resealing(self):
        artifact = self.admit()
        artifact["anchor"]["review"]["event"]["base"] = "2024-07-01"
        artifact["anchor"]["review_sha256"] = realized._hash(artifact["anchor"]["review"])
        artifact["anchor"] = self.reseal(artifact["anchor"])
        artifact["review"]["anchor_file_sha256"] = realized._hash(realized.canonical_bytes(artifact["anchor"]))
        artifact["review_sha256"] = realized._hash(artifact["review"])
        with self.assertRaises(ValueError):
            self.verify(self.reseal(artifact))

    def test_review_admission_and_replay_clocks_are_ordered(self):
        for clock in ("2026-09-07T19:59:59Z", "2026-09-09T00:00:00Z"):
            review = copy.deepcopy(self.review)
            review["reviewed_at"] = clock
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                self.validate(review)
        for clocks in (["2026-09-07T20:09:59Z"] * 3,
                       [ADMISSION_CLOCKS[0], ADMISSION_CLOCKS[1], "2026-09-07T20:59:59Z"]):
            with self.subTest(clocks=clocks), self.assertRaises(ValueError):
                self.admit(clocks=clocks)
        artifact = self.admit()
        artifact["admitted_at"] = "2026-09-09T00:00:00Z"
        with self.assertRaises(ValueError):
            self.verify(self.reseal(artifact))

    def test_producer_drift_rejects_admission_and_exact_replay(self):
        artifact = self.admit()
        changed = {**cohort._LOADED_CODES, "realized_fab_cohort.py": "f" * 64}
        with mock.patch.object(cohort, "_disk_codes", return_value=changed):
            with self.assertRaises(ValueError):
                self.admit()
            with self.assertRaises(ValueError):
                self.verify(artifact)

    def test_code_drift_during_materialization_fails_admission_and_replay(self):
        artifact = self.admit()
        materialize = cohort._materialize
        drifted = [False]

        def materialize_then_drift(*args):
            result = materialize(*args)
            drifted[0] = True
            return result

        def current_codes():
            return {**cohort._LOADED_CODES, **({"realized_fab_cohort.py": "f" * 64} if drifted[0] else {})}

        with mock.patch.object(cohort, "_disk_codes", side_effect=current_codes), \
                mock.patch.object(cohort, "_materialize", side_effect=materialize_then_drift):
            with self.assertRaises(ValueError):
                self.admit()
            drifted[0] = False
            with self.assertRaises(ValueError):
                self.verify(artifact)

    def test_reference_only_deterministic_replay_and_new_only_writer(self):
        artifact = self.admit()
        raw = cohort.canonical_bytes(artifact)
        self.assertEqual(self.verify(raw), self.verify(raw))
        self.assertEqual(raw, cohort.canonical_bytes(self.admit()))
        for forbidden in (b"<table>", b"<td>", b"base64", b"local_evidence_text", self.body, self.provenance):
            self.assertNotIn(forbidden, raw)
        self.assertTrue(artifact["selection"]["survivorship_bias"])
        self.assertFalse(artifact["boundaries"]["forecast_score"])
        self.assertFalse(artifact["boundaries"]["training_dataset_established"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "synthetic-cohort.json"
            cohort.write_new(path, artifact)
            with self.assertRaises(FileExistsError):
                cohort.write_new(path, artifact)
            self.assertEqual(raw, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
