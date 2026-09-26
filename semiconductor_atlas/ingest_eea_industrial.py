"""Import reviewed EEA v16 candidates as source-native facility records.

Only candidates with an ``accept_in_scope`` review decision enter the claim
store.  The review gates eligibility but does not become a source claim.  Every
emitted claim is an exact scalar statement from the retained EEA candidate
record; this importer emits no geometry, relationship, lifecycle, capacity,
output, process, or technology-subtype claim.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .adapters.eea_industrial import (
    EEA_ATTRIBUTION,
    EEA_DATASET_ID,
    EEA_DATASET_URL,
    EEA_DOI_URL,
    EEA_EDITION,
    EEA_FILTER_VERSION,
    EEA_LICENSE,
    EEA_RECORD_TYPE,
)
from .database import schema_version
from .eea_industrial_review import (
    EEAIndustrialReview,
    EEAIndustrialReviewDecision,
    EEAIndustrialReviewEvidence,
    EEAIndustrialReviewQueue,
    build_eea_industrial_review_queue,
    parse_eea_industrial_review_bytes,
    parse_eea_industrial_review_queue_bytes,
    read_eea_industrial_review_file,
    read_eea_industrial_review_queue_file,
)
from .eea_industrial_snapshot import (
    EEA_CANDIDATE_FILENAME,
    EEA_PUBLICATION_DATE,
    EEA_RAW_PATH,
    EEA_RAW_URL,
    VerifiedEEAIndustrialSnapshot,
    verify_eea_industrial_snapshot,
)
from .models import (
    ClaimKind,
    ClaimSeries,
    ClaimVersion,
    Entity,
    EntityKind,
    EvidenceLink,
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
    insert_claim,
    stable_id,
    validate_database,
)


LEGACY_IMPORTER_VERSION = "eea-industrial-reviewed-facility-import-v1"
IMPORTER_VERSION = "eea-industrial-reviewed-facility-import-v2"
SOURCE_FAMILY_KEY = "eea-industrial-reporting"
SOURCE_KEY = f"eea-industrial-reporting:{EEA_DATASET_ID}"
ENTITY_KEY_PREFIX = f"{SOURCE_KEY}:production-facility:"
SOURCE_FAMILY_NAME = "European Environment Agency Industrial Reporting"
SOURCE_NAME = "EEA Industrial Reporting production facilities"
SOURCE_PUBLISHER = "European Environment Agency"

_SAVEPOINTS = itertools.count()
_SOURCE_NOTES = (
    "Exact scalar source statement from EEA Industrial Reporting v16. The "
    "review controls atlas-scope eligibility only. This statement does not "
    "establish current operation, ownership, output, capacity, utilization, "
    "yield, process technology, product, ramp, or facility identity with any "
    "other source."
)
_FACILITY_FIELDS = (
    ("Facility_INSPIRE_ID", "eea_industrial.facility_inspire_id"),
    ("nameOfFeature", "name"),
    ("countryCode", "address.country_code"),
    ("streetName", "address.street"),
    ("buildingNumber", "address.building_number"),
    ("city", "address.city"),
    ("postalCode", "address.postal_code"),
)
_PROTECTED_FACILITY_FIELDS = frozenset(
    {"buildingNumber", "city", "nameOfFeature", "postalCode", "streetName"}
)
_ALLOWED_PREDICATES = frozenset(
    {
        *(predicate for _field, predicate in _FACILITY_FIELDS),
        "eea_industrial.nace_main_economic_activity_code",
        "eea_industrial.nace_main_economic_activity_name",
    }
)


@dataclass(frozen=True, slots=True)
class EEAIndustrialImportResult:
    source_family_id: str
    source_id: str
    raw_document_id: str
    candidate_document_id: str
    ingestion_run_id: str
    accepted_at: str
    replayed_existing_run: bool
    accepted_candidates: int
    deferred_candidates: int
    rejected_candidates: int
    source_documents_created: int
    source_records_created: int
    entities_created: int
    claim_series_created: int
    claims_created: int
    facility_entity_ids: tuple[str, ...]
    source_record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ClaimSpec:
    predicate: str
    dimension: str
    value: ScalarValue
    method: str
    evidence: tuple[EvidenceLink, ...]
    notes: str


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_timestamp(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{context} must be a canonical UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    canonical = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if canonical != value:
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    return value


def _clock(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@contextmanager
def _atomic_import(connection: sqlite3.Connection) -> Iterator[None]:
    savepoint = f"eea_industrial_import_{next(_SAVEPOINTS)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except BaseException:
        connection.execute(f"ROLLBACK TO {savepoint}")
        connection.execute(f"RELEASE {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE {savepoint}")


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


def _persist(connection: sqlite3.Connection, value: object, *, replayed: bool) -> bool:
    """Use repository writers only for admission; replay verifies immutable rows."""
    table, writer = {
        SourceFamily: ("source_families", add_source_family),
        Source: ("sources", add_source),
        SourceDocument: ("source_documents", add_source_document),
        IngestionRun: ("ingestion_runs", add_ingestion_run),
        IngestionRunDocument: ("ingestion_run_documents", add_ingestion_run_document),
        SourceRecord: ("source_records", add_source_record),
        Entity: ("entities", add_entity),
        ClaimSeries: ("claim_series", add_claim_series),
    }[type(value)]
    if not replayed:
        return writer(connection, value)
    expected = {}
    for field, item in asdict(value).items():
        if field in {"metadata", "parameters", "payload"}:
            expected[f"{field}_json"] = _canonical_json(item)
        else:
            expected[field] = item.value if isinstance(item, Enum) else item
    keys = ("ingestion_run_id", "source_document_id", "role") if isinstance(value, IngestionRunDocument) else ("id",)
    row = connection.execute(
        f"SELECT * FROM {table} WHERE " + " AND ".join(f"{key} = ?" for key in keys),
        tuple(expected[key] for key in keys),
    ).fetchone()
    if row is None or any(row[key] != item for key, item in expected.items()):
        raise ValueError(f"EEA replay conflicts with immutable {table} row")
    if isinstance(value, IngestionRun) and value.input_document_id is not None:
        _persist(connection, IngestionRunDocument(value.id, value.input_document_id, "primary"), replayed=True)
    if isinstance(value, SourceRecord):
        _persist(connection, IngestionRunDocument(value.ingestion_run_id, value.source_document_id, "source_record"), replayed=True)
    return False


def _verified_snapshot(
    value: str | Path | VerifiedEEAIndustrialSnapshot,
) -> VerifiedEEAIndustrialSnapshot:
    if isinstance(value, VerifiedEEAIndustrialSnapshot):
        refreshed = verify_eea_industrial_snapshot(value.root)
        if refreshed != value:
            raise ValueError(
                "EEA verified snapshot identity changed before database import"
            )
        return refreshed
    if isinstance(value, (str, Path)):
        return verify_eea_industrial_snapshot(value)
    raise TypeError("snapshot must be a verified snapshot or directory path")


def _load_queue(
    value: EEAIndustrialReviewQueue | bytes | str | Path,
) -> EEAIndustrialReviewQueue:
    if isinstance(value, EEAIndustrialReviewQueue):
        parsed = parse_eea_industrial_review_queue_bytes(value.raw_bytes)
        if parsed != replace(value, path=None):
            raise ValueError("EEA review queue object conflicts with its raw bytes")
        return value
    if isinstance(value, bytes):
        return parse_eea_industrial_review_queue_bytes(value)
    if isinstance(value, (str, Path)):
        return read_eea_industrial_review_queue_file(value)
    raise TypeError("queue must be a queue artifact, bytes, or file path")


def _load_review(
    value: EEAIndustrialReview | bytes | str | Path,
    *,
    queue: EEAIndustrialReviewQueue,
) -> EEAIndustrialReview:
    if isinstance(value, EEAIndustrialReview):
        parsed = parse_eea_industrial_review_bytes(value.raw_bytes, queue=queue)
        if parsed != replace(value, path=None):
            raise ValueError("EEA review object conflicts with its raw bytes")
        return value
    if isinstance(value, bytes):
        return parse_eea_industrial_review_bytes(value, queue=queue)
    if isinstance(value, (str, Path)):
        return read_eea_industrial_review_file(value, queue=queue)
    raise TypeError("review must be a review artifact, bytes, or file path")


def _assert_queue_matches_snapshot(
    snapshot: VerifiedEEAIndustrialSnapshot,
    queue: EEAIndustrialReviewQueue,
) -> None:
    actual_bindings = (
        queue.snapshot_manifest_sha256,
        queue.source_candidate_sha256,
        queue.source_candidate_count,
        queue.snapshot_accepted_at,
        queue.filter_version,
    )
    expected_bindings = (
        snapshot.manifest_sha256,
        snapshot.candidate_sha256,
        snapshot.candidate_count,
        snapshot.accepted_at,
        EEA_FILTER_VERSION,
    )
    if actual_bindings != expected_bindings:
        raise ValueError("EEA review queue snapshot bindings drifted")
    expected = build_eea_industrial_review_queue(
        snapshot,
        generated_at=queue.generated_at,
        knowledge_cutoff_at=queue.knowledge_cutoff_at,
    )
    if expected.raw_bytes != queue.raw_bytes or expected.raw_sha256 != queue.raw_sha256:
        raise ValueError("EEA review queue does not replay from the selected snapshot")


def _reverify_inputs(
    snapshot: VerifiedEEAIndustrialSnapshot,
    queue: EEAIndustrialReviewQueue,
    review: EEAIndustrialReview,
) -> None:
    refreshed_snapshot = verify_eea_industrial_snapshot(snapshot.root)
    if refreshed_snapshot != snapshot:
        raise ValueError("EEA snapshot changed during database import")
    refreshed_queue = (
        read_eea_industrial_review_queue_file(queue.path)
        if queue.path is not None
        else parse_eea_industrial_review_queue_bytes(queue.raw_bytes)
    )
    if refreshed_queue.raw_bytes != queue.raw_bytes:
        raise ValueError("EEA review queue changed during database import")
    refreshed_review = (
        read_eea_industrial_review_file(review.path, queue=refreshed_queue)
        if review.path is not None
        else parse_eea_industrial_review_bytes(review.raw_bytes, queue=refreshed_queue)
    )
    if refreshed_review.raw_bytes != review.raw_bytes:
        raise ValueError("EEA review changed during database import")


def _projection(value: object, context: str) -> tuple[dict[str, Any], frozenset[str]]:
    if not isinstance(value, dict) or not isinstance(value.get("values"), dict):
        raise ValueError(f"{context} lacks projected source values")
    values = value["values"]
    expected_digest = hashlib.sha256(
        _canonical_json(values).encode("utf-8")
    ).hexdigest()
    if value.get("projected_values_sha256") != expected_digest:
        raise ValueError(f"{context} projected-values hash drifted")
    raw_redacted = value.get("redacted_fields", [])
    if not isinstance(raw_redacted, list) or any(
        not isinstance(field, str) or not field for field in raw_redacted
    ):
        raise ValueError(f"{context} redacted-fields metadata drifted")
    if raw_redacted != sorted(set(raw_redacted)):
        raise ValueError(f"{context} redacted fields must be sorted and unique")
    return values, frozenset(raw_redacted)


def _projection_values(value: object, context: str) -> dict[str, Any]:
    values, _redacted = _projection(value, context)
    return values


def _source_text(values: Mapping[str, Any], field: str, context: str) -> str | None:
    if field not in values:
        raise ValueError(f"{context} lacks source field {field!r}")
    value = values[field]
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{context}.{field} must be source text or null")
    return value


def _facility_values(candidate: Mapping[str, Any]) -> dict[str, Any]:
    rows = candidate.get("facility_rows")
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError("EEA accepted candidate must contain one facility row")
    values, redacted = _projection(rows[0], "EEA accepted facility row")
    for field in _PROTECTED_FACILITY_FIELDS & redacted:
        if values.get(field) is not None:
            raise ValueError(f"EEA protected facility field {field!r} was not redacted")
    return values


def _field_evidence(
    *,
    document_id: str,
    record_id: str,
    locator: str,
    facility_id: str,
    field: str,
    value: str,
) -> tuple[EvidenceLink, ...]:
    return (
        EvidenceLink(
            document_id,
            source_record_id=record_id,
            locator=locator,
            excerpt=_canonical_json(
                {
                    "facility_inspire_id": facility_id,
                    "field": field,
                    "value": value,
                }
            ),
        ),
    )


def _text_spec(
    *,
    predicate: str,
    dimension: str,
    value: str | None,
    method: str,
    document_id: str,
    record_id: str,
    locator: str,
    facility_id: str,
    field: str,
    notes: str = _SOURCE_NOTES,
) -> _ClaimSpec | None:
    if value is None or not value.strip():
        return None
    return _ClaimSpec(
        predicate=predicate,
        dimension=dimension,
        value=ScalarValue(ScalarType.TEXT, value),
        method=method,
        evidence=_field_evidence(
            document_id=document_id,
            record_id=record_id,
            locator=locator,
            facility_id=facility_id,
            field=field,
            value=value,
        ),
        notes=notes,
    )


def _source_specs(
    candidate: Mapping[str, Any],
    *,
    document_id: str,
    record_id: str,
) -> tuple[str, tuple[_ClaimSpec, ...]]:
    facility_id = candidate.get("facility_inspire_id")
    if not isinstance(facility_id, str) or not facility_id:
        raise ValueError("EEA accepted candidate facility ID drifted")
    if candidate.get("record_type") != EEA_RECORD_TYPE:
        raise ValueError("EEA accepted candidate record type drifted")
    facility = _facility_values(candidate)
    if facility.get("Facility_INSPIRE_ID") != facility_id:
        raise ValueError("EEA accepted candidate facility identity drifted")
    specs: list[_ClaimSpec] = []

    for field, predicate in _FACILITY_FIELDS:
        spec = _text_spec(
            predicate=predicate,
            dimension="source",
            value=_source_text(facility, field, "EEA facility row"),
            method=f"eea_industrial_v16_{field}_exact_capture_v1",
            document_id=document_id,
            record_id=record_id,
            locator=f"facility_rows[0].values.{field}",
            facility_id=facility_id,
            field=field,
        )
        if spec is not None:
            specs.append(spec)

    function_rows = candidate.get("function_rows")
    if not isinstance(function_rows, list):
        raise ValueError("EEA accepted candidate function rows drifted")
    seen_function_ids: set[str] = set()
    for index, raw_function in enumerate(function_rows):
        function = _projection_values(raw_function, f"EEA function row {index}")
        function_id = _source_text(function, "FunctionId", "EEA function row")
        if (
            function_id is None
            or not function_id.isascii()
            or not function_id.isdigit()
            or function_id in seen_function_ids
        ):
            raise ValueError("EEA accepted candidate function ID drifted")
        seen_function_ids.add(function_id)
        for field, predicate in (
            (
                "NACEMainEconomicActivityCode",
                "eea_industrial.nace_main_economic_activity_code",
            ),
            (
                "NACEMainEconomicActivityName",
                "eea_industrial.nace_main_economic_activity_name",
            ),
        ):
            spec = _text_spec(
                predicate=predicate,
                dimension=f"function:{function_id}",
                value=_source_text(function, field, "EEA function row"),
                method=f"eea_industrial_v16_function_{field}_exact_capture_v1",
                document_id=document_id,
                record_id=record_id,
                locator=f"function_rows[{index}].values.{field}",
                facility_id=facility_id,
                field=field,
            )
            if spec is not None:
                specs.append(spec)

    keys = [(item.predicate, item.dimension) for item in specs]
    if len(keys) != len(set(keys)):
        raise ValueError("EEA accepted candidate produces duplicate claim series")
    return EEA_PUBLICATION_DATE, tuple(
        sorted(specs, key=lambda item: (item.predicate, item.dimension))
    )


def _ensure_series(
    connection: sqlite3.Connection,
    *,
    entity_id: str,
    entity_key: str,
    predicate: str,
    dimension: str,
    created_at: str,
    replayed: bool = False,
) -> tuple[str, bool]:
    series_id = stable_id(
        "claim-series",
        entity_id,
        predicate,
        ValueKind.SCALAR.value,
        dimension,
    )
    stable_key = f"{entity_key}:claim:{predicate}:{dimension}"
    existing_at = _existing_created_at(
        connection, "claim_series", series_id, created_at
    )
    created = _persist(
        connection,
        ClaimSeries(
            series_id,
            entity_id,
            stable_key,
            predicate,
            ValueKind.SCALAR,
            existing_at,
        ),
        replayed=replayed,
    )
    return series_id, created


def _evidence_payload(value: EEAIndustrialReviewEvidence) -> dict[str, str]:
    return {
        "accessed_at": value.accessed_at,
        "excerpt": value.excerpt,
        "title": value.title,
        "url": value.url,
    }


def _decision_payload(value: EEAIndustrialReviewDecision) -> dict[str, object]:
    return {
        "candidate_id": value.candidate_id,
        "evidence": [_evidence_payload(item) for item in value.evidence],
        "outcome": value.outcome,
        "reason": value.reason,
    }


def _run_parameters(
    *,
    snapshot: VerifiedEEAIndustrialSnapshot,
    queue: EEAIndustrialReviewQueue,
    review: EEAIndustrialReview,
    accepted_at: str,
    importer_version: str = IMPORTER_VERSION,
) -> dict[str, object]:
    outcome_counts = {
        outcome: sum(decision.outcome == outcome for decision in review.decisions)
        for outcome in (
            "accept_in_scope",
            "defer",
            "reject_out_of_scope",
        )
    }
    legacy = importer_version == LEGACY_IMPORTER_VERSION
    result = {
        "acceptance_timestamp_basis": (
            "explicit_operator_supplied" if legacy else "actual_post_validation_clock"
        ),
        "accepted_at": accepted_at,
        "candidate_queue": {
            "bytes": len(queue.raw_bytes),
            "format": queue.format,
            "generated_at": queue.generated_at,
            "knowledge_cutoff_at": queue.knowledge_cutoff_at,
            "sha256": queue.raw_sha256,
            "source_candidate_count": queue.source_candidate_count,
            "source_candidate_sha256": queue.source_candidate_sha256,
        },
        "decisions": [_decision_payload(item) for item in review.decisions],
        "filter_version": queue.filter_version,
        "import_policy": {
            "accepted_outcome": "accept_in_scope",
            "candidate_classification_claim_emitted": False,
            "claim_kind": "source_statement",
            "emitted_value_kinds": ["scalar"],
            "forbidden_value_kinds": [
                "capacity",
                "capability",
                "constraint",
                "geometry",
                "milestone",
                "relationship",
                "resource",
            ],
            "identity_assignments_created": False,
            "nonaccepted_candidates_create_entities_or_records": False,
            "operation_claims_emitted": False,
            "review_changes_require_a_new_importer_version": True,
            "source_native_only": True,
        },
        "outcome_counts": outcome_counts,
        "review_artifact": {
            "bytes": len(review.raw_bytes),
            "format": review.format,
            "candidate_queue_sha256": review.candidate_queue_sha256,
            "knowledge_cutoff_at": review.knowledge_cutoff_at,
            "reviewed_at": review.reviewed_at,
            "reviewed_by": review.reviewed_by,
            "sha256": review.raw_sha256,
            "snapshot_manifest_sha256": review.snapshot_manifest_sha256,
            "source_candidate_sha256": review.source_candidate_sha256,
            "accepted_candidate_ids": [
                item.candidate_id
                for item in review.decisions
                if item.outcome == "accept_in_scope"
            ],
        },
        "snapshot": {
            "accepted_at": snapshot.accepted_at,
            "candidate_count": snapshot.candidate_count,
            "candidate_sha256": snapshot.candidate_sha256,
            "extraction_metadata_sha256": snapshot.extraction_metadata_sha256,
            "manifest_sha256": snapshot.manifest_sha256,
            "raw_sha256": snapshot.raw_sha256,
            "retrieved_at": snapshot.retrieved_at,
        },
        "source_valid_from_basis": (
            "eea_dataset_publication_date" if legacy else "unknown_claim_effective_time"
        ),
        "workflow": importer_version,
    }
    if not legacy:
        result["import_policy"].update({
            "claim_effective_time_known": False,
            "claim_confidence_calibrated": False,
            "publication_is_claim_effective_time": False,
        })
        result["completion_timestamp_basis"] = "actual_prewrite_validation_completion"
    return result


def _source_candidate_map(
    snapshot: VerifiedEEAIndustrialSnapshot,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for candidate in snapshot.scan.candidates:
        if not isinstance(candidate, dict):
            raise ValueError("EEA verified snapshot contains a non-object candidate")
        facility_id = candidate.get("facility_inspire_id")
        if not isinstance(facility_id, str) or not facility_id:
            raise ValueError("EEA verified snapshot candidate facility ID drifted")
        if facility_id in result:
            raise ValueError("EEA verified snapshot has duplicate facility candidates")
        result[facility_id] = candidate
    if len(result) != snapshot.candidate_count:
        raise ValueError("EEA verified snapshot candidate map is incomplete")
    return result


def _assert_semantic_postconditions(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    raw_document_id: str,
    candidate_document_id: str,
    expected_entity_ids: Sequence[str],
    expected_record_ids: Sequence[str],
    expected_claim_count: int,
    importer_version: str = IMPORTER_VERSION,
) -> None:
    expected_documents = {
        (candidate_document_id, "primary"),
        (candidate_document_id, "candidate_derivative"),
        (raw_document_id, "raw_accdb"),
    }
    if expected_record_ids:
        expected_documents.add((candidate_document_id, "source_record"))
    actual_documents = {
        (row["source_document_id"], row["role"])
        for row in connection.execute(
            "SELECT source_document_id, role FROM ingestion_run_documents "
            "WHERE ingestion_run_id = ?", (run_id,),
        )
    }
    if actual_documents != expected_documents:
        raise ValueError("EEA import has unexpected ingestion-run document lineage")
    entity_rows = connection.execute(
        "SELECT id, kind, display_name FROM entities WHERE created_by_run_id = ?",
        (run_id,),
    ).fetchall()
    if tuple(sorted(str(row["id"]) for row in entity_rows)) != tuple(
        sorted(expected_entity_ids)
    ) or any(
        row["kind"] != EntityKind.FACILITY.value or row["display_name"] is not None
        for row in entity_rows
    ):
        raise ValueError("EEA import created an unexpected entity shape")
    record_rows = connection.execute(
        "SELECT id, source_document_id FROM source_records WHERE ingestion_run_id = ?",
        (run_id,),
    ).fetchall()
    if tuple(sorted(str(row["id"]) for row in record_rows)) != tuple(
        sorted(expected_record_ids)
    ) or any(row["source_document_id"] != candidate_document_id for row in record_rows):
        raise ValueError("EEA import created an unexpected source-record shape")

    claims = connection.execute(
        """
        SELECT versions.id, versions.claim_kind, versions.confidence,
               versions.valid_from, versions.valid_to,
               series.predicate, series.value_kind,
               entities.stable_key AS entity_stable_key,
               evidence.source_document_id, evidence.source_record_id,
               records.source_record_key,
               (SELECT COUNT(*) FROM claim_evidence AS all_evidence
                WHERE all_evidence.claim_version_id = versions.id) AS evidence_count,
               (SELECT COUNT(*) FROM scalar_values AS scalars
                WHERE scalars.claim_version_id = versions.id) AS scalar_count
        FROM claim_versions AS versions
        JOIN claim_series AS series ON series.id = versions.series_id
        JOIN entities ON entities.id = series.subject_entity_id
        LEFT JOIN claim_evidence AS evidence
          ON evidence.claim_version_id = versions.id
        LEFT JOIN source_records AS records ON records.id = evidence.source_record_id
        WHERE versions.created_by_run_id = ?
        ORDER BY versions.id
        """,
        (run_id,),
    ).fetchall()
    if len(claims) != expected_claim_count:
        raise ValueError("EEA import created an unexpected claim count")
    expected_records = set(expected_record_ids)
    legacy = importer_version == LEGACY_IMPORTER_VERSION
    for row in claims:
        if (
            row["claim_kind"] != ClaimKind.SOURCE_STATEMENT.value
            or row["confidence"] != (1.0 if legacy else None)
            or row["valid_from"] != (EEA_PUBLICATION_DATE if legacy else None)
            or row["valid_to"] is not None
            or row["value_kind"] != ValueKind.SCALAR.value
            or row["predicate"] not in _ALLOWED_PREDICATES
            or row["source_document_id"] != candidate_document_id
            or row["source_record_id"] not in expected_records
            or row["source_record_key"] != row["entity_stable_key"]
            or row["evidence_count"] != 1
            or row["scalar_count"] != 1
        ):
            raise ValueError("EEA import created a non-source-native claim")
    dependency_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM claim_dependencies AS dependencies
        JOIN claim_versions AS versions ON versions.id = dependencies.claim_version_id
        WHERE versions.created_by_run_id = ?
        """,
        (run_id,),
    ).fetchone()[0]
    if dependency_count != 0:
        raise ValueError("EEA import created derived claim dependencies")
    for table in (
        "geometry_values",
        "relationship_values",
        "milestone_values",
        "capability_values",
        "capacity_values",
        "resource_values",
        "constraint_values",
    ):
        count = connection.execute(
            f"""
            SELECT COUNT(*) FROM {table} AS typed
            JOIN claim_versions AS versions ON versions.id = typed.claim_version_id
            WHERE versions.created_by_run_id = ?
            """,
            (run_id,),
        ).fetchone()[0]
        if count != 0:
            raise ValueError(f"EEA import created forbidden {table} rows")


