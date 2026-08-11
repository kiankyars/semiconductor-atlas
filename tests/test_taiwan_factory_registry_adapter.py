from __future__ import annotations

import csv
import hashlib
import io
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from semiconductor_atlas.adapters.taiwan_factory_registry import (
    TAIWAN_FACTORY_FIELDS,
    canonical_candidate_jsonl_bytes,
    normalize_factory_registration_number,
    scan_taiwan_factory_archive,
)


def _row(**overrides: str) -> dict[str, str]:
    row = dict.fromkeys(TAIWAN_FACTORY_FIELDS, "")
    row.update(
        {
            "工廠名稱": "測試半導體股份有限公司一廠",
            "工廠登記編號": "94A00001",
            "工廠設立許可案號": "",
            "工廠地址": "新竹市東區測試路1號",
            "工廠市鎮鄉村里": "新竹市東區",
            "工廠負責人姓名": "不應出現在衍生檔",
            "統一編號": "22099131",
            "工廠組織型態": "股份有限公司",
            "工廠設立核准日期": "",
            "工廠登記核准日期": "1090611000000",
            "工廠登記狀態": "生產中",
            "產業類別": "26電子零組件製造業\n",
            "主要產品": "261半導體\n269其他電子零組件\n",
        }
    )
    row.update(overrides)
    return row


