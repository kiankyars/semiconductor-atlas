from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from semiconductor_atlas.adapters.epa_frs import (
    FRS_ARCHIVE_MEMBER,
    FRS_ATTRIBUTION,
    FRS_DATA_AS_OF_BASIS,
    FRS_DATA_AS_OF_SOURCE_URL,
    FRS_DOCUMENTATION_MEMBER,
    FRS_FILTER_VERSION,
    FRS_HEADERS,
    FRS_NAICS_CODES,
    FRS_LICENSE,
    FRS_LICENSE_URL,
    FRS_OFFICIAL_ARCHIVE_URL,
    FRS_RECORD_TYPE,
    FRS_SCOPE,
    FRS_SIC_CODES,
    parse_candidate_jsonl_bytes,
)
from semiconductor_atlas.cli import main
from semiconductor_atlas.snapshot import EPA_FRS_RAW_RECORD_TYPE, verify_snapshot
from tests.test_epa_frs_snapshot import _fixture


def _snapshot(root: Path) -> None:
    raw, manifest = _fixture(root, candidate_name="CLI candidate")
    (root / "candidates.jsonl").write_bytes(raw)
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


class EPAFRSCLITests(unittest.TestCase):
    def test_ingest_frs_snapshot_verifies_routes_and_commits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            _snapshot(snapshot)
            database = root / "atlas.sqlite"

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(
                    [
                        "ingest-frs-snapshot",
                        "--database",
                        str(database),
                        "--snapshot",
                        str(snapshot),
                        "--as-of",
                        "2026-07-01",
                        "--accepted-at",
                        "2026-07-20T02:00:00Z",
                    ]
                )
            self.assertEqual(0, code)
            result = json.loads(output.getvalue())
            self.assertEqual(1, result["epa_frs"]["candidates_imported"])
            self.assertEqual(2, result["snapshot_inputs_verified"])
            self.assertEqual("2026-07-20T02:00:00Z", result["accepted_at"])
            with contextlib.closing(sqlite3.connect(database)) as connection:
                parameters = json.loads(
                    connection.execute(
                        "SELECT parameters_json FROM ingestion_runs WHERE id = ?",
                        (result["epa_frs"]["ingestion_run_id"],),
                    ).fetchone()[0]
                )
            self.assertEqual(snapshot.name, parameters["source_snapshot"]["directory_name"])
            self.assertEqual(
                hashlib.sha256((snapshot / "manifest.json").read_bytes()).hexdigest(),
                parameters["source_snapshot"]["manifest_sha256"],
            )
            self.assertTrue(
                parameters["source_snapshot"]["raw_archive_relative_path"].startswith(
                    "raw/sha256/"
                )
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    0, main(["validate", "--database", str(database)])
                )
            self.assertTrue(json.loads(output.getvalue())["ok"])

    def test_accept_now_samples_clock_after_verification_and_records_basis(self) -> None:
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 7, 20, 3, 0, 0, tzinfo=tz or UTC)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            _snapshot(snapshot)
            output = io.StringIO()
            with (
                mock.patch("semiconductor_atlas.cli.datetime", FixedDatetime),
                contextlib.redirect_stdout(output),
            ):
                code = main(
                    [
                        "ingest-frs-snapshot",
                        "--database",
                        str(root / "atlas.sqlite"),
                        "--snapshot",
                        str(snapshot),
                        "--as-of",
                        "2026-07-01",
                        "--accept-now",
                    ]
                )
            self.assertEqual(0, code)
            result = json.loads(output.getvalue())
            self.assertEqual("2026-07-20T03:00:00Z", result["accepted_at"])
            self.assertEqual(
                "process_clock_after_snapshot_verification",
                result["acceptance_timestamp_basis"],
            )

    def test_raw_mutation_after_verification_rolls_back_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            _snapshot(snapshot)
            database = root / "atlas.sqlite"

            def verify_then_mutate(path):
                verified = verify_snapshot(path)
                verified.one(EPA_FRS_RAW_RECORD_TYPE).path.write_bytes(b"changed")
                return verified

            error = io.StringIO()
            with (
                mock.patch(
                    "semiconductor_atlas.cli.verify_snapshot",
                    side_effect=verify_then_mutate,
                ),
                contextlib.redirect_stderr(error),
            ):
                code = main(
                    [
                        "ingest-frs-snapshot",
                        "--database",
                        str(database),
                        "--snapshot",
                        str(snapshot),
                        "--as-of",
                        "2026-07-01",
                        "--accepted-at",
                        "2026-07-20T02:00:00Z",
                    ]
                )
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

    def test_manifest_mutation_after_verification_rolls_back_and_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            _snapshot(snapshot)
            database = root / "atlas.sqlite"
            manifest_path = snapshot / "manifest.json"
            original = manifest_path.read_bytes()

            def verify_then_mutate(path):
                verified = verify_snapshot(path)
                parsed = json.loads(original)
                manifest_path.write_text(
                    json.dumps(parsed, separators=(",", ":")), encoding="utf-8"
                )
                return verified

            error = io.StringIO()
            with (
                mock.patch(
                    "semiconductor_atlas.cli.verify_snapshot",
                    side_effect=verify_then_mutate,
                ),
                contextlib.redirect_stderr(error),
            ):
                code = main(
                    [
                        "ingest-frs-snapshot",
                        "--database",
                        str(database),
                        "--snapshot",
                        str(snapshot),
                        "--as-of",
                        "2026-07-01",
                        "--accepted-at",
                        "2026-07-20T02:00:00Z",
                    ]
                )
            self.assertEqual(1, code)
            self.assertIn("manifest.json", error.getvalue())
            self.assertIn("changed before acceptance", error.getvalue())
            with contextlib.closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    0,
                    connection.execute(
                        "SELECT COUNT(*) FROM ingestion_runs"
                    ).fetchone()[0],
                )

            manifest_path.write_bytes(original)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    0,
                    main(
                        [
                            "ingest-frs-snapshot",
                            "--database",
                            str(database),
                            "--snapshot",
                            str(snapshot),
                            "--as-of",
                            "2026-07-01",
                            "--accepted-at",
                            "2026-07-20T02:00:00Z",
                        ]
                    ),
                )
            with contextlib.closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    1,
                    connection.execute(
                        "SELECT COUNT(*) FROM ingestion_runs"
                    ).fetchone()[0],
                )

    def test_symlinked_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            _snapshot(snapshot)
            manifest_path = snapshot / "manifest.json"
            target = root / "manifest-target.json"
            manifest_path.replace(target)
            manifest_path.symlink_to(target)

            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                code = main(
                    [
                        "ingest-frs-snapshot",
                        "--database",
                        str(root / "atlas.sqlite"),
                        "--snapshot",
                        str(snapshot),
                        "--as-of",
                        "2026-07-01",
                        "--accepted-at",
                        "2026-07-20T02:00:00Z",
                    ]
                )
            self.assertEqual(1, code)
            self.assertIn("cannot read source snapshot manifest", error.getvalue())

    def test_fifo_manifest_is_rejected_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            os.mkfifo(snapshot / "manifest.json")

            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                code = main(
                    [
                        "ingest-frs-snapshot",
                        "--database",
                        str(root / "atlas.sqlite"),
                        "--snapshot",
                        str(snapshot),
                        "--as-of",
                        "2026-07-01",
                        "--accepted-at",
                        "2026-07-20T02:00:00Z",
                    ]
                )
            self.assertEqual(1, code)
            self.assertIn("not a regular file", error.getvalue())

    def test_fifo_raw_swap_at_acceptance_is_rejected_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            _snapshot(snapshot)

            def verify_then_replace_raw_with_fifo(path):
                verified = verify_snapshot(path)
                raw_path = verified.one(EPA_FRS_RAW_RECORD_TYPE).path
                raw_path.unlink()
                os.mkfifo(raw_path)
                return verified

            error = io.StringIO()
            with (
                mock.patch(
                    "semiconductor_atlas.cli.verify_snapshot",
                    side_effect=verify_then_replace_raw_with_fifo,
                ),
                contextlib.redirect_stderr(error),
            ):
                code = main(
                    [
                        "ingest-frs-snapshot",
                        "--database",
                        str(root / "atlas.sqlite"),
                        "--snapshot",
                        str(snapshot),
                        "--as-of",
                        "2026-07-01",
                        "--accepted-at",
                        "2026-07-20T02:00:00Z",
                    ]
                )
            self.assertEqual(1, code)
            self.assertIn("not a regular file", error.getvalue())

    def test_transient_candidate_swap_cannot_change_imported_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            _snapshot(snapshot)
            candidate_path = snapshot / "candidates.jsonl"
            original = candidate_path.read_bytes()
            alternate_payload = json.loads(original)
            alternate_payload["row"]["PRIMARY_NAME"] = "Transient replacement"
            alternate = (
                json.dumps(alternate_payload, separators=(",", ":")) + "\n"
            ).encode("utf-8")

            def swap_during_parse(raw):
                candidate_path.write_bytes(alternate)
                try:
                    return parse_candidate_jsonl_bytes(raw)
                finally:
                    candidate_path.write_bytes(original)

            with (
                mock.patch(
                    "semiconductor_atlas.ingest_epa_frs.parse_candidate_jsonl_bytes",
                    side_effect=swap_during_parse,
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "ingest-frs-snapshot",
                        "--database",
                        str(root / "atlas.sqlite"),
                        "--snapshot",
                        str(snapshot),
                        "--as-of",
                        "2026-07-01",
                        "--accepted-at",
                        "2026-07-20T02:00:00Z",
                    ]
                )
            self.assertEqual(0, code)
            with contextlib.closing(
                sqlite3.connect(root / "atlas.sqlite")
            ) as connection:
                names = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT values_.text_value
                        FROM scalar_values AS values_
                        JOIN claim_versions AS versions
                          ON versions.id = values_.claim_version_id
                        JOIN claim_series AS series ON series.id = versions.series_id
                        WHERE series.predicate = 'name'
                        """
                    )
                }
            self.assertEqual({"CLI candidate"}, names)
            self.assertEqual(original, candidate_path.read_bytes())

    def test_ingest_frs_snapshot_rejects_mismatched_world_date(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            _snapshot(snapshot)
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                code = main(
                    [
                        "ingest-frs-snapshot",
                        "--database",
                        str(root / "atlas.sqlite"),
                        "--snapshot",
                        str(snapshot),
                        "--as-of",
                        "2026-07-02",
                        "--accepted-at",
                        "2026-07-20T02:00:00Z",
                    ]
                )
            self.assertEqual(1, code)
            self.assertIn("must match the snapshot data_as_of", error.getvalue())


if __name__ == "__main__":
    unittest.main()
