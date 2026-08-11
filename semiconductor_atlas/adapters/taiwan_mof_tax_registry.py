"""Strict offline scanner for Taiwan MOF's active tax-registration archive.

The BGMOPEN1 publication contains all active tax registrations, not only legal
companies.  This adapter performs an exact lookup against a separately pinned
eight-digit UBN allowlist and emits only the organizational fields needed for
later resolution.  A tax unit is never promoted to a legal company, parent, or
operating facility by this layer.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Mapping


TAIWAN_MOF_DATASET_URL = "https://data.gov.tw/dataset/9400"
TAIWAN_MOF_ARCHIVE_URL = "https://eip.fia.gov.tw/data/BGMOPEN1.zip"
TAIWAN_MOF_LICENSE = "政府資料開放授權條款-第1版"
TAIWAN_MOF_LICENSE_URL = "https://data.gov.tw/license"
TAIWAN_MOF_ATTRIBUTION = (
    "Source: 財政部財政資訊中心 (Taiwan Ministry of Finance, Fiscal "
    "Information Agency), 全國營業(稅籍)登記資料集, 2026. Released under "
    "the Taiwan Open Government Data License, Version 1.0: "
    "https://data.gov.tw/license"
)
TAIWAN_MOF_DATASET_ID = "9400"
TAIWAN_MOF_RESOURCE_NAME = "BGMOPEN1"
TAIWAN_MOF_FILTER_VERSION = "taiwan-mof-bgmopen1-exact-ubn-allowlist-v1"
TAIWAN_MOF_FIELDS = (
    "營業地址",
    "統一編號",
    "總機構統一編號",
    "營業人名稱",
    "資本額",
    "設立日期",
    "組織別名稱",
    "使用統一發票",
    "行業代號",
    "名稱",
    "行業代號1",
    "名稱1",
    "行業代號2",
    "名稱2",
    "行業代號3",
    "名稱3",
)
TAIWAN_MOF_PUBLIC_FIELDS = (
    "unified_business_number",
    "head_office_unified_business_number",
    "business_name",
    "business_address",
    "established_date_raw",
    "organization_type",
    "industry_activities",
)

TAIWAN_MOF_MAX_ARCHIVE_BYTES = 100_000_000
TAIWAN_MOF_MAX_CSV_BYTES = 400_000_000
TAIWAN_MOF_MAX_ALLOWLIST_RECORDS = 10_000
TAIWAN_MOF_MAX_ALLOWLIST_BYTES = 90_000
TAIWAN_MOF_MAX_COMPRESSION_RATIO = 20

_UBN_RE = re.compile(r"^[0-9]{8}$")
_PUBLISHER_DATE_RE = re.compile(r"^([0-9]{2})-([A-Z]{3})-([0-9]{2})$")
_ESTABLISHED_DATE_RE = re.compile(r"^(?:|[0-9]{7})$")
_INDUSTRY_CODE_RE = re.compile(r"^(?:|[0-9]{6})$")
_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
_MAX_FIELD_CHARS = 1_000_000
_READ_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class TaiwanMOFTaxRecord:
    unified_business_number: str
    row: dict[str, object]
    canonical_json: bytes


@dataclass(frozen=True, slots=True)
class TaiwanMOFArchiveScan:
    archive_sha256: str
    archive_bytes: int
    csv_member: str
    csv_sha256: str
    csv_bytes: int
    csv_crc32: int
    csv_compressed_bytes: int
    publisher_date_raw: str
    publisher_date: str
    row_count: int
    business_row_count: int
    distinct_business_number_count: int
    allowlist_count: int
    matched_count: int
    missing_count: int
    missing_business_numbers: tuple[str, ...]
    organization_type_counts: tuple[tuple[str, int], ...]
    records: tuple[TaiwanMOFTaxRecord, ...]


def canonical_ubn_allowlist_bytes(values: tuple[str, ...] | list[str]) -> bytes:
    """Return the canonical sorted, unique, newline-terminated UBN allowlist."""

    if not isinstance(values, (tuple, list)):
        raise TypeError("Taiwan MOF UBN allowlist must be a tuple or list")
    normalized = tuple(values)
    if not normalized:
        raise ValueError("Taiwan MOF UBN allowlist must not be empty")
    if len(normalized) > TAIWAN_MOF_MAX_ALLOWLIST_RECORDS:
        raise ValueError("Taiwan MOF UBN allowlist exceeds the record limit")
    for index, value in enumerate(normalized):
        if not isinstance(value, str) or not _UBN_RE.fullmatch(value):
            raise ValueError(
                f"Taiwan MOF UBN allowlist entry {index} is not eight digits"
            )
    if len(set(normalized)) != len(normalized):
        raise ValueError("Taiwan MOF UBN allowlist contains duplicate values")
    if tuple(sorted(normalized)) != normalized:
        raise ValueError("Taiwan MOF UBN allowlist must be sorted")
    raw = ("\n".join(normalized) + "\n").encode("ascii")
    if len(raw) > TAIWAN_MOF_MAX_ALLOWLIST_BYTES:
        raise ValueError("Taiwan MOF UBN allowlist exceeds the byte limit")
    return raw


def parse_ubn_allowlist_bytes(raw: bytes) -> tuple[str, ...]:
    """Parse and require the exact canonical UBN allowlist representation."""

    if not isinstance(raw, bytes):
        raise TypeError("Taiwan MOF UBN allowlist must be bytes")
    if not raw or len(raw) > TAIWAN_MOF_MAX_ALLOWLIST_BYTES:
        raise ValueError("Taiwan MOF UBN allowlist is empty or exceeds the byte limit")
    try:
        text = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("Taiwan MOF UBN allowlist must be ASCII") from error
    if not text.endswith("\n") or "\r" in text:
        raise ValueError("Taiwan MOF UBN allowlist must use LF and end with one LF")
    lines = tuple(text[:-1].split("\n"))
    if any(not line for line in lines):
        raise ValueError("Taiwan MOF UBN allowlist must not contain blank lines")
    if canonical_ubn_allowlist_bytes(list(lines)) != raw:
        raise ValueError("Taiwan MOF UBN allowlist is not canonical")
    return lines


def _file_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _file_version(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


def _copy_and_hash_file(path: Path, destination: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("MOF archive cannot be opened without symlink protection")
    try:
        named_before = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(named_before.st_mode):
            raise ValueError(f"MOF archive must be a regular file: {path}")
        descriptor = os.open(
            path,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
    except ValueError:
        raise
    except OSError as error:
        raise ValueError(
            f"MOF archive must be a readable regular file: {path}"
        ) from error

    try:
        stream = os.fdopen(descriptor, "rb", closefd=True)
    except Exception:
        os.close(descriptor)
        raise

    with stream:
        opened_before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(opened_before.st_mode)
            or _file_identity(opened_before) != _file_identity(named_before)
            or _file_version(opened_before) != _file_version(named_before)
        ):
            raise ValueError("MOF archive path changed while it was being opened")
        while chunk := stream.read(_READ_CHUNK_BYTES):
            size += len(chunk)
            if size > TAIWAN_MOF_MAX_ARCHIVE_BYTES:
                raise ValueError("MOF archive exceeds the 100 MB size limit")
            digest.update(chunk)
            destination.write(chunk)
        opened_after = os.fstat(stream.fileno())
        try:
            named_after = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise ValueError(
                "MOF archive path changed while it was being read"
            ) from error

    if (
        _file_identity(opened_after) != _file_identity(opened_before)
        or _file_identity(named_after) != _file_identity(opened_before)
        or not stat.S_ISREG(named_after.st_mode)
        or _file_version(opened_after) != _file_version(opened_before)
        or _file_version(named_after) != _file_version(opened_after)
        or size != opened_after.st_size
    ):
        raise ValueError("MOF archive bytes changed while they were being read")
    return digest.hexdigest(), size


def _safe_csv_member(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    infos = archive.infolist()
    for info in infos:
        name = info.filename
        pure = PurePosixPath(name)
        unix_mode = info.external_attr >> 16
        file_type = stat.S_IFMT(unix_mode)
        if (
            not name
            or name != info.orig_filename
            or pure.is_absolute()
            or len(pure.parts) != 1
            or any(part in {"", ".", ".."} for part in pure.parts)
            or "\\" in name
            or info.is_dir()
            or (file_type and not stat.S_ISREG(unix_mode))
        ):
            raise ValueError(f"MOF archive contains an unsafe member: {name!r}")
        if info.flag_bits & 0x1:
            raise ValueError(f"MOF archive member must not be encrypted: {name}")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError(f"MOF archive member uses unsupported compression: {name}")
        if info.compress_size == 0 and info.file_size:
            raise ValueError(
                f"MOF archive member has an invalid compressed size: {name}"
            )
        if (
            info.compress_size
            and info.file_size > info.compress_size * TAIWAN_MOF_MAX_COMPRESSION_RATIO
        ):
            raise ValueError("MOF archive member exceeds the compression-ratio limit")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValueError("MOF archive contains duplicate member names")
    if (
        len(infos) != 1
        or infos[0].filename != "BGMOPEN1.csv"
        or PurePosixPath(infos[0].filename).suffix.lower() != ".csv"
    ):
        raise ValueError("MOF archive must contain only the root BGMOPEN1.csv member")
    if not 0 < infos[0].file_size <= TAIWAN_MOF_MAX_CSV_BYTES:
        raise ValueError("MOF CSV member is empty or exceeds the size limit")
    return infos[0]


class _HashingReader(io.RawIOBase):
    def __init__(self, source: BinaryIO, declared_size: int) -> None:
        self.source = source
        self.declared_size = declared_size
        self.digest = hashlib.sha256()
        self.crc32 = 0
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:
        chunk = self.source.read(len(buffer))
        if not chunk:
            return 0
        self.bytes_read += len(chunk)
        if (
            self.bytes_read > self.declared_size
            or self.bytes_read > TAIWAN_MOF_MAX_CSV_BYTES
        ):
            raise ValueError("MOF CSV exceeds its declared or allowed size")
        self.digest.update(chunk)
        self.crc32 = zlib.crc32(chunk, self.crc32)
        buffer[: len(chunk)] = chunk
        return len(chunk)


def _publisher_date(value: str) -> str:
    match = _PUBLISHER_DATE_RE.fullmatch(value)
    if match is None:
        raise ValueError("MOF CSV publisher-date row must use DD-MON-YY")
    day_text, month_text, year_text = match.groups()
    month = _MONTHS.get(month_text)
    if month is None:
        raise ValueError("MOF CSV publisher-date row has an invalid month")
    try:
        parsed = date(2000 + int(year_text), month, int(day_text))
    except ValueError as error:
        raise ValueError("MOF CSV publisher-date row is not a calendar date") from error
    return parsed.isoformat()


def _industry_activities(
    row: Mapping[str, str], row_number: int
) -> tuple[dict[str, str], ...]:
    pairs = (
        ("行業代號", "名稱"),
        ("行業代號1", "名稱1"),
        ("行業代號2", "名稱2"),
        ("行業代號3", "名稱3"),
    )
    activities: list[dict[str, str]] = []
    for code_field, name_field in pairs:
        code = row[code_field]
        name = row[name_field]
        if not _INDUSTRY_CODE_RE.fullmatch(code):
            raise ValueError(f"MOF CSV row {row_number} has an invalid industry code")
        if bool(code) != bool(name):
            raise ValueError(
                f"MOF CSV row {row_number} has an orphan industry code or name"
            )
        if code:
            activities.append({"code": code, "name": name})
    return tuple(activities)


def _public_row(row: Mapping[str, str], row_number: int) -> dict[str, object]:
    if any(len(value) > _MAX_FIELD_CHARS for value in row.values()):
        raise ValueError(f"MOF CSV row {row_number} exceeds the field-size limit")
    ubn = row["統一編號"]
    head_office = row["總機構統一編號"]
    if not _UBN_RE.fullmatch(ubn):
        raise ValueError(f"MOF CSV row {row_number} has an invalid UBN")
    if head_office and not _UBN_RE.fullmatch(head_office):
        raise ValueError(f"MOF CSV row {row_number} has an invalid head-office UBN")
    for field in ("營業地址", "營業人名稱", "組織別名稱"):
        if not row[field].strip():
            raise ValueError(f"MOF CSV row {row_number} field {field!r} is empty")
    if not _ESTABLISHED_DATE_RE.fullmatch(row["設立日期"]):
        raise ValueError(f"MOF CSV row {row_number} has an invalid establishment date")
    return {
        "unified_business_number": ubn,
        "head_office_unified_business_number": head_office,
        "business_name": row["營業人名稱"],
        "business_address": row["營業地址"],
        "established_date_raw": row["設立日期"],
        "organization_type": row["組織別名稱"],
        "industry_activities": _industry_activities(row, row_number),
    }


def _canonical_json(row: Mapping[str, object]) -> bytes:
    return json.dumps(
        row,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _scan_csv_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    allowlist: tuple[str, ...],
) -> tuple[
    str,
    int,
    int,
    str,
    str,
    int,
    int,
    tuple[tuple[str, int], ...],
    tuple[TaiwanMOFTaxRecord, ...],
]:
    allowset = set(allowlist)
    seen_business_numbers: set[str] = set()
    matched: list[TaiwanMOFTaxRecord] = []
    organization_counts: dict[str, int] = {}
    row_count = 0
    business_row_count = 0
    publisher_date_raw = ""
    publisher_date = ""
    try:
        with archive.open(info, "r") as source:
            hashing = _HashingReader(source, info.file_size)
            buffered = io.BufferedReader(hashing, buffer_size=_READ_CHUNK_BYTES)
            text = io.TextIOWrapper(
                buffered,
                encoding="utf-8-sig",
                errors="strict",
                newline="",
            )
            reader = csv.reader(text, dialect="excel", strict=True)
            try:
                header = next(reader)
            except StopIteration as error:
                raise ValueError("MOF CSV is empty") from error
            if tuple(header) != TAIWAN_MOF_FIELDS:
                raise ValueError("MOF CSV does not have the exact 16-column schema")
            try:
                date_values = next(reader)
            except StopIteration as error:
                raise ValueError("MOF CSV is missing its publisher-date row") from error
            row_count = 1
            if len(date_values) != len(TAIWAN_MOF_FIELDS):
                raise ValueError("MOF CSV publisher-date row must contain 16 fields")
            if any(date_values[index] for index in range(1, len(date_values))):
                raise ValueError(
                    "MOF CSV publisher-date row must be blank after the address column"
                )
            publisher_date_raw = date_values[0]
            publisher_date = _publisher_date(publisher_date_raw)

            for row_number, values in enumerate(reader, start=3):
                row_count += 1
                business_row_count += 1
                if len(values) != len(TAIWAN_MOF_FIELDS):
                    raise ValueError(
                        f"MOF CSV row {row_number} has {len(values)} fields; expected 16"
                    )
                row = dict(zip(TAIWAN_MOF_FIELDS, values, strict=True))
                ubn = row["統一編號"]
                if not _UBN_RE.fullmatch(ubn):
                    raise ValueError(f"MOF CSV row {row_number} has an invalid UBN")
                if ubn in seen_business_numbers:
                    raise ValueError(f"MOF CSV contains duplicate UBN {ubn}")
                seen_business_numbers.add(ubn)
                if ubn not in allowset:
                    continue
                public = _public_row(row, row_number)
                organization_type = str(public["organization_type"])
                organization_counts[organization_type] = (
                    organization_counts.get(organization_type, 0) + 1
                )
                matched.append(TaiwanMOFTaxRecord(ubn, public, _canonical_json(public)))
            # Force decoder, decompressor, and CRC verification through EOF.
            text.read()
            text.detach()
    except UnicodeDecodeError as error:
        raise ValueError("MOF CSV must be valid UTF-8 with an optional BOM") from error
    except csv.Error as error:
        raise ValueError(f"MOF CSV is malformed: {error}") from error

    crc32 = hashing.crc32 & 0xFFFFFFFF
    if hashing.bytes_read != info.file_size or crc32 != info.CRC:
        raise ValueError(
            "MOF CSV ZIP metadata does not match the complete member bytes"
        )
    records = tuple(sorted(matched, key=lambda item: item.unified_business_number))
    return (
        hashing.digest.hexdigest(),
        hashing.bytes_read,
        crc32,
        publisher_date_raw,
        publisher_date,
        row_count,
        business_row_count,
        tuple(sorted(organization_counts.items())),
        records,
    )


def scan_taiwan_mof_archive(
    path: str | Path,
    allowlist: tuple[str, ...] | list[str],
) -> TaiwanMOFArchiveScan:
    """Validate and completely scan one BGMOPEN1 ZIP for exact allowlisted UBNs."""

    allowlist_raw = canonical_ubn_allowlist_bytes(allowlist)
    normalized_allowlist = parse_ubn_allowlist_bytes(allowlist_raw)
    archive_path = Path(path)
    try:
        with tempfile.TemporaryFile(mode="w+b") as bound_archive:
            archive_sha256, archive_bytes = _copy_and_hash_file(
                archive_path, bound_archive
            )
            bound_archive.flush()
            bound_archive.seek(0)
            with zipfile.ZipFile(bound_archive, "r", allowZip64=True) as archive:
                info = _safe_csv_member(archive)
                (
                    csv_sha256,
                    csv_bytes,
                    csv_crc32,
                    publisher_date_raw,
                    publisher_date,
                    row_count,
                    business_row_count,
                    organization_counts,
                    records,
                ) = _scan_csv_member(archive, info, normalized_allowlist)
    except (
        OSError,
        EOFError,
        RuntimeError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as error:
        raise ValueError(f"MOF archive is not a valid complete ZIP: {error}") from error

    matched_numbers = {record.unified_business_number for record in records}
    missing = tuple(
        value for value in normalized_allowlist if value not in matched_numbers
    )
    return TaiwanMOFArchiveScan(
        archive_sha256=archive_sha256,
        archive_bytes=archive_bytes,
        csv_member=info.filename,
        csv_sha256=csv_sha256,
        csv_bytes=csv_bytes,
        csv_crc32=csv_crc32,
        csv_compressed_bytes=info.compress_size,
        publisher_date_raw=publisher_date_raw,
        publisher_date=publisher_date,
        row_count=row_count,
        business_row_count=business_row_count,
        distinct_business_number_count=business_row_count,
        allowlist_count=len(normalized_allowlist),
        matched_count=len(records),
        missing_count=len(missing),
        missing_business_numbers=missing,
        organization_type_counts=organization_counts,
        records=records,
    )


def canonical_matched_jsonl_bytes(scan: TaiwanMOFArchiveScan) -> bytes:
    """Return stable privacy-conscious matched records sorted by exact UBN."""

    return b"".join(record.canonical_json + b"\n" for record in scan.records)


__all__ = [
    "TAIWAN_MOF_ARCHIVE_URL",
    "TAIWAN_MOF_ATTRIBUTION",
    "TAIWAN_MOF_DATASET_ID",
    "TAIWAN_MOF_DATASET_URL",
    "TAIWAN_MOF_FIELDS",
    "TAIWAN_MOF_FILTER_VERSION",
    "TAIWAN_MOF_LICENSE",
    "TAIWAN_MOF_LICENSE_URL",
    "TAIWAN_MOF_MAX_ALLOWLIST_BYTES",
    "TAIWAN_MOF_MAX_ALLOWLIST_RECORDS",
    "TAIWAN_MOF_MAX_ARCHIVE_BYTES",
    "TAIWAN_MOF_MAX_COMPRESSION_RATIO",
    "TAIWAN_MOF_MAX_CSV_BYTES",
    "TAIWAN_MOF_PUBLIC_FIELDS",
    "TAIWAN_MOF_RESOURCE_NAME",
    "TaiwanMOFArchiveScan",
    "TaiwanMOFTaxRecord",
    "canonical_matched_jsonl_bytes",
    "canonical_ubn_allowlist_bytes",
    "parse_ubn_allowlist_bytes",
    "scan_taiwan_mof_archive",
]
