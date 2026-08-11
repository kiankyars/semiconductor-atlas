"""Immutable, offline-replayable EEA Industrial Reporting v16 snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator
from urllib.parse import urlsplit

from .adapters.eea_industrial import (
    EEA_ATTRIBUTION,
    EEA_DATASET_ID,
    EEA_DATASET_URL,
    EEA_DOI_URL,
    EEA_EDITION,
    EEA_FILTER_VERSION,
    EEA_LICENSE,
    EEA_LICENSE_SPDX,
    EEA_LICENSE_URL,
    EEA_NAME_TERM_LEXICON_SHA256,
    EEA_NULL_SENTINEL,
    EEA_REQUIRED_TABLES,
    EEA_V16_ACCDB_BYTES,
    EEA_V16_ACCDB_SHA256,
    EEA_V16_TABLE_CONTRACT,
    EEA_V16_TABLE_CONTRACT_SHA256,
    EEAIndustrialExportScan,
    canonical_candidate_jsonl_bytes,
    scan_eea_industrial_v16_exports,
)
from .eea_mdbtools import (
    EEA_MDBTOOLS_BINARY_MANIFEST_SHA256,
    EEA_MDBTOOLS_FORMAT,
    EEA_MDBTOOLS_PACKAGE_MANIFEST_SHA256,
    EEA_MDBTOOLS_PACKAGE_VERSION,
    EEA_MDBTOOLS_PLATFORM,
    EEA_MDBTOOLS_VERSION,
)
from .gleif_snapshot import _rename_directory_no_replace


SOURCE_SNAPSHOT_FORMAT = "semiconductor-atlas-source-inputs-v1"
EEA_SCOPE = "eea_industrial_v16_semiconductor_candidates"
EEA_COVERAGE = (
    "complete_exact_filter_over_the_eea_reported_production_facility_population_"
    "in_the_pinned_v16_accdb_not_a_european_facility_or_semiconductor_census"
)
EEA_CANDIDATE_FILENAME = "eea-industrial-v16-semiconductor-candidates.jsonl"
EEA_CANDIDATE_RECORD_TYPE = "eea_industrial_v16_semiconductor_candidates"
EEA_RAW_RECORD_TYPE = "eea_industrial_v16_official_accdb"
EEA_RAW_FILENAME = "Industrial_dataset_v_16_2026_02_16.accdb"
EEA_RAW_PATH = f"raw/sha256/{EEA_V16_ACCDB_SHA256}.accdb"
EEA_PUBLIC_FOLDER_URL = (
    "https://sdi.eea.europa.eu/webdav/datastore/public/"
    "eea_t_ied-eprtr_p_2007-2024_v16_r00"
)
EEA_RAW_URL = f"{EEA_PUBLIC_FOLDER_URL}/{EEA_RAW_FILENAME}"
EEA_TABULAR_CATALOGUE_URL = (
    "https://sdi.eea.europa.eu/catalogue/srv/api/records/"
    "657ac3cb-affa-4295-a4a9-27b4f539adab"
)
EEA_SPATIAL_DOI_URL = "https://doi.org/10.2909/3bbf28cb-70e8-4073-8fe9-8c1d9c513f52"
EEA_SPATIAL_CATALOGUE_URL = (
    "https://sdi.eea.europa.eu/catalogue/srv/api/records/"
    "3bbf28cb-70e8-4073-8fe9-8c1d9c513f52"
)
EEA_SPATIAL_DATASET_ID = "eea_v_4326_10_m_ied-eprtr_p_2007-2024_v16_r00"
EEA_RIGHTS_REVIEWED_AT = "2026-07-20"
EEA_PUBLICATION_DATE = "2026-02-20"
EEA_SUBMISSION_CUTOFF_DATE = "2026-02-10"
EEA_SOURCE_FILE_LAST_MODIFIED_AT = "2026-02-17T10:28:13Z"
EEA_SOURCE_FILE_ETAG = '"6aeb38734c0403a8c0451b1fb0774106"'
EEA_SOURCE_FILE_CONTENT_TYPE = "application/msaccess"
EEA_EXTRACTION_IMAGE_ID = (
    "sha256:623681f2ac9779af553e72d0578be8bec5e0dea0c703b76eb4dbd16c4b80bc55"
)
EEA_EXTRACTION_METADATA_SHA256 = (
    "31686cdc0f9fa3832f11c06d2ea813bfa5f03d5ac320231379ffa7c3111b60c8"
)
EEA_EXTRACTION_METADATA_BYTES = 16_893
EEA_EXTRACTION_FILE_COUNT = 43
EEA_INVENTORY_SCHEMA_SHA256 = (
    "d61b886e835b0fc7e275af88d339ca841f593008955f9964d6d47bc97951bb6c"
)
EEA_INVENTORY_SCHEMA_BYTES = 15_714
EEA_MAX_MANIFEST_BYTES = 8 * 1024 * 1024
EEA_MAX_SMALL_INPUT_BYTES = 32 * 1024 * 1024
EEA_MAX_CANDIDATE_BYTES = 64 * 1024 * 1024
EEA_MAX_ACCDB_BYTES = 3 * 1024 * 1024 * 1024
EEA_MAX_TABLE_BYTES = 2 * 1024 * 1024 * 1024

_READ_CHUNK_BYTES = 1024 * 1024
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

EEA_INVENTORY_TABLES = (
    "0a_DataCollectionMetadata_EUReg",
    "0b_DataCollectionMetadata_EPRTR_LCP",
    "1_ProductionSite",
    "2_ProductionFacility",
    "2a_ProductionFacilityDetails",
    "2b_EPRTRAnnexIOtherActivity",
    "2c_Function",
    "2d_CompetentAuthorityEPRTR",
    "2e_ProductionVolume",
    "2f1_PollutantRelease_MethodClassification",
    "2f_PollutantRelease",
    "2g1_OffsitePollutantTransfer_MethodClassification",
    "2g_OffsitePollutantTransfer",
    "2h1_OffsiteWasteTransfer_MethodClassification",
    "2h_OffsiteWasteTransfer",
    "3_ProductionInstallation",
    "3a_ProductionInstallationDetails",
    "3b_IEDAnnexIOtherActivity",
    "3c_PermitDetails",
    "3d_BATConclusions",
    "3e_BATDerogations",
    "3f1_eSPIRSIdentifiers",
    "3f2_ETSIdentifiers",
    "3g_CompetentAuthorityInspections",
    "3h_CompetentAuthorityPermits",
    "3i_StricterPermitConditions",
    "3j_OtherRelevantChapters",
    "4_ProductionInstallationPart",
    "4a_ProductionInstallationPartDetails",
    "4b_DesulphurisationInformation",
    "4c_Derogations",
    "4d_EnergyInput",
    "4e_EmissionsToAir",
)


@dataclass(frozen=True, slots=True)
class _OfficialArtifact:
    source_name: str
    path: str
    sha256: str
    bytes: int
    content_type: str
    record_type: str
    url: str


_OFFICIAL_ARTIFACTS = (
    _OfficialArtifact(
        "README.md",
        "official/README.md",
        "b38a5c5c8e28212025b55c3142a206e3abd692b5956f17400ae0dd6385eb6317",
        1_129,
        "text/markdown",
        "eea_industrial_v16_official_readme",
        EEA_PUBLIC_FOLDER_URL,
    ),
    _OfficialArtifact(
        "metadata.pdf",
        "official/metadata.pdf",
        "c0a4324a5576f44ec71a5f5d22ec8bc1e52f6ab9ea666359f74d7c4a850cb9c4",
        1_426_051,
        "application/pdf",
        "eea_industrial_v16_official_metadata_pdf",
        EEA_PUBLIC_FOLDER_URL,
    ),
    _OfficialArtifact(
        "metadata.xml",
        "official/metadata.xml",
        "eb2912a1736fdc4d9156dc6b73f40ed9c485e99a3cfc10ec5641366c25d3b932",
        70_011,
        "application/xml",
        "eea_industrial_v16_official_metadata_xml",
        EEA_TABULAR_CATALOGUE_URL,
    ),
    _OfficialArtifact(
        "tabular-catalogue.json",
        "official/tabular-catalogue.json",
        "324b004010f9890022c24694385d33d865797ef20550e005475c108a712477f2",
        69_455,
        "application/json",
        "eea_industrial_v16_tabular_catalogue_response",
        EEA_TABULAR_CATALOGUE_URL,
    ),
    _OfficialArtifact(
        "spatial-catalogue.json",
        "official/spatial-catalogue.json",
        "87c25d28aa281278072f03b1285b3367b59db9209c65cbbfcca26389b59b64d3",
        72_843,
        "application/json",
        "eea_industrial_v16_spatial_catalogue_response",
        EEA_SPATIAL_CATALOGUE_URL,
    ),
    _OfficialArtifact(
        "propfind.xml",
        "official/sanitized-propfind.xml",
        "ea50696f822a3304a25734281a34e541c7a8b52a2a2a652bbb8b29277c5d5a89",
        3_968,
        "application/xml",
        "eea_industrial_v16_sanitized_public_folder_listing",
        EEA_PUBLIC_FOLDER_URL,
    ),
)

_LIMITATIONS = (
    "industrial_reporting_population_is_not_a_european_industrial_or_semiconductor_census",
    "no_external_semiconductor_recall_denominator_is_available",
    "nace_26_11_includes_non_semiconductor_electronic_components",
    "explicit_names_are_discovery_signals_not_facility_classification_proof",
    "ied_or_eprtr_activity_and_status_do_not_establish_operation_output_or_capacity",
    "latest_detail_reporting_year_is_source_freshness_not_current_operating_status",
    "raw_tabular_point_scalars_are_not_geometry_or_surveyed_site_boundaries",
    "production_volume_is_withheld_in_v16_and_no_capacity_claim_is_emitted",
    "non_exact_historical_foreign_keys_are_classified_but_never_repaired_or_joined",
    "publisher_mapped_identifiers_are_not_independent_national_registry_identifiers",
    "candidate_absence_or_later_omission_is_not_closure_cancellation_or_inactivity_evidence",
)


@dataclass(frozen=True, slots=True)
class _Artifact:
    path: str
    sha256: str
    bytes: int
    content_type: str
    record_type: str
    artifact_kind: str
    url: str
    record_count: int | None = None
    filter_version: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedEEAIndustrialSnapshot:
    root: Path
    manifest_sha256: str
    manifest_size: int
    manifest_bytes: bytes
    retrieved_at: str
    metadata_retrieved_at: str
    accepted_at: str
    candidate_sha256: str
    candidate_size: int
    candidate_count: int
    raw_sha256: str
    raw_size: int
    extraction_metadata_sha256: str
    scan: EEAIndustrialExportScan


class _DuplicateJSONKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def _load_json_object(raw: bytes, context: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{context} is not valid UTF-8 JSON") from error
    except _DuplicateJSONKey as error:
        raise ValueError(f"{context} contains {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _required_text(value: object, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(f"{context} must be a non-empty clean string")
    return value


def _sha256(value: object, context: str) -> str:
    text = _required_text(value, context)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{context} must be a lowercase SHA-256 digest")
    return text


def _integer(
    value: object,
    context: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{context} must be an integer of at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{context} must not exceed {maximum}")
    return value


def _timestamp(value: object, context: str) -> str:
    text = _required_text(value, context)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{context} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must include a timezone")
    canonical = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if canonical != text:
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    return text


def _clock(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _date(value: object, context: str) -> str:
    text = _required_text(value, context)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{context} must be an ISO 8601 date") from error
    if parsed.isoformat() != text:
        raise ValueError(f"{context} must be a canonical ISO 8601 date")
    return text


def _relative_parts(value: object, context: str) -> tuple[str, ...]:
    text = _required_text(value, context)
    parsed = PurePosixPath(text)
    if (
        "\\" in text
        or len(text) > 2048
        or parsed.is_absolute()
        or parsed.as_posix() != text
        or not parsed.parts
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError(f"{context} must be a canonical relative path")
    return parsed.parts


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


class _SnapshotReader:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).absolute()
        self._root_fd = -1
        self._root_identity: tuple[int, int, int, int, int] | None = None

    def __enter__(self) -> _SnapshotReader:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        if nofollow is None or directory is None:
            raise ValueError("EEA snapshot verification requires safe directory opens")
        try:
            self._root_fd = os.open(
                self.root,
                os.O_RDONLY | nofollow | directory | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError as error:
            raise ValueError("EEA snapshot root must be a non-symlink directory") from error
        details = os.fstat(self._root_fd)
        if not stat.S_ISDIR(details.st_mode):
            os.close(self._root_fd)
            self._root_fd = -1
            raise ValueError("EEA snapshot root must be a directory")
        self._root_identity = _identity(details)
        return self

    def __exit__(self, *_args: object) -> None:
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    @contextmanager
    def open(self, relative_path: object) -> Iterator[tuple[BinaryIO, os.stat_result]]:
        if self._root_fd < 0:
            raise RuntimeError("snapshot reader is not open")
        parts = _relative_parts(relative_path, "EEA snapshot input path")
        directory_fd = os.dup(self._root_fd)
        descriptor = -1
        try:
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            for part in parts[:-1]:
                next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                details = os.fstat(next_fd)
                if not stat.S_ISDIR(details.st_mode):
                    os.close(next_fd)
                    raise ValueError(f"EEA snapshot path component is not a directory: {relative_path}")
                os.close(directory_fd)
                directory_fd = next_fd
            descriptor = os.open(
                parts[-1],
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"EEA snapshot input must be a regular file: {relative_path}")
            stream = os.fdopen(descriptor, "rb", closefd=True)
            descriptor = -1
            try:
                yield stream, before
            finally:
                stream.close()
        except OSError as error:
            raise ValueError(f"EEA snapshot input is missing or unsafe: {relative_path}") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(directory_fd)

    def hash(
        self, relative_path: object, *, maximum_bytes: int
    ) -> tuple[str, int, tuple[int, int, int, int, int]]:
        with self.open(relative_path) as (stream, before):
            if before.st_size < 0 or before.st_size > maximum_bytes:
                raise ValueError(f"EEA snapshot input exceeds its byte limit: {relative_path}")
            digest = hashlib.sha256()
            size = 0
            while chunk := stream.read(_READ_CHUNK_BYTES):
                size += len(chunk)
                if size > maximum_bytes:
                    raise ValueError(f"EEA snapshot input exceeds its byte limit: {relative_path}")
                digest.update(chunk)
            after = os.fstat(stream.fileno())
            if _identity(before) != _identity(after) or size != before.st_size:
                raise ValueError(f"EEA snapshot input changed while reading: {relative_path}")
            return digest.hexdigest(), size, _identity(after)

    def read(self, relative_path: object, *, maximum_bytes: int) -> bytes:
        with self.open(relative_path) as (stream, before):
            if before.st_size < 0 or before.st_size > maximum_bytes:
                raise ValueError(f"EEA snapshot input exceeds its byte limit: {relative_path}")
            raw = stream.read(maximum_bytes + 1)
            after = os.fstat(stream.fileno())
            if (
                len(raw) > maximum_bytes
                or len(raw) != before.st_size
                or _identity(before) != _identity(after)
            ):
                raise ValueError(f"EEA snapshot input changed or exceeds its limit: {relative_path}")
            return raw

    def identity(self, relative_path: object) -> tuple[int, int, int, int, int]:
        with self.open(relative_path) as (_stream, details):
            return _identity(details)

    def inventory(self) -> tuple[set[str], set[str]]:
        if self._root_fd < 0:
            raise RuntimeError("snapshot reader is not open")
        directories: set[str] = set()
        files: set[str] = set()

        def visit(descriptor: int, prefix: str) -> None:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    relative = f"{prefix}/{entry.name}" if prefix else entry.name
                    details = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(details.st_mode):
                        raise ValueError(f"EEA snapshot tree contains a symlink: {relative}")
                    if stat.S_ISREG(details.st_mode):
                        files.add(relative)
                    elif stat.S_ISDIR(details.st_mode):
                        directories.add(relative)
                        child = os.open(
                            entry.name,
                            os.O_RDONLY
                            | getattr(os, "O_DIRECTORY", 0)
                            | getattr(os, "O_NOFOLLOW", 0)
                            | getattr(os, "O_CLOEXEC", 0),
                            dir_fd=descriptor,
                        )
                        try:
                            visit(child, relative)
                        finally:
                            os.close(child)
                    else:
                        raise ValueError(f"EEA snapshot tree entry is unsafe: {relative}")

        visit(self._root_fd, "")
        return directories, files

    def assert_unchanged(self) -> None:
        if self._root_fd < 0 or self._root_identity is None:
            raise RuntimeError("snapshot reader is not open")
        opened = os.fstat(self._root_fd)
        try:
            named = os.stat(self.root, follow_symlinks=False)
        except OSError as error:
            raise ValueError("EEA snapshot root changed during verification") from error
        if (
            not stat.S_ISDIR(named.st_mode)
            or _identity(opened) != self._root_identity
            or _identity(named) != self._root_identity
        ):
            raise ValueError("EEA snapshot root changed during verification")


def _rights() -> dict[str, object]:
    return {
        "access_level": "public",
        "attribution": EEA_ATTRIBUTION,
        "decision": "pass_with_required_attribution",
        "license": EEA_LICENSE,
        "license_spdx": EEA_LICENSE_SPDX,
        "license_url": EEA_LICENSE_URL,
        "no_endorsement": True,
        "no_warranty": True,
        "reviewed_at": EEA_RIGHTS_REVIEWED_AT,
    }


def _filter_policy() -> dict[str, object]:
    return {
        "candidate_semantics": "manual_review_leads_only",
        "confidentiality_rule": "either_nonempty_reason_code_or_reason_name_suppresses_protected_field",
        "facility_name_match": "bounded_normalized_explicit_lexicon",
        "filter_version": EEA_FILTER_VERSION,
        "name_lexicon_sha256": EEA_NAME_TERM_LEXICON_SHA256,
        "name_sources": ["2_ProductionFacility.nameOfFeature", "1_ProductionSite.nameOfFeature"],
        "nace_code": "26.11",
        "nace_match": "exact_raw_source_cell",
        "parent_company_name_creates_candidate": False,
    }


def _coordinate_policy() -> dict[str, object]:
    return {
        "geometry_emitted": False,
        "invalid_point_rule": "missing_partial_nonnumeric_out_of_range_or_zero_pair",
        "raw_tabular_latitude_longitude_scalars_retained": True,
        "spatial_companion_correspondence_verified": False,
        "spatial_companion_dataset_id": EEA_SPATIAL_DATASET_ID,
        "spatial_companion_declared_crs": "EPSG:4326",
        "spatial_companion_doi_url": EEA_SPATIAL_DOI_URL,
        "tabular_crs_asserted": False,
    }


def _capacity_policy() -> dict[str, object]:
    return {
        "capacity_claims_emitted": False,
        "production_volume_published_in_v16": False,
        "production_volume_table": "2e_ProductionVolume",
        "production_volume_table_row_count": 0,
        "publisher_note": "additional_quality_assurance_underway_and_future_release_expected_as_aggregate_eu27",
    }


def _completeness() -> dict[str, object]:
    return {
        "candidate_filter_complete_within_pinned_accdb_reported_population": True,
        "european_industrial_facility_census_complete": False,
        "european_semiconductor_facility_census_complete": False,
        "external_semiconductor_recall_denominator_available": False,
        "later_absence_means_real_world_closure_or_inactivity": False,
    }


def _geographic_discrepancy() -> dict[str, object]:
    return {
        "catalogue_keyword_country_count": 33,
        "catalogue_keywords_omit_observed_country_codes": ["ME"],
        "observed_facility_country_count": 34,
        "pdf_geographic_list_country_count": 31,
        "pdf_list_omits_observed_country_codes": ["ME", "NO", "SK"],
        "treatment": "retain_each_statement_and_observed_count_without_treating_any_as_a_recall_denominator",
    }


def _scan_summary(scan: EEAIndustrialExportScan, candidate_raw: bytes) -> dict[str, object]:
    return {
        "candidate_count": scan.candidate_count,
        "candidate_country_counts": dict(scan.candidate_country_counts),
        "candidate_jsonl_bytes": len(candidate_raw),
        "candidate_jsonl_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "candidate_latest_detail_reporting_year_counts": dict(
            scan.candidate_latest_detail_reporting_year_counts
        ),
        "candidate_reason_counts": dict(scan.candidate_reason_counts),
        "candidate_reporting_year_counts": dict(scan.candidate_reporting_year_counts),
        "confidential_address_count": scan.confidential_address_count,
        "confidential_detail_row_count": scan.confidential_detail_row_count,
        "confidential_facility_name_count": scan.confidential_facility_name_count,
        "confidential_parent_company_count": scan.confidential_parent_company_count,
        "detail_case_only_fk_drift_count": scan.detail_case_only_fk_drift_count,
        "detail_exact_fk_drift_count": scan.detail_exact_fk_drift_count,
        "detail_unresolved_fk_drift_count": scan.detail_unresolved_fk_drift_count,
        "detail_whitespace_only_fk_drift_count": scan.detail_whitespace_only_fk_drift_count,
        "distinct_facility_count": scan.distinct_facility_count,
        "eprtr_metadata_row_count": scan.eprtr_metadata_row_count,
        "facilities_with_function_count": scan.facilities_with_function_count,
        "facility_country_counts": dict(scan.facility_country_counts),
        "facility_detail_row_count": scan.facility_detail_row_count,
        "facility_name_candidate_count": scan.facility_name_candidate_count,
        "facility_reporting_year_counts": dict(scan.facility_reporting_year_counts),
        "facility_row_count": scan.facility_row_count,
        "facility_without_function_count": scan.facility_without_function_count,
        "function_row_count": scan.function_row_count,
        "metadata_row_count": scan.metadata_row_count,
        "missing_or_invalid_coordinate_count": scan.missing_or_invalid_coordinate_count,
        "missing_parent_site_count": scan.missing_parent_site_count,
        "nace_26_11_facility_count": scan.nace_26_11_facility_count,
        "nace_26_11_row_count": scan.nace_26_11_row_count,
        "orphan_detail_count": scan.orphan_detail_count,
        "orphan_function_count": scan.orphan_function_count,
        "production_volume_row_count": scan.production_volume_row_count,
        "publisher_mapped_candidate_id_count": scan.publisher_mapped_candidate_id_count,
        "publisher_mapped_facility_id_count": scan.publisher_mapped_facility_id_count,
        "site_name_candidate_count": scan.site_name_candidate_count,
        "site_row_count": scan.site_row_count,
    }


def _input_entry(artifact: _Artifact) -> dict[str, object]:
    return {
        "artifact_kind": artifact.artifact_kind,
        "bytes": artifact.bytes,
        "content_type": artifact.content_type,
        "filter_version": artifact.filter_version,
        "path": artifact.path,
        "record_count": artifact.record_count,
        "record_type": artifact.record_type,
        "sha256": artifact.sha256,
        "url": artifact.url,
    }


def _source_clocks(
    *,
    download_started_at: str,
    retrieved_at: str,
    metadata_retrieved_at: str,
    primary_extraction_started_at: str,
    primary_extraction_completed_at: str,
    independent_extraction_started_at: str,
    independent_extraction_completed_at: str,
    candidate_generated_at: str,
    accepted_at: str,
) -> dict[str, str]:
    return {
        "accepted_at": accepted_at,
        "candidate_generated_at": candidate_generated_at,
        "download_completed_at": retrieved_at,
        "download_started_at": download_started_at,
        "independent_extraction_completed_at": independent_extraction_completed_at,
        "independent_extraction_started_at": independent_extraction_started_at,
        "metadata_retrieved_at": metadata_retrieved_at,
        "primary_extraction_completed_at": primary_extraction_completed_at,
        "primary_extraction_started_at": primary_extraction_started_at,
        "publication_date": EEA_PUBLICATION_DATE,
        "source_file_last_modified_at": EEA_SOURCE_FILE_LAST_MODIFIED_AT,
        "submission_cutoff_date": EEA_SUBMISSION_CUTOFF_DATE,
    }


def _validate_clocks(clocks: Mapping[str, object], retrieved_at: str, accepted_at: str) -> dict[str, str]:
    expected_keys = set(
        _source_clocks(
            download_started_at="x",
            retrieved_at="x",
            metadata_retrieved_at="x",
            primary_extraction_started_at="x",
            primary_extraction_completed_at="x",
            independent_extraction_started_at="x",
            independent_extraction_completed_at="x",
            candidate_generated_at="x",
            accepted_at="x",
        )
    )
    if set(clocks) != expected_keys:
        raise ValueError("EEA snapshot source clock fields drifted")
    result: dict[str, str] = {}
    for key in expected_keys - {"publication_date", "submission_cutoff_date"}:
        result[key] = _timestamp(clocks[key], f"EEA snapshot {key}")
    result["publication_date"] = _date(clocks["publication_date"], "EEA publication_date")
    result["submission_cutoff_date"] = _date(
        clocks["submission_cutoff_date"], "EEA submission_cutoff_date"
    )
    if result["publication_date"] != EEA_PUBLICATION_DATE:
        raise ValueError("EEA publication date drifted")
    if result["submission_cutoff_date"] != EEA_SUBMISSION_CUTOFF_DATE:
        raise ValueError("EEA submission cutoff date drifted")
    if result["source_file_last_modified_at"] != EEA_SOURCE_FILE_LAST_MODIFIED_AT:
        raise ValueError("EEA source file Last-Modified clock drifted")
    if result["download_completed_at"] != retrieved_at or result["accepted_at"] != accepted_at:
        raise ValueError("EEA top-level and source clocks disagree")
    ordered = [
        result["download_started_at"],
        result["download_completed_at"],
        result["metadata_retrieved_at"],
        result["primary_extraction_started_at"],
        result["primary_extraction_completed_at"],
        result["independent_extraction_started_at"],
        result["independent_extraction_completed_at"],
        result["candidate_generated_at"],
        result["accepted_at"],
    ]
    if any(_clock(first) > _clock(second) for first, second in zip(ordered, ordered[1:])):
        raise ValueError("EEA snapshot source clocks are not monotonically ordered")
    if date.fromisoformat(EEA_SUBMISSION_CUTOFF_DATE) > _clock(
        EEA_SOURCE_FILE_LAST_MODIFIED_AT
    ).date():
        raise ValueError("EEA submission cutoff is later than the source file")
    if _clock(EEA_SOURCE_FILE_LAST_MODIFIED_AT).date() > date.fromisoformat(
        EEA_PUBLICATION_DATE
    ):
        raise ValueError("EEA source file is later than publication")
    return result


def _manifest(
    *,
    scan: EEAIndustrialExportScan,
    candidate_raw: bytes,
    artifacts: Iterable[_Artifact],
    clocks: Mapping[str, str],
) -> dict[str, object]:
    retrieved_at = clocks["download_completed_at"]
    accepted_at = clocks["accepted_at"]
    scope = {
        "acquisition": {
            "canonical_public_folder_url": EEA_PUBLIC_FOLDER_URL,
            "credential_or_ephemeral_access_material_retained": False,
            "method": "GET",
            "request_headers_or_cookies_retained": False,
            "sanitized_folder_listing_retained": True,
            "transport": "public_webdav_share",
        },
        "capacity_policy": _capacity_policy(),
        "complete": True,
        "completeness": _completeness(),
        "coordinate_policy": _coordinate_policy(),
        "coverage": EEA_COVERAGE,
        "dataset_id": EEA_DATASET_ID,
        "dataset_url": EEA_DATASET_URL,
        "doi_url": EEA_DOI_URL,
        "edition": EEA_EDITION,
        "extraction": {
            "accdb_bytes": EEA_V16_ACCDB_BYTES,
            "accdb_sha256": EEA_V16_ACCDB_SHA256,
            "all_retained_files_byte_identical_across_two_runs": True,
            "allowed_table_count": len(EEA_REQUIRED_TABLES),
            "database_format": "ACE12",
            "extractor_metadata_bytes": EEA_EXTRACTION_METADATA_BYTES,
            "extractor_metadata_sha256": EEA_EXTRACTION_METADATA_SHA256,
            "inventory_schema_bytes": EEA_INVENTORY_SCHEMA_BYTES,
            "inventory_schema_sha256": EEA_INVENTORY_SCHEMA_SHA256,
            "inventory_table_count": len(EEA_INVENTORY_TABLES),
            "retained_primary_extraction_file_count": EEA_EXTRACTION_FILE_COUNT,
            "table_contract_sha256": EEA_V16_TABLE_CONTRACT_SHA256,
            "toolchain": {
                "binary_manifest_sha256": EEA_MDBTOOLS_BINARY_MANIFEST_SHA256,
                "container_image_id": EEA_EXTRACTION_IMAGE_ID,
                "mdbtools_package_version": EEA_MDBTOOLS_PACKAGE_VERSION,
                "mdbtools_version": EEA_MDBTOOLS_VERSION,
                "package_manifest_sha256": EEA_MDBTOOLS_PACKAGE_MANIFEST_SHA256,
                "platform": EEA_MDBTOOLS_PLATFORM,
            },
        },
        "filter": _filter_policy(),
        "geographic_coverage_discrepancy": _geographic_discrepancy(),
        "limitations": list(_LIMITATIONS),
        "rights": _rights(),
        "source_clocks": dict(clocks),
        "summary": _scan_summary(scan, candidate_raw),
        "temporal_coverage": {
            "e_prtr_facilities": "2007-2024",
            "ied_installations": "2017-2024",
            "large_combustion_plants": "2016-2024",
        },
        "upstream_accdb": {
            "bytes": EEA_V16_ACCDB_BYTES,
            "content_type": EEA_SOURCE_FILE_CONTENT_TYPE,
            "etag": EEA_SOURCE_FILE_ETAG,
            "filename": EEA_RAW_FILENAME,
            "last_modified_at": EEA_SOURCE_FILE_LAST_MODIFIED_AT,
            "raw_retention_path": EEA_RAW_PATH,
            "sha256": EEA_V16_ACCDB_SHA256,
            "url": EEA_RAW_URL,
        },
    }
    return {
        "accepted_at": accepted_at,
        "format": SOURCE_SNAPSHOT_FORMAT,
        "inputs": [_input_entry(item) for item in sorted(artifacts, key=lambda item: item.path)],
        "retrieval_timestamp_basis": "upstream_download_completion",
        "retrieved_at": retrieved_at,
        "source_scopes": {EEA_SCOPE: scope},
    }


def _validate_url_without_secrets(value: object, context: str) -> str:
    text = _required_text(value, context)
    parsed = urlsplit(text)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{context} must be a credential-free canonical HTTPS URL")
    return text


def _maximum_for(path: str) -> int:
    if path == EEA_RAW_PATH:
        return EEA_MAX_ACCDB_BYTES
    if path == EEA_CANDIDATE_FILENAME:
        return EEA_MAX_CANDIDATE_BYTES
    if path.startswith("adapter-exports/") or path.startswith("extraction/primary/tables/"):
        return EEA_MAX_TABLE_BYTES
    return EEA_MAX_SMALL_INPUT_BYTES


def _artifact_map(inputs: object) -> dict[str, dict[str, Any]]:
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("EEA snapshot inputs must be a non-empty list")
    result: dict[str, dict[str, Any]] = {}
    prior = ""
    expected_keys = {
        "artifact_kind",
        "bytes",
        "content_type",
        "filter_version",
        "path",
        "record_count",
        "record_type",
        "sha256",
        "url",
    }
    for index, item in enumerate(inputs):
        if not isinstance(item, dict) or set(item) != expected_keys:
            raise ValueError(f"EEA snapshot input {index} has schema drift")
        path = _required_text(item["path"], f"EEA snapshot input {index} path")
        _relative_parts(path, f"EEA snapshot input {index} path")
        if path in result or (prior and path <= prior):
            raise ValueError("EEA snapshot input paths must be unique and sorted")
        prior = path
        _sha256(item["sha256"], f"EEA snapshot input {path} sha256")
        _integer(item["bytes"], f"EEA snapshot input {path} bytes", maximum=_maximum_for(path))
        _required_text(item["content_type"], f"EEA snapshot input {path} content_type")
        _required_text(item["record_type"], f"EEA snapshot input {path} record_type")
        _required_text(item["artifact_kind"], f"EEA snapshot input {path} artifact_kind")
        _validate_url_without_secrets(item["url"], f"EEA snapshot input {path} url")
        if item["record_count"] is not None:
            _integer(item["record_count"], f"EEA snapshot input {path} record_count")
        if item["filter_version"] is not None:
            _required_text(item["filter_version"], f"EEA snapshot input {path} filter_version")
        result[path] = item
    return result


def _extraction_artifact_references(metadata: Mapping[str, Any]) -> set[str]:
    paths: set[str] = set()

    def add(artifact: object, context: str) -> None:
        if not isinstance(artifact, dict) or set(artifact) != {"bytes", "path", "sha256"}:
            raise ValueError(f"{context} artifact metadata drifted")
        path = _required_text(artifact["path"], f"{context} path")
        _relative_parts(path, f"{context} path")
        _integer(artifact["bytes"], f"{context} bytes", maximum=EEA_MAX_TABLE_BYTES)
        _sha256(artifact["sha256"], f"{context} sha256")
        if path in paths:
            raise ValueError(f"duplicate extraction artifact path {path}")
        paths.add(path)

    toolchain = metadata["toolchain"]
    add(toolchain["image_inspect"]["stdout"], "image inspect stdout")
    add(toolchain["image_inspect"]["stderr"], "image inspect stderr")
    for key, value in toolchain["runtime_evidence"].items():
        add(value["stdout"], f"runtime {key} stdout")
        add(value["stderr"], f"runtime {key} stderr")
    for key, value in metadata["inventory"].items():
        add(value["stdout"], f"inventory {key} stdout")
        add(value["stderr"], f"inventory {key} stderr")
    for table in metadata["tables"]:
        add(table["count"]["stdout"], f"table {table['name']} count stdout")
        add(table["count"]["stderr"], f"table {table['name']} count stderr")
        add(table["csv"], f"table {table['name']} csv")
        add(table["export"]["stderr"], f"table {table['name']} export stderr")
    return paths


def _verify_extraction_metadata(
    raw: bytes,
    artifacts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    metadata = _load_json_object(raw, "EEA extractor metadata")
    if raw != _canonical_json_bytes(metadata):
        raise ValueError("EEA extractor metadata is not canonical JSON")
    if metadata.get("format") != EEA_MDBTOOLS_FORMAT:
        raise ValueError("EEA extractor metadata format drifted")
    if metadata.get("database_format") != "ACE12":
        raise ValueError("EEA extractor database format drifted")
    if metadata.get("input") != {
        "bytes": EEA_V16_ACCDB_BYTES,
        "filename": EEA_RAW_FILENAME,
        "sha256": EEA_V16_ACCDB_SHA256,
    }:
        raise ValueError("EEA extractor input identity drifted")
    allowlist_raw = "".join(f"{table}\n" for table in EEA_REQUIRED_TABLES).encode("utf-8")
    if metadata.get("allowed_tables") != {
        "count": len(EEA_REQUIRED_TABLES),
        "names": list(EEA_REQUIRED_TABLES),
        "sha256": hashlib.sha256(allowlist_raw).hexdigest(),
    }:
        raise ValueError("EEA extractor allowed-table contract drifted")
    inventory = metadata.get("inventory")
    if not isinstance(inventory, dict) or set(inventory) != {"database_format", "schema", "tables"}:
        raise ValueError("EEA extractor inventory schema drifted")
    if inventory["tables"].get("count") != len(EEA_INVENTORY_TABLES) or inventory["tables"].get(
        "names"
    ) != list(EEA_INVENTORY_TABLES):
        raise ValueError("EEA extractor table inventory drifted")
    schema = inventory["schema"].get("stdout")
    if schema != {
        "bytes": EEA_INVENTORY_SCHEMA_BYTES,
        "path": "inventory/schema.access.sql",
        "sha256": EEA_INVENTORY_SCHEMA_SHA256,
    }:
        raise ValueError("EEA extractor schema identity drifted")
    serialization = metadata.get("serialization")
    if serialization != {
        "binary_format": "hex",
        "csv_delimiter": ",",
        "csv_dialect": "mdb-export_v1.0.1_defaults",
        "csv_encoding": "utf-8",
        "csv_escape": "double_quote",
        "csv_header": True,
        "csv_quote": '"',
        "csv_row_delimiter": "LF",
        "date_format": "%Y-%m-%d",
        "datetime_format": "%Y-%m-%dT%H:%M:%S",
        "null_sentinel": EEA_NULL_SENTINEL,
    }:
        raise ValueError("EEA extractor serialization contract drifted")
    toolchain = metadata.get("toolchain")
    if not isinstance(toolchain, dict):
        raise ValueError("EEA extractor toolchain metadata is missing")
    expected_toolchain = {
        "binary_manifest_expected_sha256": EEA_MDBTOOLS_BINARY_MANIFEST_SHA256,
        "container_architecture": "arm64",
        "container_image_id": EEA_EXTRACTION_IMAGE_ID,
        "container_os": "linux",
        "mdbtools_package_version": EEA_MDBTOOLS_PACKAGE_VERSION,
        "mdbtools_version": EEA_MDBTOOLS_VERSION,
        "package_manifest_expected_sha256": EEA_MDBTOOLS_PACKAGE_MANIFEST_SHA256,
    }
    for key, expected in expected_toolchain.items():
        if toolchain.get(key) != expected:
            raise ValueError(f"EEA extractor toolchain {key} drifted")
    table_metadata = metadata.get("tables")
    if not isinstance(table_metadata, list) or len(table_metadata) != len(EEA_V16_TABLE_CONTRACT):
        raise ValueError("EEA extractor table metadata count drifted")
    for observed, contract in zip(table_metadata, EEA_V16_TABLE_CONTRACT, strict=True):
        if (
            observed.get("name") != contract.table
            or observed.get("row_count") != contract.row_count
            or observed.get("csv")
            != {
                "bytes": contract.bytes,
                "path": observed.get("csv", {}).get("path"),
                "sha256": contract.sha256,
            }
        ):
            raise ValueError(f"EEA extractor table contract drifted for {contract.table}")
    referenced = _extraction_artifact_references(metadata)
    expected_primary_paths = {f"extraction/primary/{path}" for path in referenced} | {
        "extraction/primary/metadata.json"
    }
    observed_primary_paths = {
        path for path in artifacts if path.startswith("extraction/primary/")
    }
    if observed_primary_paths != expected_primary_paths or len(observed_primary_paths) != EEA_EXTRACTION_FILE_COUNT:
        raise ValueError("EEA retained primary extraction tree is incomplete or has extras")
    for relative in referenced:
        declared = None
        for item in _walk_artifact_objects(metadata):
            if item.get("path") == relative and set(item) == {"bytes", "path", "sha256"}:
                declared = item
                break
        if declared is None:
            raise AssertionError("referenced extraction artifact disappeared")
        retained = artifacts[f"extraction/primary/{relative}"]
        if retained["bytes"] != declared["bytes"] or retained["sha256"] != declared["sha256"]:
            raise ValueError(f"retained extraction artifact disagrees: {relative}")
        if relative.endswith("stderr.txt") and (
            declared["bytes"] != 0 or declared["sha256"] != _EMPTY_SHA256
        ):
            raise ValueError(f"EEA extractor stderr was not empty: {relative}")
    return metadata


def _walk_artifact_objects(value: object) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if set(value) == {"bytes", "path", "sha256"}:
            yield value
        for child in value.values():
            yield from _walk_artifact_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_artifact_objects(child)


def verify_eea_industrial_snapshot(root: str | Path) -> VerifiedEEAIndustrialSnapshot:
    """Rehash every retained artifact and replay the v16 candidate derivative."""

    with _SnapshotReader(root) as reader:
        manifest_raw = reader.read("manifest.json", maximum_bytes=EEA_MAX_MANIFEST_BYTES)
        manifest = _load_json_object(manifest_raw, "EEA snapshot manifest")
        if manifest_raw != _canonical_json_bytes(manifest):
            raise ValueError("EEA snapshot manifest is not canonical JSON")
        if set(manifest) != {
            "accepted_at",
            "format",
            "inputs",
            "retrieval_timestamp_basis",
            "retrieved_at",
            "source_scopes",
        }:
            raise ValueError("EEA snapshot manifest schema drifted")
        if manifest["format"] != SOURCE_SNAPSHOT_FORMAT:
            raise ValueError("EEA snapshot manifest format drifted")
        if manifest["retrieval_timestamp_basis"] != "upstream_download_completion":
            raise ValueError("EEA snapshot retrieval timestamp basis drifted")
        retrieved_at = _timestamp(manifest["retrieved_at"], "EEA snapshot retrieved_at")
        accepted_at = _timestamp(manifest["accepted_at"], "EEA snapshot accepted_at")
        if _clock(retrieved_at) > _clock(accepted_at):
            raise ValueError("EEA snapshot acceptance predates retrieval")
        scopes = manifest["source_scopes"]
        if not isinstance(scopes, dict) or set(scopes) != {EEA_SCOPE}:
            raise ValueError("EEA snapshot must contain exactly one source scope")
        scope = scopes[EEA_SCOPE]
        if not isinstance(scope, dict):
            raise ValueError("EEA source scope must be an object")
        expected_scope_keys = {
            "acquisition",
            "capacity_policy",
            "complete",
            "completeness",
            "coordinate_policy",
            "coverage",
            "dataset_id",
            "dataset_url",
            "doi_url",
            "edition",
            "extraction",
            "filter",
            "geographic_coverage_discrepancy",
            "limitations",
            "rights",
            "source_clocks",
            "summary",
            "temporal_coverage",
            "upstream_accdb",
        }
        if set(scope) != expected_scope_keys:
            raise ValueError("EEA source scope schema drifted")
        if scope["complete"] is not True or scope["coverage"] != EEA_COVERAGE:
            raise ValueError("EEA source coverage metadata drifted")
        for key, expected in {
            "dataset_id": EEA_DATASET_ID,
            "dataset_url": EEA_DATASET_URL,
            "doi_url": EEA_DOI_URL,
            "edition": EEA_EDITION,
        }.items():
            if scope[key] != expected:
                raise ValueError(f"EEA source {key} drifted")
        expected_acquisition = {
            "canonical_public_folder_url": EEA_PUBLIC_FOLDER_URL,
            "credential_or_ephemeral_access_material_retained": False,
            "method": "GET",
            "request_headers_or_cookies_retained": False,
            "sanitized_folder_listing_retained": True,
            "transport": "public_webdav_share",
        }
        if scope["acquisition"] != expected_acquisition:
            raise ValueError("EEA acquisition metadata drifted")
        _validate_url_without_secrets(
            scope["acquisition"]["canonical_public_folder_url"],
            "EEA canonical public folder URL",
        )
        if scope["filter"] != _filter_policy():
            raise ValueError("EEA filter metadata drifted")
        if scope["coordinate_policy"] != _coordinate_policy():
            raise ValueError("EEA coordinate policy drifted")
        if scope["capacity_policy"] != _capacity_policy():
            raise ValueError("EEA capacity policy drifted")
        if scope["completeness"] != _completeness():
            raise ValueError("EEA completeness metadata drifted")
        if scope["geographic_coverage_discrepancy"] != _geographic_discrepancy():
            raise ValueError("EEA geographic discrepancy metadata drifted")
        if scope["limitations"] != list(_LIMITATIONS):
            raise ValueError("EEA limitations metadata drifted")
        if scope["rights"] != _rights():
            raise ValueError("EEA rights metadata drifted")
        if scope["temporal_coverage"] != {
            "e_prtr_facilities": "2007-2024",
            "ied_installations": "2017-2024",
            "large_combustion_plants": "2016-2024",
        }:
            raise ValueError("EEA temporal coverage metadata drifted")
        clocks = scope["source_clocks"]
        if not isinstance(clocks, dict):
            raise ValueError("EEA source clocks must be an object")
        verified_clocks = _validate_clocks(clocks, retrieved_at, accepted_at)
        upstream = scope["upstream_accdb"]
        expected_upstream = {
            "bytes": EEA_V16_ACCDB_BYTES,
            "content_type": EEA_SOURCE_FILE_CONTENT_TYPE,
            "etag": EEA_SOURCE_FILE_ETAG,
            "filename": EEA_RAW_FILENAME,
            "last_modified_at": EEA_SOURCE_FILE_LAST_MODIFIED_AT,
            "raw_retention_path": EEA_RAW_PATH,
            "sha256": EEA_V16_ACCDB_SHA256,
            "url": EEA_RAW_URL,
        }
        if upstream != expected_upstream:
            raise ValueError("EEA upstream ACCDB metadata drifted")
        extraction = scope["extraction"]
        expected_extraction = _manifest(
            scan=_empty_scan_for_policy(),
            candidate_raw=b"",
            artifacts=(),
            clocks=verified_clocks,
        )["source_scopes"][EEA_SCOPE]["extraction"]
        if extraction != expected_extraction:
            raise ValueError("EEA extraction policy metadata drifted")

        artifacts = _artifact_map(manifest["inputs"])
        directories, files = reader.inventory()
        expected_files = set(artifacts) | {"manifest.json"}
        expected_directories = {
            str(PurePosixPath(path).parent)
            for path in expected_files
            if str(PurePosixPath(path).parent) != "."
        }
        expected_directories |= {
            str(parent)
            for path in tuple(expected_directories)
            for parent in PurePosixPath(path).parents
            if str(parent) != "."
        }
        if files != expected_files or directories != expected_directories:
            raise ValueError("EEA snapshot tree has extra or missing entries")

        identities: dict[str, tuple[int, int, int, int, int]] = {}
        for path, entry in artifacts.items():
            digest, size, identity = reader.hash(path, maximum_bytes=_maximum_for(path))
            if digest != entry["sha256"] or size != entry["bytes"]:
                raise ValueError(f"EEA snapshot input hash or size mismatch: {path}")
            identities[path] = identity

        official_by_path = {item.path: item for item in _OFFICIAL_ARTIFACTS}
        for path, expected in official_by_path.items():
            entry = artifacts.get(path)
            if entry is None or entry != _input_entry(
                _Artifact(
                    path=expected.path,
                    sha256=expected.sha256,
                    bytes=expected.bytes,
                    content_type=expected.content_type,
                    record_type=expected.record_type,
                    artifact_kind="retained_official_metadata_or_catalogue_evidence",
                    url=expected.url,
                )
            ):
                raise ValueError(f"EEA official evidence metadata drifted: {path}")
        propfind = reader.read(
            "official/sanitized-propfind.xml", maximum_bytes=EEA_MAX_SMALL_INPUT_BYTES
        ).lower()
        if any(
            marker in propfind
            for marker in (
                b"authorization",
                b"cookie:",
                b"share-token",
                b"share_token",
                b"token=",
            )
        ):
            raise ValueError("EEA sanitized folder listing retains access material")

        raw_entry = artifacts.get(EEA_RAW_PATH)
        if raw_entry != _input_entry(
            _Artifact(
                path=EEA_RAW_PATH,
                sha256=EEA_V16_ACCDB_SHA256,
                bytes=EEA_V16_ACCDB_BYTES,
                content_type=EEA_SOURCE_FILE_CONTENT_TYPE,
                record_type=EEA_RAW_RECORD_TYPE,
                artifact_kind="retained_official_relational_source",
                url=EEA_RAW_URL,
            )
        ):
            raise ValueError("EEA retained ACCDB input metadata drifted")

        primary_metadata_path = "extraction/primary/metadata.json"
        independent_metadata_path = "extraction/independent-metadata.json"
        primary_raw = reader.read(primary_metadata_path, maximum_bytes=EEA_MAX_SMALL_INPUT_BYTES)
        independent_raw = reader.read(
            independent_metadata_path, maximum_bytes=EEA_MAX_SMALL_INPUT_BYTES
        )
        if (
            primary_raw != independent_raw
            or len(primary_raw) != EEA_EXTRACTION_METADATA_BYTES
            or hashlib.sha256(primary_raw).hexdigest() != EEA_EXTRACTION_METADATA_SHA256
        ):
            raise ValueError("EEA independent extraction manifests are not byte-identical")
        _verify_extraction_metadata(primary_raw, artifacts)

        adapter_paths = {f"adapter-exports/{table}.csv" for table in EEA_REQUIRED_TABLES}
        if {path for path in artifacts if path.startswith("adapter-exports/")} != adapter_paths:
            raise ValueError("EEA adapter export tree is incomplete or has extras")
        extraction_metadata = _load_json_object(primary_raw, "EEA extractor metadata")
        by_table = {item["name"]: item for item in extraction_metadata["tables"]}
        for contract in EEA_V16_TABLE_CONTRACT:
            adapter_path = f"adapter-exports/{contract.table}.csv"
            entry = artifacts[adapter_path]
            if (
                entry["sha256"] != contract.sha256
                or entry["bytes"] != contract.bytes
                or entry["record_count"] != contract.row_count
                or entry["filter_version"] is not None
                or by_table[contract.table]["csv"]["sha256"] != contract.sha256
            ):
                raise ValueError(f"EEA adapter export metadata drifted for {contract.table}")

        scan = scan_eea_industrial_v16_exports(reader.root / "adapter-exports")
        candidate_raw = canonical_candidate_jsonl_bytes(scan)
        candidate_entry = artifacts.get(EEA_CANDIDATE_FILENAME)
        expected_candidate = _input_entry(
            _Artifact(
                path=EEA_CANDIDATE_FILENAME,
                sha256=hashlib.sha256(candidate_raw).hexdigest(),
                bytes=len(candidate_raw),
                content_type="application/x-ndjson",
                record_type=EEA_CANDIDATE_RECORD_TYPE,
                artifact_kind="deterministic_privacy_minimized_candidate_derivative",
                url=EEA_DOI_URL,
                record_count=scan.candidate_count,
                filter_version=EEA_FILTER_VERSION,
            )
        )
        if candidate_entry != expected_candidate:
            raise ValueError("EEA candidate input metadata disagrees with replay")
        retained_candidate = reader.read(
            EEA_CANDIDATE_FILENAME, maximum_bytes=EEA_MAX_CANDIDATE_BYTES
        )
        if retained_candidate != candidate_raw:
            raise ValueError("EEA candidate derivative does not replay byte-for-byte")
        expected_summary = _scan_summary(scan, candidate_raw)
        if scope["summary"] != expected_summary:
            raise ValueError("EEA source summary disagrees with replay")
        if len(expected_summary["facility_country_counts"]) != 34:
            raise ValueError("EEA observed country count no longer matches reviewed evidence")
        if scan.detail_unresolved_fk_drift_count != 0:
            raise ValueError("EEA v16 contains unresolved facility-detail foreign-key drift")
        if scan.production_volume_row_count != 0:
            raise ValueError("EEA v16 unexpectedly contains production-volume rows")

        for path, identity in identities.items():
            if reader.identity(path) != identity:
                raise ValueError(f"EEA snapshot input changed during verification: {path}")
        if reader.read("manifest.json", maximum_bytes=EEA_MAX_MANIFEST_BYTES) != manifest_raw:
            raise ValueError("EEA snapshot manifest changed during verification")
        if reader.inventory() != (directories, files):
            raise ValueError("EEA snapshot tree changed during verification")
        reader.assert_unchanged()

        return VerifiedEEAIndustrialSnapshot(
            root=reader.root,
            manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
            manifest_size=len(manifest_raw),
            manifest_bytes=manifest_raw,
            retrieved_at=retrieved_at,
            metadata_retrieved_at=verified_clocks["metadata_retrieved_at"],
            accepted_at=accepted_at,
            candidate_sha256=hashlib.sha256(candidate_raw).hexdigest(),
            candidate_size=len(candidate_raw),
            candidate_count=scan.candidate_count,
            raw_sha256=EEA_V16_ACCDB_SHA256,
            raw_size=EEA_V16_ACCDB_BYTES,
            extraction_metadata_sha256=EEA_EXTRACTION_METADATA_SHA256,
            scan=scan,
        )


def _empty_scan_for_policy() -> EEAIndustrialExportScan:
    """Return a shape-only scan used solely to obtain static manifest policy."""

    empty_pairs: tuple[tuple[str, int], ...] = ()
    return EEAIndustrialExportScan(
        root=Path("."),
        table_summaries=(),
        metadata_row_count=0,
        eprtr_metadata_row_count=0,
        site_row_count=0,
        facility_row_count=0,
        distinct_facility_count=0,
        facility_detail_row_count=0,
        function_row_count=0,
        production_volume_row_count=0,
        facilities_with_function_count=0,
        nace_26_11_row_count=0,
        nace_26_11_facility_count=0,
        facility_name_candidate_count=0,
        site_name_candidate_count=0,
        candidate_count=0,
        candidate_reason_counts=empty_pairs,
        candidate_country_counts=empty_pairs,
        facility_country_counts=empty_pairs,
        facility_reporting_year_counts=empty_pairs,
        candidate_reporting_year_counts=empty_pairs,
        candidate_latest_detail_reporting_year_counts=empty_pairs,
        missing_parent_site_count=0,
        facility_without_function_count=0,
        missing_or_invalid_coordinate_count=0,
        confidential_facility_name_count=0,
        confidential_parent_company_count=0,
        confidential_address_count=0,
        confidential_detail_row_count=0,
        orphan_function_count=0,
        orphan_detail_count=0,
        detail_exact_fk_drift_count=0,
        detail_case_only_fk_drift_count=0,
        detail_whitespace_only_fk_drift_count=0,
        detail_unresolved_fk_drift_count=0,
        publisher_mapped_facility_id_count=0,
        publisher_mapped_candidate_id_count=0,
        candidates=(),
    )


def _copy_regular_file(source: str | Path, destination: Path, maximum_bytes: int) -> tuple[str, int]:
    candidate = Path(source).absolute()
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("EEA snapshot inputs require symlink-safe opens")
    try:
        named_before = os.stat(candidate, follow_symlinks=False)
        descriptor = os.open(
            candidate,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        raise ValueError(f"EEA snapshot source input is missing or unsafe: {candidate}") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _identity(opened) != _identity(named_before)
            or opened.st_size < 0
            or opened.st_size > maximum_bytes
        ):
            raise ValueError(f"EEA snapshot source input is not a safe bounded file: {candidate}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        output_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        output = os.open(destination, output_flags, 0o600)
        digest = hashlib.sha256()
        size = 0
        try:
            while chunk := os.read(descriptor, _READ_CHUNK_BYTES):
                size += len(chunk)
                if size > maximum_bytes:
                    raise ValueError(f"EEA snapshot source input exceeds its limit: {candidate}")
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(output, view)
                    view = view[written:]
            os.fsync(output)
        finally:
            os.close(output)
        after = os.fstat(descriptor)
        named_after = os.stat(candidate, follow_symlinks=False)
        if (
            _identity(opened) != _identity(after)
            or _identity(after) != _identity(named_after)
            or size != opened.st_size
        ):
            raise ValueError(f"EEA snapshot source input changed while copying: {candidate}")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _copy_extraction_tree(source: str | Path, destination: Path) -> list[tuple[str, str, int]]:
    root = Path(source).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("EEA extraction root must be a non-symlink directory")
    copied: list[tuple[str, str, int]] = []
    for current, directory_names, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in directory_names:
            if (current_path / name).is_symlink():
                raise ValueError("EEA extraction tree contains a symlink")
        for name in sorted(filenames):
            source_path = current_path / name
            if source_path.is_symlink():
                raise ValueError("EEA extraction tree contains a symlink")
            relative = source_path.relative_to(root).as_posix()
            digest, size = _copy_regular_file(
                source_path,
                destination / relative,
                EEA_MAX_TABLE_BYTES,
            )
            copied.append((relative, digest, size))
    return sorted(copied)


def _write_new(path: Path, raw: bytes) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_eea_industrial_snapshot(
    *,
    accdb_path: str | Path,
    official_evidence_directory: str | Path,
    primary_extraction_directory: str | Path,
    independent_extraction_metadata_path: str | Path,
    output_directory: str | Path,
    download_started_at: str,
    retrieved_at: str,
    metadata_retrieved_at: str,
    primary_extraction_started_at: str,
    primary_extraction_completed_at: str,
    independent_extraction_started_at: str,
    independent_extraction_completed_at: str,
    candidate_generated_at: str,
    accepted_at: str,
) -> VerifiedEEAIndustrialSnapshot:
    """Install one exact v16 source snapshot atomically after offline replay."""

    clocks = _source_clocks(
        download_started_at=_timestamp(download_started_at, "download_started_at"),
        retrieved_at=_timestamp(retrieved_at, "retrieved_at"),
        metadata_retrieved_at=_timestamp(metadata_retrieved_at, "metadata_retrieved_at"),
        primary_extraction_started_at=_timestamp(
            primary_extraction_started_at, "primary_extraction_started_at"
        ),
        primary_extraction_completed_at=_timestamp(
            primary_extraction_completed_at, "primary_extraction_completed_at"
        ),
        independent_extraction_started_at=_timestamp(
            independent_extraction_started_at, "independent_extraction_started_at"
        ),
        independent_extraction_completed_at=_timestamp(
            independent_extraction_completed_at, "independent_extraction_completed_at"
        ),
        candidate_generated_at=_timestamp(candidate_generated_at, "candidate_generated_at"),
        accepted_at=_timestamp(accepted_at, "accepted_at"),
    )
    _validate_clocks(clocks, clocks["download_completed_at"], clocks["accepted_at"])
    output = Path(output_directory).absolute()
    if not output.name or output.name in {".", ".."}:
        raise ValueError(f"unsafe EEA snapshot output directory: {output}")
    if os.path.lexists(output):
        raise FileExistsError(f"refusing to overwrite EEA snapshot: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    installed = False
    try:
        artifacts: list[_Artifact] = []
        raw_digest, raw_size = _copy_regular_file(
            accdb_path, stage / EEA_RAW_PATH, EEA_MAX_ACCDB_BYTES
        )
        if raw_digest != EEA_V16_ACCDB_SHA256 or raw_size != EEA_V16_ACCDB_BYTES:
            raise ValueError("EEA ACCDB does not match the exact reviewed v16 source")
        artifacts.append(
            _Artifact(
                EEA_RAW_PATH,
                raw_digest,
                raw_size,
                EEA_SOURCE_FILE_CONTENT_TYPE,
                EEA_RAW_RECORD_TYPE,
                "retained_official_relational_source",
                EEA_RAW_URL,
            )
        )

        evidence_root = Path(official_evidence_directory).absolute()
        for expected in _OFFICIAL_ARTIFACTS:
            digest, size = _copy_regular_file(
                evidence_root / expected.source_name,
                stage / expected.path,
                EEA_MAX_SMALL_INPUT_BYTES,
            )
            if digest != expected.sha256 or size != expected.bytes:
                raise ValueError(f"EEA official evidence drifted: {expected.source_name}")
            artifacts.append(
                _Artifact(
                    expected.path,
                    digest,
                    size,
                    expected.content_type,
                    expected.record_type,
                    "retained_official_metadata_or_catalogue_evidence",
                    expected.url,
                )
            )

        primary_root = stage / "extraction" / "primary"
        copied = _copy_extraction_tree(primary_extraction_directory, primary_root)
        if len(copied) != EEA_EXTRACTION_FILE_COUNT:
            raise ValueError("EEA primary extraction file count drifted")
        for relative, digest, size in copied:
            path = f"extraction/primary/{relative}"
            artifacts.append(
                _Artifact(
                    path,
                    digest,
                    size,
                    "application/json" if relative == "metadata.json" else "application/octet-stream",
                    "eea_mdbtools_primary_extraction_artifact",
                    "retained_reproducibility_evidence",
                    EEA_RAW_URL,
                )
            )
        independent_digest, independent_size = _copy_regular_file(
            independent_extraction_metadata_path,
            stage / "extraction" / "independent-metadata.json",
            EEA_MAX_SMALL_INPUT_BYTES,
        )
        if (
            independent_digest != EEA_EXTRACTION_METADATA_SHA256
            or independent_size != EEA_EXTRACTION_METADATA_BYTES
        ):
            raise ValueError("EEA independent extraction metadata drifted")
        artifacts.append(
            _Artifact(
                "extraction/independent-metadata.json",
                independent_digest,
                independent_size,
                "application/json",
                "eea_mdbtools_independent_extraction_metadata",
                "retained_independent_reproducibility_evidence",
                EEA_RAW_URL,
            )
        )

        extraction_metadata_raw = (primary_root / "metadata.json").read_bytes()
        if (
            len(extraction_metadata_raw) != EEA_EXTRACTION_METADATA_BYTES
            or hashlib.sha256(extraction_metadata_raw).hexdigest()
            != EEA_EXTRACTION_METADATA_SHA256
        ):
            raise ValueError("EEA primary extraction metadata drifted")
        extraction_metadata = _load_json_object(
            extraction_metadata_raw, "EEA primary extraction metadata"
        )
        by_table = {item["name"]: item for item in extraction_metadata["tables"]}
        adapter_root = stage / "adapter-exports"
        adapter_root.mkdir()
        for contract in EEA_V16_TABLE_CONTRACT:
            source_relative = by_table[contract.table]["csv"]["path"]
            destination = adapter_root / f"{contract.table}.csv"
            os.link(primary_root / source_relative, destination, follow_symlinks=False)
            artifacts.append(
                _Artifact(
                    f"adapter-exports/{contract.table}.csv",
                    contract.sha256,
                    contract.bytes,
                    "text/csv",
                    "eea_industrial_v16_adapter_table_export",
                    "exact_reviewed_adapter_input",
                    EEA_RAW_URL,
                    record_count=contract.row_count,
                )
            )

        scan = scan_eea_industrial_v16_exports(adapter_root)
        candidate_raw = canonical_candidate_jsonl_bytes(scan)
        candidate_digest, candidate_size = _write_new(
            stage / EEA_CANDIDATE_FILENAME, candidate_raw
        )
        artifacts.append(
            _Artifact(
                EEA_CANDIDATE_FILENAME,
                candidate_digest,
                candidate_size,
                "application/x-ndjson",
                EEA_CANDIDATE_RECORD_TYPE,
                "deterministic_privacy_minimized_candidate_derivative",
                EEA_DOI_URL,
                record_count=scan.candidate_count,
                filter_version=EEA_FILTER_VERSION,
            )
        )
        manifest_raw = _canonical_json_bytes(
            _manifest(scan=scan, candidate_raw=candidate_raw, artifacts=artifacts, clocks=clocks)
        )
        _write_new(stage / "manifest.json", manifest_raw)
        _fsync_directory(stage)
        verify_eea_industrial_snapshot(stage)
        _rename_directory_no_replace(stage, output)
        installed = True
        _fsync_directory(output.parent)
        return verify_eea_industrial_snapshot(output)
    finally:
        if not installed and stage.exists():
            shutil.rmtree(stage)
