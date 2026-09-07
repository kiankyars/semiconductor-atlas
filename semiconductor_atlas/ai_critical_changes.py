"""Deterministic, fail-closed comparisons of AI-critical v1 releases."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import os
import re
import stat
import tarfile
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .ai_critical import (
    _check_directory_identity,
    _manifest_payload,
    _open_real_directory_fd,
    _release_files,
    ensure_real_directory,
    install_directory_exclusive,
    install_file_exclusive,
    load_baseline,
    materialize_baseline,
    validate_release,
)
from .ai_critical_change_claims import validate_claim_snapshot


CHANGE_BUNDLE_FORMAT = "semiconductor-atlas-ai-critical-change-bundle-v1"
CHANGE_RECORD_FORMAT = "semiconductor-atlas-ai-critical-change-v1"
ALERT_PROPOSAL_FORMAT = "semiconductor-atlas-ai-critical-alert-proposal-v1"
SUMMARY_FORMAT = "semiconductor-atlas-ai-critical-change-summary-v1"
CHANGE_SCHEMA_VERSION = "ai-critical-change-schema-v1"
SERIES_RULE_VERSION = "ai-critical-semantic-series-v1"
ALERT_RULE_VERSION = "ai-critical-alert-proposal-rules-v1"
CAPACITY_REVISION_THRESHOLD = Fraction(3, 20)

CHANGE_STATUSES = (
    "reaffirmed",
    "revised",
    "added",
    "not_carried_forward",
)
ALERT_RULE_IDS = (
    "capability_readiness_change",
    "capacity_evidence_added",
    "capacity_revision_15_percent",
    "geography_point_change",
    "lifecycle_positive_stage_change",
    "lifecycle_side_state",
)
POSITIVE_LIFECYCLE_STAGES = (
    "lead",
    "announced",
    "site_control",
    "permitting",
    "permitted",
    "site_preparation",
    "civil_works",
    "shell",
    "cleanroom_fitout",
    "utilities_ready",
    "tools_installing",
    "commissioning",
    "customer_qualification",
    "ramping",
    "complete",
)
LIFECYCLE_SIDE_STATES = frozenset(
    {"paused", "cancelled", "redesigned", "repurposed", "decommissioned"}
)
_POSITIVE_STAGE_RANK = {
    stage: rank for rank, stage in enumerate(POSITIVE_LIFECYCLE_STAGES)
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_BUNDLE_ID_RE = re.compile(r"ai-critical-change-[0-9a-f]{32}\Z")
_MANAGED_FILES = frozenset(
    {"changes.jsonl", "alert_proposals.jsonl", "summary.json", "README.md"}
)
_HISTORICAL_RENDER_FILES = frozenset({"METHODOLOGY.md", "README.md"})
_NOT_CARRIED_FORWARD_POLICY = (
    "A claim not carried forward is not negative evidence that the asserted fact, "
    "capacity, capability, or activity ceased to exist, and it cannot generate an "
    "alert proposal."
)
_STATUS_INTERPRETATIONS = {
    "reaffirmed": "The current release reaffirms the same semantic assertion.",
    "revised": "The current release revises the prior semantic assertion.",
    "added": "The current release adds evidence for this semantic series.",
    "not_carried_forward": _NOT_CARRIED_FORWARD_POLICY,
}


@dataclass(frozen=True, slots=True)
class _LoadedRelease:
    path: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str
    claims: tuple[Mapping[str, Any], ...]


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json(raw: bytes, context: str) -> Any:
    try:
        return json.loads(
            raw,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{context} is not strict UTF-8 JSON") from error


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _pretty_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Iterable[object]) -> bytes:
    return b"".join(_canonical_bytes(row) for row in rows)


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def _list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _integer(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return value


def _count_map(
    value: object,
    keys: Sequence[str],
    context: str,
) -> dict[str, int]:
    counts = _mapping(value, context)
    _require_keys(counts, keys, context)
    return {
        key: _integer(counts[key], f"{context}.{key}")
        for key in keys
    }


def _sha256(value: object, context: str) -> str:
    result = _text(value, context)
    if not _SHA256_RE.fullmatch(result):
        raise ValueError(f"{context} must be a lowercase SHA-256")
    return result


def _iso_date(value: object, context: str) -> str:
    result = _text(value, context)
    try:
        parsed = date.fromisoformat(result)
    except ValueError as error:
        raise ValueError(f"{context} must be an ISO date") from error
    if parsed.isoformat() != result:
        raise ValueError(f"{context} must use canonical YYYY-MM-DD")
    return result


def _iso_timestamp(value: object, context: str) -> str:
    result = _text(value, context)
    if not result.endswith("Z"):
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{context} must be a canonical UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    canonical = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if canonical != result:
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    return result


def _require_keys(
    value: Mapping[str, Any], required: Sequence[str], context: str
) -> None:
    expected = set(required)
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise ValueError(f"{context} has an invalid schema: {'; '.join(details)}")


def _file_metadata(raw: bytes) -> dict[str, Any]:
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _directory_identity(descriptor: int) -> tuple[int, int]:
    status = os.fstat(descriptor)
    return status.st_dev, status.st_ino


def _read_regular_file_at(
    directory_descriptor: int,
    name: str,
    context: str,
) -> bytes:
    if Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"{context} has an unsafe filename")
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as error:
        raise ValueError(f"{context} must be a regular non-symlink file") from error
    try:
        status_before = os.fstat(descriptor)
        if not stat.S_ISREG(status_before.st_mode):
            raise ValueError(f"{context} must be a regular non-symlink file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        status_after = os.fstat(descriptor)
        identity = (status_before.st_dev, status_before.st_ino)
        if (
            (status_after.st_dev, status_after.st_ino) != identity
            or status_after.st_size != status_before.st_size
        ):
            raise OSError(f"{context} identity changed while reading")
        raw = b"".join(chunks)
        if len(raw) != status_after.st_size:
            raise OSError(f"{context} size changed while reading")
    finally:
        os.close(descriptor)
    try:
        named_status = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise OSError(f"{context} pathname changed while reading") from error
    if (
        named_status.st_dev,
        named_status.st_ino,
    ) != identity or not stat.S_ISREG(named_status.st_mode):
        raise OSError(f"{context} pathname changed while reading")
    return raw


def _snapshot_open_directory(
    directory_descriptor: int,
    context: str,
) -> dict[str, bytes]:
    names_before = sorted(os.listdir(directory_descriptor))
    if len(names_before) != len(set(names_before)):
        raise ValueError(f"{context} contains duplicate filenames")
    snapshot = {
        name: _read_regular_file_at(
            directory_descriptor,
            name,
            f"{context} {name}",
        )
        for name in names_before
    }
    if sorted(os.listdir(directory_descriptor)) != names_before:
        raise OSError(f"{context} entries changed while reading")
    return snapshot


def _check_path_identity(
    path: Path,
    expected_identity: tuple[int, int],
    context: str,
) -> None:
    _, descriptor = _open_real_directory_fd(path, context, create=False)
    try:
        _check_directory_identity(descriptor, expected_identity, context)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    _, descriptor = _open_real_directory_fd(
        path,
        "directory durability sync",
        create=False,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_fsynced_file(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _resolved_path(path_value: str | Path, *, strict: bool) -> Path:
    return Path(path_value).resolve(strict=strict)


def _require_outside_directories(
    path_value: str | Path,
    directories: Iterable[str | Path],
    context: str,
) -> None:
    target = _resolved_path(path_value, strict=False)
    for directory_value in directories:
        directory = _resolved_path(directory_value, strict=True)
        try:
            target.relative_to(directory)
        except ValueError:
            continue
        raise ValueError(f"{context} must be outside input release: {directory}")


def validate_change_output_locations(
    prior_dir: str | Path,
    current_dir: str | Path,
    output_dir: str | Path,
    archive_path: str | Path | None = None,
) -> None:
    """Reject output locations that could mutate either immutable input release."""

    protected = (prior_dir, current_dir)
    output = Path(output_dir)
    _require_outside_directories(output, protected, "change bundle output")
    if output.is_symlink() or output.exists():
        raise ValueError("change bundle output must not already exist")
    if archive_path is None:
        return
    archive = Path(archive_path)
    _require_outside_directories(archive, protected, "change archive output")
    try:
        archive.resolve(strict=False).relative_to(output.resolve(strict=False))
    except ValueError:
        pass
    else:
        raise ValueError("change archive output must be outside the change bundle")
    if archive.is_symlink() or archive.exists():
        raise ValueError("change archive output must not already exist")
    if archive.suffixes[-2:] != [".tar", ".gz"]:
        raise ValueError("change archive output must end in .tar.gz")


def _jsonl_rows(raw: bytes, context: str) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line:
            raise ValueError(f"{context}:{line_number} must not be blank")
        rows.append(
            _mapping(_strict_json(line, f"{context}:{line_number}"), f"{context}:{line_number}")
        )
    if _jsonl_bytes(rows) != raw:
        raise ValueError(f"{context} is not exact canonical JSONL")
    return rows


def _release_binding(release: _LoadedRelease) -> dict[str, str]:
    return {
        "release_id": _text(release.manifest.get("release_id"), "release_id"),
        "as_of": _iso_date(release.manifest.get("as_of"), "as_of"),
        "recorded_at": _iso_timestamp(
            release.manifest.get("recorded_at"), "recorded_at"
        ),
        "manifest_sha256": release.manifest_sha256,
    }


def _validate_historical_document_rendering(
    snapshot: Mapping[str, bytes],
    context: str,
) -> dict[str, Any]:
    """Replay structured bytes while normalizing only manifest-bound prose files."""

    original_manifest_raw = snapshot.get("manifest.json")
    if original_manifest_raw is None:
        raise ValueError(f"{context} is missing manifest.json")
    original_manifest = _mapping(
        _strict_json(original_manifest_raw, f"{context} manifest"),
        f"{context} manifest",
    )
    with tempfile.TemporaryDirectory(
        prefix="semiconductor-atlas-release-compat-"
    ) as temporary_name:
        temporary = Path(temporary_name)
        for name, raw in snapshot.items():
            (temporary / name).write_bytes(raw)
        cohort = load_baseline(
            temporary / "cohort.json",
            temporary,
            verify_source_bytes=False,
            atlas_template_path=temporary / "atlas-template.html",
            code_path=temporary / "BUILD_ai_critical.py",
            builder_script_path=temporary / "BUILD_build_ai_critical_release.py",
            atlas_generator_path=temporary / "BUILD_generate_atlas.py",
        )
        materialized = materialize_baseline(cohort)
        normalized_files = _release_files(cohort, materialized)
        for name, expected in normalized_files.items():
            if name in _HISTORICAL_RENDER_FILES:
                (temporary / name).write_bytes(expected)
            elif snapshot.get(name) != expected:
                raise ValueError(
                    f"{context} structured content is not replayable: {name}"
                )
        from web.generate_atlas import load_geojson, render

        expected_html = render(
            load_geojson(temporary / "atlas.geojson"),
            (temporary / "atlas-template.html").read_text(encoding="utf-8"),
        ).encode("utf-8")
        if snapshot.get("atlas.html") != expected_html:
            raise ValueError(f"{context} atlas HTML is not exactly replayable")
        normalized_manifest = _manifest_payload(
            cohort,
            materialized,
            normalized_files,
            atlas_html=expected_html,
        )
        (temporary / "manifest.json").write_bytes(
            _pretty_bytes(normalized_manifest)
        )
        if validate_release(temporary, require_html=True) != normalized_manifest:
            raise ValueError(f"{context} normalized replay validation failed")
    original_expected = deepcopy(normalized_manifest)
    original_files = _mapping(
        original_manifest.get("files"),
        f"{context} manifest files",
    )
    for name in _HISTORICAL_RENDER_FILES:
        original_expected["files"][name] = deepcopy(original_files.get(name))
    if original_manifest_raw != _pretty_bytes(original_expected):
        raise ValueError(
            f"{context} historical rendering differs beyond manifest-bound prose"
        )
    return dict(original_manifest)


def _validate_release_for_comparison(
    path: Path,
    snapshot: Mapping[str, bytes],
    context: str,
) -> dict[str, Any]:
    try:
        return validate_release(path, require_html=True)
    except ValueError as error:
        prefix = "managed release content is inconsistent with cohort: "
        message = str(error)
        if not message.startswith(prefix) or message.removeprefix(
            prefix
        ) not in _HISTORICAL_RENDER_FILES:
            raise
    return _validate_historical_document_rendering(snapshot, context)


def _load_release(path_value: str | Path, context: str) -> _LoadedRelease:
    path, directory_descriptor = _open_real_directory_fd(
        path_value,
        context,
        create=False,
    )
    directory_identity = _directory_identity(directory_descriptor)
    try:
        before = _snapshot_open_directory(directory_descriptor, context)
        manifest = _validate_release_for_comparison(path, before, context)
        _check_path_identity(path, directory_identity, context)
        after = _snapshot_open_directory(directory_descriptor, context)
        if before != after:
            raise OSError(f"{context} changed during release validation")
        repeated_manifest = _validate_release_for_comparison(path, after, context)
        _check_path_identity(path, directory_identity, context)
        final = _snapshot_open_directory(directory_descriptor, context)
        if final != before:
            raise OSError(f"{context} changed during repeated release validation")
    finally:
        os.close(directory_descriptor)
    manifest_raw = before.get("manifest.json")
    if manifest_raw is None or manifest_raw != _pretty_bytes(manifest):
        raise ValueError(f"{context} manifest changed during validation")
    if _pretty_bytes(repeated_manifest) != manifest_raw:
        raise ValueError(f"{context} validation result is not repeatable")
    manifest_files = _mapping(manifest.get("files"), f"{context} manifest files")
    if set(before) != set(manifest_files) | {"manifest.json"}:
        raise ValueError(f"{context} has missing or unmanaged files")
    for name, metadata_value in manifest_files.items():
        metadata = _mapping(metadata_value, f"{context} manifest files.{name}")
        if metadata.get("bytes") != len(before[name]) or metadata.get(
            "sha256"
        ) != hashlib.sha256(before[name]).hexdigest():
            raise ValueError(f"{context} managed file changed after validation: {name}")
    claims_raw = before.get("claims.jsonl")
    if claims_raw is None:
        raise ValueError(f"{context} is missing claims.jsonl")
    claims = tuple(_jsonl_rows(claims_raw, f"{context} claims.jsonl"))
    return _LoadedRelease(
        path=path,
        manifest=manifest,
        manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        claims=claims,
    )


def _validate_release_order(
    prior: Mapping[str, str], current: Mapping[str, str]
) -> None:
    if prior["release_id"] == current["release_id"]:
        raise ValueError("prior and current release IDs must be different")
    prior_recorded = datetime.fromisoformat(
        prior["recorded_at"].replace("Z", "+00:00")
    )
    current_recorded = datetime.fromisoformat(
        current["recorded_at"].replace("Z", "+00:00")
    )
    if current_recorded <= prior_recorded:
        raise ValueError("current recorded_at must be strictly later than prior recorded_at")
    if date.fromisoformat(current["as_of"]) < date.fromisoformat(prior["as_of"]):
        raise ValueError("current as_of must not precede prior as_of")


def _string_array(value: object, context: str) -> list[str]:
    result = _list(value, context)
    if any(not isinstance(item, str) or not item for item in result):
        raise ValueError(f"{context} must contain non-empty strings")
    return list(result)


def _series_descriptor(claim: Mapping[str, Any]) -> dict[str, Any]:
    subject_entity_id = _text(
        claim.get("subject_entity_id"), "claim subject_entity_id"
    )
    predicate = _text(claim.get("predicate"), "claim predicate")
    value_kind = _text(claim.get("value_kind"), "claim value_kind")
    value = _mapping(claim.get("value"), "claim value")
    dimensions: dict[str, Any] = {}
    if value_kind in {"scalar", "geometry"}:
        pass
    elif value_kind == "capability":
        dimensions = {
            "category": _text(value.get("category"), "capability category"),
            "technology": _text(
                value.get("technology"),
                "capability technology",
            ),
            "valid_from": _iso_date(
                claim.get("valid_from"),
                "capability valid_from",
            ),
        }
    elif value_kind == "capacity":
        technology_scope = _string_array(
            value.get("technology_scope"), "capacity technology_scope"
        )
        if len(set(technology_scope)) != len(technology_scope):
            raise ValueError("capacity technology_scope must not contain duplicates")
        dimensions = {
            "metric": _text(value.get("metric"), "capacity metric"),
            "basis": _text(value.get("basis"), "capacity basis"),
            "unit": _text(value.get("unit"), "capacity unit"),
            "scope_kind": _text(value.get("scope_kind"), "capacity scope_kind"),
            "input_output_basis": _text(
                value.get("input_output_basis"), "capacity input_output_basis"
            ),
            "quantity_semantics": _text(
                value.get("quantity_semantics"), "capacity quantity_semantics"
            ),
            "period_start": (
                None
                if value.get("period_start") is None
                else _iso_date(value.get("period_start"), "capacity period_start")
            ),
            "period_end": (
                None
                if value.get("period_end") is None
                else _iso_date(value.get("period_end"), "capacity period_end")
            ),
            "technology_scope": sorted(technology_scope),
            "valid_from": _iso_date(
                claim.get("valid_from"),
                "capacity valid_from",
            ),
        }
    else:
        raise ValueError(f"unsupported AI-critical claim value kind: {value_kind}")
    return {
        "subject_entity_id": subject_entity_id,
        "predicate": predicate,
        "value_kind": value_kind,
        "dimensions": dimensions,
    }


def _semantic_series_id(descriptor: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_bytes(
            {"series_rule_version": SERIES_RULE_VERSION, "series": descriptor}
        )
    ).hexdigest()


def _claim_snapshot(claim: Mapping[str, Any]) -> dict[str, Any]:
    evidence_ids = _string_array(claim.get("evidence_ids"), "claim evidence_ids")
    evidence_links_value = _list(claim.get("evidence_links"), "claim evidence_links")
    evidence_links: list[dict[str, str]] = []
    for index, link_value in enumerate(evidence_links_value):
        link = _mapping(link_value, f"claim evidence_links[{index}]")
        _require_keys(
            link,
            ("evidence_id", "role", "fragment_sha256"),
            f"claim evidence_links[{index}]",
        )
        evidence_links.append(
            {
                "evidence_id": _text(
                    link.get("evidence_id"), f"claim evidence_links[{index}].evidence_id"
                ),
                "role": _text(link.get("role"), f"claim evidence_links[{index}].role"),
                "fragment_sha256": _sha256(
                    link.get("fragment_sha256"),
                    f"claim evidence_links[{index}].fragment_sha256",
                ),
            }
        )
    if [link["evidence_id"] for link in evidence_links] != evidence_ids:
        raise ValueError("claim evidence links do not match evidence IDs")
    valid_to = claim.get("valid_to")
    if valid_to is not None:
        valid_to = _iso_date(valid_to, "claim valid_to")
    notes = claim.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise ValueError("claim notes must be a string or null")
    return {
        "claim_id": _text(claim.get("claim_id"), "claim_id"),
        "subject_entity_id": _text(
            claim.get("subject_entity_id"), "claim subject_entity_id"
        ),
        "subject_stable_key": _text(
            claim.get("subject_stable_key"), "claim subject_stable_key"
        ),
        "predicate": _text(claim.get("predicate"), "claim predicate"),
        "value_kind": _text(claim.get("value_kind"), "claim value_kind"),
        "value": deepcopy(claim.get("value")),
        "valid_from": _iso_date(claim.get("valid_from"), "claim valid_from"),
        "valid_to": valid_to,
        "recorded_at": _iso_timestamp(claim.get("recorded_at"), "claim recorded_at"),
        "claim_kind": _text(claim.get("claim_kind"), "claim claim_kind"),
        "method": _text(claim.get("method"), "claim method"),
        "notes": notes,
        "evidence_ids": evidence_ids,
        "evidence_links": evidence_links,
    }


def _index_claims(
    release: _LoadedRelease, context: str
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    indexed: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    release_recorded_at = _text(release.manifest.get("recorded_at"), "recorded_at")
    for claim in release.claims:
        snapshot = _claim_snapshot(claim)
        if snapshot["recorded_at"] != release_recorded_at:
            raise ValueError(f"{context} claim recorded_at does not match its release")
        descriptor = _series_descriptor(claim)
        series_id = _semantic_series_id(descriptor)
        if series_id in indexed:
            raise ValueError(f"{context} has a duplicate semantic series: {series_id}")
        indexed[series_id] = (descriptor, snapshot)
    return indexed


def _assertion(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "value_kind": snapshot["value_kind"],
        "value": snapshot["value"],
        "valid_from": snapshot["valid_from"],
        "valid_to": snapshot["valid_to"],
    }


def _change_status(
    prior: Mapping[str, Any] | None, current: Mapping[str, Any] | None
) -> str:
    if prior is None:
        return "added"
    if current is None:
        return "not_carried_forward"
    return "reaffirmed" if _assertion(prior) == _assertion(current) else "revised"


def _changes(
    prior_index: Mapping[str, tuple[dict[str, Any], dict[str, Any]]],
    current_index: Mapping[str, tuple[dict[str, Any], dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for series_id in sorted(set(prior_index) | set(current_index)):
        prior_entry = prior_index.get(series_id)
        current_entry = current_index.get(series_id)
        descriptor = deepcopy((current_entry or prior_entry)[0])  # type: ignore[index]
        if prior_entry and current_entry and prior_entry[0] != current_entry[0]:
            raise ValueError(f"semantic series hash collision: {series_id}")
        prior_claim = deepcopy(prior_entry[1]) if prior_entry else None
        current_claim = deepcopy(current_entry[1]) if current_entry else None
        status = _change_status(prior_claim, current_claim)
        subject = current_claim or prior_claim
        assert subject is not None
        rows.append(
            {
                "format": CHANGE_RECORD_FORMAT,
                "series_id": series_id,
                "series": descriptor,
                "subject_entity_id": subject["subject_entity_id"],
                "subject_stable_key": subject["subject_stable_key"],
                "predicate": subject["predicate"],
                "value_kind": subject["value_kind"],
                "status": status,
                "prior_claim": prior_claim,
                "current_claim": current_claim,
                "interpretation": _STATUS_INTERPRETATIONS[status],
                "negative_evidence": False,
                "alert_proposal_fingerprints": [],
            }
        )
    return rows


def _claim_fingerprint_binding(
    snapshot: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    if snapshot is None:
        return None
    return {
        "claim_id": str(snapshot["claim_id"]),
        "snapshot_sha256": hashlib.sha256(_canonical_bytes(snapshot)).hexdigest(),
    }


def _evidence_lineage_side(
    release: Mapping[str, str], snapshot: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "release_id": release["release_id"],
        "manifest_sha256": release["manifest_sha256"],
        "claim_id": snapshot["claim_id"],
        "evidence_links": deepcopy(snapshot["evidence_links"]),
    }


def _proposal(
    change: Mapping[str, Any],
    prior_release: Mapping[str, str],
    current_release: Mapping[str, str],
    *,
    rule_id: str,
    reason: Mapping[str, Any],
) -> dict[str, Any]:
    prior_claim = change["prior_claim"]
    current_claim = change["current_claim"]
    fingerprint_input = {
        "rule_version": ALERT_RULE_VERSION,
        "rule_id": rule_id,
        "prior_manifest_sha256": prior_release["manifest_sha256"],
        "current_manifest_sha256": current_release["manifest_sha256"],
        "series_id": change["series_id"],
        "prior_claim": _claim_fingerprint_binding(prior_claim),
        "current_claim": _claim_fingerprint_binding(current_claim),
    }
    fingerprint = hashlib.sha256(_canonical_bytes(fingerprint_input)).hexdigest()
    return {
        "format": ALERT_PROPOSAL_FORMAT,
        "fingerprint": fingerprint,
        "rule_id": rule_id,
        "rule_version": ALERT_RULE_VERSION,
        "alert_type": rule_id,
        "series_id": change["series_id"],
        "subject_entity_id": change["subject_entity_id"],
        "subject_stable_key": change["subject_stable_key"],
        "predicate": change["predicate"],
        "change_status": change["status"],
        "prior_claim_id": None if prior_claim is None else prior_claim["claim_id"],
        "current_claim_id": None if current_claim is None else current_claim["claim_id"],
        "evidence_lineage": {
            "prior": _evidence_lineage_side(prior_release, prior_claim),
            "current": _evidence_lineage_side(current_release, current_claim),
        },
        "reason": deepcopy(reason),
        "confidence": None,
        "confidence_scope": "unknown_not_calibrated",
        "delivery_eligible": False,
        "delivery_ineligibility_reason": "proposal_only_not_production_alert",
    }


def _scalar_value(snapshot: Mapping[str, Any]) -> Any:
    return _mapping(snapshot["value"], "scalar claim value").get("value")


def _fraction(value: object, context: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a finite JSON number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{context} must be a finite JSON number")
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{context} must be a finite JSON number") from error


def _capacity_relative_changes(
    prior_value: Mapping[str, Any], current_value: Mapping[str, Any]
) -> dict[str, Fraction]:
    changes: dict[str, Fraction] = {}
    for field in ("low", "base", "high"):
        prior = _fraction(prior_value.get(field), f"prior capacity {field}")
        current = _fraction(current_value.get(field), f"current capacity {field}")
        denominator = prior if prior else current
        changes[field] = Fraction(0) if not denominator else (current - prior) / denominator
    return changes


def _fraction_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def _capability_observation_key(change: Mapping[str, Any]) -> tuple[str, str, str]:
    dimensions = change["series"]["dimensions"]
    return (
        change["subject_entity_id"],
        dimensions["category"],
        dimensions["technology"],
    )


def _derive_alert_proposals(
    changes: list[dict[str, Any]],
    prior_release: Mapping[str, str],
    current_release: Mapping[str, str],
) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    capability_observations: dict[
        tuple[str, str, str],
        dict[str, list[tuple[dict[str, Any], Mapping[str, Any]]]],
    ] = {}
    for change in changes:
        if (
            change["predicate"] != "facility.capability"
            or change["value_kind"] != "capability"
        ):
            continue
        observations = capability_observations.setdefault(
            _capability_observation_key(change),
            {"prior_claim": [], "current_claim": []},
        )
        for side in ("prior_claim", "current_claim"):
            if change[side] is not None:
                observations[side].append((change, change[side]))
    for change in changes:
        change["alert_proposal_fingerprints"] = []
        status = change["status"]
        if status in {"reaffirmed", "not_carried_forward"}:
            continue
        prior_claim = change["prior_claim"]
        current_claim = change["current_claim"]
        predicate = change["predicate"]
        proposal: dict[str, Any] | None = None
        if predicate == "lifecycle.stage" and status == "revised":
            assert prior_claim is not None and current_claim is not None
            prior_stage = _text(_scalar_value(prior_claim), "prior lifecycle stage")
            current_stage = _text(_scalar_value(current_claim), "current lifecycle stage")
            if current_stage in LIFECYCLE_SIDE_STATES and current_stage != prior_stage:
                proposal = _proposal(
                    change,
                    prior_release,
                    current_release,
                    rule_id="lifecycle_side_state",
                    reason={"prior_stage": prior_stage, "current_stage": current_stage},
                )
            elif current_stage in _POSITIVE_STAGE_RANK and current_stage != prior_stage:
                prior_rank = _POSITIVE_STAGE_RANK.get(prior_stage)
                current_rank = _POSITIVE_STAGE_RANK[current_stage]
                direction = (
                    "return_to_positive"
                    if prior_rank is None
                    else "advanced"
                    if current_rank > prior_rank
                    else "earlier_stage"
                )
                proposal = _proposal(
                    change,
                    prior_release,
                    current_release,
                    rule_id="lifecycle_positive_stage_change",
                    reason={
                        "prior_stage": prior_stage,
                        "current_stage": current_stage,
                        "direction": direction,
                    },
                )
        elif predicate == "facility.capability" and status == "revised":
            assert prior_claim is not None and current_claim is not None
            prior_value = _mapping(prior_claim["value"], "prior capability value")
            current_value = _mapping(current_claim["value"], "current capability value")
            if prior_value.get("readiness") != current_value.get("readiness"):
                proposal = _proposal(
                    change,
                    prior_release,
                    current_release,
                    rule_id="capability_readiness_change",
                    reason={
                        "category": current_value.get("category"),
                        "prior_readiness": prior_value.get("readiness"),
                        "current_readiness": current_value.get("readiness"),
                    },
                )
        elif predicate == "facility.capability" and status == "added":
            assert current_claim is not None
            observations = capability_observations[_capability_observation_key(change)]
            if (
                len(observations["prior_claim"]) == 1
                and len(observations["current_claim"]) == 1
            ):
                prior_change, prior_observation = observations["prior_claim"][0]
                prior_value = _mapping(prior_observation["value"], "prior capability value")
                current_value = _mapping(current_claim["value"], "current capability value")
                if (
                    prior_observation["valid_to"] is None
                    and current_claim["valid_from"] > prior_observation["valid_from"]
                    and prior_value.get("readiness") != current_value.get("readiness")
                ):
                    proposal = _proposal(
                        {**change, "prior_claim": prior_observation},
                        prior_release,
                        current_release,
                        rule_id="capability_readiness_change",
                        reason={
                            "comparison": "unambiguous_later_effective_observation",
                            "category": current_value.get("category"),
                            "technology": current_value.get("technology"),
                            "prior_readiness": prior_value.get("readiness"),
                            "current_readiness": current_value.get("readiness"),
                            "prior_series_id": prior_change["series_id"],
                            "prior_valid_from": prior_observation["valid_from"],
                            "current_valid_from": current_claim["valid_from"],
                        },
                    )
        elif change["value_kind"] == "capacity":
            if status == "added":
                assert current_claim is not None
                proposal = _proposal(
                    change,
                    prior_release,
                    current_release,
                    rule_id="capacity_evidence_added",
                    reason={
                        "comparison": "none_added_evidence",
                        "current_capacity": deepcopy(current_claim["value"]),
                    },
                )
            elif status == "revised":
                assert prior_claim is not None and current_claim is not None
                prior_value = _mapping(prior_claim["value"], "prior capacity value")
                current_value = _mapping(current_claim["value"], "current capacity value")
                relative_changes = _capacity_relative_changes(
                    prior_value,
                    current_value,
                )
                crossed_fields = [
                    field
                    for field in ("low", "base", "high")
                    if abs(relative_changes[field]) >= CAPACITY_REVISION_THRESHOLD
                ]
                if crossed_fields:
                    proposal = _proposal(
                        change,
                        prior_release,
                        current_release,
                        rule_id="capacity_revision_15_percent",
                        reason={
                            "comparison_fields": ["low", "base", "high"],
                            "crossed_fields": crossed_fields,
                            "prior_values": {
                                field: prior_value.get(field)
                                for field in ("low", "base", "high")
                            },
                            "current_values": {
                                field: current_value.get(field)
                                for field in ("low", "base", "high")
                            },
                            "relative_change_ratios": {
                                field: _fraction_text(relative_changes[field])
                                for field in ("low", "base", "high")
                            },
                            "threshold_ratio": _fraction_text(
                                CAPACITY_REVISION_THRESHOLD
                            ),
                        },
                    )
        elif predicate == "geography.point" and status in {"added", "revised"}:
            if current_claim is not None and (
                prior_claim is None or prior_claim["value"] != current_claim["value"]
            ):
                proposal = _proposal(
                    change,
                    prior_release,
                    current_release,
                    rule_id="geography_point_change",
                    reason={
                        "prior_point": None if prior_claim is None else deepcopy(prior_claim["value"]),
                        "current_point": deepcopy(current_claim["value"]),
                    },
                )
        if proposal is not None:
            proposals.append(proposal)
            change["alert_proposal_fingerprints"].append(proposal["fingerprint"])
    proposals.sort(key=lambda row: row["fingerprint"])
    for change in changes:
        change["alert_proposal_fingerprints"].sort()
    return proposals


def _summary(
    changes: Sequence[Mapping[str, Any]],
    proposals: Sequence[Mapping[str, Any]],
    prior_release: Mapping[str, str],
    current_release: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "format": SUMMARY_FORMAT,
        "prior_release": dict(prior_release),
        "current_release": dict(current_release),
        "change_count": len(changes),
        "changes_by_status": {
            status: sum(1 for row in changes if row["status"] == status)
            for status in CHANGE_STATUSES
        },
        "alert_proposal_count": len(proposals),
        "alerts_by_rule": {
            rule_id: sum(1 for row in proposals if row["rule_id"] == rule_id)
            for rule_id in ALERT_RULE_IDS
        },
        "not_carried_forward_policy": _NOT_CARRIED_FORWARD_POLICY,
    }


def _manifest_metadata(
    prior_release: Mapping[str, str], current_release: Mapping[str, str]
) -> dict[str, Any]:
    identity = {
        "schema_version": CHANGE_SCHEMA_VERSION,
        "series_rule_version": SERIES_RULE_VERSION,
        "rule_version": ALERT_RULE_VERSION,
        "prior_release": dict(prior_release),
        "current_release": dict(current_release),
    }
    digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    return {
        "format": CHANGE_BUNDLE_FORMAT,
        "bundle_id": f"ai-critical-change-{digest[:32]}",
        **identity,
    }


def compare_releases(
    prior_dir: str | Path, current_dir: str | Path
) -> dict[str, Any]:
    """Compare two fully validated chronological AI-critical v1 releases."""

    prior = _load_release(prior_dir, "prior release")
    current = _load_release(current_dir, "current release")
    prior_binding = _release_binding(prior)
    current_binding = _release_binding(current)
    _validate_release_order(prior_binding, current_binding)
    changes = _changes(
        _index_claims(prior, "prior release"),
        _index_claims(current, "current release"),
    )
    proposals = _derive_alert_proposals(changes, prior_binding, current_binding)
    return {
        "changes": changes,
        "alert_proposals": proposals,
        "summary": _summary(changes, proposals, prior_binding, current_binding),
        "manifest": _manifest_metadata(prior_binding, current_binding),
    }


def _readme_text(
    metadata: Mapping[str, Any], summary: Mapping[str, Any]
) -> str:
    return f"""# AI-Critical Release Change Bundle

