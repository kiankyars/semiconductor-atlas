from __future__ import annotations

import hashlib
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import review_ai_critical_alerts as cli
from semiconductor_atlas import ai_critical_alert_review as review
from semiconductor_atlas.ai_critical_changes import _pretty_bytes, write_change_bundle
from tests import test_ai_critical_changes as change_fixtures


ROOT = Path(__file__).resolve().parents[1]


class AlertReviewCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = change_fixtures.AICriticalChangeDetectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.database = self.root / "alert-review.sqlite"
        self.prior = self.fixture._write_release("prior", json.loads(
            (ROOT / "baselines/ai_critical_manufacturing_v1.json").read_bytes()))
        self.current = self.fixture._write_release("current", json.loads(
            (ROOT / "baselines/ai_critical_manufacturing_v1_amkor_2026-09-07.json").read_bytes()))
        self.bundle = self.root / "comparison"
        write_change_bundle(self.prior, self.current, self.bundle)
        self.admission_path = self.root / "admission.json"
        supporting = ROOT / "review_plans/2026-09-07-amkor-peoria-successor.json"
        admission = {
            "format": "semiconductor-atlas-ai-critical-alert-admission-v1",
            "purpose": "alert_review_only",
            "reviewer": "CLI test fixture",
            "reviewed_at": "2026-09-07T06:30:00Z",
            "reason": "Offline CLI fixture with committed derivative evidence, not a live source review.",
            "prior_manifest_sha256": self.digest(self.prior / "manifest.json"),
            "current_manifest_sha256": self.digest(self.current / "manifest.json"),
            "change_manifest_sha256": self.digest(self.bundle / "manifest.json"),
            "supporting_reviews": [{"path": str(supporting), "sha256": self.digest(supporting)}],
        }
        self.admission_path.write_bytes(_pretty_bytes(admission))
        self.clock = "2026-09-07T06:31:00Z"
        now = patch.object(review, "_now", side_effect=lambda: self.clock)
        now.start()
        self.addCleanup(now.stop)

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def invoke(self, *arguments: object) -> dict:
        output = io.StringIO()
        with patch("sys.argv", ["command", *map(str, arguments)]), redirect_stdout(output):
            cli.main()
        return json.loads(output.getvalue())

    def import_bundle(self) -> dict:
        return self.invoke("import", "--database", self.database, "--bundle", self.bundle,
                           "--prior", self.prior, "--current", self.current,
                           "--review", self.admission_path)

    def test_offline_cli_round_trip_with_actual_baseline_derivatives(self) -> None:
        self.invoke("init", "--database", self.database)
        self.import_bundle()
        report = self.invoke("report", "--database", self.database)
        self.assertEqual(1, len(report["alerts"]))
        self.assertEqual(0, report["delivery_eligible_count"])
        self.assertFalse(report["alerts"][0]["delivery_eligible"])
        self.invoke("verify", "--database", self.database)
        before = self.invoke("report", "--database", self.database,
                             "--as-of", "2026-09-07T06:30:59Z")
        self.assertEqual([], before["alerts"])
        exported = self.root / "portable.json"
        self.invoke("export", "--database", self.database, "--output", exported)
        restored = self.root / "restored.sqlite"
        self.invoke("restore", "--database", restored, "--events", exported)
        self.assertEqual(report, self.invoke("report", "--database", restored))
        self.assertEqual(self.invoke("export", "--database", self.database),
                         self.invoke("export", "--database", restored))

    def test_export_and_initialization_do_not_overwrite_existing_files(self) -> None:
        self.invoke("init", "--database", self.database)
        self.import_bundle()
        exported = self.root / "portable.json"
        self.invoke("export", "--database", self.database, "--output", exported)
        before = exported.read_bytes()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke("export", "--database", self.database, "--output", exported)
        self.assertEqual(before, exported.read_bytes())
        events = self.invoke("export", "--database", self.database)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke("init", "--database", self.database)
        self.assertEqual(events, self.invoke("export", "--database", self.database))

    def test_cli_decisions_preserve_bound_evidence_and_reject_stale_token(self) -> None:
        self.invoke("init", "--database", self.database)
        self.import_bundle()
        alert = self.invoke("report", "--database", self.database)["alerts"][0]
        original_token = alert["last_event_id"]
        self.clock = "2026-09-07T06:32:00Z"
        acknowledged = self.invoke("decide", "--database", self.database, "--alert", alert["id"],
                                   "--action", "acknowledge", "--reviewer", "CLI fixture",
                                   "--reason", "Fixture review", "--expected-event", original_token)
        self.assertEqual("acknowledged", acknowledged["status"])
        observation = alert["observations"][0]
        claim = observation["after"]
        link = claim["evidence_links"][0]
        ref = {"bundle_id": observation["bundle_id"], "side": "current",
               "claim_id": claim["claim_id"], "evidence_id": link["evidence_id"],
               "fragment_sha256": link["fragment_sha256"]}
        self.clock = "2026-09-07T06:33:00Z"
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke("decide", "--database", self.database, "--alert", alert["id"],
                        "--action", "resolve", "--reviewer", "CLI fixture", "--reason", "Fixture review",
                        "--expected-event", original_token, "--evidence-ref", json.dumps(ref))
        resolved = self.invoke("decide", "--database", self.database, "--alert", alert["id"],
                               "--action", "resolve", "--reviewer", "CLI fixture", "--reason", "Fixture review",
                               "--expected-event", acknowledged["last_event_id"], "--evidence-ref", json.dumps(ref))
        self.assertEqual("resolved", resolved["status"])
        self.assertFalse(resolved["delivery_eligible"])
        self.assertEqual([ref], resolved["decisions"][-1]["evidence_refs"])
        before_resolution = self.invoke("report", "--database", self.database,
                                        "--as-of", "2026-09-07T06:32:59Z")
        self.assertEqual("acknowledged", before_resolution["alerts"][0]["status"])

    def test_import_requires_review_and_both_release_arguments(self) -> None:
        for omitted in ("--prior", "--current", "--review"):
            arguments = {"--bundle": self.bundle, "--prior": self.prior,
                         "--current": self.current, "--review": self.admission_path}
            del arguments[omitted]
            flattened = [part for pair in arguments.items() for part in pair]
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.invoke("import", "--database", self.database, *flattened)
            self.assertFalse(self.database.exists())

    def test_evidence_argument_rejects_duplicate_keys_and_non_objects(self) -> None:
        for malformed in ('{"side":"prior","side":"current"}', '[]', 'null', '{bad}'):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.invoke("decide", "--database", self.database, "--alert", "fixture",
                            "--action", "retract", "--reviewer", "fixture", "--reason", "fixture",
                            "--expected-event", "fixture", "--evidence-ref", malformed)
            self.assertFalse(self.database.exists())


if __name__ == "__main__":
    unittest.main()
