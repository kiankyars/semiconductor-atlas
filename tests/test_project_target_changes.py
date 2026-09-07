from __future__ import annotations

import copy
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from semiconductor_atlas import database, project_target_changes as changes, project_target_review as acceptance
from semiconductor_atlas.curated_review import _hash
from tests import test_project_target_review


class ProjectTargetChangeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_project_target_review.ProjectTargetReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        mocked = patch.object(changes, "_now", side_effect=self.fixture.now)
        mocked.start(); self.addCleanup(mocked.stop)
        self.admission = self.fixture.accept()

    def build(self):
        return changes.build_packet(self.fixture.connection, self.fixture.review_path,
            reference_root=self.fixture.root, source_queue=self.fixture.queue)

    def resign(self, packet):
        packet["packet_id"] = _hash({key: value for key, value in packet.items() if key != "packet_id"})
        return packet

    def validate(self, packet):
        return changes.validate_packet(packet, clock=self.fixture.now())

    def reject(self, packet):
        self.resign(packet)
        with self.assertRaises(ValueError):
            self.validate(packet)

    def period(self, low, high, *, literal="fixture period", precision="range"):
        return {"milestone_type": "production_start", "status": "expected", "date_low": low,
            "date_base": None, "date_high": high, "date_precision": precision, "date_literal": literal}

    def test_packet_links_exact_core_claims_and_evidence(self):
        packet = self.build()
        self.assertEqual(self.admission["run_id"], packet["comparison_id"])
        self.assertEqual(self.admission["entity_id"], packet["subject"]["entity_id"])
        self.assertEqual(self.admission["admitted_at"], packet["accepted_at"])
        self.assertEqual("disjoint_earlier", packet["change"]["classification"])
        self.assertEqual(1, len(packet["proposals"]))
        for name in ("before", "after"):
            claim = packet["claims"][name]
            self.assertEqual(self.admission["claim_ids"][name], claim["id"])
            self.assertEqual(2, len(claim["evidence"]))
            self.assertTrue(all(len(row["fragment_sha256"]) == 64 for row in claim["evidence"]))
            self.assertIsNone(claim["confidence"])
            self.assertIsNone(claim["valid_from"])
            self.assertIsNone(claim["document"]["published_at"])
            self.assertIsNone(claim["value"]["date_base"])
        self.assertEqual(packet, self.validate(packet))

    def test_build_is_read_only_even_with_sqlite_writes_denied(self):
        connection = self.fixture.connection
        before = connection.total_changes
        connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) else sqlite3.SQLITE_OK)
        self.build()
        self.assertEqual(before, connection.total_changes)

    def test_unaccepted_review_cannot_trigger_import(self):
        fresh, _ = database.initialize(":memory:")
        try:
            with patch.object(acceptance, "accept_review", side_effect=AssertionError("must not try to accept")):
                with self.assertRaisesRegex(ValueError, "missing accepted core"):
                    changes.build_packet(fresh, self.fixture.review_path, reference_root=self.fixture.root, source_queue=self.fixture.queue)
            self.assertEqual(0, fresh.execute("SELECT COUNT(*) FROM claim_versions").fetchone()[0])
        finally:
            fresh.close()

    def test_offline_validation_needs_no_source_files_or_database(self):
        packet = self.build()
        with patch.object(changes, "_read", side_effect=AssertionError("filesystem")), patch.object(acceptance, "accept_review", side_effect=AssertionError("source replay")), patch.object(sqlite3, "connect", side_effect=AssertionError("database")):
            self.assertEqual(packet, self.validate(packet))
        self.assertEqual(changes.VALIDATION_SCOPE, packet["provenance"]["verification_scope"])

    def test_build_comparison_and_proposal_identity_stable_across_generation_clocks(self):
        first, second = self.build(), self.build()
        self.assertEqual(first["comparison_id"], second["comparison_id"])
        self.assertEqual(first["series_id"], second["series_id"])
        self.assertEqual(first["proposals"][0]["fingerprint"], second["proposals"][0]["fingerprint"])
        self.assertNotEqual(first["generated_at"], second["generated_at"])
        self.assertNotEqual(first["packet_id"], second["packet_id"])

    def test_exact_generation_clock_rebuild_is_deterministic(self):
        clock = self.fixture.now()
        with patch.object(changes, "_now", return_value=clock):
            self.assertEqual(self.build(), self.build())

    def test_period_classifier_disjoint_later(self):
        self.assertEqual("disjoint_later", changes.classify_periods(self.period("2027-07-01", "2027-12-31"), self.period("2028-01-01", "2028-12-31")))

    def test_overlap_cannot_be_reported_as_definite_earlier(self):
        prior = self.period("2028-01-01", "2028-12-31")
        for after in (self.period("2027-07-01", "2028-06-30"), self.period("2028-07-01", "2028-12-31"), self.period("2027-01-01", "2029-12-31")):
            self.assertEqual("overlapping_changed", changes.classify_periods(prior, after))

    def test_same_bounds_different_literal_or_precision_are_reaffirmation(self):
        prior = self.period("2028-01-01", "2028-12-31", literal="2028", precision="year")
        after = self.period("2028-01-01", "2028-12-31", literal="throughout the calendar year")
        self.assertEqual("reaffirmation", changes.classify_periods(prior, after))
        packet = self.build()
        packet["change"]["before"] = prior
        packet["change"]["after"] = after
        self.assertEqual([], changes._proposals(packet))

    def test_adjacent_inclusive_periods_are_disjoint_but_shared_day_is_overlap(self):
        before = self.period("2028-01-01", "2028-12-31")
        self.assertEqual("disjoint_earlier", changes.classify_periods(before, self.period("2027-07-01", "2027-12-31")))
        self.assertEqual("overlapping_changed", changes.classify_periods(before, self.period("2027-07-01", "2028-01-01")))

    def test_midpoint_and_attainment_rejected(self):
        original = self.period("2028-01-01", "2028-12-31")
        for key, value in (("date_base", "2028-07-01"), ("status", "completed"), ("milestone_type", "qualification")):
            value_period = {**original, key: value}
            with self.assertRaises(ValueError):
                changes.classify_periods(original, value_period)

    def test_packet_hash_mutation_rejected(self):
        packet = self.build()
        packet["accepted_at"] = "2026-09-07T03:00:00Z"
        with self.assertRaisesRegex(ValueError, "digest"):
            self.validate(packet)

    def test_rehashed_attainment_or_confidence_escalation_rejected(self):
        original = self.build()
        for key, value in (("confidence", 0.9), ("valid_from", "2026-09-07"), ("claim_kind", "reconciled_fact")):
            packet = copy.deepcopy(original)
            packet["claims"]["after"][key] = value
            self.reject(packet)

    def test_rehashed_claim_or_evidence_transplant_rejected(self):
        original = self.build()
        for mutate in (lambda p: p["claims"]["after"].update(id=p["claims"]["before"]["id"]),
                       lambda p: p["claims"]["after"]["evidence"][0].update(fragment_sha256="0" * 64),
                       lambda p: p["claims"]["after"]["source_record"].update(record_sha256="0" * 64)):
            packet = copy.deepcopy(original); mutate(packet); self.reject(packet)

    def test_rehashed_source_native_subject_transplant_rejected(self):
        packet = self.build()
        packet["subject"]["entity_id"] = "tsmc:fab21-arizona"
        self.reject(packet)

    def test_rehashed_proposal_delivery_or_physical_acceleration_rejected(self):
        original = self.build()
        for key, value in (("delivery_eligible", True), ("exact_acceleration_days", 365), ("classification", "physical_acceleration")):
            packet = copy.deepcopy(original)
            packet["proposals"][0][key] = value
            self.reject(packet)

    def test_before_after_swap_rejected(self):
        packet = self.build()
        packet["claims"]["before"], packet["claims"]["after"] = packet["claims"]["after"], packet["claims"]["before"]
        self.reject(packet)

    def test_generated_or_accepted_future_clock_rejected(self):
        original = self.build()
        for key, value in (("accepted_at", "2026-07-18T01:54:47Z"), ("generated_at", "2099-01-01T00:00:00Z")):
            packet = copy.deepcopy(original); packet[key] = value; self.reject(packet)

    def test_removed_or_duplicate_proposal_rejected(self):
        original = self.build()
        for proposals in ([], original["proposals"] * 2):
            packet = copy.deepcopy(original); packet["proposals"] = proposals; self.reject(packet)

    def test_review_raw_bytes_and_content_must_both_match(self):
        packet = self.build()
        packet["provenance"]["review"]["content"]["project"]["source_native_subject"] = "Fab 1"
        self.reject(packet)

    def test_portable_scope_cannot_claim_external_source_verification(self):
        packet = self.build()
        packet["provenance"]["verification_scope"] = "independently_verified_publisher_truth"
        self.reject(packet)

    def test_acceptance_dependency_and_code_binding_rejected(self):
        packet = self.build()
        packet["provenance"]["acceptance"]["dependency_code_sha256"]["semiconductor_atlas/project_target_review.py"] = "0" * 64
        self.reject(packet)

    def test_unchanged_real_accepted_pair_produces_zero_proposals(self):
        other = test_project_target_review.ProjectTargetReviewTests(); other.setUp()
        self.addCleanup(other.doCleanups)
        body = other.after_body + b"<!-- previously retained -->"
        other.before_path.write_bytes(body)
        other.manifest["inputs"][0].update(sha256=_hash(body), bytes=len(body))
        other.write(other.manifest_path, other.manifest)
        other.review["before"].update(body=other.binding(other.before_path), manifest=other.binding(other.manifest_path),
            target=copy.deepcopy(other.review["after"]["target"]), spans=other.spans(body))
        other.write_review(); other.accept()
        with patch.object(changes, "_now", side_effect=other.now):
            packet = changes.build_packet(other.connection, other.review_path, reference_root=other.root, source_queue=other.queue)
            self.assertEqual("reaffirmation", packet["change"]["classification"])
            self.assertEqual([], packet["proposals"])
            self.assertEqual(packet, changes.validate_packet(packet, clock=other.now()))

    def test_packet_omits_future_canonical_scope_and_raw_publisher_html(self):
        packet = self.build()
        self.assertIsNone(packet["subject"]["canonical_facility_assignment"])
        for claim in packet["claims"].values():
            for evidence in claim["evidence"]:
                self.assertNotIn("<p>", evidence["excerpt"])
                self.assertNotIn("<table>", evidence["excerpt"])
        self.assertLess(len(json.dumps(packet)), changes.MAX_PACKET_BYTES)

    def test_rehashed_published_date_and_source_body_hash_changes_rejected(self):
        original = self.build()
        for key, value in (("published_at", "2026-07-27"), ("content_sha256", "0" * 64), ("retrieval_timestamp_basis", "exact physical event time")):
            packet = copy.deepcopy(original)
            packet["claims"]["after"]["document"][key] = value
            self.reject(packet)

    def test_missing_evidence_or_extra_claims_rejected(self):
        packet = self.build()
        packet["claims"]["after"]["evidence"].pop()
        self.reject(packet)
        packet = self.build()
        packet["claims"]["third"] = copy.deepcopy(packet["claims"]["after"])
        self.reject(packet)

    def test_portable_validator_does_not_silently_normalize_mutated_period(self):
        packet = self.build()
        packet["change"]["after"]["date_low"] = "2027-01-01"
        self.reject(packet)

    def test_builder_uses_backup_and_preserves_source_connection_settings(self):
        original = self.fixture.connection
        class ObservedConnection:
            def __init__(self):
                self.backups = 0
            @property
            def in_transaction(self):
                return original.in_transaction
            def execute(self, *args, **kwargs):
                return original.execute(*args, **kwargs)
            def backup(self, destination):
                self.backups += 1
                return original.backup(destination)
        observed = ObservedConnection()
        before = original.execute("PRAGMA query_only").fetchone()[0]
        changes.build_packet(observed, self.fixture.review_path, reference_root=self.fixture.root, source_queue=self.fixture.queue)
        self.assertEqual(1, observed.backups)
        self.assertEqual(before, original.execute("PRAGMA query_only").fetchone()[0])

    def test_changed_retained_review_cannot_build_derivative_of_previous_admission(self):
        self.fixture.review["rationale"] += " Changed after acceptance."
        self.fixture.write_review()
        with self.assertRaisesRegex(ValueError, "missing accepted core"):
            self.build()

    def test_sequential_comparisons_same_subject_keep_series_not_comparison_identity(self):
        first = self.build()
        self.fixture.second_review(); self.fixture.accept()
        second = self.build()
        self.assertEqual(first["series_id"], second["series_id"])
        self.assertNotEqual(first["comparison_id"], second["comparison_id"])
        self.assertEqual("disjoint_later", second["change"]["classification"])
        self.assertEqual(first["claims"]["after"]["document"]["id"], second["claims"]["before"]["document"]["id"])