This deterministic bundle compares two validated AI-critical manufacturing v1 releases.

- Prior release: `{metadata['prior_release']['release_id']}`
- Current release: `{metadata['current_release']['release_id']}`
- Semantic series: {summary['change_count']}
- Alert proposals: {summary['alert_proposal_count']}
- Alert rule version: `{metadata['rule_version']}`

`changes.jsonl` records every semantic series in the union. A `not_carried_forward`
record is explicitly not negative evidence and cannot generate an alert proposal.
Capability series bind category, technology, and effective date. Capacity series bind
basis, unit, accounting scope, period, technology scope, and effective date. Material
capacity proposals compare low, base, and high bounds with exact rational arithmetic.

`alert_proposals.jsonl` contains deterministic review proposals, not production alerts.
Confidence is uncalibrated and null, and every proposal has `delivery_eligible: false`.

`summary.json` contains exact counts. `manifest.json` binds both source manifest byte
hashes and hashes every managed file in this bundle.
"""


def _bundle_payload(result: Mapping[str, Any]) -> tuple[dict[str, bytes], dict[str, Any]]:
    summary = _mapping(result["summary"], "comparison summary")
    metadata = _mapping(result["manifest"], "comparison manifest metadata")
    files = {
        "changes.jsonl": _jsonl_bytes(result["changes"]),
        "alert_proposals.jsonl": _jsonl_bytes(result["alert_proposals"]),
        "summary.json": _pretty_bytes(summary),
        "README.md": _readme_text(metadata, summary).encode("utf-8"),
    }
    manifest = {
        **dict(metadata),
        "change_count": summary["change_count"],
        "changes_by_status": deepcopy(summary["changes_by_status"]),
        "alert_proposal_count": summary["alert_proposal_count"],
        "alerts_by_rule": deepcopy(summary["alerts_by_rule"]),
        "stable_sort_rules": {
            "changes": "series_id",
            "alert_proposals": "fingerprint",
            "files": "filename_codepoint_order",
        },
        "files": {name: _file_metadata(raw) for name, raw in sorted(files.items())},
    }
    return files, manifest


def write_change_bundle(
    prior_dir: str | Path,
    current_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Write a new change bundle by an exclusive atomic directory install."""

    output = Path(output_dir)
    validate_change_output_locations(prior_dir, current_dir, output)
    if not output.name or output.name in {".", ".."}:
        raise ValueError("change bundle output name is unsafe")
    parent = ensure_real_directory(output.parent, "change bundle output parent")
    output = parent / output.name
    parent_status = parent.lstat()
    parent_identity = (parent_status.st_dev, parent_status.st_ino)
    result = compare_releases(prior_dir, current_dir)
    files, manifest = _bundle_payload(result)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=parent))
    stage_status = stage.lstat()
    stage_identity = (stage_status.st_dev, stage_status.st_ino)
    # A failed pre-publication stage is intentionally retained. Recursively
    # deleting by pathname after an identity check would introduce a swap race.
    for name, raw in sorted(files.items()):
        _write_fsynced_file(stage / name, raw)
    _write_fsynced_file(stage / "manifest.json", _pretty_bytes(manifest))
    _fsync_directory(stage)
    if validate_change_bundle(stage, prior_dir, current_dir) != manifest:
        raise ValueError("staged change bundle failed exact validation")
    _check_path_identity(stage, stage_identity, "staged change bundle")
    install_directory_exclusive(
        stage,
        output,
        expected_source_parent=parent_identity,
        expected_destination_parent=parent_identity,
    )
    _fsync_directory(parent)
    _check_path_identity(output, stage_identity, "installed change bundle")
    if validate_change_bundle(output, prior_dir, current_dir) != manifest:
        raise ValueError("installed change bundle failed exact validation")
    _check_path_identity(output, stage_identity, "installed change bundle")
    return manifest


