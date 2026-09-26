from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from semiconductor_atlas import curated_capture as capture
from semiconductor_atlas import curated_review, discovery_review, discovery_handoff as handoff
from scripts import verify_discovery_handoff as cli
from tests import test_curated_capture, test_nist_discovery


class DiscoveryHandoffTests(unittest.TestCase):
    """Actual offline capture packets and queue admissions, not mocked validation."""

    def setUp(self):
        self.discover = test_nist_discovery.NISTDiscoveryTests()
        self.discover.setUp()
        self.addCleanup(self.discover.doCleanups)
        self.source = test_curated_capture.CuratedCaptureTests()
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.root = self.discover.root
        (self.root / "review_plans").mkdir()
        for module in (capture, curated_review, discovery_review, handoff):
            mocked = patch.object(module, "_now", side_effect=self.discover.clock)
            mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(capture.time, "sleep", side_effect=self.discover.advance)
        mocked.start(); self.addCleanup(mocked.stop)
        self.discovery_queue = self.root / "discovery.sqlite"
        self.source_queue = self.root / "source.sqlite"
        discovery_review.initialize_queue(self.discovery_queue)
        curated_review.initialize_queue(self.source_queue)
        self.discovery_capture, _ = self.discover.run_capture("discovered")
        admitted = discovery_review.import_capture(self.discovery_queue, self.discovery_capture)
        found = next(row for row in discovery_review.queue_report(self.discovery_queue)["candidates"]
                     if "TSMC" in row["matched_companies"])
        self.discovery_admitted_at = found["first_recorded_at"]
        self.discover.advance(1)
        self.access_path = self.root / "review_plans/access.json"
        self.access = {"reviewed_at": self.discover.clock(), "reviewer": "fixture reviewer",
            "decision": "approve_one_exact_discovered_document_for_local_source_text_review",
            "facility_key": "tsmc:fab21-arizona",
            "document": {"id": "document", "url": found["source_url"],
                         "scope": "Broader Arizona portfolio; not all projects are Fab 21."},
            "discovery": {"candidate_id": found["id"], "expected_event_id": found["last_event_id"],
                "latest_capture_manifest_sha256": admitted["run_id"], "review_fingerprint": found["review_fingerprint"]},
            "acquisition_boundary": {key: False for key in ("claim_acceptance", "delivery_eligible", "raw_redistribution", "linked_resource_acquisition")}}
        self.write(self.access_path, self.access)
        access_binding = self.binding(self.access_path)
        self.discover.advance(1)
        discovered = discovery_review.record_decision(self.discovery_queue, found["id"], action="handoff",
            reviewer="fixture reviewer", reason="Separate exact URL scope/access review completed",
            expected_event_id=found["last_event_id"], evidence_ref=handoff._ref(access_binding))
        self.source.plan["checked_facility_key"] = "tsmc:fab21-arizona"
        self.source.plan["reviewed_at"] = self.access["reviewed_at"]
        self.source.plan["review_record"] = access_binding
        for row in self.source.plan["policies"] + self.source.plan["documents"]:
            row["company"] = "TSMC"
            row["url"] = row["url"].replace("https://example.org", "https://www.nist.gov")
        self.source.plan["documents"][0]["url"] = found["source_url"]
        self.source.plan["documents"][0]["scope"] = "Broader Arizona portfolio; not all projects are Fab 21."
        self.plan_path = self.root / "plans/source.json"
        self.write(self.plan_path, self.source.plan)
        self.discover.advance(1)
        self.source_capture = self.root / "acquired"
        result = capture.capture_sources(self.plan_path, self.source_capture, review_root=self.root,
                                         transport=self.source.transport)
        source_import = curated_review.import_capture(self.source_queue, self.source_capture)
        candidate = curated_review.queue_report(self.source_queue)["candidates"][0]
        self.discover.advance(1)
        self.source_review_path = self.root / "review_plans/text.json"
        self.source_review = {
            "reviewed_at": self.discover.clock(), "reviewer": "fixture reviewer",
            "facility_key": candidate["facility_key"], "candidate_id": candidate["id"],
            "expected_event_id": candidate["last_event_id"],
            "decision": "dismiss_reviewed_text_version_without_baseline_revision",
            "claim_acceptance": False, "delivery_eligible": False, "baseline_modified": False,
            "source_capture": {"manifest_sha256": source_import["run_id"],
                "body_sha256": result["documents"][0]["current_sha256"],
                "normalized_sha256": result["documents"][0]["current_text_sha256"],
                "normalization": "html_visible_text_v1", "retrieved_at": candidate["first_seen_at"]},
            "assessment": "No new facility-specific evidence; no baseline revision."}
        self.write(self.source_review_path, self.source_review)
        self.discover.advance(1)
        acquired = curated_review.record_decision(self.source_queue, candidate["id"], action="dismiss",
            reviewer="fixture reviewer", reason="No baseline revision justified",
            expected_event_id=candidate["last_event_id"], evidence_ref=handoff._ref(self.binding(self.source_review_path)))
        self.discovery_events_path = self.root / "discovery-events.json"
        self.source_events_path = self.root / "source-events.json"
        self.write(self.discovery_events_path, discovery_review.export_events(self.discovery_queue))
        self.write(self.source_events_path, curated_review.export_events(self.source_queue))
        self.discover.advance(1)
        self.review = {
            "format": handoff.REVIEW_FORMAT, "reviewed_at": self.discover.clock(), "reviewer": "fixture reviewer",
            "facility_key": candidate["facility_key"], "source_url": found["source_url"],
            "document_scope": self.source.plan["documents"][0]["scope"],
            "scope_limitation": "Company routing does not allocate portfolio statements to Fab 21.",
            "outcome": "no_baseline_revision", "rationale": "Source text reviewed, broader project claims left unaccepted.",
            "discovery": {"events": self.binding(self.discovery_events_path), "candidate_id": discovered["id"],
                "expected_event_id": discovered["last_event_id"], "run_id": admitted["run_id"]},
            "source": {"events": self.binding(self.source_events_path), "candidate_id": acquired["id"],
                "expected_event_id": acquired["last_event_id"], "run_id": source_import["run_id"],
                "document_id": "document", "plan_sha256": handoff._hash(self.plan_path.read_bytes()),
                "body_sha256": result["documents"][0]["current_sha256"],
                "text_sha256": result["documents"][0]["current_text_sha256"]},
            "access_review": access_binding, "source_review": self.binding(self.source_review_path),
            "boundaries": dict(handoff.BOUNDARIES),
        }
        self.review_path = self.root / "review_plans/handoff.json"
        self.output = self.root / "handoff.json"
        self.save_review()

    def write(self, path, value):
        path.write_bytes(handoff._pretty_bytes(value))

    def binding(self, path):
        return {"path": path.relative_to(self.root).as_posix(), "sha256": handoff._hash(path.read_bytes())}

    def save_review(self):
        self.write(self.review_path, self.review)

    def build(self):
        self.save_review()
        return handoff.build_handoff(self.review_path, reference_root=self.root)

    def rewrite_events(self, kind, mutate):
        path = self.discovery_events_path if kind == "discovery" else self.source_events_path
        artifact = json.loads(path.read_bytes())
        mutate(artifact["events"])
        previous = None
        for sequence, event in enumerate(artifact["events"], 1):
            event["sequence"], event["previous_event_id"] = sequence, previous
            event["event_id"] = handoff._hash({key: value for key, value in event.items() if key != "event_id"})
            previous = event["event_id"]
        self.write(path, artifact)
        self.review[kind]["events"] = self.binding(path)

    def replace_bound_access_review(self):
        """Rebind every affected fixture byte so semantic checks, not broken hashes, reject."""
        self.write(self.access_path, self.access)
        self.review["access_review"] = self.binding(self.access_path)
        self.source.plan["review_record"] = self.review["access_review"]
        self.write(self.plan_path, self.source.plan)
        self.write(self.source_capture / "plan.json", self.source.plan)
        self.write(self.source_capture / "review.json", self.access)
        run = json.loads((self.source_capture / "run.json").read_bytes())
        run["plan_sha256"] = handoff._hash(self.plan_path.read_bytes())
        self.write(self.source_capture / "run.json", run)
        manifest = json.loads((self.source_capture / "manifest.json").read_bytes())
        manifest["files"] = capture._inventory(self.source_capture)
        self.write(self.source_capture / "manifest.json", manifest)
        payload = curated_review._capture_payload(self.source_capture)
        self.rewrite_events("discovery", lambda events: events[-1]["payload"].update(
            evidence_ref=handoff._ref(self.review["access_review"])))
        self.review["discovery"]["expected_event_id"] = json.loads(self.discovery_events_path.read_bytes())["events"][-1]["event_id"]
        artifact = json.loads(self.source_events_path.read_bytes())
        first = artifact["events"][0]
        first["payload"] = payload
        first["event_id"] = handoff._hash({key: value for key, value in first.items() if key != "event_id"})
        self.source_review["expected_event_id"] = first["event_id"]
        self.source_review["source_capture"]["manifest_sha256"] = payload["run_id"]
        self.write(self.source_review_path, self.source_review)
        self.review["source_review"] = self.binding(self.source_review_path)
        artifact["events"][-1]["payload"].update(expected_event_id=first["event_id"],
            evidence_ref=handoff._ref(self.review["source_review"]))
        self.write(self.source_events_path, artifact)
        self.rewrite_events("source", lambda events: None)
        self.review["source"].update(run_id=payload["run_id"], plan_sha256=payload["plan_sha256"],
            expected_event_id=json.loads(self.source_events_path.read_bytes())["events"][-1]["event_id"])

    def test_end_to_end_deterministic_offline_link_with_explicit_nonportable_dependencies(self):
        before = [path.read_bytes() for path in (self.discovery_queue, self.source_queue)]
        with patch.object(capture, "curl_fetch", side_effect=AssertionError("network")), patch.object(capture, "capture_sources", side_effect=AssertionError("acquisition")):
            first = self.build()
            self.assertEqual(first, self.build())
            self.assertEqual(first, handoff.write_handoff(self.review_path, self.output))
            self.assertEqual(first, handoff.validate_handoff(self.output))
        self.assertEqual(before, [path.read_bytes() for path in (self.discovery_queue, self.source_queue)])
        self.assertTrue(first["linked"])
        self.assertEqual("no_baseline_revision", first["facts"]["outcome"])
        self.assertEqual(self.review["document_scope"], first["facts"]["document_scope"])
        self.assertFalse(first["replay_dependencies"]["self_contained"])
        self.assertEqual(2, len(first["replay_dependencies"]["capture_paths"]))
        self.assertTrue(all(value is False for value in first["boundaries"].values()))
        self.assertNotEqual("2026-07-16T12:00:00Z", first["facts"]["discovery_admitted_at"])

    def test_every_event_cutoff_hides_later_knowledge_and_final_review(self):
        handoff.write_handoff(self.review_path, self.output)
        early = handoff.replay_handoff(self.output, as_of="2026-09-07T04:00:00Z")
        self.assertIsNone(early["source"])
        self.assertIsNone(early["discovery"])
        self.assertFalse(early["linked"])
        events = json.loads(self.discovery_events_path.read_bytes())["events"] + json.loads(self.source_events_path.read_bytes())["events"]
        for event in events:
            with self.subTest(cutoff=event["recorded_at"]):
                result = handoff.replay_handoff(self.output, as_of=event["recorded_at"])
                self.assertFalse(result["linked"])
                self.assertIsNone(result["facts"])
                if event["recorded_at"] < self.source_review["reviewed_at"]:
                    self.assertNotEqual("dismissed", (result["source"] or {}).get("status"))
        final = handoff.replay_handoff(self.output, as_of=self.review["reviewed_at"])
        self.assertTrue(final["linked"])
        self.assertEqual("retrospective_target_selected_not_blind", final["selection"])
        with self.assertRaisesRegex(ValueError, "reviewed cutoff"):
            handoff.replay_handoff(self.output, as_of="2026-09-08T00:00:00Z")

    def test_later_valid_decision_does_not_rewrite_historical_link(self):
        original = self.build()["facts"]
        self.discover.advance(60)
        candidate = curated_review.queue_report(self.source_queue)["candidates"][0]
        curated_review.record_decision(self.source_queue, candidate["id"], action="reopen",
            reviewer="fixture reviewer", reason="Later review", expected_event_id=candidate["last_event_id"])
        self.write(self.source_events_path, curated_review.export_events(self.source_queue))
        self.review["source"]["events"] = self.binding(self.source_events_path)
        self.assertEqual(original, self.build()["facts"])

    def test_stale_post_decision_event_guards(self):
        for name in ("discovery", "source"):
            original = self.review[name]["expected_event_id"]
            self.review[name]["expected_event_id"] = "0" * 64
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "stale expected"):
                self.build()
            self.review[name]["expected_event_id"] = original

    def test_unknown_candidates_and_packet_ids_rejected(self):
        for name in ("discovery", "source"):
            for field in ("candidate_id", "run_id"):
                original = self.review[name][field]
                self.review[name][field] = "0" * 64
                with self.subTest(name=name, field=field), self.assertRaises(ValueError):
                    self.build()
                self.review[name][field] = original

    def test_wrong_url_facility_scope_and_body_bindings_rejected(self):
        for field, value in (("source_url", "https://www.nist.gov/other"), ("facility_key", "intel:fab52-chandler"), ("document_scope", "Fab 21 only")):
            original = self.review[field]
            self.review[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.build()
            self.review[field] = original
        for field in ("plan_sha256", "body_sha256", "text_sha256"):
            original = self.review["source"][field]
            self.review["source"][field] = "0" * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.build()
            self.review["source"][field] = original

    def test_review_cannot_promote_claims_permissions_or_numeric_boolean_aliases(self):
        for field in handoff.BOUNDARIES:
            for value in (True, 0, None):
                self.review["boundaries"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.build()
            self.review["boundaries"][field] = False
        self.review["outcome"] = "claim_accepted"
        with self.assertRaises(ValueError):
            self.build()

    def test_malformed_extra_missing_fields_and_duplicate_json_keys(self):
        original = copy.deepcopy(self.review)
        for value in ([], None, {**original, "unreviewed": True}, {key: val for key, val in original.items() if key != "scope_limitation"}):
            self.review = value
            with self.subTest(value=type(value)), self.assertRaises(ValueError):
                self.build()
        self.review = original
        self.save_review()
        self.review_path.write_bytes(self.review_path.read_bytes().replace(b'"format":', b'"format":"duplicate","format":', 1))
        with self.assertRaises(ValueError):
            handoff.build_handoff(self.review_path)

    def test_future_review_and_backdated_link_rejected(self):
        for clock in ("2030-01-01T00:00:00Z", self.discovery_admitted_at, "invalid", None):
            self.review["reviewed_at"] = clock
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                self.build()

    def test_hash_bound_supporting_review_cannot_be_swapped(self):
        for path in (self.access_path, self.source_review_path):
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                self.build()
            path.write_bytes(original)

    def test_independent_access_review_rejects_blocked_wrong_scope_and_expansive_approval(self):
        plan = self.source.plan
        document = plan["documents"][0]
        handoff._access_approval(self.access, plan, document)
        for key, value in (("decision", "blocked"), ("facility_key", "intel:fab52-chandler"),
                           ("reviewed_at", "2026-09-08T00:00:00Z"), ("reviewer", "")):
            changed = {**self.access, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                handoff._access_approval(changed, plan, document)
        for key, value in (("url", "https://www.nist.gov/unapproved"), ("id", "other"), ("scope", "All TSMC fabs")):
            changed = copy.deepcopy(self.access)
            changed["document"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                handoff._access_approval(changed, plan, document)
        for key in self.access["acquisition_boundary"]:
            for value in (True, 0, None):
                changed = copy.deepcopy(self.access)
                changed["acquisition_boundary"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    handoff._access_approval(changed, plan, document)

    def test_access_review_discovery_bindings_are_semantic_not_opaque_metadata(self):
        original = copy.deepcopy(self.access["discovery"])
        for key in ("candidate_id", "expected_event_id", "latest_capture_manifest_sha256", "review_fingerprint"):
            self.access["discovery"] = {**original, key: "0" * 64}
            self.replace_bound_access_review()
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "discovery state known"):
                self.build()
        self.access["discovery"] = original
        self.replace_bound_access_review()
        self.assertTrue(self.build()["linked"])

    def test_source_review_cannot_bind_a_future_reopen_token_with_an_old_review_clock(self):
        self.discover.advance(60)
        candidate = curated_review.queue_report(self.source_queue)["candidates"][0]
        reopened = curated_review.record_decision(self.source_queue, candidate["id"], action="reopen",
            reviewer="fixture reviewer", reason="Later explicit reopen", expected_event_id=candidate["last_event_id"])
        self.source_review["expected_event_id"] = reopened["last_event_id"]
        self.write(self.source_review_path, self.source_review)
        self.review["source_review"] = self.binding(self.source_review_path)
        self.discover.advance(1)
        closed = curated_review.record_decision(self.source_queue, candidate["id"], action="dismiss",
            reviewer="fixture reviewer", reason="Later dismissal with stale supporting review clock",
            expected_event_id=reopened["last_event_id"], evidence_ref=handoff._ref(self.review["source_review"]))
        self.write(self.source_events_path, curated_review.export_events(self.source_queue))
        self.review["source"]["events"] = self.binding(self.source_events_path)
        self.review["source"]["expected_event_id"] = closed["last_event_id"]
        self.discover.advance(1)
        self.review["reviewed_at"] = self.discover.clock()
        with self.assertRaisesRegex(ValueError, "predecision token was not applicable"):
            self.build()

    def test_supporting_review_change_during_derivation_is_detected(self):
        real_read = handoff._read
        source_review_reads = 0

        def change_after_read(path):
            nonlocal source_review_reads
            raw = real_read(path)
            if path == self.source_review_path:
                source_review_reads += 1
                if source_review_reads == 1:
                    path.write_bytes(raw + b" ")
            return raw

        with patch.object(handoff, "_read", side_effect=change_after_read), self.assertRaisesRegex(ValueError, "hash mismatch|dependency snapshot"):
            self.build()

    def test_code_hash_boundary_hook_cannot_hide_late_review_mutation(self):
        original = handoff._code_hashes

        def change_after_hashes():
            result = original()
            self.access_path.write_bytes(self.access_path.read_bytes() + b" ")
            return result

        with patch.object(handoff, "_code_hashes", side_effect=change_after_hashes), self.assertRaises(ValueError):
            self.build()

    def test_all_dependency_classes_are_rechecked_after_semantic_derivation(self):
        code = self.root / "fixture-code.py"
        code.write_bytes(b"fixture validator code\n")
        paths = {**handoff._code_paths(), "fixture-code": code}
        original_load = handoff._load
        for target in (self.review_path, self.access_path, self.source_review_path,
                       self.discovery_events_path, self.source_events_path,
                       self.discovery_capture / "responses/news-page-0.body",
                       self.source_capture / "responses/document.body",
                       self.source_capture / "manifest.json", code):
            original = target.read_bytes()

            def mutate_after_load(*args):
                result = original_load(*args)
                target.write_bytes(original + b" ")
                return result

            with self.subTest(target=target), patch.object(handoff, "_code_paths", return_value=paths), patch.object(handoff, "_load", side_effect=mutate_after_load), self.assertRaises(ValueError):
                self.build()
            target.write_bytes(original)

    def test_same_byte_inode_replacement_and_new_unmanaged_capture_file_reject(self):
        original_load = handoff._load
        body = self.source_capture / "responses/document.body"

        def replace_after_load(*args):
            result = original_load(*args)
            replacement = self.root / "replacement.body"
            replacement.write_bytes(body.read_bytes())
            replacement.replace(body)
            return result

        with patch.object(handoff, "_load", side_effect=replace_after_load), self.assertRaisesRegex(ValueError, "dependencies changed"):
            self.build()

        def add_after_load(*args):
            result = original_load(*args)
            (self.source_capture / "unmanaged.json").write_bytes(b"{}")
            return result

        with patch.object(handoff, "_load", side_effect=add_after_load), self.assertRaisesRegex(ValueError, "dependencies changed"):
            self.build()

    def test_write_rechecks_after_build_and_after_staging_fsync_without_publishing(self):
        original = self.access_path.read_bytes()
        real_build = handoff.build_handoff

        def mutate_after_build(*args, **kwargs):
            result = real_build(*args, **kwargs)
            self.access_path.write_bytes(original + b" ")
            return result

        with patch.object(handoff, "build_handoff", side_effect=mutate_after_build), self.assertRaises(ValueError):
            handoff.write_handoff(self.review_path, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".handoff-*")))
        self.access_path.write_bytes(original)
        real_fsync = handoff.os.fsync

        def mutate_after_fsync(descriptor):
            real_fsync(descriptor)
            self.access_path.write_bytes(original + b" ")

        with patch.object(handoff.os, "fsync", side_effect=mutate_after_fsync), self.assertRaises(ValueError):
            handoff.write_handoff(self.review_path, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".handoff-*")))

    def test_write_postpublication_drift_removes_only_its_own_unchanged_new_inode(self):
        original = self.access_path.read_bytes()
        real_install = handoff.ai_critical.install_file_exclusive

        def mutate_after_install(*args, **kwargs):
            real_install(*args, **kwargs)
            self.access_path.write_bytes(original + b" ")

        with patch.object(handoff.ai_critical, "install_file_exclusive", side_effect=mutate_after_install), self.assertRaises(ValueError):
            handoff.write_handoff(self.review_path, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".handoff-*")))
        self.access_path.write_bytes(original)

        def replace_after_install(*args, **kwargs):
            mutate_after_install(*args, **kwargs)
            replacement = self.root / "user-file.json"
            replacement.write_bytes(b"preserve user replacement")
            replacement.replace(self.output)

        with patch.object(handoff.ai_critical, "install_file_exclusive", side_effect=replace_after_install), self.assertRaises(ValueError):
            handoff.write_handoff(self.review_path, self.output)
        self.assertEqual(b"preserve user replacement", self.output.read_bytes())

    def test_validation_rechecks_the_artifact_itself_at_acceptance(self):
        handoff.write_handoff(self.review_path, self.output)
        real_build = handoff.build_handoff

        def mutate_after_build(*args, **kwargs):
            result = real_build(*args, **kwargs)
            self.output.write_bytes(self.output.read_bytes() + b" ")
            return result

        with patch.object(handoff, "build_handoff", side_effect=mutate_after_build), self.assertRaisesRegex(ValueError, "artifact changed"):
            handoff.validate_handoff(self.output)

    def test_supporting_text_review_requires_exact_pre_decision_and_text_binding(self):
        # Updating both the event reference and link hash still cannot authorize a different body.
        self.source_review["source_capture"]["body_sha256"] = "0" * 64
        self.write(self.source_review_path, self.source_review)
        self.review["source_review"] = self.binding(self.source_review_path)
        self.rewrite_events("source", lambda events: events[-1]["payload"].update(evidence_ref=handoff._ref(self.review["source_review"])))
        self.review["source"]["expected_event_id"] = json.loads(self.source_events_path.read_bytes())["events"][-1]["event_id"]
        with self.assertRaisesRegex(ValueError, "supporting source review"):
            self.build()

    def test_rehashed_forged_capture_payload_rejected_against_original_packet(self):
        self.rewrite_events("source", lambda events: events[0]["payload"].update(attention_required=False))
        with self.assertRaises(ValueError):
            self.build()

    def test_rehashed_duplicate_import_and_stale_decision_rejected(self):
        self.rewrite_events("source", lambda events: events.append(copy.deepcopy(events[0])))
        with self.assertRaises(ValueError):
            self.build()

    def test_rehashed_stale_decision_cannot_replace_its_pre_decision_token(self):
        self.rewrite_events("source", lambda events: events[-1]["payload"].update(expected_event_id="0" * 64))
        with self.assertRaisesRegex(ValueError, "stale candidate"):
            self.build()

    def test_separate_claim_review_route_remains_a_proposal_not_acceptance(self):
        self.source_review["decision"] = "handoff_reviewed_text_version_for_separate_claim_review"
        self.write(self.source_review_path, self.source_review)
        self.review["source_review"] = self.binding(self.source_review_path)
        self.rewrite_events("source", lambda events: events[-1]["payload"].update(
            action="handoff", evidence_ref=handoff._ref(self.review["source_review"])))
        self.review["source"]["expected_event_id"] = json.loads(self.source_events_path.read_bytes())["events"][-1]["event_id"]
        self.review["outcome"] = "requires_claim_review"
        result = self.build()
        self.assertEqual("requires_claim_review", result["facts"]["outcome"])
        self.assertTrue(all(value is False for value in result["boundaries"].values()))

    def test_backdated_source_text_review_rejected_after_all_hash_bindings_updated(self):
        self.source_review["reviewed_at"] = self.access["reviewed_at"]
        self.write(self.source_review_path, self.source_review)
        self.review["source_review"] = self.binding(self.source_review_path)
        self.rewrite_events("source", lambda events: events[-1]["payload"].update(evidence_ref=handoff._ref(self.review["source_review"])))
        self.review["source"]["expected_event_id"] = json.loads(self.source_events_path.read_bytes())["events"][-1]["event_id"]
        with self.assertRaisesRegex(ValueError, "clocks violate|source at review time"):
            self.build()

    def test_opaque_or_wrong_disposition_text_review_is_not_accepted(self):
        self.source_review["decision"] = "accepted_operating_capacity"
        self.write(self.source_review_path, self.source_review)
        self.review["source_review"] = self.binding(self.source_review_path)
        self.rewrite_events("source", lambda events: events[-1]["payload"].update(evidence_ref=handoff._ref(self.review["source_review"])))
        self.review["source"]["expected_event_id"] = json.loads(self.source_events_path.read_bytes())["events"][-1]["event_id"]
        with self.assertRaisesRegex(ValueError, "supporting source review"):
            self.build()

    def test_future_queue_admission_rejected_even_when_chain_is_rehashed(self):
        self.rewrite_events("source", lambda events: events[-1].update(recorded_at="2030-01-01T00:00:00Z"))
        with self.assertRaisesRegex(ValueError, "future admissions"):
            self.build()

    def test_original_body_mutation_and_missing_capture_are_not_portable_success(self):
        body = self.source_capture / "responses/document.body"
        original = body.read_bytes()
        body.write_bytes(original.replace(b"announced", b"producing"))
        with self.assertRaises(ValueError):
            self.build()
        body.write_bytes(original)
        moved = self.source_capture.with_name("moved")
        self.source_capture.rename(moved)
        try:
            with self.assertRaises((ValueError, OSError)):
                self.build()
        finally:
            moved.rename(self.source_capture)

    def test_pending_or_acknowledged_review_is_not_closed_text_review(self):
        events = json.loads(self.source_events_path.read_bytes())
        events["events"] = events["events"][:1]
        self.write(self.source_events_path, events)
        self.review["source"]["events"] = self.binding(self.source_events_path)
        self.review["source"]["expected_event_id"] = events["events"][0]["event_id"]
        with self.assertRaisesRegex(ValueError, "unresolved candidate"):
            self.build()

    def test_failed_later_check_does_not_let_old_dismissed_text_pass_as_current(self):
        self.discover.advance(60)
        self.source.bodies["rights"] += b'<a href="/changed-policy">Changed</a>'
        failed = self.root / "failed"
        capture.capture_sources(self.plan_path, failed, review_root=self.root, transport=self.source.transport)
        curated_review.import_capture(self.source_queue, failed)
        self.write(self.source_events_path, curated_review.export_events(self.source_queue))
        self.review["source"]["events"] = self.binding(self.source_events_path)
        self.review["reviewed_at"] = self.discover.clock()
        with self.assertRaisesRegex(ValueError, "changed or failed"):
            self.build()

    def test_changed_later_text_does_not_let_old_dismissed_version_pass(self):
        self.discover.advance(60)
        self.source.bodies["document"] += b"<p>Different current text.</p>"
        changed = self.root / "changed"
        capture.capture_sources(self.plan_path, changed, review_root=self.root, transport=self.source.transport)
        curated_review.import_capture(self.source_queue, changed)
        self.write(self.source_events_path, curated_review.export_events(self.source_queue))
        self.review["source"]["events"] = self.binding(self.source_events_path)
        self.review["reviewed_at"] = self.discover.clock()
        with self.assertRaisesRegex(ValueError, "changed or failed"):
            self.build()

    def test_discovery_reference_string_is_not_permission_or_a_completed_handoff(self):
        self.rewrite_events("discovery", lambda events: events[-1]["payload"].update(evidence_ref="permission granted"))
        self.review["discovery"]["expected_event_id"] = json.loads(self.discovery_events_path.read_bytes())["events"][-1]["event_id"]
        with self.assertRaisesRegex(ValueError, "supporting review"):
            self.build()

    def test_refs_reject_traversal_absolute_paths_symlinks_and_bad_hashes(self):
        original = dict(self.review["source_review"])
        for path in ("../text.json", str(self.source_review_path), "review_plans//text.json"):
            self.review["source_review"]["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.build()
        self.review["source_review"] = original
        link = self.root / "review_plans/link.json"
        link.symlink_to(self.source_review_path)
        self.review["source_review"]["path"] = "review_plans/link.json"
        with self.assertRaises(ValueError):
            self.build()

    def test_new_file_only_and_immutable_capture_output_guards(self):
        handoff.write_handoff(self.review_path, self.output)
        original = self.output.read_bytes()
        with self.assertRaises(FileExistsError):
            handoff.write_handoff(self.review_path, self.output)
        self.assertEqual(original, self.output.read_bytes())
        with self.assertRaisesRegex(ValueError, "outside immutable"):
            handoff.write_handoff(self.review_path, self.source_capture / "handoff.json")
        link = self.root / "output-link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            handoff.write_handoff(self.review_path, link / "other.json")

    def test_artifact_forgery_and_code_drift_reject(self):
        handoff.write_handoff(self.review_path, self.output)
        original = self.output.read_bytes()
        artifact = json.loads(original)
        artifact["facts"]["outcome"] = "claim_accepted"
        self.write(self.output, artifact)
        with self.assertRaisesRegex(ValueError, "replay exactly"):
            handoff.validate_handoff(self.output)
        self.output.write_bytes(original)
        with patch.object(handoff, "_code_hashes", return_value={"changed": "0" * 64}), self.assertRaisesRegex(ValueError, "replay exactly|code changed"):
            handoff.validate_handoff(self.output)

    def test_cli_build_verify_history_and_fail_closed(self):
        for arguments in (["build", "--review", str(self.review_path), "--output", str(self.output)],
                          ["verify", "--artifact", str(self.output)],
                          ["history", "--artifact", str(self.output), "--as-of", self.discovery_admitted_at]):
            stdout = io.StringIO()
            with patch("sys.argv", ["verify_discovery_handoff.py", *arguments]), redirect_stdout(stdout):
                cli.main()
            self.assertIsInstance(json.loads(stdout.getvalue()), dict)
        with patch("sys.argv", ["verify_discovery_handoff.py", "build", "--review", str(self.review_path), "--output", str(self.output)]), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            cli.main()
        self.assertEqual(1, error.exception.code)


if __name__ == "__main__":
    unittest.main()
