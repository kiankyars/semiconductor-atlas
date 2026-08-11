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
from semiconductor_atlas.taiwan_mof_snapshot import (
    TAIWAN_MOF_ALLOWLIST_FILENAME,
    TAIWAN_MOF_MATCHED_FILENAME,
)

from tests.test_ingest_taiwan_mof import ACCEPTED_AT, _row, _snapshot
from tests._taiwan_mof_fixtures import create_source_snapshots


def _run(
    database: Path,
    snapshot: Path,
    moenv: Path,
    factory: Path,
    *extra: str,
) -> tuple[int, dict[str, object]]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(
            [
                "ingest-taiwan-mof-snapshot",
                "--database",
                str(database),
                "--snapshot",
                str(snapshot),
                "--moenv-snapshot",
                str(moenv),
                "--factory-snapshot",
                str(factory),
                "--accepted-at",
                ACCEPTED_AT,
                *extra,
            ]
        )
    return code, json.loads(output.getvalue()) if output.getvalue() else {}


class TaiwanMOFCLITests(unittest.TestCase):
    def test_verifies_imports_commits_and_reports_all_snapshot_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
            snapshot = _snapshot(
                root,
                "snapshot",
                [_row("00123456")],
                moenv=moenv,
                factory=factory,
            )
            database = root / "atlas.sqlite"

            code, result = _run(database, snapshot.root, moenv, factory)

            self.assertEqual(0, code)
            self.assertTrue(result["complete_refresh"])
            self.assertEqual(4, result["snapshot_inputs_verified"])
            self.assertEqual(2, result["bound_source_snapshots_verified"])
            self.assertEqual(1, result["taiwan_mof"]["tax_units_imported"])
            self.assertEqual(
                snapshot.manifest_sha256,
                result["snapshot_integrity"]["manifest"]["sha256"],
            )
            self.assertEqual(
                snapshot.allowlist_sha256,
                result["snapshot_integrity"]["allowlist"]["sha256"],
            )
            self.assertEqual(
                snapshot.matched_sha256,
                result["snapshot_integrity"]["matched_derivative"]["sha256"],
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
            self.assertEqual(snapshot.publisher_date, parameters["publisher_date"])
            self.assertEqual(ACCEPTED_AT, parameters["accepted_at"])

    def test_partial_flag_records_nonclosing_refresh_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
            snapshot = _snapshot(
                root,
                "snapshot",
                [_row("00123456")],
                moenv=moenv,
                factory=factory,
            )
            database = root / "atlas.sqlite"

            code, result = _run(database, snapshot.root, moenv, factory, "--partial")

            self.assertEqual(0, code)
            self.assertFalse(result["complete_refresh"])
            self.assertFalse(result["taiwan_mof"]["complete_refresh"])

    def test_acceptance_rechecks_all_snapshot_and_source_inputs_and_rolls_back(
        self,
    ) -> None:
        targets = (
            "matched",
            "allowlist",
            "raw",
            "manifest",
            "bound-source-manifest",
        )
        for target_name in targets:
            with (
                self.subTest(target=target_name),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                moenv, factory = create_source_snapshots(root)
                snapshot = _snapshot(
                    root,
                    "snapshot",
                    [_row("00123456")],
                    moenv=moenv,
                    factory=factory,
                )
                database = root / "atlas.sqlite"
                target = {
                    "matched": snapshot.root / TAIWAN_MOF_MATCHED_FILENAME,
                    "allowlist": snapshot.root / TAIWAN_MOF_ALLOWLIST_FILENAME,
                    "raw": snapshot.root / snapshot.raw_path,
                    "manifest": snapshot.root / "manifest.json",
                    "bound-source-manifest": moenv / "manifest.json",
                }[target_name]
                real_import = cli.import_taiwan_mof_snapshot

                def import_then_mutate(*args, **kwargs):
                    result = real_import(*args, **kwargs)
                    target.write_bytes(b"changed after importer verification")
                    return result

                error = io.StringIO()
                with (
                    mock.patch(
                        "semiconductor_atlas.cli.import_taiwan_mof_snapshot",
                        side_effect=import_then_mutate,
                    ),
                    contextlib.redirect_stderr(error),
                ):
                    code, _ = _run(database, snapshot.root, moenv, factory)

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
