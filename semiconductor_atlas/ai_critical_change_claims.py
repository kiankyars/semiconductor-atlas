"""Structural claim validation for standalone AI-critical v1 change bundles.

This checks the producer's emitted claim/value vocabulary without claiming that
an evidence hash proves source support. Exact source provenance still requires
validation against the bound releases. The published producer stays unchanged.
"""

from __future__ import annotations

import re
from typing import Any, Container, Mapping

from .ai_critical import (
    CAPACITY_BASES,
    CAPACITY_INPUT_OUTPUT_BASES,
    CAPACITY_METRIC_UNITS,
    CAPACITY_QUANTITY_SEMANTICS,
    CLAIM_KINDS,
    COMPANIES,
    EVIDENCE_ROLES,
    IDENTITY_SCOPES,
    LIFECYCLE_STATES,
    READINESS_STATES,
    SCOPE_CATEGORIES,
    _decimal_number,
    _identifier,
    _iso_date,
    _iso_timestamp,
    _require_keys,
    _sha256,
    _string_list,
    _text,
)


_SNAPSHOT_FIELDS = (
    "claim_id", "subject_entity_id", "subject_stable_key", "predicate",
    "value_kind", "value", "valid_from", "valid_to", "recorded_at",
    "claim_kind", "method", "notes", "evidence_ids", "evidence_links",
)
_TEXT_PREDICATES = frozenset({
    "facility.name", "cohort.company", "geography.country_code",
    "geography.country", "geography.admin1", "geography.city",
    "lifecycle.source_statement", "lifecycle.evidence_summary",
})
_CONCEPT_VOCABULARIES = {
    "facility.identity_scope": IDENTITY_SCOPES,
    "lifecycle.stage": LIFECYCLE_STATES,
}


def _object(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} field names must be strings")
    return value


def _enum(value: object, allowed: Container[str], context: str) -> str:
    result = _text(value, context)
    if result not in allowed:
        raise ValueError(f"{context} is not in the AI-critical v1 vocabulary")
    return result


def _scalar(value: Mapping[str, Any], predicate: str) -> None:
    _require_keys(value, required=("scalar_type", "value", "unit"), context="scalar")
    if value["unit"] is not None:
        raise ValueError("AI-critical v1 text and concept scalars must have null unit")
    if predicate in _TEXT_PREDICATES:
        if value["scalar_type"] != "text":
            raise ValueError(f"{predicate} must be a text scalar")
        text = _text(value["value"], "scalar.value")
        if predicate == "cohort.company" and text not in COMPANIES:
            raise ValueError("cohort.company is outside the seven-company cohort")
        if predicate == "geography.country_code" and not re.fullmatch(r"[A-Z]{2}", text):
            raise ValueError("geography.country_code must use ISO alpha-2 form")
    elif predicate in _CONCEPT_VOCABULARIES:
        if value["scalar_type"] != "controlled_concept":
            raise ValueError(f"{predicate} must be a controlled_concept scalar")
        _enum(value["value"], _CONCEPT_VOCABULARIES[predicate], "scalar.value")
    else:
        raise ValueError(f"unsupported AI-critical v1 scalar predicate: {predicate}")


def _capability(value: Mapping[str, Any], predicate: str) -> None:
    _require_keys(
        value,
        required=("capability_type", "category", "technology", "readiness"),
        context="capability",
    )
    if predicate != "facility.capability":
        raise ValueError("capability value requires facility.capability predicate")
    if value["capability_type"] != "ai_critical_manufacturing_activity":
        raise ValueError("capability.capability_type is invalid")
    _enum(value["category"], SCOPE_CATEGORIES, "capability.category")
    _text(value["technology"], "capability.technology")
    _enum(value["readiness"], READINESS_STATES, "capability.readiness")


def _capacity(value: Mapping[str, Any], predicate: str) -> None:
    _require_keys(
        value,
        required=(
            "metric", "basis", "unit", "low", "base", "high", "period_start",
            "period_end", "scope_kind", "input_output_basis", "quantity_semantics",
            "technology_scope",
        ),
        context="capacity",
    )
    metric = _enum(value["metric"], CAPACITY_METRIC_UNITS, "capacity.metric")
    if predicate != f"capacity.{metric}":
        raise ValueError("capacity predicate does not match its metric")
    _enum(value["basis"], CAPACITY_BASES, "capacity.basis")
    unit = _enum(value["unit"], CAPACITY_METRIC_UNITS[metric], "capacity.unit")
    low, base, high = (
        _decimal_number(value[key], f"capacity.{key}")
        for key in ("low", "base", "high")
    )
    if not 0 <= low <= base <= high:
        raise ValueError("capacity must satisfy 0 <= low <= base <= high")
    start, end = value["period_start"], value["period_end"]
    if start is not None:
        start = _iso_date(start, "capacity.period_start")
    if end is not None:
        end = _iso_date(end, "capacity.period_end")
    if start is not None and end is not None and end <= start:
        raise ValueError("capacity.period_end must be later than period_start")
    _enum(
        value["scope_kind"],
        {"project_addition", "facility_total", "production_unit_total"},
        "capacity.scope_kind",
    )
    _enum(
        value["input_output_basis"], CAPACITY_INPUT_OUTPUT_BASES,
        "capacity.input_output_basis",
    )
    if value["quantity_semantics"] != CAPACITY_QUANTITY_SEMANTICS[(metric, unit)]:
        raise ValueError("capacity.quantity_semantics does not match metric and unit")
    scope = _string_list(value["technology_scope"], "capacity.technology_scope", nonempty=True)
    if not set(scope) <= SCOPE_CATEGORIES:
        raise ValueError("capacity.technology_scope is outside the AI-critical vocabulary")


