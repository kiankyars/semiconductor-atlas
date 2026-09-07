"""Typed inputs for the semiconductor facility claim store."""

from __future__ import annotations

import calendar
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from math import isfinite
from typing import Any, Mapping, TypeAlias
from urllib.parse import urlparse


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EntityKind(StrEnum):
    ORGANIZATION = "organization"
    SITE = "site"
    FACILITY = "facility"
    BUILDING = "building"
    PRODUCTION_UNIT = "production_unit"
    PROJECT = "project"
    INFRASTRUCTURE_ASSET = "infrastructure_asset"


class IngestionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EntityResolutionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OrganizationNameType(StrEnum):
    LEGAL = "legal"
    OTHER = "other"
    TRANSLITERATED = "transliterated"
    SHORT = "short"


class ResolutionDecisionOutcome(StrEnum):
    MATCH = "match"
    REJECT = "reject"
    DEFER = "defer"


class ValueKind(StrEnum):
    SCALAR = "scalar"
    GEOMETRY = "geometry"
    RELATIONSHIP = "relationship"
    MILESTONE = "milestone"
    CAPABILITY = "capability"
    CAPACITY = "capacity"
    RESOURCE = "resource"
    CONSTRAINT = "constraint"


class ScalarType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"


class EvidenceRole(StrEnum):
    SUPPORT = "support"
    REFUTE = "refute"
    CONTEXT = "context"


class DependencyKind(StrEnum):
    DERIVED_FROM = "derived_from"
    AGGREGATES = "aggregates"
    TRANSFORMS = "transforms"


class ClaimKind(StrEnum):
    SOURCE_STATEMENT = "source_statement"
    DIRECT_OBSERVATION = "direct_observation"
    RECONCILED_FACT = "reconciled_fact"
    DERIVED_ESTIMATE = "derived_estimate"


class CapacityBasis(StrEnum):
    ANNOUNCED = "announced"
    PHYSICAL_CONSTRUCTION = "physical_construction"
    TOOL_INSTALLED = "tool_installed"
    QUALIFIED = "qualified"
    ECONOMICALLY_USABLE = "economically_usable"


class MilestoneStatus(StrEnum):
    EXPECTED = "expected"
    STARTED = "started"
    COMPLETED = "completed"
    DELAYED = "delayed"
    CANCELLED = "cancelled"


class ConstraintStatus(StrEnum):
    POTENTIAL = "potential"
    BINDING = "binding"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"


class ConstraintSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")


def _optional_text(value: str | None, field_name: str) -> None:
    if value is not None:
        _required(value, field_name)


def _confidence(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("confidence must be a number between 0 and 1")
    if not isfinite(float(value)) or not 0 <= float(value) <= 1:
        raise ValueError("confidence must be between 0 and 1")


def _iso_date(value: str, field_name: str) -> None:
    _required(value, field_name)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO date") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must use YYYY-MM-DD")


def _iso_timestamp(value: str, field_name: str) -> str:
    _required(value, field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _published_at(value: str | None) -> str | None:
    if value is None:
        return None
    if "T" in value:
        return _iso_timestamp(value, "published_at")
    _iso_date(value, "published_at")
    return value


def _absolute_url(value: str, field_name: str) -> None:
    _required(value, field_name)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")


def _sha256(value: str, field_name: str) -> None:
    if not SHA256_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _json_object(value: Mapping[str, Any], field_name: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must contain finite JSON data") from error


def source_record_payload_sha256(value: Mapping[str, Any]) -> str:
    _json_object(value, "payload")
    raw = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _ordered_numbers(low: float, base: float, high: float, field_name: str) -> None:
    values = (low, base, high)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError(f"{field_name} range values must be numbers")
    if not all(isfinite(float(value)) for value in values):
        raise ValueError(f"{field_name} range values must be finite")
    if not 0 <= float(low) <= float(base) <= float(high):
        raise ValueError(f"{field_name} must satisfy 0 <= low <= base <= high")


def _ordered_dates(low: str, base: str, high: str, field_name: str) -> None:
    for suffix, value in (("low", low), ("base", base), ("high", high)):
        _iso_date(value, f"{field_name}_{suffix}")
    if not low <= base <= high:
        raise ValueError(f"{field_name} must satisfy low <= base <= high")


@dataclass(frozen=True, slots=True)
class SourceFamily:
    id: str
    stable_key: str
    name: str
    created_at: str
    description: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "stable_key", "name"):
            _required(getattr(self, field_name), field_name)
        object.__setattr__(self, "created_at", _iso_timestamp(self.created_at, "created_at"))
        _optional_text(self.description, "description")


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    family_id: str
    stable_key: str
    name: str
    publisher: str
    canonical_url: str
    created_at: str
    license: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "family_id", "stable_key", "name", "publisher"):
            _required(getattr(self, field_name), field_name)
        _absolute_url(self.canonical_url, "canonical_url")
        object.__setattr__(self, "created_at", _iso_timestamp(self.created_at, "created_at"))
        _optional_text(self.license, "license")


