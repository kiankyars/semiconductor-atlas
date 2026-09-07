from __future__ import annotations

import io
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import review_alerts_v2 as cli
from semiconductor_atlas import ai_critical_alert_review as legacy
from semiconductor_atlas import alert_review_v2 as review
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from tests import test_ai_critical_alert_review as legacy_fixtures
from tests import test_project_target_review as project_fixtures


class AlertReviewV2CLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.database = self.root / "review.sqlite"

    def invoke(self, *arguments):
        output = io.StringIO()
        with patch("sys.argv", ["review-alerts-v2", *map(str, arguments)]), redirect_stdout(output):
            cli.main()
        return json.loads(output.getvalue())

    def test_empty_cli_roundtrip(self):
        self.invoke("init", "--database", self.database)
        before = self.invoke("report", "--database", self.database)
        self.assertEqual([], before["alerts"])
        self.assertEqual(0, before["delivery_eligible_count"])
        self.invoke("verify", "--database", self.database)
        exported = self.root / "export.json"
        self.invoke("export", "--database", self.database, "--output", exported)
        self.assertEqual(self.invoke("export", "--database", self.database), json.loads(exported.read_bytes()))
        restored = self.root / "restored.sqlite"
        self.invoke("restore", "--database", restored, "--events", exported)
        self.assertEqual(before, self.invoke("report", "--database", restored))
        cutoff = "2026-09-07T00:00:00Z"
        for command in ("report", "verify", "export"):
            result = self.invoke(command, "--database", restored, "--as-of", cutoff)
            self.assertEqual(0, result.get("event_count", len(result.get("events", []))))

    def test_init_export_restore_never_overwrite(self):
        self.invoke("init", "--database", self.database)
        exported = self.root / "export.json"
        self.invoke("export", "--database", self.database, "--output", exported)
        before = exported.read_bytes()
        events = self.invoke("export", "--database", self.database)
        for arguments in (
            ("init", "--database", self.database),
            ("export", "--database", self.database, "--output", exported),
            ("restore", "--database", self.database, "--events", exported),
        ):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.invoke(*arguments)
        self.assertEqual(before, exported.read_bytes())
        self.assertEqual(events, self.invoke("export", "--database", self.database))

    def test_malformed_restore_does_not_create_database(self):
        exported = self.root / "malformed.json"
        for body in (b"null", b"[]", b'{"format":"wrong","events":[]}', b'{"format":"a","format":"b","events":[]}'):
            exported.write_bytes(body)
            with self.subTest(body=body), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.invoke("restore", "--database", self.database, "--events", exported)
            self.assertFalse(self.database.exists())

    def test_bad_evidence_json_rejected_before_any_database_access(self):
        for malformed in ('null', '[]', '{bad}', '{"side":"before","side":"after"}'):
            with self.subTest(malformed=malformed), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.invoke("decide", "--database", self.database, "--alert", "test", "--action", "retract",
                            "--reviewer", "fixture", "--reason", "fixture", "--expected-event", "token",
                            "--evidence-ref", malformed)
            self.assertFalse(self.database.exists())

    def test_required_project_arguments(self):
        arguments = {"--database": self.database, "--review": self.root / "review.json",
                     "--source-queue": self.root / "source.sqlite", "--reference-root": self.root,
                     "--output": self.root / "packet.json"}
        for omitted in arguments:
            flat = [part for pair in arguments.items() if pair[0] != omitted for part in pair]
            with self.subTest(omitted=omitted), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.invoke("build-project", *flat)
        self.assertFalse(self.database.exists())
        for omitted in ("--packet", "--review"):
            args = {"--packet": self.root / "packet.json", "--review": self.root / "admission.json"}
            del args[omitted]
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.invoke("import-project", "--database", self.database,
                            *[part for pair in args.items() for part in pair])
        self.assertFalse(self.database.exists())

    def test_build_opens_core_read_only_without_migration(self):
        core = self.root / "core # database.sqlite"
        connection = sqlite3.connect(core)
        connection.execute("CREATE TABLE sentinel (value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('preserved')")
        connection.commit()
        connection.close()
        before = core.read_bytes()
        output = self.root / "proposal.json"
        def inspect_read_only(connection, review_path, *, reference_root, source_queue):
            self.assertIs(connection.row_factory, sqlite3.Row)
            self.assertEqual("preserved", connection.execute("SELECT value FROM sentinel").fetchone()["value"])
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("INSERT INTO sentinel VALUES ('forbidden')")
            self.assertEqual(self.root / "claim-review.json", review_path)
            self.assertEqual(self.root, reference_root)
            self.assertEqual(self.root / "source.sqlite", source_queue)
            return {"packet_id": "fixture-dispatch-only", "not_real_evidence": True}
        with patch.object(cli.project_target_changes, "build_packet", side_effect=inspect_read_only):
            result = self.invoke("build-project", "--database", core, "--review", self.root / "claim-review.json",
                                 "--source-queue", self.root / "source.sqlite", "--reference-root", self.root,
                                 "--output", output)
        self.assertEqual("fixture-dispatch-only", result["packet_id"])
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), result["packet_sha256"])
        self.assertEqual(len(output.read_bytes()), result["bytes"])
        self.assertEqual(before, core.read_bytes())
        self.assertEqual({"packet_id": "fixture-dispatch-only", "not_real_evidence": True}, json.loads(output.read_bytes()))

    def test_build_missing_core_does_not_create_database_or_output(self):
        output = self.root / "packet.json"
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke("build-project", "--database", self.database, "--review", self.root / "review.json",
                        "--source-queue", self.root / "source.sqlite", "--reference-root", self.root,
                        "--output", output)
        self.assertFalse(self.database.exists())
        self.assertFalse(output.exists())

    def test_help_names_scope_and_commands(self):
        for arguments, required in (((), ("build-project", "import-project", "import-facility", "restore")),
                                    (("build-project",), ("Read-only", "no claim acceptance", "--source-queue")),
                                    (("import-project",), ("Packet-bound", "--packet")),
                                    (("export",), ("--as-of", "--output"))):
            output = io.StringIO()
            with patch("sys.argv", ["review-alerts-v2", *arguments, "--help"]), redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                cli.main()
            self.assertEqual(0, caught.exception.code)
            for text in required:
                self.assertIn(text, output.getvalue())


class LegacyAlertMigrationCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        legacy_fixtures.AICriticalAlertReviewTests.setUpClass()

    def setUp(self):
        self.fixture = legacy_fixtures.AICriticalAlertReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.database = self.root / "unified.sqlite"

    def invoke(self, *arguments):
        output = io.StringIO()
        with patch("sys.argv", ["review-alerts-v2", *map(str, arguments)]), redirect_stdout(output):
            cli.main()
        return json.loads(output.getvalue())

    def test_legacy_prefix_and_decision_cutoffs_survive_cli_restore(self):
        first = self.fixture._import()["alerts"][0]
        acknowledged = self.fixture._decide(first, "acknowledge")
        old = legacy.export_queue(self.fixture.db)
        path = self.root / "legacy.json"
        path.write_bytes(_pretty_bytes(old))
        self.invoke("restore", "--database", self.database, "--events", path)
        exported = self.invoke("export", "--database", self.database)
        self.assertEqual(old["events"], exported["events"])
        self.assertEqual(old, legacy.export_queue(self.fixture.db))
        self.assertEqual([acknowledged["id"]], [row["id"] for row in self.invoke("report", "--database", self.database)["alerts"]])
        for event in old["events"]:
            actual = self.invoke("report", "--database", self.database, "--as-of", event["recorded_at"])
            expected = legacy.queue_report(self.fixture.db, as_of=event["recorded_at"])
            self.assertTrue(all(row["origin"] == "baseline_facility" for row in actual["alerts"]))
            self.assertEqual(expected["alerts"], [
                {key: value for key, value in row.items() if key != "origin"}
                for row in actual["alerts"]
            ])
        output = self.root / "unified.json"
        self.invoke("export", "--database", self.database, "--output", output)
        restored = self.root / "restored.sqlite"
        self.invoke("restore", "--database", restored, "--events", output)
        self.assertEqual(exported, self.invoke("export", "--database", restored))
        self.invoke("verify", "--database", restored)

    def test_import_facility_continues_review_in_v2_queue(self):
        self.invoke("init", "--database", self.database)
        with patch.object(review, "_now", return_value="2026-09-07T10:00:00Z"):
            result = self.invoke("import-facility", "--database", self.database,
                "--bundle", self.fixture.bundle, "--prior", self.fixture.prior,
                "--current", self.fixture.current, "--review", self.fixture.admission)
        self.assertTrue(result["imported"])
        alert = result["alerts"][0]
        self.assertEqual("baseline_facility", alert["origin"])
        with patch.object(review, "_now", return_value="2026-09-07T11:00:00Z"):
            resolved = self.invoke("decide", "--database", self.database, "--alert", alert["id"],
                "--action", "resolve", "--reviewer", "CLI fixture", "--reason", "Bound legacy evidence fixture",
                "--expected-event", alert["last_event_id"], "--evidence-ref", json.dumps(self.fixture._ref(alert)))
        self.assertEqual("resolved", resolved["status"])
        self.assertFalse(resolved["delivery_eligible"])
        self.assertEqual(0, legacy.queue_report(self.fixture.db)["event_count"])