def _geometry(value: Mapping[str, Any], predicate: str) -> None:
    _require_keys(
        value,
        required=("type", "coordinates", "crs", "precision_m", "geometry_scope"),
        context="geometry",
    )
    if predicate != "geography.point" or value["type"] != "Point":
        raise ValueError("AI-critical v1 geometry must be a geography.point Point")
    if value["crs"] != "EPSG:4326":
        raise ValueError("geometry.crs must be EPSG:4326")
    coordinates = value["coordinates"]
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        raise ValueError("geometry.coordinates must be [longitude, latitude]")
    longitude = _decimal_number(coordinates[0], "geometry.longitude")
    latitude = _decimal_number(coordinates[1], "geometry.latitude")
    precision = _decimal_number(value["precision_m"], "geometry.precision_m")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90 or precision < 0:
        raise ValueError("geometry has invalid coordinate bounds or precision")
    if value["geometry_scope"] != "source_reported_facility_point":
        raise ValueError("geometry.geometry_scope must be facility-safe")


def validate_claim_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Reject malformed v1 snapshots, including unchanged/reaffirmed assertions.

    Unknown lifecycle/readiness values remain explicit vocabulary members. All
    five capacity bases are structurally recognized, not certified as supported
    by these checks. Unknown geography is represented by absence of a point
    claim, never by a fake or null-coordinate geometry claim.
    """
    snapshot = _object(snapshot, "claim snapshot")
    _require_keys(snapshot, required=_SNAPSHOT_FIELDS, context="claim snapshot")
    for key in ("claim_id", "subject_entity_id", "subject_stable_key", "predicate", "method"):
        _identifier(snapshot[key], f"claim.{key}")
    _enum(snapshot["claim_kind"], CLAIM_KINDS, "claim.claim_kind")
    valid_from = _iso_date(snapshot["valid_from"], "claim.valid_from")
    if snapshot["valid_to"] is not None:
        valid_to = _iso_date(snapshot["valid_to"], "claim.valid_to")
        if valid_to <= valid_from:
            raise ValueError("claim.valid_to must be later than valid_from")
    _iso_timestamp(snapshot["recorded_at"], "claim.recorded_at")
    if snapshot["notes"] is not None and not isinstance(snapshot["notes"], str):
        raise ValueError("claim.notes must be a string or null")

    evidence_ids = _string_list(snapshot["evidence_ids"], "claim.evidence_ids", nonempty=True)
    for evidence_id in evidence_ids:
        _identifier(evidence_id, "claim.evidence_id")
    links = snapshot["evidence_links"]
    if not isinstance(links, list):
        raise ValueError("claim.evidence_links must be an array")
    linked_ids: list[str] = []
    roles: list[str] = []
    for index, raw in enumerate(links):
        context = f"claim.evidence_links[{index}]"
        link = _object(raw, context)
        _require_keys(link, required=("evidence_id", "role", "fragment_sha256"), context=context)
        linked_ids.append(_identifier(link["evidence_id"], f"{context}.evidence_id"))
        roles.append(_enum(link["role"], EVIDENCE_ROLES, f"{context}.role"))
        _sha256(link["fragment_sha256"], f"{context}.fragment_sha256")
    if len(set(linked_ids)) != len(linked_ids):
        raise ValueError("claim.evidence_links must not contain duplicate evidence")
    if linked_ids != evidence_ids:
        raise ValueError("claim.evidence_links must match evidence_ids in order")
    if "support" not in roles:
        raise ValueError("claim must have at least one support evidence link")

    value = _object(snapshot["value"], "claim.value")
    kind = _enum(snapshot["value_kind"], {"scalar", "capability", "capacity", "geometry"}, "claim.value_kind")
    validators = {"scalar": _scalar, "capability": _capability, "capacity": _capacity, "geometry": _geometry}
    validators[kind](value, snapshot["predicate"])