@dataclass(frozen=True, slots=True)
class SourceDocument:
    id: str
    source_id: str
    document_url: str
    title: str
    retrieved_at: str
    content_sha256: str
    published_at: str | None = None
    media_type: str | None = None
    license: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("id", "source_id", "title"):
            _required(getattr(self, field_name), field_name)
        _absolute_url(self.document_url, "document_url")
        object.__setattr__(self, "retrieved_at", _iso_timestamp(self.retrieved_at, "retrieved_at"))
        _sha256(self.content_sha256, "content_sha256")
        object.__setattr__(self, "published_at", _published_at(self.published_at))
        _optional_text(self.media_type, "media_type")
        _optional_text(self.license, "license")
        _json_object(self.metadata, "metadata")


@dataclass(frozen=True, slots=True)
class IngestionRun:
    id: str
    source_id: str
    started_at: str
    status: IngestionStatus = IngestionStatus.RUNNING
    completed_at: str | None = None
    code_version: str | None = None
    input_document_id: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        _required(self.id, "id")
        _required(self.source_id, "source_id")
        object.__setattr__(self, "started_at", _iso_timestamp(self.started_at, "started_at"))
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", _iso_timestamp(self.completed_at, "completed_at"))
            started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            completed = datetime.fromisoformat(self.completed_at.replace("Z", "+00:00"))
            if completed <= started:
                raise ValueError("completed_at must be later than started_at")
        if self.status is IngestionStatus.RUNNING and self.completed_at is not None:
            raise ValueError("a running ingestion cannot have completed_at")
        if self.status is not IngestionStatus.RUNNING and self.completed_at is None:
            raise ValueError("a completed ingestion requires completed_at")
        _optional_text(self.code_version, "code_version")
        _optional_text(self.input_document_id, "input_document_id")
        _optional_text(self.error, "error")
        _json_object(self.parameters, "parameters")


@dataclass(frozen=True, slots=True)
class IngestionRunDocument:
    ingestion_run_id: str
    source_document_id: str
    role: str

    def __post_init__(self) -> None:
        for field_name in ("ingestion_run_id", "source_document_id", "role"):
            _required(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class SourceRecord:
    id: str
    ingestion_run_id: str
    source_document_id: str
    source_record_key: str
    observed_at: str
    record_sha256: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "id",
            "ingestion_run_id",
            "source_document_id",
            "source_record_key",
        ):
            _required(getattr(self, field_name), field_name)
        object.__setattr__(self, "observed_at", _iso_timestamp(self.observed_at, "observed_at"))
        _sha256(self.record_sha256, "record_sha256")
        _json_object(self.payload, "payload")
        if self.record_sha256 != source_record_payload_sha256(self.payload):
            raise ValueError("record_sha256 must match the canonical source-record payload")


@dataclass(frozen=True, slots=True)
class Entity:
    id: str
    kind: EntityKind
    stable_key: str
    created_at: str
    display_name: str | None = None
    created_by_run_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.id, "id")
        _required(self.stable_key, "stable_key")
        object.__setattr__(self, "created_at", _iso_timestamp(self.created_at, "created_at"))
        _optional_text(self.display_name, "display_name")
        _optional_text(self.created_by_run_id, "created_by_run_id")


@dataclass(frozen=True, slots=True)
class ClaimSeries:
    id: str
    subject_entity_id: str
    stable_key: str
    predicate: str
    value_kind: ValueKind
    created_at: str

    def __post_init__(self) -> None:
        for field_name in ("id", "subject_entity_id", "stable_key", "predicate"):
            _required(getattr(self, field_name), field_name)
        object.__setattr__(self, "created_at", _iso_timestamp(self.created_at, "created_at"))


