"""Verification and routing for immutable source snapshot directories."""

from __future__ import annotations

import hashlib
import html
import json
import os
import stat
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from .adapters.epa_frs import (
    FRS_ARCHIVE_MEMBER,
    FRS_ATTRIBUTION,
    FRS_DATA_AS_OF_BASIS,
    FRS_DATA_AS_OF_SOURCE_URL,
    FRS_DOCUMENTATION_MEMBER,
    FRS_FILTER_VERSION,
    FRS_LICENSE,
    FRS_LICENSE_URL,
    FRS_NAICS_CODES,
    FRS_OFFICIAL_ARCHIVE_URL,
    FRS_RAW_RECORD_TYPE,
    FRS_RETRIEVAL_TIMESTAMP_BASES,
    FRS_SIC_CODES,
    candidate_jsonl_bytes,
    parse_candidate_jsonl,
    scan_frs_archive,
)


SOURCE_SNAPSHOT_FORMAT = "semiconductor-atlas-source-inputs-v1"
NIST_AWARDS_URL = "https://www.nist.gov/chips/chips-america-awards"
NIST_COMPLETE_DETAIL_COVERAGE = (
    "all_chips_program_office_links_in_archived_index_pages"
)
EPA_FRS_RECORD_TYPE = "epa_frs_semiconductor_candidates"
EPA_FRS_RAW_RECORD_TYPE = FRS_RAW_RECORD_TYPE
EPA_FRS_SCOPE = "epa_frs_semiconductor_candidates"
EPA_FRS_COMPLETE_COVERAGE = (
    "all_national_single_rows_matching_exact_naics_334413_or_sic_3674"
)


@dataclass(frozen=True, slots=True)
class SnapshotInput:
    path: Path
    relative_path: str
    record_type: str
    url: str
    sha256: str
    size: int
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VerifiedSnapshot:
    root: Path
    manifest_sha256: str
    manifest_size: int
    retrieved_at: str
    inputs: tuple[SnapshotInput, ...]
    source_scopes: dict[str, dict[str, Any]]

    def paths(self, record_type: str) -> list[Path]:
        return [item.path for item in self.inputs if item.record_type == record_type]

    def one(self, record_type: str) -> SnapshotInput:
        matches = [item for item in self.inputs if item.record_type == record_type]
        if len(matches) != 1:
            raise ValueError(
                f"snapshot requires exactly one {record_type!r} input; found {len(matches)}"
            )
        return matches[0]

    def scope_is_complete(self, scope: str) -> bool:
        metadata = self.source_scopes.get(scope)
        return isinstance(metadata, dict) and metadata.get("complete") is True


