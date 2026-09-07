from __future__ import annotations

import copy
import json
import sqlite3
import os
import io
from contextlib import redirect_stdout
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from semiconductor_atlas import curated_capture as capture, curated_review, database, models, cli
from semiconductor_atlas import project_target_review as targets, repository, release
from tests import test_curated_capture


class ProjectTargetReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_curated_capture.CuratedCaptureTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.tick = datetime(2026, 9, 7, 4, tzinfo=UTC)
        for module in (capture, curated_review, targets):
            mocked = patch.object(module, "_now", side_effect=self.now)
            mocked.start(); self.addCleanup(mocked.stop)
        self.source_url = "https://www.nist.gov/chips/tsmc-arizona-phoenix"
        self.access_path = self.root / "review.json"
        plan = self.fixture.plan
        for row in plan["policies"]:
            row["url"] = row["url"].replace("https://example.org", "https://www.nist.gov")
        plan["documents"][0].update(url=self.source_url, scope="Three-fab project; monitoring route is not canonical identity.")
        self.access = {"reviewed_at": plan["reviewed_at"], "reviewer": "fixture access reviewer",
            "decision": "approve_exact_nist_documents_for_source_text_review_only",
            "documents": [{"url": self.source_url, "facility_key": plan["checked_facility_key"], "scope": plan["documents"][0]["scope"]}],
            "acquisition_boundary": {key: False for key in ("claim_acceptance", "delivery_eligible", "raw_redistribution")}}
        self.write(self.access_path, self.access)
        plan["review_record"] = self.binding(self.access_path)
        self.before_body = (b"<h1>Project Update</h1><p>TSMC Arizona is on track, with production beginning in the second fab in 2028.</p>"
            b"<table><td><strong>Project Timeline</strong></td><td><strong>Fab 2</strong>: Expected to begin production in 2028</td></table>")
        self.after_body = self.before_body.replace(b"production beginning in the second fab in 2028", b"production in the second fab targeted for the second half in 2027").replace(b"production in 2028", b"production in second half of 2027")
        self.fixture.bodies["document"] = self.after_body
        self.packet, _ = self.fixture.run_capture()
        self.queue = self.root / "queue.sqlite"
        curated_review.initialize_queue(self.queue)
        imported = curated_review.import_capture(self.queue, self.packet)
        candidate = curated_review.queue_report(self.queue)["candidates"][0]
        self.text_review_path = self.root / "text-review.json"
        self.text_review = {"reviewed_at": self.now(), "reviewer": "fixture source reviewer",
            "decision": "handoff_reviewed_text_version_for_separate_claim_review",
            "candidate_id": candidate["id"], "expected_event_id": candidate["last_event_id"],
            "facility_key": candidate["facility_key"], "claim_acceptance": False, "baseline_modified": False, "delivery_eligible": False,
            "source_capture": {"manifest_sha256": imported["run_id"], "body_sha256": capture._sha(self.after_body),
                "normalized_sha256": candidate["current_text_sha256"], "normalization": "html_visible_text_v1", "retrieved_at": candidate["first_seen_at"]}}
        self.write(self.text_review_path, self.text_review)
        candidate = curated_review.record_decision(self.queue, candidate["id"], action="handoff", reviewer="fixture source reviewer",
            expected_event_id=candidate["last_event_id"], reason="Separate Fab 2 target review", evidence_ref=targets._ref(self.binding(self.text_review_path)))
        self.events_path = self.root / "events.json"
        self.write(self.events_path, curated_review.export_events(self.queue))
        self.before_path = self.root / "old.html"
        self.before_path.write_bytes(self.before_body)
        self.manifest_path = self.root / "manifest.json"
        self.manifest = {"format": "semiconductor-atlas-source-inputs-v1", "retrieved_at": "2026-07-18T01:54:47Z",
            "retrieval_timestamp_basis": "conservative corrected batch completion time", "inputs": [{"url": self.source_url,
                "path": "old.html", "sha256": capture._sha(self.before_body), "bytes": len(self.before_body)}]}
        self.write(self.manifest_path, self.manifest)
        self.review = {"format": targets.REVIEW_FORMAT, "reviewed_at": self.now(), "reviewer": "fixture claim reviewer",
            "decision": targets.DECISION, "source_url": self.source_url,
            "project": {"label": "Fab 2, second TSMC Arizona fab", "source_native_subject": "Fab 2", "narrative_subject": "second fab",
                "scope": "Source-native planned second fab; not first-fab operating state", "canonical_facility_assignment": None},
            "before": {"body": self.binding(self.before_path), "manifest": self.binding(self.manifest_path),
                "retrieved_at": self.manifest["retrieved_at"], "retrieval_timestamp_basis": self.manifest["retrieval_timestamp_basis"],
                "target": {"literal": "2028", "precision": "year", "date_low": "2028-01-01", "date_high": "2028-12-31"}, "spans": self.spans(self.before_body)},
            "after": {"body": self.binding(self.packet / "responses/document.body"), "retrieved_at": self.text_review["source_capture"]["retrieved_at"],
                "target": {"literal": "second half of 2027", "precision": "half_year", "date_low": "2027-07-01", "date_high": "2027-12-31"}, "spans": self.spans(self.after_body)},
            "source": {"events": self.binding(self.events_path), "candidate_id": candidate["id"], "expected_event_id": candidate["last_event_id"],
                "run_id": imported["run_id"], "document_id": "document", "plan_sha256": capture._sha((self.packet / "plan.json").read_bytes()),
                "text_sha256": candidate["current_text_sha256"]}, "access_review": self.binding(self.access_path),
            "source_review": self.binding(self.text_review_path), "rationale": "Reviewed revision to source-stated period only", "boundaries": copy.deepcopy(targets.BOUNDARIES)}
        self.review_path = self.root / "claim-review.json"
        self.write_review()
        self.connection, _ = database.initialize(self.root / "core.sqlite")
        self.addCleanup(self.connection.close)

    def now(self):
        self.tick += timedelta(seconds=1)
        return self.tick.isoformat().replace("+00:00", "Z")

    def write(self, path, value):
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    def binding(self, path):
        return {"path": path.relative_to(self.root).as_posix(), "sha256": capture._sha(path.read_bytes())}

    def write_review(self):
        self.write(self.review_path, self.review)

    def spans(self, body):
        result = []
        for role, opening, ending in (("narrative", b"<p>", b"</p>"), ("timeline", b"<table>", b"</table>")):
            start, end = body.index(opening), body.index(ending) + len(ending)
            result.append({"role": role, "start": start, "end": end, "sha256": capture._sha(body[start:end]), "locator": role + " fixture"})
        return result

    def accept(self):
        return targets.accept_review(self.connection, self.review_path, reference_root=self.root, source_queue=self.queue)

    def assert_empty(self):
        for table in ("sources", "ingestion_runs", "entities", "source_records", "claim_versions", "claim_evidence"):
            self.assertEqual(0, self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], table)

    def reject(self):
        self.write_review()
        with self.assertRaises(ValueError):
            self.accept()
        self.assert_empty()

    def test_accepts_two_unknown_effective_source_claims(self):
        result = self.accept()
        self.assertFalse(result["replayed"])
        self.assertEqual([], repository.validate_database(self.connection))
        claims = list(self.connection.execute("SELECT * FROM claim_versions"))
        self.assertEqual(2, len(claims))
        self.assertEqual({result["admitted_at"]}, {row["recorded_at"] for row in claims})
        self.assertTrue(all(row["valid_from"] is None and row["confidence"] is None and row["claim_kind"] == "source_statement" for row in claims))
        values = list(self.connection.execute("SELECT * FROM milestone_values"))
        self.assertEqual({"year", "half_year"}, {row["date_precision"] for row in values})
        self.assertTrue(all(row["date_base"] is None and row["status"] == "expected" for row in values))
        self.assertEqual(4, self.connection.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0])
        self.assertEqual([], repository.current_claims(self.connection, as_of="2026-09-07", recorded_at=result["admitted_at"]))
        self.assertEqual(2, len(repository.known_source_claims(self.connection, recorded_at=result["admitted_at"])))
        self.assertEqual([], repository.known_source_claims(self.connection, recorded_at=self.review["reviewed_at"]))

    def test_exact_replay_performs_no_target_database_writes(self):
        first = self.accept()
        changes = self.connection.total_changes
        self.connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) else sqlite3.SQLITE_OK)
        replay = self.accept()
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["admitted_at"], replay["admitted_at"])
        self.assertEqual(changes, self.connection.total_changes)

    def test_old_access_approval_cannot_accept_claims(self):
        self.review["decision"] = "approve_exact_nist_documents_for_source_text_review_only"
        self.reject()

    def test_scope_and_boundary_fail_closed(self):
        original = copy.deepcopy(self.review)
        for key, value in (("canonical_facility_assignment", "tsmc:fab21-arizona"), ("source_native_subject", "Fab 1"), ("narrative_subject", "first fab")):
            with self.subTest(key=key):
                self.review = copy.deepcopy(original)
                self.review["project"][key] = value
                self.reject()
        self.review = copy.deepcopy(original)
        self.review["boundaries"]["manufacturing_attainment"] = True
        self.reject()

    def test_period_literal_precision_and_bounds_must_agree(self):
        original = copy.deepcopy(self.review)
        for key, value in (("literal", "2026"), ("date_low", "2027-01-01"), ("precision", "day"), ("date_high", "2027-11-30")):
            with self.subTest(key=key):
                self.review = copy.deepcopy(original)
                self.review["after"]["target"][key] = value
                self.reject()

    def test_hash_and_byte_span_mutation_rejected(self):
        self.before_path.write_bytes(self.before_body + b" ")
        self.reject()

    def test_rehashed_body_with_unsupported_target_rejected(self):
        self.before_path.write_bytes(self.before_body.replace(b"2028", b"2029"))
        self.review["before"]["body"] = self.binding(self.before_path)
        self.reject()

    def test_missing_narrative_or_duplicate_timeline_rejected(self):
        self.review["after"]["spans"][0] = copy.deepcopy(self.review["after"]["spans"][1])
        self.reject()

    def test_stale_post_handoff_cas_rejected(self):
        self.review["source"]["expected_event_id"] = self.text_review["expected_event_id"]
        self.reject()

    def test_current_queue_must_match_frozen_review_before_acceptance(self):
        curated_review.record_decision(self.queue, self.review["source"]["candidate_id"], action="reopen", reviewer="fixture",
            reason="additional review required", expected_event_id=self.review["source"]["expected_event_id"])
        self.reject()

    def test_historical_replay_allows_later_queue_events_without_reacceptance(self):
        first = self.accept()
        curated_review.record_decision(self.queue, self.review["source"]["candidate_id"], action="reopen", reviewer="fixture",
            reason="new pending scope work", expected_event_id=self.review["source"]["expected_event_id"])
        replay = self.accept()
        self.assertEqual(first["admitted_at"], replay["admitted_at"])
        self.assertEqual(0, replay["database_writes"])

    def test_claim_review_cannot_predate_source_handoff(self):
        self.review["reviewed_at"] = self.text_review["reviewed_at"]
        self.reject()

    def test_old_corrected_batch_basis_is_bound(self):
        self.review["before"]["retrieval_timestamp_basis"] = "exact source response time"
        self.reject()

    def test_source_text_approval_must_explicitly_handoff(self):
        self.text_review["decision"] = "dismiss_reviewed_text_version_without_baseline_revision"
        self.write(self.text_review_path, self.text_review)
        self.review["source_review"] = self.binding(self.text_review_path)
        self.reject()

    def test_final_input_mutation_rolls_back_entire_admission(self):
        original = targets._populate
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            self.before_path.write_bytes(self.before_body + b" ")
            return result
        with patch.object(targets, "_populate", side_effect=mutate):
            with self.assertRaises(ValueError):
                self.accept()
        self.assert_empty()

    def test_second_claim_failure_rolls_back_first_claim_and_lineage(self):
        original = repository.insert_claim
        calls = 0
        def fail(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("fixture second claim rejection")
            return original(*args, **kwargs)
        with patch.object(repository, "insert_claim", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "second claim"):
                self.accept()
        self.assert_empty()

    def test_future_review_and_backwards_observation_rejected(self):
        original = copy.deepcopy(self.review)
        self.review["reviewed_at"] = "2099-01-01T00:00:00Z"
        self.reject()
        self.review = original
        self.review["before"]["retrieved_at"] = self.review["after"]["retrieved_at"]
        self.reject()

    def test_malformed_extra_fields_and_non_boolean_boundaries_rejected(self):
        self.review["boundaries"]["capacity_claim"] = 0
        self.reject()
        self.review["boundaries"]["capacity_claim"] = False
        self.review["admitted_at"] = self.now()
        self.reject()

    def test_schema_four_database_not_silently_migrated(self):
        old, _ = database.initialize(":memory:", target_version=4)
        try:
            with self.assertRaisesRegex(ValueError, "schema 5"):
                targets.accept_review(old, self.review_path, reference_root=self.root, source_queue=self.queue)
            self.assertEqual(4, database.schema_version(old))
        finally:
            old.close()

    def test_release_retains_actual_admission_timestamp_basis(self):
        result = self.accept()
        output = self.root / "release"
        release.write_release(self.connection, output, as_of="2026-09-07", recorded_at=result["admitted_at"])
        inputs = json.loads((output / "source_inputs.json").read_bytes())
        self.assertEqual(2, len(inputs))
        self.assertEqual({"actual_database_admission"}, {row["acceptance_timestamp_basis"] for row in inputs})
        self.assertEqual({result["admitted_at"]}, {row["database_accepted_at"] for row in inputs})
        observations = [json.loads(line) for line in (output / "source_observations.jsonl").read_text().splitlines()]
        self.assertEqual({result["admitted_at"]}, {row["database_accepted_at"] for row in observations})

    def test_release_during_validation_does_not_leak_later_admission(self):
        result = self.accept()
        started = self.connection.execute("SELECT started_at FROM ingestion_runs WHERE id=?", (result["run_id"],)).fetchone()[0]
        output = self.root / "during-validation"
        manifest = release.write_release(self.connection, output, as_of="2026-09-07", recorded_at=started)
        for key in ("claims", "source_claims", "claim_history", "entities", "source_documents", "source_observations"):
            self.assertEqual(0, manifest[key], key)
        self.assertEqual([], json.loads((output / "source_inputs.json").read_bytes()))
        self.assertEqual(b"", (output / "source_observations.jsonl").read_bytes())
        coverage = json.loads((output / "coverage.json").read_bytes())
        self.assertNotIn("reviewed-source-project-targets", json.dumps(coverage))

    def test_microsecond_before_admission_is_still_unknown(self):
        self.tick += timedelta(microseconds=123456)
        result = self.accept()
        before = (datetime.fromisoformat(result["admitted_at"].replace("Z", "+00:00"))
                  - timedelta(microseconds=1)).isoformat().replace("+00:00", "Z")
        views = release._collect_release_views(self.connection, as_of="2026-09-07", recorded_at=before)
        for key in ("claims", "source_claims", "claim_history", "entities", "inputs", "observations"):
            self.assertEqual([], views[key], key)
        self.assertEqual(0, views["summary"]["source_document_count"])
        self.assertNotIn("reviewed-source-project-targets", json.dumps(views["coverage"]))

    def second_review(self):
        old_after = copy.deepcopy(self.review["after"])
        next_body = self.after_body.replace(b"second half in 2027", b"first half in 2028").replace(b"second half of 2027", b"first half of 2028")
        self.fixture.bodies["document"] = next_body
        packet, _ = self.fixture.run_capture("second-capture", self.packet)
        imported = curated_review.import_capture(self.queue, packet)
        candidate = next(row for row in curated_review.queue_report(self.queue)["candidates"] if row["status"] == "pending")
        text = copy.deepcopy(self.text_review)
        text.update(reviewed_at=self.now(), candidate_id=candidate["id"], expected_event_id=candidate["last_event_id"])
        text["source_capture"] = {"manifest_sha256": imported["run_id"], "body_sha256": capture._sha(next_body),
            "normalized_sha256": candidate["current_text_sha256"], "normalization": "html_visible_text_v1", "retrieved_at": candidate["first_seen_at"]}
        text_path = self.root / "second-text-review.json"
        self.write(text_path, text)
        handed = curated_review.record_decision(self.queue, candidate["id"], action="handoff", reviewer="fixture", reason="Next source-stated target",
            expected_event_id=candidate["last_event_id"], evidence_ref=targets._ref(self.binding(text_path)))
        events = self.root / "second-events.json"
        self.write(events, curated_review.export_events(self.queue))
        manifest = self.root / "second-before-manifest.json"
        basis = "UTC microsecond response-finished invocation clock"
        self.write(manifest, {"format": "semiconductor-atlas-source-inputs-v1", "retrieved_at": old_after["retrieved_at"],
            "retrieval_timestamp_basis": basis, "inputs": [{"url": self.source_url, "path": old_after["body"]["path"],
                "sha256": old_after["body"]["sha256"], "bytes": len(self.after_body)}]})
        self.review = copy.deepcopy(self.review)
        self.review["reviewed_at"] = self.now()
        self.review["before"] = {**old_after, "manifest": self.binding(manifest), "retrieval_timestamp_basis": basis}
        self.review["after"] = {"body": self.binding(packet / "responses/document.body"), "retrieved_at": text["source_capture"]["retrieved_at"],
            "target": {"literal": "first half of 2028", "precision": "half_year", "date_low": "2028-01-01", "date_high": "2028-06-30"}, "spans": self.spans(next_body)}
        self.review["source"].update(events=self.binding(events), candidate_id=handed["id"], expected_event_id=handed["last_event_id"],
            run_id=imported["run_id"], text_sha256=handed["current_text_sha256"])
        self.review["source_review"] = self.binding(text_path)
        self.review_path = self.root / "second-claim-review.json"
        self.write_review()

    def test_sequential_reviews_reuse_shared_document_series_and_replay_both(self):
        first_path = self.review_path
        first = self.accept()
        first_series = self.connection.execute("SELECT series_id FROM claim_versions WHERE id=?", (first["claim_ids"]["after"],)).fetchone()[0]
        self.second_review()
        second = self.accept()
        second_series = self.connection.execute("SELECT series_id FROM claim_versions WHERE id=?", (second["claim_ids"]["before"],)).fetchone()[0]
        self.assertEqual(first_series, second_series)
        self.assertEqual(3, self.connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0])
        self.assertEqual(3, self.connection.execute("SELECT COUNT(*) FROM claim_series").fetchone()[0])
        self.assertEqual(4, self.connection.execute("SELECT COUNT(*) FROM claim_versions").fetchone()[0])
        self.assertEqual(first["admitted_at"], self.connection.execute("SELECT created_at FROM claim_series WHERE id=?", (first_series,)).fetchone()[0])
        self.assertTrue(self.accept()["replayed"])
        replay = targets.accept_review(self.connection, first_path, reference_root=self.root, source_queue=self.queue)
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["admitted_at"], replay["admitted_at"])

    def test_transitive_code_and_migration_files_are_in_snapshot(self):
        names = {path.name for path in targets._code_files()}
        self.assertTrue({"discovery_handoff.py", "source_checks.py", "ai_critical_changes.py", "0005_source_claim_precision.sql"} <= names)

    def test_queue_writer_cannot_advance_cas_during_core_commit_boundary(self):
        original = repository.validate_database
        attempted = []
        def compete(connection):
            other = sqlite3.connect(self.queue, timeout=0)
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    other.execute("BEGIN IMMEDIATE")
                attempted.append(True)
            finally:
                other.close()
            return original(connection)
        original_events = curated_review.export_events(self.queue)
        with patch.object(repository, "validate_database", side_effect=compete):
            self.accept()
        self.assertEqual([True], attempted)
        self.assertEqual(original_events, curated_review.export_events(self.queue))
        other = sqlite3.connect(self.queue, timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.rollback()
        finally:
            other.close()

    def test_replay_opens_queue_read_only(self):
        self.accept()
        original = curated_review._connection
        modes = []
        def checked(path, *, write=False):
            modes.append(write)
            return original(path, write=write)
        with patch.object(curated_review, "_connection", side_effect=checked):
            self.accept()
        self.assertTrue(modes)
        self.assertTrue(all(mode is False for mode in modes))

    def test_same_physical_database_hardlink_is_rejected(self):
        alias = self.root / "core-hardlink.sqlite"
        os.link(self.root / "core.sqlite", alias)
        with self.assertRaisesRegex(ValueError, "different physical"):
            targets.accept_review(self.connection, self.review_path, reference_root=self.root, source_queue=alias)
        self.assert_empty()

    def test_replay_detects_extra_source_record_owned_by_admission(self):
        result = self.accept()
        record = self.connection.execute("SELECT * FROM source_records WHERE ingestion_run_id=?", (result["run_id"],)).fetchone()
        repository.add_source_record(self.connection, models.SourceRecord("extra-record", result["run_id"], record["source_document_id"],
            "extra-source-record", record["observed_at"], record["record_sha256"], json.loads(record["payload_json"])))
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, "unexpected or altered source_records"):
            self.accept()

    def test_replay_detects_extra_run_document_role(self):
        result = self.accept()
        repository.add_ingestion_run_document(self.connection, models.IngestionRunDocument(result["run_id"], result["document_ids"]["after"], "unreviewed-extra-role"))
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, "unexpected or altered ingestion_run_documents"):
            self.accept()

    def test_replay_detects_extra_claim_owned_by_admission(self):
        result = self.accept()
        repository.add_claim_series(self.connection, models.ClaimSeries("extra-series", result["entity_id"], "extra-series",
            "milestone.production_start", models.ValueKind.MILESTONE, result["admitted_at"]))
        repository.insert_claim(self.connection, models.ClaimVersion("extra-claim", "extra-series", None, result["admitted_at"],
            models.ClaimKind.SOURCE_STATEMENT, "extra unreviewed claim", None, created_by_run_id=result["run_id"]),
            targets._target(self.review["after"]["target"]), evidence=[models.EvidenceLink(result["document_ids"]["after"])])
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, "unexpected or altered claim_versions"):
            self.accept()

    def test_cli_admission_and_replay(self):
        args = ["accept-project-targets", "--database", str(self.root / "core.sqlite"), "--review", str(self.review_path),
                "--reference-root", str(self.root), "--source-queue", str(self.queue)]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, cli.main(args))
        first = json.loads(output.getvalue())
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, cli.main(args))
        second = json.loads(output.getvalue())
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["admitted_at"], second["admitted_at"])

    def test_unchanged_target_different_document_is_reaffirmation_not_revision(self):
        body = self.after_body + b"<!-- previously retained HTML -->"
        self.before_path.write_bytes(body)
        self.manifest["inputs"][0].update(sha256=capture._sha(body), bytes=len(body))
        self.write(self.manifest_path, self.manifest)
        self.review["before"].update(body=self.binding(self.before_path), manifest=self.binding(self.manifest_path),
            target=copy.deepcopy(self.review["after"]["target"]), spans=self.spans(body))
        self.write_review()
        result = self.accept()
        self.assertEqual("source_stated_target_reaffirmation", result["comparison"]["kind"])
        self.assertIsNone(result["comparison"]["exact_acceleration_days"])
        self.assertTrue(self.accept()["replayed"])

    def test_changed_helper_fingerprint_rejects_historical_replay(self):
        self.accept()
        original = targets._code_fingerprint
        def changed(snapshot):
            fingerprint = original(snapshot)
            fingerprint["semiconductor_atlas/discovery_handoff.py"] = "0" * 64
            return fingerprint
        with patch.object(targets, "_code_fingerprint", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "helper code or migration fingerprint"):
                self.accept()

    def test_durable_code_fingerprint_excludes_inode_and_file_clocks(self):
        result = self.accept()
        params = json.loads(self.connection.execute("SELECT parameters_json FROM ingestion_runs WHERE id=?", (result["run_id"],)).fetchone()[0])
        self.assertIn("semiconductor_atlas/migrations/0005_source_claim_precision.sql", params["dependency_code_sha256"])
        self.assertIn("semiconductor_atlas/discovery_handoff.py", params["dependency_code_sha256"])
        self.assertTrue(all(isinstance(value, str) and len(value) == 64 for value in params["dependency_code_sha256"].values()))