def accept_eea_industrial_review(
    connection: sqlite3.Connection,
    *,
    snapshot: str | Path | VerifiedEEAIndustrialSnapshot,
    candidate_queue: EEAIndustrialReviewQueue | bytes | str | Path,
    review: EEAIndustrialReview | bytes | str | Path,
    accepted_at: str | None = None,
) -> EEAIndustrialImportResult:
    """Atomically import only accepted EEA candidates as source-native facts.

    New admissions use schema-5 unknown-effective, uncalibrated source statements.
    An explicit acceptance timestamp is replay-only. Existing v1 imports retain
    their original representation and clocks; no new v1 import is permitted.
    A changed review remains forbidden across both importer versions.
    """

    started = _canonical_timestamp(_now(), "processing clock")
    verified = _verified_snapshot(snapshot)
    queue = _load_queue(candidate_queue)
    _assert_queue_matches_snapshot(verified, queue)
    reviewed = _load_review(review, queue=queue)
    requested_acceptance = (
        _canonical_timestamp(accepted_at, "accepted_at")
        if accepted_at is not None else None
    )
    cutoffs = (
        ("snapshot acceptance", verified.accepted_at),
        ("queue generation", queue.generated_at),
        ("review", reviewed.reviewed_at),
        ("review knowledge cutoff", reviewed.knowledge_cutoff_at),
    )

    sources = _source_candidate_map(verified)
    queue_by_id = {item.candidate_id: item for item in queue.candidates}
    accepted: list[tuple[EEAIndustrialReviewDecision, Mapping[str, Any]]] = []
    for decision in reviewed.decisions:
        if decision.outcome != "accept_in_scope":
            continue
        queued = queue_by_id[decision.candidate_id]
        source = sources[queued.facility_inspire_id]
        if source_record_payload_sha256(source) != queued.source_candidate_sha256:
            raise ValueError("EEA accepted source candidate hash drifted")
        accepted.append((decision, source))
    accepted.sort(key=lambda item: str(item[1]["facility_inspire_id"]))

    family_id = stable_id("source-family", SOURCE_FAMILY_KEY)
    source_id = stable_id("source", SOURCE_KEY)
    raw_document_id = stable_id(
        "source-document",
        source_id,
        EEA_RAW_URL,
        verified.retrieved_at,
        verified.raw_sha256,
        "raw-accdb",
    )
    candidate_document_id = stable_id(
        "source-document",
        source_id,
        EEA_DOI_URL,
        verified.accepted_at,
        verified.candidate_sha256,
        "candidate-derivative",
    )
    run_ids = {
        version: stable_id(
            "ingestion-run", version, verified.manifest_sha256,
            queue.raw_sha256, reviewed.raw_sha256,
        ) for version in (LEGACY_IMPORTER_VERSION, IMPORTER_VERSION)
    }
    prior_runs = connection.execute(
        "SELECT * FROM ingestion_runs WHERE source_id = ? AND code_version IN (?, ?)",
        (source_id, LEGACY_IMPORTER_VERSION, IMPORTER_VERSION),
    ).fetchall()
    if len(prior_runs) > 1:
        raise ValueError("EEA source has conflicting or mixed-version reviewed imports")
    existing_run = prior_runs[0] if prior_runs else None
    importer_version = (
        str(existing_run["code_version"]) if existing_run is not None else IMPORTER_VERSION
    )
    legacy = importer_version == LEGACY_IMPORTER_VERSION
    run_id = run_ids[importer_version]
    replayed = existing_run is not None
    if existing_run is not None:
        if existing_run["id"] != run_id:
            raise ValueError("EEA source already accepted a different review in this database")
        prior_parameters = json.loads(existing_run["parameters_json"])
        acceptance = _canonical_timestamp(prior_parameters.get("accepted_at"), "stored accepted_at")
        if requested_acceptance is not None and requested_acceptance != acceptance:
            raise ValueError("EEA exact replay accepted_at conflicts with the immutable original run")
        started = _canonical_timestamp(existing_run["started_at"], "stored started_at")
        completed = _canonical_timestamp(existing_run["completed_at"], "stored completed_at")
        if not _clock(started) <= _clock(acceptance) <= _clock(completed):
            raise ValueError("EEA stored admission clock is outside its processing interval")
    else:
        if requested_acceptance is not None:
            raise ValueError("accepted_at is replay-only; new EEA admissions require the actual clock")
        if schema_version(connection) < 5:
            raise ValueError("new EEA source statements require an existing schema-5 database")
        acceptance = completed = ""
    if replayed:
        for context, cutoff in cutoffs:
            if _clock(acceptance) < _clock(cutoff):
                raise ValueError(f"accepted_at cannot predate {context}")

    documents_created = 0
    records_created = 0
    entities_created = 0
    series_created = 0
    claims_created = 0
    expected_claims = 0
    entity_ids: list[str] = []
    record_ids: list[str] = []

    with _atomic_import(connection):
        def persist(value: object) -> bool:
            return _persist(connection, value, replayed=replayed)

        if not replayed:
            # Validate under the transaction before assigning the admission clock.
            # Ingestion runs are immutable, so completed_at records this real
            # validation-completion boundary, not a fabricated later timestamp.
            issues = validate_database(connection)
            if issues:
                raise ValueError("EEA pre-admission database validation: " + "; ".join(issues))
            _reverify_inputs(verified, queue, reviewed)
            acceptance = completed = _canonical_timestamp(_now(), "admission clock")
            if _clock(acceptance) <= _clock(started):
                raise ValueError("actual admission clock must follow validation start")
            for context, cutoff in cutoffs:
                if _clock(acceptance) < _clock(cutoff):
                    raise ValueError(f"accepted_at cannot predate {context}")
            if connection.execute(
                "SELECT 1 FROM ingestion_runs WHERE source_id = ? AND code_version IN (?, ?)",
                (source_id, LEGACY_IMPORTER_VERSION, IMPORTER_VERSION),
            ).fetchone() is not None:
                raise ValueError("EEA review admission changed during validation")
        family_created_at = _existing_created_at(
            connection, "source_families", family_id, acceptance
        )
        persist(
            SourceFamily(
                family_id,
                SOURCE_FAMILY_KEY,
                SOURCE_FAMILY_NAME,
                family_created_at,
                description=(
                    "Official European industrial-reporting records retained as "
                    "source-native facility evidence after explicit scope review."
                ),
            ),
        )
        source_created_at = _existing_created_at(
            connection, "sources", source_id, acceptance
        )
        persist(
            Source(
                source_id,
                family_id,
                SOURCE_KEY,
                SOURCE_NAME,
                SOURCE_PUBLISHER,
                EEA_DATASET_URL,
                source_created_at,
                license=EEA_LICENSE,
            ),
        )

        documents_created += persist(
            SourceDocument(
                raw_document_id,
                source_id,
                EEA_RAW_URL,
                "EEA Industrial Reporting v16 official Access database",
                verified.retrieved_at,
                verified.raw_sha256,
                published_at=EEA_PUBLICATION_DATE,
                media_type="application/msaccess",
                license=EEA_LICENSE,
                metadata={
                    "artifact_kind": "retained_official_relational_source",
                    "attribution": EEA_ATTRIBUTION,
                    "dataset_id": EEA_DATASET_ID,
                    "edition": EEA_EDITION,
                    "retention_path": EEA_RAW_PATH,
                    "snapshot_manifest_sha256": verified.manifest_sha256,
                },
            ),
        )
        documents_created += persist(
            SourceDocument(
                candidate_document_id,
                source_id,
                EEA_DOI_URL,
                "EEA Industrial Reporting v16 semiconductor candidate derivative",
                verified.accepted_at,
                verified.candidate_sha256,
                published_at=EEA_PUBLICATION_DATE,
                media_type="application/x-ndjson",
                license=EEA_LICENSE,
                metadata={
                    "artifact_kind": (
                        "deterministic_privacy_minimized_candidate_derivative"
                    ),
                    "attribution": EEA_ATTRIBUTION,
                    "dataset_id": EEA_DATASET_ID,
                    "edition": EEA_EDITION,
                    "filter_version": EEA_FILTER_VERSION,
                    "record_count": verified.candidate_count,
                    "retention_path": EEA_CANDIDATE_FILENAME,
                    "snapshot_manifest_sha256": verified.manifest_sha256,
                    "upstream_document_id": raw_document_id,
                },
            ),
        )
        parameters = _run_parameters(
            snapshot=verified,
            queue=queue,
            review=reviewed,
            accepted_at=acceptance,
            importer_version=importer_version,
        )
        persist(
            IngestionRun(
                run_id,
                source_id,
                started,
                status=IngestionStatus.SUCCEEDED,
                completed_at=completed,
                code_version=importer_version,
                input_document_id=candidate_document_id,
                parameters=parameters,
            ),
        )
        persist(
            IngestionRunDocument(run_id, raw_document_id, "raw_accdb"),
        )
        persist(
            IngestionRunDocument(run_id, candidate_document_id, "candidate_derivative"),
        )

        for _decision, candidate in accepted:
            facility_id = str(candidate["facility_inspire_id"])
            candidate_hash = source_record_payload_sha256(candidate)
            entity_key = f"{ENTITY_KEY_PREFIX}{facility_id}"
            entity_id = stable_id("entity", entity_key)
            record_key = entity_key
            record_id = stable_id("source-record", run_id, record_key)
            records_created += persist(
                SourceRecord(
                    record_id,
                    run_id,
                    candidate_document_id,
                    record_key,
                    verified.accepted_at,
                    candidate_hash,
                    payload=candidate,
                ),
            )
            entity_created_at = _existing_created_at(
                connection, "entities", entity_id, acceptance
            )
            entities_created += persist(
                Entity(
                    entity_id,
                    EntityKind.FACILITY,
                    entity_key,
                    entity_created_at,
                    display_name=None,
                    created_by_run_id=run_id,
                ),
            )
            valid_from, specs = _source_specs(
                candidate,
                document_id=candidate_document_id,
                record_id=record_id,
            )
            expected_claims += len(specs)
            for spec in specs:
                series_id, created = _ensure_series(
                    connection,
                    entity_id=entity_id,
                    entity_key=entity_key,
                    predicate=spec.predicate,
                    dimension=spec.dimension,
                    created_at=acceptance,
                    replayed=replayed,
                )
                series_created += created
                claim_id = stable_id(
                    "claim-version",
                    importer_version,
                    series_id,
                    record_id,
                    EEA_PUBLICATION_DATE,
                )
                if replayed and connection.execute(
                    "SELECT 1 FROM claim_versions WHERE id = ?", (claim_id,)
                ).fetchone() is None:
                    raise ValueError("EEA replay is missing an immutable claim")
                claims_created += insert_claim(
                    connection,
                    ClaimVersion(
                        claim_id,
                        series_id,
                        valid_from if legacy else None,
                        acceptance,
                        ClaimKind.SOURCE_STATEMENT,
                        spec.method,
                        1.0 if legacy else None,
                        created_by_run_id=run_id,
                        notes=(spec.notes if legacy else spec.notes + " Claim-effective time and calibrated claim confidence are unknown; publication is document metadata only."),
                    ),
                    spec.value,
                    evidence=spec.evidence,
                )
            entity_ids.append(entity_id)
            record_ids.append(record_id)

        _assert_semantic_postconditions(
            connection,
            run_id=run_id,
            raw_document_id=raw_document_id,
            candidate_document_id=candidate_document_id,
            expected_entity_ids=entity_ids,
            expected_record_ids=record_ids,
            expected_claim_count=expected_claims,
            importer_version=importer_version,
        )
        issues = validate_database(connection)
        if issues:
            raise ValueError(
                "EEA import left invalid database state: " + "; ".join(issues)
            )
        _reverify_inputs(verified, queue, reviewed)
        if not replayed and _clock(_canonical_timestamp(_now(), "final verification clock")) < _clock(acceptance):
            raise ValueError("EEA clock moved backwards during final verification")

    if replayed and any(
        (
            documents_created,
            records_created,
            entities_created,
            series_created,
            claims_created,
        )
    ):
        raise AssertionError("EEA exact replay unexpectedly created rows")
    counts = {
        outcome: sum(decision.outcome == outcome for decision in reviewed.decisions)
        for outcome in (
            "accept_in_scope",
            "defer",
            "reject_out_of_scope",
        )
    }
    return EEAIndustrialImportResult(
        source_family_id=family_id,
        source_id=source_id,
        raw_document_id=raw_document_id,
        candidate_document_id=candidate_document_id,
        ingestion_run_id=run_id,
        accepted_at=acceptance,
        replayed_existing_run=replayed,
        accepted_candidates=counts["accept_in_scope"],
        deferred_candidates=counts["defer"],
        rejected_candidates=counts["reject_out_of_scope"],
        source_documents_created=documents_created,
        source_records_created=records_created,
        entities_created=entities_created,
        claim_series_created=series_created,
        claims_created=claims_created,
        facility_entity_ids=tuple(entity_ids),
        source_record_ids=tuple(record_ids),
    )


__all__ = [
    "EEAIndustrialImportResult",
    "ENTITY_KEY_PREFIX",
    "IMPORTER_VERSION",
    "SOURCE_FAMILY_KEY",
    "SOURCE_KEY",
    "accept_eea_industrial_review",
]
