"""Strict offline scanner for Taiwan's national registered-factory archive.

The Industrial Development Administration publication is a nationwide CSV of
factory registrations.  This adapter retains only rows whose principal-product
list contains the exact official token ``261半導體``.  The publisher's
``生產中`` value is preserved as a *registration status* and is never promoted
to evidence of observed operation, output, utilization, or capacity.

The source includes the responsible person's name.  It is validated in the raw
schema but deliberately omitted from the public deterministic derivative and
from downstream claims because it is unnecessary for facility intelligence.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import stat
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Mapping


TAIWAN_FACTORY_DATASET_URL = "https://data.gov.tw/dataset/6569"
TAIWAN_FACTORY_POINTER_URL = "https://www.ida.gov.tw/opendata/02/SDD6569.csv"
TAIWAN_FACTORY_ARCHIVE_URL = (
    "https://serv.gcis.nat.gov.tw/RDownLoad/Data/statistical/"
    "%E7%94%9F%E7%94%A2%E4%B8%AD%E5%B7%A5%E5%BB%A0%E6%B8%85%E5%86%8A.zip"
)
TAIWAN_FACTORY_LICENSE = "政府資料開放授權條款-第1版"
TAIWAN_FACTORY_LICENSE_URL = "https://data.gov.tw/license"
TAIWAN_FACTORY_ATTRIBUTION = (
    "Source: 經濟部產業發展署 (Taiwan Ministry of Economic Affairs, "
    "Industrial Development Administration), 登記工廠名錄, 2026. Released "
    "under the Taiwan Open Government Data License, Version 1.0: "
    "https://data.gov.tw/license"
)

TAIWAN_FACTORY_FILTER_VERSION = "taiwan-ida-factory-principal-product-261-v1"
TAIWAN_FACTORY_PRODUCT_TOKEN = "261半導體"
TAIWAN_FACTORY_FIELDS = (
    "工廠名稱",
    "工廠登記編號",
    "工廠設立許可案號",
    "工廠地址",
    "工廠市鎮鄉村里",
    "工廠負責人姓名",
    "統一編號",
    "工廠組織型態",
    "工廠設立核准日期",
    "工廠登記核准日期",
    "工廠登記狀態",
    "產業類別",
    "主要產品",
)

# Public derivative names are stable and intentionally exclude the responsible
# person's name.
TAIWAN_FACTORY_PUBLIC_FIELDS = (
    "factory_name",
    "factory_registration_number",
    "establishment_approval_case_number",
    "factory_address",
    "administrative_area",
    "unified_business_number",
    "organization_type",
    "establishment_approved_at_raw",
    "registration_approved_at_raw",
    "registration_status",
    "industry_categories",
    "principal_products",
)

_PUBLIC_FIELD_MAP = dict(
    zip(
        TAIWAN_FACTORY_PUBLIC_FIELDS,
        (
            "工廠名稱",
            "工廠登記編號",
            "工廠設立許可案號",
            "工廠地址",
            "工廠市鎮鄉村里",
            "統一編號",
            "工廠組織型態",
            "工廠設立核准日期",
            "工廠登記核准日期",
            "工廠登記狀態",
            "產業類別",
            "主要產品",
        ),
        strict=True,
    )
)

_FACTORY_NUMBER_RE = re.compile(r"^[A-Z0-9]{8}$")
_LEGACY_FACTORY_NUMBER_PATTERNS = (
    re.compile(r"^[0-9]{10}$"),
    re.compile(r"^[0-9]{2}-[0-9]{6}-[0-9]{2}$"),
    re.compile(r"^[0-9]{2}-[A-Za-z][0-9]{5}-[0-9]{2}$"),
    re.compile(r"^[0-9]{2}-[0-9]{8}$"),
)
_BUSINESS_NUMBER_RE = re.compile(r"^[0-9]{8}$")
_CATEGORY_TOKEN_RE = re.compile(r"^[0-9]{2}(?:\S.*)?$")
_PRODUCT_TOKEN_RE = re.compile(r"^[0-9]{3}(?:\S.*)?$")
_RAW_DATE_RE = re.compile(r"^(?:|[0-9]{13})$")
_MAX_ARCHIVE_BYTES = 100_000_000
_MAX_CSV_BYTES = 500_000_000
_MAX_COMPRESSION_RATIO = 100
_MAX_FIELD_CHARS = 1_000_000
_READ_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class TaiwanFactoryRecord:
    factory_registration_number: str
    row: dict[str, str | tuple[str, ...]]
    canonical_json: bytes


@dataclass(frozen=True, slots=True)
class TaiwanFactoryArchiveScan:
    archive_sha256: str
    archive_bytes: int
    csv_member: str
    csv_sha256: str
    csv_bytes: int
    csv_crc32: int
    csv_compressed_bytes: int
    row_count: int
    candidate_row_count: int
    candidate_count: int
    business_number_count: int
    registration_status_counts: tuple[tuple[str, int], ...]
    records: tuple[TaiwanFactoryRecord, ...]


def normalize_factory_registration_number(value: str) -> str | None:
    """Normalize only the legacy certificate formats documented by MOEA.

    Official guidance maps an old certificate number to the first eight
    alphanumeric characters after separator removal.  Unknown shapes are not
    guessed: callers must leave them for review.
    """

    if not isinstance(value, str) or value != value.strip() or not value:
        return None
    if _FACTORY_NUMBER_RE.fullmatch(value.upper()):
        return value.upper()
    if not any(pattern.fullmatch(value) for pattern in _LEGACY_FACTORY_NUMBER_PATTERNS):
        return None
    compact = value.replace("-", "").upper()
    normalized = compact[:8]
    return normalized if _FACTORY_NUMBER_RE.fullmatch(normalized) else None


def _file_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _file_version(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


def _copy_and_hash_file(path: Path, destination: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("factory archive cannot be opened without symlink following")
    try:
        named_before = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(named_before.st_mode):
            raise ValueError(f"factory archive must be a regular file: {path}")
        descriptor = os.open(
            path,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
    except ValueError:
        raise
    except OSError as error:
        raise ValueError(f"factory archive must be a readable regular file: {path}") from error

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
            raise ValueError("factory archive path changed while it was being opened")
        while chunk := stream.read(_READ_CHUNK):
            size += len(chunk)
            if size > _MAX_ARCHIVE_BYTES:
                raise ValueError("factory archive exceeds the size limit")
            digest.update(chunk)
            destination.write(chunk)
        opened_after = os.fstat(stream.fileno())
        try:
            named_after = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise ValueError("factory archive path changed while it was being read") from error

    if (
        _file_identity(opened_after) != _file_identity(opened_before)
        or _file_identity(named_after) != _file_identity(opened_before)
        or not stat.S_ISREG(named_after.st_mode)
        or _file_version(opened_after) != _file_version(opened_before)
        or _file_version(named_after) != _file_version(opened_after)
        or size != opened_after.st_size
    ):
        raise ValueError("factory archive bytes changed while they were being read")
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
            raise ValueError(f"factory archive contains an unsafe member: {name!r}")
        if info.flag_bits & 0x1:
            raise ValueError(f"factory archive member must not be encrypted: {name}")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError(f"factory archive member uses unsupported compression: {name}")
        if info.compress_size == 0 and info.file_size:
            raise ValueError(f"factory archive member has an invalid compressed size: {name}")
        if info.compress_size and info.file_size > info.compress_size * _MAX_COMPRESSION_RATIO:
            raise ValueError(f"factory archive member exceeds compression-ratio limit: {name}")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValueError("factory archive contains duplicate member names")
    if len(infos) != 1 or PurePosixPath(infos[0].filename).suffix.lower() != ".csv":
        raise ValueError("factory archive must contain exactly one root CSV member")
    if infos[0].file_size > _MAX_CSV_BYTES:
        raise ValueError("factory CSV member exceeds the size limit")
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
        if self.bytes_read > self.declared_size or self.bytes_read > _MAX_CSV_BYTES:
            raise ValueError("factory CSV exceeds its declared or allowed size")
        self.digest.update(chunk)
        self.crc32 = zlib.crc32(chunk, self.crc32)
        buffer[: len(chunk)] = chunk
        return len(chunk)


def _tokens(value: str, *, product: bool, row_number: int) -> tuple[str, ...]:
    result = tuple(part.strip() for part in value.splitlines() if part.strip())
    pattern = _PRODUCT_TOKEN_RE if product else _CATEGORY_TOKEN_RE
    label = "principal product" if product else "industry category"
    if any(not pattern.fullmatch(token) for token in result):
        raise ValueError(f"factory CSV row {row_number} has an invalid {label} token")
    return result


def _public_row(row: Mapping[str, str], row_number: int) -> dict[str, str | tuple[str, ...]]:
    if any(len(value) > _MAX_FIELD_CHARS for value in row.values()):
        raise ValueError(f"factory CSV row {row_number} exceeds the field-size limit")
    registration_number = row["工廠登記編號"]
    business_number = row["統一編號"]
    if not _FACTORY_NUMBER_RE.fullmatch(registration_number):
        raise ValueError(f"factory CSV row {row_number} has an invalid registration number")
    if not _BUSINESS_NUMBER_RE.fullmatch(business_number):
        raise ValueError(f"factory CSV row {row_number} has an invalid business number")
    for source_field in ("工廠名稱", "工廠地址", "工廠登記狀態"):
        if not row[source_field].strip():
            raise ValueError(f"factory CSV row {row_number} field {source_field!r} is empty")
    for source_field in ("工廠設立核准日期", "工廠登記核准日期"):
        if not _RAW_DATE_RE.fullmatch(row[source_field]):
            raise ValueError(f"factory CSV row {row_number} field {source_field!r} has an invalid raw date")

    public: dict[str, str | tuple[str, ...]] = {
        target: row[source] for target, source in _PUBLIC_FIELD_MAP.items()
    }
    public["industry_categories"] = _tokens(
        row["產業類別"], product=False, row_number=row_number
    )
    public["principal_products"] = _tokens(
        row["主要產品"], product=True, row_number=row_number
    )
    return public


def _canonical_json(row: Mapping[str, object]) -> bytes:
    import json

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
) -> tuple[str, int, int, int, tuple[tuple[str, int], ...], tuple[TaiwanFactoryRecord, ...]]:
    row_count = 0
    candidate_rows: list[TaiwanFactoryRecord] = []
    status_counts: dict[str, int] = {}
    seen_registration_numbers: set[str] = set()
    try:
        with archive.open(info, "r") as source:
            hashing = _HashingReader(source, info.file_size)
            buffered = io.BufferedReader(hashing, buffer_size=_READ_CHUNK)
            text = io.TextIOWrapper(buffered, encoding="utf-8-sig", errors="strict", newline="")
            reader = csv.reader(text, dialect="excel", strict=True)
            try:
                header = next(reader)
            except StopIteration as error:
                raise ValueError("factory CSV is empty") from error
            if tuple(header) != TAIWAN_FACTORY_FIELDS:
                raise ValueError("factory CSV does not have the exact 13-column schema")
            for row_number, values in enumerate(reader, start=2):
                row_count += 1
                if len(values) != len(TAIWAN_FACTORY_FIELDS):
                    raise ValueError(
                        f"factory CSV row {row_number} has {len(values)} fields; expected 13"
                    )
                row = dict(zip(TAIWAN_FACTORY_FIELDS, values, strict=True))
                products = _tokens(row["主要產品"], product=True, row_number=row_number)
                # Validate category syntax for every row so the denominator is meaningful.
                _tokens(row["產業類別"], product=False, row_number=row_number)
                if TAIWAN_FACTORY_PRODUCT_TOKEN not in products:
                    continue
                public = _public_row(row, row_number)
                registration_number = str(public["factory_registration_number"])
                if registration_number in seen_registration_numbers:
                    raise ValueError(
                        "factory candidate rows contain a duplicate registration number: "
                        f"{registration_number}"
                    )
                seen_registration_numbers.add(registration_number)
                status = str(public["registration_status"])
                status_counts[status] = status_counts.get(status, 0) + 1
                canonical = _canonical_json(public)
                candidate_rows.append(
                    TaiwanFactoryRecord(registration_number, public, canonical)
                )
            # Force the decoder, ZIP member, and CRC verification to EOF.
            text.read()
            text.detach()
    except UnicodeDecodeError as error:
        raise ValueError("factory CSV must be valid UTF-8 with an optional BOM") from error
    except csv.Error as error:
        raise ValueError(f"factory CSV is malformed: {error}") from error

    crc32 = hashing.crc32 & 0xFFFFFFFF
    if hashing.bytes_read != info.file_size or crc32 != info.CRC:
        raise ValueError("factory CSV ZIP metadata does not match the complete member bytes")
    records = tuple(sorted(candidate_rows, key=lambda item: item.factory_registration_number))
    return (
        hashing.digest.hexdigest(),
        hashing.bytes_read,
        crc32,
        row_count,
        tuple(sorted(status_counts.items())),
        records,
    )


def scan_taiwan_factory_archive(path: str | Path) -> TaiwanFactoryArchiveScan:
    """Validate and completely scan one official registered-factory ZIP."""

    archive_path = Path(path)
    try:
        with tempfile.TemporaryFile(mode="w+b") as bound_archive:
            archive_sha256, archive_bytes = _copy_and_hash_file(archive_path, bound_archive)
            bound_archive.flush()
            bound_archive.seek(0)
            with zipfile.ZipFile(bound_archive, "r", allowZip64=True) as archive:
                info = _safe_csv_member(archive)
                csv_sha256, csv_bytes, csv_crc32, row_count, statuses, records = (
                    _scan_csv_member(archive, info)
                )
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ValueError(f"factory archive is not a valid complete ZIP: {error}") from error

    business_numbers = {
        str(record.row["unified_business_number"]) for record in records
    }
    return TaiwanFactoryArchiveScan(
        archive_sha256=archive_sha256,
        archive_bytes=archive_bytes,
        csv_member=info.filename,
        csv_sha256=csv_sha256,
        csv_bytes=csv_bytes,
        csv_crc32=csv_crc32,
        csv_compressed_bytes=info.compress_size,
        row_count=row_count,
        candidate_row_count=len(records),
        candidate_count=len(records),
        business_number_count=len(business_numbers),
        registration_status_counts=statuses,
        records=records,
    )


def canonical_candidate_jsonl_bytes(scan: TaiwanFactoryArchiveScan) -> bytes:
    """Return stable privacy-minimized candidate JSONL sorted by registration ID."""

    return b"".join(record.canonical_json + b"\n" for record in scan.records)


__all__ = [
    "TAIWAN_FACTORY_ARCHIVE_URL",
    "TAIWAN_FACTORY_ATTRIBUTION",
    "TAIWAN_FACTORY_DATASET_URL",
    "TAIWAN_FACTORY_FIELDS",
    "TAIWAN_FACTORY_FILTER_VERSION",
    "TAIWAN_FACTORY_LICENSE",
    "TAIWAN_FACTORY_LICENSE_URL",
    "TAIWAN_FACTORY_POINTER_URL",
    "TAIWAN_FACTORY_PRODUCT_TOKEN",
    "TAIWAN_FACTORY_PUBLIC_FIELDS",
    "TaiwanFactoryArchiveScan",
    "TaiwanFactoryRecord",
    "canonical_candidate_jsonl_bytes",
    "normalize_factory_registration_number",
    "scan_taiwan_factory_archive",
]
