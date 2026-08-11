from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile
import zlib
from pathlib import Path

from semiconductor_atlas.adapters import moenv_ems
from semiconductor_atlas.adapters.moenv_ems import (
    MOENV_ATTRIBUTION,
    MOENV_FIELDS,
    MOENV_FILTER_VERSION,
    MOENV_HASH_MEMBER,
    MOENV_INDUSTRY_LABELS,
    MOENV_LICENSE,
    canonical_candidate_jsonl_bytes,
    is_currently_regulated,
    scan_moenv_archive,
    valid_taiwan_wgs84_point,
)


JSON_MEMBER = "環境保護許可管理系統(暨解除列管)對象基本資料.json"


def _row(**overrides: str | None) -> dict[str, str | None]:
    row: dict[str, str | None] = {
        "emsno": "A1234567",
        "facilityname": "測試半導體股份有限公司",
        "uniformno": "00123456",
        "county": "新竹市",
        "township": "東區",
        "facilityaddress": "新竹市東區測試路1號",
        "industryareaname": "新竹科學園區",
        "industryid": "2611",
        "industryname": MOENV_INDUSTRY_LABELS["2611"],
        "twd97tm2x": "250000.125",
        "twd97tm2y": "2740000.500",
        "wgs84lon": "121.012300",
        "wgs84lat": "24.800400",
        "isair": "1",
        "iswater": "0",
        "iswaste": "0",
        "istoxic": "0",
        "issoil": "0",
        "airreleasedate": "",
        "waterreleasedate": "",
        "wastereleasedate": "",
        "toxicreleasedate": "",
        "soilreleasedate": "",
        "industrygroup": "261",
        "admino": None,
        "facno": None,
    }
    row.update(overrides)
    return row


