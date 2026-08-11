from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import semiconductor_atlas.eea_mdbtools as eea_mdbtools
from semiconductor_atlas.eea_mdbtools import (
    EEA_MDBTOOLS_DATE_FORMAT,
    EEA_MDBTOOLS_DATETIME_FORMAT,
    EEA_MDBTOOLS_NULL_SENTINEL,
    EEAMDBToolsError,
    extract_eea_accdb,
)


IMAGE_ID = "sha256:" + "a" * 64
ALLOWED_TABLES = ("Facilities", "Functions")
PACKAGE_MANIFEST = b"base-files\tfixture\nmdbtools\t1.0.1-0.1\n"
BINARY_MANIFEST = b"fixture  /usr/bin/mdb-export\n"
FACILITIES_CSV = (
    b"Facility_INSPIRE_ID,name,reported_date,nullable\n"
    b'"EU.F1","Fab, ""One""","2026-01-01",'
    + EEA_MDBTOOLS_NULL_SENTINEL.encode("ascii")
    + b"\n"
    b'"EU.F2","Line\n""Two""","2026-02-03",""\n'
)
LITERAL_QUOTE_ESCAPE_CSV = (
    b"Facility_INSPIRE_ID,name,reported_date,nullable\n"
    b'"EU.F1","Fab, quote"Onequote"","2026-01-01",'
    + EEA_MDBTOOLS_NULL_SENTINEL.encode("ascii")
    + b"\n"
    b'"EU.F2","Line\nquote"Twoquote"","2026-02-03",""\n'
)
FUNCTIONS_CSV = b'Facility_INSPIRE_ID,NACE\n"EU.F1","26.11"\n"EU.F2","26.11"\n'


@contextlib.contextmanager
def _fixture_manifest_pins():
    with (
        mock.patch.object(
            eea_mdbtools,
            "EEA_MDBTOOLS_PACKAGE_MANIFEST_SHA256",
            hashlib.sha256(PACKAGE_MANIFEST).hexdigest(),
        ),
        mock.patch.object(
            eea_mdbtools,
            "EEA_MDBTOOLS_BINARY_MANIFEST_SHA256",
            hashlib.sha256(BINARY_MANIFEST).hexdigest(),
        ),
    ):
        yield


class _FakeCommandRunner:
    def __init__(self, *, input_path: Path | None = None) -> None:
        self.input_path = input_path
        self.calls: list[tuple[str, ...]] = []
        self.overrides: dict[str, tuple[bytes, bytes, int]] = {}
        self.mutate_on: str | None = None

    def _key_and_stdout(self, argv: tuple[str, ...]) -> tuple[str, bytes]:
        if argv[:3] == ("docker", "image", "inspect"):
            return "image_inspect", f"{IMAGE_ID}\tlinux\tarm64\n".encode("ascii")
        image_index = argv.index(IMAGE_ID)
        command = argv[image_index + 1 :]
        if command == ("mdb-export", "--version"):
            return "version", b"mdbtools v1.0.1\n"
        if command == ("/bin/cat", "/opt/tool-manifest/packages.tsv"):
            return "package_manifest", PACKAGE_MANIFEST
        if command == ("/bin/cat", "/opt/tool-manifest/binaries.sha256"):
            return "binary_manifest", BINARY_MANIFEST
        if command[0] == "mdb-ver":
            return "database_format", b"ACE12\n"
        if command[0] == "mdb-tables":
            return "inventory", b"Facilities\nFunctions\n"
        if command[0] == "mdb-schema":
            return (
                "schema",
                b"CREATE TABLE [Facilities] ([Facility_INSPIRE_ID] Text);\n",
            )
        if command[0] == "mdb-count":
            return f"count:{command[-1]}", b"2\n"
        if command[0] == "mdb-export":
            table = command[-1]
            csv_raw = FACILITIES_CSV if table == "Facilities" else FUNCTIONS_CSV
            # mdb-export treats --escape=quote as a literal escape string.
            if table == "Facilities" and "--escape=quote" in command:
                csv_raw = LITERAL_QUOTE_ESCAPE_CSV
            return f"export:{table}", csv_raw
        raise AssertionError(f"unexpected command: {argv!r}")

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        stdin: int,
        stdout,
        stderr,
        check: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        self.assert_run_contract(
            stdin=stdin,
            check=check,
            timeout=timeout,
        )
        key, default_stdout = self._key_and_stdout(argv)
        output, error, returncode = self.overrides.get(
            key,
            (default_stdout, b"", 0),
        )
        stdout.write(output)
        stderr.write(error)
        if key == self.mutate_on:
            if self.input_path is None:
                raise AssertionError("mutating fake has no input path")
            self.input_path.write_bytes(self.input_path.read_bytes() + b"mutation")
        return subprocess.CompletedProcess(argv, returncode)

    @staticmethod
    def assert_run_contract(*, stdin: int, check: bool, timeout: int) -> None:
        if stdin != subprocess.DEVNULL:
            raise AssertionError("stdin must be disconnected")
        if check:
            raise AssertionError("runner must retain outputs before checking status")
        if timeout <= 0:
            raise AssertionError("every command must have a positive timeout")