def _validate_release_binding(value: object, context: str) -> dict[str, str]:
    binding = _mapping(value, context)
    _require_keys(
        binding,
        ("release_id", "as_of", "recorded_at", "manifest_sha256"),
        context,
    )
    return {
        "release_id": _text(binding["release_id"], f"{context}.release_id"),
        "as_of": _iso_date(binding["as_of"], f"{context}.as_of"),
        "recorded_at": _iso_timestamp(binding["recorded_at"], f"{context}.recorded_at"),
        "manifest_sha256": _sha256(
            binding["manifest_sha256"], f"{context}.manifest_sha256"
        ),
    }


def _validate_snapshot(value: object, context: str) -> Mapping[str, Any]:
    snapshot = _mapping(value, context)
    validate_claim_snapshot(snapshot)
    _require_keys(
        snapshot,
        (
            "claim_id",
            "subject_entity_id",
            "subject_stable_key",
            "predicate",
            "value_kind",
            "value",
            "valid_from",
            "valid_to",
            "recorded_at",
            "claim_kind",
            "method",
            "notes",
            "evidence_ids",
            "evidence_links",
        ),
        context,
    )
    reconstructed = _claim_snapshot(snapshot)
    if reconstructed != snapshot:
        raise ValueError(f"{context} is not a canonical claim snapshot")
    return snapshot


