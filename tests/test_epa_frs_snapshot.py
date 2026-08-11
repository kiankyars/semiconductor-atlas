from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from semiconductor_atlas.adapters import epa_frs as frs_adapter
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
    FRS_RAW_RECORD_TYPE,
    FRS_RECORD_TYPE,
    FRS_SCOPE,
    FRS_SIC_CODES,
    candidate_jsonl_bytes,
    scan_frs_archive,
)
from semiconductor_atlas.snapshot import SOURCE_SNAPSHOT_FORMAT, verify_snapshot


def _archive_raw(*, candidate_name: str = "Fixture candidate") -> bytes:
    row = {field: "" for field in FRS_HEADERS}
    row.update(
        {
            "FRS_FACILITY_DETAIL_REPORT_URL": (
                "https://ofmpub.epa.gov/frs_public2/fii_query_detail."
                "disp_program_facility?p_registry_id=110000000001"
            ),
            "REGISTRY_ID": "110000000001",
            "PRIMARY_NAME": candidate_name,
            "NAICS_CODES": "236220, 334413",
            "LATITUDE83": "33.4",
            "LONGITUDE83": "-112.1",
            "HDATUM_DESC": "NAD83",
        }
    )
    noncandidate = {field: "" for field in FRS_HEADERS}
    noncandidate.update(
        {
            "REGISTRY_ID": "110000000002",
            "PRIMARY_NAME": "Not selected",
            "NAICS_CODES": "334418",
        }
    )
    csv_stream = io.StringIO(newline="")
    writer = csv.writer(csv_stream, lineterminator="\n", quoting=csv.QUOTE_ALL)
    writer.writerow(FRS_HEADERS)
    writer.writerow([row[field] for field in FRS_HEADERS])
    writer.writerow([noncandidate[field] for field in FRS_HEADERS])
    archive_stream = io.BytesIO()
    with zipfile.ZipFile(
        archive_stream, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr(FRS_ARCHIVE_MEMBER, csv_stream.getvalue().encode("utf-8"))
        archive.writestr(FRS_DOCUMENTATION_MEMBER, b"fixture documentation")
    return archive_stream.getvalue()


def _manifest(raw: bytes, scan, locator: str) -> dict[str, object]:
    return {
        "format": SOURCE_SNAPSHOT_FORMAT,
        "retrieved_at": "2026-07-20T01:12:47Z",
        "retrieval_timestamp_basis": "operator_supplied_for_archived_bytes",
        "inputs": [
            {
                "path": "candidates.jsonl",
                "record_type": FRS_RECORD_TYPE,
                "url": FRS_OFFICIAL_ARCHIVE_URL,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "artifact_kind": "deterministic_filtered_derivative",
                "attribution": FRS_ATTRIBUTION,
                "data_as_of": "2026-07-01",
                "license": FRS_LICENSE,
                "license_url": FRS_LICENSE_URL,
            }
        ],
        "source_scopes": {
            FRS_SCOPE: {
                "complete": True,
                "coverage": (
                    "all_national_single_rows_matching_exact_naics_334413_or_sic_3674"
                ),
                "filter_version": FRS_FILTER_VERSION,
                "naics_codes": list(FRS_NAICS_CODES),
                "sic_codes": list(FRS_SIC_CODES),
                "data_as_of": "2026-07-01",
                "data_as_of_basis": FRS_DATA_AS_OF_BASIS,
                "data_as_of_source_url": FRS_DATA_AS_OF_SOURCE_URL,
                "retrieval_timestamp_basis": "operator_supplied_for_archived_bytes",
                "candidate_count": scan.candidate_count,
                "upstream_total_rows": scan.row_count,
                "upstream_archive": {
                    "url": FRS_OFFICIAL_ARCHIVE_URL,
                    "sha256": scan.archive_sha256,
                    "bytes": scan.archive_bytes,
                    "etag": None,
                    "last_modified": "Wed, 08 Jul 2026 19:35:13 GMT",
                },
                "upstream_csv": {
                    "member": FRS_ARCHIVE_MEMBER,
                    "sha256": scan.csv_sha256,
                    "bytes": scan.csv_bytes,
                    "crc32": scan.csv_crc32,
                    "row_count": scan.row_count,
                },
                "documentation_member": FRS_DOCUMENTATION_MEMBER,
                "raw_retention": {
                    "retained_in_snapshot": True,
                    "blob_locator": locator,
                    "reason": None,
                    "upstream_sha256": scan.archive_sha256,
                    "bytes": scan.archive_bytes,
                },
                "rights": {
                    "reviewed_at": "2026-07-19",
                    "decision": "pass_for_exact_public_archive",
                    "access_level": "public",
                    "license_url": "https://edg.epa.gov/epa_data_license.html",
                    "no_warranty": True,
                    "scope": "exact_public_national_single_archive",
                },
            }
        },
    }


def _fixture(root: Path, *, candidate_name: str = "Fixture candidate"):
    archive_raw = _archive_raw(candidate_name=candidate_name)
    archive_sha256 = hashlib.sha256(archive_raw).hexdigest()
    locator = f"raw/sha256/{archive_sha256}.zip"
    archive_path = root / locator
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(archive_raw)
    scan = scan_frs_archive(archive_path)
    candidate_raw = candidate_jsonl_bytes(scan)
    manifest = _manifest(candidate_raw, scan, locator)
    manifest["inputs"].append(
        {
            "path": locator,
            "record_type": FRS_RAW_RECORD_TYPE,
            "url": FRS_OFFICIAL_ARCHIVE_URL,
            "sha256": scan.archive_sha256,
            "bytes": scan.archive_bytes,
            "content_type": "application/zip",
            "artifact_kind": "upstream_official_archive",
            "attribution": FRS_ATTRIBUTION,
            "data_as_of": "2026-07-01",
            "license": FRS_LICENSE,
            "license_url": FRS_LICENSE_URL,
        }
    )
    return candidate_raw, manifest


class EPAFRSSnapshotTests(unittest.TestCase):
    def _write(self, root: Path, manifest: dict[str, object], raw: bytes) -> None:
        (root / "candidates.jsonl").write_bytes(raw)
        (root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_verifies_complete_filtered_derivative_and_upstream_population(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw, manifest = _fixture(root)
            self._write(root, manifest, raw)

            snapshot = verify_snapshot(root)

            self.assertTrue(snapshot.scope_is_complete(FRS_SCOPE))
            self.assertEqual(2, len(snapshot.inputs))
            self.assertEqual(FRS_RECORD_TYPE, snapshot.inputs[0].record_type)
            self.assertEqual(FRS_RAW_RECORD_TYPE, snapshot.inputs[1].record_type)
            self.assertEqual(
                2,
                snapshot.source_scopes[FRS_SCOPE]["upstream_total_rows"],
            )

    def test_rejects_candidate_count_filter_and_rights_drift(self) -> None:
        mutations = (
            ("candidate count", lambda scope: scope.__setitem__("candidate_count", 2)),
            ("NAICS", lambda scope: scope.__setitem__("naics_codes", ["334418"])),
            (
                "rights",
                lambda scope: scope["rights"].__setitem__("decision", "unknown"),
            ),
            (
                "license URL",
                lambda scope: scope["rights"].__setitem__(
                    "license_url", "https://example.com/"
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw, manifest = _fixture(root)
                mutate(manifest["source_scopes"][FRS_SCOPE])
                self._write(root, manifest, raw)
                with self.assertRaises(ValueError):
                    verify_snapshot(root)

    def test_rejects_unscoped_candidate_input_and_nonqualifying_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw, manifest = _fixture(root)
            manifest["source_scopes"] = {}
            self._write(root, manifest, raw)
            with self.assertRaisesRegex(ValueError, "requires.*scope"):
                verify_snapshot(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_raw, manifest = _fixture(root)
            payload = json.loads(original_raw)
            payload["row"]["NAICS_CODES"] = "334418"
            raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
            manifest["inputs"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
            manifest["inputs"][0]["bytes"] = len(raw)
            self._write(root, manifest, raw)
            with self.assertRaisesRegex(ValueError, "does not match filter"):
                verify_snapshot(root)

    def test_rejects_tampered_filtered_bytes_before_scope_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw, manifest = _fixture(root)
            self._write(root, manifest, raw + b"\n")
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                verify_snapshot(root)

    def test_rejects_future_data_date_and_raw_nonretention(self) -> None:
        mutations = (
            (
                "future data date",
                lambda manifest, scope: (
                    scope.__setitem__("data_as_of", "2099-01-01"),
                    manifest["inputs"][0].__setitem__("data_as_of", "2099-01-01"),
                    manifest["inputs"][1].__setitem__("data_as_of", "2099-01-01"),
                ),
                "must not be later",
            ),
            (
                "raw not retained",
                lambda _manifest, scope: scope["raw_retention"].__setitem__(
                    "retained_in_snapshot", False
                ),
                "must be retained",
            ),
        )
        for label, mutate, message in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw, manifest = _fixture(root)
                mutate(manifest, manifest["source_scopes"][FRS_SCOPE])
                self._write(root, manifest, raw)
                with self.assertRaisesRegex(ValueError, message):
                    verify_snapshot(root)

    def test_rejects_missing_symlinked_escaping_duplicate_and_misSized_raw_blobs(
        self,
    ) -> None:
        cases = ("missing", "symlink", "escaping", "duplicate", "wrong size")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw, manifest = _fixture(root)
                raw_input = manifest["inputs"][1]
                retained = root / raw_input["path"]
                if case == "missing":
                    retained.unlink()
                    message = "input is missing"
                elif case == "symlink":
                    actual = root / "actual-archive.zip"
                    retained.replace(actual)
                    retained.symlink_to(actual)
                    message = "must not use symlinks"
                elif case == "escaping":
                    raw_input["path"] = "../escaped-archive.zip"
                    message = "invalid relative path"
                elif case == "duplicate":
                    duplicate = root / "duplicate-archive.zip"
                    duplicate.write_bytes(retained.read_bytes())
                    duplicate_input = dict(raw_input)
                    duplicate_input["path"] = duplicate.name
                    manifest["inputs"].append(duplicate_input)
                    message = "exactly one retained"
                else:
                    raw_input["bytes"] += 1
                    message = "size mismatch"
                self._write(root, manifest, raw)
                with self.assertRaisesRegex(ValueError, message):
                    verify_snapshot(root)

    def test_rejects_filtered_derivative_that_does_not_replay_from_raw(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_raw, manifest = _fixture(root)
            old_locator = manifest["inputs"][1]["path"]
            (root / old_locator).unlink()

            alternate_raw = _archive_raw(candidate_name="Changed in retained raw")
            alternate_sha = hashlib.sha256(alternate_raw).hexdigest()
            alternate_locator = f"raw/sha256/{alternate_sha}.zip"
            alternate_path = root / alternate_locator
            alternate_path.parent.mkdir(parents=True, exist_ok=True)
            alternate_path.write_bytes(alternate_raw)
            alternate_scan = scan_frs_archive(alternate_path)

            scope = manifest["source_scopes"][FRS_SCOPE]
            scope["upstream_archive"]["sha256"] = alternate_scan.archive_sha256
            scope["upstream_archive"]["bytes"] = alternate_scan.archive_bytes
            scope["upstream_csv"].update(
                {
                    "sha256": alternate_scan.csv_sha256,
                    "bytes": alternate_scan.csv_bytes,
                    "crc32": alternate_scan.csv_crc32,
                    "row_count": alternate_scan.row_count,
                }
            )
            scope["raw_retention"].update(
                {
                    "blob_locator": alternate_locator,
                    "upstream_sha256": alternate_scan.archive_sha256,
                    "bytes": alternate_scan.archive_bytes,
                }
            )
            manifest["inputs"][1].update(
                {
                    "path": alternate_locator,
                    "sha256": alternate_scan.archive_sha256,
                    "bytes": alternate_scan.archive_bytes,
                }
            )
            self._write(root, manifest, candidate_raw)

            with self.assertRaisesRegex(ValueError, "does not replay"):
                verify_snapshot(root)

    def test_transient_raw_swap_cannot_pair_one_digest_with_other_semantics(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_a, manifest = _fixture(root, candidate_name="Archive A")
            raw_input = manifest["inputs"][1]
            raw_path = root / raw_input["path"]
            archive_a = raw_path.read_bytes()

            archive_b = _archive_raw(candidate_name="Archive B")
            alternate_path = root / "alternate.zip"
            alternate_path.write_bytes(archive_b)
            scan_b = scan_frs_archive(alternate_path)
            candidate_b = candidate_jsonl_bytes(scan_b)
            scope = manifest["source_scopes"][FRS_SCOPE]
            scope["upstream_csv"].update(
                {
                    "sha256": scan_b.csv_sha256,
                    "bytes": scan_b.csv_bytes,
                    "crc32": scan_b.csv_crc32,
                    "row_count": scan_b.row_count,
                }
            )
            manifest["inputs"][0].update(
                {
                    "sha256": hashlib.sha256(candidate_b).hexdigest(),
                    "bytes": len(candidate_b),
                }
            )
            self._write(root, manifest, candidate_b)

            real_copy = frs_adapter._copy_and_hash_file
            real_scan = frs_adapter._scan_csv

            def copy_then_swap(path, destination):
                result = real_copy(path, destination)
                raw_path.write_bytes(archive_b)
                return result

            def scan_then_restore(archive, info):
                try:
                    return real_scan(archive, info)
                finally:
                    raw_path.write_bytes(archive_a)

            with (
                mock.patch(
                    "semiconductor_atlas.adapters.epa_frs._copy_and_hash_file",
                    side_effect=copy_then_swap,
                ),
                mock.patch(
                    "semiconductor_atlas.adapters.epa_frs._scan_csv",
                    side_effect=scan_then_restore,
                ),
                self.assertRaisesRegex(ValueError, "semantics disagree"),
            ):
                verify_snapshot(root)
            self.assertEqual(archive_a, raw_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