class ProjectAlertCLITests(unittest.TestCase):
    def setUp(self):
        self.fixture = project_fixtures.ProjectTargetReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.accept()
        self.database = self.root / "alerts-v2.sqlite"
        self.packet_path = self.root / "proposal.json"
        self.admission_path = self.root / "alert-admission.json"
        self.clock_patch = patch.object(cli.project_target_changes, "_now", side_effect=self.fixture.now)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        now = patch.object(review, "_now", side_effect=self.fixture.now)
        now.start()
        self.addCleanup(now.stop)

    def invoke(self, *arguments):
        output = io.StringIO()
        with patch("sys.argv", ["review-alerts-v2", *map(str, arguments)]), redirect_stdout(output):
            cli.main()
        return json.loads(output.getvalue())

    def build(self):
        core_path = self.root / "core.sqlite"
        before = core_path.read_bytes()
        result = self.invoke("build-project", "--database", core_path,
            "--review", self.fixture.review_path, "--source-queue", self.fixture.queue,
            "--reference-root", self.root, "--output", self.packet_path)
        self.assertEqual(before, core_path.read_bytes())
        self.assertEqual(hashlib.sha256(self.packet_path.read_bytes()).hexdigest(), result["packet_sha256"])
        return result

    def test_build_import_decide_and_offline_restore_real_fixture(self):
        self.invoke("init", "--database", self.database)
        built = self.build()
        packet = json.loads(self.packet_path.read_bytes())
        admission = {"format": review.ADMISSION_FORMAT, "purpose": "alert_review_only",
            "reviewer": "CLI fixture", "reviewed_at": self.fixture.now(),
            "reason": "Fixture source-stated target comparison only",
            "packet_sha256": built["packet_sha256"], "expected_head_event_id": None}
        self.admission_path.write_bytes(_pretty_bytes(admission))
        imported = self.invoke("import-project", "--database", self.database,
                               "--packet", self.packet_path, "--review", self.admission_path)
        self.assertTrue(imported["imported"])
        self.assertEqual(1, imported["alert_count"])
        alert = imported["alerts"][0]
        self.assertEqual("source_native_project", alert["origin"])
        self.assertIsNone(alert["confidence"])
        self.assertFalse(alert["delivery_eligible"])
        before = self.invoke("report", "--database", self.database, "--as-of", admission["reviewed_at"])
        self.assertEqual([], before["alerts"])
        repeated = self.invoke("import-project", "--database", self.database,
                               "--packet", self.packet_path, "--review", self.admission_path)
        self.assertFalse(repeated["imported"])
        self.assertEqual(1, repeated["event_count"])
        acknowledged = self.invoke("decide", "--database", self.database, "--alert", alert["id"],
            "--action", "acknowledge", "--reviewer", "CLI fixture", "--reason", "Fixture acknowledgement",
            "--expected-event", alert["last_event_id"])
        claim = packet["claims"]["after"]
        link = claim["evidence"][0]
        ref = {"packet_id": built["packet_sha256"], "side": "after", "claim_id": claim["id"],
               "evidence_id": link["evidence_id"], "fragment_sha256": link["fragment_sha256"]}
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke("decide", "--database", self.database, "--alert", alert["id"],
                "--action", "resolve", "--reviewer", "CLI fixture", "--reason", "Stale fixture",
                "--expected-event", alert["last_event_id"], "--evidence-ref", json.dumps(ref))
        resolved = self.invoke("decide", "--database", self.database, "--alert", alert["id"],
            "--action", "resolve", "--reviewer", "CLI fixture", "--reason", "Close review, not construction",
            "--expected-event", acknowledged["last_event_id"], "--evidence-ref", json.dumps(ref))
        self.assertEqual("resolved", resolved["status"])
        self.assertFalse(resolved["delivery_eligible"])
        exported = self.root / "events-v2.json"
        self.invoke("export", "--database", self.database, "--output", exported)
        restored = self.root / "restored-v2.sqlite"
        with patch.object(cli.project_target_changes, "build_packet", side_effect=AssertionError("offline restore must not rebuild")):
            self.invoke("restore", "--database", restored, "--events", exported)
            self.invoke("verify", "--database", restored)
        self.assertEqual(self.invoke("export", "--database", self.database),
                         self.invoke("export", "--database", restored))
        self.assertEqual(2, self.fixture.connection.execute("SELECT COUNT(*) FROM claim_versions").fetchone()[0])

    def test_existing_packet_output_is_preserved(self):
        self.build()
        before = self.packet_path.read_bytes()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.build()
        self.assertEqual(before, self.packet_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