def _validate_change_rows(
    rows: list[Mapping[str, Any]],
    prior_release: Mapping[str, str],
    current_release: Mapping[str, str],
) -> list[dict[str, Any]]:
    expected_keys = (
        "format",
        "series_id",
        "series",
        "subject_entity_id",
        "subject_stable_key",
        "predicate",
        "value_kind",
        "status",
        "prior_claim",
        "current_claim",
        "interpretation",
        "negative_evidence",
        "alert_proposal_fingerprints",
    )
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        context = f"changes.jsonl[{index}]"
        _require_keys(row, expected_keys, context)
        if row["format"] != CHANGE_RECORD_FORMAT:
            raise ValueError(f"{context} format is invalid")
        series_id = _sha256(row["series_id"], f"{context}.series_id")
        if series_id in seen:
            raise ValueError(f"change bundle has a duplicate semantic series: {series_id}")
        seen.add(series_id)
        descriptor = _mapping(row["series"], f"{context}.series")
        _require_keys(
            descriptor,
            ("subject_entity_id", "predicate", "value_kind", "dimensions"),
            f"{context}.series",
        )
        if _semantic_series_id(descriptor) != series_id:
            raise ValueError(f"{context} series ID is inconsistent")
        status = _text(row["status"], f"{context}.status")
        if status not in CHANGE_STATUSES:
            raise ValueError(f"{context} status is invalid")
        prior = (
            None
            if row["prior_claim"] is None
            else _validate_snapshot(row["prior_claim"], f"{context}.prior_claim")
        )
        current = (
            None
            if row["current_claim"] is None
            else _validate_snapshot(row["current_claim"], f"{context}.current_claim")
        )
        if prior is None and current is None:
            raise ValueError(f"{context} has no claim snapshot")
        if prior is not None and prior["recorded_at"] != prior_release["recorded_at"]:
            raise ValueError(f"{context} prior claim clock does not match its release")
        if (
            current is not None
            and current["recorded_at"] != current_release["recorded_at"]
        ):
            raise ValueError(f"{context} current claim clock does not match its release")
        if _change_status(prior, current) != status:
            raise ValueError(f"{context} status is inconsistent with its claims")
        representative = current or prior
        assert representative is not None
        if (
            row["subject_entity_id"] != representative["subject_entity_id"]
            or row["subject_stable_key"] != representative["subject_stable_key"]
            or row["predicate"] != representative["predicate"]
            or row["value_kind"] != representative["value_kind"]
            or descriptor["subject_entity_id"] != representative["subject_entity_id"]
            or descriptor["predicate"] != representative["predicate"]
            or descriptor["value_kind"] != representative["value_kind"]
        ):
            raise ValueError(f"{context} subject or predicate is inconsistent")
        if _series_descriptor(representative) != descriptor:
            raise ValueError(f"{context} series dimensions are inconsistent")
        if prior is not None and _series_descriptor(prior) != descriptor:
            raise ValueError(f"{context} prior claim has different series dimensions")
        if row["interpretation"] != _STATUS_INTERPRETATIONS[status]:
            raise ValueError(f"{context} interpretation is invalid")
        if row["negative_evidence"] is not False:
            raise ValueError(f"{context}.negative_evidence must be false")
        fingerprints = _string_array(
            row["alert_proposal_fingerprints"],
            f"{context}.alert_proposal_fingerprints",
        )
        if any(not _SHA256_RE.fullmatch(item) for item in fingerprints):
            raise ValueError(f"{context} has an invalid alert proposal fingerprint")
        if fingerprints != sorted(set(fingerprints)):
            raise ValueError(f"{context} alert proposal fingerprints are not canonical")
        if status == "not_carried_forward" and fingerprints:
            raise ValueError("not_carried_forward changes must not alert")
        validated.append(dict(row))
    if [row["series_id"] for row in validated] != sorted(seen):
        raise ValueError("changes.jsonl is not sorted by semantic series ID")
    return validated


