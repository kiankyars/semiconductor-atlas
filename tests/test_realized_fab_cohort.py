"""Complete synthetic inventories; never real publisher admissions or truth labels."""

from __future__ import annotations

import copy
import hashlib
import unittest
from unittest import mock

from semiconductor_atlas import milestone_benchmark as benchmark
from semiconductor_atlas import realized_fab_cohort as cohort
from semiconductor_atlas import realized_milestones as realized
from tests.test_realized_milestones import fixture as observation_fixture


ANCHOR_AT = "2026-09-02T00:00:00Z"
REVIEW_AT = "2026-09-03T00:00:00Z"
ADMISSION_CLOCKS = ["2026-09-04T00:00:00Z", "2026-09-04T00:00:01Z", "2026-09-04T00:00:02Z"]
VERIFY_AT = "2026-09-05T00:00:00Z"


def fixture(*, extra_rows=None):
    cells = [["2", "", "1990", "", "6-inch", "", "450"],
             ["21", "", "2024", "", "12-inch", "", "5"],
             ["23", "", "2024", "", "12-inch", "", "28"],
             ["22", "", "2025", "", "12-inch", "", "2"], *(extra_rows or [])]
    observation_review, body, provenance = observation_fixture(rows=cells, selected=1)
    with mock.patch.object(realized, "_now", return_value=ANCHOR_AT):
        anchor = realized.canonical_bytes(realized.admit(observation_review, body=body, provenance=provenance))
    rows = []
    for values in cells:
        raw = ("<tr>" + "".join("<td><div>" + cell + "</div></td>" for cell in values) + "</tr>").encode()
        start = body.index(raw)
        rows.append({"fab": values[0], "year": values[2],
                     "evidence": {"start": start, "end": start + len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
                     "decision": "carry_forward" if values[0] == "21" else "defer" if values[0] == "22" else "accept_source_report",
                     "reason": "Engineering decision for this exact synthetic row; not independent physical corroboration."})
    review = {"format": cohort.REVIEW_FORMAT, "scope": cohort.SCOPE, "anchor_file_sha256": hashlib.sha256(anchor).hexdigest(),
              "reviewed_by": "Synthetic cohort reviewer", "reviewed_at": REVIEW_AT,
              "prior_exposure": "All fixture rows were authored for engineering tests; this is an exposed review.",
              "rationale": "Enumerate every row in source order, preserve the old admission and retain deferred rows in the denominator.",
              "rows": rows}
    return review, anchor, body, provenance


class RealizedFabCohortTests(unittest.TestCase):
    def setUp(self):
        self.review, self.anchor, self.body, self.provenance = fixture()

    def validate(self, review=None, *, anchor=None, body=None, provenance=None):
        with mock.patch.object(cohort, "_now", return_value=VERIFY_AT), mock.patch.object(realized, "_now", return_value=VERIFY_AT):
            return cohort.validate_review(self.review if review is None else review,
                anchor=self.anchor if anchor is None else anchor, body=self.body if body is None else body,
                provenance=self.provenance if provenance is None else provenance)

    def admit(self, review=None, clocks=ADMISSION_CLOCKS):
        with mock.patch.object(cohort, "_now", side_effect=clocks), mock.patch.object(realized, "_now", return_value=VERIFY_AT):
            return cohort.admit(self.review if review is None else review, anchor=self.anchor, body=self.body, provenance=self.provenance)

    def verify(self, artifact):
        with mock.patch.object(cohort, "_now", return_value=VERIFY_AT), mock.patch.object(realized, "_now", return_value=VERIFY_AT):
            return cohort.verify(artifact, body=self.body, provenance=self.provenance)

    def reseal(self, artifact):
        value = copy.deepcopy(artifact)
        value.pop("sha256", None)
        return benchmark._seal(value)

    def test_full_inventory_retains_defer_and_prior_admission(self):
        result = self.validate()
        self.assertEqual(result["table_rows"], 4)
        artifact = self.admit()
        self.assertEqual(artifact["counts"], {"newly_accepted": 2, "carried_forward": 1, "deferred": 1})
        self.assertEqual(artifact["accepted_source_reports"], 3)
        self.assertEqual([row["subject"]["label"] for row in artifact["cases"]], ["Fab 2", "Fab 21", "Fab 23", "Fab 22"])
        self.assertEqual(artifact["table_as_of"], "2026-02-28")
        for index in (0, 2):
            self.assertEqual(artifact["cases"][index]["recorded_at"], ADMISSION_CLOCKS[-1])
            self.assertIsNone(artifact["cases"][index]["event"]["base"])
            self.assertEqual(artifact["cases"][index]["event"]["event_type"], "commercial_production_commencement")
        carried, deferred = artifact["cases"][1], artifact["cases"][3]
        self.assertEqual(carried["recorded_at"], ANCHOR_AT)
        self.assertEqual(carried["prior_artifact_sha256"], artifact["anchor"]["sha256"])
        self.assertEqual(carried["event"], artifact["anchor"]["review"]["event"])
        self.assertIsNone(deferred["event"])
        self.assertIsNone(deferred["recorded_at"])
        self.assertEqual(deferred["evidence"], self.review["rows"][3]["evidence"])
        self.assertTrue(self.verify(cohort.canonical_bytes(artifact))["verified"])

    def test_selection_bias_and_reference_only_boundaries_are_explicit(self):
        artifact = self.admit()
        self.assertTrue(artifact["selection"]["survivorship_bias"])
        self.assertFalse(artifact["selection"]["global_or_issuer_historical_project_census"])
        self.assertFalse(artifact["boundaries"]["training_dataset_established"])
        self.assertFalse(artifact["boundaries"]["geographic_generalization_established"])
        self.assertFalse(artifact["rights"]["redistribution_authorized"])
        raw = cohort.canonical_bytes(artifact)
        for prohibited in (self.body, self.provenance, b"<table>", b"local_evidence_text", b"base64"):
            self.assertNotIn(prohibited, raw)
        self.assertEqual(cohort.canonical_bytes(artifact["anchor"]), self.anchor)

    def test_omitted_duplicate_added_or_reordered_rows_rejected(self):
        variants = [self.review["rows"][:-1], self.review["rows"] + [self.review["rows"][0]],
                    self.review["rows"][::-1], [self.review["rows"][1]]]
        for rows in variants:
            with self.subTest(rows=len(rows)):
                review = copy.deepcopy(self.review); review["rows"] = rows
                with self.assertRaisesRegex(ValueError, "every exact table row"):
                    self.validate(review)

    def test_year_label_span_and_hash_substitution_rejected(self):
        for field, value in [("fab", "99"), ("year", "2024"), ("evidence", self.review["rows"][2]["evidence"])]:
            review = copy.deepcopy(self.review); review["rows"][0][field] = value
            with self.assertRaisesRegex(ValueError, "every exact table row"):
                self.validate(review)
        for field, value in [("start", 1), ("end", 5), ("sha256", "f" * 64)]:
            review = copy.deepcopy(self.review); review["rows"][0]["evidence"][field] = value
            with self.assertRaises(ValueError): self.validate(review)

    def test_unicode_digits_cannot_become_source_ids_or_calendar_years(self):
        for cells in (["2٣", "", "2000", "", "8-inch", "", "150"],
                      ["24", "", "202٣", "", "12-inch", "", "5"]):
            with self.subTest(cells=cells), self.assertRaises(ValueError):
                review, anchor, body, provenance = fixture(extra_rows=[cells])
                self.validate(review, anchor=anchor, body=body, provenance=provenance)

    def test_duplicate_nonanchor_fab_and_future_year_are_not_a_valid_population(self):
        for cells in (["23", "", "2024", "", "12-inch", "", "28"],
                      ["24", "", "2026", "", "12-inch", "", "5"]):
            with self.subTest(cells=cells), self.assertRaises(ValueError):
                review, anchor, body, provenance = fixture(extra_rows=[cells])
                self.validate(review, anchor=anchor, body=body, provenance=provenance)

    def test_prior_cannot_be_readmitted_deferred_or_carried_by_another_row(self):
        for index, decision in [(1, "accept_source_report"), (1, "defer"), (0, "carry_forward")]:
            review = copy.deepcopy(self.review); review["rows"][index]["decision"] = decision
            with self.assertRaisesRegex(ValueError, "carried forward"):
                self.validate(review)

    def test_all_other_rows_can_defer_without_shrinking_inventory(self):
        review = copy.deepcopy(self.review)
        for row in review["rows"]:
            if row["fab"] != "21": row["decision"] = "defer"
        artifact = self.admit(review)
        self.assertEqual(artifact["table_rows"], 4)
        self.assertEqual(artifact["counts"], {"newly_accepted": 0, "carried_forward": 1, "deferred": 3})
        self.assertEqual(artifact["accepted_source_reports"], 1)
        self.assertTrue(self.verify(artifact)["verified"])

    def test_exact_anchor_file_bytes_source_and_provenance_required(self):
        for kwargs in ({"anchor": self.anchor + b"\n"}, {"anchor": b""}, {"body": self.body + b" "},
                       {"provenance": self.provenance + b" "}):
            with self.subTest(field=next(iter(kwargs))):
                with self.assertRaises(ValueError): self.validate(**kwargs)

    def test_changed_resealed_anchor_does_not_match_review(self):
        anchor = realized._json(self.anchor)
        anchor["admitted_at"] = "2026-09-02T00:00:01Z"
        changed = cohort.canonical_bytes(self.reseal(anchor))
        with self.assertRaisesRegex(ValueError, "artifact bytes"):
            self.validate(anchor=changed)

    def test_actual_clocks_no_review_backdating_future_or_regression(self):
        for timestamp in ("2026-09-01T00:00:00Z", "2099-01-01T00:00:00Z"):
            review = copy.deepcopy(self.review); review["reviewed_at"] = timestamp
            with self.assertRaisesRegex(ValueError, "review"):
                self.validate(review)
        with self.assertRaisesRegex(ValueError, "clock"):
            self.admit(clocks=[ADMISSION_CLOCKS[2], ADMISSION_CLOCKS[2], ADMISSION_CLOCKS[0]])

    def test_changed_dependency_during_admission_rejected(self):
        codes = cohort._codes()
        with mock.patch.object(cohort, "_codes", side_effect=[codes, {}]):
            with self.assertRaisesRegex(ValueError, "producer"):
                self.admit()

    def test_producer_change_during_materialization_is_checked_before_returning(self):
        codes, original = cohort._codes(), cohort._materialize
        with mock.patch.object(cohort, "_codes", return_value=codes) as checker:
            def changing(*args):
                result = original(*args)
                checker.return_value = {}
                return result
            with mock.patch.object(cohort, "_materialize", side_effect=changing):
                with self.assertRaisesRegex(ValueError, "producer"):
                    self.admit()

    def test_resealed_materialized_counts_cases_selection_and_rights_rejected(self):
        artifact = self.admit()
        for field, value in [("table_rows", 3), ("counts", {"newly_accepted": 4}), ("cases", artifact["cases"][:-1]),
                             ("selection", {}), ("rights", {}), ("boundaries", {}), ("code_sha256", {}),
                             ("accepted_source_reports", 4), ("table_as_of", "2024-12-31")]:
            with self.subTest(field=field):
                changed = copy.deepcopy(artifact); changed[field] = value
                with self.assertRaises(ValueError): self.verify(self.reseal(changed))

    def test_resealed_case_cannot_change_identity_event_date_carry_clock_or_defer(self):
        artifact = self.admit()
        changes = [(0, "subject", {**artifact["cases"][0]["subject"], "canonical_entity_id": "canonical:fab2"}),
                   (0, "event", {**artifact["cases"][0]["event"], "event_type": "high_volume_production"}),
                   (0, "event", {**artifact["cases"][0]["event"], "base": "1990-07-01"}),
                   (1, "recorded_at", ADMISSION_CLOCKS[-1]), (1, "prior_artifact_sha256", None),
                   (3, "event", artifact["cases"][0]["event"]), (0, "source_report_key", "0" * 64)]
        for index, field, value in changes:
            changed = copy.deepcopy(artifact); changed["cases"][index][field] = value
            with self.assertRaises(ValueError): self.verify(self.reseal(changed))

    def test_resealed_review_and_anchor_cannot_drop_denominator(self):
        artifact = self.admit()
        artifact["review"]["rows"].pop()
        artifact["review_sha256"] = benchmark._hash(artifact["review"])
        with self.assertRaises(ValueError): self.verify(self.reseal(artifact))

    def test_review_requires_substantive_exposure_reason_and_exact_keys(self):
        for field, value in [("prior_exposure", ""), ("rationale", "approved"), ("scope", "all_global_fabs")]:
            review = copy.deepcopy(self.review); review[field] = value
            with self.assertRaises(ValueError): self.validate(review)
        review = copy.deepcopy(self.review); review["rows"][0]["reason"] = "ok"
        with self.assertRaises(ValueError): self.validate(review)
        review = copy.deepcopy(self.review); review["rows"][0]["capacity"] = 1
        with self.assertRaises(ValueError): self.validate(review)

    def test_duplicate_json_and_unsealed_or_noncanonical_artifacts_rejected(self):
        review = cohort.canonical_bytes(self.review).replace(b'{\n', b'{\n  "format":"duplicate",\n', 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.validate(review)
        artifact = self.admit()
        with self.assertRaises(ValueError): self.verify(cohort.canonical_bytes(artifact) + b" ")
        artifact.pop("sha256")
        with self.assertRaises(ValueError): self.verify(artifact)


if __name__ == "__main__":
    unittest.main()