def _csv_bytes(
    rows: list[dict[str, str]],
    *,
    header: tuple[str, ...] = TAIWAN_FACTORY_FIELDS,
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([row.get(field, "") for field in header])
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def _archive(
    path: Path,
    payload: bytes,
    *,
    member: str = "11506.csv",
    compression: int = zipfile.ZIP_DEFLATED,
) -> None:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        archive.writestr(member, payload)


class TaiwanFactoryRegistryAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_scans_exact_product_and_omits_responsible_person(self) -> None:
        path = self.root / "factory.zip"
        selected = _row()
        excluded = _row(
            **{
                "工廠名稱": "印刷電路板廠",
                "工廠登記編號": "99624278",
                "統一編號": "16374010",
                "主要產品": "263印刷電路板\n",
            }
        )
        _archive(path, _csv_bytes([excluded, selected]))

        scan = scan_taiwan_factory_archive(path)
        derivative = canonical_candidate_jsonl_bytes(scan)

        self.assertEqual(2, scan.row_count)
        self.assertEqual(1, scan.candidate_count)
        self.assertEqual(1, scan.business_number_count)
        self.assertEqual((("生產中", 1),), scan.registration_status_counts)
        self.assertEqual("94A00001", scan.records[0].factory_registration_number)
        self.assertNotIn("不應出現在衍生檔".encode(), derivative)
        self.assertNotIn("工廠負責人姓名".encode(), derivative)
        self.assertIn(b'"principal_products":[', derivative)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), scan.archive_sha256)

    def test_output_is_deterministic_and_sorted_by_registration_number(self) -> None:
        rows = [
            _row(**{"工廠登記編號": "99621928", "統一編號": "84900976"}),
            _row(**{"工廠登記編號": "03000239", "統一編號": "22335281"}),
        ]
        first = self.root / "first.zip"
        second = self.root / "second.zip"
        _archive(first, _csv_bytes(rows))
        _archive(second, _csv_bytes(list(reversed(rows))))
        first_bytes = canonical_candidate_jsonl_bytes(scan_taiwan_factory_archive(first))
        second_bytes = canonical_candidate_jsonl_bytes(scan_taiwan_factory_archive(second))
        self.assertEqual(first_bytes, second_bytes)
        self.assertLess(first_bytes.find(b"03000239"), first_bytes.find(b"99621928"))

    def test_product_filter_is_an_exact_token(self) -> None:
        path = self.root / "factory.zip"
        _archive(
            path,
            _csv_bytes(
                [
                    _row(**{"主要產品": "1261半導體材料\n"}),
                    _row(
                        **{
                            "工廠登記編號": "94A00002",
                            "統一編號": "22099132",
                            "主要產品": "261半導體設備\n",
                        }
                    ),
                ]
            ),
        )
        self.assertEqual(0, scan_taiwan_factory_archive(path).candidate_count)

    def test_accepts_bare_upstream_category_and_product_codes(self) -> None:
        path = self.root / "factory.zip"
        _archive(
            path,
            _csv_bytes(
                [
                    _row(
                        **{
                            "產業類別": "26電子零組件製造業\n34\n",
                            "主要產品": "261半導體\n340\n",
                        }
                    )
                ]
            ),
        )
        scan = scan_taiwan_factory_archive(path)
        self.assertEqual(("261半導體", "340"), scan.records[0].row["principal_products"])

    def test_rejects_wrong_header(self) -> None:
        path = self.root / "factory.zip"
        _archive(path, _csv_bytes([_row()], header=TAIWAN_FACTORY_FIELDS[:-1]))
        with self.assertRaisesRegex(ValueError, "exact 13-column schema"):
            scan_taiwan_factory_archive(path)

    def test_rejects_duplicate_candidate_registration_number(self) -> None:
        path = self.root / "factory.zip"
        _archive(path, _csv_bytes([_row(), _row(**{"工廠名稱": "另一列"})]))
        with self.assertRaisesRegex(ValueError, "duplicate registration number"):
            scan_taiwan_factory_archive(path)

    def test_rejects_invalid_candidate_business_number(self) -> None:
        path = self.root / "factory.zip"
        _archive(path, _csv_bytes([_row(**{"統一編號": "123"})]))
        with self.assertRaisesRegex(ValueError, "invalid business number"):
            scan_taiwan_factory_archive(path)

    def test_rejects_invalid_candidate_raw_date(self) -> None:
        path = self.root / "factory.zip"
        _archive(path, _csv_bytes([_row(**{"工廠登記核准日期": "2020-01-01"})]))
        with self.assertRaisesRegex(ValueError, "invalid raw date"):
            scan_taiwan_factory_archive(path)

    def test_rejects_invalid_utf8(self) -> None:
        path = self.root / "factory.zip"
        _archive(path, b"\xff\xfe\x00")
        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            scan_taiwan_factory_archive(path)

    def test_rejects_extra_or_nested_archive_members(self) -> None:
        extra = self.root / "extra.zip"
        with zipfile.ZipFile(extra, "w") as archive:
            archive.writestr("11506.csv", _csv_bytes([_row()]))
            archive.writestr("other.txt", b"x")
        with self.assertRaisesRegex(ValueError, "exactly one root CSV"):
            scan_taiwan_factory_archive(extra)

        nested = self.root / "nested.zip"
        _archive(nested, _csv_bytes([_row()]), member="nested/11506.csv")
        with self.assertRaisesRegex(ValueError, "unsafe member"):
            scan_taiwan_factory_archive(nested)

    def test_rejects_non_regular_archive_member(self) -> None:
        path = self.root / "symlink-member.zip"
        info = zipfile.ZipInfo("11506.csv")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(info, b"target")
        with self.assertRaisesRegex(ValueError, "unsafe member"):
            scan_taiwan_factory_archive(path)

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlink support")
    def test_rejects_symlink_archive_path(self) -> None:
        target = self.root / "target.zip"
        _archive(target, _csv_bytes([_row()]))
        link = self.root / "link.zip"
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "regular file"):
            scan_taiwan_factory_archive(link)

    def test_rejects_directory_as_archive(self) -> None:
        with self.assertRaisesRegex(ValueError, "regular file"):
            scan_taiwan_factory_archive(self.root)

    def test_normalizes_only_documented_legacy_factory_numbers(self) -> None:
        cases = {
            "94A00001": "94A00001",
            "94a00001": "94A00001",
            "9909150301": "99091503",
            "99-612345-01": "99612345",
            "90-T00308-08": "90T00308",
            "99-61234501": "99612345",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, normalize_factory_registration_number(raw))
        for raw in ("", " 94A00001", "94-A00-001", "../../x", "123456789"):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_factory_registration_number(raw))


if __name__ == "__main__":
    unittest.main()
