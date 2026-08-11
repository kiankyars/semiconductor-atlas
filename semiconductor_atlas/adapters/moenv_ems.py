"""Strict offline scanner for Taiwan MOENV EMS_S_01 archives.

The upstream product is a large ZIP containing one JSON array and a publisher
``hash.txt`` file.  This module binds validation to one private archive copy,
streams the JSON member, and retains only the exact semiconductor-industry
rows.  Regulatory flags are kept as regulatory facts; they are never treated
as evidence that a facility is operating.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import stat
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


MOENV_HASH_MEMBER = "hash.txt"
MOENV_FILTER_VERSION = "moenv-ems-s-01-industry-261-v1"
MOENV_INDUSTRY_GROUP = "261"
MOENV_INDUSTRY_LABELS = {
    "2611": "積體電路製造業",
    "2612": "分離式元件製造業",
    "2613": "半導體封裝及測試業",
}
MOENV_INDUSTRY_CODES = tuple(MOENV_INDUSTRY_LABELS)
MOENV_DATASET_URL = "https://data.moenv.gov.tw/dataset/detail/EMS_S_01"
MOENV_LICENSE = "依政府資料開放平臺使用規範"
MOENV_LICENSE_URL = "https://data.gov.tw/license"
MOENV_ATTRIBUTION = (
    "Source: 環境部資源循環署 (Taiwan Ministry of Environment, Resource "
    "Circulation Administration), 環境保護許可管理系統(暨解除列管)"
    "對象基本資料 (EMS_S_01), 2026. Released under the Taiwan Open "
    "Government Data License, Version 1.0: https://data.gov.tw/license"
)

MOENV_FIELDS = (
    "emsno",
    "facilityname",
    "uniformno",
    "county",
    "township",
    "facilityaddress",
    "industryareaname",
    "industryid",
    "industryname",
    "twd97tm2x",
    "twd97tm2y",
    "wgs84lon",
    "wgs84lat",
    "isair",
    "iswater",
    "iswaste",
    "istoxic",
    "issoil",
    "airreleasedate",
    "waterreleasedate",
    "wastereleasedate",
    "toxicreleasedate",
    "soilreleasedate",
    "industrygroup",
    "admino",
    "facno",
)
MOENV_FLAG_FIELDS = ("isair", "iswater", "iswaste", "istoxic", "issoil")
MOENV_RELEASE_DATE_FIELDS = (
    "airreleasedate",
    "waterreleasedate",
    "wastereleasedate",
    "toxicreleasedate",
    "soilreleasedate",
)

_EMSNO_RE = re.compile(r"^[A-Za-z0-9]{8}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DECIMAL_RE = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")
_PUBLISHER_HASH_RE = re.compile(
    r"(?P<member>[^\r\n]+?)[：:](?P<md5>[0-9A-Fa-f]{32})[，,]"
    r"\s*演算法[：:]\s*MD5\s*"
)
_JSON_WHITESPACE = " \t\r\n"

_MAX_ARCHIVE_BYTES = 250_000_000
_MAX_JSON_BYTES = 1_000_000_000
_MAX_HASH_BYTES = 4_096
_MAX_COMPRESSION_RATIO = 100
_MAX_RECORD_CHARS = 2_000_000
_READ_CHUNK = 1024 * 1024
_NULLABLE_FIELDS = frozenset({"admino", "facno"})


@dataclass(frozen=True, slots=True)
class MOENVVariant:
    """One distinct exact source payload for an EMS control number."""

    row: dict[str, str | None]
    canonical_json: bytes


@dataclass(frozen=True, slots=True)
class MOENVFacility:
    """All distinct source variants sharing one case-sensitive ``emsno``."""

    emsno: str
    variants: tuple[MOENVVariant, ...]


@dataclass(frozen=True, slots=True)
class MOENVArchiveScan:
    archive_sha256: str
    archive_bytes: int
    json_member: str
    json_sha256: str
    json_md5: str
    json_bytes: int
    json_crc32: int
    json_compressed_bytes: int
    hash_member: str
    hash_member_sha256: str
    hash_member_bytes: int
    hash_member_crc32: int
    hash_member_compressed_bytes: int
    publisher_md5: str
    row_count: int
    industry_group_row_count: int
    candidate_row_count: int
    candidate_count: int
    facility_count: int
    current_regulation_count: int
    valid_coordinate_count: int
    conflicting_facility_count: int
    facilities: tuple[MOENVFacility, ...]


class _DecodedMember:
    """Incrementally decode and hash one ZIP member without materializing it."""

    def __init__(self, source: zipfile.ZipExtFile, info: zipfile.ZipInfo) -> None:
        self._source = source
        self._info = info
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self._sha256 = hashlib.sha256()
        self._md5 = hashlib.md5(usedforsecurity=False)
        self._crc32 = 0
        self.bytes_read = 0
        self.eof = False

    def read_text(self) -> str:
        if self.eof:
            return ""
        raw = self._source.read(_READ_CHUNK)
        if raw:
            self.bytes_read += len(raw)
            if (
                self.bytes_read > self._info.file_size
                or self.bytes_read > _MAX_JSON_BYTES
            ):
                raise ValueError("MOENV JSON member exceeds its declared or allowed size")
            self._sha256.update(raw)
            self._md5.update(raw)
            self._crc32 = zlib.crc32(raw, self._crc32)
            return self._decoder.decode(raw, final=False)
        self.eof = True
        return self._decoder.decode(b"", final=True)

    @property
    def sha256(self) -> str:
        return self._sha256.hexdigest()

    @property
    def md5(self) -> str:
        return self._md5.hexdigest()

    @property
    def crc32(self) -> int:
        return self._crc32 & 0xFFFFFFFF


class _TextBuffer:
    """Small compacting buffer over :class:`_DecodedMember`."""

    def __init__(self, member: _DecodedMember) -> None:
        self.member = member
        self.text = ""
        self.position = 0

    def _compact(self) -> None:
        if self.position:
            self.text = self.text[self.position :]
            self.position = 0

    def read_more(self) -> None:
        self._compact()
        while not self.member.eof:
            decoded = self.member.read_text()
            self.text += decoded
            if decoded or self.member.eof:
                return

    def ensure_character(self) -> bool:
        while self.position >= len(self.text) and not self.member.eof:
            self.read_more()
        return self.position < len(self.text)

    def skip_json_whitespace(self) -> None:
        while True:
            while (
                self.position < len(self.text)
                and self.text[self.position] in _JSON_WHITESPACE
            ):
                self.position += 1
            if self.position < len(self.text) or self.member.eof:
                return
            self.read_more()


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"MOENV JSON object contains duplicate key {key!r}")
        value[key] = item
    return value


def _json_constant(value: str) -> None:
    raise ValueError(f"MOENV JSON contains non-finite value {value}")


_JSON_DECODER = json.JSONDecoder(
    object_pairs_hook=_json_object,
    parse_constant=_json_constant,
)


def _canonical_row(row: Mapping[str, str | None]) -> bytes:
    return json.dumps(
        row,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate_exact_shape_and_types(
    row: object,
    row_number: int,
) -> dict[str, str | None]:
    if not isinstance(row, dict):
        raise ValueError(f"MOENV JSON array row {row_number} must be an object")
    actual = set(row)
    expected = set(MOENV_FIELDS)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ValueError(
            f"MOENV JSON row {row_number} does not have the exact 26-field schema "
            f"(missing={missing!r}, unexpected={unexpected!r})"
        )
    exact: dict[str, str | None] = {}
    for field in MOENV_FIELDS:
        value = row[field]
        if field in _NULLABLE_FIELDS:
            valid_type = value is None or isinstance(value, str)
            expected_type = "a string or null"
        else:
            valid_type = isinstance(value, str)
            expected_type = "a string"
        if not valid_type:
            raise ValueError(
                f"MOENV JSON row {row_number} field {field!r} must be {expected_type}"
            )
        exact[field] = value
    return exact


def _validate_candidate(row: Mapping[str, str | None], row_number: int) -> None:
    emsno = row["emsno"]
    if not isinstance(emsno, str) or not _EMSNO_RE.fullmatch(emsno):
        raise ValueError(
            f"MOENV candidate row {row_number} has an invalid 8-character emsno"
        )

    industry_id = row["industryid"]
    assert isinstance(industry_id, str) and industry_id in MOENV_INDUSTRY_LABELS
    expected_label = MOENV_INDUSTRY_LABELS[industry_id]
    if row["industryname"] != expected_label:
        raise ValueError(
            f"MOENV candidate row {row_number} industryname does not exactly match "
            f"the official label for {industry_id}"
        )

    for field in MOENV_FLAG_FIELDS:
        if row[field] not in {"0", "1"}:
            raise ValueError(
                f"MOENV candidate row {row_number} field {field!r} must be '0' or '1'"
            )

    for field in MOENV_RELEASE_DATE_FIELDS:
        value = row[field]
        assert isinstance(value, str)
        if value == "":
            continue
        if not _DATE_RE.fullmatch(value):
            raise ValueError(
                f"MOENV candidate row {row_number} field {field!r} must be YYYY-MM-DD"
            )
        try:
            parsed = date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(
                f"MOENV candidate row {row_number} field {field!r} is not a real date"
            ) from error
        if parsed.isoformat() != value:
            raise ValueError(
                f"MOENV candidate row {row_number} field {field!r} is not canonical"
            )


def is_currently_regulated(row: Mapping[str, object]) -> bool:
    """Return current regulation membership, never an operating-status claim."""

    return any(row.get(field) == "1" for field in MOENV_FLAG_FIELDS)


def valid_taiwan_wgs84_point(
    row: Mapping[str, object],
) -> tuple[str, str] | None:
    """Return preserved ``(longitude, latitude)`` strings for a valid Taiwan point."""

    longitude = row.get("wgs84lon")
    latitude = row.get("wgs84lat")
    if not isinstance(longitude, str) or not isinstance(latitude, str):
        return None
    if not _DECIMAL_RE.fullmatch(longitude) or not _DECIMAL_RE.fullmatch(latitude):
        return None
    try:
        lon = Decimal(longitude)
        lat = Decimal(latitude)
    except InvalidOperation:
        return None
    if not lon.is_finite() or not lat.is_finite():
        return None
    if lon == 0 and lat == 0:
        return None
    if not (Decimal("118") <= lon <= Decimal("123")):
        return None
    if not (Decimal("21") <= lat <= Decimal("27")):
        return None
    return longitude, latitude


def _decode_next_object(
    buffer: _TextBuffer,
    row_number: int,
) -> tuple[object, int]:
    start = buffer.position
    while True:
        if len(buffer.text) - start > _MAX_RECORD_CHARS:
            raise ValueError(
                f"MOENV JSON row {row_number} exceeds the per-record size limit"
            )
        try:
            return _JSON_DECODER.raw_decode(buffer.text, start)
        except json.JSONDecodeError as error:
            if buffer.member.eof:
                raise ValueError(
                    f"MOENV JSON row {row_number} is malformed or truncated"
                ) from error
            buffer.position = start
            buffer.read_more()
            start = 0


def _scan_json_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> tuple[
    str,
    str,
    int,
    int,
    int,
    int,
    int,
    dict[str, dict[bytes, dict[str, str | None]]],
]:
    grouped: dict[str, dict[bytes, dict[str, str | None]]] = {}
    row_count = 0
    industry_group_row_count = 0
    candidate_row_count = 0

    try:
        with archive.open(info, "r") as source:
            member = _DecodedMember(source, info)
            buffer = _TextBuffer(member)
            buffer.skip_json_whitespace()
            if not buffer.ensure_character():
                raise ValueError("MOENV JSON member is empty")
            first = buffer.text[buffer.position]
            if first == "<":
                raise ValueError("MOENV JSON member contains HTML or an upstream error page")
            if first != "[":
                raise ValueError("MOENV JSON member must contain one top-level array")
            buffer.position += 1
            first_item = True

            while True:
                buffer.skip_json_whitespace()
                if not buffer.ensure_character():
                    raise ValueError("MOENV JSON top-level array is truncated")
                character = buffer.text[buffer.position]
                if character == "]":
                    buffer.position += 1
                    break
                if not first_item:
                    if character != ",":
                        raise ValueError("MOENV JSON array entries must be comma-separated")
                    buffer.position += 1
                    buffer.skip_json_whitespace()
                    if not buffer.ensure_character():
                        raise ValueError("MOENV JSON top-level array is truncated")
                    character = buffer.text[buffer.position]
                    if character == "]":
                        raise ValueError("MOENV JSON top-level array has a trailing comma")
                if character != "{":
                    raise ValueError(
                        f"MOENV JSON array row {row_count + 1} must be an object"
                    )

                raw_row, end = _decode_next_object(buffer, row_count + 1)
                buffer.position = end
                row_count += 1
                row = _validate_exact_shape_and_types(raw_row, row_count)

                if row["industrygroup"] == MOENV_INDUSTRY_GROUP:
                    industry_group_row_count += 1
                industry_id = row["industryid"]
                if (
                    row["industrygroup"] == MOENV_INDUSTRY_GROUP
                    and isinstance(industry_id, str)
                    and industry_id in MOENV_INDUSTRY_LABELS
                ):
                    candidate_row_count += 1
                    _validate_candidate(row, row_count)
                    emsno = row["emsno"]
                    assert isinstance(emsno, str)
                    canonical = _canonical_row(row)
                    grouped.setdefault(emsno, {}).setdefault(canonical, row)
                first_item = False

            buffer.skip_json_whitespace()
            while not member.eof:
                if buffer.position < len(buffer.text):
                    raise ValueError("MOENV JSON member has data after the top-level array")
                buffer.read_more()
                buffer.skip_json_whitespace()
            if buffer.position < len(buffer.text):
                raise ValueError("MOENV JSON member has data after the top-level array")
    except UnicodeDecodeError as error:
        raise ValueError("MOENV JSON member must be valid UTF-8") from error

    if member.bytes_read != info.file_size or member.crc32 != info.CRC:
        raise ValueError("MOENV JSON ZIP metadata does not match the complete member bytes")
    return (
        member.sha256,
        member.md5,
        member.bytes_read,
        member.crc32,
        row_count,
        industry_group_row_count,
        candidate_row_count,
        grouped,
    )


def _safe_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    pure = PurePosixPath(name)
    if (
        not name
        or name != info.orig_filename
        or pure.is_absolute()
        or len(pure.parts) != 1
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in name
        or info.is_dir()
    ):
        raise ValueError(f"MOENV archive contains an unsafe member: {name!r}")
    unix_mode = info.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    if file_type and not stat.S_ISREG(unix_mode):
        raise ValueError(f"MOENV archive member must be a regular file: {name}")
    if info.flag_bits & 0x1:
        raise ValueError(f"MOENV archive member must not be encrypted: {name}")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise ValueError(f"MOENV archive member uses unsupported compression: {name}")
    if info.compress_size == 0 and info.file_size:
        raise ValueError(f"MOENV archive member has an invalid compressed size: {name}")
    if (
        info.compress_size
        and info.file_size > info.compress_size * _MAX_COMPRESSION_RATIO
    ):
        raise ValueError(f"MOENV archive member exceeds the compression-ratio limit: {name}")


def _validated_members(
    archive: zipfile.ZipFile,
) -> tuple[zipfile.ZipInfo, zipfile.ZipInfo]:
    infos = archive.infolist()
    for info in infos:
        _safe_member(info)
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValueError("MOENV archive contains duplicate member names")
    json_infos = [
        info for info in infos if PurePosixPath(info.filename).suffix == ".json"
    ]
    hash_infos = [info for info in infos if info.filename == MOENV_HASH_MEMBER]
    if len(infos) != 2 or len(json_infos) != 1 or len(hash_infos) != 1:
        raise ValueError(
            "MOENV archive must contain exactly one root JSON member and hash.txt"
        )
    json_info = json_infos[0]
    hash_info = hash_infos[0]
    if json_info.file_size > _MAX_JSON_BYTES:
        raise ValueError("MOENV JSON member exceeds the size limit")
    if hash_info.file_size > _MAX_HASH_BYTES:
        raise ValueError("MOENV hash.txt member exceeds the size limit")
    return json_info, hash_info


def _file_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _file_version(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


def _copy_and_hash_file(path: Path, destination) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("MOENV archive cannot be opened without symlink following")
    try:
        named_before = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(named_before.st_mode):
            raise ValueError(f"MOENV archive must be a regular file: {path}")
        descriptor = os.open(
            path,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
    except ValueError:
        raise
    except OSError as error:
        raise ValueError(f"MOENV archive must be a readable regular file: {path}") from error

    try:
        stream = os.fdopen(descriptor, "rb", closefd=True)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise

    with stream:
        opened_before = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened_before.st_mode):
            raise ValueError(f"MOENV archive must be a regular file: {path}")
        if (
            _file_identity(opened_before) != _file_identity(named_before)
            or _file_version(opened_before) != _file_version(named_before)
        ):
            raise ValueError("MOENV archive path changed while it was being opened")
        while chunk := stream.read(_READ_CHUNK):
            size += len(chunk)
            if size > _MAX_ARCHIVE_BYTES:
                raise ValueError("MOENV archive exceeds the size limit")
            digest.update(chunk)
            destination.write(chunk)
        opened_after = os.fstat(stream.fileno())
        try:
            named_after = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise ValueError("MOENV archive path changed while it was being read") from error

    if (
        _file_identity(opened_after) != _file_identity(opened_before)
        or _file_identity(named_after) != _file_identity(opened_before)
        or not stat.S_ISREG(named_after.st_mode)
    ):
        raise ValueError("MOENV archive path changed while it was being read")
    if (
        _file_version(opened_after) != _file_version(opened_before)
        or _file_version(named_after) != _file_version(opened_after)
        or size != opened_after.st_size
    ):
        raise ValueError("MOENV archive bytes changed while they were being read")
    return digest.hexdigest(), size


def _read_hash_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    json_member: str,
) -> tuple[str, int, int, str]:
    digest = hashlib.sha256()
    crc32 = 0
    size = 0
    chunks: list[bytes] = []
    with archive.open(info, "r") as source:
        while chunk := source.read(_READ_CHUNK):
            size += len(chunk)
            if size > info.file_size or size > _MAX_HASH_BYTES:
                raise ValueError("MOENV hash.txt exceeds its declared or allowed size")
            digest.update(chunk)
            crc32 = zlib.crc32(chunk, crc32)
            chunks.append(chunk)
    crc32 &= 0xFFFFFFFF
    if size != info.file_size or crc32 != info.CRC:
        raise ValueError("MOENV hash.txt ZIP metadata does not match its bytes")
    try:
        text = b"".join(chunks).decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("MOENV hash.txt must be valid UTF-8") from error
    match = _PUBLISHER_HASH_RE.fullmatch(text)
    if match is None:
        raise ValueError("MOENV hash.txt does not contain the expected MD5 declaration")
    if match.group("member") != json_member:
        raise ValueError("MOENV hash.txt names a different JSON member")
    return digest.hexdigest(), size, crc32, match.group("md5").lower()


def scan_moenv_archive(path: str | Path) -> MOENVArchiveScan:
    """Validate and completely scan one official archived EMS_S_01 ZIP."""

    archive_path = Path(path)
    try:
        with tempfile.TemporaryFile(mode="w+b") as bound_archive:
            archive_sha256, archive_bytes = _copy_and_hash_file(
                archive_path, bound_archive
            )
            bound_archive.flush()
            bound_archive.seek(0)
            with zipfile.ZipFile(bound_archive, "r", allowZip64=True) as archive:
                json_info, hash_info = _validated_members(archive)
                hash_sha256, hash_bytes, hash_crc32, publisher_md5 = _read_hash_member(
                    archive, hash_info, json_info.filename
                )
                (
                    json_sha256,
                    json_md5,
                    json_bytes,
                    json_crc32,
                    row_count,
                    industry_group_row_count,
                    candidate_row_count,
                    grouped,
                ) = _scan_json_member(archive, json_info)
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ValueError(f"MOENV archive is not a valid complete ZIP: {error}") from error

    if json_md5 != publisher_md5:
        raise ValueError(
            "MOENV JSON member does not match the publisher MD5 in hash.txt"
        )

    facilities: list[MOENVFacility] = []
    for emsno in sorted(grouped):
        variants = tuple(
            MOENVVariant(row=grouped[emsno][canonical], canonical_json=canonical)
            for canonical in sorted(grouped[emsno])
        )
        facilities.append(MOENVFacility(emsno=emsno, variants=variants))
    facility_tuple = tuple(facilities)
    candidate_count = sum(len(facility.variants) for facility in facility_tuple)
    current_regulation_count = sum(
        any(is_currently_regulated(variant.row) for variant in facility.variants)
        for facility in facility_tuple
    )
    valid_coordinate_count = sum(
        any(
            valid_taiwan_wgs84_point(variant.row) is not None
            for variant in facility.variants
        )
        for facility in facility_tuple
    )
    conflicting_facility_count = sum(
        len(facility.variants) > 1 for facility in facility_tuple
    )
    return MOENVArchiveScan(
        archive_sha256=archive_sha256,
        archive_bytes=archive_bytes,
        json_member=json_info.filename,
        json_sha256=json_sha256,
        json_md5=json_md5,
        json_bytes=json_bytes,
        json_crc32=json_crc32,
        json_compressed_bytes=json_info.compress_size,
        hash_member=MOENV_HASH_MEMBER,
        hash_member_sha256=hash_sha256,
        hash_member_bytes=hash_bytes,
        hash_member_crc32=hash_crc32,
        hash_member_compressed_bytes=hash_info.compress_size,
        publisher_md5=publisher_md5,
        row_count=row_count,
        industry_group_row_count=industry_group_row_count,
        candidate_row_count=candidate_row_count,
        candidate_count=candidate_count,
        facility_count=len(facility_tuple),
        current_regulation_count=current_regulation_count,
        valid_coordinate_count=valid_coordinate_count,
        conflicting_facility_count=conflicting_facility_count,
        facilities=facility_tuple,
    )


def canonical_candidate_jsonl_bytes(scan: MOENVArchiveScan) -> bytes:
    """Return stable exact-row JSONL sorted by emsno then canonical payload."""

    rows: list[bytes] = []
    for facility in sorted(scan.facilities, key=lambda item: item.emsno):
        for variant in sorted(
            facility.variants, key=lambda item: item.canonical_json
        ):
            rows.append(variant.canonical_json + b"\n")
    return b"".join(rows)


# Explicit compatibility alias for callers that include the dataset name.
scan_moenv_ems_archive = scan_moenv_archive


__all__ = [
    "MOENV_ATTRIBUTION",
    "MOENV_DATASET_URL",
    "MOENV_FIELDS",
    "MOENV_FILTER_VERSION",
    "MOENV_FLAG_FIELDS",
    "MOENV_HASH_MEMBER",
    "MOENV_INDUSTRY_CODES",
    "MOENV_INDUSTRY_GROUP",
    "MOENV_INDUSTRY_LABELS",
    "MOENV_LICENSE",
    "MOENV_LICENSE_URL",
    "MOENV_RELEASE_DATE_FIELDS",
    "MOENVArchiveScan",
    "MOENVFacility",
    "MOENVVariant",
    "canonical_candidate_jsonl_bytes",
    "is_currently_regulated",
    "scan_moenv_archive",
    "scan_moenv_ems_archive",
    "valid_taiwan_wgs84_point",
]
