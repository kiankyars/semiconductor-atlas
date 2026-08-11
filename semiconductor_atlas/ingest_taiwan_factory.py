"""Import verified Taiwan registered-factory semiconductor candidates.

Factory-registration numbers remain source-native identities.  A retained row
is evidence that the registry published that administrative record and the
exact principal-product token ``261半導體``; it is not evidence of observed
operation, output, lifecycle, ownership, capacity, utilization, or yield.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

from .adapters.taiwan_factory_registry import (
    TAIWAN_FACTORY_ARCHIVE_URL,
    TAIWAN_FACTORY_ATTRIBUTION,
    TAIWAN_FACTORY_DATASET_URL,
    TAIWAN_FACTORY_FILTER_VERSION,
    TAIWAN_FACTORY_LICENSE,
    TAIWAN_FACTORY_LICENSE_URL,
    TAIWAN_FACTORY_PRODUCT_TOKEN,
    TAIWAN_FACTORY_PUBLIC_FIELDS,
    TaiwanFactoryRecord,
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
from .taiwan_factory_snapshot import (
    TAIWAN_FACTORY_CANDIDATE_FILENAME,
    TAIWAN_FACTORY_COVERAGE,
    TAIWAN_FACTORY_LICENSE_SPDX,
    TAIWAN_FACTORY_SCOPE,
    VerifiedTaiwanFactorySnapshot,
    verify_taiwan_factory_snapshot,
)


IMPORTER_VERSION = "taiwan-ida-registered-factory-import-v1"
SOURCE_FAMILY_KEY = "taiwan-ida-factory"
SOURCE_KEY = "taiwan-ida-factory:registered-factories"
ENTITY_KEY_PREFIX = "taiwan-ida-factory:registered-factories:"
SOURCE_FAMILY_NAME = "Taiwan IDA Registered Factory Registry"
SOURCE_FAMILY_DESCRIPTION = (
    "Taiwan national factory-registration records retained as source-native "
    "facility-discovery evidence."
)
SOURCE_NAME = "Taiwan registered-factory dataset 6569"
SOURCE_PUBLISHER = (
    "Taiwan Ministry of Economic Affairs, Industrial Development Administration"
)
ACCEPTANCE_TIMESTAMP_BASES = frozenset(
    {
        "explicit_operator_supplied",
        "process_clock_after_snapshot_verification",
    }
)

_SAVEPOINTS = itertools.count()
_SOURCE_NOTES = (
    "Exact source statement from Taiwan's registered-factory publication. "
    "The publisher's registration status is administrative and does not "
    "establish observed operation, output, lifecycle, ownership, capacity, "
    "utilization, or yield."
)
_CLASSIFICATION = "semiconductor_facility_candidate"
_SINGLE_FIELDS = (
    ("factory_name", "name"),
    ("factory_address", "address.street"),
    (
        "factory_registration_number",
        "taiwan_factory_registry.factory_registration_number",
    ),
    (
        "establishment_approval_case_number",
        "taiwan_factory_registry.establishment_approval_case_number",
    ),
    ("unified_business_number", "taiwan_factory_registry.unified_business_number"),
    ("administrative_area", "address.administrative_area"),
    ("organization_type", "taiwan_factory_registry.organization_type"),
    (
        "establishment_approved_at_raw",
        "taiwan_factory_registry.establishment_approved_at_raw",
    ),
    (
        "registration_approved_at_raw",
        "taiwan_factory_registry.registration_approved_at_raw",
    ),
    ("registration_status", "taiwan_factory_registry.registration_status"),
)
_TOKEN_FIELDS = (
    ("industry_categories", "taiwan_factory_registry.industry_category_token"),
    ("principal_products", "taiwan_factory_registry.principal_product_token"),
)


@dataclass(frozen=True, slots=True)
class TaiwanFactoryImportResult:
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


def _source_date(snapshot: VerifiedTaiwanFactorySnapshot) -> str:
    if snapshot.source_updated_at_basis != "http_last_modified_header":
        raise ValueError(
            "Taiwan factory source_updated_at_basis cannot be mapped to publisher time"
        )
    return _clock(snapshot.source_updated_at).astimezone(
        ZoneInfo("Asia/Taipei")
    ).date().isoformat()


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
    savepoint = f"taiwan_factory_import_{next(_SAVEPOINTS)}"
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
    supplied: VerifiedTaiwanFactorySnapshot,
    refreshed: VerifiedTaiwanFactorySnapshot,
) -> None:
    if supplied != refreshed:
        raise ValueError(
            "Taiwan factory verified snapshot identity changed before database import"
        )


def _manifest_scope(snapshot: VerifiedTaiwanFactorySnapshot) -> dict[str, object]:
    try:
        manifest = json.loads(snapshot.manifest_bytes.decode("utf-8"))
        scope = manifest["source_scopes"][TAIWAN_FACTORY_SCOPE]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Taiwan factory verified manifest lacks source scope") from error
    if not isinstance(scope, dict):
        raise ValueError("Taiwan factory verified source scope is invalid")
    return dict(scope)


def _ensure_document(
    connection: sqlite3.Connection, document: SourceDocument
) -> bool:
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
        "metadata_json": _canonical_json(document.metadata),
    }
    conflicts = [key for key, value in expected.items() if existing[key] != value]
    if conflicts:
        raise ValueError(
            f"Taiwan factory source document {document.id} conflicts on "
            + ", ".join(conflicts)
        )
    return False


def _entity_key(registration_number: str) -> str:
    return f"{ENTITY_KEY_PREFIX}{registration_number}"


def _ensure_entity(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    entity_id: str,
    stable_key: str,
    display_name: str,
    created_at: str,
    run_id: str,
) -> bool:
    existing = connection.execute(
        "SELECT * FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if existing is not None:
        _verify_entity_identity(
            connection,
            source_id=source_id,
            entity_id=entity_id,
            entity_key=stable_key,
        )
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


def _series_id(
    entity_id: str,
    predicate: str,
    value_kind: ValueKind,
    dimension: str,
) -> str:
    return stable_id(
        "claim-series", entity_id, predicate, value_kind.value, dimension
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
        conflicts = [key for key, value in expected.items() if existing[key] != value]
        if conflicts:
            raise ValueError(
                f"Taiwan factory claim series {series_id} conflicts on "
                + ", ".join(conflicts)
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


def _token_dimension(field: str, token: str) -> str:
    digest = hashlib.sha256(
        _canonical_json({"field": field, "token": token}).encode("utf-8")
    ).hexdigest()[:20]
    return f"token:{digest}"


def _record_payload(record: TaiwanFactoryRecord) -> dict[str, object]:
    return {
        "dataset_id": "6569",
        "factory_registration_number": record.factory_registration_number,
        "row": dict(record.row),
    }


def _record_from_payload(payload_json: str, *, context: str) -> TaiwanFactoryRecord:
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{context} has invalid payload JSON") from error
    if not isinstance(payload, dict) or set(payload) != {
        "dataset_id",
        "factory_registration_number",
        "row",
    }:
        raise ValueError(f"{context} payload schema drifted")
    registration_number = payload.get("factory_registration_number")
    row_value = payload.get("row")
    if (
        payload.get("dataset_id") != "6569"
        or not isinstance(registration_number, str)
        or not isinstance(row_value, dict)
        or set(row_value) != set(TAIWAN_FACTORY_PUBLIC_FIELDS)
    ):
        raise ValueError(f"{context} payload identity drifted")
    row: dict[str, str | tuple[str, ...]] = {}
    for field in TAIWAN_FACTORY_PUBLIC_FIELDS:
        value = row_value[field]
        if field in {"industry_categories", "principal_products"}:
            if not isinstance(value, list) or any(
                not isinstance(token, str) for token in value
            ):
                raise ValueError(f"{context} token payload drifted")
            row[field] = tuple(value)
        elif isinstance(value, str):
            row[field] = value
        else:
            raise ValueError(f"{context} scalar payload drifted")
    if row["factory_registration_number"] != registration_number:
        raise ValueError(f"{context} payload registration number drifted")
    return TaiwanFactoryRecord(
        registration_number,
        row,
        _canonical_json(row).encode("utf-8"),
    )


def _origin_record_row(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    entity_key: str,
    ingestion_run_id: str | None = None,
) -> sqlite3.Row:
    run_filter = "AND records.ingestion_run_id = ?" if ingestion_run_id else ""
    parameters: list[object] = [source_id, entity_key]
    if ingestion_run_id is not None:
        parameters.append(ingestion_run_id)
    row = connection.execute(
        f"""
        SELECT records.*, runs.started_at AS run_started_at,
               runs.parameters_json AS run_parameters_json,
               runs.input_document_id AS run_input_document_id,
               runs.status AS run_status,
               runs.code_version AS run_code_version
        FROM source_records AS records
        JOIN ingestion_runs AS runs ON runs.id = records.ingestion_run_id
        WHERE runs.source_id = ?
          AND records.source_record_key = ?
          {run_filter}
        ORDER BY julianday(runs.started_at), runs.id, records.id
        LIMIT 1
        """,
        parameters,
    ).fetchone()
    if row is None:
        suffix = f" in run {ingestion_run_id}" if ingestion_run_id else ""
        raise ValueError(
            f"Taiwan factory replay lacks origin record for {entity_key}{suffix}"
        )
    return row


def _verified_origin_record(
    row: sqlite3.Row, *, entity_key: str
) -> tuple[TaiwanFactoryRecord, str]:
    context = f"Taiwan factory origin {row['id']}"
    try:
        parameters = json.loads(row["run_parameters_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{context} has invalid run parameters") from error
    if not isinstance(parameters, dict):
        raise ValueError(f"{context} run parameters drifted")
    source_updated_at = _normalize_timestamp(
        parameters.get("source_updated_at"), f"{context} source_updated_at"
    )
    source_retrieved_at = _normalize_timestamp(
        parameters.get("source_retrieved_at"), f"{context} source_retrieved_at"
    )
    if (
        parameters.get("source_updated_at_basis")
        != "http_last_modified_header"
        or row["run_status"] != IngestionStatus.SUCCEEDED.value
        or row["run_code_version"] != IMPORTER_VERSION
        or row["source_record_key"] != entity_key
        or row["source_document_id"] != row["run_input_document_id"]
        or row["observed_at"] != source_retrieved_at
        or row["id"]
        != stable_id("source-record", row["ingestion_run_id"], entity_key)
    ):
        raise ValueError(f"{context} immutable provenance drifted")
    record = _record_from_payload(str(row["payload_json"]), context=context)
    payload = _record_payload(record)
    if (
        row["record_sha256"] != source_record_payload_sha256(payload)
        or str(row["payload_json"]) != _canonical_json(payload)
    ):
        raise ValueError(f"{context} payload hash drifted")
    valid_from = _clock(source_updated_at).astimezone(
        ZoneInfo("Asia/Taipei")
    ).date().isoformat()
    return record, valid_from


def _source_created_at(connection: sqlite3.Connection, source_id: str) -> str:
    row = connection.execute(
        """
        SELECT started_at
        FROM ingestion_runs
        WHERE source_id = ?
        ORDER BY julianday(started_at), id
        LIMIT 1
        """,
        (source_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Taiwan factory source has no ingestion run")
    return str(row["started_at"])


def _verify_entity_identity(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    entity_id: str,
    entity_key: str,
) -> None:
    origin = _origin_record_row(
        connection,
        source_id=source_id,
        entity_key=entity_key,
    )
    record, _valid_from = _verified_origin_record(origin, entity_key=entity_key)
    expected = {
        "kind": EntityKind.FACILITY.value,
        "stable_key": entity_key,
        "display_name": record.row["factory_name"],
        "created_at": origin["run_started_at"],
        "created_by_run_id": origin["ingestion_run_id"],
    }
    entity = connection.execute(
        "SELECT * FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if entity is None or any(entity[key] != value for key, value in expected.items()):
        raise ValueError(f"Taiwan factory replay facility entity {entity_id} conflicts")


def _field_evidence(
    *,
    document_id: str,
    record_id: str,
    registration_number: str,
    field: str,
    value: str,
    token_index: int | None = None,
) -> tuple[EvidenceLink, ...]:
    locator = f"source_record.row.{field}"
    if token_index is not None:
        locator += f"[{token_index}]"
    return (
        EvidenceLink(
            document_id,
            source_record_id=record_id,
            locator=locator,
            excerpt=_canonical_json(
                {
                    "factory_registration_number": registration_number,
                    "field": field,
                    "value": value,
                    **(
                        {"token_index": token_index}
                        if token_index is not None
                        else {}
                    ),
                }
            ),
        ),
    )


def _source_specs(
    record: TaiwanFactoryRecord,
    *,
    candidate_document_id: str,
    record_id: str,
) -> tuple[_ClaimSpec, ...]:
    row = record.row
    if row.get("factory_registration_number") != record.factory_registration_number:
        raise ValueError(
            f"Taiwan factory record {record.factory_registration_number} has mismatched identity"
        )
    specs: list[_ClaimSpec] = []
    for field, predicate in _SINGLE_FIELDS:
        value = row.get(field)
        if not isinstance(value, str):
            raise ValueError(
                f"Taiwan factory record {record.factory_registration_number} field {field} drifted"
            )
        if value == "":
            continue
        specs.append(
            _ClaimSpec(
                predicate,
                "source",
                ScalarValue(ScalarType.TEXT, value),
                ClaimKind.SOURCE_STATEMENT,
                f"taiwan_factory_registry_{field}_exact_capture_v1",
                1.0,
                evidence=_field_evidence(
                    document_id=candidate_document_id,
                    record_id=record_id,
                    registration_number=record.factory_registration_number,
                    field=field,
                    value=value,
                ),
                notes=_SOURCE_NOTES,
            )
        )
    for field, predicate in _TOKEN_FIELDS:
        tokens = row.get(field)
        if not isinstance(tokens, tuple) or any(
            not isinstance(token, str) or not token for token in tokens
        ):
            raise ValueError(
                f"Taiwan factory record {record.factory_registration_number} field {field} drifted"
            )
        seen_tokens: set[str] = set()
        for token_index, token in enumerate(tokens):
            # The publication contains repeated line tokens in some rows. The
            # source-record payload preserves them exactly; a semantic token
            # proposition is asserted once using its first source occurrence.
            if token in seen_tokens:
                continue
            seen_tokens.add(token)
            specs.append(
                _ClaimSpec(
                    predicate,
                    _token_dimension(field, token),
                    ScalarValue(ScalarType.TEXT, token),
                    ClaimKind.SOURCE_STATEMENT,
                    f"taiwan_factory_registry_{field}_exact_token_v1",
                    1.0,
                    evidence=_field_evidence(
                        document_id=candidate_document_id,
                        record_id=record_id,
                        registration_number=record.factory_registration_number,
                        field=field,
                        value=token,
                        token_index=token_index,
                    ),
                    notes=_SOURCE_NOTES,
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
    record: TaiwanFactoryRecord,
    source_claim_ids: Mapping[tuple[str, str], str],
) -> tuple[_ClaimSpec, ...]:
    dependency_id = source_claim_ids.get(
        (
            "taiwan_factory_registry.principal_product_token",
            TAIWAN_FACTORY_PRODUCT_TOKEN,
        )
    )
    if dependency_id is None:
        raise ValueError(
            f"Taiwan factory record {record.factory_registration_number} lacks exact 261 lineage"
        )
    return (
        _ClaimSpec(
            "candidate_classification",
            "exact-principal-product-261",
            ScalarValue(ScalarType.TEXT, _CLASSIFICATION),
            ClaimKind.DERIVED_ESTIMATE,
            "taiwan_factory_registry_exact_principal_product_261_candidate_v1",
            1.0,
            dependencies=(DependencyLink(dependency_id),),
            notes=(
                "Deterministic candidate classification from the exact source token "
                "261半導體. It asserts no observed operation, output, lifecycle, "
                "ownership, capacity, utilization, yield, process, or technology subtype."
            ),
        ),
    )


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
        series_id = str(row["series_id"])
        if series_id in claims:
            raise ValueError(
                f"Taiwan factory series {series_id} has multiple active open claims"
            )
        claims[series_id] = row
    dependencies: dict[str, list[DependencyLink]] = {
        str(row["id"]): [] for row in rows
    }
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
    if row["value_kind"] != ValueKind.SCALAR.value:
        raise ValueError(
            f"Taiwan factory claim {row['id']} has unsupported value kind {row['value_kind']}"
        )
    scalar = connection.execute(
        "SELECT * FROM scalar_values WHERE claim_version_id = ?", (row["id"],)
    ).fetchone()
    if scalar is None:
        raise ValueError(f"Taiwan factory claim {row['id']} lacks its scalar value")
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
) -> tuple[str | None, bool]:
    if _clock(str(prior["recorded_at"])) >= _clock(accepted_at):
        raise ValueError("Taiwan factory refresh is not later than its prior claim")
    if str(prior["valid_from"]) > valid_to:
        raise ValueError("Taiwan factory source update predates an active prior claim")
    connection.execute(
        "UPDATE claim_versions SET superseded_at = ? WHERE id = ?",
        (accepted_at, prior["id"]),
    )
    if str(prior["valid_from"]) == valid_to:
        return None, False

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
                locator="source_record_scope;source_assertion_interval_closure",
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
            "taiwan_factory_registry_source_interval_closure_v1",
            float(prior["confidence"]),
            valid_to=valid_to,
            created_by_run_id=run_id,
            notes=(
                "A later verified same-filter registered-factory snapshot closes only "
                f"the prior source-assertion interval at {valid_to}. {closure_basis}. "
                "It does not assert real-world closure, inactivity, cancellation, "
                "production stop, ownership, output, or capacity."
            ),
        ),
        _stored_value(connection, prior),
        evidence=evidence,
        dependencies=dependencies,
    )
    closure_ids[str(prior["id"])] = closure_id
    return closure_id, created


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
        raise ValueError(
            "Taiwan factory snapshots must be imported in database-acceptance order"
        )
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
            raise ValueError(
                f"prior Taiwan factory run {row['id']} has invalid parameters"
            ) from error
        prior_retrieved = _normalize_timestamp(
            parameters.get("source_retrieved_at"),
            f"prior Taiwan factory run {row['id']} source_retrieved_at",
        )
        prior_updated = _normalize_timestamp(
            parameters.get("source_updated_at"),
            f"prior Taiwan factory run {row['id']} source_updated_at",
        )
        if _clock(prior_retrieved) > _clock(retrieved_at):
            raise ValueError(
                "Taiwan factory snapshots must be imported in source-retrieval order"
            )
        if _clock(prior_updated) > _clock(source_updated_at):
            raise ValueError("Taiwan factory source_updated_at must be non-decreasing")


def _document_models(
    snapshot: VerifiedTaiwanFactorySnapshot,
    source_id: str,
) -> tuple[SourceDocument, SourceDocument]:
    static = {
        "attribution": TAIWAN_FACTORY_ATTRIBUTION,
        "dataset_id": "6569",
        "license_spdx": TAIWAN_FACTORY_LICENSE_SPDX,
        "license_url": TAIWAN_FACTORY_LICENSE_URL,
        "source_scope": TAIWAN_FACTORY_SCOPE,
    }
    candidate_id = stable_id(
        "source-document",
        source_id,
        TAIWAN_FACTORY_DATASET_URL,
        snapshot.retrieved_at,
        snapshot.candidate_sha256,
        "privacy-minimized-candidate-derivative",
    )
    raw_id = stable_id(
        "source-document",
        source_id,
        TAIWAN_FACTORY_ARCHIVE_URL,
        snapshot.retrieved_at,
        snapshot.raw_sha256,
        "raw-archive",
    )
    candidate = SourceDocument(
        candidate_id,
        source_id,
        TAIWAN_FACTORY_DATASET_URL,
        "Taiwan registered-factory exact 261 semiconductor candidate derivative",
        snapshot.retrieved_at,
        snapshot.candidate_sha256,
        published_at=snapshot.source_updated_at,
        media_type="application/x-ndjson",
        license=TAIWAN_FACTORY_LICENSE,
        metadata={
            **static,
            "artifact_kind": "deterministic_privacy_minimized_filtered_derivative",
            "bytes": snapshot.candidate_size,
            "record_count": snapshot.candidate_count,
        },
    )
    raw = SourceDocument(
        raw_id,
        source_id,
        TAIWAN_FACTORY_ARCHIVE_URL,
        "Taiwan registered-factory official national archive",
        snapshot.retrieved_at,
        snapshot.raw_sha256,
        published_at=snapshot.source_updated_at,
        media_type="application/zip",
        license=TAIWAN_FACTORY_LICENSE,
        metadata={
            **static,
            "artifact_kind": "upstream_official_national_archive",
            "bytes": snapshot.raw_size,
            "member_name": snapshot.member_name,
            "member_sha256": snapshot.member_sha256,
            "member_size": snapshot.member_size,
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


@dataclass(frozen=True, slots=True)
class _ExactClaim:
    version: ClaimVersion
    value: ClaimValue
    evidence: tuple[EvidenceLink, ...]
    dependencies: tuple[DependencyLink, ...]


@dataclass(frozen=True, slots=True)
class _OriginContext:
    row: sqlite3.Row
    record: TaiwanFactoryRecord
    valid_from: str
    source_specs: tuple[_ClaimSpec, ...]


def _origin_context(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    entity_key: str,
    ingestion_run_id: str,
    cache: dict[tuple[str, str], _OriginContext],
) -> _OriginContext:
    key = (ingestion_run_id, entity_key)
    cached = cache.get(key)
    if cached is not None:
        return cached
    row = _origin_record_row(
        connection,
        source_id=source_id,
        entity_key=entity_key,
        ingestion_run_id=ingestion_run_id,
    )
    record, valid_from = _verified_origin_record(row, entity_key=entity_key)
    context = _OriginContext(
        row,
        record,
        valid_from,
        _source_specs(
            record,
            candidate_document_id=str(row["source_document_id"]),
            record_id=str(row["id"]),
        ),
    )
    cache[key] = context
    return context


def _expected_origin_claim(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    claim_version_id: str,
    claim_cache: dict[str, _ExactClaim],
    origin_cache: dict[tuple[str, str], _OriginContext],
) -> _ExactClaim:
    cached = claim_cache.get(claim_version_id)
    if cached is not None:
        return cached
    claim = connection.execute(
        """
        SELECT versions.*, series.subject_entity_id, series.predicate,
               series.value_kind AS series_value_kind,
               entities.stable_key AS entity_key
        FROM claim_versions AS versions
        JOIN claim_series AS series ON series.id = versions.series_id
        JOIN entities ON entities.id = series.subject_entity_id
        JOIN ingestion_runs AS runs ON runs.id = versions.created_by_run_id
        WHERE versions.id = ? AND runs.source_id = ?
        """,
        (claim_version_id, source_id),
    ).fetchone()
    if claim is None:
        raise ValueError(
            f"Taiwan factory replay lacks source claim {claim_version_id}"
        )
    if claim["valid_to"] is not None:
        raise ValueError(
            f"Taiwan factory origin claim {claim_version_id} is not an open assertion"
        )
    context = _origin_context(
        connection,
        source_id=source_id,
        entity_key=str(claim["entity_key"]),
        ingestion_run_id=str(claim["created_by_run_id"]),
        cache=origin_cache,
    )
    matching_source = [
        spec
        for spec in context.source_specs
        if _series_id(
            str(claim["subject_entity_id"]),
            spec.predicate,
            spec.value.kind,
            spec.dimension,
        )
        == claim["series_id"]
    ]
    if len(matching_source) > 1:
        raise ValueError(
            f"Taiwan factory origin claim {claim_version_id} has ambiguous source specs"
        )
    if matching_source:
        spec = matching_source[0]
    else:
        origin_current = {
            str(row["series_id"]): row
            for row in current_claims(
                connection,
                as_of=context.valid_from,
                recorded_at=str(context.row["run_started_at"]),
                subject_entity_id=str(claim["subject_entity_id"]),
            )
        }
        source_claim_ids: dict[tuple[str, str], str] = {}
        for source_spec in context.source_specs:
            source_series_id = _series_id(
                str(claim["subject_entity_id"]),
                source_spec.predicate,
                source_spec.value.kind,
                source_spec.dimension,
            )
            source_claim = origin_current.get(source_series_id)
            if source_claim is None:
                raise ValueError(
                    f"Taiwan factory origin lacks source series {source_series_id}"
                )
            exact_source = _expected_origin_claim(
                connection,
                source_id=source_id,
                claim_version_id=str(source_claim["id"]),
                claim_cache=claim_cache,
                origin_cache=origin_cache,
            )
            _verify_exact_claim(
                connection,
                exact_source,
                context=f"Taiwan factory origin source {source_claim['id']}",
            )
            assert isinstance(source_spec.value, ScalarValue)
            source_claim_ids[
                (source_spec.predicate, str(source_spec.value.value))
            ] = str(source_claim["id"])
        matching_derived = [
            spec
            for spec in _derived_specs(context.record, source_claim_ids)
            if _series_id(
                str(claim["subject_entity_id"]),
                spec.predicate,
                spec.value.kind,
                spec.dimension,
            )
            == claim["series_id"]
        ]
        if len(matching_derived) != 1:
            raise ValueError(
                f"Taiwan factory origin claim {claim_version_id} has no exact spec"
            )
        spec = matching_derived[0]
    expected_id = stable_id(
        "claim-version",
        IMPORTER_VERSION,
        claim["series_id"],
        context.row["id"],
        context.valid_from,
    )
    if claim_version_id != expected_id:
        raise ValueError(
            f"Taiwan factory origin claim {claim_version_id} has nondeterministic identity"
        )
    expected = _ExactClaim(
        ClaimVersion(
            expected_id,
            str(claim["series_id"]),
            context.valid_from,
            str(context.row["run_started_at"]),
            spec.claim_kind,
            spec.method,
            spec.confidence,
            created_by_run_id=str(context.row["ingestion_run_id"]),
            notes=spec.notes,
        ),
        spec.value,
        spec.evidence,
        spec.dependencies,
    )
    claim_cache[claim_version_id] = expected
    return expected


def _verify_exact_claim(
    connection: sqlite3.Connection,
    expected: _ExactClaim,
    *,
    context: str,
) -> None:
    if connection.execute(
        "SELECT 1 FROM claim_versions WHERE id = ?", (expected.version.id,)
    ).fetchone() is None:
        raise ValueError(f"{context} is missing")
    try:
        created = insert_claim(
            connection,
            expected.version,
            expected.value,
            evidence=expected.evidence,
            dependencies=expected.dependencies,
        )
    except ValueError as error:
        raise ValueError(f"{context} conflicts: {error}") from error
    if created:
        raise AssertionError(f"{context} replay unexpectedly inserted a claim")


def _verify_replay_claim_ledger(
    connection: sqlite3.Connection,
    *,
    snapshot: VerifiedTaiwanFactorySnapshot,
    run: IngestionRun,
    candidate_document: SourceDocument,
    complete_refresh: bool,
) -> None:
    valid_from = _source_date(snapshot)
    source_run_ids = {
        str(row["id"])
        for row in connection.execute(
            "SELECT id FROM ingestion_runs WHERE source_id = ?", (run.source_id,)
        )
    }
    source_current: dict[str, sqlite3.Row] = {}
    for row in current_claims(
        connection, as_of=valid_from, recorded_at=run.started_at
    ):
        if row["created_by_run_id"] not in source_run_ids:
            continue
        series_id = str(row["series_id"])
        if series_id in source_current:
            raise ValueError(
                f"Taiwan factory replay has duplicate current series {series_id}"
            )
        source_current[series_id] = row

    expected_series_ids: set[str] = set()
    snapshot_entity_ids: set[str] = set()
    expected_current_run_claim_ids: set[str] = set()
    exact_claim_cache: dict[str, _ExactClaim] = {}
    origin_cache: dict[tuple[str, str], _OriginContext] = {}

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
        if (
            series is None
            or series["subject_entity_id"] != entity_id
            or series["stable_key"]
            != f"{entity_key}:claim:{spec.predicate}:{spec.dimension}"
            or series["predicate"] != spec.predicate
            or series["value_kind"] != spec.value.kind.value
        ):
            raise ValueError(
                f"Taiwan factory replay claim series {series_id} conflicts"
            )
        claim = source_current.get(series_id)
        if claim is None:
            raise ValueError(
                f"Taiwan factory replay is missing expected claim series {series_id}"
            )
        dependencies = _claim_dependencies(connection, str(claim["id"]))
        if not _claim_matches(
            claim, spec, {str(claim["id"]): dependencies}
        ):
            raise ValueError(f"Taiwan factory replay claim {claim['id']} conflicts")
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
                    f"Taiwan factory replay claim {claim['id']} has nondeterministic identity"
                )
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
                raise AssertionError(
                    "Taiwan factory replay unexpectedly inserted a claim"
                )
            expected_current_run_claim_ids.add(expected_claim_id)
        else:
            exact = _expected_origin_claim(
                connection,
                source_id=run.source_id,
                claim_version_id=str(claim["id"]),
                claim_cache=exact_claim_cache,
                origin_cache=origin_cache,
            )
            _verify_exact_claim(
                connection,
                exact,
                context=f"Taiwan factory reused claim {claim['id']}",
            )
        return str(claim["id"])

    for record in snapshot.scan.records:
        entity_key = _entity_key(record.factory_registration_number)
        entity_id = stable_id("entity", entity_key)
        snapshot_entity_ids.add(entity_id)
        record_id = stable_id("source-record", run.id, entity_key)
        source_claim_ids: dict[tuple[str, str], str] = {}
        for spec in _source_specs(
            record,
            candidate_document_id=candidate_document.id,
            record_id=record_id,
        ):
            claim_id = verify_spec(
                entity_id=entity_id,
                entity_key=entity_key,
                record_id=record_id,
                spec=spec,
            )
            assert isinstance(spec.value, ScalarValue)
            source_claim_ids[(spec.predicate, str(spec.value.value))] = claim_id
        for spec in _derived_specs(record, source_claim_ids):
            verify_spec(
                entity_id=entity_id,
                entity_key=entity_key,
                record_id=record_id,
                spec=spec,
            )

    if complete_refresh and set(source_current) != expected_series_ids:
        raise ValueError(
            "Taiwan factory replay current same-filter claim set conflicts"
        )
    if not expected_series_ids.issubset(source_current):
        raise ValueError("Taiwan factory replay is missing current snapshot claims")

    expected_closure_ids: set[str] = set()
    closure_ids_by_prior: dict[str, str] = {}
    superseded_priors = connection.execute(
        """
        SELECT versions.*, series.subject_entity_id, series.predicate
        FROM claim_versions AS versions
        JOIN ingestion_runs AS producer ON producer.id = versions.created_by_run_id
        JOIN claim_series AS series ON series.id = versions.series_id
        WHERE producer.source_id = ?
          AND versions.created_by_run_id != ?
          AND versions.superseded_at = ?
        ORDER BY
          CASE versions.claim_kind WHEN 'derived_estimate' THEN 1 ELSE 0 END,
          versions.series_id,
          versions.id
        """,
        (run.source_id, run.id, run.started_at),
    ).fetchall()
    for prior in superseded_priors:
        exact_prior = _expected_origin_claim(
            connection,
            source_id=run.source_id,
            claim_version_id=str(prior["id"]),
            claim_cache=exact_claim_cache,
            origin_cache=origin_cache,
        )
        _verify_exact_claim(
            connection,
            exact_prior,
            context=f"Taiwan factory superseded prior claim {prior['id']}",
        )
        if str(prior["valid_from"]) > valid_from:
            raise ValueError(
                "Taiwan factory replay has a prior claim newer than its source update"
            )
        if str(prior["valid_from"]) == valid_from:
            continue
        closure_id = stable_id(
            "claim-version",
            IMPORTER_VERSION,
            "valid-time-closure",
            prior["id"],
            run.id,
            valid_from,
        )
        expected_closure_ids.add(closure_id)
        closure_ids_by_prior[str(prior["id"])] = closure_id
        if str(prior["series_id"]) in expected_series_ids:
            closure_basis = "later_same_filter_changed_value_or_lineage"
        elif str(prior["subject_entity_id"]) not in snapshot_entity_ids:
            closure_basis = "later_complete_same_filter_nonselection"
        elif prior["claim_kind"] == ClaimKind.DERIVED_ESTIMATE.value:
            closure_basis = "later_same_filter_seen_entity_derived_absence"
        else:
            closure_basis = "later_same_filter_seen_entity_field_absence"
        evidence = exact_prior.evidence
        if exact_prior.version.claim_kind is not ClaimKind.DERIVED_ESTIMATE:
            evidence = (
                *evidence,
                EvidenceLink(
                    candidate_document.id,
                    role=EvidenceRole.CONTEXT,
                    locator="source_record_scope;source_assertion_interval_closure",
                    excerpt=_canonical_json(
                        {
                            "closure_basis": closure_basis,
                            "prior_claim_version_id": prior["id"],
                            "valid_to": valid_from,
                        }
                    ),
                ),
            )
        dependencies = tuple(
            DependencyLink(
                closure_ids_by_prior.get(
                    dependency.depends_on_claim_version_id,
                    dependency.depends_on_claim_version_id,
                ),
                dependency.kind,
            )
            for dependency in exact_prior.dependencies
        )
        expected_closure = _ExactClaim(
            ClaimVersion(
                closure_id,
                exact_prior.version.series_id,
                exact_prior.version.valid_from,
                run.started_at,
                exact_prior.version.claim_kind,
                "taiwan_factory_registry_source_interval_closure_v1",
                exact_prior.version.confidence,
                valid_to=valid_from,
                created_by_run_id=run.id,
                notes=(
                    "A later verified same-filter registered-factory snapshot closes only "
                    f"the prior source-assertion interval at {valid_from}. "
                    f"{closure_basis}. It does not assert real-world closure, inactivity, "
                    "cancellation, production stop, ownership, output, or capacity."
                ),
            ),
            exact_prior.value,
            evidence,
            dependencies,
        )
        _verify_exact_claim(
            connection,
            expected_closure,
            context=f"Taiwan factory closure claim {closure_id}",
        )
    actual_run_claim_ids = {
        str(row["id"])
        for row in connection.execute(
            "SELECT id FROM claim_versions WHERE created_by_run_id = ?", (run.id,)
        )
    }
    expected_run_claim_ids = expected_current_run_claim_ids | expected_closure_ids
    if actual_run_claim_ids != expected_run_claim_ids:
        raise ValueError(
            "Taiwan factory replay run-created claim ledger conflicts "
            f"(missing={sorted(expected_run_claim_ids - actual_run_claim_ids)!r}, "
            f"unexpected={sorted(actual_run_claim_ids - expected_run_claim_ids)!r})"
        )


def _existing_run_result(
    connection: sqlite3.Connection,
    *,
    snapshot: VerifiedTaiwanFactorySnapshot,
    run: IngestionRun,
    candidate_document: SourceDocument,
    raw_document: SourceDocument,
    complete_refresh: bool,
    acceptance_timestamp_basis: str,
) -> TaiwanFactoryImportResult:
    row = connection.execute(
        "SELECT * FROM ingestion_runs WHERE id = ?", (run.id,)
    ).fetchone()
    if row is None:
        raise AssertionError("Taiwan factory replay requires an existing run")
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
    conflicts = [key for key, value in expected.items() if row[key] != value]
    if conflicts:
        raise ValueError(
            "Taiwan factory replay conflicts with immutable ingestion run fields: "
            + ", ".join(conflicts)
        )
    source_created_at = _source_created_at(connection, run.source_id)
    family = connection.execute(
        "SELECT * FROM source_families WHERE id = ?",
        (stable_id("source-family", SOURCE_FAMILY_KEY),),
    ).fetchone()
    expected_family = {
        "stable_key": SOURCE_FAMILY_KEY,
        "name": SOURCE_FAMILY_NAME,
        "description": SOURCE_FAMILY_DESCRIPTION,
        "created_at": source_created_at,
    }
    source = connection.execute(
        "SELECT * FROM sources WHERE id = ?", (run.source_id,)
    ).fetchone()
    expected_source = {
        "family_id": stable_id("source-family", SOURCE_FAMILY_KEY),
        "stable_key": SOURCE_KEY,
        "name": SOURCE_NAME,
        "publisher": SOURCE_PUBLISHER,
        "canonical_url": TAIWAN_FACTORY_DATASET_URL,
        "license": TAIWAN_FACTORY_LICENSE,
        "created_at": source_created_at,
    }
    if family is None or any(
        family[key] != value for key, value in expected_family.items()
    ):
        raise ValueError("Taiwan factory replay source family conflicts")
    if source is None or any(
        source[key] != value for key, value in expected_source.items()
    ):
        raise ValueError("Taiwan factory replay source conflicts")
    for document in (candidate_document, raw_document):
        existing = connection.execute(
            "SELECT * FROM source_documents WHERE id = ?", (document.id,)
        ).fetchone()
        expected_document = {
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
        if existing is None or any(
            existing[key] != value for key, value in expected_document.items()
        ):
            raise ValueError(
                f"Taiwan factory replay source document {document.id} conflicts"
            )
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
        raise ValueError("Taiwan factory replay document lineage conflicts")

    expected_records: dict[str, tuple[str, str, str, str, str, str]] = {}
    entity_ids: list[str] = []
    record_ids: list[str] = []
    for record in snapshot.scan.records:
        payload = _record_payload(record)
        entity_key = _entity_key(record.factory_registration_number)
        record_id = stable_id("source-record", run.id, entity_key)
        expected_records[record_id] = (
            run.id,
            candidate_document.id,
            entity_key,
            snapshot.retrieved_at,
            source_record_payload_sha256(payload),
            _canonical_json(payload),
        )
        record_ids.append(record_id)
        entity_id = stable_id("entity", entity_key)
        entity_ids.append(entity_id)
        _verify_entity_identity(
            connection,
            source_id=run.source_id,
            entity_id=entity_id,
            entity_key=entity_key,
        )
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
            FROM source_records WHERE ingestion_run_id = ?
            """,
            (run.id,),
        )
    }
    if actual_records != expected_records:
        raise ValueError("Taiwan factory replay source records conflict")
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
            "Taiwan factory replay failed claim-store validation: "
            + "; ".join(errors)
        )
    final_snapshot = verify_taiwan_factory_snapshot(snapshot.root)
    _verify_snapshot_identity(snapshot, final_snapshot)
    return TaiwanFactoryImportResult(
        source_family_id=stable_id("source-family", SOURCE_FAMILY_KEY),
        source_id=run.source_id,
        candidate_document_id=candidate_document.id,
        raw_document_id=raw_document.id,
        ingestion_run_id=run.id,
        accepted_at=run.started_at,
        source_updated_at=snapshot.source_updated_at,
        acceptance_timestamp_basis=acceptance_timestamp_basis,
        complete_refresh=complete_refresh,
        replayed_existing_run=True,
        facilities_imported=snapshot.candidate_count,
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


def import_taiwan_factory_candidates(
    connection: sqlite3.Connection,
    snapshot: VerifiedTaiwanFactorySnapshot,
    *,
    accepted_at: str,
    acceptance_timestamp_basis: str = "explicit_operator_supplied",
    complete_refresh: bool = True,
) -> TaiwanFactoryImportResult:
    """Import one immutable exact-261 registered-factory snapshot.

    A complete refresh closes prior same-filter assertion intervals only.  A
    partial refresh never closes omitted records or fields, and absence never
    becomes a claim about a real facility's lifecycle or activity.
    """

    if not isinstance(snapshot, VerifiedTaiwanFactorySnapshot):
        raise TypeError("snapshot must be a VerifiedTaiwanFactorySnapshot")
    if not isinstance(complete_refresh, bool):
        raise ValueError("complete_refresh must be a boolean")
    refreshed = verify_taiwan_factory_snapshot(snapshot.root)
    _verify_snapshot_identity(snapshot, refreshed)
    snapshot = refreshed
    accepted_at = _normalize_timestamp(accepted_at, "accepted_at")
    if acceptance_timestamp_basis not in ACCEPTANCE_TIMESTAMP_BASES:
        raise ValueError("Taiwan factory import has an invalid acceptance timestamp basis")
    if _clock(accepted_at) < _clock(snapshot.retrieved_at):
        raise ValueError(
            "Taiwan factory accepted_at must not predate snapshot retrieval"
        )
    if _clock(snapshot.source_updated_at) > _clock(snapshot.retrieved_at):
        raise ValueError(
            "Taiwan factory source_updated_at must not postdate retrieval"
        )
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
        snapshot.source_updated_at,
        str(complete_refresh).casefold(),
    )
    parameters = {
        "accepted_at": accepted_at,
        "acceptance_timestamp_basis": acceptance_timestamp_basis,
        "business_number_count": snapshot.business_number_count,
        "candidate_count": snapshot.candidate_count,
        "candidate_derivative": {
            "bytes": snapshot.candidate_size,
            "path": TAIWAN_FACTORY_CANDIDATE_FILENAME,
            "sha256": snapshot.candidate_sha256,
        },
        "complete_refresh": complete_refresh,
        "coverage": TAIWAN_FACTORY_COVERAGE,
        "filter_version": TAIWAN_FACTORY_FILTER_VERSION,
        "manifest_sha256": snapshot.manifest_sha256,
        "privacy": _manifest_scope(snapshot).get("privacy"),
        "raw_archive": {
            "bytes": snapshot.raw_size,
            "member": {
                "bytes": snapshot.member_size,
                "name": snapshot.member_name,
                "sha256": snapshot.member_sha256,
            },
            "path": snapshot.raw_path,
            "sha256": snapshot.raw_sha256,
        },
        "raw_matching_row_count": snapshot.candidate_row_count,
        "registration_status_counts": dict(snapshot.registration_status_counts),
        "retrieval_timestamp_basis": snapshot.retrieval_timestamp_basis,
        "rights": _manifest_scope(snapshot).get("rights"),
        "source_retrieved_at": snapshot.retrieved_at,
        "source_scope": TAIWAN_FACTORY_SCOPE,
        "source_updated_at": snapshot.source_updated_at,
        "source_updated_at_basis": snapshot.source_updated_at_basis,
        "upstream_row_count": snapshot.row_count,
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
    if connection.execute(
        "SELECT 1 FROM ingestion_runs WHERE id = ?", (run_id,)
    ).fetchone():
        with _atomic_import(connection):
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
            source_updated_at=snapshot.source_updated_at,
        )
        add_source_family(
            connection,
            SourceFamily(
                family_id,
                SOURCE_FAMILY_KEY,
                SOURCE_FAMILY_NAME,
                _existing_created_at(
                    connection, "source_families", family_id, accepted_at
                ),
                description=SOURCE_FAMILY_DESCRIPTION,
            ),
        )
        add_source(
            connection,
            Source(
                source_id,
                family_id,
                SOURCE_KEY,
                SOURCE_NAME,
                SOURCE_PUBLISHER,
                TAIWAN_FACTORY_DATASET_URL,
                _existing_created_at(connection, "sources", source_id, accepted_at),
                license=TAIWAN_FACTORY_LICENSE,
            ),
        )
        source_documents_created += int(
            _ensure_document(connection, candidate_document)
        )
        source_documents_created += int(_ensure_document(connection, raw_document))
        add_ingestion_run(connection, run)
        add_ingestion_run_document(
            connection,
            IngestionRunDocument(
                run_id, candidate_document.id, "candidate_derivative"
            ),
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
        exact_claim_cache: dict[str, _ExactClaim] = {}
        origin_cache: dict[tuple[str, str], _OriginContext] = {}
        verified_prior_ids: set[str] = set()

        def verify_prior(prior: sqlite3.Row) -> None:
            prior_id = str(prior["id"])
            if prior_id in verified_prior_ids:
                return
            expected = _expected_origin_claim(
                connection,
                source_id=source_id,
                claim_version_id=prior_id,
                claim_cache=exact_claim_cache,
                origin_cache=origin_cache,
            )
            _verify_exact_claim(
                connection,
                expected,
                context=f"Taiwan factory prior claim {prior_id}",
            )
            verified_prior_ids.add(prior_id)

        def close_prior(
            prior: sqlite3.Row, *, closure_basis: str
        ) -> None:
            nonlocal claims_created, prior_closed
            verify_prior(prior)
            closure_id, created = _close_prior_claim(
                connection,
                prior,
                run_id=run_id,
                candidate_document_id=candidate_document.id,
                accepted_at=accepted_at,
                valid_to=valid_from,
                closure_basis=closure_basis,
                closure_ids=closure_ids,
                prior_dependencies=prior_dependencies,
            )
            claims_created += int(created)
            prior_closed += 1
            closed_series.add(str(prior["series_id"]))

        def write_spec(
            *,
            entity_id: str,
            entity_key: str,
            record_id: str,
            spec: _ClaimSpec,
        ) -> str:
            nonlocal series_created, claims_created, unchanged_reused
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
                verify_prior(prior)
                unchanged_reused += 1
                return str(prior["id"])
            if prior is not None:
                close_prior(
                    prior,
                    closure_basis="later_same_filter_changed_value_or_lineage",
                )
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

        for record in snapshot.scan.records:
            entity_key = _entity_key(record.factory_registration_number)
            record_payload = _record_payload(record)
            record_id = stable_id("source-record", run_id, entity_key)
            source_record_ids.append(record_id)
            source_records_created += int(
                add_source_record(
                    connection,
                    SourceRecord(
                        record_id,
                        run_id,
                        candidate_document.id,
                        entity_key,
                        snapshot.retrieved_at,
                        source_record_payload_sha256(record_payload),
                        payload=record_payload,
                    ),
                )
            )
            entity_id = stable_id("entity", entity_key)
            facility_entity_ids.append(entity_id)
            current_entity_ids.add(entity_id)
            display_name = record.row.get("factory_name")
            if not isinstance(display_name, str) or not display_name:
                raise ValueError(
                    f"Taiwan factory {record.factory_registration_number} lacks a name"
                )
            entities_created += int(
                _ensure_entity(
                    connection,
                    source_id=source_id,
                    entity_id=entity_id,
                    stable_key=entity_key,
                    display_name=display_name,
                    created_at=accepted_at,
                    run_id=run_id,
                )
            )

            source_claim_ids: dict[tuple[str, str], str] = {}
            for spec in _source_specs(
                record,
                candidate_document_id=candidate_document.id,
                record_id=record_id,
            ):
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
                    close_prior(
                        prior,
                        closure_basis="later_same_filter_seen_entity_field_absence",
                    )

            for spec in _derived_specs(record, source_claim_ids):
                write_spec(
                    entity_id=entity_id,
                    entity_key=entity_key,
                    record_id=record_id,
                    spec=spec,
                )

            if complete_refresh:
                for prior_series_id, prior in sorted(
                    prior_by_entity.get(entity_id, {}).items()
                ):
                    if prior_series_id in seen_series or prior_series_id in closed_series:
                        continue
                    close_prior(
                        prior,
                        closure_basis="later_same_filter_seen_entity_derived_absence",
                    )

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
            for _series_id_value, prior in stale:
                close_prior(
                    prior,
                    closure_basis="later_complete_same_filter_nonselection",
                )

        errors = validate_database(connection)
        if errors:
            raise RuntimeError(
                "Taiwan factory import failed claim-store validation: "
                + "; ".join(errors)
            )
        final_snapshot = verify_taiwan_factory_snapshot(snapshot.root)
        _verify_snapshot_identity(snapshot, final_snapshot)

    return TaiwanFactoryImportResult(
        source_family_id=family_id,
        source_id=source_id,
        candidate_document_id=candidate_document.id,
        raw_document_id=raw_document.id,
        ingestion_run_id=run_id,
        accepted_at=accepted_at,
        source_updated_at=snapshot.source_updated_at,
        acceptance_timestamp_basis=acceptance_timestamp_basis,
        complete_refresh=complete_refresh,
        replayed_existing_run=False,
        facilities_imported=snapshot.candidate_count,
        source_documents_created=source_documents_created,
        source_records_created=source_records_created,
        entities_created=entities_created,
        claim_series_created=series_created,
        claims_created=claims_created,
        unchanged_claims_reused=unchanged_reused,
        prior_open_claims_closed_or_corrected=prior_closed,
        candidate_entities_no_longer_selected=len(
            prior_entity_ids - current_entity_ids
        ),
        facility_entity_ids=tuple(facility_entity_ids),
        source_record_ids=tuple(source_record_ids),
    )


__all__ = [
    "ACCEPTANCE_TIMESTAMP_BASES",
    "ENTITY_KEY_PREFIX",
    "IMPORTER_VERSION",
    "SOURCE_FAMILY_KEY",
    "SOURCE_KEY",
    "TaiwanFactoryImportResult",
    "import_taiwan_factory_candidates",
]
