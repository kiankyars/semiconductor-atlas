from __future__ import annotations

import copy
import json
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from semiconductor_atlas import ai_critical_alert_review as legacy
from semiconductor_atlas import alert_review_v2 as alerts
from semiconductor_atlas import project_target_changes as changes
from semiconductor_atlas import project_target_population as population
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from tests import test_project_target_review as fixtures


STUDY_FORMAT = "semiconductor-atlas-project-target-study-v1"
SELECTION = "all_accepted_project_target_reviews_before_end"
START = "2026-09-07T00:00:00Z"
END = "2026-09-07T06:00:00Z"
GENERATED = "2026-09-07T05:00:00Z"
REVIEWED = "2026-09-07T05:05:00Z"
ADMITTED = "2026-09-07T05:10:00Z"
FROZEN = "2026-09-07T18:00:00Z"


class ProjectTargetPopulationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProjectTargetReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.connection = self.fixture.connection
        self.first = self.fixture.accept()
        self.first_path = self.fixture.review_path
        self.reviews = [self.review_ref(self.first, self.first_path)]
        self.queue = self.root / "alerts-v2.sqlite"
        alerts.initialize_queue(self.queue)
        self.study_path = self.root / "study.json"
        self.history_path = self.root / "history.json"
        self.packet_path = self.root / "proposal.json"
        self.admission_path = self.root / "proposal-review.json"
        self.frozen_path = self.root / "population.json"
        self.write_study()
        self.write_history()

    def review_ref(self, result, path):
        return {"run_id": result["run_id"], "path": path.relative_to(self.root).as_posix(),
            "sha256": legacy._hash(path.read_bytes())}

    def write_study(self, *, start=START, end=END, reviews=None, **extra):
        self.study = {"format": STUDY_FORMAT, "study_id": "fixture-accepted-project-population",
            "start": start, "end": end, "selection": SELECTION,
            "reviews": copy.deepcopy(self.reviews if reviews is None else reviews), **extra}
        self.study_path.write_bytes(_pretty_bytes(self.study))

    def write_history(self):
        self.history_path.write_bytes(_pretty_bytes(alerts.export_queue(self.queue)))

    def freeze(self):
        with patch.object(population, "_now", return_value=FROZEN), patch.object(changes, "_now", return_value=FROZEN):
            return population.freeze_population(self.connection, self.study_path, self.history_path,
                reference_root=self.root, source_queue=self.fixture.queue)

    def validate(self, artifact):
        self.frozen_path.write_bytes(_pretty_bytes(artifact))
        with patch.object(population, "_now", return_value=FROZEN):
            return population.validate_population(self.frozen_path)

    def build(self, *, path=None, clock=GENERATED):
        with patch.object(changes, "_now", return_value=clock):
            return changes.build_packet(self.connection, path or self.first_path,
                reference_root=self.root, source_queue=self.fixture.queue)

    def admit(self, *, packet=None, clock=ADMITTED, reviewed=REVIEWED, **overrides):
        packet = self.build() if packet is None else packet
        self.packet_path.write_bytes(_pretty_bytes(packet))
        admission = {"format": alerts.ADMISSION_FORMAT, "purpose": "alert_review_only",
            "reviewer": "fixture admission reviewer", "reviewed_at": reviewed,
            "reason": "Review source target statement only; no production attainment.",
            "packet_sha256": legacy._hash(self.packet_path.read_bytes()),
            "expected_head_event_id": alerts.queue_report(self.queue)["head_event_id"], **overrides}
        self.admission_path.write_bytes(_pretty_bytes(admission))
        with patch.object(alerts, "_now", return_value=clock):
            result = alerts.import_project_packet(self.queue, self.packet_path, self.admission_path)
        self.write_history()
        return result

    def second_review(self):
        self.fixture.second_review()
        result = self.fixture.accept()
        self.reviews.append(self.review_ref(result, self.fixture.review_path))
        self.write_study()
        return result

    @staticmethod
    def one_microsecond(clock, difference):
        return (datetime.fromisoformat(clock.replace("Z", "+00:00"))
            + timedelta(microseconds=difference)).isoformat().replace("+00:00", "Z")

    def test_unadmitted_accepted_revision_is_in_population_and_denominator(self):
        frozen = self.freeze()
        self.assertEqual(1, len(frozen["packets"]))
        self.assertEqual(1, len(frozen["cohort"]))
        self.assertEqual(1, len(frozen["opportunities"]))
        self.assertEqual([], frozen["predictions"])
        self.assertEqual([], frozen["excluded_comparisons"])
        self.assertIsNone(frozen["history_head_at_cutoff"])
        self.assertEqual(self.first["run_id"], frozen["packets"][0]["comparison_id"])
        self.assertEqual(1, len(frozen["packets"][0]["proposals"]))
        self.assertTrue(all(value is False for value in frozen["boundaries"].values()))
        restored, raw = self.validate(frozen)
        self.assertEqual(frozen, restored)
        self.assertEqual(_pretty_bytes(frozen), raw)

    def test_no_accepted_run_before_end_has_empty_cohort_not_future_members(self):
        self.write_study(end=self.first["admitted_at"], reviews=[])
        frozen = self.freeze()
        self.assertEqual([], frozen["packets"])
        self.assertEqual([], frozen["cohort"])
        self.assertEqual([], frozen["opportunities"])
        self.assertEqual([], frozen["predictions"])
        self.assertEqual(frozen, self.validate(frozen)[0])

    def test_final_seal_clock_follows_packet_generation_not_snapshot_start(self):
        clocks = ("2026-09-07T16:00:00Z", "2026-09-07T17:00:01Z",
            "2026-09-07T17:00:02Z", FROZEN)
        with patch.object(population, "_now", side_effect=clocks), patch.object(changes, "_now", return_value="2026-09-07T17:00:00Z"):
            frozen = population.freeze_population(self.connection, self.study_path, self.history_path,
                reference_root=self.root, source_queue=self.fixture.queue)
        self.assertEqual(clocks[0], frozen["snapshot_started_at"])
        self.assertEqual(clocks[2], frozen["frozen_at"])
        self.assertLess(frozen["packets"][0]["generated_at"], frozen["frozen_at"])

    def test_refused_queue_admission_does_not_erase_accepted_opportunity(self):
        with self.assertRaises(ValueError):
            self.admit(expected_head_event_id="0" * 64)
        self.write_history()
        frozen = self.freeze()
        self.assertEqual((1, 0), (len(frozen["opportunities"]), len(frozen["predictions"])))
        self.assertEqual(self.first["run_id"], frozen["packets"][0]["comparison_id"])

    def test_accepted_reaffirmation_remains_in_population_without_a_proposal(self):
        fixture = self.fixture
        body = fixture.after_body + b"<!-- fixture earlier unchanged target -->"
        body_path = self.root / "reaffirmation-before.html"
        body_path.write_bytes(body)
        manifest_path = self.root / "reaffirmation-manifest.json"
        manifest = copy.deepcopy(fixture.manifest)
        manifest["inputs"][0].update(path=body_path.name, sha256=legacy._hash(body), bytes=len(body))
        fixture.write(manifest_path, manifest)
        fixture.review = copy.deepcopy(fixture.review)
        fixture.review["before"].update(body=fixture.binding(body_path), manifest=fixture.binding(manifest_path),
            target=copy.deepcopy(fixture.review["after"]["target"]), spans=fixture.spans(body))
        fixture.review["reviewed_at"] = fixture.now()
        fixture.review_path = self.root / "reaffirmation-review.json"
        fixture.write_review()
        result = fixture.accept()
        self.reviews.append(self.review_ref(result, fixture.review_path))
        self.write_study()
        frozen = self.freeze()
        self.assertEqual(2, len(frozen["opportunities"]))
        self.assertEqual([], frozen["predictions"])
        reaffirmation = next(packet for packet in frozen["packets"] if packet["comparison_id"] == result["run_id"])
        self.assertEqual("reaffirmation", reaffirmation["change"]["classification"])
        self.assertEqual([], reaffirmation["proposals"])

    def test_manifest_requires_every_accepted_run_not_just_admitted_runs(self):
        self.admit()
        self.second_review()
        self.write_study(reviews=self.reviews[:1])
        with self.assertRaises(ValueError):
            self.freeze()
        self.write_study()
        frozen = self.freeze()
        self.assertEqual(2, len(frozen["opportunities"]))
        self.assertEqual(1, len(frozen["predictions"]))

    def test_extra_duplicate_wrong_hash_and_unbound_review_are_rejected(self):
        original = copy.deepcopy(self.reviews)
        variants = [[], original * 2, [*original, {**original[0], "run_id": "missing-run"}],
            [{**original[0], "sha256": "0" * 64}], [{**original[0], "path": "missing.json"}]]
        for reviews in variants:
            self.write_study(reviews=reviews)
            with self.subTest(reviews=reviews), self.assertRaises((ValueError, FileNotFoundError)):
                self.freeze()

    def test_prior_window_runs_must_be_in_manifest_and_backfills_remain_distinct(self):
        first = self.first
        second = self.second_review()
        self.admit(packet=self.build(path=self.first_path))
        self.write_study(start=second["admitted_at"], reviews=self.reviews[1:])
        with self.assertRaises(ValueError):
            self.freeze()
        self.write_study(start=second["admitted_at"])
        frozen = self.freeze()
        self.assertEqual(2, len(frozen["packets"]))
        self.assertEqual(1, len(frozen["opportunities"]))
        self.assertEqual(1, len(frozen["excluded_comparisons"]))
        self.assertIn(first["run_id"], json.dumps(frozen["excluded_comparisons"]))
        self.assertIn(second["run_id"], json.dumps(frozen["opportunities"]))
        self.assertEqual(1, len(frozen["predictions"]))

    def test_start_includes_exact_microsecond_and_excludes_previous_microsecond(self):
        self.fixture.tick += timedelta(microseconds=123456)
        second = self.second_review()
        for start, expected in ((second["admitted_at"], 1), (self.one_microsecond(second["admitted_at"], 1), 0)):
            self.write_study(start=start)
            frozen = self.freeze()
            self.assertEqual(expected, len(frozen["opportunities"]))
            self.assertEqual(2, len(frozen["packets"]))

    def test_end_excludes_exact_microsecond_without_rounding(self):
        self.fixture.tick += timedelta(microseconds=123456)
        second = self.second_review()
        self.write_study(end=second["admitted_at"], reviews=self.reviews[:1])
        frozen = self.freeze()
        self.assertEqual([self.first["run_id"]], [packet["comparison_id"] for packet in frozen["packets"]])
        self.write_study(end=self.one_microsecond(second["admitted_at"], 1))
        frozen = self.freeze()
        self.assertEqual(2, len(frozen["packets"]))
        self.assertEqual(2, len(frozen["opportunities"]))

    def test_unfinished_window_and_manual_cohort_selection_are_rejected(self):
        for extra in ({"end": "2026-09-08T00:00:00Z"}, {"start": END},
                {"selection": "only_admitted_alerts"}, {"cohort": []}):
            self.write_study()
            item = {**self.study, **extra}
            self.study_path.write_bytes(_pretty_bytes(item))
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.freeze()

    def test_later_admission_is_not_a_prediction_at_end(self):
        self.admit(clock="2026-09-07T07:00:00Z", reviewed="2026-09-07T06:59:00Z")
        frozen = self.freeze()
        self.assertEqual(1, len(frozen["opportunities"]))
        self.assertEqual([], frozen["predictions"])
        self.assertIsNone(frozen["history_head_at_cutoff"])

    def test_alert_admission_at_end_is_excluded_until_one_microsecond_later(self):
        result = self.admit()
        self.write_study(end=ADMITTED)
        frozen = self.freeze()
        self.assertEqual([], frozen["predictions"])
        self.assertIsNone(frozen["history_head_at_cutoff"])
        self.write_study(end=self.one_microsecond(ADMITTED, 1))
        frozen = self.freeze()
        self.assertEqual(1, len(frozen["predictions"]))
        self.assertEqual(result["head_event_id"], frozen["history_head_at_cutoff"])

    def test_later_decisions_do_not_leak_into_frozen_prediction(self):
        alert = self.admit()["alerts"][0]
        with patch.object(alerts, "_now", return_value="2026-09-07T07:00:00Z"):
            alerts.record_decision(self.queue, alert["id"], action="acknowledge",
                reviewer="fixture later reviewer", reason="Later action excluded at cutoff", expected_event_id=alert["last_event_id"])
        self.write_history()
        frozen = self.freeze()
        self.assertEqual(1, len(frozen["predictions"]))
        self.assertIn("pending", json.dumps(frozen["predictions"]))
        self.assertNotIn("fixture later reviewer", json.dumps(frozen["predictions"]))
        self.assertEqual(alert["last_event_id"], frozen["history_head_at_cutoff"])

    def test_same_run_with_different_retained_queue_packet_is_rejected(self):
        packet = self.build()
        packet["provenance"]["producer"]["sha256"] = "0" * 64
        packet["packet_id"] = legacy._hash({key: value for key, value in packet.items() if key != "packet_id"})
        self.admit(packet=packet)
        with self.assertRaises(ValueError):
            self.freeze()

    def test_freeze_is_read_only_with_sqlite_writes_denied(self):
        before = self.connection.total_changes
        self.connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) else sqlite3.SQLITE_OK)
        frozen = self.freeze()
        self.assertEqual(1, len(frozen["opportunities"]))
        self.assertEqual(before, self.connection.total_changes)
        self.assertFalse(self.connection.in_transaction)
        self.assertIs(sqlite3.Row, self.connection.row_factory)

    def test_producer_reads_same_coherent_snapshot_not_later_live_state(self):
        original = changes.build_packet
        observed = []
        def mutate_live_after_snapshot(connection, *args, **kwargs):
            self.assertIsNot(connection, self.connection)
            external = sqlite3.connect(self.root / "core.sqlite")
            try:
                external.execute("CREATE TABLE later_live_only (value TEXT)")
                external.commit()
            finally:
                external.close()
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='later_live_only'").fetchone())
            observed.append(True)
            return original(connection, *args, **kwargs)
        with patch.object(changes, "build_packet", side_effect=mutate_live_after_snapshot):
            frozen = self.freeze()
        self.assertEqual([True], observed)
        self.assertEqual(1, len(frozen["opportunities"]))
        self.assertIsNotNone(self.connection.execute("SELECT name FROM sqlite_master WHERE name='later_live_only'").fetchone())

    def test_broken_source_of_prior_window_run_is_not_silently_excluded(self):
        second = self.second_review()
        self.write_study(start=second["admitted_at"])
        self.fixture.before_path.write_bytes(self.fixture.before_path.read_bytes() + b" changed")
        with self.assertRaises(ValueError):
            self.freeze()

    def test_missing_rule_marker_cannot_remove_run_from_census(self):
        self.connection.execute("DROP TRIGGER ingestion_runs_immutable_update")
        row = self.connection.execute("SELECT parameters_json FROM ingestion_runs WHERE id=?", (self.first["run_id"],)).fetchone()
        parameters = json.loads(row[0])
        parameters.pop("rule_version")
        self.connection.execute("UPDATE ingestion_runs SET parameters_json=? WHERE id=?", (json.dumps(parameters), self.first["run_id"]))
        self.connection.commit()
        self.write_study(reviews=[])
        with self.assertRaises(ValueError):
            self.freeze()

    def test_route_claim_marker_conflict_fails_closed(self):
        self.connection.execute("DROP TRIGGER claim_versions_content_immutable")
        self.connection.execute("UPDATE claim_versions SET method='unrelated-method' WHERE id=?", (self.first["claim_ids"]["after"],))
        self.connection.commit()
        with self.assertRaises(ValueError):
            self.freeze()

    def test_core_integrity_error_is_not_reclassified_as_excluded_run(self):
        self.connection.execute("DROP TRIGGER ingestion_runs_immutable_update")
        self.connection.execute("UPDATE ingestion_runs SET status='failed' WHERE id=?", (self.first["run_id"],))
        self.connection.commit()
        self.write_study(reviews=[])
        with self.assertRaises(ValueError):
            self.freeze()

    def test_future_or_changed_acceptance_metadata_cannot_evade_manifest(self):
        self.connection.execute("DROP TRIGGER ingestion_runs_immutable_update")
        row = self.connection.execute("SELECT parameters_json FROM ingestion_runs WHERE id=?", (self.first["run_id"],)).fetchone()
        params = json.loads(row[0])
        params["accepted_at"] = "2099-01-01T00:00:00Z"
        self.connection.execute("UPDATE ingestion_runs SET parameters_json=? WHERE id=?", (json.dumps(params), self.first["run_id"]))
        self.connection.commit()
        self.write_study(reviews=[])
        with self.assertRaises(ValueError):
            self.freeze()

    def test_population_tampering_cannot_drop_an_unadmitted_run(self):
        self.second_review()
        frozen = self.freeze()
        for key in ("packets", "opportunities", "core_inventory"):
            changed = copy.deepcopy(frozen)
            changed[key] = changed[key][:-1]
            if key == "core_inventory":
                changed["core_inventory_sha256"] = legacy._hash(changed[key])
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(changed)

    def test_rehashed_inventory_source_and_importer_must_match_retained_packet(self):
        frozen = self.freeze()
        for field, wrong in (("source_id", "wrong-source"), ("code_version", "0" * 64)):
            changed = copy.deepcopy(frozen)
            changed["core_inventory"][0][field] = wrong
            changed["core_inventory_sha256"] = legacy._hash(changed["core_inventory"])
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(changed)

    def test_tampered_boundary_unknowns_or_cutoff_cannot_be_scored(self):
        self.admit()
        frozen = self.freeze()
        mutations = (
            lambda item: item["boundaries"].update(production_outcome_verified=True),
            lambda item: item["boundaries"].update(delivery_eligible=0),
            lambda item: item.update(history_head_at_cutoff=None),
            lambda item: item["opportunities"][0].update(queue_admitted_before_end=False),
            lambda item: item.update(snapshot_started_at="2026-09-07T01:00:00Z"),
            lambda item: item["code_sha256"].update({"semiconductor_atlas/project_target_population.py": "0" * 64}),
        )
        for mutate in mutations:
            changed = copy.deepcopy(frozen)
            mutate(changed)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                self.validate(changed)

    def test_population_validation_uses_retained_inputs_not_live_source_replay(self):
        frozen = self.freeze()
        self.first_path.unlink()
        self.fixture.before_path.unlink()
        with patch.object(changes, "build_packet", side_effect=AssertionError("must not re-read core or source")):
            self.assertEqual(frozen, self.validate(frozen)[0])


if __name__ == "__main__":
    unittest.main()