@dataclass(frozen=True, slots=True)
class ClaimVersion:
    id: str
    series_id: str
    valid_from: str | None
    recorded_at: str
    claim_kind: ClaimKind
    method: str
    confidence: float | None
    valid_to: str | None = None
    created_by_run_id: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "series_id", "method"):
            _required(getattr(self, field_name), field_name)
        if self.valid_from is None:
            if self.claim_kind is not ClaimKind.SOURCE_STATEMENT or self.valid_to is not None:
                raise ValueError("unknown effective time requires a source statement with no valid_to")
        else:
            _iso_date(self.valid_from, "valid_from")
        if self.valid_to is not None:
            _iso_date(self.valid_to, "valid_to")
            if self.valid_to <= self.valid_from:
                raise ValueError("valid_to must be later than valid_from")
        object.__setattr__(self, "recorded_at", _iso_timestamp(self.recorded_at, "recorded_at"))
        if self.confidence is not None:
            _confidence(self.confidence)
        _optional_text(self.created_by_run_id, "created_by_run_id")
        _optional_text(self.notes, "notes")


@dataclass(frozen=True, slots=True)
class OrganizationNameClaimMetadata:
    claim_version_id: str
    name_type: OrganizationNameType
    language_tag: str | None = None
    script_code: str | None = None

    def __post_init__(self) -> None:
        _required(self.claim_version_id, "claim_version_id")
        if self.language_tag is not None:
            _required(self.language_tag, "language_tag")
            if not re.fullmatch(
                r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*",
                self.language_tag,
            ):
                raise ValueError("language_tag must be a simple BCP-47 language tag")
        if self.script_code is not None:
            if not re.fullmatch(r"[A-Za-z]{4}", self.script_code):
                raise ValueError("script_code must contain four ASCII letters")
            object.__setattr__(
                self,
                "script_code",
                self.script_code[0].upper() + self.script_code[1:].lower(),
            )


@dataclass(frozen=True, slots=True)
class OrganizationIdentifierClaimMetadata:
    claim_version_id: str
    scheme: str
    normalized_value: str
    jurisdiction: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("claim_version_id", "scheme", "normalized_value"):
            _required(getattr(self, field_name), field_name)
        _optional_text(self.jurisdiction, "jurisdiction")


@dataclass(frozen=True, slots=True)
class EntityResolutionRun:
    id: str
    started_at: str
    resolver_version: str
    status: EntityResolutionStatus = EntityResolutionStatus.RUNNING
    completed_at: str | None = None
    code_version: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        _required(self.id, "id")
        _required(self.resolver_version, "resolver_version")
        object.__setattr__(self, "started_at", _iso_timestamp(self.started_at, "started_at"))
        if self.completed_at is not None:
            object.__setattr__(
                self,
                "completed_at",
                _iso_timestamp(self.completed_at, "completed_at"),
            )
            started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            completed = datetime.fromisoformat(self.completed_at.replace("Z", "+00:00"))
            if completed <= started:
                raise ValueError("completed_at must be later than started_at")
        if self.status is EntityResolutionStatus.RUNNING and self.completed_at is not None:
            raise ValueError("a running resolution cannot have completed_at")
        if self.status is not EntityResolutionStatus.RUNNING and self.completed_at is None:
            raise ValueError("a completed resolution requires completed_at")
        _optional_text(self.code_version, "code_version")
        _optional_text(self.error, "error")
        if self.status in {
            EntityResolutionStatus.RUNNING,
            EntityResolutionStatus.SUCCEEDED,
        } and self.error is not None:
            raise ValueError("only a failed resolution may have an error")
        if self.status is EntityResolutionStatus.FAILED and self.error is None:
            raise ValueError("a failed resolution requires an error")
        _json_object(self.parameters, "parameters")


@dataclass(frozen=True, slots=True)
class EntityResolutionRunInput:
    resolution_run_id: str
    ingestion_run_id: str

    def __post_init__(self) -> None:
        _required(self.resolution_run_id, "resolution_run_id")
        _required(self.ingestion_run_id, "ingestion_run_id")


