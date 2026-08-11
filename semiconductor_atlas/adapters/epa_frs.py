"""Fail-closed parser for EPA FRS national semiconductor candidate snapshots.

The upstream EPA product is a large ZIP containing a flattened national CSV and
its documentation.  This module scans the archive without extracting it, retains
the exact logical CSV cells for qualifying rows, and produces a small,
deterministic JSONL derivative.  Industry codes are candidate signals only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import stat
import string
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


FRS_ARCHIVE_MEMBER = "NATIONAL_SINGLE.CSV"
FRS_DOCUMENTATION_MEMBER = "Facility State File Documentation 11132012_new.pdf"
FRS_EXPECTED_ARCHIVE_MEMBERS = frozenset(
    {FRS_ARCHIVE_MEMBER, FRS_DOCUMENTATION_MEMBER}
)
FRS_FILTER_VERSION = "epa-frs-semiconductor-direct-v1"
FRS_NAICS_CODES = ("334413",)
FRS_SIC_CODES = ("3674",)
FRS_RECORD_TYPE = "epa_frs_semiconductor_candidates"
FRS_RAW_RECORD_TYPE = "epa_frs_national_single_archive"
FRS_SCOPE = FRS_RECORD_TYPE
FRS_OFFICIAL_ARCHIVE_URL = (
    "https://ordsext.epa.gov/FLA/www3/state_files/national_single.zip"
)
FRS_LANDING_PAGE_URL = (
    "https://www.epa.gov/frs/epa-frs-facilities-state-single-file-csv-download"
)
FRS_DATA_AS_OF_SOURCE_URL = "https://www.epa.gov/frs/geospatial-data-download-service"
FRS_DATA_AS_OF_BASIS = "operator_supplied_from_official_epa_download_page"
FRS_RETRIEVAL_TIMESTAMP_BASES = frozenset(
    {
        "local_archive_transform_start_for_archived_bytes",
        "operator_supplied_for_archived_bytes",
        "upstream_download_completion",
    }
)
FRS_LICENSE = "U.S. Public Domain"
FRS_LICENSE_URL = "https://edg.epa.gov/epa_data_license.html"
FRS_ATTRIBUTION = (
    "Source: U.S. Environmental Protection Agency, Facility Registry Service "
    "(public-domain U.S. Government data; no EPA endorsement)"
)
FRS_COVERAGE = (
    "all_national_single_rows_matching_exact_naics_334413_or_sic_3674"
)

FRS_HEADERS = (
    "FRS_FACILITY_DETAIL_REPORT_URL",
    "REGISTRY_ID",
    "PRIMARY_NAME",
    "LOCATION_ADDRESS",
    "SUPPLEMENTAL_LOCATION",
    "CITY_NAME",
    "COUNTY_NAME",
    "FIPS_CODE",
    "STATE_CODE",
    "STATE_NAME",
    "COUNTRY_NAME",
    "POSTAL_CODE",
    "FEDERAL_FACILITY_CODE",
    "FEDERAL_AGENCY_NAME",
    "TRIBAL_LAND_CODE",
    "TRIBAL_LAND_NAME",
    "CONGRESSIONAL_DIST_NUM",
    "CENSUS_BLOCK_CODE",
    "HUC_CODE",
    "EPA_REGION_CODE",
    "SITE_TYPE_NAME",
    "LOCATION_DESCRIPTION",
    "CREATE_DATE",
    "UPDATE_DATE",
    "US_MEXICO_BORDER_IND",
    "PGM_SYS_ACRNMS",
    "INTEREST_TYPES",
    "NAICS_CODES",
    "NAICS_CODE_DESCRIPTIONS",
    "SIC_CODES",
    "SIC_CODE_DESCRIPTIONS",
    "LATITUDE83",
    "LONGITUDE83",
    "CONVEYOR",
    "COLLECT_DESC",
    "ACCURACY_VALUE",
    "REF_POINT_DESC",
    "HDATUM_DESC",
    "SOURCE_DESC",
)

_REGISTRY_ID_RE = re.compile(r"^[0-9]{12}$")
_MAX_ARCHIVE_BYTES = 1_000_000_000
_MAX_CSV_BYTES = 5_000_000_000
_MAX_DOCUMENTATION_BYTES = 25_000_000
_MAX_COMPRESSION_RATIO = 100
_READ_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class FRSCandidate:
    archive_member: str
    row_number: int
    row: dict[str, str]
    registry_id: str
    naics_codes: tuple[str, ...]
    sic_codes: tuple[str, ...]
    qualifying_naics_codes: tuple[str, ...]
    qualifying_sic_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FRSArchiveScan:
    archive_sha256: str
    archive_bytes: int
    csv_sha256: str
    csv_bytes: int
    csv_crc32: int
    row_count: int
    candidate_count: int
    candidates: tuple[FRSCandidate, ...]


class _HashingReader(io.RawIOBase):
    """Track the exact uncompressed member bytes consumed by TextIOWrapper."""

    def __init__(self, source: zipfile.ZipExtFile) -> None:
        super().__init__()
        self._source = source
        self._digest = hashlib.sha256()
        self._crc32 = 0
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:
        raw = self._source.read(len(buffer))
        if not raw:
            return 0
        size = len(raw)
        buffer[:size] = raw
        self._digest.update(raw)
        self._crc32 = zlib.crc32(raw, self._crc32)
        self.bytes_read += size
        return size

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()

    @property
    def crc32(self) -> int:
        return self._crc32 & 0xFFFFFFFF


def _code_tokens(value: str) -> tuple[str, ...]:
    return tuple(sorted({part.strip() for part in value.split(",") if part.strip()}))


def _candidate_from_row(row_number: int, row: Mapping[str, str]) -> FRSCandidate | None:
    naics_codes = _code_tokens(row["NAICS_CODES"])
    sic_codes = _code_tokens(row["SIC_CODES"])
    qualifying_naics = tuple(code for code in naics_codes if code in FRS_NAICS_CODES)
    qualifying_sic = tuple(code for code in sic_codes if code in FRS_SIC_CODES)
    if not qualifying_naics and not qualifying_sic:
        return None

    registry_id = row["REGISTRY_ID"]
    if not _REGISTRY_ID_RE.fullmatch(registry_id):
        raise ValueError(
            f"FRS candidate CSV row {row_number} has an invalid 12-digit REGISTRY_ID"
        )
    exact_row = {field: row[field] for field in FRS_HEADERS}
    return FRSCandidate(
        archive_member=FRS_ARCHIVE_MEMBER,
        row_number=row_number,
        row=exact_row,
        registry_id=registry_id,
        naics_codes=naics_codes,
        sic_codes=sic_codes,
        qualifying_naics_codes=qualifying_naics,
        qualifying_sic_codes=qualifying_sic,
    )


def _copy_and_hash_file(path: Path, destination) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_READ_CHUNK):
            digest.update(chunk)
            size += len(chunk)
            destination.write(chunk)
    return digest.hexdigest(), size


def _safe_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    pure = PurePosixPath(name)
    if (
        not name
        or name != info.orig_filename
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in name
        or info.is_dir()
    ):
        raise ValueError(f"EPA FRS archive contains an unsafe member: {name!r}")
    unix_mode = info.external_attr >> 16
    if unix_mode and stat.S_ISLNK(unix_mode):
        raise ValueError(f"EPA FRS archive member must not be a symlink: {name}")
    if info.flag_bits & 0x1:
        raise ValueError(f"EPA FRS archive member must not be encrypted: {name}")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise ValueError(f"EPA FRS archive member uses unsupported compression: {name}")
    if info.compress_size == 0 and info.file_size:
        raise ValueError(f"EPA FRS archive member has an invalid compressed size: {name}")
    if (
        info.compress_size
        and info.file_size > info.compress_size * _MAX_COMPRESSION_RATIO
    ):
        raise ValueError(f"EPA FRS archive member exceeds the compression-ratio limit: {name}")


def _validated_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    for info in infos:
        _safe_member(info)
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValueError("EPA FRS archive contains duplicate member names")
    actual = set(names)
    if actual != FRS_EXPECTED_ARCHIVE_MEMBERS:
        missing = sorted(FRS_EXPECTED_ARCHIVE_MEMBERS - actual)
        unexpected = sorted(actual - FRS_EXPECTED_ARCHIVE_MEMBERS)
        raise ValueError(
            "EPA FRS archive members do not match the expected product "
            f"(missing={missing!r}, unexpected={unexpected!r})"
        )
    members = {info.filename: info for info in infos}
    if members[FRS_ARCHIVE_MEMBER].file_size > _MAX_CSV_BYTES:
        raise ValueError("EPA FRS CSV member exceeds the size limit")
    if members[FRS_DOCUMENTATION_MEMBER].file_size > _MAX_DOCUMENTATION_BYTES:
        raise ValueError("EPA FRS documentation member exceeds the size limit")
    return members


def _consume_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> None:
    digest = hashlib.sha256()
    crc32 = 0
    size = 0
    with archive.open(info, "r") as stream:
        while chunk := stream.read(_READ_CHUNK):
            digest.update(chunk)
            crc32 = zlib.crc32(chunk, crc32)
            size += len(chunk)
    if size != info.file_size or (crc32 & 0xFFFFFFFF) != info.CRC:
        raise ValueError(f"EPA FRS archive member metadata does not match bytes: {info.filename}")


def _scan_csv(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> tuple[str, int, int, int, tuple[FRSCandidate, ...]]:
    candidates: list[FRSCandidate] = []
    candidate_ids: set[str] = set()
    row_count = 0
    source = archive.open(info, "r")
    hashing = _HashingReader(source)
    buffered = io.BufferedReader(hashing, buffer_size=_READ_CHUNK)
    text = io.TextIOWrapper(buffered, encoding="utf-8-sig", errors="strict", newline="")
    try:
        reader = csv.reader(text, strict=True)
        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError("EPA FRS CSV is empty") from error
        if tuple(header) != FRS_HEADERS:
            raise ValueError("EPA FRS CSV header does not match the expected 39-field schema")
        for row_number, values in enumerate(reader, start=2):
            if len(values) != len(FRS_HEADERS):
                raise ValueError(
                    f"EPA FRS CSV row {row_number} has {len(values)} fields; "
                    f"expected {len(FRS_HEADERS)}"
                )
            row_count += 1
            row = dict(zip(FRS_HEADERS, values, strict=True))
            candidate = _candidate_from_row(row_number, row)
            if candidate is None:
                continue
            if candidate.registry_id in candidate_ids:
                raise ValueError(
                    "EPA FRS candidate CSV contains duplicate REGISTRY_ID "
                    f"{candidate.registry_id}"
                )
            candidate_ids.add(candidate.registry_id)
            candidates.append(candidate)
    except UnicodeDecodeError as error:
        raise ValueError("EPA FRS CSV must be valid UTF-8") from error
    except csv.Error as error:
        raise ValueError(f"EPA FRS CSV is malformed: {error}") from error
    finally:
        text.close()

    if hashing.bytes_read != info.file_size or hashing.crc32 != info.CRC:
        raise ValueError("EPA FRS CSV ZIP metadata does not match the complete member bytes")
    candidates.sort(key=lambda item: item.registry_id)
    return (
        hashing.sha256,
        hashing.bytes_read,
        hashing.crc32,
        row_count,
        tuple(candidates),
    )


def scan_frs_archive(path: str | Path) -> FRSArchiveScan:
    """Validate and completely scan one official EPA FRS national ZIP archive."""

    archive_path = Path(path)
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError(f"EPA FRS archive must be a regular file: {archive_path}")
    try:
        # Bind the archive digest and semantic scan to one private immutable copy.
        # Hashing a path and reopening it as a ZIP would allow a rename swap between
        # those operations to pair one archive's digest with another's contents.
        with tempfile.TemporaryFile(mode="w+b") as bound_archive:
            archive_sha256, archive_bytes = _copy_and_hash_file(
                archive_path, bound_archive
            )
            if archive_bytes > _MAX_ARCHIVE_BYTES:
                raise ValueError("EPA FRS archive exceeds the size limit")
            bound_archive.flush()
            bound_archive.seek(0)
            with zipfile.ZipFile(bound_archive, "r", allowZip64=True) as archive:
                members = _validated_members(archive)
                csv_sha256, csv_bytes, csv_crc32, row_count, candidates = _scan_csv(
                    archive, members[FRS_ARCHIVE_MEMBER]
                )
                _consume_member(archive, members[FRS_DOCUMENTATION_MEMBER])
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ValueError(f"EPA FRS archive is not a valid complete ZIP: {error}") from error
    return FRSArchiveScan(
        archive_sha256=archive_sha256,
        archive_bytes=archive_bytes,
        csv_sha256=csv_sha256,
        csv_bytes=csv_bytes,
        csv_crc32=csv_crc32,
        row_count=row_count,
        candidate_count=len(candidates),
        candidates=candidates,
    )


def candidate_jsonl_bytes(scan: FRSArchiveScan) -> bytes:
    """Serialize candidates in stable Registry-ID order using only source fields."""

    rows = []
    for candidate in sorted(scan.candidates, key=lambda item: item.registry_id):
        payload = {
            "archive_member": candidate.archive_member,
            "row_number": candidate.row_number,
            "row": {field: candidate.row[field] for field in FRS_HEADERS},
        }
        rows.append(
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
    return "".join(rows).encode("utf-8")


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"candidate JSON contains duplicate key {key!r}")
        value[key] = item
    return value


def _json_constant(value: str) -> None:
    raise ValueError(f"candidate JSON contains non-finite value {value}")


def _parse_candidate_lines(stream) -> tuple[FRSCandidate, ...]:
    candidates: list[FRSCandidate] = []
    seen_ids: set[str] = set()
    seen_rows: set[int] = set()
    for line_number, line in enumerate(stream, start=1):
        if not line.strip():
            raise ValueError(
                f"EPA FRS candidate JSONL contains a blank line at {line_number}"
            )
        try:
            payload = json.loads(
                line,
                object_pairs_hook=_json_object,
                parse_constant=_json_constant,
            )
        except json.JSONDecodeError as error:
            raise ValueError(
                f"EPA FRS candidate JSONL line {line_number} is invalid JSON"
            ) from error
        if not isinstance(payload, dict) or set(payload) != {
            "archive_member",
            "row_number",
            "row",
        }:
            raise ValueError(
                f"EPA FRS candidate JSONL line {line_number} has an invalid shape"
            )
        if payload["archive_member"] != FRS_ARCHIVE_MEMBER:
            raise ValueError(
                f"EPA FRS candidate JSONL line {line_number} has the wrong archive member"
            )
        row_number = payload["row_number"]
        if isinstance(row_number, bool) or not isinstance(row_number, int) or row_number < 2:
            raise ValueError(
                f"EPA FRS candidate JSONL line {line_number} has an invalid row_number"
            )
        if row_number in seen_rows:
            raise ValueError(
                f"EPA FRS candidate JSONL repeats CSV row_number {row_number}"
            )
        seen_rows.add(row_number)
        row = payload["row"]
        if not isinstance(row, dict) or tuple(row) != FRS_HEADERS:
            raise ValueError(
                f"EPA FRS candidate JSONL line {line_number} lacks the exact 39-field row"
            )
        if any(not isinstance(row[field], str) for field in FRS_HEADERS):
            raise ValueError(
                f"EPA FRS candidate JSONL line {line_number} contains a non-string field"
            )
        candidate = _candidate_from_row(row_number, row)
        if candidate is None:
            raise ValueError(
                f"EPA FRS candidate JSONL line {line_number} does not match "
                f"filter {FRS_FILTER_VERSION}"
            )
        if candidate.registry_id in seen_ids:
            raise ValueError(
                "EPA FRS candidate JSONL contains duplicate REGISTRY_ID "
                f"{candidate.registry_id}"
            )
        seen_ids.add(candidate.registry_id)
        candidates.append(candidate)
    registry_ids = [candidate.registry_id for candidate in candidates]
    if registry_ids != sorted(registry_ids):
        raise ValueError("EPA FRS candidate JSONL must be sorted by REGISTRY_ID")
    return tuple(candidates)


def parse_candidate_jsonl_bytes(raw: bytes) -> tuple[FRSCandidate, ...]:
    """Parse candidates from the exact in-memory bytes hashed by an importer."""

    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("EPA FRS candidate JSONL must be valid UTF-8") from error
    return _parse_candidate_lines(io.StringIO(text, newline=""))


def parse_candidate_jsonl(path: str | Path) -> tuple[FRSCandidate, ...]:
    """Parse and validate the deterministic filtered derivative used by ingestion."""

    candidate_path = Path(path)
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise ValueError(f"EPA FRS candidate input must be a regular file: {candidate_path}")
    try:
        raw = candidate_path.read_bytes()
    except OSError as error:
        raise ValueError(f"EPA FRS candidate input is not readable: {candidate_path}") from error
    return parse_candidate_jsonl_bytes(raw)


def is_sha256(value: object) -> bool:
    """Small shared predicate used by acquisition tests and downstream verification."""

    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in string.hexdigits for character in value)
        and value == value.lower()
    )


__all__ = [
    "FRS_ARCHIVE_MEMBER",
    "FRS_ATTRIBUTION",
    "FRS_COVERAGE",
    "FRS_DATA_AS_OF_BASIS",
    "FRS_DATA_AS_OF_SOURCE_URL",
    "FRS_DOCUMENTATION_MEMBER",
    "FRS_EXPECTED_ARCHIVE_MEMBERS",
    "FRS_FILTER_VERSION",
    "FRS_HEADERS",
    "FRS_LANDING_PAGE_URL",
    "FRS_LICENSE",
    "FRS_LICENSE_URL",
    "FRS_NAICS_CODES",
    "FRS_OFFICIAL_ARCHIVE_URL",
    "FRS_RECORD_TYPE",
    "FRS_RAW_RECORD_TYPE",
    "FRS_RETRIEVAL_TIMESTAMP_BASES",
    "FRS_SCOPE",
    "FRS_SIC_CODES",
    "FRSArchiveScan",
    "FRSCandidate",
    "candidate_jsonl_bytes",
    "is_sha256",
    "parse_candidate_jsonl",
    "parse_candidate_jsonl_bytes",
    "scan_frs_archive",
]
