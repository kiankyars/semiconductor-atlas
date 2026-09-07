from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas.cli import main


class CLITests(unittest.TestCase):
    def test_init_validate_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "atlas.sqlite"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["init", "--database", str(database)]))
            self.assertEqual(5, json.loads(output.getvalue())["schema_version"])

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["validate", "--database", str(database)]))
            self.assertTrue(json.loads(output.getvalue())["ok"])

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    0,
                    main(
                        [
                            "summary",
                            "--database",
                            str(database),
                            "--as-of",
                            "2026-07-17",
                            "--recorded-at",
                            "2026-07-17T12:00:00Z",
                        ]
                    ),
                )
            self.assertEqual(0, json.loads(output.getvalue())["entities_total"])

    def test_validate_rejects_missing_database_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "missing.sqlite"
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(1, main(["validate", "--database", str(database)]))
            self.assertIn("does not exist", error.getvalue())
            self.assertFalse(database.exists())


if __name__ == "__main__":
    unittest.main()