def _validate_proposal_shape(row: Mapping[str, Any], context: str) -> None:
    _require_keys(
        row,
        (
            "format",
            "fingerprint",
            "rule_id",
            "rule_version",
            "alert_type",
            "series_id",
            "subject_entity_id",
            "subject_stable_key",
            "predicate",
            "change_status",
            "prior_claim_id",
            "current_claim_id",
            "evidence_lineage",
            "reason",
            "confidence",
            "confidence_scope",
            "delivery_eligible",
            "delivery_ineligibility_reason",
        ),
        context,
    )
    if row["format"] != ALERT_PROPOSAL_FORMAT:
        raise ValueError(f"{context} format is invalid")
    _sha256(row["fingerprint"], f"{context}.fingerprint")
    rule_id = _text(row["rule_id"], f"{context}.rule_id")
    if rule_id not in ALERT_RULE_IDS or row["alert_type"] != rule_id:
        raise ValueError(f"{context} alert rule is invalid")
    if row["rule_version"] != ALERT_RULE_VERSION:
        raise ValueError(f"{context} rule version is invalid")
    _sha256(row["series_id"], f"{context}.series_id")
    if row["change_status"] not in {"added", "revised"}:
        raise ValueError(f"{context} cannot alert for this change status")
    if row["confidence"] is not None or row["confidence_scope"] != "unknown_not_calibrated":
        raise ValueError(f"{context} must preserve null uncalibrated confidence")
    if row["delivery_eligible"] is not False:
        raise ValueError(f"{context} must not be delivery eligible")
    if row["delivery_ineligibility_reason"] != "proposal_only_not_production_alert":
        raise ValueError(f"{context} delivery ineligibility reason is invalid")
    _mapping(row["reason"], f"{context}.reason")
    lineage = _mapping(row["evidence_lineage"], f"{context}.evidence_lineage")
    _require_keys(lineage, ("prior", "current"), f"{context}.evidence_lineage")
    for side in ("prior", "current"):
        item = lineage[side]
        if item is None:
            continue
        item_map = _mapping(item, f"{context}.evidence_lineage.{side}")
        _require_keys(
            item_map,
            ("release_id", "manifest_sha256", "claim_id", "evidence_links"),
            f"{context}.evidence_lineage.{side}",
        )
        _sha256(
            item_map["manifest_sha256"],
            f"{context}.evidence_lineage.{side}.manifest_sha256",
        )
        _list(item_map["evidence_links"], f"{context}.evidence_lineage.{side}.evidence_links")


