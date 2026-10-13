"""Synthetic semantic-review binding checks; no source truth or calibration claims."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import unittest
from unittest import mock

from semiconductor_atlas import forecast_scope as scope
from semiconductor_atlas import models, repository
from tests import test_analytics as fixtures


AS_OF = "2026-07-17"
RECORDED = fixtures.RECORDED_AT
REVIEWED = "2026-07-18T00:00:01Z"
LATER = "2026-07-19T00:00:00Z"
NOW = "2026-07-20T00:00:00Z"


class ForecastScopeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ForecastBridgeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.connection = self.fixture.connection
        self.milestone = self.fixture._milestone(models.MilestoneStatus.EXPECTED)
        self.capacity = self.fixture._capacity(name="scope-capacity", basis=models.CapacityBasis.ANNOUNCED,
                                               milestone=self.milestone)
        self.connection.commit()
        clock = mock.patch.object(scope, "_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def decision(self, **changes):
        return dict(capacity_claim_id=self.capacity.id, milestone_claim_id=self.milestone.id,
                    project_scope="Synthetic single project", phase_scope="Synthetic phase one",
                    event_type="production_start", decision="accepted",
                    rationale="Synthetic reviewer attests that this rate and event concern this exact phase.") | changes

    def create(self, *, decisions=None, as_of=AS_OF, recorded_at=RECORDED, reviewed_at=REVIEWED):
        with mock.patch.object(scope, "_now", return_value=reviewed_at):
            return scope.create_scope_review(self.connection, as_of=as_of, recorded_at=recorded_at,
                reviewer="synthetic-reviewer", decisions=decisions if decisions is not None else [self.decision()])

    def validate(self, review, *, expected_sha256=None, as_of=AS_OF, recorded_at=LATER):
        payload = scope.canonical_bytes(review) if isinstance(review, dict) else review
        return scope.validate_scope_review(self.connection, review=payload,
            expected_sha256=expected_sha256 or hashlib.sha256(payload).hexdigest(),
            as_of=as_of, recorded_at=recorded_at)

    def scalar(self, identifier, *, recorded=LATER, entity=None, connection=None):
        c = connection or self.connection
        entity = entity or self.fixture.entity_id
        series = models.ClaimSeries("series:" + identifier, entity, "series:" + identifier,
            "source_reported." + identifier, models.ValueKind.SCALAR, fixtures.CREATED_AT)
        repository.add_claim_series(c, series)
        repository.insert_claim(c, models.ClaimVersion(identifier, series.id, None, recorded,
            models.ClaimKind.SOURCE_STATEMENT, "synthetic-review", None),
            models.ScalarValue(models.ScalarType.TEXT, "Synthetic later project evidence"),
            evidence=[models.EvidenceLink(self.fixture.document_id)])

    def test_accepted_and_deferred_bind_exact_entity_and_dossier_without_raw_text(self):
        for decision in ("accepted", "deferred"):
            with self.subTest(decision=decision):
                review = self.create(decisions=[self.decision(decision=decision)])
                self.assertEqual(scope.FORMAT, review["format"])
                self.assertEqual(RECORDED, review["evidence_cutoff_at"])
                self.assertEqual(REVIEWED, review["reviewed_at"])
                entry = self.validate(review)[self.capacity.id]
                self.assertEqual(decision, entry["decision"])
                self.assertEqual(self.fixture.entity_id, entry["entity_id"])
                self.assertRegex(entry["evidence_dossier_sha256"], r"^[a-f0-9]{64}$")
                self.assertNotIn("evidence", entry)
                self.assertNotIn("recorded_history", entry)

    def test_semantic_attestation_is_not_automated_truth_verification(self):
        review = self.create(decisions=[self.decision(phase_scope="Reviewer assertion is deliberately unverified")])
        self.assertEqual("Reviewer assertion is deliberately unverified",
                         self.validate(review)[self.capacity.id]["phase_scope"])

    def test_create_sorts_without_mutating_caller_decisions(self):
        other = self.fixture._capacity(name="other-capacity", basis=models.CapacityBasis.ANNOUNCED,
                                        milestone=self.milestone)
        self.connection.commit()
        inputs = sorted([self.decision(), self.decision(capacity_claim_id=other.id)],
                        key=lambda row: row["capacity_claim_id"], reverse=True)
        original = copy.deepcopy(inputs)
        review = self.create(decisions=inputs)
        self.assertEqual(original, inputs)
        self.assertEqual(sorted(row["capacity_claim_id"] for row in inputs),
                         [row["capacity_claim_id"] for row in review["decisions"]])
        self.assertEqual(2, len(self.validate(review)))
        self.assertEqual(review["decisions"][0]["evidence_dossier_sha256"],
                         review["decisions"][1]["evidence_dossier_sha256"])

    def test_tampered_review_bytes_cannot_reuse_trusted_hash(self):
        review = self.create()
        digest = hashlib.sha256(scope.canonical_bytes(review)).hexdigest()
        for field, value in (("phase_scope", "Different phase"), ("project_scope", "Different project"),
                             ("event_type", "mass_production"), ("capacity_claim_id", "different-root"),
                             ("decision", "deferred"), ("rationale", "Changed rationale")):
            changed = copy.deepcopy(review)
            changed["decisions"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                self.validate(changed, expected_sha256=digest)

    def test_separately_supplied_digest_must_be_well_formed(self):
        payload = scope.canonical_bytes(self.create())
        for digest in (None, "", "A" * 64, "g" * 64, "0" * 63, 123):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                scope.validate_scope_review(self.connection, review=payload, expected_sha256=digest,
                    as_of=AS_OF, recorded_at=LATER)

    def test_event_must_match_actual_typed_milestone_even_with_new_digest(self):
        review = self.create()
        review["decisions"][0]["event_type"] = "mass_production"
        with self.assertRaisesRegex(ValueError, "exact typed milestone event"):
            self.validate(review)
        with self.assertRaisesRegex(ValueError, "exact typed milestone event"):
            self.create(decisions=[self.decision(event_type="mass_production")])

    def test_unknown_or_wrong_root_kinds_fail(self):
        for changes in ({"capacity_claim_id": "missing"}, {"milestone_claim_id": "missing"},
                        {"capacity_claim_id": self.milestone.id}, {"milestone_claim_id": self.capacity.id}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.create(decisions=[self.decision(**changes)])

    def test_foreign_entity_milestone_cannot_be_attested(self):
        repository.add_entity(self.connection, models.Entity("other", models.EntityKind.PROJECT,
            "other-project", fixtures.CREATED_AT, "Other synthetic project"))
        original = self.fixture.entity_id
        try:
            self.fixture.entity_id = "other"
            other = self.fixture._milestone(models.MilestoneStatus.COMPLETED)
        finally:
            self.fixture.entity_id = original
        with self.assertRaisesRegex(ValueError, "same exact entity"):
            self.create(decisions=[self.decision(milestone_claim_id=other.id)])

    def test_binding_metadata_cannot_be_forged_with_new_digest(self):
        for field, value in (("entity_id", "wrong-entity"), ("evidence_dossier_sha256", "0" * 64)):
            review = self.create()
            review["decisions"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "binding differs"):
                self.validate(review)

    def test_duplicate_capacities_and_unsorted_bound_entries_fail(self):
        with self.assertRaisesRegex(ValueError, "duplicate capacity"):
            self.create(decisions=[self.decision(), self.decision(decision="deferred")])
        review = self.create()
        review["decisions"].append(copy.deepcopy(review["decisions"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate capacity"):
            self.validate(review)
        other = self.fixture._capacity(name="sort-other", basis=models.CapacityBasis.ANNOUNCED,
                                       milestone=self.milestone)
        review = self.create(decisions=[self.decision(), self.decision(capacity_claim_id=other.id)])
        review["decisions"].reverse()
        with self.assertRaisesRegex(ValueError, "must be sorted"):
            self.validate(review)

    def test_strict_fields_and_text_bounds_fail_before_binding(self):
        variants = [self.decision(extra=True), {k: v for k, v in self.decision().items() if k != "phase_scope"},
                    self.decision(phase_scope=" "), self.decision(project_scope="x" * 4097),
                    self.decision(rationale="x" * 16385), self.decision(rationale="two\nlines"),
                    self.decision(capacity_claim_id="x" * 257), self.decision(decision="maybe")]
        for entry in variants:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.create(decisions=[entry])
        for entries in ([], {}, [self.decision()] * 1001):
            with self.subTest(entries_type=type(entries)), self.assertRaises(ValueError):
                self.create(decisions=entries)
        review = self.create()
        review["unknown"] = True
        with self.assertRaisesRegex(ValueError, "invalid format or fields"):
            self.validate(review)

    def test_duplicate_json_keys_and_nonfinite_json_fail_despite_matching_digest(self):
        review = self.create()
        payload = scope.canonical_bytes(review)
        duplicate = payload.replace(b'"format":', b'"format": "ignored",\n  "format":', 1)
        with self.assertRaisesRegex(ValueError, "duplicate object keys"):
            self.validate(duplicate)
        nonfinite = payload.replace(b'"reviewer": "synthetic-reviewer"', b'"reviewer": NaN')
        with self.assertRaisesRegex(ValueError, "finite"):
            self.validate(nonfinite)

    def test_noncanonical_or_unbounded_review_bytes_fail(self):
        review = self.create()
        with self.assertRaisesRegex(ValueError, "canonical JSON"):
            self.validate(json.dumps(review).encode())
        for payload in (b"", b"x" * (scope.MAX_REVIEW_BYTES + 1), bytearray(b"{}")):
            with self.subTest(kind=type(payload)), self.assertRaisesRegex(ValueError, "bounded exact bytes"):
                self.validate(payload)

    def test_canonical_bytes_are_strict_finite_json(self):
        self.assertEqual(b'{\n  "a": 1,\n  "b": 2\n}\n', scope.canonical_bytes({"b": 2, "a": 1}))
        for value in (float("inf"), float("nan"), {1: "key"}, (1, 2), {"set": {1}}, "\ud800"):
            with self.subTest(value_type=type(value)), self.assertRaises(ValueError):
                scope.canonical_bytes(value)
        nested = []
        for _ in range(66):
            nested = [nested]
        with self.assertRaisesRegex(ValueError, "nesting"):
            scope.canonical_bytes(nested)

    def test_future_evidence_cannot_be_reviewed_and_creation_uses_actual_clock(self):
        with self.assertRaisesRegex(ValueError, "cannot be in the future"):
            self.create(recorded_at=LATER)
        review = self.create(reviewed_at=LATER)
        self.assertEqual(LATER, review["reviewed_at"])
        with self.assertRaisesRegex(ValueError, "unavailable or out of order"):
            self.validate(review, recorded_at=REVIEWED)

    def test_unknown_naive_future_and_out_of_order_stored_clocks_fail(self):
        for changes in ({"reviewed_at": None}, {"reviewed_at": "2026-07-18T00:00:01"},
                        {"evidence_cutoff_at": None}, {"reviewed_at": fixtures.CREATED_AT},
                        {"reviewed_at": "2026-07-21T00:00:00Z"},
                        {"evidence_cutoff_at": LATER}):
            review = self.create() | changes
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.validate(review, recorded_at="2026-07-22T00:00:00Z")

    def test_review_cannot_be_used_before_its_actual_review_microsecond(self):
        review = self.create(reviewed_at="2026-07-18T00:00:01.000001Z")
        with self.assertRaisesRegex(ValueError, "unavailable or out of order"):
            self.validate(review, recorded_at="2026-07-18T00:00:01Z")
        self.assertEqual(1, len(self.validate(review, recorded_at="2026-07-18T00:00:01.000001Z")))

    def test_dates_and_request_clocks_are_strict_and_world_cutoff_cannot_change(self):
        review = self.create()
        for as_of in ("2026-07-18", "20260717", "2026-02-30", None):
            with self.subTest(as_of=as_of), self.assertRaises(ValueError):
                self.validate(review, as_of=as_of)
        for recorded in (None, "2026-07-19", "2026-07-19T00:00:00", "bad"):
            with self.subTest(recorded=recorded), self.assertRaises(ValueError):
                self.validate(review, recorded_at=recorded)

    def test_timezone_inputs_normalize_without_changing_evidence_digest(self):
        normal = self.create()
        offset = self.create(recorded_at="2026-07-17T17:00:00-07:00")
        self.assertEqual(normal, offset)
        self.assertEqual(self.validate(normal),
                         self.validate(normal, recorded_at="2026-07-18T17:00:00-07:00"))
        offset["evidence_cutoff_at"] = "2026-07-17T17:00:00-07:00"
        with self.assertRaisesRegex(ValueError, "normalized UTC"):
            self.validate(offset)

    def test_roots_must_exist_at_both_world_and_knowledge_cutoffs(self):
        for kwargs in ({"as_of": "2025-12-31"}, {"recorded_at": "2026-07-17T23:59:59Z"}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "physical-current view"):
                self.create(**kwargs)

    def test_inactive_milestone_is_deferable_but_not_acceptible(self):
        cancelled = self.fixture._milestone(models.MilestoneStatus.CANCELLED)
        with self.assertRaisesRegex(ValueError, "active usable"):
            self.create(decisions=[self.decision(milestone_claim_id=cancelled.id)])
        review = self.create(decisions=[self.decision(milestone_claim_id=cancelled.id, decision="deferred")])
        self.assertEqual("deferred", self.validate(review)[self.capacity.id]["decision"])

    def test_unknown_midpoint_cannot_become_an_accepted_ramp(self):
        series = self.fixture._series("literal-only", models.ValueKind.MILESTONE)
        repository.insert_claim(self.connection, models.ClaimVersion("literal-only", series.id,
            "2026-01-01", RECORDED, models.ClaimKind.SOURCE_STATEMENT, "synthetic", 1.0),
            models.MilestoneValue("production_start", models.MilestoneStatus.EXPECTED,
                "2027-01-01", None, "2027-12-31", "year", "2027"),
            evidence=[models.EvidenceLink(self.fixture.document_id)])
        with self.assertRaisesRegex(ValueError, "active usable"):
            self.create(decisions=[self.decision(milestone_claim_id="literal-only")])
        review = self.create(decisions=[self.decision(milestone_claim_id="literal-only", decision="deferred")])
        self.assertEqual(1, len(self.validate(review)))

    def test_unknown_effective_root_cannot_replace_physical_current_view(self):
        series = self.fixture._series("unknown-effective", models.ValueKind.MILESTONE)
        repository.insert_claim(self.connection, models.ClaimVersion("unknown-effective", series.id,
            None, RECORDED, models.ClaimKind.SOURCE_STATEMENT, "synthetic", None),
            models.MilestoneValue("production_start", models.MilestoneStatus.EXPECTED,
                "2027-01-01", None, "2027-12-31", "year", "2027"),
            evidence=[models.EvidenceLink(self.fixture.document_id)])
        with self.assertRaisesRegex(ValueError, "physical-current view"):
            self.create(decisions=[self.decision(milestone_claim_id="unknown-effective", decision="deferred")])

    def test_later_unchanged_snapshot_remains_valid(self):
        review = self.create()
        self.assertEqual(self.validate(review, recorded_at=REVIEWED), self.validate(review, recorded_at=LATER))

    def test_new_same_entity_evidence_requires_new_review_but_old_snapshot_stays_valid(self):
        review = self.create()
        self.scalar("new-project-evidence")
        self.connection.commit()
        self.assertEqual(1, len(self.validate(review, recorded_at=REVIEWED)))
        with self.assertRaisesRegex(ValueError, "stale"):
            self.validate(review)
        refreshed = self.create(recorded_at=LATER, reviewed_at=NOW)
        self.assertNotEqual(review["decisions"][0]["evidence_dossier_sha256"],
                            refreshed["decisions"][0]["evidence_dossier_sha256"])
        self.assertEqual(1, len(self.validate(refreshed, recorded_at=NOW)))

    def test_unrelated_entity_evidence_does_not_stale_exact_subject_review(self):
        review = self.create()
        repository.add_entity(self.connection, models.Entity("other", models.EntityKind.PROJECT,
            "other-project", fixtures.CREATED_AT))
        self.scalar("unrelated-evidence", entity="other")
        self.connection.commit()
        self.assertEqual(1, len(self.validate(review)))

    def test_replaced_root_rejects_current_review_without_rewriting_earlier_snapshot(self):
        review = self.create()
        repository.insert_claim(self.connection, models.ClaimVersion("replacement-capacity", self.capacity.series_id,
            "2026-01-01", LATER, models.ClaimKind.DERIVED_ESTIMATE, "synthetic", 1.0),
            models.CapacityValue("wafer_starts", models.CapacityBasis.ANNOUNCED, "wafers/month", 90, 110, 130),
            dependencies=[models.DependencyLink(self.milestone.id)])
        self.connection.commit()
        self.assertEqual(1, len(self.validate(review, recorded_at=REVIEWED)))
        with self.assertRaisesRegex(ValueError, "physical-current view"):
            self.validate(review)

    def test_corrupted_typed_value_hash_is_rejected_without_repair(self):
        review = self.create()
        self.connection.execute("DROP TRIGGER capacity_values_immutable_update")
        self.connection.execute("UPDATE capacity_values SET base=101 WHERE claim_version_id=?", (self.capacity.id,))
        before = list(self.connection.iterdump())
        with self.assertRaisesRegex(ValueError, "typed value.*hash"):
            self.validate(review)
        self.assertEqual(before, list(self.connection.iterdump()))

    def test_changed_source_metadata_invalidates_old_binding(self):
        review = self.create()
        self.connection.execute("DROP TRIGGER source_documents_immutable_update")
        self.connection.execute("UPDATE source_documents SET title='Changed source identity' WHERE id=?",
                                (self.fixture.document_id,))
        with self.assertRaisesRegex(ValueError, "binding differs"):
            self.validate(review)

    def test_creation_and_validation_are_read_only_with_no_leftover_transaction(self):
        before, changes = list(self.connection.iterdump()), self.connection.total_changes
        review = self.create()
        self.validate(review)
        self.assertEqual(before, list(self.connection.iterdump()))
        self.assertEqual(changes, self.connection.total_changes)
        self.assertFalse(self.connection.in_transaction)
        review["decisions"][0]["evidence_dossier_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.validate(review)
        self.assertFalse(self.connection.in_transaction)

    def test_caller_transaction_and_same_named_savepoint_survive_success_and_failure(self):
        self.connection.execute("SAVEPOINT atlas_forecast_scope_read")
        self.scalar("uncommitted-evidence", recorded=RECORDED)
        before = list(self.connection.iterdump())
        review = self.create()
        self.validate(review)
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(before, list(self.connection.iterdump()))
        review["decisions"][0]["evidence_dossier_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.validate(review)
        self.assertEqual(before, list(self.connection.iterdump()))
        self.connection.execute("ROLLBACK TO SAVEPOINT atlas_forecast_scope_read")
        self.connection.execute("RELEASE SAVEPOINT atlas_forecast_scope_read")
        self.assertFalse(self.connection.in_transaction)
        self.assertIsNone(self.connection.execute("SELECT id FROM claim_versions WHERE id='uncommitted-evidence'").fetchone())

    def test_wal_writer_cannot_change_review_validation_snapshot_between_reads(self):
        review = self.create()
        self.assertEqual("wal", self.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0])
        path = self.connection.execute("PRAGMA database_list").fetchone()[2]
        writer = sqlite3.connect(path)
        writer.row_factory = sqlite3.Row
        writer.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(writer.close)
        original = scope.claim_records
        calls = 0

        def read_then_write(*args, **kwargs):
            nonlocal calls
            rows = original(*args, **kwargs)
            calls += 1
            if calls == 1:
                self.scalar("concurrent-evidence", recorded=LATER, connection=writer)
                writer.commit()
            return rows

        with mock.patch.object(scope, "claim_records", side_effect=read_then_write):
            self.assertEqual(1, len(self.validate(review)))
        self.assertEqual(2, calls)
        self.assertFalse(self.connection.in_transaction)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.validate(review)


if __name__ == "__main__":
    unittest.main()
