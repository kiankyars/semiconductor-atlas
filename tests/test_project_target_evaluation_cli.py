from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from scripts import evaluate_project_targets as cli
from semiconductor_atlas import project_target_population as population, project_target_changes as producer
from semiconductor_atlas import project_target_evaluation as evaluation
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from tests import test_project_target_population as fixtures


class ProjectTargetEvaluationCLITests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProjectTargetPopulationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.core = self.root / "core.sqlite"
        self.frozen = self.root / "frozen.json"
        self.labels = self.root / "labels.json"
        self.report = self.root / "report.json"

    def invoke(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["evaluate_project_targets.py", *map(str, arguments)]), redirect_stdout(stdout), redirect_stderr(stderr):
            cli.main()
        return json.loads(stdout.getvalue())

    def freeze(self):
        with patch.object(population, "_now", return_value=fixtures.FROZEN), patch.object(producer, "_now", return_value=fixtures.FROZEN):
            return self.invoke("freeze", "--database", self.core, "--study", self.fixture.study_path,
                "--history", self.fixture.history_path, "--reference-root", self.root,
                "--source-queue", self.fixture.fixture.queue, "--output", self.frozen)

    def label(self):
        review = self.root / "outcome-review.json"
        review.write_bytes(_pretty_bytes({"fixture": True, "source_revision_supported": True,
                                         "independent": False, "production_outcome": None}))
        artifact = {"format": evaluation.LABEL_FORMAT, "frozen_sha256": population._hash(self.frozen.read_bytes()),
            "adjudicator": "fixture reviewer", "reviewed_at": "2026-09-07T19:00:00Z",
            "prior_exposure": "Synthetic fixture, not independently blinded evidence.",
            "coverage_statement": "Exactly one synthetic accepted comparison; no publisher completeness.",
            "supporting_reviews": [{"path": review.name, "sha256": population._hash(review.read_bytes())}],
            "adjudications": [{"comparison_id": self.fixture.first["run_id"], "verdict": "supported_revision",
                "reason": "Fixture supports source revision only", "review_sha256": population._hash(review.read_bytes()),
                "locator": "source_revision_supported"}]}
        self.labels.write_bytes(_pretty_bytes(artifact))

    def test_real_read_only_freeze_verify_source_replay_and_score(self):
        self.fixture.admit()
        before = self.core.read_bytes()
        summary = self.freeze()
        self.assertEqual(population._hash(self.frozen.read_bytes()), summary["sha256"])
        self.label()
        with patch.object(population, "_now", return_value="2026-09-07T20:00:00Z"), \
                patch.object(producer, "_now", return_value="2026-09-07T20:00:00Z"), \
                patch.object(evaluation, "_now", return_value="2026-09-07T20:00:00Z"):
            verified = self.invoke("verify", "--frozen", self.frozen)
            self.assertEqual((1, 1), (verified["opportunities"], verified["predictions"]))
            external = self.invoke("verify-sources", "--database", self.core, "--frozen", self.frozen,
                "--reference-root", self.root, "--source-queue", self.fixture.fixture.queue)
            self.assertTrue(external["source_acceptance_replayed"])
            self.invoke("score", "--frozen", self.frozen, "--labels", self.labels, "--output", self.report)
        result = json.loads(self.report.read_bytes())
        self.assertEqual(1, result["counts"]["accepted_opportunities_in_window"])
        self.assertIsNone(result["metrics"]["detection_recall"])
        self.assertFalse(result["phase3_gate_passed"])
        self.assertEqual(before, self.core.read_bytes())

    def test_freeze_and_score_outputs_are_new_only(self):
        self.freeze()
        original = self.frozen.read_bytes()
        with self.assertRaises(SystemExit):
            self.freeze()
        self.assertEqual(original, self.frozen.read_bytes())
        self.label()
        self.report.write_bytes(b"user-owned report")
        with patch.object(population, "_now", return_value="2026-09-07T20:00:00Z"), \
                patch.object(evaluation, "_now", return_value="2026-09-07T20:00:00Z"), self.assertRaises(SystemExit):
            self.invoke("score", "--frozen", self.frozen, "--labels", self.labels, "--output", self.report)
        self.assertEqual(b"user-owned report", self.report.read_bytes())

    def test_missing_database_is_not_created(self):
        missing = self.root / "missing core.sqlite"
        with self.assertRaises(SystemExit):
            self.invoke("freeze", "--database", missing, "--study", self.fixture.study_path,
                "--history", self.fixture.history_path, "--reference-root", self.root,
                "--source-queue", self.fixture.fixture.queue, "--output", self.frozen)
        self.assertFalse(missing.exists())
        self.assertFalse(self.frozen.exists())

    def test_missing_accepted_review_aborts_before_output(self):
        self.fixture.write_study(reviews=[])
        with self.assertRaises(SystemExit):
            self.freeze()
        self.assertFalse(self.frozen.exists())

    def test_tampered_population_is_not_verified(self):
        self.freeze()
        artifact = json.loads(self.frozen.read_bytes())
        artifact["opportunities"] = []
        self.frozen.write_bytes(_pretty_bytes(artifact))
        with patch.object(population, "_now", return_value=fixtures.FROZEN), self.assertRaises(SystemExit):
            self.invoke("verify", "--frozen", self.frozen)

    def test_help_explains_read_only_and_new_only_boundaries(self):
        stdout = io.StringIO()
        with patch("sys.argv", ["evaluate_project_targets.py", "freeze", "--help"]), redirect_stdout(stdout), self.assertRaises(SystemExit):
            cli.main()
        self.assertIn("read-only core database", stdout.getvalue())
        self.assertIn("New-only output file", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
