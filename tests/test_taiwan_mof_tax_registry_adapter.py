from __future__ import annotations

import csv
import io
import os
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from semiconductor_atlas.adapters.taiwan_mof_tax_registry import (
    TAIWAN_MOF_FIELDS,
    canonical_matched_jsonl_bytes,
    canonical_ubn_allowlist_bytes,
    parse_ubn_allowlist_bytes,
    scan_taiwan_mof_archive,
)

from tests._taiwan_mof_fixtures import mof_archive_raw


ALLOWLIST = ["00123456", "00888888", "00999999"]


def _zip_csv(
    csv_raw: bytes,
    *,
    member: str = "BGMOPEN1.csv",
    mode: int = 0o100644,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        info = zipfile.ZipInfo(member, date_time=(2026, 7, 20, 0, 0, 0))
        info.compress_type = compression
        info.external_attr = mode << 16
        archive.writestr(info, csv_raw)
    return stream.getvalue()


def _csv_raw(
    rows: list[list[str]], *, header: tuple[str, ...] = TAIWAN_MOF_FIELDS
) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + text.getvalue().encode()


class TaiwanMOFTaxRegistryAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, raw: bytes, name: str = "source.zip") -> Path:
        path = self.root / name
        path.write_bytes(raw)
        return path

    def test_scans_exact_allowlist_and_emits_privacy_conscious_rows(self) -> None:
        scan = scan_taiwan_mof_archive(self._write(mof_archive_raw()), ALLOWLIST)
        self.assertEqual("20-JUL-26", scan.publisher_date_raw)
        self.assertEqual("2026-07-20", scan.publisher_date)
        self.assertEqual(4, scan.row_count)
        self.assertEqual(3, scan.business_row_count)
        self.assertEqual(3, scan.distinct_business_number_count)
        self.assertEqual(3, scan.allowlist_count)
        self.assertEqual(2, scan.matched_count)
        self.assertEqual(1, scan.missing_count)
        self.assertEqual(("00999999",), scan.missing_business_numbers)
        self.assertEqual(
            (("本國公司設立之分公司", 1), ("股份有限公司", 1)),
            scan.organization_type_counts,
        )
        self.assertEqual(
            ["00123456", "00888888"],
            [record.unified_business_number for record in scan.records],
        )
        derivative = canonical_matched_jsonl_bytes(scan).decode()
        self.assertNotIn("00777777", derivative)
        self.assertNotIn("資本額", derivative)
        self.assertNotIn("使用統一發票", derivative)
        self.assertIn('"head_office_unified_business_number":"00123456"', derivative)

    def test_allowlist_representation_is_strict_sorted_unique_ascii(self) -> None:
        raw = canonical_ubn_allowlist_bytes(ALLOWLIST)
        self.assertEqual(b"00123456\n00888888\n00999999\n", raw)
        self.assertEqual(tuple(ALLOWLIST), parse_ubn_allowlist_bytes(raw))
        invalid = (
            ["00888888", "00123456"],
            ["00123456", "00123456"],
            ["1234567"],
            ["１２３４５６７８"],
            [],
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                canonical_ubn_allowlist_bytes(values)
        for value in (b"00123456", b"00123456\r\n", b"00123456\n\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_ubn_allowlist_bytes(value)

    def test_output_is_deterministic_for_allowlist_input_type(self) -> None:
        first = self._write(mof_archive_raw(), "first.zip")
        second = self._write(mof_archive_raw(), "second.zip")
        self.assertEqual(
            canonical_matched_jsonl_bytes(scan_taiwan_mof_archive(first, ALLOWLIST)),
            canonical_matched_jsonl_bytes(
                scan_taiwan_mof_archive(second, tuple(ALLOWLIST))
            ),
        )

    def test_rejects_wrong_schema_and_invalid_publisher_date_row(self) -> None:
        date_row = ["20-JUL-26", *("" for _ in range(15))]
        bad_header = list(TAIWAN_MOF_FIELDS)
        bad_header[-1] = "unexpected"
        with self.assertRaisesRegex(ValueError, "exact 16-column schema"):
            scan_taiwan_mof_archive(
                self._write(_zip_csv(_csv_raw([date_row], header=tuple(bad_header)))),
                ALLOWLIST,
            )

        invalid_rows = (
            ["2026-07-20", *("" for _ in range(15))],
            ["31-FEB-26", *("" for _ in range(15))],
            ["20-JUL-26", "not blank", *("" for _ in range(14))],
        )
        for index, date_values in enumerate(invalid_rows):
            with self.subTest(date_values=date_values), self.assertRaises(ValueError):
                scan_taiwan_mof_archive(
                    self._write(_zip_csv(_csv_raw([date_values])), f"date-{index}.zip"),
                    ALLOWLIST,
                )

    def test_rejects_duplicate_business_keys_even_outside_allowlist(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate UBN"):
            scan_taiwan_mof_archive(
                self._write(mof_archive_raw(duplicate=True)), ALLOWLIST
            )

    def test_rejects_unsafe_archive_shapes_encoding_and_compression_ratio(self) -> None:
        valid_csv = _csv_raw([["20-JUL-26", *("" for _ in range(15))]])
        cases = (
            _zip_csv(valid_csv, member="nested/BGMOPEN1.csv"),
            _zip_csv(valid_csv, member="../BGMOPEN1.csv"),
            _zip_csv(valid_csv, mode=0o120777),
            _zip_csv(b"\xff\xfe", compression=zipfile.ZIP_STORED),
            _zip_csv(b"0" * 1_000_000),
        )
        for index, raw in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                scan_taiwan_mof_archive(
                    self._write(raw, f"unsafe-{index}.zip"), ALLOWLIST
                )

        stream = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("BGMOPEN1.csv", valid_csv)
                archive.writestr("BGMOPEN1.csv", valid_csv)
        with self.assertRaisesRegex(ValueError, "duplicate member"):
            scan_taiwan_mof_archive(
                self._write(stream.getvalue(), "duplicate.zip"), ALLOWLIST
            )

    def test_rejects_symlink_fifo_and_directory_inputs_without_blocking(self) -> None:
        source = self._write(mof_archive_raw())
        linked = self.root / "linked.zip"
        linked.symlink_to(source)
        with self.assertRaisesRegex(ValueError, "regular file"):
            scan_taiwan_mof_archive(linked, ALLOWLIST)
        with self.assertRaisesRegex(ValueError, "regular file"):
            scan_taiwan_mof_archive(self.root, ALLOWLIST)
        if hasattr(os, "mkfifo"):
            fifo = self.root / "source.fifo"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ValueError, "regular file"):
                scan_taiwan_mof_archive(fifo, ALLOWLIST)


if __name__ == "__main__":
    unittest.main()
