from __future__ import annotations

import hashlib
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from scripts import milestone_benchmark as cli
from semiconductor_atlas import milestone_benchmark as benchmark
from tests import test_milestone_benchmark as fixtures


class MilestoneBenchmarkCLITests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MilestoneBenchmarkTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.database = self.root / "fixture.sqlite"

    def _json(self, name, value):
        path = self.root / name
        path.write_bytes(benchmark.canonical_bytes(value))
        return str(path)

    def _call(self, *args, clocks=None, expected=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            if clocks is None:
                result = cli.main(list(args))
            else:
                with patch.object(benchmark, "_now", side_effect=clocks):
                    result = cli.main(list(args))
        self.assertEqual(expected, result, stderr.getvalue())
        return json.loads(stdout.getvalue()) if result == 0 else stderr.getvalue()

    def _sources(self, bodies):
        rows = []
        for digest, body in bodies.items():
            path = self.root / (digest + ".body")
            path.write_bytes(body)
            rows.append({"sha256": digest, "path": str(path)})
        return self._json("local-sources.json", {"format": cli.SOURCE_MANIFEST_FORMAT, "documents": rows})

    def test_full_cli_workflow_is_readonly_reference_only_and_no_overwrite(self):
        before = self.database.read_bytes()
        study = self.root / "study.json"
        self._call("freeze-study", "--database", str(self.database),
            "--specification", self._json("specification.json", self.fixture.spec),
            "--output", str(study), clocks=fixtures.STUDY_CLOCKS)
        model = self.root / "model.txt"
        model.write_bytes(b"engineering-only supplied scenario")
        vintage = self.root / "vintage.json"
        receipt = self._call("freeze-vintage", "--database", str(self.database),
            "--study", str(study), "--predictions", self._json("predictions.json", self.fixture._predictions()),
            "--model-artifact", str(model), "--configuration", self._json("config.json", {}),
            "--evidence-cutoff-at", fixtures.STUDY_CLOCKS[1], "--horizon-end", "2026-06-30",
            "--output", str(vintage), clocks=fixtures.VINTAGE_CLOCKS)
        self.assertEqual(hashlib.sha256(vintage.read_bytes()).hexdigest(), receipt["sha256"])
        checked = self._call("verify-vintage", "--database", str(self.database), "--vintage", str(vintage))
        self.assertEqual(receipt["artifact_sha256"], checked["verified_vintage_sha256"])
        rows, bodies = self.fixture._outcomes()
        sources = self._sources(bodies)
        review = self._json("review.json", {"reviewed_by": "Engineering fixture",
            "prior_exposure": "Exposed synthetic review, not independent", "outcomes": rows})
        outcomes = self.root / "outcomes.json"
        self._call("review-outcomes", "--database", str(self.database), "--vintage", str(vintage),
            "--source-manifest", sources, "--review", review, "--output", str(outcomes),
            clocks=fixtures.REVIEW_CLOCKS)
        self.assertNotIn("bodies", json.loads(outcomes.read_bytes()))
        report = self.root / "report.json"
        score_args = ("score", "--database", str(self.database), "--vintage", str(vintage),
            "--outcomes", str(outcomes), "--source-manifest", sources, "--output", str(report))
        self._call(*score_args, clocks=["2026-05-03T00:00:00Z"])
        result = json.loads(report.read_bytes())
        self.assertEqual(7, result["roster_cases"])
        self.assertIsNone(result["probability_calibration"])
        saved = report.read_bytes()
        self._call(*score_args, clocks=["2026-05-03T00:00:00Z"], expected=1)
        self.assertEqual(saved, report.read_bytes())
        self.assertEqual(before, self.database.read_bytes())

    def test_source_manifest_rejects_duplicates_wrong_bytes_relative_paths_and_symlinks(self):
        body = b"engineering fixture"
        digest = hashlib.sha256(body).hexdigest()
        path = self.root / "body.txt"
        path.write_bytes(body)
        alias = self.root / "body-link.txt"
        alias.symlink_to(path)
        rows = [
            [{"sha256": digest, "path": str(path)}] * 2,
            [{"sha256": "0" * 64, "path": str(path)}],
            [{"sha256": digest, "path": "body.txt"}],
            [{"sha256": digest, "path": str(alias)}],
        ]
        for index, documents in enumerate(rows):
            with self.subTest(index=index):
                manifest = self.root / (str(index) + ".json")
                manifest.write_bytes(benchmark.canonical_bytes({
                    "format": cli.SOURCE_MANIFEST_FORMAT, "documents": documents}))
                with self.assertRaises((ValueError, OSError)):
                    cli._bodies(manifest)

    def test_reader_rejects_duplicate_json_keys_empty_oversize_and_symlinked_inputs(self):
        path = self.root / "input.json"
        path.write_bytes(b'{"a":1,"a":2}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            cli._json(path)
        path.write_bytes(b"")
        with self.assertRaises(ValueError):
            cli._read(path)
        path.write_bytes(b"12345")
        with self.assertRaises(ValueError):
            cli._read(path, limit=4)
        alias = self.root / "alias.json"
        alias.symlink_to(path)
        with self.assertRaises((ValueError, OSError)):
            cli._read(alias)

    def test_database_cannot_be_created_or_followed_through_symlink(self):
        absent = self.root / "absent.sqlite"
        with self.assertRaises(OSError):
            with cli._database(absent):
                self.fail("missing database opened")
        self.assertFalse(absent.exists())
        alias = self.root / "alias.sqlite"
        alias.symlink_to(self.database)
        with self.assertRaises(ValueError):
            with cli._database(alias):
                self.fail("symlinked database opened")


if __name__ == "__main__":
    unittest.main()
