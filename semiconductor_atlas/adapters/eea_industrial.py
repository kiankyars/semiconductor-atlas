"""Fail-closed EEA Industrial Reporting v16 candidate extraction.

The EEA release is an Access database.  A separately pinned extractor produces
one CSV per explicitly allowed table with a sentinel that preserves Access NULL
separately from an empty string.  This module never executes database queries;
it validates those exports, audits the full reported facility population, and
emits a small deterministic JSONL lead set.

Candidate membership is deliberately not a statement that a facility makes
semiconductors.  NACE 26.11 is broad and includes many adjacent electronic
components, while name terms are only discovery signals.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping


EEA_RECORD_TYPE = "eea_industrial_v16_semiconductor_candidates"
EEA_FILTER_VERSION = "eea-industrial-semiconductor-candidates-v1"
EEA_NACE_CODE = "26.11"
EEA_NULL_SENTINEL = "__SEMICONDUCTOR_ATLAS_MDB_NULL_48c374a5__"
EEA_DATASET_URL = "https://industry.eea.europa.eu/industrial-emissions/dataset"
EEA_DOI_URL = "https://doi.org/10.2909/657ac3cb-affa-4295-a4a9-27b4f539adab"
EEA_EDITION = "16.00"
EEA_DATASET_ID = "eea_t_ied-eprtr_p_2007-2024_v16_r00"
EEA_LICENSE = "Creative Commons Attribution 4.0 International"
EEA_LICENSE_SPDX = "CC-BY-4.0"
EEA_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
EEA_ATTRIBUTION = (
    "Source: European Environment Agency (EEA), Industrial reporting under "
    "Directive 2010/75/EU and Regulation (EC) No 166/2006, version 16.00, "
    "published 20 February 2026, DOI "
    "10.2909/657ac3cb-affa-4295-a4a9-27b4f539adab. Licensed under CC BY 4.0. "
    "Semiconductor Atlas filtered and transformed the source; changes were made."
)
EEA_V16_ACCDB_SHA256 = (
    "804bdcab04a31a4c9fb7ffa7e4affde6253f44d4617b8eadd6a20a90648c28d7"
)
EEA_V16_ACCDB_BYTES = 2_031_214_592

EEA_METADATA_TABLE = "0a_DataCollectionMetadata_EUReg"
EEA_EPRTR_METADATA_TABLE = "0b_DataCollectionMetadata_EPRTR_LCP"
EEA_SITE_TABLE = "1_ProductionSite"
EEA_FACILITY_TABLE = "2_ProductionFacility"
EEA_FACILITY_DETAILS_TABLE = "2a_ProductionFacilityDetails"
EEA_FUNCTION_TABLE = "2c_Function"
EEA_PRODUCTION_VOLUME_TABLE = "2e_ProductionVolume"
EEA_REQUIRED_TABLES = (
    EEA_METADATA_TABLE,
    EEA_EPRTR_METADATA_TABLE,
    EEA_SITE_TABLE,
    EEA_FACILITY_TABLE,
    EEA_FACILITY_DETAILS_TABLE,
    EEA_FUNCTION_TABLE,
    EEA_PRODUCTION_VOLUME_TABLE,
)

EEA_TABLE_HEADERS: dict[str, tuple[str, ...]] = {
    EEA_METADATA_TABLE: (
        "fileId",
        "envelopeUrl",
        "filename",
        "dateSubmitted",
        "dateReleased",
        "dateImported",
        "fileSHA256Hash",
        "countryCode",
        "reportingYear",
        "obligation",
    ),
    EEA_EPRTR_METADATA_TABLE: (
        "fileId",
        "envelopeUrl",
        "filename",
        "dateSubmitted",
        "dateReleased",
        "dateImported",
        "fileSHA256Hash",
        "countryCode",
        "reportingYear",
        "obligation",
    ),
    EEA_SITE_TABLE: (
        "fileId_EUReg",
        "Site_INSPIRE_ID",
        "ProductionSite_thematicId",
        "ProductionSite_thematicIdScheme",
        "pointGeometryLat",
        "pointGeometryLon",
        "nameOfFeature",
        "countryCode",
    ),
    EEA_FACILITY_TABLE: (
        "fileId_EUReg",
        "Parent_Site_INSPIRE_ID",
        "Facility_INSPIRE_ID",
        "ProductionFacility_thematicId",
        "ProductionFacility_thematicIdScheme",
        "parentCompanyName",
        "parentCompany_confidentialityReasonCode",
        "parentCompany_confidentialityReasonName",
        "nameOfFeature",
        "facilityName_confidentialityReasonCode",
        "facilityName_confidentialityReasonName",
        "facilityType",
        "pointGeometryLat",
        "pointGeometryLon",
        "streetName",
        "buildingNumber",
        "city",
        "countryCode",
        "addressDetails_confidentialityReasonCode",
        "addressDetails_confidentialityReasonName",
        "mainActivityCode",
        "mainActivityName",
        "dateOfStartOfOperation",
        "RBDSourceCode",
        "RBDSourceName",
        "NUTSRegionSourceCode",
        "NUTSRegionSourceName",
        "parentCompanyURL",
        "postalCode",
    ),
    EEA_FACILITY_DETAILS_TABLE: (
        "fileId_EUReg",
        "fileId_EPRTR_LCP",
        "ProductionFacilityDetailsID",
        "Facility_INSPIRE_ID",
        "reportingYear",
        "status",
        "remarks",
        "numberOfOperatingHours",
        "numberOfEmployees",
        "stackHeightClass",
        "representativeStackHeightM",
        "confidentialityReasonCode",
        "confidentialityReasonName",
    ),
    EEA_FUNCTION_TABLE: (
        "FunctionId",
        "Facility_INSPIRE_ID",
        "NACEMainEconomicActivityCode",
        "NACEMainEconomicActivityName",
    ),
    EEA_PRODUCTION_VOLUME_TABLE: (
        "fileId_EPRTR_LCP",
        "ProductionVolumeTypeId",
        "Facility_INSPIRE_ID",
        "reportingYear",
        "productName",
        "productionVolume",
        "productionVolumeUnits",
    ),
}

# Terms are intentionally explicit and frozen.  Generic words such as
# ``electronics``, ``silicon``, ``chemical``, ``materials``, ``gas``, ``fab``,
# and ``chip`` never qualify alone.
_SEMICONDUCTOR_TERMS = (
    "semiconductor",
    "semiconductors",
    "semi conductor",
    "semi conductors",
    "semiconducteur",
    "semiconducteurs",
    "semi conducteur",
    "semi conducteurs",
    "semiconduttore",
    "semiconduttori",
    "halbleiter",
    "halfgeleider",
    "halfgeleiders",
    "halvleder",
    "halvledere",
    "puolijohde",
    "puolijohteet",
    "półprzewodnik",
    "półprzewodniki",
    "polovodič",
    "polovodiče",
    "félvezető",
    "semiconductori",
    "ημιαγωγός",
    "ημιαγωγοί",
    "полупроводник",
    "полупроводници",
    "puslaidininkis",
    "puslaidininkiai",
    "pusvadītājs",
    "pusvadītāji",
    "pooljuht",
    "pooljuhid",
    "microelectronics",
    "microelectronic",
    "micro electronics",
    "micro electronic",
    "microélectronique",
    "microelectronique",
    "mikroelektronik",
    "microelettronica",
    "microelectrónica",
    "microelectronica",
    "stmicroelectronics",
    "integrated circuit",
    "integrated circuits",
)
_SEMICONDUCTOR_MATERIAL_TERMS = (
    "semiconductor material",
    "semiconductor materials",
    "semiconductor wafer",
    "semiconductor wafers",
    "silicon wafer",
    "silicon wafers",
    "křemíkových desek",
    "electronic grade silicon",
    "compound semiconductor",
    "compound semiconductors",
    "epitaxial wafer",
    "epitaxial wafers",
    "photomask",
    "photomasks",
    "photo mask",
    "photo masks",
    "mask blank",
    "mask blanks",
    "photoresist",
    "photoresists",
    "cmp slurry",
    "cmp slurries",
    "wafer fabrication",
    "wafer production",
    "wafer plant",
    "wafer factory",
    "wafer foundry",
    "mems foundry",
)
EEA_NAME_TERM_RULES = tuple(
    {"category": category, "term": term}
    for category, terms in (
        ("explicit_semiconductor_name_candidate", _SEMICONDUCTOR_TERMS),
        (
            "explicit_semiconductor_material_name_candidate",
            _SEMICONDUCTOR_MATERIAL_TERMS,
        ),
    )
    for term in terms
)
EEA_NAME_TERM_LEXICON_SHA256 = hashlib.sha256(
    json.dumps(
        EEA_NAME_TERM_RULES,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()

_READ_CHUNK_BYTES = 1024 * 1024
_MAX_TABLE_BYTES = 2_000_000_000
_MAX_TABLE_ROWS = 20_000_000
_MAX_FIELD_CHARS = 16 * 1024 * 1024
_INTEGER_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_PUBLISHER_MAPPED_FACILITY_ID_RE = re.compile(r"^[A-Z]{2}\.EEA/")


@dataclass(frozen=True, slots=True)
class EEATableSummary:
    table: str
    filename: str
    sha256: str
    bytes: int
    row_count: int
    headers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EEAV16TableContract:
    table: str
    sha256: str
    bytes: int
    row_count: int


EEA_V16_TABLE_CONTRACT = (
    EEAV16TableContract(
        EEA_METADATA_TABLE,
        "95287699a54ba943004ef4a5cd69ceb0c7bdf0441c18454d5c77f84cb74ab2ad",
        161_145,
        663,
    ),
    EEAV16TableContract(
        EEA_EPRTR_METADATA_TABLE,
        "92fbf26b4cc14dfecc884428a29b404f469d0c5599903afbad4b9393cace0005",
        148_462,
        609,
    ),
    EEAV16TableContract(
        EEA_SITE_TABLE,
        "dd67c9b80125dd865d187f3bd9eab8821530ad9157418022f5001771226f096c",
        14_899_911,
        97_758,
    ),
    EEAV16TableContract(
        EEA_FACILITY_TABLE,
        "de8dc0dfd8a1a8580e6a783d8fcf5fd6e91085509d2e2625315a2c8d0fd2622f",
        76_309_804,
        99_275,
    ),
    EEAV16TableContract(
        EEA_FACILITY_DETAILS_TABLE,
        "1b769e18f2d39a9f636809f4d975712b2861cfa4024e592054d6af714d8caf96",
        336_975_799,
        873_090,
    ),
    EEAV16TableContract(
        EEA_FUNCTION_TABLE,
        "020b89053f62132bcbca93faefe75ecb8b22fd241919267db1ef6abee3a254d4",
        8_352_279,
        97_403,
    ),
    EEAV16TableContract(
        EEA_PRODUCTION_VOLUME_TABLE,
        "459d9c36d855dcd8bf47c4ef97cc1e43c3a66f8466ef4aaeca5c7242260ec73c",
        125,
        0,
    ),
)
EEA_V16_TABLE_CONTRACT_SHA256 = hashlib.sha256(
    json.dumps(
        [
            {
                "bytes": item.bytes,
                "row_count": item.row_count,
                "sha256": item.sha256,
                "table": item.table,
            }
            for item in EEA_V16_TABLE_CONTRACT
        ],
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()


@dataclass(frozen=True, slots=True)
class EEAIndustrialExportScan:
    root: Path
    table_summaries: tuple[EEATableSummary, ...]
    metadata_row_count: int
    eprtr_metadata_row_count: int
    site_row_count: int
    facility_row_count: int
    distinct_facility_count: int
    facility_detail_row_count: int
    function_row_count: int
    production_volume_row_count: int
    facilities_with_function_count: int
    nace_26_11_row_count: int
    nace_26_11_facility_count: int
    facility_name_candidate_count: int
    site_name_candidate_count: int
    candidate_count: int
    candidate_reason_counts: tuple[tuple[str, int], ...]
    candidate_country_counts: tuple[tuple[str, int], ...]
    facility_country_counts: tuple[tuple[str, int], ...]
    facility_reporting_year_counts: tuple[tuple[str, int], ...]
    candidate_reporting_year_counts: tuple[tuple[str, int], ...]
    candidate_latest_detail_reporting_year_counts: tuple[tuple[str, int], ...]
    missing_parent_site_count: int
    facility_without_function_count: int
    missing_or_invalid_coordinate_count: int
    confidential_facility_name_count: int
    confidential_parent_company_count: int
    confidential_address_count: int
    confidential_detail_row_count: int
    orphan_function_count: int
    orphan_detail_count: int
    detail_exact_fk_drift_count: int
    detail_case_only_fk_drift_count: int
    detail_whitespace_only_fk_drift_count: int
    detail_unresolved_fk_drift_count: int
    publisher_mapped_facility_id_count: int
    publisher_mapped_candidate_id_count: int
    candidates: tuple[dict[str, Any], ...]


class _HashingReader(io.RawIOBase):
    def __init__(self, source: io.BufferedReader, maximum_bytes: int) -> None:
        super().__init__()
        self._source = source
        self._maximum_bytes = maximum_bytes
        self._digest = hashlib.sha256()
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:
        raw = self._source.read(len(buffer))
        if not raw:
            return 0
        self.bytes_read += len(raw)
        if self.bytes_read > self._maximum_bytes:
            raise ValueError("EEA table export exceeds the byte limit")
        self._digest.update(raw)
        buffer[: len(raw)] = raw
        return len(raw)

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _projected_row(values: Mapping[str, str | None], redacted: Iterable[str] = ()) -> dict[str, Any]:
    projected = dict(values)
    result: dict[str, Any] = {
        "projected_values_sha256": hashlib.sha256(
            _canonical_json_bytes(projected)
        ).hexdigest(),
        "values": projected,
    }
    redacted_fields = sorted(set(redacted))
    if redacted_fields:
        result["redacted_fields"] = redacted_fields
    return result


def _required_identifier(value: str | None, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value.encode("utf-8")) > 4096
    ):
        raise ValueError(f"{context} must be a non-empty exact source identifier")
    return value


def _integer_text(value: str | None, context: str) -> str:
    if not isinstance(value, str) or not _INTEGER_RE.fullmatch(value):
        raise ValueError(f"{context} must be a canonical non-negative integer")
    return value


def _decoded_row(headers: tuple[str, ...], row: list[str], context: str) -> dict[str, str | None]:
    if len(row) != len(headers):
        raise ValueError(
            f"{context} has {len(row)} columns; expected {len(headers)}"
        )
    values: dict[str, str | None] = {}
    for header, raw in zip(headers, row, strict=True):
        if "\x00" in raw:
            raise ValueError(f"{context} field {header!r} contains a NUL byte")
        values[header] = None if raw == EEA_NULL_SENTINEL else raw
    return values


def _read_table(
    root_descriptor: int, table: str
) -> tuple[list[dict[str, str | None]], EEATableSummary]:
    expected_headers = EEA_TABLE_HEADERS[table]
    filename = f"{table}.csv"
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("EEA exports cannot be opened without symlink protection")
    try:
        named_before = os.stat(
            filename,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(named_before.st_mode):
            raise ValueError(f"EEA table export must be a regular file: {filename}")
        if not 0 < named_before.st_size <= _MAX_TABLE_BYTES:
            raise ValueError(f"EEA table export has an invalid size: {filename}")
        descriptor = os.open(
            filename,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_descriptor,
        )
    except ValueError:
        raise
    except OSError as error:
        raise ValueError(f"EEA table export is missing or unsafe: {filename}") from error

    rows: list[dict[str, str | None]] = []
    binary: io.BufferedReader | None = None
    hashing: _HashingReader | None = None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(
            named_before
        ):
            raise ValueError(f"EEA table export changed while opening: {filename}")
        binary = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        hashing = _HashingReader(binary, _MAX_TABLE_BYTES)
        buffered = io.BufferedReader(hashing, buffer_size=_READ_CHUNK_BYTES)
        text = io.TextIOWrapper(buffered, encoding="utf-8", errors="strict", newline="")
        prior_limit = csv.field_size_limit()
        csv.field_size_limit(_MAX_FIELD_CHARS)
        try:
            reader = csv.reader(text, dialect="excel", strict=True)
            try:
                raw_headers = next(reader)
            except StopIteration as error:
                raise ValueError(f"EEA table export is empty: {filename}") from error
            headers = tuple(raw_headers)
            if headers != expected_headers:
                raise ValueError(
                    f"EEA table {table} headers drifted: {headers!r}"
                )
            if len(headers) != len(set(headers)):
                raise ValueError(f"EEA table {table} has duplicate headers")
            for row_number, raw_row in enumerate(reader, start=2):
                if len(rows) >= _MAX_TABLE_ROWS:
                    raise ValueError(f"EEA table {table} exceeds the row limit")
                rows.append(
                    _decoded_row(
                        headers,
                        raw_row,
                        f"EEA table {table} row {row_number}",
                    )
                )
            # Force TextIOWrapper and the hashing reader through EOF.
            text.read()
        finally:
            csv.field_size_limit(prior_limit)
        after = os.fstat(binary.fileno())
        try:
            named_after = os.stat(
                filename,
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            raise ValueError(f"EEA table export changed while reading: {filename}") from error
        if (
            _identity(opened) != _identity(after)
            or _identity(after) != _identity(named_after)
            or hashing.bytes_read != opened.st_size
        ):
            raise ValueError(f"EEA table export changed while reading: {filename}")
        summary = EEATableSummary(
            table=table,
            filename=filename,
            sha256=hashing.sha256,
            bytes=hashing.bytes_read,
            row_count=len(rows),
            headers=headers,
        )
        return rows, summary
    except UnicodeDecodeError as error:
        raise ValueError(f"EEA table {table} is not valid UTF-8") from error
    except csv.Error as error:
        raise ValueError(f"EEA table {table} is not valid CSV: {error}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if binary is not None:
            binary.close()


def _normalized_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


_NORMALIZED_NAME_TERM_RULES = tuple(
    (rule["category"], rule["term"], _normalized_name(rule["term"]))
    for rule in EEA_NAME_TERM_RULES
)


def _name_term_matches(value: str | None) -> tuple[tuple[str, str], ...]:
    if value is None or not value:
        return ()
    normalized = f" {_normalized_name(value)} "
    matches = {
        (category, term)
        for category, term, normalized_term in _NORMALIZED_NAME_TERM_RULES
        if f" {normalized_term} " in normalized
    }
    return tuple(sorted(matches))


def _is_confidential(*markers: str | None) -> bool:
    # A non-empty reason name is independently sufficient.  Whitespace is also
    # a non-empty source marker and must fail closed rather than disclose a field
    # merely because a malformed code was blank or absent.
    return any(marker is not None and marker != "" for marker in markers)


def _coordinate_missing_or_invalid(row: Mapping[str, str | None]) -> bool:
    latitude = row["pointGeometryLat"]
    longitude = row["pointGeometryLon"]
    if latitude in {None, ""} or longitude in {None, ""}:
        return True
    try:
        parsed_latitude = Decimal(latitude)
        parsed_longitude = Decimal(longitude)
    except InvalidOperation:
        return True
    in_range = (
        parsed_latitude.is_finite()
        and parsed_longitude.is_finite()
        and Decimal("-90") <= parsed_latitude <= Decimal("90")
        and Decimal("-180") <= parsed_longitude <= Decimal("180")
    )
    return not in_range or (
        parsed_latitude == Decimal(0) and parsed_longitude == Decimal(0)
    )


def _metadata_projection(row: Mapping[str, str | None]) -> dict[str, Any]:
    # Envelope URLs and their dummy historical UUID substitutes are unnecessary
    # in the public derivative.  The retained raw export remains authoritative.
    fields = (
        "fileId",
        "filename",
        "dateSubmitted",
        "dateReleased",
        "dateImported",
        "fileSHA256Hash",
        "countryCode",
        "reportingYear",
        "obligation",
    )
    return _projected_row({field: row[field] for field in fields}, ("envelopeUrl",))


def _site_projection(row: Mapping[str, str | None]) -> dict[str, Any]:
    return _projected_row(row)


def _facility_projection(row: Mapping[str, str | None]) -> dict[str, Any]:
    omitted = {
        "parentCompanyURL",
        "parentCompany_confidentialityReasonName",
        "facilityName_confidentialityReasonName",
        "addressDetails_confidentialityReasonName",
    }
    values = {field: value for field, value in row.items() if field not in omitted}
    redacted = set(omitted)
    if _is_confidential(
        row["parentCompany_confidentialityReasonCode"],
        row["parentCompany_confidentialityReasonName"],
    ):
        values["parentCompanyName"] = None
        redacted.add("parentCompanyName")
    if _is_confidential(
        row["facilityName_confidentialityReasonCode"],
        row["facilityName_confidentialityReasonName"],
    ):
        values["nameOfFeature"] = None
        redacted.add("nameOfFeature")
    if _is_confidential(
        row["addressDetails_confidentialityReasonCode"],
        row["addressDetails_confidentialityReasonName"],
    ):
        for field in ("streetName", "buildingNumber", "city", "postalCode"):
            values[field] = None
            redacted.add(field)
    return _projected_row(values, redacted)


def _detail_projection(row: Mapping[str, str | None]) -> dict[str, Any]:
    omitted = {"remarks", "confidentialityReasonName"}
    values = {field: value for field, value in row.items() if field not in omitted}
    redacted = set(omitted)
    if _is_confidential(
        row["confidentialityReasonCode"], row["confidentialityReasonName"]
    ):
        for field in (
            "status",
            "numberOfOperatingHours",
            "numberOfEmployees",
            "stackHeightClass",
            "representativeStackHeightM",
        ):
            values[field] = None
            redacted.add(field)
    return _projected_row(values, redacted)


def _function_projection(row: Mapping[str, str | None]) -> dict[str, Any]:
    return _projected_row(row)


def _counter_tuple(counter: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counter.items()))


def _metadata_index(
    rows: Iterable[dict[str, str | None]], *, table: str
) -> dict[str, dict[str, str | None]]:
    indexed: dict[str, dict[str, str | None]] = {}
    for row in rows:
        file_id = _integer_text(row["fileId"], f"EEA metadata {table} fileId")
        if file_id in indexed:
            raise ValueError(
                f"EEA metadata {table} has duplicate fileId {file_id!r}"
            )
        year = _integer_text(
            row["reportingYear"],
            f"EEA metadata {table} {file_id} reportingYear",
        )
        if not 2000 <= int(year) <= 2100:
            raise ValueError(
                f"EEA metadata {table} {file_id} reportingYear is out of range"
            )
        indexed[file_id] = row
    return indexed


def _metadata_reference(
    value: str | None,
    *,
    index: Mapping[str, dict[str, str | None]],
    context: str,
) -> str | None:
    if value is None:
        return None
    file_id = _integer_text(value, context)
    if file_id not in index:
        raise ValueError(f"{context} references missing metadata {file_id!r}")
    return file_id


def _annotated_metadata_projection(
    row: Mapping[str, str | None], *, source_table: str
) -> dict[str, Any]:
    projection = _metadata_projection(row)
    projection["source_table"] = source_table
    return projection


def _reason(
    *,
    kind: str,
    source_table: str,
    source_row: dict[str, Any],
    source_field: str,
    raw_value: str,
    matched_terms: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": kind,
        "raw_value": raw_value,
        "source_field": source_field,
        "source_projected_values_sha256": source_row["projected_values_sha256"],
        "source_table": source_table,
    }
    if matched_terms:
        result["matched_terms"] = [
            {"category": category, "term": term}
            for category, term in matched_terms
        ]
    return result


def scan_eea_industrial_exports(root: str | Path) -> EEAIndustrialExportScan:
    """Structurally validate reviewed exports and build deterministic leads.

    This generic scanner is suitable for tests and source-shape inspection.  A
    production v16 acceptance must additionally use
    :func:`scan_eea_industrial_v16_exports`, which binds every input table to the
    exact reviewed release hashes and row counts.
    """

    export_root = Path(root).absolute()
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory_flag is None:
        raise ValueError("EEA exports require directory and symlink-safe opens")
    root_descriptor = -1
    try:
        root_descriptor = os.open(
            export_root,
            os.O_RDONLY
            | directory_flag
            | nofollow
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened_root = os.fstat(root_descriptor)
        named_root = os.stat(export_root, follow_symlinks=False)
    except OSError as error:
        if root_descriptor >= 0:
            os.close(root_descriptor)
        raise ValueError(
            f"EEA export root is missing or unsafe: {export_root}"
        ) from error
    if (
        not stat.S_ISDIR(opened_root.st_mode)
        or not stat.S_ISDIR(named_root.st_mode)
        or _identity(opened_root) != _identity(named_root)
    ):
        os.close(root_descriptor)
        raise ValueError("EEA export root must be a stable non-symlink directory")

    try:
        expected_files = {f"{table}.csv" for table in EEA_REQUIRED_TABLES}
        actual_files: set[str] = set()
        with os.scandir(root_descriptor) as entries:
            for entry in entries:
                details = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(details.st_mode):
                    raise ValueError(
                        "EEA export root contains a non-regular entry: "
                        f"{entry.name}"
                    )
                actual_files.add(entry.name)
        if actual_files != expected_files:
            raise ValueError(
                "EEA export root has extra or missing files "
                f"(expected={sorted(expected_files)!r}, actual={sorted(actual_files)!r})"
            )

        table_rows: dict[str, list[dict[str, str | None]]] = {}
        summaries: list[EEATableSummary] = []
        for table in EEA_REQUIRED_TABLES:
            rows, summary = _read_table(root_descriptor, table)
            table_rows[table] = rows
            summaries.append(summary)

        opened_root_after = os.fstat(root_descriptor)
        try:
            named_root_after = os.stat(export_root, follow_symlinks=False)
        except OSError as error:
            raise ValueError("EEA export root changed while reading") from error
        if (
            _identity(opened_root) != _identity(opened_root_after)
            or _identity(opened_root_after) != _identity(named_root_after)
        ):
            raise ValueError("EEA export root changed while reading")
    finally:
        os.close(root_descriptor)

    metadata = _metadata_index(
        table_rows[EEA_METADATA_TABLE], table=EEA_METADATA_TABLE
    )
    eprtr_metadata = _metadata_index(
        table_rows[EEA_EPRTR_METADATA_TABLE], table=EEA_EPRTR_METADATA_TABLE
    )

    sites: dict[str, dict[str, str | None]] = {}
    site_name_matches: dict[str, tuple[tuple[str, str], ...]] = {}
    for row in table_rows[EEA_SITE_TABLE]:
        site_id = _required_identifier(row["Site_INSPIRE_ID"], "EEA Site_INSPIRE_ID")
        if site_id in sites:
            raise ValueError(f"EEA sites have duplicate Site_INSPIRE_ID {site_id!r}")
        file_id = _integer_text(row["fileId_EUReg"], f"EEA site {site_id} fileId_EUReg")
        if file_id not in metadata:
            raise ValueError(f"EEA site {site_id!r} references missing metadata {file_id!r}")
        sites[site_id] = row
        matches = _name_term_matches(row["nameOfFeature"])
        if matches:
            site_name_matches[site_id] = matches

    function_ids: set[str] = set()
    functions_by_facility: dict[str, list[dict[str, str | None]]] = defaultdict(list)
    nace_candidate_ids: set[str] = set()
    nace_26_11_rows = 0
    for row in table_rows[EEA_FUNCTION_TABLE]:
        function_id = _integer_text(row["FunctionId"], "EEA FunctionId")
        if function_id in function_ids:
            raise ValueError(f"EEA functions have duplicate FunctionId {function_id!r}")
        function_ids.add(function_id)
        facility_id = _required_identifier(
            row["Facility_INSPIRE_ID"], "EEA function Facility_INSPIRE_ID"
        )
        functions_by_facility[facility_id].append(row)
        raw_code = row["NACEMainEconomicActivityCode"]
        if raw_code == EEA_NACE_CODE:
            nace_26_11_rows += 1
            nace_candidate_ids.add(facility_id)

    facilities: dict[str, dict[str, str | None]] = {}
    facility_name_matches: dict[str, tuple[tuple[str, str], ...]] = {}
    facility_country_counts: Counter[str] = Counter()
    facility_reporting_year_counts: Counter[str] = Counter()
    missing_parent_site_count = 0
    missing_or_invalid_coordinate_count = 0
    confidential_facility_name_count = 0
    confidential_parent_company_count = 0
    confidential_address_count = 0
    publisher_mapped_facility_id_count = 0
    for row in table_rows[EEA_FACILITY_TABLE]:
        facility_id = _required_identifier(
            row["Facility_INSPIRE_ID"], "EEA Facility_INSPIRE_ID"
        )
        if facility_id in facilities:
            raise ValueError(
                f"EEA facilities have duplicate Facility_INSPIRE_ID {facility_id!r}"
            )
        file_id = _integer_text(
            row["fileId_EUReg"], f"EEA facility {facility_id} fileId_EUReg"
        )
        if file_id not in metadata:
            raise ValueError(
                f"EEA facility {facility_id!r} references missing metadata {file_id!r}"
            )
        parent_site = _required_identifier(
            row["Parent_Site_INSPIRE_ID"],
            f"EEA facility {facility_id} Parent_Site_INSPIRE_ID",
        )
        if parent_site not in sites:
            missing_parent_site_count += 1
        facilities[facility_id] = row
        if _PUBLISHER_MAPPED_FACILITY_ID_RE.match(facility_id):
            publisher_mapped_facility_id_count += 1
        facility_country_counts[row["countryCode"] or "__MISSING__"] += 1
        facility_reporting_year_counts[
            metadata[file_id]["reportingYear"] or "__MISSING__"
        ] += 1
        if _coordinate_missing_or_invalid(row):
            missing_or_invalid_coordinate_count += 1
        facility_name_confidential = _is_confidential(
            row["facilityName_confidentialityReasonCode"],
            row["facilityName_confidentialityReasonName"],
        )
        if facility_name_confidential:
            confidential_facility_name_count += 1
        else:
            matches = _name_term_matches(row["nameOfFeature"])
            if matches:
                facility_name_matches[facility_id] = matches
        if _is_confidential(
            row["parentCompany_confidentialityReasonCode"],
            row["parentCompany_confidentialityReasonName"],
        ):
            confidential_parent_company_count += 1
        if _is_confidential(
            row["addressDetails_confidentialityReasonCode"],
            row["addressDetails_confidentialityReasonName"],
        ):
            confidential_address_count += 1

    orphan_function_ids = sorted(set(functions_by_facility) - set(facilities))
    qualifying_orphans = sorted(nace_candidate_ids - set(facilities))
    if qualifying_orphans:
        raise ValueError(
            "EEA NACE candidate evidence is not joinable to a facility: "
            f"{qualifying_orphans[:5]!r}"
        )

    site_candidate_ids = {
        facility_id
        for facility_id, row in facilities.items()
        if row["Parent_Site_INSPIRE_ID"] in site_name_matches
    }
    candidate_ids = (
        nace_candidate_ids | set(facility_name_matches) | site_candidate_ids
    )

    detail_ids: set[str] = set()
    details_by_facility: dict[str, list[dict[str, str | None]]] = defaultdict(list)
    detail_metadata_references: dict[str, tuple[tuple[str, str], ...]] = {}
    orphan_detail_count = 0
    detail_case_only_fk_drift_count = 0
    detail_whitespace_only_fk_drift_count = 0
    detail_unresolved_fk_drift_count = 0
    confidential_detail_row_count = 0
    facility_ids_by_casefold: dict[str, set[str]] = defaultdict(set)
    for exact_facility_id in facilities:
        facility_ids_by_casefold[exact_facility_id.casefold()].add(exact_facility_id)
    for row in table_rows[EEA_FACILITY_DETAILS_TABLE]:
        detail_id = _integer_text(
            row["ProductionFacilityDetailsID"],
            "EEA ProductionFacilityDetailsID",
        )
        if detail_id in detail_ids:
            raise ValueError(
                f"EEA facility details have duplicate ID {detail_id!r}"
            )
        detail_ids.add(detail_id)
        year = _integer_text(
            row["reportingYear"], f"EEA facility detail {detail_id} reportingYear"
        )
        if not 2000 <= int(year) <= 2100:
            raise ValueError(
                f"EEA facility detail {detail_id} reportingYear is out of range"
            )
        if _is_confidential(
            row["confidentialityReasonCode"], row["confidentialityReasonName"]
        ):
            confidential_detail_row_count += 1
        references: list[tuple[str, str]] = []
        eu_reg_file_id = _metadata_reference(
            row["fileId_EUReg"],
            index=metadata,
            context=f"EEA facility detail {detail_id} fileId_EUReg",
        )
        if eu_reg_file_id is not None:
            references.append((EEA_METADATA_TABLE, eu_reg_file_id))
        eprtr_file_id = _metadata_reference(
            row["fileId_EPRTR_LCP"],
            index=eprtr_metadata,
            context=f"EEA facility detail {detail_id} fileId_EPRTR_LCP",
        )
        if eprtr_file_id is not None:
            references.append((EEA_EPRTR_METADATA_TABLE, eprtr_file_id))
        detail_metadata_references[detail_id] = tuple(references)

        raw_facility_id = row["Facility_INSPIRE_ID"]
        facility_id: str | None = None
        if isinstance(raw_facility_id, str) and raw_facility_id in facilities:
            facility_id = raw_facility_id
        else:
            # Classification is audit-only.  It never repairs a foreign key or
            # permits a non-exact row to enter candidate evidence.
            orphan_detail_count += 1
            if (
                isinstance(raw_facility_id, str)
                and raw_facility_id != raw_facility_id.strip()
                and raw_facility_id.strip() in facilities
            ):
                detail_whitespace_only_fk_drift_count += 1
            elif isinstance(raw_facility_id, str):
                matches = facility_ids_by_casefold.get(raw_facility_id.casefold(), set())
                if len(matches) == 1 and raw_facility_id not in matches:
                    detail_case_only_fk_drift_count += 1
                else:
                    detail_unresolved_fk_drift_count += 1
            else:
                detail_unresolved_fk_drift_count += 1
        if facility_id in candidate_ids:
            details_by_facility[facility_id].append(row)

    candidate_country_counts: Counter[str] = Counter()
    candidate_reporting_year_counts: Counter[str] = Counter()
    candidate_latest_detail_reporting_year_counts: Counter[str] = Counter()
    candidate_reason_counts: Counter[str] = Counter()
    publisher_mapped_candidate_id_count = 0
    candidates: list[dict[str, Any]] = []
    for facility_id in sorted(candidate_ids):
        facility = facilities[facility_id]
        facility_projected = _facility_projection(facility)
        parent_site_id = facility["Parent_Site_INSPIRE_ID"]
        site = sites.get(parent_site_id or "")
        site_projected = None if site is None else _site_projection(site)
        file_id = _integer_text(
            facility["fileId_EUReg"],
            f"EEA facility {facility_id} fileId_EUReg",
        )
        metadata_row = metadata[file_id]
        projected_functions = [
            _function_projection(row)
            for row in sorted(
                functions_by_facility.get(facility_id, []),
                key=lambda item: int(
                    _integer_text(item["FunctionId"], "EEA FunctionId")
                ),
            )
        ]
        sorted_details = sorted(
            details_by_facility.get(facility_id, []),
            key=lambda item: int(
                _integer_text(
                    item["ProductionFacilityDetailsID"],
                    "EEA ProductionFacilityDetailsID",
                )
            ),
        )
        projected_details = [
            _detail_projection(row)
            for row in sorted_details
        ]

        metadata_keys: set[tuple[str, str]] = {(EEA_METADATA_TABLE, file_id)}
        if site is not None:
            site_file_id = _integer_text(
                site["fileId_EUReg"], f"EEA site {parent_site_id} fileId_EUReg"
            )
            metadata_keys.add((EEA_METADATA_TABLE, site_file_id))
        for detail in sorted_details:
            detail_id = _integer_text(
                detail["ProductionFacilityDetailsID"],
                "EEA ProductionFacilityDetailsID",
            )
            metadata_keys.update(detail_metadata_references[detail_id])
        projected_metadata: list[dict[str, Any]] = []
        for source_table, referenced_file_id in sorted(
            metadata_keys, key=lambda item: (item[0], int(item[1]))
        ):
            source_index = (
                metadata
                if source_table == EEA_METADATA_TABLE
                else eprtr_metadata
            )
            projected_metadata.append(
                _annotated_metadata_projection(
                    source_index[referenced_file_id], source_table=source_table
                )
            )

        reasons: list[dict[str, Any]] = []
        for row, projected in zip(
            sorted(
                functions_by_facility.get(facility_id, []),
                key=lambda item: int(
                    _integer_text(item["FunctionId"], "EEA FunctionId")
                ),
            ),
            projected_functions,
            strict=True,
        ):
            raw_code = row["NACEMainEconomicActivityCode"]
            if raw_code == EEA_NACE_CODE:
                reasons.append(
                    _reason(
                        kind="electronic_components_nace_26_11_candidate",
                        source_table=EEA_FUNCTION_TABLE,
                        source_row=projected,
                        source_field="NACEMainEconomicActivityCode",
                        raw_value=raw_code or "",
                    )
                )
        if facility_id in facility_name_matches:
            reasons.append(
                _reason(
                    kind="explicit_facility_name_candidate",
                    source_table=EEA_FACILITY_TABLE,
                    source_row=facility_projected,
                    source_field="nameOfFeature",
                    raw_value=facility["nameOfFeature"] or "",
                    matched_terms=facility_name_matches[facility_id],
                )
            )
        if facility_id in site_candidate_ids:
            if site is None or site_projected is None:
                raise AssertionError("site-name candidate lost its parent site")
            reasons.append(
                _reason(
                    kind="explicit_parent_site_name_candidate",
                    source_table=EEA_SITE_TABLE,
                    source_row=site_projected,
                    source_field="nameOfFeature",
                    raw_value=site["nameOfFeature"] or "",
                    matched_terms=site_name_matches[parent_site_id or ""],
                )
            )
        reasons.sort(
            key=lambda item: (
                item["kind"],
                item["source_projected_values_sha256"],
            )
        )
        if not reasons:
            raise AssertionError("EEA candidate has no deterministic reason")
        for kind in {reason["kind"] for reason in reasons}:
            candidate_reason_counts[kind] += 1

        country = facility["countryCode"] or "__MISSING__"
        reporting_year = metadata_row["reportingYear"] or "__MISSING__"
        candidate_country_counts[country] += 1
        candidate_reporting_year_counts[reporting_year] += 1
        latest_detail_reporting_year = (
            max(
                (
                    _integer_text(
                        row["reportingYear"],
                        "EEA candidate detail reportingYear",
                    )
                    for row in sorted_details
                ),
                key=int,
            )
            if sorted_details
            else None
        )
        candidate_latest_detail_reporting_year_counts[
            latest_detail_reporting_year or "__NO_EXACT_DETAIL__"
        ] += 1
        if _PUBLISHER_MAPPED_FACILITY_ID_RE.match(facility_id):
            publisher_mapped_candidate_id_count += 1
        anomalies: list[str] = []
        if site is None:
            anomalies.append("missing_parent_site")
        if _coordinate_missing_or_invalid(facility):
            anomalies.append("missing_or_invalid_representative_point")
        candidates.append(
            {
                "candidate_reasons": reasons,
                "facility_detail_rows": projected_details,
                "facility_inspire_id": facility_id,
                "facility_rows": [facility_projected],
                "function_rows": projected_functions,
                "join_anomalies": anomalies,
                "latest_source_detail_reporting_year": latest_detail_reporting_year,
                "metadata_rows": projected_metadata,
                "record_type": EEA_RECORD_TYPE,
                "site_rows": [] if site_projected is None else [site_projected],
            }
        )

    function_facilities = set(functions_by_facility) & set(facilities)
    return EEAIndustrialExportScan(
        root=export_root,
        table_summaries=tuple(summaries),
        metadata_row_count=len(table_rows[EEA_METADATA_TABLE]),
        eprtr_metadata_row_count=len(table_rows[EEA_EPRTR_METADATA_TABLE]),
        site_row_count=len(table_rows[EEA_SITE_TABLE]),
        facility_row_count=len(table_rows[EEA_FACILITY_TABLE]),
        distinct_facility_count=len(facilities),
        facility_detail_row_count=len(table_rows[EEA_FACILITY_DETAILS_TABLE]),
        function_row_count=len(table_rows[EEA_FUNCTION_TABLE]),
        production_volume_row_count=len(table_rows[EEA_PRODUCTION_VOLUME_TABLE]),
        facilities_with_function_count=len(function_facilities),
        nace_26_11_row_count=nace_26_11_rows,
        nace_26_11_facility_count=len(nace_candidate_ids),
        facility_name_candidate_count=len(facility_name_matches),
        site_name_candidate_count=len(site_candidate_ids),
        candidate_count=len(candidates),
        candidate_reason_counts=_counter_tuple(candidate_reason_counts),
        candidate_country_counts=_counter_tuple(candidate_country_counts),
        facility_country_counts=_counter_tuple(facility_country_counts),
        facility_reporting_year_counts=_counter_tuple(
            facility_reporting_year_counts
        ),
        candidate_reporting_year_counts=_counter_tuple(
            candidate_reporting_year_counts
        ),
        candidate_latest_detail_reporting_year_counts=_counter_tuple(
            candidate_latest_detail_reporting_year_counts
        ),
        missing_parent_site_count=missing_parent_site_count,
        facility_without_function_count=len(facilities) - len(function_facilities),
        missing_or_invalid_coordinate_count=missing_or_invalid_coordinate_count,
        confidential_facility_name_count=confidential_facility_name_count,
        confidential_parent_company_count=confidential_parent_company_count,
        confidential_address_count=confidential_address_count,
        confidential_detail_row_count=confidential_detail_row_count,
        orphan_function_count=len(orphan_function_ids),
        orphan_detail_count=orphan_detail_count,
        detail_exact_fk_drift_count=orphan_detail_count,
        detail_case_only_fk_drift_count=detail_case_only_fk_drift_count,
        detail_whitespace_only_fk_drift_count=(
            detail_whitespace_only_fk_drift_count
        ),
        detail_unresolved_fk_drift_count=detail_unresolved_fk_drift_count,
        publisher_mapped_facility_id_count=publisher_mapped_facility_id_count,
        publisher_mapped_candidate_id_count=publisher_mapped_candidate_id_count,
        candidates=tuple(candidates),
    )


def scan_eea_industrial_v16_exports(root: str | Path) -> EEAIndustrialExportScan:
    """Build leads only from the exact reviewed EEA edition 16.00 exports."""

    scan = scan_eea_industrial_exports(root)
    observed = tuple(
        EEAV16TableContract(
            table=summary.table,
            sha256=summary.sha256,
            bytes=summary.bytes,
            row_count=summary.row_count,
        )
        for summary in scan.table_summaries
    )
    if observed != EEA_V16_TABLE_CONTRACT:
        raise ValueError(
            "EEA exports do not match the exact reviewed edition 16.00 "
            "table hash/count contract"
        )
    return scan


def canonical_candidate_jsonl_bytes(scan: EEAIndustrialExportScan) -> bytes:
    """Serialize one deterministic JSON object per exact facility identifier."""

    if not scan.candidates:
        return b""
    return b"\n".join(_canonical_json_bytes(candidate) for candidate in scan.candidates) + b"\n"
