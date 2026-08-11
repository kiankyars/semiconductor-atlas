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
from semiconductor_atlas.moenv_snapshot import (
    MOENV_CANDIDATE_FILENAME,
    MOENV_FULL_PACKAGE_URL,
    create_moenv_snapshot,
)
from tests.test_ingest_moenv import (
    DATASET_UPDATED_AT_BASIS,
    REQUEST_BODY,
    _archive_bytes,
    _row,
)


RETRIEVED_AT = "2026-07-20T06:00:00Z"
DATASET_UPDATED_AT = "2026-07-19T23:15:13Z"
ACCEPTED_AT = "2026-07-20T06:05:00Z"


def _snapshot(root: Path, name: str = "snapshot"):
    archive = root / f"{name}.zip"
    archive.write_bytes(_archive_bytes([_row("A0000001")]))
    return create_moenv_snapshot(
        archive,
        root / name,
        retrieved_at=RETRIEVED_AT,
        dataset_updated_at=DATASET_UPDATED_AT,
        dataset_updated_at_basis=DATASET_UPDATED_AT_BASIS,
        download_url=MOENV_FULL_PACKAGE_URL,
        acquisition_request_body=dict(REQUEST_BODY),
    )


def _run(database: Path, snapshot: Path, *extra: str) -> tuple[int, dict[str, object]]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(
            [
                "ingest-moenv-snapshot",
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


class MOENVCLITests(unittest.TestCase):
    def test_verifies_imports_commits_and_reports_counts_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = _snapshot(root)
            database = root / "atlas.sqlite"

            code, result = _run(database, snapshot.root)

            self.assertEqual(0, code)
            self.assertTrue(result["complete_refresh"])
            self.assertEqual(3, result["snapshot_inputs_verified"])
            self.assertEqual(1, result["moenv"]["facilities_imported"])
            self.assertEqual(1, result["moenv"]["variants_imported"])
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
            self.assertTrue(parameters["complete_refresh"])
            self.assertEqual(snapshot.manifest_sha256, parameters["manifest_sha256"])

    def test_partial_flag_records_non_closing_refresh_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = _snapshot(root)
            database = root / "atlas.sqlite"

            code, result = _run(database, snapshot.root, "--partial")

            self.assertEqual(0, code)
            self.assertFalse(result["complete_refresh"])
            self.assertFalse(result["moenv"]["complete_refresh"])
            with contextlib.closing(sqlite3.connect(database)) as connection:
                parameters = json.loads(
                    connection.execute(
                        "SELECT parameters_json FROM ingestion_runs"
                    ).fetchone()[0]
                )
            self.assertFalse(parameters["complete_refresh"])

    def test_acceptance_rechecks_each_snapshot_file_and_rolls_back(self) -> None:
        for target_name in ("candidate", "raw", "manifest"):
            with self.subTest(target=target_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                snapshot = _snapshot(root)
                database = root / "atlas.sqlite"
                real_import = cli.import_moenv_ems_candidates

                target = {
                    "candidate": snapshot.root / MOENV_CANDIDATE_FILENAME,
                    "raw": snapshot.root / snapshot.raw_path,
                    "manifest": snapshot.root / "manifest.json",
                }[target_name]

                def import_then_mutate(*args, **kwargs):
                    result = real_import(*args, **kwargs)
                    target.write_bytes(b"changed after import")
                    return result

                error = io.StringIO()
                with (
                    mock.patch(
                        "semiconductor_atlas.cli.import_moenv_ems_candidates",
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