def _validate_summary_schema(value: object) -> Mapping[str, Any]:
    summary = _mapping(value, "summary.json")
    _require_keys(
        summary,
        (
            "format",
            "prior_release",
            "current_release",
            "change_count",
            "changes_by_status",
            "alert_proposal_count",
            "alerts_by_rule",
            "not_carried_forward_policy",
        ),
        "summary.json",
    )
    if summary["format"] != SUMMARY_FORMAT:
        raise ValueError("summary.json format is invalid")
    _integer(summary["change_count"], "summary.json change_count")
    _integer(summary["alert_proposal_count"], "summary.json alert_proposal_count")
    _count_map(
        summary["changes_by_status"],
        CHANGE_STATUSES,
        "summary.json changes_by_status",
    )
    _count_map(
        summary["alerts_by_rule"],
        ALERT_RULE_IDS,
        "summary.json alerts_by_rule",
    )
    return summary


def _validate_manifest_schema(value: object) -> Mapping[str, Any]:
    manifest = _mapping(value, "change bundle manifest")
    _require_keys(
        manifest,
        (
            "format",
            "bundle_id",
            "schema_version",
            "series_rule_version",
            "rule_version",
            "prior_release",
            "current_release",
            "change_count",
            "changes_by_status",
            "alert_proposal_count",
            "alerts_by_rule",
            "stable_sort_rules",
            "files",
        ),
        "change bundle manifest",
    )
    if manifest["format"] != CHANGE_BUNDLE_FORMAT:
        raise ValueError("change bundle manifest format is invalid")
    if not isinstance(manifest["bundle_id"], str) or not _BUNDLE_ID_RE.fullmatch(
        manifest["bundle_id"]
    ):
        raise ValueError("change bundle ID is invalid")
    if manifest["schema_version"] != CHANGE_SCHEMA_VERSION:
        raise ValueError("change bundle schema version is invalid")
    if manifest["series_rule_version"] != SERIES_RULE_VERSION:
        raise ValueError("change bundle series rule version is invalid")
    if manifest["rule_version"] != ALERT_RULE_VERSION:
        raise ValueError("change bundle alert rule version is invalid")
    _integer(manifest["change_count"], "change bundle manifest change_count")
    _integer(
        manifest["alert_proposal_count"],
        "change bundle manifest alert_proposal_count",
    )
    _count_map(
        manifest["changes_by_status"],
        CHANGE_STATUSES,
        "change bundle manifest changes_by_status",
    )
    _count_map(
        manifest["alerts_by_rule"],
        ALERT_RULE_IDS,
        "change bundle manifest alerts_by_rule",
    )
    return manifest