@dataclass(frozen=True, slots=True)
class EntityResolutionCandidate:
    id: str
    resolution_run_id: str
    source_record_id: str
    observed_entity_id: str
    candidate_entity_id: str
    score: float
    candidate_rank: int
    created_at: str
    features: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "id",
            "resolution_run_id",
            "source_record_id",
            "observed_entity_id",
            "candidate_entity_id",
        ):
            _required(getattr(self, field_name), field_name)
        if self.observed_entity_id == self.candidate_entity_id:
            raise ValueError("a resolution candidate must compare distinct entities")
        _confidence(self.score)
        if (
            isinstance(self.candidate_rank, bool)
            or not isinstance(self.candidate_rank, int)
            or self.candidate_rank < 1
        ):
            raise ValueError("candidate_rank must be a positive integer")
        object.__setattr__(self, "created_at", _iso_timestamp(self.created_at, "created_at"))
        _json_object(self.features, "features")


@dataclass(frozen=True, slots=True)
class EntityResolutionDecision:
    id: str
    candidate_id: str
    outcome: ResolutionDecisionOutcome
    decided_at: str
    decided_by: str
    reason: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("id", "candidate_id", "decided_by", "reason"):
            _required(getattr(self, field_name), field_name)
        object.__setattr__(self, "decided_at", _iso_timestamp(self.decided_at, "decided_at"))
        _json_object(self.metadata, "metadata")


@dataclass(frozen=True, slots=True)
class SourceEntityAssignment:
    id: str
    source_record_id: str
    observed_entity_id: str
    canonical_entity_id: str
    decision_id: str
    valid_from: str
    recorded_at: str
    valid_to: str | None = None
    superseded_at: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "id",
            "source_record_id",
            "observed_entity_id",
            "canonical_entity_id",
            "decision_id",
        ):
            _required(getattr(self, field_name), field_name)
        if self.observed_entity_id == self.canonical_entity_id:
            raise ValueError("an assignment must map distinct source and canonical entities")
        _iso_date(self.valid_from, "valid_from")
        if self.valid_to is not None:
            _iso_date(self.valid_to, "valid_to")
            if self.valid_to <= self.valid_from:
                raise ValueError("valid_to must be later than valid_from")
        object.__setattr__(self, "recorded_at", _iso_timestamp(self.recorded_at, "recorded_at"))
        if self.superseded_at is not None:
            object.__setattr__(
                self,
                "superseded_at",
                _iso_timestamp(self.superseded_at, "superseded_at"),
            )
            recorded = datetime.fromisoformat(self.recorded_at.replace("Z", "+00:00"))
            superseded = datetime.fromisoformat(
                self.superseded_at.replace("Z", "+00:00")
            )
            if superseded <= recorded:
                raise ValueError("superseded_at must be later than recorded_at")


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    source_document_id: str
    role: EvidenceRole = EvidenceRole.SUPPORT
    source_record_id: str | None = None
    locator: str | None = None
    excerpt: str | None = None

    def __post_init__(self) -> None:
        _required(self.source_document_id, "source_document_id")
        _optional_text(self.source_record_id, "source_record_id")
        _optional_text(self.locator, "locator")
        _optional_text(self.excerpt, "excerpt")


@dataclass(frozen=True, slots=True)
class DependencyLink:
    depends_on_claim_version_id: str
    kind: DependencyKind = DependencyKind.DERIVED_FROM

    def __post_init__(self) -> None:
        _required(self.depends_on_claim_version_id, "depends_on_claim_version_id")


@dataclass(frozen=True, slots=True)
class ScalarValue:
    scalar_type: ScalarType
    value: str | float | int | bool
    unit: str | None = None

    @property
    def kind(self) -> ValueKind:
        return ValueKind.SCALAR

    def __post_init__(self) -> None:
        _optional_text(self.unit, "unit")
        if self.scalar_type is ScalarType.TEXT:
            if not isinstance(self.value, str):
                raise ValueError("text scalar requires a string")
            _required(self.value, "value")
        elif self.scalar_type is ScalarType.NUMBER:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise ValueError("number scalar requires a finite number")
            if not isfinite(float(self.value)):
                raise ValueError("number scalar requires a finite number")
        elif self.scalar_type is ScalarType.INTEGER:
            if isinstance(self.value, bool) or not isinstance(self.value, int):
                raise ValueError("integer scalar requires an integer")
            if not -(2**63) <= self.value <= 2**63 - 1:
                raise ValueError("integer scalar must fit SQLite's signed 64-bit range")
        elif self.scalar_type is ScalarType.BOOLEAN:
            if not isinstance(self.value, bool):
                raise ValueError("boolean scalar requires a boolean")
        elif self.scalar_type is ScalarType.DATE:
            if not isinstance(self.value, str):
                raise ValueError("date scalar requires a string")
            _iso_date(self.value, "value")
        elif self.scalar_type is ScalarType.TIMESTAMP:
            if not isinstance(self.value, str):
                raise ValueError("timestamp scalar requires a string")
            object.__setattr__(self, "value", _iso_timestamp(self.value, "value"))