class _NistAwardLinkParser(HTMLParser):
    """Collect the detail links on award cards owned by the CHIPS Program Office."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.card_depth = 0
        self.organization_depth: int | None = None
        self.organization_parts: list[str] = []
        self.detail_url: str | None = None
        self.cpo_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "div":
            if self.card_depth:
                self.card_depth += 1
            elif "margin-top-3" in classes:
                self.card_depth = 1
                self.organization_depth = None
                self.organization_parts = []
                self.detail_url = None
            if self.card_depth and "nist-field__item" in classes:
                self.organization_depth = self.card_depth
        if self.card_depth and tag == "a" and self.detail_url is None:
            href = attributes.get("href") or ""
            if href.startswith("/chips/"):
                self.detail_url = urljoin(NIST_AWARDS_URL, href)

    def handle_data(self, data: str) -> None:
        if self.organization_depth is not None:
            self.organization_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "div" or not self.card_depth:
            return
        if self.organization_depth == self.card_depth:
            self.organization_depth = None
        self.card_depth -= 1
        if self.card_depth == 0:
            organization = html.unescape(" ".join(self.organization_parts)).strip()
            if organization == "CHIPS Program Office" and self.detail_url:
                self.cpo_urls.add(self.detail_url)


def _scope_count(metadata: dict[str, Any], scope: str, field: str) -> int:
    value = metadata.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"source snapshot scope {scope} has an invalid {field}"
        )
    return value


def _nist_detail_urls(index_inputs: list[SnapshotInput]) -> set[str]:
    parser = _NistAwardLinkParser()
    for item in index_inputs:
        try:
            parser.feed(item.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError(
                f"NIST award index is not readable UTF-8 HTML: {item.path.name}"
            ) from error
    parser.close()
    return parser.cpo_urls


def _validate_nist_scope(
    inputs: list[SnapshotInput], source_scopes: dict[str, dict[str, Any]]
) -> None:
    scope = "nist_chips_awards"
    metadata = source_scopes.get(scope)
    if metadata is None:
        return

    index_inputs = [item for item in inputs if item.record_type == "award_index_page"]
    detail_inputs = [item for item in inputs if item.record_type == "award_detail_page"]
    declared_index_count = _scope_count(metadata, scope, "index_page_count")
    declared_detail_count = _scope_count(metadata, scope, "detail_page_count")
    if declared_index_count != len(index_inputs):
        raise ValueError(
            f"source snapshot scope {scope} declares index_page_count "
            f"{declared_index_count}, but contains {len(index_inputs)} award index inputs"
        )
    if declared_detail_count != len(detail_inputs):
        raise ValueError(
            f"source snapshot scope {scope} declares detail_page_count "
            f"{declared_detail_count}, but contains {len(detail_inputs)} award detail inputs"
        )

    detail_coverage = metadata.get("detail_coverage")
    if not isinstance(detail_coverage, str) or not detail_coverage:
        raise ValueError(
            f"source snapshot scope {scope} has an invalid detail_coverage"
        )
    if metadata.get("complete") is not True:
        return
    if detail_coverage != NIST_COMPLETE_DETAIL_COVERAGE:
        raise ValueError(
            f"complete source snapshot scope {scope} must declare detail_coverage "
            f"{NIST_COMPLETE_DETAIL_COVERAGE!r}"
        )
    if not index_inputs or not detail_inputs:
        raise ValueError(
            f"complete source snapshot scope {scope} requires award index and detail inputs"
        )

    linked_urls = _nist_detail_urls(index_inputs)
    archived_urls = [item.url for item in detail_inputs]
    if len(archived_urls) != len(set(archived_urls)):
        raise ValueError(
            f"complete source snapshot scope {scope} contains duplicate award detail URLs"
        )
    if linked_urls != set(archived_urls):
        missing_count = len(linked_urls - set(archived_urls))
        unlinked_count = len(set(archived_urls) - linked_urls)
        raise ValueError(
            f"complete source snapshot scope {scope} does not archive exactly the CHIPS "
            f"Program Office detail links in its index inputs "
            f"({missing_count} missing, {unlinked_count} unlinked)"
        )


def _lower_sha256(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"EPA FRS scope has an invalid {field_name}")
    return value


def _positive_scope_count(metadata: dict[str, Any], field_name: str) -> int:
    value = metadata.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"EPA FRS scope has an invalid {field_name}")
    return value


def _validate_epa_frs_scope(
    inputs: list[SnapshotInput],
    source_scopes: dict[str, dict[str, Any]],
    *,
    retrieved_timestamp: datetime,
    retrieval_timestamp_basis: object,
) -> None:
    candidate_inputs = [
        item for item in inputs if item.record_type == EPA_FRS_RECORD_TYPE
    ]
    raw_archive_inputs = [
        item for item in inputs if item.record_type == EPA_FRS_RAW_RECORD_TYPE
    ]
    metadata = source_scopes.get(EPA_FRS_SCOPE)
    if not candidate_inputs and not raw_archive_inputs and metadata is None:
        return
    if metadata is None:
        raise ValueError(
            "EPA FRS candidate input requires epa_frs_semiconductor_candidates scope metadata"
        )
    if len(candidate_inputs) != 1:
        raise ValueError(
            "EPA FRS scope requires exactly one deterministic filtered candidate input"
        )
    if len(raw_archive_inputs) != 1:
        raise ValueError(
            "EPA FRS scope requires exactly one retained national archive input"
        )
    item = candidate_inputs[0]
    raw_item = raw_archive_inputs[0]
    if item.url != FRS_OFFICIAL_ARCHIVE_URL:
        raise ValueError("EPA FRS input must identify the official national single-file URL")
    if item.metadata.get("artifact_kind") != "deterministic_filtered_derivative":
        raise ValueError("EPA FRS input must declare its filtered-derivative artifact kind")
    expected_input_rights = {
        "license": FRS_LICENSE,
        "license_url": FRS_LICENSE_URL,
        "attribution": FRS_ATTRIBUTION,
    }
    for key, expected in expected_input_rights.items():
        if item.metadata.get(key) != expected:
            raise ValueError(f"EPA FRS input has invalid {key} metadata")
        if raw_item.metadata.get(key) != expected:
            raise ValueError(f"EPA FRS raw archive input has invalid {key} metadata")
    if raw_item.url != FRS_OFFICIAL_ARCHIVE_URL:
        raise ValueError("EPA FRS raw archive must identify the official national file")
    if raw_item.metadata.get("artifact_kind") != "upstream_official_archive":
        raise ValueError("EPA FRS raw archive has an invalid artifact kind")
    if raw_item.metadata.get("content_type") != "application/zip":
        raise ValueError("EPA FRS raw archive must declare application/zip")
    if metadata.get("filter_version") != FRS_FILTER_VERSION:
        raise ValueError("EPA FRS scope filter version does not match the compiled filter")
    if metadata.get("naics_codes") != list(FRS_NAICS_CODES):
        raise ValueError("EPA FRS scope NAICS codes do not match the compiled filter")
    if metadata.get("sic_codes") != list(FRS_SIC_CODES):
        raise ValueError("EPA FRS scope SIC codes do not match the compiled filter")
    if retrieval_timestamp_basis not in FRS_RETRIEVAL_TIMESTAMP_BASES:
        raise ValueError("EPA FRS snapshot has an invalid retrieval timestamp basis")
    if metadata.get("retrieval_timestamp_basis") != retrieval_timestamp_basis:
        raise ValueError("EPA FRS scope retrieval timestamp basis disagrees with the manifest")
    if metadata.get("data_as_of_basis") != FRS_DATA_AS_OF_BASIS:
        raise ValueError("EPA FRS scope has an invalid data_as_of basis")
    if metadata.get("data_as_of_source_url") != FRS_DATA_AS_OF_SOURCE_URL:
        raise ValueError("EPA FRS scope has an invalid data_as_of source URL")

    data_as_of = metadata.get("data_as_of")
    if not isinstance(data_as_of, str):
        raise ValueError("EPA FRS scope data_as_of is required")
    try:
        parsed_as_of = datetime.fromisoformat(data_as_of)
    except ValueError as error:
        raise ValueError("EPA FRS scope data_as_of must use YYYY-MM-DD") from error
    if "T" in data_as_of or parsed_as_of.date().isoformat() != data_as_of:
        raise ValueError("EPA FRS scope data_as_of must use YYYY-MM-DD")
    if parsed_as_of.date() > retrieved_timestamp.astimezone(UTC).date():
        raise ValueError("EPA FRS scope data_as_of must not be later than retrieved_at")
    if item.metadata.get("data_as_of") != data_as_of:
        raise ValueError("EPA FRS input data_as_of disagrees with its source scope")

    candidates = parse_candidate_jsonl(item.path)
    candidate_count = _scope_count(metadata, EPA_FRS_SCOPE, "candidate_count")
    if candidate_count != len(candidates):
        raise ValueError(
            "EPA FRS scope candidate_count does not match the filtered artifact"
        )
    total_rows = _positive_scope_count(metadata, "upstream_total_rows")
    if total_rows < candidate_count:
        raise ValueError("EPA FRS upstream row count is smaller than its candidate count")

    archive = metadata.get("upstream_archive")
    csv_metadata = metadata.get("upstream_csv")
    if not isinstance(archive, dict) or not isinstance(csv_metadata, dict):
        raise ValueError("EPA FRS scope requires upstream archive and CSV metadata")
    if archive.get("url") != FRS_OFFICIAL_ARCHIVE_URL:
        raise ValueError("EPA FRS upstream archive URL is not the official national file")
    archive_sha256 = _lower_sha256(
        archive.get("sha256"), "upstream archive SHA-256"
    )
    archive_bytes = _positive_scope_count(archive, "bytes")
    for field_name in ("etag", "last_modified"):
        value = archive.get(field_name)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"EPA FRS archive {field_name} must be text or null")
    if csv_metadata.get("member") != FRS_ARCHIVE_MEMBER:
        raise ValueError("EPA FRS scope identifies an unexpected CSV member")
    _lower_sha256(csv_metadata.get("sha256"), "upstream CSV SHA-256")
    _positive_scope_count(csv_metadata, "bytes")
    if _positive_scope_count(csv_metadata, "row_count") != total_rows:
        raise ValueError("EPA FRS upstream CSV row count disagrees with the scope")
    crc32 = csv_metadata.get("crc32")
    if (
        isinstance(crc32, bool)
        or not isinstance(crc32, int)
        or not 0 <= crc32 <= 0xFFFFFFFF
    ):
        raise ValueError("EPA FRS upstream CSV CRC32 is invalid")
    if metadata.get("documentation_member") != FRS_DOCUMENTATION_MEMBER:
        raise ValueError("EPA FRS scope identifies an unexpected documentation member")
    raw_retention = metadata.get("raw_retention")
    if not isinstance(raw_retention, dict):
        raise ValueError("EPA FRS scope must declare raw archive retention status")
    retained = raw_retention.get("retained_in_snapshot")
    if retained is not True:
        raise ValueError("EPA FRS raw archive must be retained in the snapshot")
    locator = raw_retention.get("blob_locator")
    if not isinstance(locator, str) or not locator.strip():
        raise ValueError("EPA FRS retained raw archive requires a blob locator")
    expected_locator = f"raw/sha256/{archive_sha256}.zip"
    if locator != expected_locator or raw_item.relative_path != expected_locator:
        raise ValueError("EPA FRS raw archive locator is not content-addressed")
    if raw_retention.get("reason") is not None:
        raise ValueError("EPA FRS retained raw archive must not declare a retention gap")
    if raw_retention.get("upstream_sha256") != archive_sha256:
        raise ValueError("EPA FRS raw retention digest disagrees with the upstream archive")
    if raw_retention.get("bytes") != archive_bytes:
        raise ValueError("EPA FRS raw retention byte count disagrees with the archive")
    if raw_item.sha256 != archive_sha256 or raw_item.size != archive_bytes:
        raise ValueError("EPA FRS raw input disagrees with upstream archive metadata")
    if raw_item.metadata.get("data_as_of") != data_as_of:
        raise ValueError("EPA FRS raw archive data_as_of disagrees with its scope")
    rights = metadata.get("rights")
    if not isinstance(rights, dict) or not rights:
        raise ValueError("EPA FRS scope must retain its bounded rights review")
    expected_rights = {
        "decision": "pass_for_exact_public_archive",
        "access_level": "public",
        "license_url": FRS_LICENSE_URL,
        "no_warranty": True,
        "scope": "exact_public_national_single_archive",
    }
    for key, expected in expected_rights.items():
        if rights.get(key) != expected:
            raise ValueError(
                f"complete EPA FRS scope has an invalid rights decision field: {key}"
            )
    reviewed_at = rights.get("reviewed_at")
    if not isinstance(reviewed_at, str):
        raise ValueError("complete EPA FRS scope requires a rights review date")
    try:
        if datetime.fromisoformat(reviewed_at).date().isoformat() != reviewed_at:
            raise ValueError
    except ValueError as error:
        raise ValueError(
            "complete EPA FRS scope rights reviewed_at must use YYYY-MM-DD"
        ) from error
    if metadata.get("complete") is not True:
        return
    if metadata.get("coverage") != EPA_FRS_COMPLETE_COVERAGE:
        raise ValueError(
            "complete EPA FRS scope must declare the exact national direct-code coverage"
        )
    if total_rows == candidate_count:
        raise ValueError(
            "complete EPA FRS filtered scope must identify the larger upstream population"
        )

    scan = scan_frs_archive(raw_item.path)
    if (
        scan.archive_sha256 != archive_sha256
        or scan.archive_bytes != archive_bytes
        or scan.csv_sha256 != csv_metadata.get("sha256")
        or scan.csv_bytes != csv_metadata.get("bytes")
        or scan.csv_crc32 != csv_metadata.get("crc32")
        or scan.row_count != total_rows
        or scan.candidate_count != candidate_count
    ):
        raise ValueError("EPA FRS retained raw archive semantics disagree with the scope")
    replay_raw = candidate_jsonl_bytes(scan)
    if (
        hashlib.sha256(replay_raw).hexdigest() != item.sha256
        or len(replay_raw) != item.size
    ):
        raise ValueError(
            "EPA FRS filtered derivative does not replay from the retained raw archive"
        )


def _snapshot_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("snapshot input path must be a non-empty string")
    relative_path = Path(relative)
    if relative_path.is_absolute() or any(
        part in {"", ".", ".."} for part in relative_path.parts
    ):
        raise ValueError(
            f"snapshot input escapes or has an invalid relative path: {relative}"
        )
    lexical = root / relative_path
    cursor = root
    for part in relative_path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"snapshot input must not use symlinks: {relative}")
    candidate = lexical.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"snapshot input escapes its directory: {relative}") from error
    return candidate


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def verify_snapshot(path: str | Path) -> VerifiedSnapshot:
    root = Path(path).resolve()
    manifest_path = root / "manifest.json"
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(manifest_path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            manifest_stat = os.fstat(stream.fileno())
            if not stat.S_ISREG(manifest_stat.st_mode):
                raise ValueError(
                    f"source snapshot manifest is not a regular file: {manifest_path}"
                )
            manifest_raw = stream.read()
        manifest = json.loads(manifest_raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read source snapshot manifest: {manifest_path}") from error
    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    manifest_size = len(manifest_raw)
    if not isinstance(manifest, dict) or manifest.get("format") != SOURCE_SNAPSHOT_FORMAT:
        raise ValueError(f"unsupported source snapshot format in {manifest_path}")
    retrieved_at = manifest.get("retrieved_at")
    if not isinstance(retrieved_at, str) or not retrieved_at:
        raise ValueError("source snapshot retrieved_at is required")
    try:
        retrieved_timestamp = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("source snapshot retrieved_at must be an ISO timestamp") from error
    if retrieved_timestamp.tzinfo is None or retrieved_timestamp.utcoffset() is None:
        raise ValueError("source snapshot retrieved_at must include a timezone")
    retrieved_at = retrieved_timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    raw_inputs = manifest.get("inputs")
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise ValueError("source snapshot inputs must be a non-empty list")
    raw_scopes = manifest.get("source_scopes", {})
    if not isinstance(raw_scopes, dict):
        raise ValueError("source snapshot source_scopes must be an object")
    source_scopes: dict[str, dict[str, Any]] = {}
    for scope, metadata in raw_scopes.items():
        if not isinstance(scope, str) or not scope or not isinstance(metadata, dict):
            raise ValueError("source snapshot contains invalid source scope metadata")
        if "complete" in metadata and not isinstance(metadata["complete"], bool):
            raise ValueError(f"source snapshot scope {scope} has a non-boolean complete flag")
        source_scopes[scope] = dict(metadata)

    inputs = []
    seen_paths: set[Path] = set()
    for index, raw in enumerate(raw_inputs):
        if not isinstance(raw, dict):
            raise ValueError(f"snapshot input {index} must be an object")
        relative_path = raw.get("path")
        input_path = _snapshot_path(root, relative_path)
        if input_path in seen_paths:
            raise ValueError(f"duplicate snapshot input path: {input_path.name}")
        seen_paths.add(input_path)
        if not input_path.is_file():
            raise ValueError(f"snapshot input is missing: {input_path}")
        record_type = raw.get("record_type")
        url = raw.get("url")
        expected_hash = raw.get("sha256")
        expected_size = raw.get("bytes")
        if not isinstance(record_type, str) or not record_type:
            raise ValueError(f"snapshot input {input_path.name} lacks record_type")
        parsed_url = urlparse(url) if isinstance(url, str) else None
        if parsed_url is None or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"snapshot input {input_path.name} lacks an absolute source URL")
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(character not in string.hexdigits for character in expected_hash)
            or expected_hash != expected_hash.lower()
        ):
            raise ValueError(f"snapshot input {input_path.name} lacks a SHA-256 digest")
        if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
            raise ValueError(f"snapshot input {input_path.name} has an invalid byte count")
        actual_hash, actual_size = _sha256_file(input_path)
        if actual_size != expected_size:
            raise ValueError(
                f"snapshot input size mismatch for {input_path.name}: "
                f"expected {expected_size}, found {actual_size}"
            )
        if actual_hash != expected_hash:
            raise ValueError(f"snapshot input hash mismatch for {input_path.name}")
        if record_type == "osm_overpass_snapshot":
            try:
                raw_bytes = input_path.read_bytes()
                osm_payload = json.loads(raw_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError(f"OSM snapshot input is not valid JSON: {input_path.name}") from error
            osm3s = osm_payload.get("osm3s") if isinstance(osm_payload, dict) else None
            osm_base = osm3s.get("timestamp_osm_base") if isinstance(osm3s, dict) else None
            if isinstance(osm_base, str) and osm_base:
                try:
                    osm_base_timestamp = datetime.fromisoformat(
                        osm_base.replace("Z", "+00:00")
                    )
                except ValueError as error:
                    raise ValueError(
                        f"OSM snapshot has an invalid timestamp_osm_base: {input_path.name}"
                    ) from error
                if (
                    osm_base_timestamp.tzinfo is None
                    or osm_base_timestamp.utcoffset() is None
                ):
                    raise ValueError(
                        f"OSM timestamp_osm_base lacks a timezone: {input_path.name}"
                    )
                if retrieved_timestamp < osm_base_timestamp.astimezone(UTC):
                    raise ValueError(
                        "source snapshot retrieved_at predates the included OSM base timestamp"
                    )
        metadata = {
            key: value
            for key, value in raw.items()
            if key not in {"path", "record_type", "url", "sha256", "bytes"}
        }
        inputs.append(
            SnapshotInput(
                path=input_path,
                relative_path=str(relative_path),
                record_type=record_type,
                url=url,
                sha256=expected_hash,
                size=expected_size,
                metadata=metadata,
            )
        )
    _validate_nist_scope(inputs, source_scopes)
    _validate_epa_frs_scope(
        inputs,
        source_scopes,
        retrieved_timestamp=retrieved_timestamp,
        retrieval_timestamp_basis=manifest.get("retrieval_timestamp_basis"),
    )
    return VerifiedSnapshot(
        root,
        manifest_sha256,
        manifest_size,
        retrieved_at,
        tuple(inputs),
        source_scopes,
    )