def _json_bytes(rows: list[dict[str, object]]) -> bytes:
    return json.dumps(
        rows,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash_bytes(raw: bytes, json_name: str = JSON_MEMBER) -> bytes:
    md5 = hashlib.md5(raw, usedforsecurity=False).hexdigest()
    return f"{json_name}：{md5}，演算法：MD5\n".encode("utf-8")


def _write_archive(
    path: Path,
    *,
    raw_json: bytes | None = None,
    rows: list[dict[str, object]] | None = None,
    json_name: str = JSON_MEMBER,
    hash_raw: bytes | None = None,
    compression: int = zipfile.ZIP_STORED,
    extra_members: tuple[tuple[str, bytes], ...] = (),
    json_symlink: bool = False,
) -> tuple[bytes, bytes]:
    raw = raw_json if raw_json is not None else _json_bytes(rows or [_row()])
    publisher_hash = hash_raw if hash_raw is not None else _hash_bytes(raw, json_name)
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        if json_symlink:
            info = zipfile.ZipInfo(json_name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, raw)
        else:
            archive.writestr(json_name, raw)
        archive.writestr(MOENV_HASH_MEMBER, publisher_hash)
        for name, member_raw in extra_members:
            archive.writestr(name, member_raw)
    return raw, publisher_hash


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


class MOENVEMSAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def archive(self, **kwargs: object) -> tuple[Path, bytes, bytes]:
        path = self.root / f"archive-{len(list(self.root.glob('archive-*.zip')))}.zip"
        raw, hash_raw = _write_archive(path, **kwargs)
        return path, raw, hash_raw

    def test_exact_filter_hashes_counts_and_preserves_source_strings(self) -> None:
        rows = [
            _row(
                emsno="p5806269",
                uniformno="00123456",
                admino="00001234",
                facno=None,
                airreleasedate="2024-02-29",
            ),
            _row(
                emsno="B0000002",
                industryid="2612",
                industryname=MOENV_INDUSTRY_LABELS["2612"],
                isair="0",
                wgs84lon="0",
                wgs84lat="0",
            ),
            _row(
                emsno="ignored!",
                industrygroup="999",
                industryid="9999",
                industryname="other",
                isair="Y",
                airreleasedate="NULL",
            ),
        ]
        path, json_raw, hash_raw = self.archive(rows=rows)
        scan = scan_moenv_archive(path)

        self.assertEqual(3, scan.row_count)
        self.assertEqual(2, scan.industry_group_row_count)
        self.assertEqual(2, scan.candidate_row_count)
        self.assertEqual(2, scan.candidate_count)
        self.assertEqual(2, scan.facility_count)
        self.assertEqual(1, scan.current_regulation_count)
        self.assertEqual(1, scan.valid_coordinate_count)
        self.assertEqual(0, scan.conflicting_facility_count)
        self.assertEqual(["B0000002", "p5806269"], [item.emsno for item in scan.facilities])

        lowercase = scan.facilities[1].variants[0].row
        self.assertEqual("p5806269", lowercase["emsno"])
        self.assertEqual("00123456", lowercase["uniformno"])
        self.assertEqual("00001234", lowercase["admino"])
        self.assertIsNone(lowercase["facno"])
        self.assertEqual(
            ("121.012300", "24.800400"), valid_taiwan_wgs84_point(lowercase)
        )
        self.assertTrue(is_currently_regulated(lowercase))
        self.assertFalse(hasattr(scan, "operating_count"))

        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), scan.archive_sha256)
        self.assertEqual(path.stat().st_size, scan.archive_bytes)
        self.assertEqual(JSON_MEMBER, scan.json_member)
        self.assertEqual(hashlib.sha256(json_raw).hexdigest(), scan.json_sha256)
        self.assertEqual(
            hashlib.md5(json_raw, usedforsecurity=False).hexdigest(), scan.json_md5
        )
        self.assertEqual(scan.json_md5, scan.publisher_md5)
        self.assertEqual(len(json_raw), scan.json_bytes)
        self.assertEqual(zlib.crc32(json_raw) & 0xFFFFFFFF, scan.json_crc32)
        self.assertEqual(MOENV_HASH_MEMBER, scan.hash_member)
        self.assertEqual(hashlib.sha256(hash_raw).hexdigest(), scan.hash_member_sha256)
        self.assertEqual(len(hash_raw), scan.hash_member_bytes)
        self.assertEqual(zlib.crc32(hash_raw) & 0xFFFFFFFF, scan.hash_member_crc32)
        self.assertEqual("moenv-ems-s-01-industry-261-v1", MOENV_FILTER_VERSION)

    def test_duplicate_payloads_collapse_conflicts_survive_and_order_is_invariant(self) -> None:
        first = _row(emsno="A0000001", facilityname="Alpha")
        duplicate_reordered = dict(reversed(tuple(first.items())))
        conflict = _row(emsno="A0000001", facilityname="Alpha variant")
        second = _row(
            emsno="B0000002",
            facilityname="Beta",
            industryid="2613",
            industryname=MOENV_INDUSTRY_LABELS["2613"],
        )
        rows = [second, conflict, duplicate_reordered, first]
        first_path, _, _ = self.archive(rows=rows)
        second_path, _, _ = self.archive(rows=list(reversed(rows)))

        first_scan = scan_moenv_archive(first_path)
        second_scan = scan_moenv_archive(second_path)
        self.assertEqual(4, first_scan.candidate_row_count)
        self.assertEqual(3, first_scan.candidate_count)
        self.assertEqual(2, first_scan.facility_count)
        self.assertEqual(1, first_scan.conflicting_facility_count)
        self.assertEqual(2, len(first_scan.facilities[0].variants))

        first_jsonl = canonical_candidate_jsonl_bytes(first_scan)
        second_jsonl = canonical_candidate_jsonl_bytes(second_scan)
        self.assertEqual(first_jsonl, second_jsonl)
        payloads = [json.loads(line) for line in first_jsonl.splitlines()]
        self.assertEqual(
            ["A0000001", "A0000001", "B0000002"],
            [payload["emsno"] for payload in payloads],
        )
        alpha_lines = first_jsonl.splitlines()[:2]
        self.assertEqual(sorted(alpha_lines), alpha_lines)
        self.assertTrue(first_jsonl.endswith(b"\n"))

    def test_filter_is_exact_and_candidate_label_mapping_is_exact(self) -> None:
        ignored = [
            _row(emsno="A0000002", industrygroup="261 "),
            _row(emsno="A0000003", industryid="26110", industryname="nearby"),
            _row(emsno="A0000004", industrygroup="0261"),
        ]
        path, _, _ = self.archive(rows=ignored)
        scan = scan_moenv_archive(path)
        self.assertEqual(0, scan.candidate_count)
        self.assertEqual(b"", canonical_candidate_jsonl_bytes(scan))

        bad_label = _row(industryname="積體電路製造業 ")
        bad_path, _, _ = self.archive(rows=[bad_label])
        with self.assertRaisesRegex(ValueError, "official label"):
            scan_moenv_archive(bad_path)

    def test_schema_and_string_or_null_types_are_global(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []
        missing = _row(industrygroup="999", industryid="9999")
        del missing["facno"]
        cases.append(("missing", missing))
        extra: dict[str, object] = _row(industrygroup="999", industryid="9999")
        extra["surprise"] = "x"
        cases.append(("unexpected", extra))
        numeric: dict[str, object] = _row(industrygroup="999", industryid="9999")
        numeric["uniformno"] = 1234
        cases.append(("must be a string", numeric))
        null_core: dict[str, object] = _row(
            industrygroup="999", industryid="9999"
        )
        null_core["waterreleasedate"] = None
        cases.append(("must be a string", null_core))

        for message, row in cases:
            with self.subTest(message=message):
                path, _, _ = self.archive(rows=[row])
                with self.assertRaisesRegex(ValueError, message):
                    scan_moenv_archive(path)

    def test_admino_and_facno_preserve_null_distinct_from_empty_string(self) -> None:
        rows = [
            _row(emsno="A0000001", admino=None, facno=""),
            _row(emsno="A0000002", admino="", facno=None),
        ]
        path, _, _ = self.archive(rows=rows)
        scan = scan_moenv_archive(path)
        first = scan.facilities[0].variants[0].row
        second = scan.facilities[1].variants[0].row
        self.assertIsNone(first["admino"])
        self.assertEqual("", first["facno"])
        self.assertEqual("", second["admino"])
        self.assertIsNone(second["facno"])

    def test_candidate_emsno_flags_and_dates_are_strict(self) -> None:
        invalid_rows = [
            ("emsno", _row(emsno="A123456")),
            ("emsno", _row(emsno="A123-567")),
            ("emsno", _row(emsno="Å1234567")),
            ("must be '0' or '1'", _row(iswater="")),
            ("must be a string", _row(iswaste=None)),
            ("YYYY-MM-DD", _row(airreleasedate="2024-2-01")),
            ("real date", _row(airreleasedate="2023-02-29")),
            ("YYYY-MM-DD", _row(airreleasedate="NULL")),
        ]
        for message, row in invalid_rows:
            with self.subTest(message=message, value=row):
                path, _, _ = self.archive(rows=[row])
                with self.assertRaisesRegex(ValueError, message):
                    scan_moenv_archive(path)

    def test_malformed_and_out_of_range_coordinates_are_preserved_but_not_points(self) -> None:
        cases = [
            ("0", "0"),
            ("not-a-number", "24.8"),
            ("121.0", "91"),
            ("117.999", "24.8"),
            (" 121.0", "24.8"),
            ("", "24.8"),
        ]
        for longitude, latitude in cases:
            with self.subTest(longitude=longitude, latitude=latitude):
                row = _row(wgs84lon=longitude, wgs84lat=latitude)
                path, _, _ = self.archive(rows=[row])
                scan = scan_moenv_archive(path)
                retained = scan.facilities[0].variants[0].row
                self.assertEqual(longitude, retained["wgs84lon"])
                self.assertEqual(latitude, retained["wgs84lat"])
                self.assertIsNone(valid_taiwan_wgs84_point(retained))
                self.assertEqual(0, scan.valid_coordinate_count)
        self.assertIsNone(
            valid_taiwan_wgs84_point({"wgs84lon": None, "wgs84lat": "24.8"})
        )

    def test_duplicate_keys_nonfinite_values_and_bad_top_level_json_fail(self) -> None:
        encoded = json.dumps(_row(), ensure_ascii=False, separators=(",", ":"))
        duplicate = ("[" + encoded[:-1] + ',"emsno":"B1234567"}]').encode("utf-8")
        nan = (
            "["
            + encoded.replace('"wgs84lon":"121.012300"', '"wgs84lon":NaN')
            + "]"
        ).encode("utf-8")
        cases = [
            ("duplicate key", duplicate),
            ("non-finite", nan),
            (
                "non-finite",
                (
                    "["
                    + encoded.replace(
                        '"wgs84lon":"121.012300"', '"wgs84lon":Infinity'
                    )
                    + "]"
                ).encode("utf-8"),
            ),
            (
                "non-finite",
                (
                    "["
                    + encoded.replace(
                        '"wgs84lon":"121.012300"', '"wgs84lon":-Infinity'
                    )
                    + "]"
                ).encode("utf-8"),
            ),
            ("valid UTF-8", b'[{"emsno":"\xff"}]'),
            ("HTML", b"<html>upstream failure</html>"),
            ("top-level array", encoded.encode("utf-8")),
            ("truncated", _json_bytes([_row()])[:-1]),
            ("data after", _json_bytes([_row()]) + b"{}"),
            ("trailing comma", b"[" + encoded.encode("utf-8") + b",]"),
        ]
        for message, raw in cases:
            with self.subTest(message=message):
                path, _, _ = self.archive(raw_json=raw)
                with self.assertRaisesRegex(ValueError, message):
                    scan_moenv_archive(path)

    def test_publisher_md5_and_declaration_are_verified(self) -> None:
        raw = _json_bytes([_row()])
        bad_md5 = f"{JSON_MEMBER}：{'0' * 32}，演算法：MD5\n".encode()
        wrong_name = _hash_bytes(raw, "other.json")
        cases = [
            ("publisher MD5", bad_md5),
            ("different JSON member", wrong_name),
            ("expected MD5 declaration", b"not a checksum\n"),
            ("valid UTF-8", b"\xff"),
        ]
        for message, hash_raw in cases:
            with self.subTest(message=message):
                path, _, _ = self.archive(raw_json=raw, hash_raw=hash_raw)
                with self.assertRaisesRegex(ValueError, message):
                    scan_moenv_archive(path)

    def test_archive_members_are_exact_safe_regular_supported_and_unencrypted(self) -> None:
        unsafe_path, _, _ = self.archive(json_name="../records.json")
        with self.assertRaisesRegex(ValueError, "unsafe member"):
            scan_moenv_archive(unsafe_path)

        extra_path, _, _ = self.archive(extra_members=(("extra.txt", b"x"),))
        with self.assertRaisesRegex(ValueError, "exactly one"):
            scan_moenv_archive(extra_path)

        uppercase_path, _, _ = self.archive(json_name="records.JSON")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            scan_moenv_archive(uppercase_path)

        symlink_path, _, _ = self.archive(json_symlink=True)
        with self.assertRaisesRegex(ValueError, "regular file"):
            scan_moenv_archive(symlink_path)

        unsupported_path, _, _ = self.archive(compression=zipfile.ZIP_BZIP2)
        with self.assertRaisesRegex(ValueError, "unsupported compression"):
            scan_moenv_archive(unsupported_path)

        encrypted_path, _, _ = self.archive()
        _mark_encrypted(encrypted_path)
        with self.assertRaisesRegex(ValueError, "must not be encrypted"):
            scan_moenv_archive(encrypted_path)

    def test_duplicate_missing_and_multiple_json_members_fail(self) -> None:
        duplicate_path = self.root / "duplicate.zip"
        raw = _json_bytes([_row()])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate_path, "w") as archive:
                archive.writestr(JSON_MEMBER, raw)
                archive.writestr(JSON_MEMBER, raw)
                archive.writestr(MOENV_HASH_MEMBER, _hash_bytes(raw))
        with self.assertRaisesRegex(ValueError, "duplicate member names"):
            scan_moenv_archive(duplicate_path)

        missing_path = self.root / "missing.zip"
        with zipfile.ZipFile(missing_path, "w") as archive:
            archive.writestr(JSON_MEMBER, raw)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            scan_moenv_archive(missing_path)

        multiple_path, _, _ = self.archive(extra_members=(("second.json", raw),))
        with self.assertRaisesRegex(ValueError, "exactly one"):
            scan_moenv_archive(multiple_path)

    def test_oversized_high_ratio_and_oversized_record_limits_fail(self) -> None:
        stored_path, raw, _ = self.archive()
        with mock.patch.object(moenv_ems, "_MAX_JSON_BYTES", len(raw) - 1):
            with self.assertRaisesRegex(ValueError, "size limit"):
                scan_moenv_archive(stored_path)

        with mock.patch.object(
            moenv_ems, "_MAX_ARCHIVE_BYTES", stored_path.stat().st_size - 1
        ):
            with self.assertRaisesRegex(ValueError, "archive exceeds"):
                scan_moenv_archive(stored_path)

        deflated_path, _, _ = self.archive(compression=zipfile.ZIP_DEFLATED)
        with mock.patch.object(moenv_ems, "_MAX_COMPRESSION_RATIO", 1):
            with self.assertRaisesRegex(ValueError, "compression-ratio"):
                scan_moenv_archive(deflated_path)

        with mock.patch.object(moenv_ems, "_MAX_RECORD_CHARS", 32):
            with self.assertRaisesRegex(ValueError, "per-record size"):
                scan_moenv_archive(stored_path)

    def test_large_json_is_read_only_in_bounded_chunks(self) -> None:
        large = _row(
            industrygroup="999",
            industryid="9999",
            industryname="other",
            facilityname="x" * (moenv_ems._READ_CHUNK + 257),
        )
        path, _, _ = self.archive(rows=[large, _row()])
        original_read = zipfile.ZipExtFile.read
        requested_sizes: list[int] = []

        def bounded_read(stream: zipfile.ZipExtFile, size: int = -1) -> bytes:
            requested_sizes.append(size)
            return original_read(stream, size)

        with mock.patch.object(zipfile.ZipExtFile, "read", new=bounded_read):
            scan = scan_moenv_archive(path)
        self.assertEqual(2, scan.row_count)
        self.assertGreaterEqual(len(requested_sizes), 3)
        self.assertTrue(
            all(0 < size <= moenv_ems._READ_CHUNK for size in requested_sizes)
        )

    def test_source_file_identity_change_is_rejected(self) -> None:
        path, _, _ = self.archive()
        identities = iter(((1, 1), (1, 1), (1, 1), (1, 1), (2, 2), (1, 1)))
        with mock.patch.object(
            moenv_ems, "_file_identity", side_effect=lambda _metadata: next(identities)
        ):
            with self.assertRaisesRegex(ValueError, "path changed"):
                scan_moenv_archive(path)

    def test_truncated_zip_non_zip_symlink_path_and_crc_corruption_fail(self) -> None:
        truncated_path, _, _ = self.archive()
        truncated_path.write_bytes(truncated_path.read_bytes()[:-12])
        with self.assertRaisesRegex(ValueError, "valid complete ZIP"):
            scan_moenv_archive(truncated_path)

        non_zip = self.root / "not.zip"
        non_zip.write_bytes(b"not a zip")
        with self.assertRaisesRegex(ValueError, "valid complete ZIP"):
            scan_moenv_archive(non_zip)

        target, _, _ = self.archive()
        symlink = self.root / "archive-link.zip"
        symlink.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "regular file"):
            scan_moenv_archive(symlink)

        corrupt, _, _ = self.archive()
        raw_archive = bytearray(corrupt.read_bytes())
        json_offset = raw_archive.find(b"[{\"")
        self.assertGreater(json_offset, 0)
        raw_archive[json_offset + 2] ^= 0x01
        corrupt.write_bytes(raw_archive)
        with self.assertRaisesRegex(ValueError, "valid complete ZIP"):
            scan_moenv_archive(corrupt)

    def test_field_contract_is_exact_and_case_sensitive(self) -> None:
        self.assertEqual(26, len(MOENV_FIELDS))
        self.assertEqual("emsno", MOENV_FIELDS[0])
        self.assertEqual("facno", MOENV_FIELDS[-1])
        self.assertEqual(
            {
                "2611": "積體電路製造業",
                "2612": "分離式元件製造業",
                "2613": "半導體封裝及測試業",
            },
            MOENV_INDUSTRY_LABELS,
        )
        self.assertEqual("依政府資料開放平臺使用規範", MOENV_LICENSE)
        for fragment in (
            "環境部資源循環署",
            "2026",
            "環境保護許可管理系統(暨解除列管)對象基本資料",
            "EMS_S_01",
            "Taiwan Open Government Data License, Version 1.0",
            "https://data.gov.tw/license",
        ):
            self.assertIn(fragment, MOENV_ATTRIBUTION)


if __name__ == "__main__":
    unittest.main()
