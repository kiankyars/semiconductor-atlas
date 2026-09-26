from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from semiconductor_atlas import ai_critical_alert_review as review
from semiconductor_atlas.ai_critical_changes import _pretty_bytes, write_change_bundle
from tests import test_ai_critical_changes as fixtures


NOW = "2026-09-07T10:00:00Z"
LATER = "2026-09-07T11:00:00Z"


class AICriticalAlertReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixtures.AICriticalChangeDetectionTests.setUpClass()

    def setUp(self) -> None:
        self.fixture = fixtures.AICriticalChangeDetectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.db = self.root / "alerts.sqlite"
        review.initialize_queue(self.db)
        self.prior, self.current = self.fixture._release_pair(mutate_current=self._progress)
        self.bundle, self.admission = self._comparison("comparison", self.prior, self.current)

    @staticmethod
    def _progress(spec):
        fixtures.AICriticalChangeDetectionTests._facility(spec, "Samsung")["lifecycle"]["state"] = "site_preparation"

    def _comparison(self, name, prior, current):
        bundle = self.root / name
        write_change_bundle(prior, current, bundle)
        support = self.root / f"{name}-source-review.json"
        support.write_bytes(_pretty_bytes({"status": "reviewed_fixture_only", "not_real_evidence": True}))
        admission = self.root / f"{name}-admission.json"
        admission.write_bytes(_pretty_bytes({
            "format": review.ADMISSION_FORMAT, "purpose": "alert_review_only",
            "reviewer": "fixture-reviewer", "reviewed_at": "2026-09-07T09:00:00Z",
            "reason": "Test fixture review, not evidence of actual manufacturing activity.",
            "prior_manifest_sha256": review._hash((prior / "manifest.json").read_bytes()),
            "current_manifest_sha256": review._hash((current / "manifest.json").read_bytes()),
            "change_manifest_sha256": review._hash((bundle / "manifest.json").read_bytes()),
            "supporting_reviews": [{"path": support.name, "sha256": review._hash(support.read_bytes())}],
        }))
        return bundle, admission

    def _import(self, *, clock=NOW, bundle=None, prior=None, current=None, admission=None):
        with patch.object(review, "_now", return_value=clock):
            return review.import_bundle(self.db, bundle or self.bundle, prior or self.prior,
                                        current or self.current, admission or self.admission)

    @staticmethod
    def _ref(alert):
        observation = alert["observations"][0]
        claim = observation["after"]
        link = claim["evidence_links"][0]
        return {"bundle_id": observation["bundle_id"], "side": "current", "claim_id": claim["claim_id"],
                "evidence_id": link["evidence_id"], "fragment_sha256": link["fragment_sha256"]}

    def _decide(self, alert, action, *, clock=LATER, refs=None, token=None):
        with patch.object(review, "_now", return_value=clock):
            return review.record_decision(self.db, alert["id"], action=action, reviewer="test-reviewer",
                reason="Explicit fixture decision, not canonical fact acceptance.",
                expected_event_id=token or alert["last_event_id"],
                evidence_refs=refs if refs is not None else ([self._ref(alert)] if action in {"resolve", "retract"} else []))

    def test_import_preserves_full_lineage_and_actual_admission_clock(self):
        result = self._import()
        self.assertTrue(result["imported"])
        self.assertEqual((1, 1, 1), (result["event_count"], result["packet_count"], result["alert_count"]))
        alert = result["alerts"][0]
        self.assertEqual(NOW, alert["first_recorded_at"])
        self.assertEqual("pending", alert["status"])
        self.assertFalse(alert["delivery_eligible"])
        self.assertIsNone(alert["confidence"])
        self.assertEqual("announced", alert["observations"][0]["before"]["value"]["value"])
        self.assertEqual("site_preparation", alert["observations"][0]["after"]["value"]["value"])
        self.assertEqual(0, review.queue_report(self.db, as_of="2026-09-07T09:59:59Z")["alert_count"])
        self.assertEqual(1, review.queue_report(self.db, as_of=NOW)["alert_count"])

    def test_exact_import_is_idempotent(self):
        self._import()
        original = review.export_queue(self.db)
        self.assertFalse(self._import(clock=LATER)["imported"])
        self.assertEqual(original, review.export_queue(self.db))

    def test_explicit_lifecycle_and_each_cutoff_restore_without_source_paths(self):
        alert = self._import()["alerts"][0]
        checkpoints = [(NOW, review.queue_report(self.db, as_of=NOW))]
        for index, action in enumerate(("acknowledge", "resolve", "reopen", "retract"), 1):
            clock = f"2026-09-07T10:0{index}:00Z"
            alert = self._decide(alert, action, clock=clock)
            checkpoints.append((clock, review.queue_report(self.db, as_of=clock)))
        self.assertEqual("retracted", alert["status"])
        exported = review.export_queue(self.db)
        export_path = self.root / "export.json"
        export_path.write_bytes(_pretty_bytes(exported))
        for path in (self.prior, self.current, self.bundle):
            shutil.rmtree(path)
        self.admission.unlink()
        (self.root / "comparison-source-review.json").unlink()
        restored = self.root / "restored.sqlite"
        review.restore_queue(restored, export_path)
        self.assertEqual(exported, review.export_queue(restored))
        self.assertEqual(1, review.verify_queue(restored)["verified_packet_count"])
        for cutoff, expected in checkpoints:
            self.assertEqual(expected, review.queue_report(restored, as_of=cutoff))

    def test_invalid_disposition_and_stale_cas_are_atomic(self):
        alert = self._import()["alerts"][0]
        with self.assertRaisesRegex(ValueError, "bound evidence"):
            self._decide(alert, "resolve", refs=[])
        with self.assertRaisesRegex(ValueError, "only closed"):
            self._decide(alert, "reopen")
        with self.assertRaisesRegex(ValueError, "stale"):
            self._decide(alert, "acknowledge", token="wrong")
        self.assertEqual(1, review.queue_report(self.db)["event_count"])
        closed = self._decide(alert, "resolve")
        with self.assertRaisesRegex(ValueError, "reopen closed"):
            self._decide(closed, "retract")
        self.assertEqual(2, review.queue_report(self.db)["event_count"])

    def test_decision_evidence_must_be_admitted_exact_and_same_facility(self):
        alert = self._import()["alerts"][0]
        for field in ("bundle_id", "claim_id", "evidence_id", "fragment_sha256"):
            ref = self._ref(alert)
            ref[field] = "wrong"
            with self.subTest(field=field), self.assertRaises(ValueError):
                self._decide(alert, "retract", refs=[ref])
        ref = self._ref(alert)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self._decide(alert, "resolve", refs=[ref, ref])
        self.assertEqual(1, review.queue_report(self.db)["event_count"])

    def test_rejects_review_future_knowledge_and_bad_manifest_binding(self):
        original = json.loads(self.admission.read_bytes())
        for field, value in (("reviewed_at", LATER), ("reviewed_at", "2026-08-20T10:00:00Z"),
                             ("current_manifest_sha256", "0" * 64), ("purpose", "delivery")):
            item = copy.deepcopy(original)
            item[field] = value
            self.admission.write_bytes(_pretty_bytes(item))
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self._import()
        self.assertEqual(0, review.queue_report(self.db)["event_count"])

    def test_rejects_corrupt_release_despite_standalone_valid_comparison(self):
        target = self.current / "claims.jsonl"
        target.write_bytes(target.read_bytes() + b"\n")
        with self.assertRaises(ValueError):
            self._import()
        self.assertEqual(0, review.queue_report(self.db)["event_count"])

    def test_malformed_admission_is_rejected_before_any_supporting_path_read(self):
        original = json.loads(self.admission.read_bytes())
        for malformed in ([], None, "not an object", {**original, "supporting_reviews": [None]},
                          {**original, "supporting_reviews": ["abc"]},
                          {**original, "supporting_reviews": [{"path": [], "sha256": "0" * 64}]},
                          {**original, "supporting_reviews": [{"path": "missing", "sha256": False}]}):
            self.admission.write_bytes(_pretty_bytes(malformed))
            with self.subTest(value=malformed), self.assertRaises(ValueError):
                self._import()
        self.assertEqual(0, review.queue_report(self.db)["event_count"])

    def test_blob_size_is_bounded_before_decoding(self):
        with patch.object(review.base64, "b64decode", side_effect=AssertionError("must not decode")):
            for blob in ({"bytes": 1, "sha256": "0" * 64, "base64": "A" * 1000},
                         {"bytes": 20_000_001, "sha256": "0" * 64, "base64": ""},
                         {"bytes": True, "sha256": "0" * 64, "base64": ""}):
                with self.subTest(blob=blob), self.assertRaises(ValueError):
                    review._unblob(blob)

    def test_missing_bound_manifest_is_value_error(self):
        self._import()
        artifact = review.export_queue(self.db)
        event = artifact["events"][0]
        del event["payload"]["inventories"]["prior"]["manifest.json"]
        event["event_id"] = review._hash({key: value for key, value in event.items() if key != "event_id"})
        export = self.root / "missing-manifest.json"
        export.write_bytes(_pretty_bytes(artifact))
        target = self.root / "missing-manifest.sqlite"
        with self.assertRaisesRegex(ValueError, "missing manifest"):
            review.restore_queue(target, export)
        self.assertFalse(target.exists())

    def test_corrupt_portable_export_fails_before_creating_destination(self):
        self._import()
        artifact = review.export_queue(self.db)
        event = artifact["events"][0]
        blob = event["payload"]["inventories"]["current"]["claims.jsonl"]
        blob["base64"] = "YQ=="
        event["event_id"] = review._hash({key: value for key, value in event.items() if key != "event_id"})
        export = self.root / "corrupt.json"
        export.write_bytes(_pretty_bytes(artifact))
        target = self.root / "should-not-exist.sqlite"
        with self.assertRaises(ValueError):
            review.restore_queue(target, export)
        self.assertFalse(target.exists())

    def test_database_is_distinct_append_only_and_new_file_only(self):
        with self.assertRaises(FileExistsError):
            review.initialize_queue(self.db)
        self._import()
        connection = sqlite3.connect(self.db)
        try:
            for query in ("DELETE FROM alert_events", "UPDATE alert_events SET recorded_at='wrong'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(query)
        finally:
            connection.close()
        foreign = self.root / "foreign.sqlite"
        sqlite3.connect(foreign).close()
        with self.assertRaisesRegex(ValueError, "not a supported"):
            review.queue_report(foreign)

    def test_manifest_only_repeat_coalesces_without_erasing_review(self):
        alert = self._import()["alerts"][0]
        resolved = self._decide(alert, "resolve")
        later = self.fixture._write_release("later", self.fixture._spec(
            "change-fixture-later", as_of="2026-08-22", recorded_at="2026-08-22T18:00:00Z", mutate=self._progress))
        bundle, admission = self._comparison("later-comparison", self.prior, later)
        result = self._import(clock="2026-09-07T12:00:00Z", current=later, bundle=bundle, admission=admission)
        self.assertEqual(1, result["alert_count"])
        self.assertEqual(2, len(result["alerts"][0]["observations"]))
        self.assertEqual("resolved", result["alerts"][0]["status"])
        self.assertEqual(resolved["last_event_id"], result["alerts"][0]["last_event_id"])

    def test_real_return_transition_creates_new_episode(self):
        first = self._import()["alerts"][0]
        backward = self.fixture._write_release("backward", self.fixture._spec(
            "change-fixture-backward", as_of="2026-08-22", recorded_at="2026-08-22T18:00:00Z"))
        bundle, admission = self._comparison("backward-comparison", self.current, backward)
        self._import(clock=LATER, prior=self.current, current=backward, bundle=bundle, admission=admission)
        forward = self.fixture._write_release("forward", self.fixture._spec(
            "change-fixture-forward", as_of="2026-08-23", recorded_at="2026-08-23T18:00:00Z", mutate=self._progress))
        bundle, admission = self._comparison("forward-comparison", backward, forward)
        result = self._import(clock="2026-09-07T12:00:00Z", prior=backward, current=forward, bundle=bundle, admission=admission)
        self.assertEqual(3, result["alert_count"])
        forwards = [alert for alert in result["alerts"] if alert["observations"][0]["after"]["value"]["value"] == "site_preparation"]
        self.assertEqual(2, len(forwards))
        self.assertNotEqual(forwards[0]["id"], forwards[1]["id"])
        self.assertEqual(first["id"], forwards[0]["id"])

    def test_exact_same_prior_backfill_coalesces_without_clock_or_status_regression(self):
        later = self.fixture._write_release("backfill-later", self.fixture._spec(
            "fixture-backfill-later", as_of="2026-08-22", recorded_at="2026-08-22T18:00:00Z", mutate=self._progress))
        bundle, admission = self._comparison("backfill-later-comparison", self.prior, later)
        alert = self._import(current=later, bundle=bundle, admission=admission)["alerts"][0]
        resolved = self._decide(alert, "resolve")
        result = self._import(clock="2026-09-07T12:00:00Z")
        self.assertEqual((1, 0), (result["alert_count"], result["open_count"]))
        self.assertEqual(resolved["last_event_id"], result["alerts"][0]["last_event_id"])
        self.assertEqual(2, len(result["alerts"][0]["observations"]))
        self.assertEqual("2026-08-22T18:00:00Z", result["alerts"][0]["observations"][0]["release_recorded_at"])

    def test_later_bound_prior_proves_return_without_separate_reverse_comparison(self):
        for quiet in (False, True):
            with self.subTest(quiet=quiet):
                label = str(quiet).lower()
                self.db = self.root / f"return-{quiet}.sqlite"
                review.initialize_queue(self.db)
                original = self._import()["alerts"][0]
                self._decide(original, "resolve")
                returned = self.fixture._write_release(f"returned-{quiet}", self.fixture._spec(
                    f"fixture-returned-{label}", as_of="2026-08-22", recorded_at="2026-08-22T18:00:00Z"))
                if quiet:
                    steady = self.fixture._write_release("interleaved-quiet", self.fixture._spec(
                        "fixture-interleaved-quiet", as_of="2026-08-23", recorded_at="2026-08-23T18:00:00Z", mutate=self._progress))
                    bundle, admission = self._comparison("interleaved-quiet-comparison", self.current, steady)
                    self._import(clock="2026-09-07T12:00:00Z", prior=self.current, current=steady, bundle=bundle, admission=admission)
                progressed = self.fixture._write_release(f"progressed-{quiet}", self.fixture._spec(
                    f"fixture-progressed-{label}", as_of="2026-08-24", recorded_at="2026-08-24T18:00:00Z", mutate=self._progress))
                bundle, admission = self._comparison(f"returned-progressed-{quiet}", returned, progressed)
                result = self._import(clock="2026-09-07T13:00:00Z", prior=returned, current=progressed, bundle=bundle, admission=admission)
                self.assertEqual((2, 1), (result["alert_count"], result["open_count"]))
                self.assertEqual(["resolved", "pending"], [alert["status"] for alert in result["alerts"]])

    def test_later_capability_proposal_retains_prior_from_its_original_series(self):
        def mutate(spec):
            facility = self.fixture._facility(spec, "Amkor")
            facility["lifecycle"]["state"] = "tools_installing"
            facility["lifecycle"]["as_of"] = "2026-08-21"
            facility["capabilities"][0]["readiness"] = "equipment_installed"
            facility["capabilities"][0]["valid_from"] = "2026-08-21"

        current = self.fixture._write_release("capability", self.fixture._spec(
            "fixture-capability", as_of="2026-08-21", recorded_at="2026-08-21T18:00:00Z", mutate=mutate))
        bundle, admission = self._comparison("capability-comparison", self.prior, current)
        result = self._import(current=current, bundle=bundle, admission=admission)
        alert = next(alert for alert in result["alerts"] if alert["rule_id"] == "capability_readiness_change")
        observation = alert["observations"][0]
        self.assertEqual("2024-07-24", observation["before"]["valid_from"])
        self.assertEqual("planned", observation["before"]["value"]["readiness"])
        self.assertEqual("2026-08-21", observation["after"]["valid_from"])
        self.assertEqual("equipment_installed", observation["after"]["value"]["readiness"])
        for side, field in (("prior", "before"), ("current", "after")):
            self.assertEqual(observation["proposal"][f"{side}_claim_id"], observation[field]["claim_id"])
            self.assertEqual(observation["proposal"]["evidence_lineage"][side]["evidence_links"], observation[field]["evidence_links"])
        self.assertEqual(1, review.verify_queue(self.db)["verified_packet_count"])

    def test_quiet_comparison_does_not_close_outstanding_review(self):
        first = self._import()["alerts"][0]
        later = self.fixture._write_release("quiet", self.fixture._spec(
            "change-fixture-quiet", as_of="2026-08-22", recorded_at="2026-08-22T18:00:00Z", mutate=self._progress))
        bundle, admission = self._comparison("quiet-comparison", self.current, later)
        result = self._import(clock=LATER, prior=self.current, current=later, bundle=bundle, admission=admission)
        self.assertEqual((1, 1), (result["alert_count"], result["open_count"]))
        self.assertEqual(first, result["alerts"][0])


if __name__ == "__main__":
    unittest.main()