def validate_change_bundle(
    output_dir: str | Path,
    prior_dir: str | Path | None = None,
    current_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Validate exact schemas, bytes, hashes, semantics, and optional source bindings."""

    if (prior_dir is None) != (current_dir is None):
        raise ValueError("prior_dir and current_dir must be provided together")
    output, directory_descriptor = _open_real_directory_fd(
        output_dir,
        "change bundle output",
        create=False,
    )
    directory_identity = _directory_identity(directory_descriptor)
    try:
        raw_by_name = _snapshot_open_directory(
            directory_descriptor,
            "change bundle",
        )
    finally:
        os.close(directory_descriptor)
    actual_names = set(raw_by_name)
    expected_names = set(_MANAGED_FILES) | {"manifest.json"}
    if actual_names != expected_names:
        raise ValueError("change bundle contains missing or unmanaged files")
    manifest = _validate_manifest_schema(
        _strict_json(raw_by_name["manifest.json"], "change bundle manifest")
    )
    if raw_by_name["manifest.json"] != _pretty_bytes(manifest):
        raise ValueError("change bundle manifest is not exact pretty JSON")
    files = _mapping(manifest["files"], "change bundle manifest files")
    if set(files) != set(_MANAGED_FILES):
        raise ValueError("change bundle manifest has missing or unmanaged files")
    for name in sorted(_MANAGED_FILES):
        metadata = _mapping(files[name], f"change bundle manifest files.{name}")
        _require_keys(metadata, ("bytes", "sha256"), f"manifest files.{name}")
        _integer(metadata["bytes"], f"manifest files.{name}.bytes")
        _sha256(metadata["sha256"], f"manifest files.{name}.sha256")
        if metadata != _file_metadata(raw_by_name[name]):
            raise ValueError(f"managed change bundle file does not match manifest: {name}")
    prior_binding = _validate_release_binding(manifest["prior_release"], "prior release binding")
    current_binding = _validate_release_binding(
        manifest["current_release"], "current release binding"
    )
    _validate_release_order(prior_binding, current_binding)
    metadata = _manifest_metadata(prior_binding, current_binding)
    for key, expected in metadata.items():
        if manifest.get(key) != expected:
            raise ValueError(f"change bundle manifest {key} is inconsistent")
    changes = _validate_change_rows(
        _jsonl_rows(raw_by_name["changes.jsonl"], "changes.jsonl"),
        prior_binding,
        current_binding,
    )
    proposals_raw = _jsonl_rows(
        raw_by_name["alert_proposals.jsonl"], "alert_proposals.jsonl"
    )
    proposals = [dict(row) for row in proposals_raw]
    for index, proposal in enumerate(proposals):
        _validate_proposal_shape(proposal, f"alert_proposals.jsonl[{index}]")
    fingerprints = [row["fingerprint"] for row in proposals]
    if fingerprints != sorted(set(fingerprints)):
        raise ValueError("alert proposals are not uniquely sorted by fingerprint")
    expected_changes = deepcopy(changes)
    expected_proposals = _derive_alert_proposals(
        expected_changes, prior_binding, current_binding
    )
    if (
        raw_by_name["changes.jsonl"] != _jsonl_bytes(expected_changes)
        or raw_by_name["alert_proposals.jsonl"]
        != _jsonl_bytes(expected_proposals)
    ):
        raise ValueError("change or alert proposal content is not exactly recomputable")
    summary = _validate_summary_schema(
        _strict_json(raw_by_name["summary.json"], "summary.json")
    )
    if raw_by_name["summary.json"] != _pretty_bytes(summary):
        raise ValueError("summary.json is not exact pretty JSON")
    expected_summary = _summary(changes, proposals, prior_binding, current_binding)
    if raw_by_name["summary.json"] != _pretty_bytes(expected_summary):
        raise ValueError("summary.json is inconsistent with change records")
    if raw_by_name["README.md"] != _readme_text(metadata, summary).encode("utf-8"):
        raise ValueError("README.md is not exact deterministic content")
    expected_files = {
        name: raw_by_name[name] for name in sorted(_MANAGED_FILES)
    }
    expected_manifest = {
        **metadata,
        "change_count": summary["change_count"],
        "changes_by_status": deepcopy(summary["changes_by_status"]),
        "alert_proposal_count": summary["alert_proposal_count"],
        "alerts_by_rule": deepcopy(summary["alerts_by_rule"]),
        "stable_sort_rules": {
            "changes": "series_id",
            "alert_proposals": "fingerprint",
            "files": "filename_codepoint_order",
        },
        "files": {
            name: _file_metadata(raw) for name, raw in sorted(expected_files.items())
        },
    }
    if raw_by_name["manifest.json"] != _pretty_bytes(expected_manifest):
        raise ValueError("change bundle manifest is not the exact deterministic manifest")
    if prior_dir is not None and current_dir is not None:
        expected_result = compare_releases(prior_dir, current_dir)
        expected_payload, expected_bound_manifest = _bundle_payload(expected_result)
        if (
            expected_payload != expected_files
            or _pretty_bytes(expected_bound_manifest)
            != raw_by_name["manifest.json"]
        ):
            raise ValueError("change bundle does not match its bound input releases")
    final_output, final_descriptor = _open_real_directory_fd(
        output,
        "change bundle output",
        create=False,
    )
    try:
        _check_directory_identity(
            final_descriptor,
            directory_identity,
            "change bundle output",
        )
        if _snapshot_open_directory(final_descriptor, "change bundle") != raw_by_name:
            raise OSError("change bundle changed during validation")
    finally:
        os.close(final_descriptor)
    if final_output != output:
        raise OSError("change bundle output path changed during validation")
    return dict(manifest)


def _hash_stream(stream: io.BufferedIOBase) -> tuple[int, str]:
    stream.seek(0)
    size = 0
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def write_deterministic_change_archive(
    output_dir: str | Path,
    archive_path: str | Path,
    *,
    protected_directories: Sequence[str | Path],
) -> dict[str, Any]:
    """Publish a deterministic tar.gz containing one validated change bundle."""

    if len(protected_directories) != 2:
        raise ValueError("archive publication requires both bound input releases")
    manifest = validate_change_bundle(output_dir, *protected_directories)
    manifest_raw = _pretty_bytes(manifest)
    output = Path(output_dir)
    archive = Path(archive_path)
    _require_outside_directories(
        archive,
        protected_directories,
        "change archive output",
    )
    try:
        archive.resolve(strict=False).relative_to(output.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise ValueError("archive output must be outside the change bundle directory")
    if archive.is_symlink() or archive.exists():
        raise ValueError("archive output must not already exist")
    if archive.suffixes[-2:] != [".tar", ".gz"]:
        raise ValueError("archive output must end in .tar.gz")
    parent = ensure_real_directory(archive.parent, "change archive output parent")
    archive = parent / archive.name
    parent_status = parent.lstat()
    parent_identity = (parent_status.st_dev, parent_status.st_ino)
    output, bundle_descriptor = _open_real_directory_fd(
        output,
        "change archive input bundle",
        create=False,
    )
    bundle_identity = _directory_identity(bundle_descriptor)
    try:
        bundle_snapshot = _snapshot_open_directory(
            bundle_descriptor,
            "change archive input bundle",
        )
    except BaseException:
        os.close(bundle_descriptor)
        raise
    if bundle_snapshot.get("manifest.json") != manifest_raw:
        os.close(bundle_descriptor)
        raise ValueError("change bundle manifest changed before archive creation")
    try:
        descriptor, stage_name = tempfile.mkstemp(
            prefix=f".{archive.name}.stage-", dir=parent
        )
    except BaseException:
        os.close(bundle_descriptor)
        raise
    stage = Path(stage_name)
    stage_status = os.fstat(descriptor)
    stage_identity = (stage_status.st_dev, stage_status.st_ino)
    try:
        with os.fdopen(descriptor, "w+b") as raw_stream:
            with gzip.GzipFile(
                fileobj=raw_stream, mode="wb", filename="", mtime=0
            ) as gzip_stream:
                with tarfile.open(fileobj=gzip_stream, mode="w") as tar:
                    for name in sorted(set(_MANAGED_FILES) | {"manifest.json"}):
                        data = bundle_snapshot[name]
                        if name == "manifest.json":
                            if data != manifest_raw:
                                raise ValueError(
                                    "change bundle manifest changed during archive creation"
                                )
                        elif manifest["files"].get(name) != _file_metadata(data):
                            raise ValueError(
                                f"managed change bundle file changed during archive creation: {name}"
                            )
                        info = tarfile.TarInfo(f"{manifest['bundle_id']}/{name}")
                        info.size = len(data)
                        info.mtime = 0
                        info.mode = 0o644
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        tar.addfile(info, io.BytesIO(data))
            raw_stream.flush()
            os.fsync(raw_stream.fileno())
            staged_size, staged_sha256 = _hash_stream(raw_stream)
            status_after_hash = os.fstat(raw_stream.fileno())
            if (status_after_hash.st_dev, status_after_hash.st_ino) != stage_identity:
                raise OSError("change archive stage identity changed while hashing")
        if _pretty_bytes(validate_change_bundle(output)) != manifest_raw:
            raise ValueError("change bundle changed during archive creation")
        _check_path_identity(
            output,
            bundle_identity,
            "change archive input bundle",
        )
        if (
            _snapshot_open_directory(
                bundle_descriptor,
                "change archive input bundle",
            )
            != bundle_snapshot
        ):
            raise OSError("change bundle changed during archive creation")
        _, stage_parent_descriptor = _open_real_directory_fd(
            parent, "change archive stage parent", create=False
        )
        try:
            _check_directory_identity(
                stage_parent_descriptor, parent_identity, "change archive stage parent"
            )
            staged_raw = _read_regular_file_at(
                stage_parent_descriptor, stage.name, "change archive stage"
            )
            named_stage = os.stat(
                stage.name, dir_fd=stage_parent_descriptor, follow_symlinks=False
            )
            if (named_stage.st_dev, named_stage.st_ino) != stage_identity:
                raise OSError("change archive stage identity changed")
            if _file_metadata(staged_raw) != {"bytes": staged_size, "sha256": staged_sha256}:
                raise OSError("change archive stage bytes changed")
        finally:
            os.close(stage_parent_descriptor)
        install_file_exclusive(
            stage,
            archive,
            expected_source_parent=parent_identity,
            expected_destination_parent=parent_identity,
        )
    finally:
        os.close(bundle_descriptor)
        # Failed stages and a staging hard link left by the exclusive installer
        # are retained rather than deleted through a raceable pathname.
    _fsync_directory(parent)
    _, parent_descriptor = _open_real_directory_fd(
        parent,
        "change archive output parent",
        create=False,
    )
    try:
        _check_directory_identity(
            parent_descriptor,
            parent_identity,
            "change archive output parent",
        )
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        installed_descriptor = os.open(
            archive.name,
            flags,
            dir_fd=parent_descriptor,
        )
        try:
            installed_status = os.fstat(installed_descriptor)
            if (
                (installed_status.st_dev, installed_status.st_ino)
                != stage_identity
                or not stat.S_ISREG(installed_status.st_mode)
            ):
                raise OSError("change archive output identity changed")
            with os.fdopen(installed_descriptor, "rb", closefd=False) as stream:
                installed_size, installed_sha256 = _hash_stream(stream)
            installed_after_hash = os.fstat(installed_descriptor)
            if (
                installed_after_hash.st_dev,
                installed_after_hash.st_ino,
            ) != stage_identity:
                raise OSError("change archive output identity changed while hashing")
        finally:
            os.close(installed_descriptor)
        named_status = os.stat(
            archive.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            (named_status.st_dev, named_status.st_ino) != stage_identity
            or not stat.S_ISREG(named_status.st_mode)
        ):
            raise OSError("change archive output pathname changed while hashing")
    finally:
        os.close(parent_descriptor)
    if (installed_size, installed_sha256) != (staged_size, staged_sha256):
        raise OSError("change archive output bytes changed during publication")
    return {
        "path": str(archive),
        "bytes": staged_size,
        "sha256": staged_sha256,
    }


__all__ = [
    "ALERT_PROPOSAL_FORMAT",
    "ALERT_RULE_VERSION",
    "CAPACITY_REVISION_THRESHOLD",
    "CHANGE_BUNDLE_FORMAT",
    "CHANGE_RECORD_FORMAT",
    "CHANGE_SCHEMA_VERSION",
    "SERIES_RULE_VERSION",
    "SUMMARY_FORMAT",
    "compare_releases",
    "validate_change_output_locations",
    "validate_change_bundle",
    "write_change_bundle",
    "write_deterministic_change_archive",
]