def _position(value: Any) -> tuple[float, float]:
    if (
        not isinstance(value, list)
        or len(value) < 2
        or any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in value
        )
    ):
        raise ValueError("GeoJSON position must be an array of at least two numbers")
    if not all(isfinite(float(item)) for item in value):
        raise ValueError("geometry coordinates must be finite")
    longitude, latitude = float(value[0]), float(value[1])
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError("geometry coordinates must be WGS84 longitude/latitude")
    return longitude, latitude


def _line(value: Any, *, minimum: int = 2) -> list[tuple[float, float]]:
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError(f"GeoJSON coordinate sequence requires at least {minimum} positions")
    return [_position(item) for item in value]


def _ring(value: Any) -> list[tuple[float, float]]:
    positions = _line(value, minimum=4)
    if positions[0] != positions[-1]:
        raise ValueError("GeoJSON polygon rings must be closed")
    return positions


def _validate_geometry_coordinates(geometry_type: str, value: Any) -> None:
    if geometry_type == "Point":
        _position(value)
    elif geometry_type == "MultiPoint":
        _line(value, minimum=1)
    elif geometry_type == "LineString":
        _line(value)
    elif geometry_type == "MultiLineString":
        if not isinstance(value, list) or not value:
            raise ValueError("GeoJSON MultiLineString requires line strings")
        for line in value:
            _line(line)
    elif geometry_type == "Polygon":
        if not isinstance(value, list) or not value:
            raise ValueError("GeoJSON Polygon requires rings")
        for ring in value:
            _ring(ring)
    elif geometry_type == "MultiPolygon":
        if not isinstance(value, list) or not value:
            raise ValueError("GeoJSON MultiPolygon requires polygons")
        for polygon in value:
            if not isinstance(polygon, list) or not polygon:
                raise ValueError("GeoJSON MultiPolygon polygons require rings")
            for ring in polygon:
                _ring(ring)


@dataclass(frozen=True, slots=True)
class GeometryValue:
    geometry: Mapping[str, Any]
    crs: str = "EPSG:4326"
    precision_m: float | None = None

    @property
    def kind(self) -> ValueKind:
        return ValueKind.GEOMETRY

    def __post_init__(self) -> None:
        _json_object(self.geometry, "geometry")
        geometry_type = self.geometry.get("type")
        if geometry_type not in {
            "Point",
            "LineString",
            "Polygon",
            "MultiPoint",
            "MultiLineString",
            "MultiPolygon",
        }:
            raise ValueError("unsupported GeoJSON geometry type")
        _validate_geometry_coordinates(geometry_type, self.geometry.get("coordinates"))
        _required(self.crs, "crs")
        if self.precision_m is not None:
            if isinstance(self.precision_m, bool) or not isinstance(self.precision_m, (int, float)):
                raise ValueError("precision_m must be a nonnegative finite number")
            if not isfinite(float(self.precision_m)) or self.precision_m < 0:
                raise ValueError("precision_m must be a nonnegative finite number")


@dataclass(frozen=True, slots=True)
class RelationshipValue:
    object_entity_id: str
    relationship_type: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> ValueKind:
        return ValueKind.RELATIONSHIP

    def __post_init__(self) -> None:
        _required(self.object_entity_id, "object_entity_id")
        _required(self.relationship_type, "relationship_type")
        _json_object(self.attributes, "attributes")


