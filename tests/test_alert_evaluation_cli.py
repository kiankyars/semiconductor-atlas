from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from scripts import evaluate_ai_critical_alerts as cli
from semiconductor_atlas import ai_critical_alert_review as ledger
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from tests.test_alert_evaluation import BASELINE, END, SCORE, START, EvaluationFixture


class AlertEvaluationCLITests(EvaluationFixture, unittest.TestCase):
    def invoke(self, *arguments):
        output = io.StringIO()
        with patch("sys.argv", ["command", *map(str, arguments)]), redirect_stdout(output):
            cli.main()
        return json.loads(output.getvalue())

    def freeze_cli(self):
        return self.invoke("freeze", "--history", self.history, "--baseline", BASELINE,
                           "--study-id", "cli-fixture", "--start", START, "--end", END,
                           "--output", self.frozen)

    def test_freeze_verify_and_score_are_offline_and_do_not_mutate_history_or_queue(self):
        self.fixture._import()
        self._export()
        before_queue = ledger.export_queue(self.fixture.db)
        before_history = self.history.read_bytes()
        with patch("urllib.request.urlopen", side_effect=AssertionError("must stay offline")):
            frozen = self.freeze_cli()
            self.assertEqual((7, 1), (frozen["cohort_count"], frozen["prediction_count"]))
            verified = self.invoke("verify", "--frozen", self.frozen)
            self.assertTrue(verified["verified"])
            self.assertEqual("cli-fixture", verified["study_id"])
            self.labels.write_bytes(_pretty_bytes(self._label_payload()))
            self.clock = SCORE
            report_path = self.root / "report.json"
            report = self.invoke("score", "--frozen", self.frozen, "--labels", self.labels, "--output", report_path)
            self.assertEqual(report, json.loads(report_path.read_bytes()))
            self.assertEqual(report, self.invoke("score", "--frozen", self.frozen, "--labels", self.labels))
        self.assertEqual(before_queue, ledger.export_queue(self.fixture.db))
        self.assertEqual(before_history, self.history.read_bytes())
        self.assertFalse(report["delivery_eligible"])
        self.assertFalse(report["phase3_gate_passed"])

    def test_freeze_and_score_refuse_existing_outputs(self):
        self.fixture._import()
        self._export()
        self.freeze_cli()
        frozen_bytes = self.frozen.read_bytes()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failed:
            self.freeze_cli()
        self.assertEqual(1, failed.exception.code)
        self.assertEqual(frozen_bytes, self.frozen.read_bytes())
        self.labels.write_bytes(_pretty_bytes(self._label_payload()))
        self.clock = SCORE
        output = self.root / "report.json"
        output.write_bytes(b"existing-user-report")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failed:
            self.invoke("score", "--frozen", self.frozen, "--labels", self.labels, "--output", output)
        self.assertEqual(1, failed.exception.code)
        self.assertEqual(b"existing-user-report", output.read_bytes())

    def test_invalid_frozen_or_labels_exit_without_score_artifact(self):
        self._freeze()
        self.labels.write_bytes(b'{"format":')
        self.clock = SCORE
        output = self.root / "must-not-exist.json"
        errors = io.StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit) as failed:
            self.invoke("score", "--frozen", self.frozen, "--labels", self.labels, "--output", output)
        self.assertEqual(1, failed.exception.code)
        self.assertTrue(errors.getvalue())
        self.assertFalse(output.exists())
        self.frozen.write_bytes(b"{}\n")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failed:
            self.invoke("verify", "--frozen", self.frozen)
        self.assertEqual(1, failed.exception.code)

    def test_freeze_requires_explicit_window_baseline_and_history(self):
        arguments = {"--history": self.history, "--baseline": BASELINE, "--study-id": "fixture",
                     "--start": START, "--end": END, "--output": self.frozen}
        for omitted in ("--history", "--baseline", "--start", "--end"):
            flattened = [part for pair in arguments.items() if pair[0] != omitted for part in pair]
            with self.subTest(omitted=omitted), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failed:
                self.invoke("freeze", *flattened)
            self.assertEqual(2, failed.exception.code)
            self.assertFalse(self.frozen.exists())


if __name__ == "__main__":
    unittest.main()
