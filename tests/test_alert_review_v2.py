from __future__ import annotations

import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

from semiconductor_atlas import ai_critical_alert_review as legacy
from semiconductor_atlas import alert_review_v2 as review
from semiconductor_atlas import project_target_changes as projects
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from tests import test_ai_critical_alert_review as legacy_fixtures
from tests import test_project_target_review as project_fixtures


GENERATED = "2026-09-07T10:30:00Z"
REVIEWED = "2026-09-07T11:00:00Z"
ADMITTED = "2026-09-07T12:00:00Z"
LATER = "2026-09-07T13:00:00Z"


class AlertReviewV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        legacy_fixtures.AICriticalAlertReviewTests.setUpClass()

    def setUp(self):
        self.fixture = project_fixtures.ProjectTargetReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.acceptance = self.fixture.accept()
        self.packet_path = self.root / "project-change.json"
        self.admission_path = self.root / "project-admission.json"
        self.db = self.root / "unified.sqlite"
        review.initialize_queue(self.db)
        self.packet = self.build_packet()
        self.write_admission()

    def build_packet(self, *, clock=GENERATED):
        with patch.object(projects, "_now", return_value=clock):
            packet = projects.build_packet(self.fixture.connection, self.fixture.review_path,
                reference_root=self.root, source_queue=self.fixture.queue)
        self.packet_path.write_bytes(_pretty_bytes(packet))
        return packet

    def write_admission(self, *, clock=REVIEWED, **overrides):
        item = {"format": review.ADMISSION_FORMAT, "purpose": "alert_review_only",
            "reviewer": "fixture proposal reviewer", "reviewed_at": clock,
            "reason": "Review a source-stated target change, not attained manufacturing.",
            "packet_sha256": legacy._hash(self.packet_path.read_bytes()),
            "expected_head_event_id": review.queue_report(self.db)["head_event_id"], **overrides}
        self.admission_path.write_bytes(_pretty_bytes(item))
        return item

    def import_project(self, *, clock=ADMITTED):
        with patch.object(review, "_now", return_value=clock):
            return review.import_project_packet(self.db, self.packet_path, self.admission_path)

    def legacy_fixture(self):
        fixture = legacy_fixtures.AICriticalAlertReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def import_legacy(self, fixture, *, clock="2026-09-07T10:00:00Z", **overrides):
        with patch.object(review, "_now", return_value=clock):
            return review.import_bundle(self.db, overrides.get("bundle", fixture.bundle),
                overrides.get("prior", fixture.prior), overrides.get("current", fixture.current),
                overrides.get("admission", fixture.admission))

    @staticmethod
    def project_ref(alert, *, side="after"):
        observation = alert["observations"][0]
        claim = observation[side]
        link = claim["evidence"][0]
        return {"packet_id": observation["packet_id"], "side": side, "claim_id": claim["id"],
            "evidence_id": link["evidence_id"], "fragment_sha256": link["fragment_sha256"]}

    def decide(self, alert, action, *, clock=LATER, refs=(), token=None):
        with patch.object(review, "_now", return_value=clock):
            return review.record_decision(self.db, alert["id"], action=action,
                reviewer="fixture disposition reviewer", reason="Review handling only, never claim acceptance.",
                expected_event_id=alert["last_event_id"] if token is None else token, evidence_refs=refs)

    @staticmethod
    def rechain(events):
        previous = None
        for index, event in enumerate(events, 1):
            event.update(sequence=index, previous_event_id=previous)
            event["event_id"] = legacy._hash({key: value for key, value in event.items() if key != "event_id"})
            previous = event["event_id"]

    def test_project_packet_uses_actual_admission_and_preserves_unknowns(self):
        source_before = self.fixture.connection.total_changes
        result = self.import_project()
        self.assertTrue(result["imported"])
        self.assertEqual((1, 1, 1, 0), (result["event_count"], result["packet_count"],
            result["alert_count"], result["delivery_eligible_count"]))
        alert = result["alerts"][0]
        self.assertEqual("source_native_project", alert["origin"])
        self.assertEqual(self.acceptance["entity_id"], alert["subject_entity_id"])
        self.assertEqual(ADMITTED, alert["first_recorded_at"])
        self.assertIsNone(alert["confidence"])
        self.assertFalse(alert["delivery_eligible"])
        self.assertEqual("pending", alert["status"])
        before, after = alert["observations"][0]["before"], alert["observations"][0]["after"]
        self.assertEqual("2028", before["value"]["date_literal"])
        self.assertEqual("second half of 2027", after["value"]["date_literal"])
        for claim in (before, after):
            self.assertEqual("source_statement", claim["claim_kind"])
            self.assertEqual("expected", claim["value"]["status"])
            self.assertIsNone(claim["valid_from"])
            self.assertIsNone(claim["value"]["date_base"])
            self.assertIsNone(claim["confidence"])
        self.assertEqual(source_before, self.fixture.connection.total_changes)
        self.assertEqual(0, review.queue_report(self.db, as_of=REVIEWED)["alert_count"])
        self.assertEqual(1, review.queue_report(self.db, as_of=ADMITTED)["alert_count"])

    def test_exact_repeat_retains_original_admission_clock_and_decision(self):
        alert = self.import_project()["alerts"][0]
        acknowledged = self.decide(alert, "acknowledge")
        before = review.export_queue(self.db)
        result = self.import_project(clock="2026-09-07T14:00:00Z")
        self.assertFalse(result["imported"])
        self.assertEqual(before, review.export_queue(self.db))
        self.assertEqual(ADMITTED, result["alerts"][0]["first_recorded_at"])
        self.assertEqual(acknowledged["last_event_id"], result["alerts"][0]["last_event_id"])

    def test_rebuilt_same_comparison_never_creates_a_duplicate_episode(self):
        self.import_project()
        before = review.export_queue(self.db)
        self.build_packet(clock="2026-09-07T12:30:00Z")
        self.write_admission(clock="2026-09-07T12:40:00Z")
        self.assertFalse(self.import_project(clock=LATER)["imported"])
        self.assertEqual(before, review.export_queue(self.db))

    def test_reaffirmation_is_retained_without_creating_alert(self):
        fixture = self.fixture
        body = fixture.after_body + b"<!-- fixture earlier observation -->"
        fixture.before_path.write_bytes(body)
        fixture.manifest["inputs"][0].update(sha256=legacy._hash(body), bytes=len(body))
        fixture.write(fixture.manifest_path, fixture.manifest)
        fixture.review["before"].update(body=fixture.binding(fixture.before_path),
            manifest=fixture.binding(fixture.manifest_path), spans=fixture.spans(body),
            target=copy.deepcopy(fixture.review["after"]["target"]))
        fixture.review["reviewed_at"] = fixture.now()
        fixture.write_review()
        fixture.accept()
        packet = self.build_packet()
        self.assertEqual("reaffirmation", packet["change"]["classification"])
        self.assertEqual([], packet["proposals"])
        self.write_admission()
        result = self.import_project()
        self.assertEqual((1, 1, 0), (result["event_count"], result["packet_count"], result["alert_count"]))
        self.assertEqual(1, len(result["project_packets"]))
        self.assertFalse(self.import_project(clock=LATER)["imported"])

    def test_mixed_legacy_project_events_and_legacy_decisions_keep_global_chain(self):
        fixture = self.legacy_fixture()
        legacy_alert = self.import_legacy(fixture)["alerts"][0]
        prefix = review.export_queue(self.db)["events"]
        self.write_admission()
        result = self.import_project()
        project_alert = next(row for row in result["alerts"] if row["origin"] == "source_native_project")
        resolved = self.decide(legacy_alert, "resolve", clock="2026-09-07T12:01:00Z",
            refs=[fixture._ref(legacy_alert)])
        project_closed = self.decide(project_alert, "resolve", clock="2026-09-07T12:02:00Z",
            refs=[self.project_ref(project_alert)])
        later = fixture.fixture._write_release("later-in-unified", fixture.fixture._spec(
            "later-unified-facility-observation", as_of="2026-08-22", recorded_at="2026-08-22T18:00:00Z",
            mutate=fixture._progress))
        bundle, admission = fixture._comparison("later-unified-comparison", fixture.prior, later)
        self.import_legacy(fixture, clock=LATER, current=later, bundle=bundle, admission=admission)
        exported = review.export_queue(self.db)
        events = exported["events"]
        self.assertEqual(prefix, events[:len(prefix)])
        self.assertEqual([legacy.RULE_VERSION, review.RULE_VERSION, legacy.RULE_VERSION, review.RULE_VERSION, legacy.RULE_VERSION],
            [event["payload"]["rule_version"] for event in events])
        legacy._validate_events(events)
        result = review.verify_queue(self.db)
        self.assertEqual((5, 3, 2, 0), (result["event_count"], result["packet_count"], result["alert_count"], result["open_count"]))
        self.assertEqual({resolved["id"], project_closed["id"]}, {row["id"] for row in result["alerts"]})
        retained_legacy = next(row for row in result["alerts"] if row["origin"] == "baseline_facility")
        self.assertEqual("resolved", retained_legacy["status"])
        self.assertEqual(2, len(retained_legacy["observations"]))
        self.assertEqual(resolved["last_event_id"], retained_legacy["last_event_id"])
        self.assertEqual(0, result["delivery_eligible_count"])
        self.assertTrue(all(not row["delivery_eligible"] for row in result["alerts"]))

    def test_restore_v1_preserves_exact_prefix_and_original_queue(self):
        fixture = self.legacy_fixture()
        first = fixture._import()["alerts"][0]
        fixture._decide(first, "acknowledge", clock="2026-09-07T10:05:00Z")
        original = legacy.export_queue(fixture.db)
        exported = self.root / "v1.json"
        exported.write_bytes(_pretty_bytes(original))
        migrated = self.root / "migrated.sqlite"
        review.restore_queue(migrated, exported)
        self.db = migrated
        self.assertEqual(original["events"], review.export_queue(self.db)["events"])
        self.write_admission()
        self.import_project()
        self.assertEqual(original["events"], review.export_queue(self.db)["events"][:2])
        self.assertEqual(original, legacy.export_queue(fixture.db))
        with self.assertRaisesRegex(ValueError, "not a supported"):
            legacy.queue_report(self.db)
        with self.assertRaisesRegex(ValueError, "not a supported"):
            review.queue_report(fixture.db)

    def test_each_mixed_history_cutoff_restores_without_local_inputs(self):
        fixture = self.legacy_fixture()
        old = self.import_legacy(fixture)["alerts"][0]
        self.write_admission()
        new = next(row for row in self.import_project()["alerts"] if row["origin"] == "source_native_project")
        self.decide(old, "acknowledge", clock="2026-09-07T12:01:00Z")
        self.decide(new, "resolve", clock="2026-09-07T12:02:00Z", refs=[self.project_ref(new)])
        checkpoints = ("2026-09-07T09:00:00Z", "2026-09-07T10:00:00Z", ADMITTED,
            "2026-09-07T12:01:00Z", "2026-09-07T12:02:00Z")
        retained = [(cutoff, review.queue_report(self.db, as_of=cutoff), review.export_queue(self.db, as_of=cutoff))
            for cutoff in checkpoints]
        self.packet_path.unlink()
        self.admission_path.unlink()
        self.fixture.before_path.unlink()
        self.fixture.review_path.unlink()
        for index, (cutoff, expected, artifact) in enumerate(retained):
            path = self.root / f"cutoff-{index}.json"
            path.write_bytes(_pretty_bytes(artifact))
            restored = self.root / f"cutoff-{index}.sqlite"
            review.restore_queue(restored, path)
            self.assertEqual(artifact, review.export_queue(restored))
            self.assertEqual(expected, review.queue_report(restored, as_of=cutoff))

    def test_admission_requires_exact_packet_review_clock_and_empty_head_token(self):
        original = json.loads(self.admission_path.read_bytes())
        invalid = (("purpose", "delivery"), ("packet_sha256", "0" * 64),
            ("reviewed_at", "2026-09-07T10:00:00Z"), ("reviewed_at", LATER),
            ("expected_head_event_id", "not-empty"), ("expected_head_event_id", 0),
            ("expected_head_event_id", []), ("reviewer", ""))
        for field, value in invalid:
            self.admission_path.write_bytes(_pretty_bytes({**original, field: value}))
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.import_project()
            self.assertEqual([], review.export_queue(self.db)["events"])

    def test_stale_head_after_review_is_atomic(self):
        fixture = self.legacy_fixture()
        self.import_legacy(fixture)
        before = review.export_queue(self.db)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.import_project()
        self.assertEqual(before, review.export_queue(self.db))

    def test_head_change_after_prevalidation_is_detected_inside_admission_transaction(self):
        fixture = self.legacy_fixture()
        original = review._project_packet
        calls = 0
        def add_concurrent_legacy_event(payload, clock):
            nonlocal calls
            result = original(payload, clock)
            calls += 1
            if calls == 1:
                self.import_legacy(fixture, clock="2026-09-07T11:30:00Z")
            return result
        with patch.object(review, "_project_packet", side_effect=add_concurrent_legacy_event):
            with self.assertRaisesRegex(ValueError, "stale"):
                self.import_project()
        result = review.queue_report(self.db)
        self.assertEqual(1, result["event_count"])
        self.assertEqual([], result["project_packets"])
        self.assertEqual("baseline_facility", result["alerts"][0]["origin"])

    def test_head_must_already_exist_at_review_time(self):
        fixture = self.legacy_fixture()
        self.import_legacy(fixture, clock="2026-09-07T11:30:00Z")
        self.write_admission(clock=REVIEWED)
        before = review.export_queue(self.db)
        with self.assertRaisesRegex(ValueError, "future ledger head"):
            self.import_project()
        self.assertEqual(before, review.export_queue(self.db))

    def test_bad_project_evidence_and_stale_decisions_are_atomic(self):
        alert = self.import_project()["alerts"][0]
        baseline = review.export_queue(self.db)
        ref = self.project_ref(alert)
        for field in ref:
            wrong = {**ref, field: "wrong"}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.decide(alert, "resolve", refs=[wrong])
            self.assertEqual(baseline, review.export_queue(self.db))
        for action, refs, token in (("resolve", [], None), ("retract", [ref, ref], None),
                ("reopen", [], None), ("acknowledge", [], "stale")):
            with self.subTest(action=action, refs=refs, token=token), self.assertRaises(ValueError):
                self.decide(alert, action, refs=refs, token=token)
            self.assertEqual(baseline, review.export_queue(self.db))
        closed = self.decide(alert, "resolve", refs=[ref])
        with self.assertRaisesRegex(ValueError, "reopen closed"):
            self.decide(closed, "retract", refs=[ref])
        reopened = self.decide(closed, "reopen", clock="2026-09-07T13:01:00Z")
        final = self.decide(reopened, "retract", clock="2026-09-07T13:02:00Z", refs=[self.project_ref(alert, side="before")])
        self.assertEqual("retracted", final["status"])
        self.assertFalse(final["delivery_eligible"])
        self.assertEqual(4, review.queue_report(self.db)["event_count"])

    def test_legacy_and_project_reference_shapes_cannot_be_interchanged(self):
        fixture = self.legacy_fixture()
        old = self.import_legacy(fixture)["alerts"][0]
        self.write_admission()
        new = next(row for row in self.import_project()["alerts"] if row["origin"] == "source_native_project")
        before = review.export_queue(self.db)
        for alert, ref in ((new, fixture._ref(old)), (old, self.project_ref(new))):
            with self.subTest(origin=alert["origin"]), self.assertRaises(ValueError):
                self.decide(alert, "resolve", refs=[ref])
        self.assertEqual(before, review.export_queue(self.db))

    def test_malformed_project_reference_fields_raise_domain_errors_atomically(self):
        alert = self.import_project()["alerts"][0]
        before = review.export_queue(self.db)
        original = self.project_ref(alert)
        for field in original:
            for value in (None, [], {}, False):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.decide(alert, "resolve", refs=[{**original, field: value}])
                self.assertEqual(before, review.export_queue(self.db))

    def test_resealed_tampered_project_payload_fails_before_restore_creation(self):
        self.import_project()
        original = review.export_queue(self.db)
        for name in ("packet", "admission", "head", "rule"):
            artifact = copy.deepcopy(original)
            event = artifact["events"][0]
            if name == "packet":
                packet = json.loads(legacy._unblob(event["payload"]["packet"]))
                packet["change"]["classification"] = "reaffirmation"
                packet["packet_id"] = legacy._hash({key: value for key, value in packet.items() if key != "packet_id"})
                raw = _pretty_bytes(packet)
                event["payload"].update(packet=legacy._blob(raw), packet_id=legacy._hash(raw))
                admission = json.loads(legacy._unblob(event["payload"]["admission"]))
                admission["packet_sha256"] = legacy._hash(raw)
                event["payload"]["admission"] = legacy._blob(_pretty_bytes(admission))
            elif name == "admission":
                admission = json.loads(legacy._unblob(event["payload"]["admission"]))
                admission["purpose"] = "delivery"
                event["payload"]["admission"] = legacy._blob(_pretty_bytes(admission))
            elif name == "head":
                admission = json.loads(legacy._unblob(event["payload"]["admission"]))
                admission["expected_head_event_id"] = "0" * 64
                event["payload"]["admission"] = legacy._blob(_pretty_bytes(admission))
            else:
                event["payload"]["rule_version"] = legacy.RULE_VERSION
            self.rechain(artifact["events"])
            exported = self.root / f"tampered-{name}.json"
            exported.write_bytes(_pretty_bytes(artifact))
            target = self.root / f"tampered-{name}.sqlite"
            with self.subTest(name=name), self.assertRaises(ValueError):
                review.restore_queue(target, exported)
            self.assertFalse(target.exists())

    def test_duplicate_comparison_in_resealed_export_is_not_a_new_episode(self):
        self.import_project()
        artifact = review.export_queue(self.db)
        duplicate = copy.deepcopy(artifact["events"][0])
        duplicate["recorded_at"] = LATER
        admission = json.loads(legacy._unblob(duplicate["payload"]["admission"]))
        admission.update(expected_head_event_id=artifact["events"][0]["event_id"], reviewed_at=LATER)
        duplicate["payload"]["admission"] = legacy._blob(_pretty_bytes(admission))
        artifact["events"].append(duplicate)
        self.rechain(artifact["events"])
        path = self.root / "duplicate.json"
        path.write_bytes(_pretty_bytes(artifact))
        target = self.root / "duplicate.sqlite"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            review.restore_queue(target, path)
        self.assertFalse(target.exists())

    def test_malformed_restore_discriminator_is_domain_error_without_destination(self):
        path = self.root / "malformed-export.json"
        target = self.root / "malformed-export.sqlite"
        for value in (None, [], {}, False, 2):
            path.write_bytes(_pretty_bytes({"format": value, "events": []}))
            with self.subTest(value=value), self.assertRaises(ValueError):
                review.restore_queue(target, path)
            self.assertFalse(target.exists())

    def test_snapshot_admission_uses_validated_private_bytes(self):
        original = review._project_packet
        calls = 0
        def mutate_after_validation(payload, clock):
            nonlocal calls
            result = original(payload, clock)
            calls += 1
            if calls == 1:
                self.packet_path.write_bytes(b"not the validated packet")
                self.admission_path.write_bytes(b"not the validated review")
            return result
        with patch.object(review, "_project_packet", side_effect=mutate_after_validation):
            result = self.import_project()
        self.assertTrue(result["imported"])
        payload = review.export_queue(self.db)["events"][0]["payload"]
        self.assertEqual(self.packet, json.loads(legacy._unblob(payload["packet"])))
        self.assertEqual(REVIEWED, json.loads(legacy._unblob(payload["admission"]))["reviewed_at"])

    def test_append_only_new_file_and_hashchain_guards(self):
        self.import_project()
        with self.assertRaises(FileExistsError):
            review.initialize_queue(self.db)
        connection = sqlite3.connect(self.db)
        try:
            for query in ("DELETE FROM alert_events", "UPDATE alert_events SET recorded_at='wrong'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(query)
        finally:
            connection.close()
        artifact = review.export_queue(self.db)
        artifact["events"][0]["recorded_at"] = LATER
        path = self.root / "broken-chain.json"
        path.write_bytes(_pretty_bytes(artifact))
        target = self.root / "broken-chain.sqlite"
        with self.assertRaisesRegex(ValueError, "chain|chronology"):
            review.restore_queue(target, path)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
