from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semiconductor_atlas import cli
from semiconductor_atlas.cli import main
from semiconductor_atlas.taiwan_factory_snapshot import (
    TAIWAN_FACTORY_CANDIDATE_FILENAME,
)
from tests.test_ingest_taiwan_factory import ACCEPTED_AT, _row, _snapshot


def _run(database: Path, snapshot: Path, *extra: str) -> tuple[int, dict[str, object]]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(
            [
                "ingest-taiwan-factory-snapshot",
                "--database",
                str(database),
                "--snapshot",
                str(snapshot),
                "--accepted-at",
                ACCEPTED_AT,
                *extra,
            ]
        )
    return code, json.loads(output.getvalue()) if output.getvalue() else {}


class TaiwanFactoryCLITests(unittest.TestCase):
    def test_verifies_imports_commits_and_reports_all_snapshot_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = _snapshot(root, "snapshot", [_row()])
            database = root / "atlas.sqlite"

            code, result = _run(database, snapshot.root)

            self.assertEqual(0, code)
            self.assertTrue(result["complete_refresh"])
            self.assertEqual(3, result["snapshot_inputs_verified"])
            self.assertEqual(1, result["taiwan_factory"]["facilities_imported"])
            self.assertEqual(
                snapshot.manifest_sha256,
                result["snapshot_integrity"]["manifest"]["sha256"],
            )
            self.assertEqual(
                snapshot.candidate_sha256,
                result["snapshot_integrity"]["candidate_derivative"]["sha256"],
            )
            self.assertEqual(
                snapshot.raw_sha256,
                result["snapshot_integrity"]["raw_archive"]["sha256"],
            )
            with contextlib.closing(sqlite3.connect(database)) as connection:
                parameters = json.loads(
                    connection.execute(
                        "SELECT parameters_json FROM ingestion_runs"
                    ).fetchone()[0]
                )
            self.assertEqual(snapshot.manifest_sha256, parameters["manifest_sha256"])
            self.assertEqual(snapshot.source_updated_at, parameters["source_updated_at"])
            self.assertEqual(snapshot.retrieved_at, parameters["source_retrieved_at"])
            self.assertEqual(ACCEPTED_AT, parameters["accepted_at"])

    def test_partial_flag_records_nonclosing_refresh_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = _snapshot(root, "snapshot", [_row()])
            database = root / "atlas.sqlite"

            code, result = _run(database, snapshot.root, "--partial")

            self.assertEqual(0, code)
            self.assertFalse(result["complete_refresh"])
            self.assertFalse(result["taiwan_factory"]["complete_refresh"])
            with contextlib.closing(sqlite3.connect(database)) as connection:
                parameters = json.loads(
                    connection.execute(
                        "SELECT parameters_json FROM ingestion_runs"
                    ).fetchone()[0]
                )
            self.assertFalse(parameters["complete_refresh"])

    def test_acceptance_rechecks_candidate_raw_and_manifest_and_rolls_back(self) -> None:
        for target_name in ("candidate", "raw", "manifest"):
            with self.subTest(target=target_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                snapshot = _snapshot(root, "snapshot", [_row()])
                database = root / "atlas.sqlite"
                real_import = cli.import_taiwan_factory_candidates
                target = {
                    "candidate": snapshot.root / TAIWAN_FACTORY_CANDIDATE_FILENAME,
                    "raw": snapshot.root / snapshot.raw_path,
                    "manifest": snapshot.root / "manifest.json",
                }[target_name]

                def import_then_mutate(*args, **kwargs):
                    result = real_import(*args, **kwargs)
                    target.write_bytes(b"changed after importer verification")
                    return result

                error = io.StringIO()
                with (
                    mock.patch(
                        "semiconductor_atlas.cli.import_taiwan_factory_candidates",
                        side_effect=import_then_mutate,
                    ),
                    contextlib.redirect_stderr(error),
                ):
                    code, _ = _run(database, snapshot.root)

                self.assertEqual(1, code)
                self.assertIn("changed before acceptance", error.getvalue())
                with contextlib.closing(sqlite3.connect(database)) as connection:
                    self.assertEqual(
                        0,
                        connection.execute(
                            "SELECT COUNT(*) FROM ingestion_runs"
                        ).fetchone()[0],
                    )
                    self.assertEqual(
                        0,
                        connection.execute(
                            "SELECT COUNT(*) FROM claim_versions"
                        ).fetchone()[0],
                    )


if __name__ == "__main__":
    unittest.main()
