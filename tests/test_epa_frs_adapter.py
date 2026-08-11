from __future__ import annotations

import csv
import contextlib
import hashlib
import importlib.util
import io
import json
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
import zlib
from pathlib import Path

from semiconductor_atlas.adapters.epa_frs import (
    FRS_ARCHIVE_MEMBER,
    FRS_ATTRIBUTION,
    FRS_COVERAGE,
    FRS_DATA_AS_OF_BASIS,
    FRS_DATA_AS_OF_SOURCE_URL,
    FRS_DOCUMENTATION_MEMBER,
    FRS_FILTER_VERSION,
    FRS_HEADERS,
    FRS_LICENSE,
    FRS_LICENSE_URL,
    FRS_NAICS_CODES,
    FRS_OFFICIAL_ARCHIVE_URL,
    FRS_RAW_RECORD_TYPE,
    FRS_SCOPE,
    FRS_SIC_CODES,
    candidate_jsonl_bytes,
    parse_candidate_jsonl,
    scan_frs_archive,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "epa_frs"
CSV_FIXTURE = FIXTURES / FRS_ARCHIVE_MEMBER
DOCUMENTATION_FIXTURE = FIXTURES / FRS_DOCUMENTATION_MEMBER


def _load_fetch_module():
    path = ROOT / "scripts" / "fetch_epa_frs.py"
    specification = importlib.util.spec_from_file_location(
        "_semiconductor_atlas_fetch_epa_frs_test", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


FETCH = _load_fetch_module()


def _csv_rows(raw: bytes | None = None) -> tuple[list[str], list[list[str]]]:
    text = (raw if raw is not None else CSV_FIXTURE.read_bytes()).decode("utf-8")
    rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    return rows[0], rows[1:]


def _csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_ALL)
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _write_archive(
    path: Path,
    *,
    csv_raw: bytes | None = None,
    documentation_name: str = FRS_DOCUMENTATION_MEMBER,
    extra_members: tuple[tuple[str, bytes], ...] = (),
    documentation_symlink: bool = False,
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(FRS_ARCHIVE_MEMBER, csv_raw or CSV_FIXTURE.read_bytes())
        if documentation_symlink:
            info = zipfile.ZipInfo(documentation_name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"documentation-target")
        else:
            archive.writestr(documentation_name, DOCUMENTATION_FIXTURE.read_bytes())
        for name, raw in extra_members:
            archive.writestr(name, raw)


def _mark_encrypted(path: Path) -> None:
    raw = bytearray(path.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        cursor = 0
        while True:
            location = raw.find(signature, cursor)
            if location < 0:
                break
            offset = location + flag_offset
            flags = int.from_bytes(raw[offset : offset + 2], "little") | 0x1
            raw[offset : offset + 2] = flags.to_bytes(2, "little")
            cursor = location + 4
    path.write_bytes(raw)


class EPAFRSAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def archive(self, **kwargs) -> Path:
        path = self.root / f"archive-{len(list(self.root.glob('archive-*.zip')))}.zip"
        _write_archive(path, **kwargs)
        return path

    def test_exact_filter_preserves_all_cells_and_raw_nad83_coordinates(self) -> None:
        archive = self.archive()
        scan = scan_frs_archive(archive)

        self.assertEqual(4, scan.row_count)
        self.assertEqual(2, scan.candidate_count)
        self.assertEqual(
            ["110000000001", "110000000002"],
            [candidate.registry_id for candidate in scan.candidates],
        )
        direct, legacy = scan.candidates
        self.assertEqual(("123456", "334413"), direct.naics_codes)
        self.assertEqual(("334413",), direct.qualifying_naics_codes)
        self.assertEqual((), direct.qualifying_sic_codes)
        self.assertEqual(("3674",), legacy.sic_codes)
        self.assertEqual(("3674",), legacy.qualifying_sic_codes)
        self.assertEqual(39, len(direct.row))
        self.assertEqual(FRS_HEADERS, tuple(direct.row))
        self.assertTrue(all(isinstance(value, str) for value in direct.row.values()))
        self.assertEqual("33.4", direct.row["LATITUDE83"])
        self.assertEqual("-112.0", direct.row["LONGITUDE83"])
        self.assertFalse(hasattr(direct, "geometry"))

        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), scan.archive_sha256)
        self.assertEqual(archive.stat().st_size, scan.archive_bytes)
        csv_raw = CSV_FIXTURE.read_bytes()
        self.assertEqual(hashlib.sha256(csv_raw).hexdigest(), scan.csv_sha256)
        self.assertEqual(len(csv_raw), scan.csv_bytes)
        self.assertEqual(zlib.crc32(csv_raw) & 0xFFFFFFFF, scan.csv_crc32)

    def test_broad_adjacent_and_substring_codes_are_not_candidates(self) -> None:
        scan = scan_frs_archive(self.archive())
        names = {candidate.row["PRIMARY_NAME"] for candidate in scan.candidates}
        self.assertNotIn("Broad Printed Circuit Assembly", names)
        self.assertNotIn("Substring Is Not Exact", names)
        self.assertEqual(("334413",), FRS_NAICS_CODES)
        self.assertEqual(("3674",), FRS_SIC_CODES)
        self.assertEqual("epa-frs-semiconductor-direct-v1", FRS_FILTER_VERSION)

    def test_candidate_jsonl_is_sorted_deterministic_and_replayable(self) -> None:
        first_archive = self.archive()
        second_archive = self.archive()
        first = candidate_jsonl_bytes(scan_frs_archive(first_archive))
        second = candidate_jsonl_bytes(scan_frs_archive(second_archive))
        self.assertEqual(first, second)

        path = self.root / "candidates.jsonl"
        path.write_bytes(first)
        payloads = [json.loads(line) for line in first.decode("utf-8").splitlines()]
        self.assertEqual(
            ["archive_member", "row_number", "row"], list(payloads[0])
        )
        self.assertEqual(FRS_ARCHIVE_MEMBER, payloads[0]["archive_member"])
        self.assertEqual(FRS_HEADERS, tuple(payloads[0]["row"]))
        self.assertEqual(
            ["110000000001", "110000000002"],
            [candidate.registry_id for candidate in parse_candidate_jsonl(path)],
        )
        self.assertEqual([4, 3], [payload["row_number"] for payload in payloads])

    def test_candidate_jsonl_rejects_unsorted_and_nonqualifying_rows(self) -> None:
        raw = candidate_jsonl_bytes(scan_frs_archive(self.archive()))
        lines = raw.decode("utf-8").splitlines()
        unsorted = self.root / "unsorted.jsonl"
        unsorted.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sorted by REGISTRY_ID"):
            parse_candidate_jsonl(unsorted)

        payload = json.loads(lines[0])
        payload["row"]["NAICS_CODES"] = "334418"
        payload["row"]["SIC_CODES"] = ""
        nonqualifying = self.root / "nonqualifying.jsonl"
        nonqualifying.write_text(
            json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "does not match filter"):
            parse_candidate_jsonl(nonqualifying)

    def test_rejects_wrong_schema_and_row_width(self) -> None:
        header, rows = _csv_rows()
        wrong_header = list(header)
        wrong_header[0] = "WRONG_FIELD"
        with self.assertRaisesRegex(ValueError, "expected 39-field schema"):
            scan_frs_archive(
                self.archive(csv_raw=_csv_bytes(wrong_header, rows))
            )

        short_rows = [list(row) for row in rows]
        short_rows[2] = short_rows[2][:-1]
        with self.assertRaisesRegex(ValueError, "has 38 fields"):
            scan_frs_archive(
                self.archive(csv_raw=_csv_bytes(header, short_rows))
            )

    def test_rejects_duplicate_or_invalid_candidate_registry_ids(self) -> None:
        header, rows = _csv_rows()
        duplicate = [list(row) for row in rows]
        duplicate.append(list(rows[2]))
        with self.assertRaisesRegex(ValueError, "duplicate REGISTRY_ID 110000000001"):
            scan_frs_archive(
                self.archive(csv_raw=_csv_bytes(header, duplicate))
            )

        invalid = [list(row) for row in rows]
        invalid[2][header.index("REGISTRY_ID")] = "11000000001"
        with self.assertRaisesRegex(ValueError, "invalid 12-digit REGISTRY_ID"):
            scan_frs_archive(
                self.archive(csv_raw=_csv_bytes(header, invalid))
            )

    def test_rejects_invalid_utf8_and_incomplete_csv(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            scan_frs_archive(
                self.archive(csv_raw=CSV_FIXTURE.read_bytes() + b"\xff")
            )

        incomplete = CSV_FIXTURE.read_bytes() + b'"unterminated'
        with self.assertRaisesRegex(ValueError, "malformed"):
            scan_frs_archive(self.archive(csv_raw=incomplete))

    def test_rejects_unsafe_unexpected_symlink_and_encrypted_members(self) -> None:
        with self.subTest("unexpected"):
            with self.assertRaisesRegex(ValueError, "unexpected"):
                scan_frs_archive(
                    self.archive(extra_members=(("unexpected.txt", b"no"),))
                )
        with self.subTest("duplicate member"):
            with self.assertWarns(UserWarning):
                duplicate = self.archive(
                    extra_members=((FRS_ARCHIVE_MEMBER, CSV_FIXTURE.read_bytes()),)
                )
            with self.assertRaisesRegex(ValueError, "duplicate member names"):
                scan_frs_archive(duplicate)
        with self.subTest("unsafe"):
            with self.assertRaisesRegex(ValueError, "unsafe member"):
                scan_frs_archive(
                    self.archive(documentation_name="../documentation.pdf")
                )
        with self.subTest("symlink"):
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                scan_frs_archive(self.archive(documentation_symlink=True))
        with self.subTest("encrypted"):
            encrypted = self.archive()
            _mark_encrypted(encrypted)
            with self.assertRaisesRegex(ValueError, "must not be encrypted"):
                scan_frs_archive(encrypted)

    def test_offline_acquisition_is_atomic_complete_and_never_overwrites(self) -> None:
        archive = self.archive()
        output = self.root / "snapshot"
        transport = FETCH.TransportMetadata(
            etag='"example-etag"',
            last_modified="Wed, 08 Jul 2026 19:35:13 GMT",
        )
        manifest = FETCH.create_snapshot(
            archive,
            output,
            data_as_of="2026-07-08",
            retrieved_at="2026-07-19T12:00:00-07:00",
            transport=transport,
        )

        self.assertTrue((output / "manifest.json").is_file())
        self.assertTrue((output / FETCH.CANDIDATE_FILENAME).is_file())
        self.assertFalse((output / "national_single.zip").exists())
        persisted = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest, persisted)
        self.assertEqual("semiconductor-atlas-source-inputs-v1", manifest["format"])
        self.assertEqual("2026-07-19T19:00:00Z", manifest["retrieved_at"])
        self.assertEqual(
            "operator_supplied_for_archived_bytes",
            manifest["retrieval_timestamp_basis"],
        )
        scope = manifest["source_scopes"][FRS_SCOPE]
        self.assertTrue(scope["complete"])
        self.assertEqual(FRS_COVERAGE, scope["coverage"])
        self.assertEqual(FRS_FILTER_VERSION, scope["filter_version"])
        self.assertEqual(["334413"], scope["naics_codes"])
        self.assertEqual(["3674"], scope["sic_codes"])
        self.assertEqual("2026-07-08", scope["data_as_of"])
        self.assertEqual(FRS_DATA_AS_OF_BASIS, scope["data_as_of_basis"])
        self.assertEqual(FRS_DATA_AS_OF_SOURCE_URL, scope["data_as_of_source_url"])
        self.assertEqual(
            "operator_supplied_for_archived_bytes",
            scope["retrieval_timestamp_basis"],
        )
        self.assertTrue(scope["raw_retention"]["retained_in_snapshot"])
        self.assertIsNone(scope["raw_retention"]["reason"])
        self.assertEqual(
            scope["upstream_archive"]["sha256"],
            scope["raw_retention"]["upstream_sha256"],
        )
        self.assertEqual(
            scope["upstream_archive"]["bytes"],
            scope["raw_retention"]["bytes"],
        )
        self.assertEqual(2, scope["candidate_count"])
        self.assertEqual(4, scope["upstream_total_rows"])
        self.assertEqual(FRS_OFFICIAL_ARCHIVE_URL, scope["upstream_archive"]["url"])
        self.assertEqual('"example-etag"', scope["upstream_archive"]["etag"])
        self.assertEqual(
            "Wed, 08 Jul 2026 19:35:13 GMT",
            scope["upstream_archive"]["last_modified"],
        )
        self.assertEqual(FRS_ARCHIVE_MEMBER, scope["upstream_csv"]["member"])
        self.assertEqual(4, scope["upstream_csv"]["row_count"])
        self.assertEqual(
            zlib.crc32(CSV_FIXTURE.read_bytes()) & 0xFFFFFFFF,
            scope["upstream_csv"]["crc32"],
        )
        self.assertEqual(
            {
                "reviewed_at": "2026-07-19",
                "decision": "pass_for_exact_public_archive",
                "access_level": "public",
                "license_url": FRS_LICENSE_URL,
                "no_warranty": True,
                "scope": "exact_public_national_single_archive",
            },
            scope["rights"],
        )
        item = manifest["inputs"][0]
        candidate_raw = (output / FETCH.CANDIDATE_FILENAME).read_bytes()
        self.assertEqual("epa_frs_semiconductor_candidates", item["record_type"])
        self.assertEqual("deterministic_filtered_derivative", item["artifact_kind"])
        self.assertEqual(hashlib.sha256(candidate_raw).hexdigest(), item["sha256"])
        self.assertEqual(len(candidate_raw), item["bytes"])
        self.assertEqual(FRS_LICENSE, item["license"])
        self.assertEqual(FRS_ATTRIBUTION, item["attribution"])
        raw_item = manifest["inputs"][1]
        self.assertEqual(FRS_RAW_RECORD_TYPE, raw_item["record_type"])
        self.assertEqual(
            scope["raw_retention"]["blob_locator"], raw_item["path"]
        )
        retained_archive = output / raw_item["path"]
        self.assertEqual(archive.read_bytes(), retained_archive.read_bytes())
        self.assertNotEqual(archive.stat().st_ino, retained_archive.stat().st_ino)
        self.assertEqual(
            scope["upstream_archive"]["sha256"],
            hashlib.sha256(retained_archive.read_bytes()).hexdigest(),
        )

        with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
            FETCH.create_snapshot(
                archive,
                output,
                data_as_of="2026-07-08",
                retrieved_at="2026-07-19T19:00:00Z",
            )
        self.assertEqual(manifest, json.loads((output / "manifest.json").read_text()))

        retained_before = retained_archive.read_bytes()
        archive.write_bytes(b"original archive changed after snapshot installation")
        self.assertEqual(retained_before, retained_archive.read_bytes())

    def test_offline_cli_preserves_transport_and_normalizes_timestamp(self) -> None:
        archive = self.archive()
        output = self.root / "cli-snapshot"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = FETCH.main(
                [
                    "--archive",
                    str(archive),
                    "--output-dir",
                    str(output),
                    "--data-as-of",
                    "2026-07-08",
                    "--retrieved-at",
                    "2026-07-19T12:00:00-07:00",
                    "--upstream-etag",
                    '"archived-etag"',
                    "--upstream-last-modified",
                    "Wed, 08 Jul 2026 19:35:13 GMT",
                ]
            )

        self.assertEqual(0, result, stdout.getvalue())
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("2026-07-19T19:00:00Z", manifest["retrieved_at"])
        self.assertEqual(
            "operator_supplied_for_archived_bytes",
            manifest["retrieval_timestamp_basis"],
        )
        upstream = manifest["source_scopes"][FRS_SCOPE]["upstream_archive"]
        self.assertEqual('"archived-etag"', upstream["etag"])
        self.assertEqual(
            "Wed, 08 Jul 2026 19:35:13 GMT", upstream["last_modified"]
        )

    def test_offline_cli_default_timestamp_records_local_transform_basis(self) -> None:
        output = self.root / "default-time-snapshot"
        with (
            mock.patch.object(FETCH, "_now", return_value="2026-07-19T20:01:02Z"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = FETCH.main(
                [
                    "--archive",
                    str(self.archive()),
                    "--output-dir",
                    str(output),
                    "--data-as-of",
                    "2026-07-08",
                ]
            )

        self.assertEqual(0, result)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("2026-07-19T20:01:02Z", manifest["retrieved_at"])
        self.assertEqual(
            "local_archive_transform_start_for_archived_bytes",
            manifest["retrieval_timestamp_basis"],
        )

    def test_network_mode_timestamps_after_download_and_rejects_overrides(self) -> None:
        archive = self.archive()
        output = self.root / "network-snapshot"
        events: list[str] = []

        def fake_download(destination: Path, *, timeout: int):
            self.assertEqual(600, timeout)
            destination.write_bytes(archive.read_bytes())
            events.append("download")
            return FETCH.TransportMetadata(etag='"network-etag"')

        def fake_now() -> str:
            events.append("now")
            return "2026-07-19T20:02:03Z"

        with (
            mock.patch.object(FETCH, "_download_official", side_effect=fake_download),
            mock.patch.object(FETCH, "_now", side_effect=fake_now),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = FETCH.main(
                [
                    "--output-dir",
                    str(output),
                    "--data-as-of",
                    "2026-07-08",
                ]
            )

        self.assertEqual(0, result)
        self.assertEqual(["download", "now"], events)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("2026-07-19T20:02:03Z", manifest["retrieved_at"])
        self.assertEqual(
            "upstream_download_completion",
            manifest["retrieval_timestamp_basis"],
        )

        rejected_output = self.root / "rejected-network-snapshot"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rejected = FETCH.main(
                [
                    "--output-dir",
                    str(rejected_output),
                    "--data-as-of",
                    "2026-07-08",
                    "--retrieved-at",
                    "2026-07-19T20:02:03Z",
                ]
            )
        self.assertEqual(1, rejected)
        self.assertIn("require --archive", stderr.getvalue())
        self.assertFalse(rejected_output.exists())

    def test_failed_acquisition_does_not_install_partial_output(self) -> None:
        invalid = self.root / "invalid.zip"
        invalid.write_bytes(b"not a zip")
        output = self.root / "failed-snapshot"
        with self.assertRaisesRegex(ValueError, "valid complete ZIP"):
            FETCH.create_snapshot(
                invalid,
                output,
                data_as_of="2026-07-08",
                retrieved_at="2026-07-19T19:00:00Z",
            )
        self.assertFalse(output.exists())
        self.assertEqual([], list(self.root.glob(".failed-snapshot.stage-*")))

    def test_rejects_data_date_later_than_retrieval_date(self) -> None:
        output = self.root / "future-date-snapshot"
        with self.assertRaisesRegex(ValueError, "must not be later than retrieved_at"):
            FETCH.create_snapshot(
                self.archive(),
                output,
                data_as_of="2099-01-01",
                retrieved_at="2026-07-19T19:00:00Z",
            )
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