def _fixture_paths(root: Path, namespace: str = "fixture") -> tuple[Path, Path]:
    parent = root / namespace
    parent.mkdir()
    accdb = parent / "source.accdb"
    accdb.write_bytes(b"not executable Access fixture bytes")
    return accdb, parent / "output"


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


class EEAMDBToolsTests(unittest.TestCase):
    def test_success_is_canonical_and_uses_hardened_offline_docker_commands(
        self,
    ) -> None:
        self.assertEqual(
            "__SEMICONDUCTOR_ATLAS_MDB_NULL_48c374a5__",
            EEA_MDBTOOLS_NULL_SENTINEL,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accdb, output = _fixture_paths(root)
            fake = _FakeCommandRunner(input_path=accdb)
            runner = mock.Mock(side_effect=fake)
            with _fixture_manifest_pins():
                result = extract_eea_accdb(
                    image_id=IMAGE_ID,
                    accdb_path=accdb,
                    output_directory=output,
                    allowed_tables=ALLOWED_TABLES,
                    command_runner=runner,
                )

            self.assertEqual("ACE12", result.database_format)
            self.assertEqual(ALLOWED_TABLES, result.inventory_tables)
            self.assertEqual(
                (2, 2), tuple(item.row_count for item in result.table_exports)
            )
            self.assertEqual(
                (4, 2), tuple(item.column_count for item in result.table_exports)
            )
            self.assertEqual(1, result.table_exports[0].null_field_count)
            metadata_raw = result.metadata_path.read_bytes()
            metadata = json.loads(metadata_raw)
            self.assertEqual(_canonical_json_bytes(metadata), metadata_raw)
            self.assertEqual(
                hashlib.sha256(metadata_raw).hexdigest(), result.metadata_sha256
            )
            self.assertNotIn(os.fspath(accdb), metadata_raw.decode("utf-8"))
            self.assertEqual(ALLOWED_TABLES, tuple(metadata["allowed_tables"]["names"]))
            self.assertEqual(
                {
                    "binary_format": "hex",
                    "csv_dialect": "mdb-export_v1.0.1_defaults",
                    "csv_delimiter": ",",
                    "csv_encoding": "utf-8",
                    "csv_escape": "double_quote",
                    "csv_header": True,
                    "csv_quote": '"',
                    "csv_row_delimiter": "LF",
                    "date_format": EEA_MDBTOOLS_DATE_FORMAT,
                    "datetime_format": EEA_MDBTOOLS_DATETIME_FORMAT,
                    "null_sentinel": EEA_MDBTOOLS_NULL_SENTINEL,
                },
                metadata["serialization"],
            )
            with result.table_exports[0].csv_path.open(
                "r", encoding="utf-8", newline=""
            ) as stream:
                csv_rows = list(csv.reader(stream, dialect="excel", strict=True))
            self.assertEqual('Fab, "One"', csv_rows[1][1])
            self.assertEqual('Line\n"Two"', csv_rows[2][1])

            docker_runs = [call for call in fake.calls if call[:2] == ("docker", "run")]
            self.assertEqual(10, len(docker_runs))
            required_flags = {
                "--pull=never",
                "--platform=linux/arm64",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--pids-limit=64",
                "--memory=4g",
                "--memory-swap=4g",
                "--cpus=2",
                "--user=65534:65534",
            }
            for call in docker_runs:
                self.assertTrue(required_flags.issubset(call))
                mount = next(
                    argument for argument in call if argument.startswith("--mount=")
                )
                self.assertIn("readonly", mount)
                self.assertEqual(IMAGE_ID, call[call.index(IMAGE_ID)])
            export_call = next(
                call
                for call in docker_runs
                if "mdb-export" in call and "--version" not in call
            )
            self.assertEqual(
                (
                    "mdb-export",
                    f"--date-format={EEA_MDBTOOLS_DATE_FORMAT}",
                    f"--datetime-format={EEA_MDBTOOLS_DATETIME_FORMAT}",
                    f"--null={EEA_MDBTOOLS_NULL_SENTINEL}",
                    "--bin=hex",
                    "/input/source.accdb",
                    "Facilities",
                ),
                export_call[export_call.index(IMAGE_ID) + 1 :],
            )

            retained_stderr = sorted(output.rglob("*.stderr.txt"))
            self.assertEqual(11, len(retained_stderr))
            self.assertTrue(all(path.read_bytes() == b"" for path in retained_stderr))

    def test_dockerfile_uses_proven_arm64_snapshot_and_package_pins(self) -> None:
        dockerfile = (
            Path(__file__).parents[1] / "containers" / "eea-mdbtools" / "Dockerfile"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "FROM docker.io/library/debian@sha256:"
            "e9606f88b5f49b14d013d5c6d54ac7e11a48e13a6ec4c99d952330d03ddc703f",
            dockerfile,
        )
        self.assertNotIn("FROM --platform", dockerfile)
        self.assertIn(
            "snapshot.debian.org/archive/debian/20260610T000000Z/ trixie main",
            dockerfile,
        )
        self.assertIn("DEBIAN_FRONTEND=noninteractive apt-get install", dockerfile)
        self.assertIn("mdbtools=1.0.1-0.1", dockerfile)

    def test_fixed_inputs_produce_byte_identical_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accdb_a, output_a = _fixture_paths(root, "a")
            accdb_b, output_b = _fixture_paths(root, "b")
            with _fixture_manifest_pins():
                first = extract_eea_accdb(
                    image_id=IMAGE_ID,
                    accdb_path=accdb_a,
                    output_directory=output_a,
                    allowed_tables=ALLOWED_TABLES,
                    command_runner=_FakeCommandRunner(input_path=accdb_a),
                )
                second = extract_eea_accdb(
                    image_id=IMAGE_ID,
                    accdb_path=accdb_b,
                    output_directory=output_b,
                    allowed_tables=ALLOWED_TABLES,
                    command_runner=_FakeCommandRunner(input_path=accdb_b),
                )
            self.assertEqual(
                first.metadata_path.read_bytes(), second.metadata_path.read_bytes()
            )

    def test_rejects_mutable_image_path_and_non_exact_table_inputs_before_runner(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accdb, output = _fixture_paths(root)
            invalid_cases = (
                ("mutable image", "debian:latest", ALLOWED_TABLES),
                ("uppercase image", "sha256:" + "A" * 64, ALLOWED_TABLES),
                ("list", IMAGE_ID, ["Facilities"]),
                ("empty", IMAGE_ID, ()),
                ("unsorted", IMAGE_ID, ("Functions", "Facilities")),
                ("duplicate", IMAGE_ID, ("Facilities", "Facilities")),
                ("pattern", IMAGE_ID, ("Fac*",)),
                ("option", IMAGE_ID, ("--help",)),
                ("path", IMAGE_ID, ("Facility/Rows",)),
                ("control", IMAGE_ID, ("Facility\nRows",)),
            )
            for label, image_id, allowed_tables in invalid_cases:
                with self.subTest(label=label):
                    runner = mock.Mock()
                    with self.assertRaises(ValueError):
                        extract_eea_accdb(
                            image_id=image_id,
                            accdb_path=accdb,
                            output_directory=output,
                            allowed_tables=allowed_tables,
                            command_runner=runner,
                        )
                    runner.assert_not_called()
                    self.assertFalse(output.exists())

    def test_rejects_non_absolute_non_regular_and_existing_paths_before_runner(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accdb, output = _fixture_paths(root)
            runner = mock.Mock()
            with self.assertRaisesRegex(ValueError, "absolute"):
                extract_eea_accdb(
                    image_id=IMAGE_ID,
                    accdb_path=Path("source.accdb"),
                    output_directory=output,
                    allowed_tables=ALLOWED_TABLES,
                    command_runner=runner,
                )
            linked = root / "linked.accdb"
            linked.symlink_to(accdb)
            with self.assertRaisesRegex(ValueError, "regular file"):
                extract_eea_accdb(
                    image_id=IMAGE_ID,
                    accdb_path=linked,
                    output_directory=output,
                    allowed_tables=ALLOWED_TABLES,
                    command_runner=runner,
                )
            output.mkdir()
            with self.assertRaises(FileExistsError):
                extract_eea_accdb(
                    image_id=IMAGE_ID,
                    accdb_path=accdb,
                    output_directory=output,
                    allowed_tables=ALLOWED_TABLES,
                    command_runner=runner,
                )
            runner.assert_not_called()

    def test_fails_on_warning_and_retains_export_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accdb, output = _fixture_paths(root)
            fake = _FakeCommandRunner(input_path=accdb)
            fake.overrides["export:Facilities"] = (
                FACILITIES_CSV,
                b"fixture warning\n",
                0,
            )
            with (
                _fixture_manifest_pins(),
                self.assertRaisesRegex(EEAMDBToolsError, "warnings are fatal"),
            ):
                extract_eea_accdb(
                    image_id=IMAGE_ID,
                    accdb_path=accdb,
                    output_directory=output,
                    allowed_tables=ALLOWED_TABLES,
                    command_runner=fake,
                )
            stderr_paths = list((output / "tables").glob("*.export.stderr.txt"))
            self.assertEqual(1, len(stderr_paths))
            self.assertEqual(b"fixture warning\n", stderr_paths[0].read_bytes())
            self.assertFalse((output / "metadata.json").exists())

    def test_fails_on_nonzero_status_and_retains_count_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accdb, output = _fixture_paths(root)
            fake = _FakeCommandRunner(input_path=accdb)
            fake.overrides["count:Facilities"] = (b"", b"fixture failure\n", 9)
            with (
                _fixture_manifest_pins(),
                self.assertRaisesRegex(EEAMDBToolsError, "status 9"),
            ):
                extract_eea_accdb(
                    image_id=IMAGE_ID,
                    accdb_path=accdb,
                    output_directory=output,
                    allowed_tables=ALLOWED_TABLES,
                    command_runner=fake,
                )
            stderr_paths = list((output / "tables").glob("*.count.stderr.txt"))
            self.assertEqual(1, len(stderr_paths))
            self.assertEqual(b"fixture failure\n", stderr_paths[0].read_bytes())
            self.assertFalse((output / "metadata.json").exists())

    def test_fails_on_missing_allowed_inventory_before_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accdb, output = _fixture_paths(root)
            fake = _FakeCommandRunner(input_path=accdb)
            fake.overrides["inventory"] = (b"Facilities\n", b"", 0)
            with (
                _fixture_manifest_pins(),
                self.assertRaisesRegex(
                    EEAMDBToolsError, "absent from the exact inventory"
                ),
            ):
                extract_eea_accdb(
                    image_id=IMAGE_ID,
                    accdb_path=accdb,
                    output_directory=output,
                    allowed_tables=ALLOWED_TABLES,
                    command_runner=fake,
                )
            self.assertFalse(any("mdb-count" in call for call in fake.calls))
            self.assertFalse((output / "metadata.json").exists())

    def test_fails_on_count_mismatch_and_truncated_csv(self) -> None:
        cases = (
            ("count mismatch", b"3\n", FACILITIES_CSV, "parser counted 2"),
            ("truncated", b"2\n", FACILITIES_CSV[:-1], "is truncated"),
        )
        for label, count_raw, csv_raw, pattern in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                accdb, output = _fixture_paths(root)
                fake = _FakeCommandRunner(input_path=accdb)
                fake.overrides["count:Facilities"] = (count_raw, b"", 0)
                fake.overrides["export:Facilities"] = (csv_raw, b"", 0)
                with (
                    _fixture_manifest_pins(),
                    self.assertRaisesRegex(EEAMDBToolsError, pattern),
                ):
                    extract_eea_accdb(
                        image_id=IMAGE_ID,
                        accdb_path=accdb,
                        output_directory=output,
                        allowed_tables=ALLOWED_TABLES,
                        command_runner=fake,
                    )
                self.assertFalse((output / "metadata.json").exists())

    def test_fails_if_accdb_changes_between_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accdb, output = _fixture_paths(root)
            fake = _FakeCommandRunner(input_path=accdb)
            fake.mutate_on = "inventory"
            with (
                _fixture_manifest_pins(),
                self.assertRaisesRegex(EEAMDBToolsError, "changed during extraction"),
            ):
                extract_eea_accdb(
                    image_id=IMAGE_ID,
                    accdb_path=accdb,
                    output_directory=output,
                    allowed_tables=ALLOWED_TABLES,
                    command_runner=fake,
                )
            self.assertFalse((output / "metadata.json").exists())


if __name__ == "__main__":
    unittest.main()