@dataclass(frozen=True, slots=True)
class MilestoneValue:
    milestone_type: str
    status: MilestoneStatus
    date_low: str
    date_base: str | None
    date_high: str
    date_precision: str | None = None
    date_literal: str | None = None

    @property
    def kind(self) -> ValueKind:
        return ValueKind.MILESTONE

    def __post_init__(self) -> None:
        _required(self.milestone_type, "milestone_type")
        if self.date_precision is None and self.date_literal is None:
            _ordered_dates(self.date_low, self.date_base, self.date_high, "milestone_date")
            return
        if self.date_base is not None or self.status is not MilestoneStatus.EXPECTED:
            raise ValueError("source target periods require expected status and no midpoint")
        if not isinstance(self.date_precision, str) or self.date_precision not in {
            "day", "month", "quarter", "half_year", "year", "range"
        }:
            raise ValueError("invalid source target date precision")
        _required(self.date_literal, "date_literal")
        _iso_date(self.date_low, "date_low")
        _iso_date(self.date_high, "date_high")
        low, high = date.fromisoformat(self.date_low), date.fromisoformat(self.date_high)
        if low > high:
            raise ValueError("source target period bounds are reversed")
        if self.date_precision == "range":
            return
        if self.date_precision == "day":
            valid = low == high
        else:
            width = {"month": 1, "quarter": 3, "half_year": 6, "year": 12}[self.date_precision]
            last_month = low.month + width - 1
            valid = (low.day == 1 and (low.month - 1) % width == 0 and last_month <= 12
                     and high == date(low.year, last_month, calendar.monthrange(low.year, last_month)[1]))
        if not valid:
            raise ValueError("source target bounds do not match their calendar precision")


@dataclass(frozen=True, slots=True)
class CapabilityValue:
    capability_type: str
    value: str | float
    unit: str | None = None
    qualifier: str | None = None

    @property
    def kind(self) -> ValueKind:
        return ValueKind.CAPABILITY

    def __post_init__(self) -> None:
        _required(self.capability_type, "capability_type")
        if isinstance(self.value, str):
            _required(self.value, "value")
        elif isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError("capability value must be text or a finite number")
        elif not isfinite(float(self.value)):
            raise ValueError("capability value must be text or a finite number")
        _optional_text(self.unit, "unit")
        _optional_text(self.qualifier, "qualifier")


@dataclass(frozen=True, slots=True)
class CapacityValue:
    metric: str
    basis: CapacityBasis
    unit: str
    low: float
    base: float
    high: float
    period_start: str | None = None
    period_end: str | None = None

    @property
    def kind(self) -> ValueKind:
        return ValueKind.CAPACITY

    def __post_init__(self) -> None:
        _required(self.metric, "metric")
        _required(self.unit, "unit")
        _ordered_numbers(self.low, self.base, self.high, "capacity")
        if self.period_start is not None:
            _iso_date(self.period_start, "period_start")
        if self.period_end is not None:
            _iso_date(self.period_end, "period_end")
        if self.period_start and self.period_end and self.period_end <= self.period_start:
            raise ValueError("period_end must be later than period_start")


@dataclass(frozen=True, slots=True)
class ResourceValue:
    resource_type: str
    unit: str
    low: float
    base: float
    high: float
    period_start: str | None = None
    period_end: str | None = None

    @property
    def kind(self) -> ValueKind:
        return ValueKind.RESOURCE

    def __post_init__(self) -> None:
        _required(self.resource_type, "resource_type")
        _required(self.unit, "unit")
        _ordered_numbers(self.low, self.base, self.high, "resource")
        if self.period_start is not None:
            _iso_date(self.period_start, "period_start")
        if self.period_end is not None:
            _iso_date(self.period_end, "period_end")
        if self.period_start and self.period_end and self.period_end <= self.period_start:
            raise ValueError("period_end must be later than period_start")


@dataclass(frozen=True, slots=True)
class ConstraintValue:
    constraint_type: str
    status: ConstraintStatus
    severity: ConstraintSeverity
    description: str
    constrained_entity_id: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> ValueKind:
        return ValueKind.CONSTRAINT

    def __post_init__(self) -> None:
        _required(self.constraint_type, "constraint_type")
        _required(self.description, "description")
        _optional_text(self.constrained_entity_id, "constrained_entity_id")
        _json_object(self.attributes, "attributes")


ClaimValue: TypeAlias = (
    ScalarValue
    | GeometryValue
    | RelationshipValue
    | MilestoneValue
    | CapabilityValue
    | CapacityValue
    | ResourceValue
    | ConstraintValue
)
