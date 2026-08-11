"""Import verified Taiwan MOENV EMS_S_01 candidates into the claim store.

The importer deliberately keeps MOENV identities and assertions source-native.
An EMS control number identifies one MOENV facility record; it is not an Atlas
cross-source identity resolution, an owner/operator assertion, or proof that a
semiconductor facility is operating.  Conflicting rows for one control number
remain visible as parallel, value-dimensioned claims.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

from .adapters.moenv_ems import (
    MOENV_ATTRIBUTION,
    MOENV_DATASET_URL,
    MOENV_FIELDS,
    MOENV_FILTER_VERSION,
    MOENV_FLAG_FIELDS,
    MOENV_INDUSTRY_GROUP,
    MOENV_INDUSTRY_LABELS,
    MOENV_LICENSE,
    MOENV_LICENSE_URL,
    MOENV_RELEASE_DATE_FIELDS,
    MOENVFacility,
    is_currently_regulated,
    valid_taiwan_wgs84_point,
)
from .models import (
    ClaimKind,
    ClaimSeries,
    ClaimValue,
    ClaimVersion,
    DependencyKind,
    DependencyLink,
    Entity,
    EntityKind,
    EvidenceLink,
    EvidenceRole,
    GeometryValue,
    IngestionRun,
    IngestionRunDocument,
    IngestionStatus,
    ScalarType,
    ScalarValue,
    Source,
    SourceDocument,
    SourceFamily,
    SourceRecord,
    ValueKind,
    source_record_payload_sha256,
)
from .moenv_snapshot import (
    MOENV_COVERAGE,
    MOENV_DATASET_UPDATED_AT_BASES,
    MOENV_LICENSE_SPDX,
    MOENV_SCOPE,
    VerifiedMOENVSnapshot,
    verify_moenv_snapshot,
)
from .repository import (
    add_claim_series,
    add_entity,
    add_ingestion_run,
    add_ingestion_run_document,
    add_source,
    add_source_document,
    add_source_family,
    add_source_record,
    current_claims,
    insert_claim,
    stable_id,
    validate_database,
    value_sha256,
)


IMPORTER_VERSION = "taiwan-moenv-ems-s-01-import-v1"
SOURCE_FAMILY_KEY = "taiwan-moenv-ems"
SOURCE_KEY = "taiwan-moenv-ems:ems_s_01"
ACCEPTANCE_TIMESTAMP_BASES = frozenset(
    {
        "explicit_operator_supplied",
        "process_clock_after_snapshot_verification",
    }
)

_SAVEPOINTS = itertools.count()
_SOURCE_NOTES = (
    "Exact Taiwan MOENV EMS_S_01 source statement. The environmental-control "
    "registry includes current and historically deregistered records; this claim "
    "does not establish facility operation, production, ownership, or capacity."
)
_RELEASE_DATE_NOTES = (
    "Exact EMS_S_01 release-from-environmental-regulation date. It is not a "
    "facility closure, production-stop, or operating-status date."
)
_CLASSIFICATIONS = {
    "2611": "integrated_circuit_manufacturing_facility_candidate",
    "2612": "discrete_semiconductor_manufacturing_facility_candidate",
    "2613": "semiconductor_packaging_and_test_facility_candidate",
}
_TEXT_FIELDS = (
    ("emsno", "moenv.emsno"),
    ("facilityname", "name"),
    ("facilityaddress", "address.street"),
    ("county", "address.county"),
    ("township", "address.township"),
    ("industrygroup", "moenv.industry_group"),
    ("industryid", "moenv.industry_code"),
    ("industryname", "moenv.industry_label"),
    ("industryareaname", "moenv.industrial_area"),
    ("uniformno", "moenv.uniformno"),
    ("facno", "moenv.facno"),
    ("admino", "moenv.admino"),
    ("wgs84lon", "moenv.wgs84_longitude_raw"),
    ("wgs84lat", "moenv.wgs84_latitude_raw"),
    ("twd97tm2x", "moenv.twd97_tm2_x_raw"),
    ("twd97tm2y", "moenv.twd97_tm2_y_raw"),
)
_FLAG_PREDICATES = {
    "isair": "moenv.environmental_control.air_raw",
    "iswater": "moenv.environmental_control.water_raw",
    "iswaste": "moenv.environmental_control.waste_raw",
    "istoxic": "moenv.environmental_control.toxic_raw",
    "issoil": "moenv.environmental_control.soil_raw",
}
_RELEASE_DATE_PREDICATES = {
    "airreleasedate": "moenv.environmental_control.air_release_date",
    "waterreleasedate": "moenv.environmental_control.water_release_date",
    "wastereleasedate": "moenv.environmental_control.waste_release_date",
    "toxicreleasedate": "moenv.environmental_control.toxic_release_date",
    "soilreleasedate": "moenv.environmental_control.soil_release_date",
}


@dataclass(frozen=True, slots=True)
class MOENVImportResult:
    source_family_id: str
    source_id: str
    candidate_document_id: str
    raw_document_id: str
    ingestion_run_id: str
    accepted_at: str
    source_updated_at: str
    acceptance_timestamp_basis: str
    complete_refresh: bool
    replayed_existing_run: bool
    facilities_imported: int
    variants_imported: int
    source_documents_created: int
    source_records_created: int
    entities_created: int
    claim_series_created: int
    claims_created: int
    unchanged_claims_reused: int
    prior_open_claims_closed_or_corrected: int
    candidate_entities_no_longer_selected: int
    facility_entity_ids: tuple[str, ...]
    source_record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ClaimSpec:
    predicate: str
    dimension: str
    value: ClaimValue
    claim_kind: ClaimKind
    method: str
    confidence: float
    evidence: tuple[EvidenceLink, ...] = ()
    dependencies: tuple[DependencyLink, ...] = ()
    notes: str | None = None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalize_timestamp(value: str, field_name: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError(f"{field_name} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _clock(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _completed_at(started_at: str) -> str:
    return (_clock(started_at) + timedelta(seconds=1)).isoformat().replace(
        "+00:00", "Z"
    )


def _source_date(snapshot: VerifiedMOENVSnapshot) -> str:
    expected_basis = "official_dataset_page_displayed_asia_taipei"
    if (
        snapshot.dataset_updated_at_basis != expected_basis
        or snapshot.dataset_updated_at_basis not in MOENV_DATASET_UPDATED_AT_BASES
    ):
        raise ValueError(
            "MOENV dataset_updated_at_basis cannot be mapped to publisher-local time"
        )
    updated_at = _normalize_timestamp(snapshot.dataset_updated_at, "dataset_updated_at")
    return _clock(updated_at).astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()


def _existing_created_at(
    connection: sqlite3.Connection,
    table: str,
    identifier: str,
    default: str,
) -> str:
    row = connection.execute(
        f"SELECT created_at FROM {table} WHERE id = ?", (identifier,)
    ).fetchone()
    return str(row["created_at"]) if row is not None else default


@contextmanager
def _atomic_import(connection: sqlite3.Connection) -> Iterator[None]:
    savepoint = f"moenv_import_{next(_SAVEPOINTS)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except BaseException:
        connection.execute(f"ROLLBACK TO {savepoint}")
        connection.execute(f"RELEASE {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE {savepoint}")


def _verify_snapshot_identity(
    supplied: VerifiedMOENVSnapshot,
    refreshed: VerifiedMOENVSnapshot,
) -> None:
    if supplied != refreshed:
        raise ValueError(
            "MOENV verified snapshot identity changed before database import"
        )


def _ensure_document(
    connection: sqlite3.Connection, document: SourceDocument
) -> bool:
    """Reuse the same acquired bytes across importer metadata versions."""

    existing = connection.execute(
        "SELECT * FROM source_documents WHERE id = ?", (document.id,)
    ).fetchone()
    if existing is None:
        return add_source_document(connection, document)
    expected = {
        "source_id": document.source_id,
        "document_url": document.document_url,
        "title": document.title,
        "published_at": document.published_at,
        "retrieved_at": document.retrieved_at,
        "content_sha256": document.content_sha256,
        "media_type": document.media_type,
        "license": document.license,
    }
    conflicts = [key for key, expected_value in expected.items() if existing[key] != expected_value]
    if conflicts:
        raise ValueError(
            f"MOENV source document {document.id} conflicts on byte-intrinsic fields: "
            + ", ".join(conflicts)
        )
    return False


def _ensure_entity(
    connection: sqlite3.Connection,
    *,
    entity_id: str,
    stable_key: str,
    display_name: str | None,
    created_at: str,
    run_id: str,
) -> bool:
    existing = connection.execute(
        "SELECT * FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if existing is not None:
        if (
            existing["kind"] != EntityKind.FACILITY.value
            or existing["stable_key"] != stable_key
        ):
            raise ValueError(f"entity {entity_id} conflicts with MOENV identity")
        return False
    return add_entity(
        connection,
        Entity(
            entity_id,
            EntityKind.FACILITY,
            stable_key,
            created_at,
            display_name=display_name,
            created_by_run_id=run_id,
        ),
    )


def _ensure_series(
    connection: sqlite3.Connection,
    *,
    entity_id: str,
    entity_key: str,
    predicate: str,
    value_kind: ValueKind,
    dimension: str,
    created_at: str,
) -> tuple[str, bool]:
    series_id = _series_id(entity_id, predicate, value_kind, dimension)
    stable_key = f"{entity_key}:claim:{predicate}:{dimension}"
    existing = connection.execute(
        "SELECT * FROM claim_series WHERE id = ?", (series_id,)
    ).fetchone()
    if existing is not None:
        expected = {
            "subject_entity_id": entity_id,
            "stable_key": stable_key,
            "predicate": predicate,
            "value_kind": value_kind.value,
        }
        conflicts = [key for key, expected_value in expected.items() if existing[key] != expected_value]
        if conflicts:
            raise ValueError(
                f"MOENV claim series {series_id} conflicts on: {', '.join(conflicts)}"
            )
        return series_id, False
    return series_id, add_claim_series(
        connection,
        ClaimSeries(
            series_id,
            entity_id,
            stable_key,
            predicate,
            value_kind,
            created_at,
        ),
    )


def _series_id(
    entity_id: str,
    predicate: str,
    value_kind: ValueKind,
    dimension: str,
) -> str:
    return stable_id(
        "claim-series", entity_id, predicate, value_kind.value, dimension
    )


def _value_dimension(field: str, value: object) -> str:
    digest = hashlib.sha256(
        _canonical_json({"field": field, "value": value}).encode("utf-8")
    ).hexdigest()[:20]
    return f"value:{digest}"


def _variant_rows(facility: MOENVFacility) -> tuple[Mapping[str, str | None], ...]:
    variants = tuple(sorted(facility.variants, key=lambda item: item.canonical_json))
    if not variants:
        raise ValueError(f"MOENV facility {facility.emsno} has no source variants")
    rows: list[Mapping[str, str | None]] = []
    seen: set[bytes] = set()
    for variant in variants:
        if variant.canonical_json in seen:
            raise ValueError(f"MOENV facility {facility.emsno} repeats a variant")
        seen.add(variant.canonical_json)
        if set(variant.row) != set(MOENV_FIELDS):
            raise ValueError(f"MOENV facility {facility.emsno} has schema drift")
        if variant.row["emsno"] != facility.emsno:
            raise ValueError(f"MOENV facility {facility.emsno} has a mismatched variant")
        rows.append(variant.row)
    return tuple(rows)


def _record_payload(facility: MOENVFacility) -> dict[str, object]:
    return {
        "dataset_id": "EMS_S_01",
        "emsno": facility.emsno,
        "variants": [dict(row) for row in _variant_rows(facility)],
    }


def _field_evidence(
    document_id: str,
    record_id: str,
    facility: MOENVFacility,
    field: str,
    value: object,
) -> tuple[EvidenceLink, ...]:
    links: list[EvidenceLink] = []
    for variant_index, row in enumerate(_variant_rows(facility)):
        if row[field] != value:
            continue
        links.append(
            EvidenceLink(
                document_id,
                source_record_id=record_id,
                locator=f"source_record.variants[{variant_index}].{field}",
                excerpt=_canonical_json(
                    {
                        "emsno": facility.emsno,
                        "field": field,
                        "value": value,
                        "variant_index": variant_index,
                    }
                ),
            )
        )
    if not links:
        raise AssertionError(f"MOENV evidence cannot locate {field}={value!r}")
    return tuple(links)


def _distinct_values(
    facility: MOENVFacility, field: str
) -> tuple[str, ...]:
    values = {
        value
        for row in _variant_rows(facility)
        if isinstance((value := row[field]), str) and value != ""
    }
    return tuple(sorted(values))


def _invariant_display_name(facility: MOENVFacility) -> str | None:
    values = _distinct_values(facility, "facilityname")
    return values[0] if len(values) == 1 else None


def _invariant_point(facility: MOENVFacility) -> tuple[str, str] | None:
    points = tuple(valid_taiwan_wgs84_point(row) for row in _variant_rows(facility))
    if not points or any(point is None for point in points):
        return None
    first = points[0]
    if any(point != first for point in points[1:]):
        return None
    assert first is not None
    return first


def _source_specs(
    facility: MOENVFacility,
    *,
    candidate_document_id: str,
    record_id: str,
) -> tuple[_ClaimSpec, ...]:
    specs: list[_ClaimSpec] = []
    for field, predicate in _TEXT_FIELDS:
        for value in _distinct_values(facility, field):
            specs.append(
                _ClaimSpec(
                    predicate,
                    _value_dimension(field, value),
                    ScalarValue(ScalarType.TEXT, value),
                    ClaimKind.SOURCE_STATEMENT,
                    f"moenv_ems_s_01_{field}_exact_capture_v1",
                    1.0,
                    evidence=_field_evidence(
                        candidate_document_id, record_id, facility, field, value
                    ),
                    notes=_SOURCE_NOTES,
                )
            )
    for field in MOENV_FLAG_FIELDS:
        predicate = _FLAG_PREDICATES[field]
        for value in _distinct_values(facility, field):
            if value not in {"0", "1"}:
                raise ValueError(
                    f"MOENV facility {facility.emsno} has invalid {field} flag"
                )
            specs.append(
                _ClaimSpec(
                    predicate,
                    _value_dimension(field, value),
                    ScalarValue(ScalarType.TEXT, value),
                    ClaimKind.SOURCE_STATEMENT,
                    f"moenv_ems_s_01_{field}_exact_capture_v1",
                    1.0,
                    evidence=_field_evidence(
                        candidate_document_id, record_id, facility, field, value
                    ),
                    notes=(
                        _SOURCE_NOTES
                        + " The exact 0/1 token denotes regulatory-category membership, "
                        "not operating status."
                    ),
                )
            )
    for field in MOENV_RELEASE_DATE_FIELDS:
        predicate = _RELEASE_DATE_PREDICATES[field]
        for value in _distinct_values(facility, field):
            try:
                parsed = date.fromisoformat(value)
            except ValueError as error:
                raise ValueError(
                    f"MOENV facility {facility.emsno} has invalid {field} date"
                ) from error
            if parsed.isoformat() != value:
                raise ValueError(
                    f"MOENV facility {facility.emsno} has noncanonical {field} date"
                )
            specs.append(
                _ClaimSpec(
                    predicate,
                    _value_dimension(field, value),
                    ScalarValue(ScalarType.DATE, value),
                    ClaimKind.SOURCE_STATEMENT,
                    f"moenv_ems_s_01_{field}_exact_capture_v1",
                    1.0,
                    evidence=_field_evidence(
                        candidate_document_id, record_id, facility, field, value
                    ),
                    notes=_RELEASE_DATE_NOTES,
                )
            )
    return tuple(
        sorted(
            specs,
            key=lambda item: (
                item.claim_kind.value,
                item.predicate,
                item.dimension,
            ),
        )
    )


def _derived_specs(
    facility: MOENVFacility,
    source_claim_ids: Mapping[tuple[str, str], str],
) -> tuple[_ClaimSpec, ...]:
    specs: list[_ClaimSpec] = []
    point = _invariant_point(facility)
    if point is not None:
        longitude, latitude = point
        coordinate_dependencies = tuple(
            DependencyLink(source_claim_ids[(predicate, value)])
            for predicate, value in (
                ("moenv.wgs84_longitude_raw", longitude),
                ("moenv.wgs84_latitude_raw", latitude),
            )
        )
        specs.append(
            _ClaimSpec(
                "geometry",
                "invariant-wgs84-point",
                GeometryValue(
                    {
                        "type": "Point",
                        "coordinates": [float(longitude), float(latitude)],
                    }
                ),
                ClaimKind.DERIVED_ESTIMATE,
                "moenv_ems_s_01_invariant_wgs84_point_v1",
                1.0,
                dependencies=coordinate_dependencies,
                notes=(
                    "Deterministic GeoJSON conversion of one valid WGS84 pair "
                    "present identically in every retained source variant. It is "
                    "not independently surveyed."
                ),
            )
        )

    flag_predicates = frozenset(_FLAG_PREDICATES.values())
    flag_dependencies = tuple(
        DependencyLink(claim_id)
        for (predicate, _value), claim_id in sorted(source_claim_ids.items())
        if predicate in flag_predicates
    )
    if len(flag_dependencies) < len(MOENV_FLAG_FIELDS):
        raise ValueError(
            f"MOENV facility {facility.emsno} lacks complete flag lineage"
        )
    rows = _variant_rows(facility)
    currently_regulated = any(is_currently_regulated(row) for row in rows)
    specs.append(
        _ClaimSpec(
            "currently_environmentally_regulated",
            "five-category-union",
            ScalarValue(ScalarType.BOOLEAN, currently_regulated),
            ClaimKind.DERIVED_ESTIMATE,
            "moenv_ems_s_01_any_environmental_control_flag_v1",
            1.0,
            dependencies=flag_dependencies,
            notes=(
                "True exactly when at least one retained source variant has a "
                "1 in any of the five MOENV environmental-control flags. This "
                "is regulatory membership only, not facility operating, "
                "production, lifecycle, or capacity status."
            ),
        )
    )

    for code in _distinct_values(facility, "industryid"):
        if code not in _CLASSIFICATIONS:
            raise ValueError(
                f"MOENV facility {facility.emsno} has unexpected industry code"
            )
        code_claim_id = source_claim_ids[("moenv.industry_code", code)]
        specs.append(
            _ClaimSpec(
                "candidate_classification",
                f"industry-code:{code}",
                ScalarValue(ScalarType.TEXT, _CLASSIFICATIONS[code]),
                ClaimKind.DERIVED_ESTIMATE,
                MOENV_FILTER_VERSION,
                1.0,
                dependencies=(DependencyLink(code_claim_id),),
                notes=(
                    f"Deterministic discovery classification from exact EMS_S_01 "
                    f"industry code {code}. Confidence applies only to filter "
                    "membership; it is not the probability of an operating "
                    "semiconductor facility and asserts no subtype beyond the "
                    "source code, ownership, production, or capacity."
                ),
            )
        )
    return tuple(specs)


def _active_prior_claims(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    run_id: str,
    accepted_at: str,
) -> tuple[dict[str, sqlite3.Row], dict[str, tuple[DependencyLink, ...]]]:
    rows = connection.execute(
        """
        SELECT versions.id, versions.series_id, versions.value_kind,
               versions.value_sha256, versions.valid_from, versions.recorded_at,
               versions.superseded_at, versions.claim_kind, versions.method,
               versions.confidence, versions.notes, series.subject_entity_id,
               series.predicate, entities.stable_key
        FROM ingestion_runs AS runs
        JOIN claim_versions AS versions ON versions.created_by_run_id = runs.id
        JOIN claim_series AS series ON series.id = versions.series_id
        JOIN entities ON entities.id = series.subject_entity_id
        WHERE runs.source_id = ?
          AND versions.created_by_run_id != ?
          AND julianday(versions.recorded_at) < julianday(?)
          AND (
                versions.superseded_at IS NULL
                OR julianday(versions.superseded_at) >= julianday(?)
              )
          AND versions.valid_to IS NULL
        ORDER BY
          CASE versions.claim_kind WHEN 'derived_estimate' THEN 1 ELSE 0 END,
          versions.series_id,
          versions.id
        """,
        (source_id, run_id, accepted_at, accepted_at),
    ).fetchall()
    claims: dict[str, sqlite3.Row] = {}
    for row in rows:
        if row["series_id"] in claims:
            raise ValueError(
                f"MOENV series {row['series_id']} has multiple active open claims"
            )
        claims[str(row["series_id"])] = row
    dependencies: dict[str, list[DependencyLink]] = {str(row["id"]): [] for row in rows}
    for row in connection.execute(
        """
        SELECT dependencies.claim_version_id,
               dependencies.depends_on_claim_version_id,
               dependencies.dependency_kind
        FROM claim_dependencies AS dependencies
        JOIN claim_versions AS versions ON versions.id = dependencies.claim_version_id
        JOIN ingestion_runs AS runs ON runs.id = versions.created_by_run_id
        WHERE runs.source_id = ?
          AND versions.created_by_run_id != ?
          AND julianday(versions.recorded_at) < julianday(?)
          AND (
                versions.superseded_at IS NULL
                OR julianday(versions.superseded_at) >= julianday(?)
              )
          AND versions.valid_to IS NULL
        ORDER BY dependencies.claim_version_id,
                 dependencies.depends_on_claim_version_id,
                 dependencies.dependency_kind
        """,
        (source_id, run_id, accepted_at, accepted_at),
    ):
        dependencies.setdefault(str(row["claim_version_id"]), []).append(
            DependencyLink(
                str(row["depends_on_claim_version_id"]),
                DependencyKind(row["dependency_kind"]),
            )
        )
    return claims, {key: tuple(value) for key, value in dependencies.items()}


def _stored_value(connection: sqlite3.Connection, row: sqlite3.Row) -> ClaimValue:
    if row["value_kind"] == ValueKind.SCALAR.value:
        scalar = connection.execute(
            "SELECT * FROM scalar_values WHERE claim_version_id = ?", (row["id"],)
        ).fetchone()
        if scalar is None:
            raise ValueError(f"MOENV claim {row['id']} lacks its scalar value")
        scalar_type = ScalarType(scalar["scalar_type"])
        if scalar_type in {ScalarType.TEXT, ScalarType.DATE, ScalarType.TIMESTAMP}:
            value: str | float | int | bool = scalar["text_value"]
        elif scalar_type is ScalarType.NUMBER:
            value = float(scalar["number_value"])
        elif scalar_type is ScalarType.INTEGER:
            value = int(scalar["integer_value"])
        else:
            value = bool(scalar["boolean_value"])
        return ScalarValue(scalar_type, value, scalar["unit"])
    if row["value_kind"] == ValueKind.GEOMETRY.value:
        geometry = connection.execute(
            "SELECT * FROM geometry_values WHERE claim_version_id = ?", (row["id"],)
        ).fetchone()
        if geometry is None:
            raise ValueError(f"MOENV claim {row['id']} lacks its geometry value")
        return GeometryValue(
            json.loads(geometry["geometry_json"]),
            geometry["crs"],
            geometry["precision_m"],
        )
    raise ValueError(
        f"MOENV claim {row['id']} has unsupported value kind {row['value_kind']}"
    )


def _prior_evidence(
    connection: sqlite3.Connection, claim_version_id: str
) -> tuple[EvidenceLink, ...]:
    return tuple(
        EvidenceLink(
            row["source_document_id"],
            role=EvidenceRole(row["role"]),
            source_record_id=row["source_record_id"],
            locator=row["locator"],
            excerpt=row["excerpt"],
        )
        for row in connection.execute(
            """
            SELECT source_document_id, source_record_id, role, locator, excerpt
            FROM claim_evidence
            WHERE claim_version_id = ?
            ORDER BY id
            """,
            (claim_version_id,),
        )
    )


def _claim_matches(
    prior: sqlite3.Row,
    spec: _ClaimSpec,
    prior_dependencies: Mapping[str, Sequence[DependencyLink]],
) -> bool:
    if (
        prior["value_sha256"] != value_sha256(spec.value)
        or prior["claim_kind"] != spec.claim_kind.value
        or prior["method"] != spec.method
        or float(prior["confidence"]) != float(spec.confidence)
        or prior["notes"] != spec.notes
    ):
        return False
    expected = {
        (link.depends_on_claim_version_id, link.kind.value)
        for link in spec.dependencies
    }
    actual = {
        (link.depends_on_claim_version_id, link.kind.value)
        for link in prior_dependencies.get(str(prior["id"]), ())
    }
    return expected == actual


def _close_prior_claim(
    connection: sqlite3.Connection,
    prior: sqlite3.Row,
    *,
    run_id: str,
    candidate_document_id: str,
    accepted_at: str,
    valid_to: str,
    closure_basis: str,
    closure_ids: dict[str, str],
    prior_dependencies: Mapping[str, Sequence[DependencyLink]],
) -> bool:
    if _clock(str(prior["recorded_at"])) >= _clock(accepted_at):
        raise ValueError("MOENV refresh is not later than its prior active claim")
    if str(prior["valid_from"]) > valid_to:
        raise ValueError("MOENV source update predates an active prior claim")
    if str(prior["valid_from"]) == valid_to:
        connection.execute(
            "UPDATE claim_versions SET superseded_at = ? WHERE id = ?",
            (accepted_at, prior["id"]),
        )
        return False

    dependencies = tuple(
        DependencyLink(
            closure_ids.get(
                dependency.depends_on_claim_version_id,
                dependency.depends_on_claim_version_id,
            ),
            dependency.kind,
        )
        for dependency in prior_dependencies.get(str(prior["id"]), ())
    )
    evidence = _prior_evidence(connection, str(prior["id"]))
    if ClaimKind(prior["claim_kind"]) is not ClaimKind.DERIVED_ESTIMATE:
        evidence = (
            *evidence,
            EvidenceLink(
                candidate_document_id,
                role=EvidenceRole.CONTEXT,
                locator=(
                    f"source_record_scope;emsno={str(prior['stable_key']).split(':')[-1]};"
                    f"source_updated_date={valid_to};interval_closure"
                ),
                excerpt=_canonical_json(
                    {
                        "closure_basis": closure_basis,
                        "prior_claim_version_id": prior["id"],
                        "valid_to": valid_to,
                    }
                ),
            ),
        )
    closure_id = stable_id(
        "claim-version",
        IMPORTER_VERSION,
        "valid-time-closure",
        prior["id"],
        run_id,
        valid_to,
    )
    created = insert_claim(
        connection,
        ClaimVersion(
            closure_id,
            str(prior["series_id"]),
            str(prior["valid_from"]),
            accepted_at,
            ClaimKind(prior["claim_kind"]),
            "moenv_ems_s_01_source_interval_closure_v1",
            float(prior["confidence"]),
            valid_to=valid_to,
            created_by_run_id=run_id,
            notes=(
                "A later verified same-filter MOENV source snapshot closes only the "
                f"prior source-assertion interval at {valid_to}. {closure_basis}. "
                "It does not assert real-world facility closure, inactivity, or a "
                "production stop."
            ),
        ),
        _stored_value(connection, prior),
        evidence=evidence,
        dependencies=dependencies,
    )
    closure_ids[str(prior["id"])] = closure_id
    return created


def _assert_chronology(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    run_id: str,
    accepted_at: str,
    retrieved_at: str,
    source_updated_at: str,
) -> None:
    later_acceptance = connection.execute(
        """
        SELECT id FROM ingestion_runs
        WHERE source_id = ? AND id != ?
          AND julianday(started_at) >= julianday(?)
        ORDER BY started_at, id LIMIT 1
        """,
        (source_id, run_id, accepted_at),
    ).fetchone()
    if later_acceptance is not None:
        raise ValueError("MOENV snapshots must be imported in database-acceptance order")

    for row in connection.execute(
        """
        SELECT id, parameters_json FROM ingestion_runs
        WHERE source_id = ? AND id != ? AND status = 'succeeded'
        ORDER BY started_at, id
        """,
        (source_id, run_id),
    ):
        try:
            parameters = json.loads(row["parameters_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"prior MOENV run {row['id']} has invalid parameters") from error
        prior_retrieved = _normalize_timestamp(
            parameters.get("source_retrieved_at"),
            f"prior MOENV run {row['id']} source_retrieved_at",
        )
        prior_updated = _normalize_timestamp(
            parameters.get("dataset_updated_at"),
            f"prior MOENV run {row['id']} dataset_updated_at",
        )
        if _clock(prior_retrieved) > _clock(retrieved_at):
            raise ValueError("MOENV snapshots must be imported in source-retrieval order")
        if _clock(prior_updated) > _clock(source_updated_at):
            raise ValueError("MOENV dataset_updated_at must be non-decreasing")


def _verified_rights(snapshot: VerifiedMOENVSnapshot) -> dict[str, object]:
    try:
        manifest = json.loads(snapshot.manifest_bytes.decode("utf-8"))
        rights = manifest["source_scopes"][MOENV_SCOPE]["rights"]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("MOENV verified manifest lacks rights provenance") from error
    if not isinstance(rights, dict):
        raise ValueError("MOENV verified manifest rights provenance is invalid")
    return dict(rights)


def _document_models(
    snapshot: VerifiedMOENVSnapshot,
    source_id: str,
) -> tuple[SourceDocument, SourceDocument]:
    try:
        acquisition_request_body = json.loads(
            snapshot.acquisition.canonical_request_body.decode("utf-8")
        )
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("MOENV verified acquisition request body is invalid") from error
    acquisition_method = snapshot.acquisition.method
    acquisition_url = snapshot.acquisition.url
    if (
        acquisition_method != "POST"
        or acquisition_url != snapshot.download_url
        or not isinstance(acquisition_request_body, dict)
        or _canonical_json(acquisition_request_body).encode("utf-8")
        != snapshot.acquisition.canonical_request_body
    ):
        raise ValueError("MOENV verified acquisition provenance conflicts")
    rights = _verified_rights(snapshot)
    published_at = snapshot.dataset_updated_at
    candidate_id = stable_id(
        "source-document",
        source_id,
        MOENV_DATASET_URL,
        snapshot.retrieved_at,
        snapshot.candidate_sha256,
        "candidate-derivative",
    )
    raw_id = stable_id(
        "source-document",
        source_id,
        snapshot.download_url,
        snapshot.retrieved_at,
        snapshot.raw_sha256,
        "raw-archive",
    )
    common = {
        "attribution": MOENV_ATTRIBUTION,
        "dataset_id": "EMS_S_01",
        "dataset_updated_at": snapshot.dataset_updated_at,
        "dataset_updated_at_basis": snapshot.dataset_updated_at_basis,
        "license_spdx": MOENV_LICENSE_SPDX,
        "license_url": MOENV_LICENSE_URL,
        "manifest_sha256": snapshot.manifest_sha256,
        "retrieval_timestamp_basis": snapshot.retrieval_timestamp_basis,
        "rights": rights,
        "rights_decision": "pass_with_required_attribution",
        "source_scope": MOENV_SCOPE,
    }
    candidate = SourceDocument(
        candidate_id,
        source_id,
        MOENV_DATASET_URL,
        "Taiwan MOENV EMS_S_01 exact semiconductor-code candidate derivative",
        snapshot.retrieved_at,
        snapshot.candidate_sha256,
        published_at=published_at,
        media_type="application/x-ndjson",
        license=MOENV_LICENSE,
        metadata={
            **common,
            "artifact_kind": "deterministic_filtered_derivative",
            "bytes": snapshot.candidate_size,
            "conflicting_facility_count": snapshot.scan.conflicting_facility_count,
            "current_regulation_count": snapshot.current_regulation_count,
            "exact_industry_code_variant_counts": dict(snapshot.code_counts),
            "facility_count": snapshot.facility_count,
            "filter_version": MOENV_FILTER_VERSION,
            "industry_group_row_count": snapshot.scan.industry_group_row_count,
            "raw_archive_sha256": snapshot.raw_sha256,
            "raw_matching_row_count": snapshot.scan.candidate_row_count,
            "record_count": snapshot.candidate_count,
            "upstream_row_count": snapshot.row_count,
            "valid_coordinate_count": snapshot.valid_coordinate_count,
        },
    )
    raw = SourceDocument(
        raw_id,
        source_id,
        snapshot.download_url,
        "Taiwan MOENV EMS_S_01 official full-package archive",
        snapshot.retrieved_at,
        snapshot.raw_sha256,
        published_at=published_at,
        media_type="application/zip",
        license=MOENV_LICENSE,
        metadata={
            **common,
            "acquisition_method": acquisition_method,
            "acquisition_request_body": acquisition_request_body,
            "acquisition_url": acquisition_url,
            "artifact_kind": "upstream_official_full_package_archive",
            "bytes": snapshot.raw_size,
            "candidate_derivative_sha256": snapshot.candidate_sha256,
            "member_md5": snapshot.member_md5,
            "member_name": snapshot.member_name,
            "member_sha256": snapshot.member_sha256,
            "publisher_md5": snapshot.publisher_md5,
            "raw_retention_path": snapshot.raw_path,
        },
    )
    return candidate, raw


def _claim_dependencies(
    connection: sqlite3.Connection, claim_version_id: str
) -> tuple[DependencyLink, ...]:
    return tuple(
        DependencyLink(
            str(row["depends_on_claim_version_id"]),
            DependencyKind(row["dependency_kind"]),
        )
        for row in connection.execute(
            """
            SELECT depends_on_claim_version_id, dependency_kind
            FROM claim_dependencies
            WHERE claim_version_id = ?
            ORDER BY depends_on_claim_version_id, dependency_kind
            """,
            (claim_version_id,),
        )
    )


def _verify_replay_claim_ledger(
    connection: sqlite3.Connection,
    *,
    snapshot: VerifiedMOENVSnapshot,
    run: IngestionRun,
    candidate_document: SourceDocument,
    complete_refresh: bool,
) -> None:
    """Recompute snapshot propositions and the exact run-created claim ID set."""

    valid_from = _source_date(snapshot)
    current_rows = current_claims(
        connection,
        as_of=valid_from,
        recorded_at=run.started_at,
    )
    source_run_ids = {
        str(row["id"])
        for row in connection.execute(
            "SELECT id FROM ingestion_runs WHERE source_id = ?",
            (run.source_id,),
        )
    }
    source_current = {
        str(row["series_id"]): row
        for row in current_rows
        if row["created_by_run_id"] in source_run_ids
    }
    expected_series_ids: set[str] = set()
    expected_current_run_claim_ids: set[str] = set()

    def verify_spec(
        *,
        entity_id: str,
        entity_key: str,
        record_id: str,
        spec: _ClaimSpec,
    ) -> str:
        series_id = _series_id(
            entity_id, spec.predicate, spec.value.kind, spec.dimension
        )
        expected_series_ids.add(series_id)
        series = connection.execute(
            "SELECT * FROM claim_series WHERE id = ?", (series_id,)
        ).fetchone()
        expected_series_key = f"{entity_key}:claim:{spec.predicate}:{spec.dimension}"
        if (
            series is None
            or series["subject_entity_id"] != entity_id
            or series["stable_key"] != expected_series_key
            or series["predicate"] != spec.predicate
            or series["value_kind"] != spec.value.kind.value
        ):
            raise ValueError(f"MOENV replay claim series {series_id} conflicts")
        claim = source_current.get(series_id)
        if claim is None:
            raise ValueError(f"MOENV replay is missing expected claim series {series_id}")
        dependencies = _claim_dependencies(connection, str(claim["id"]))
        if not _claim_matches(
            claim,
            spec,
            {str(claim["id"]): dependencies},
        ):
            raise ValueError(f"MOENV replay claim {claim['id']} conflicts")
        if claim["created_by_run_id"] == run.id:
            expected_claim_id = stable_id(
                "claim-version",
                IMPORTER_VERSION,
                series_id,
                record_id,
                valid_from,
            )
            if claim["id"] != expected_claim_id:
                raise ValueError(
                    f"MOENV replay claim {claim['id']} has a nondeterministic identity"
                )
            if not connection.execute(
                "SELECT 1 FROM claim_versions WHERE id = ?", (expected_claim_id,)
            ).fetchone():
                raise ValueError(f"MOENV replay is missing claim {expected_claim_id}")
            # Existing-claim verification checks the complete immutable value,
            # evidence, and dependency lineage without issuing a write.
            if insert_claim(
                connection,
                ClaimVersion(
                    expected_claim_id,
                    series_id,
                    valid_from,
                    run.started_at,
                    spec.claim_kind,
                    spec.method,
                    spec.confidence,
                    created_by_run_id=run.id,
                    notes=spec.notes,
                ),
                spec.value,
                evidence=spec.evidence,
                dependencies=spec.dependencies,
            ):
                raise AssertionError("MOENV replay unexpectedly inserted a claim")
            expected_current_run_claim_ids.add(expected_claim_id)
        return str(claim["id"])

    for facility in snapshot.scan.facilities:
        record_key = f"taiwan-moenv-ems:ems_s_01:{facility.emsno}"
        record_id = stable_id("source-record", run.id, record_key)
        entity_id = stable_id("entity", record_key)
        source_claim_ids: dict[tuple[str, str], str] = {}
        for spec in _source_specs(
            facility,
            candidate_document_id=candidate_document.id,
            record_id=record_id,
        ):
            claim_id = verify_spec(
                entity_id=entity_id,
                entity_key=record_key,
                record_id=record_id,
                spec=spec,
            )
            assert isinstance(spec.value, ScalarValue)
            source_claim_ids[(spec.predicate, str(spec.value.value))] = claim_id
        for spec in _derived_specs(facility, source_claim_ids):
            verify_spec(
                entity_id=entity_id,
                entity_key=record_key,
                record_id=record_id,
                spec=spec,
            )

    if complete_refresh and set(source_current) != expected_series_ids:
        unexpected = sorted(set(source_current) - expected_series_ids)
        missing = sorted(expected_series_ids - set(source_current))
        raise ValueError(
            "MOENV replay current same-filter claim set conflicts "
            f"(missing={missing!r}, unexpected={unexpected!r})"
        )
    if not expected_series_ids.issubset(source_current):
        raise ValueError("MOENV replay is missing current snapshot claim series")

    superseded_priors = connection.execute(
        """
        SELECT versions.id, versions.valid_from
        FROM claim_versions AS versions
        JOIN ingestion_runs AS producer ON producer.id = versions.created_by_run_id
        WHERE producer.source_id = ?
          AND versions.created_by_run_id != ?
          AND versions.superseded_at = ?
        ORDER BY versions.id
        """,
        (run.source_id, run.id, run.started_at),
    ).fetchall()
    expected_closure_ids = {
        stable_id(
            "claim-version",
            IMPORTER_VERSION,
            "valid-time-closure",
            str(prior["id"]),
            run.id,
            valid_from,
        )
        for prior in superseded_priors
        if str(prior["valid_from"]) < valid_from
    }
    for closure_id in expected_closure_ids:
        closure = connection.execute(
            """
            SELECT valid_to, recorded_at, created_by_run_id, method
            FROM claim_versions WHERE id = ?
            """,
            (closure_id,),
        ).fetchone()
        if (
            closure is None
            or closure["valid_to"] != valid_from
            or closure["recorded_at"] != run.started_at
            or closure["created_by_run_id"] != run.id
            or closure["method"] != "moenv_ems_s_01_source_interval_closure_v1"
        ):
            raise ValueError(f"MOENV replay closure claim {closure_id} conflicts")
    actual_run_claim_ids = {
        str(row["id"])
        for row in connection.execute(
            "SELECT id FROM claim_versions WHERE created_by_run_id = ?", (run.id,)
        )
    }
    expected_run_claim_ids = expected_current_run_claim_ids | expected_closure_ids
    if actual_run_claim_ids != expected_run_claim_ids:
        raise ValueError(
            "MOENV replay run-created claim ledger conflicts "
            f"(missing={sorted(expected_run_claim_ids - actual_run_claim_ids)!r}, "
            f"unexpected={sorted(actual_run_claim_ids - expected_run_claim_ids)!r})"
        )


def _existing_run_result(
    connection: sqlite3.Connection,
    *,
    snapshot: VerifiedMOENVSnapshot,
    run: IngestionRun,
    candidate_document: SourceDocument,
    raw_document: SourceDocument,
    complete_refresh: bool,
    acceptance_timestamp_basis: str,
) -> MOENVImportResult:
    row = connection.execute(
        "SELECT * FROM ingestion_runs WHERE id = ?", (run.id,)
    ).fetchone()
    if row is None:
        raise AssertionError("MOENV replay verifier requires an existing run")
    expected = {
        "source_id": run.source_id,
        "input_document_id": run.input_document_id,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "status": IngestionStatus.SUCCEEDED.value,
        "code_version": run.code_version,
        "parameters_json": _canonical_json(run.parameters),
        "error": None,
    }
    conflicts = [key for key, expected_value in expected.items() if row[key] != expected_value]
    if conflicts:
        raise ValueError(
            "MOENV replay conflicts with immutable ingestion run fields: "
            + ", ".join(conflicts)
        )
    for document in (candidate_document, raw_document):
        existing = connection.execute(
            "SELECT * FROM source_documents WHERE id = ?", (document.id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"MOENV replay is missing source document {document.id}")
        intrinsic = {
            "source_id": document.source_id,
            "document_url": document.document_url,
            "title": document.title,
            "published_at": document.published_at,
            "retrieved_at": document.retrieved_at,
            "content_sha256": document.content_sha256,
            "media_type": document.media_type,
            "license": document.license,
            "metadata_json": _canonical_json(document.metadata),
        }
        if any(existing[key] != value for key, value in intrinsic.items()):
            raise ValueError(f"MOENV replay source document {document.id} conflicts")

    expected_roles = {
        (candidate_document.id, "primary"),
        (candidate_document.id, "candidate_derivative"),
        (candidate_document.id, "source_record"),
        (raw_document.id, "raw_archive"),
    }
    actual_roles = {
        (str(item["source_document_id"]), str(item["role"]))
        for item in connection.execute(
            """
            SELECT source_document_id, role FROM ingestion_run_documents
            WHERE ingestion_run_id = ?
            """,
            (run.id,),
        )
    }
    if actual_roles != expected_roles:
        raise ValueError("MOENV replay ingestion-run document lineage conflicts")

    expected_records: dict[str, tuple[str, str, str, str, str, str]] = {}
    entity_ids: list[str] = []
    record_ids: list[str] = []
    for facility in snapshot.scan.facilities:
        payload = _record_payload(facility)
        record_key = f"taiwan-moenv-ems:ems_s_01:{facility.emsno}"
        record_id = stable_id("source-record", run.id, record_key)
        expected_records[record_id] = (
            run.id,
            candidate_document.id,
            record_key,
            snapshot.retrieved_at,
            source_record_payload_sha256(payload),
            _canonical_json(payload),
        )
        record_ids.append(record_id)
        entity_key = record_key
        entity_id = stable_id("entity", entity_key)
        entity_ids.append(entity_id)
        entity = connection.execute(
            "SELECT kind, stable_key FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        if (
            entity is None
            or entity["kind"] != EntityKind.FACILITY.value
            or entity["stable_key"] != entity_key
        ):
            raise ValueError(f"MOENV replay is missing facility entity {entity_id}")
    actual_records = {
        str(item["id"]): (
            str(item["ingestion_run_id"]),
            str(item["source_document_id"]),
            str(item["source_record_key"]),
            str(item["observed_at"]),
            str(item["record_sha256"]),
            str(item["payload_json"]),
        )
        for item in connection.execute(
            """
            SELECT id, ingestion_run_id, source_document_id, source_record_key,
                   observed_at, record_sha256, payload_json
            FROM source_records
            WHERE ingestion_run_id = ?
            """,
            (run.id,),
        )
    }
    if actual_records != expected_records:
        raise ValueError("MOENV replay grouped source records conflict")
    _verify_replay_claim_ledger(
        connection,
        snapshot=snapshot,
        run=run,
        candidate_document=candidate_document,
        complete_refresh=complete_refresh,
    )
    errors = validate_database(connection)
    if errors:
        raise RuntimeError(
            "MOENV replay failed claim-store validation: " + "; ".join(errors)
        )
    return MOENVImportResult(
        source_family_id=stable_id("source-family", SOURCE_FAMILY_KEY),
        source_id=run.source_id,
        candidate_document_id=candidate_document.id,
        raw_document_id=raw_document.id,
        ingestion_run_id=run.id,
        accepted_at=run.started_at,
        source_updated_at=snapshot.dataset_updated_at,
        acceptance_timestamp_basis=acceptance_timestamp_basis,
        complete_refresh=complete_refresh,
        replayed_existing_run=True,
        facilities_imported=snapshot.facility_count,
        variants_imported=snapshot.candidate_count,
        source_documents_created=0,
        source_records_created=0,
        entities_created=0,
        claim_series_created=0,
        claims_created=0,
        unchanged_claims_reused=0,
        prior_open_claims_closed_or_corrected=0,
        candidate_entities_no_longer_selected=0,
        facility_entity_ids=tuple(entity_ids),
        source_record_ids=tuple(record_ids),
    )


def import_moenv_ems_candidates(
    connection: sqlite3.Connection,
    snapshot: VerifiedMOENVSnapshot,
    *,
    accepted_at: str,
    acceptance_timestamp_basis: str = "explicit_operator_supplied",
    complete_refresh: bool = True,
) -> MOENVImportResult:
    """Import one immutable, verified EMS_S_01 exact-code snapshot.

    ``complete_refresh`` controls source-assertion interval closure only.  Even a
    complete refresh is not a complete Taiwanese semiconductor-facility census,
    and absence never becomes a lifecycle or operating-status assertion.
    """

    if not isinstance(snapshot, VerifiedMOENVSnapshot):
        raise TypeError("snapshot must be a VerifiedMOENVSnapshot")
    if not isinstance(complete_refresh, bool):
        raise ValueError("complete_refresh must be a boolean")
    refreshed = verify_moenv_snapshot(snapshot.root)
    _verify_snapshot_identity(snapshot, refreshed)
    snapshot = refreshed
    accepted_at = _normalize_timestamp(accepted_at, "accepted_at")
    if acceptance_timestamp_basis not in ACCEPTANCE_TIMESTAMP_BASES:
        raise ValueError("MOENV import has an invalid acceptance timestamp basis")
    if _clock(accepted_at) < _clock(snapshot.retrieved_at):
        raise ValueError("MOENV accepted_at must not predate snapshot retrieval")
    if _clock(snapshot.dataset_updated_at) > _clock(snapshot.retrieved_at):
        raise ValueError("MOENV dataset_updated_at must not postdate retrieval")
    valid_from = _source_date(snapshot)

    family_id = stable_id("source-family", SOURCE_FAMILY_KEY)
    source_id = stable_id("source", SOURCE_KEY)
    candidate_document, raw_document = _document_models(snapshot, source_id)
    run_id = stable_id(
        "ingestion-run",
        IMPORTER_VERSION,
        candidate_document.id,
        raw_document.id,
        snapshot.manifest_sha256,
        snapshot.dataset_updated_at,
        str(complete_refresh).casefold(),
    )
    parameters = {
        "accepted_at": accepted_at,
        "acceptance_timestamp_basis": acceptance_timestamp_basis,
        "candidate_derivative": {
            "bytes": snapshot.candidate_size,
            "sha256": snapshot.candidate_sha256,
        },
        "complete_refresh": complete_refresh,
        "conflicting_facility_count": snapshot.scan.conflicting_facility_count,
        "coverage": MOENV_COVERAGE,
        "current_regulation_count": snapshot.current_regulation_count,
        "dataset_updated_at": snapshot.dataset_updated_at,
        "dataset_updated_at_basis": snapshot.dataset_updated_at_basis,
        "exact_industry_codes": {
            code: MOENV_INDUSTRY_LABELS[code]
            for code in sorted(MOENV_INDUSTRY_LABELS)
        },
        "exact_industry_code_variant_counts": dict(snapshot.code_counts),
        "facility_count": snapshot.facility_count,
        "filter_version": MOENV_FILTER_VERSION,
        "industry_group": MOENV_INDUSTRY_GROUP,
        "industry_group_row_count": snapshot.scan.industry_group_row_count,
        "manifest_sha256": snapshot.manifest_sha256,
        "raw_archive": {
            "bytes": snapshot.raw_size,
            "path": snapshot.raw_path,
            "sha256": snapshot.raw_sha256,
        },
        "rights": _verified_rights(snapshot),
        "raw_matching_row_count": snapshot.scan.candidate_row_count,
        "source_retrieved_at": snapshot.retrieved_at,
        "source_scope": MOENV_SCOPE,
        "upstream_row_count": snapshot.row_count,
        "valid_coordinate_count": snapshot.valid_coordinate_count,
        "variant_count": snapshot.candidate_count,
    }
    run = IngestionRun(
        run_id,
        source_id,
        accepted_at,
        status=IngestionStatus.SUCCEEDED,
        completed_at=_completed_at(accepted_at),
        code_version=IMPORTER_VERSION,
        input_document_id=candidate_document.id,
        parameters=parameters,
    )
    existing_run = connection.execute(
        "SELECT id FROM ingestion_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if existing_run is not None:
        return _existing_run_result(
            connection,
            snapshot=snapshot,
            run=run,
            candidate_document=candidate_document,
            raw_document=raw_document,
            complete_refresh=complete_refresh,
            acceptance_timestamp_basis=acceptance_timestamp_basis,
        )

    source_documents_created = 0
    source_records_created = 0
    entities_created = 0
    series_created = 0
    claims_created = 0
    unchanged_reused = 0
    prior_closed = 0
    facility_entity_ids: list[str] = []
    source_record_ids: list[str] = []

    with _atomic_import(connection):
        _assert_chronology(
            connection,
            source_id=source_id,
            run_id=run_id,
            accepted_at=accepted_at,
            retrieved_at=snapshot.retrieved_at,
            source_updated_at=snapshot.dataset_updated_at,
        )
        add_source_family(
            connection,
            SourceFamily(
                family_id,
                SOURCE_FAMILY_KEY,
                "Taiwan MOENV Environmental Management System",
                _existing_created_at(
                    connection, "source_families", family_id, accepted_at
                ),
                description=(
                    "Taiwan environmental-control registry records used as source-native "
                    "facility discovery evidence, including deregistered history."
                ),
            ),
        )
        add_source(
            connection,
            Source(
                source_id,
                family_id,
                SOURCE_KEY,
                "Taiwan MOENV EMS_S_01 environmental-control facilities",
                "Taiwan Ministry of Environment",
                MOENV_DATASET_URL,
                _existing_created_at(connection, "sources", source_id, accepted_at),
                license=MOENV_LICENSE,
            ),
        )
        source_documents_created += int(_ensure_document(connection, candidate_document))
        source_documents_created += int(_ensure_document(connection, raw_document))
        add_ingestion_run(connection, run)
        add_ingestion_run_document(
            connection,
            IngestionRunDocument(run_id, candidate_document.id, "candidate_derivative"),
        )
        add_ingestion_run_document(
            connection,
            IngestionRunDocument(run_id, raw_document.id, "raw_archive"),
        )

        prior_claims, prior_dependencies = _active_prior_claims(
            connection,
            source_id=source_id,
            run_id=run_id,
            accepted_at=accepted_at,
        )
        prior_by_entity: dict[str, dict[str, sqlite3.Row]] = {}
        for series_id, prior in prior_claims.items():
            prior_by_entity.setdefault(str(prior["subject_entity_id"]), {})[
                series_id
            ] = prior
        prior_entity_ids = set(prior_by_entity)
        current_entity_ids: set[str] = set()
        seen_series: set[str] = set()
        closed_series: set[str] = set()
        closure_ids: dict[str, str] = {}

        def write_spec(
            *,
            entity_id: str,
            entity_key: str,
            record_id: str,
            spec: _ClaimSpec,
        ) -> str:
            nonlocal series_created, claims_created, unchanged_reused, prior_closed
            series_id, created = _ensure_series(
                connection,
                entity_id=entity_id,
                entity_key=entity_key,
                predicate=spec.predicate,
                value_kind=spec.value.kind,
                dimension=spec.dimension,
                created_at=accepted_at,
            )
            series_created += int(created)
            seen_series.add(series_id)
            prior = prior_claims.get(series_id)
            if prior is not None and _claim_matches(
                prior, spec, prior_dependencies
            ):
                unchanged_reused += 1
                return str(prior["id"])
            if prior is not None:
                claims_created += int(
                    _close_prior_claim(
                        connection,
                        prior,
                        run_id=run_id,
                        candidate_document_id=candidate_document.id,
                        accepted_at=accepted_at,
                        valid_to=valid_from,
                        closure_basis="later_same_filter_changed_value_or_lineage",
                        closure_ids=closure_ids,
                        prior_dependencies=prior_dependencies,
                    )
                )
                closed_series.add(series_id)
                prior_closed += 1
            claim_id = stable_id(
                "claim-version",
                IMPORTER_VERSION,
                series_id,
                record_id,
                valid_from,
            )
            created = insert_claim(
                connection,
                ClaimVersion(
                    claim_id,
                    series_id,
                    valid_from,
                    accepted_at,
                    spec.claim_kind,
                    spec.method,
                    spec.confidence,
                    created_by_run_id=run_id,
                    notes=spec.notes,
                ),
                spec.value,
                evidence=spec.evidence,
                dependencies=spec.dependencies,
            )
            claims_created += int(created)
            return claim_id

        for facility in snapshot.scan.facilities:
            record_payload = _record_payload(facility)
            record_key = f"taiwan-moenv-ems:ems_s_01:{facility.emsno}"
            record_id = stable_id("source-record", run_id, record_key)
            source_record_ids.append(record_id)
            source_records_created += int(
                add_source_record(
                    connection,
                    SourceRecord(
                        record_id,
                        run_id,
                        candidate_document.id,
                        record_key,
                        snapshot.retrieved_at,
                        source_record_payload_sha256(record_payload),
                        payload=record_payload,
                    ),
                )
            )

            entity_key = record_key
            entity_id = stable_id("entity", entity_key)
            facility_entity_ids.append(entity_id)
            current_entity_ids.add(entity_id)
            entities_created += int(
                _ensure_entity(
                    connection,
                    entity_id=entity_id,
                    stable_key=entity_key,
                    display_name=_invariant_display_name(facility),
                    created_at=accepted_at,
                    run_id=run_id,
                )
            )

            source_claim_ids: dict[tuple[str, str], str] = {}
            source_specs = _source_specs(
                facility,
                candidate_document_id=candidate_document.id,
                record_id=record_id,
            )
            for spec in source_specs:
                claim_id = write_spec(
                    entity_id=entity_id,
                    entity_key=entity_key,
                    record_id=record_id,
                    spec=spec,
                )
                assert isinstance(spec.value, ScalarValue)
                source_claim_ids[(spec.predicate, str(spec.value.value))] = claim_id

            if complete_refresh:
                for prior_series_id, prior in sorted(
                    prior_by_entity.get(entity_id, {}).items()
                ):
                    if (
                        prior_series_id in seen_series
                        or prior_series_id in closed_series
                        or prior["claim_kind"] == ClaimKind.DERIVED_ESTIMATE.value
                    ):
                        continue
                    claims_created += int(
                        _close_prior_claim(
                            connection,
                            prior,
                            run_id=run_id,
                            candidate_document_id=candidate_document.id,
                            accepted_at=accepted_at,
                            valid_to=valid_from,
                            closure_basis="later_same_filter_seen_entity_field_absence",
                            closure_ids=closure_ids,
                            prior_dependencies=prior_dependencies,
                        )
                    )
                    closed_series.add(prior_series_id)
                    prior_closed += 1

            for derived_spec in _derived_specs(facility, source_claim_ids):
                write_spec(
                    entity_id=entity_id,
                    entity_key=entity_key,
                    record_id=record_id,
                    spec=derived_spec,
                )

            if complete_refresh:
                for prior_series_id, prior in sorted(
                    prior_by_entity.get(entity_id, {}).items()
                ):
                    if prior_series_id in seen_series or prior_series_id in closed_series:
                        continue
                    claims_created += int(
                        _close_prior_claim(
                            connection,
                            prior,
                            run_id=run_id,
                            candidate_document_id=candidate_document.id,
                            accepted_at=accepted_at,
                            valid_to=valid_from,
                            closure_basis="later_same_filter_seen_entity_derived_absence",
                            closure_ids=closure_ids,
                            prior_dependencies=prior_dependencies,
                        )
                    )
                    closed_series.add(prior_series_id)
                    prior_closed += 1

        if complete_refresh:
            stale = [
                (series_id, prior)
                for series_id, prior in prior_claims.items()
                if series_id not in seen_series and series_id not in closed_series
            ]
            stale.sort(
                key=lambda item: (
                    item[1]["claim_kind"] == ClaimKind.DERIVED_ESTIMATE.value,
                    item[0],
                )
            )
            for series_id, prior in stale:
                claims_created += int(
                    _close_prior_claim(
                        connection,
                        prior,
                        run_id=run_id,
                        candidate_document_id=candidate_document.id,
                        accepted_at=accepted_at,
                        valid_to=valid_from,
                        closure_basis="later_complete_same_filter_nonselection",
                        closure_ids=closure_ids,
                        prior_dependencies=prior_dependencies,
                    )
                )
                closed_series.add(series_id)
                prior_closed += 1

        errors = validate_database(connection)
        if errors:
            raise RuntimeError(
                "MOENV import failed claim-store validation: " + "; ".join(errors)
            )
        final_snapshot = verify_moenv_snapshot(snapshot.root)
        _verify_snapshot_identity(snapshot, final_snapshot)

    return MOENVImportResult(
        source_family_id=family_id,
        source_id=source_id,
        candidate_document_id=candidate_document.id,
        raw_document_id=raw_document.id,
        ingestion_run_id=run_id,
        accepted_at=accepted_at,
        source_updated_at=snapshot.dataset_updated_at,
        acceptance_timestamp_basis=acceptance_timestamp_basis,
        complete_refresh=complete_refresh,
        replayed_existing_run=False,
        facilities_imported=snapshot.facility_count,
        variants_imported=snapshot.candidate_count,
        source_documents_created=source_documents_created,
        source_records_created=source_records_created,
        entities_created=entities_created,
        claim_series_created=series_created,
        claims_created=claims_created,
        unchanged_claims_reused=unchanged_reused,
        prior_open_claims_closed_or_corrected=prior_closed,
        candidate_entities_no_longer_selected=(
            len(prior_entity_ids - current_entity_ids) if complete_refresh else 0
        ),
        facility_entity_ids=tuple(facility_entity_ids),
        source_record_ids=tuple(source_record_ids),
    )


# Short alias for callers that already name the dataset in their command surface.
import_moenv_snapshot = import_moenv_ems_candidates


__all__ = [
    "ACCEPTANCE_TIMESTAMP_BASES",
    "IMPORTER_VERSION",
    "MOENVImportResult",
    "SOURCE_FAMILY_KEY",
    "SOURCE_KEY",
    "import_moenv_ems_candidates",
    "import_moenv_snapshot",
]
