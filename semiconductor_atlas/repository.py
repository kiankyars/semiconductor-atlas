"""Persistence helpers for immutable evidence and bitemporal claims."""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator, Mapping, Sequence

from .database import knowledge_clock_sql

from .models import (
    CapabilityValue,
    CapacityBasis,
    CapacityValue,
    ClaimSeries,
    ClaimValue,
    ClaimVersion,
    ConstraintSeverity,
    ConstraintStatus,
    ConstraintValue,
    DependencyLink,
    EntityResolutionCandidate,
    EntityResolutionDecision,
    EntityResolutionRun,
    EntityResolutionRunInput,
    EntityResolutionStatus,
    Entity,
    EvidenceLink,
    GeometryValue,
    IngestionRun,
    IngestionRunDocument,
    IngestionStatus,
    MilestoneStatus,
    MilestoneValue,
    RelationshipValue,
    ResourceValue,
    OrganizationIdentifierClaimMetadata,
    OrganizationNameClaimMetadata,
    ScalarType,
    ScalarValue,
    Source,
    SourceDocument,
    SourceFamily,
    SourceRecord,
    SourceEntityAssignment,
    source_record_payload_sha256,
)


ATLAS_NAMESPACE = uuid.UUID("77e9b581-e9f9-4662-94bc-580cf478d260")
_SAVEPOINTS = itertools.count()


def stable_id(*parts: object) -> str:
    if not parts:
        raise ValueError("stable_id requires at least one part")
    encoded_parts = json.dumps(
        [str(part) for part in parts],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return str(uuid.uuid5(ATLAS_NAMESPACE, encoded_parts))


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _insert_or_verify(
    connection: sqlite3.Connection,
    *,
    table: str,
    identifier: str,
    insert_sql: str,
    params: Sequence[object],
    expected: Mapping[str, object],
) -> bool:
    cursor = connection.execute(insert_sql, params)
    if cursor.rowcount == 1:
        return True
    row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (identifier,)).fetchone()
    if row is None:
        raise ValueError(f"{table} stable key already belongs to a different id")
    conflicts = [column for column, value in expected.items() if row[column] != value]
    if conflicts:
        raise ValueError(
            f"{table}.{identifier} conflicts on immutable fields: {', '.join(conflicts)}"
        )
    return False


def _insert_or_verify_unique(
    connection: sqlite3.Connection,
    *,
    table: str,
    lookup_sql: str,
    lookup_params: Sequence[object],
    insert_sql: str,
    params: Sequence[object],
    expected: Mapping[str, object],
) -> bool:
    cursor = connection.execute(insert_sql, params)
    if cursor.rowcount == 1:
        return True
    row = connection.execute(lookup_sql, lookup_params).fetchone()
    if row is None:
        raise ValueError(f"{table} stable key already belongs to a different row")
    conflicts = [column for column, value in expected.items() if row[column] != value]
    if conflicts:
        raise ValueError(
            f"{table} conflicts on immutable fields: {', '.join(conflicts)}"
        )
    return False


def _normalized_timestamp(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def add_source_family(connection: sqlite3.Connection, family: SourceFamily) -> bool:
    return _insert_or_verify(
        connection,
        table="source_families",
        identifier=family.id,
        insert_sql="""
            INSERT OR IGNORE INTO source_families(
                id, stable_key, name, description, created_at
            ) VALUES (?, ?, ?, ?, ?)
        """,
        params=(family.id, family.stable_key, family.name, family.description, family.created_at),
        expected={
            "stable_key": family.stable_key,
            "name": family.name,
            "description": family.description,
            "created_at": family.created_at,
        },
    )


def add_source(connection: sqlite3.Connection, source: Source) -> bool:
    return _insert_or_verify(
        connection,
        table="sources",
        identifier=source.id,
        insert_sql="""
            INSERT OR IGNORE INTO sources(
                id, family_id, stable_key, name, publisher, canonical_url, license, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params=(
            source.id,
            source.family_id,
            source.stable_key,
            source.name,
            source.publisher,
            source.canonical_url,
            source.license,
            source.created_at,
        ),
        expected={
            "family_id": source.family_id,
            "stable_key": source.stable_key,
            "name": source.name,
            "publisher": source.publisher,
            "canonical_url": source.canonical_url,
            "license": source.license,
            "created_at": source.created_at,
        },
    )


def add_source_document(
    connection: sqlite3.Connection, document: SourceDocument
) -> bool:
    metadata_json = _json(document.metadata)
    return _insert_or_verify(
        connection,
        table="source_documents",
        identifier=document.id,
        insert_sql="""
            INSERT OR IGNORE INTO source_documents(
                id, source_id, document_url, title, published_at, retrieved_at,
                content_sha256, media_type, license, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params=(
            document.id,
            document.source_id,
            document.document_url,
            document.title,
            document.published_at,
            document.retrieved_at,
            document.content_sha256,
            document.media_type,
            document.license,
            metadata_json,
        ),
        expected={
            "source_id": document.source_id,
            "document_url": document.document_url,
            "title": document.title,
            "published_at": document.published_at,
            "retrieved_at": document.retrieved_at,
            "content_sha256": document.content_sha256,
            "media_type": document.media_type,
            "license": document.license,
            "metadata_json": metadata_json,
        },
    )


def add_evidence(connection: sqlite3.Connection, document: SourceDocument) -> bool:
    return add_source_document(connection, document)


def add_ingestion_run(connection: sqlite3.Connection, run: IngestionRun) -> bool:
    parameters_json = _json(run.parameters)
    with _atomic(connection):
        created = _insert_or_verify(
            connection,
            table="ingestion_runs",
            identifier=run.id,
            insert_sql="""
                INSERT OR IGNORE INTO ingestion_runs(
                    id, source_id, input_document_id, started_at, completed_at, status,
                    code_version, parameters_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params=(
                run.id,
                run.source_id,
                run.input_document_id,
                run.started_at,
                run.completed_at,
                run.status.value,
                run.code_version,
                parameters_json,
                run.error,
            ),
            expected={
                "source_id": run.source_id,
                "input_document_id": run.input_document_id,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "status": run.status.value,
                "code_version": run.code_version,
                "parameters_json": parameters_json,
                "error": run.error,
            },
        )
        if run.input_document_id is not None:
            add_ingestion_run_document(
                connection,
                IngestionRunDocument(
                    run.id,
                    run.input_document_id,
                    "primary",
                ),
            )
    return created


def add_ingestion_run_document(
    connection: sqlite3.Connection,
    document: IngestionRunDocument,
) -> bool:
    return _insert_or_verify_unique(
        connection,
        table="ingestion_run_documents",
        lookup_sql="""
            SELECT *
            FROM ingestion_run_documents
            WHERE ingestion_run_id = ? AND source_document_id = ? AND role = ?
        """,
        lookup_params=(
            document.ingestion_run_id,
            document.source_document_id,
            document.role,
        ),
        insert_sql="""
            INSERT OR IGNORE INTO ingestion_run_documents(
                ingestion_run_id, source_document_id, role
            ) VALUES (?, ?, ?)
        """,
        params=(
            document.ingestion_run_id,
            document.source_document_id,
            document.role,
        ),
        expected={
            "ingestion_run_id": document.ingestion_run_id,
            "source_document_id": document.source_document_id,
            "role": document.role,
        },
    )


def add_source_record(connection: sqlite3.Connection, record: SourceRecord) -> bool:
    payload_json = _json(record.payload)
    with _atomic(connection):
        created = _insert_or_verify(
            connection,
            table="source_records",
            identifier=record.id,
            insert_sql="""
                INSERT OR IGNORE INTO source_records(
                    id, ingestion_run_id, source_document_id, source_record_key,
                    observed_at, record_sha256, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            params=(
                record.id,
                record.ingestion_run_id,
                record.source_document_id,
                record.source_record_key,
                record.observed_at,
                record.record_sha256,
                payload_json,
            ),
            expected={
                "ingestion_run_id": record.ingestion_run_id,
                "source_document_id": record.source_document_id,
                "source_record_key": record.source_record_key,
                "observed_at": record.observed_at,
                "record_sha256": record.record_sha256,
                "payload_json": payload_json,
            },
        )
        add_ingestion_run_document(
            connection,
            IngestionRunDocument(
                record.ingestion_run_id,
                record.source_document_id,
                "source_record",
            ),
        )
    return created


def add_entity(connection: sqlite3.Connection, entity: Entity) -> bool:
    return _insert_or_verify(
        connection,
        table="entities",
        identifier=entity.id,
        insert_sql="""
            INSERT OR IGNORE INTO entities(
                id, kind, stable_key, display_name, created_at, created_by_run_id
            ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        params=(
            entity.id,
            entity.kind.value,
            entity.stable_key,
            entity.display_name,
            entity.created_at,
            entity.created_by_run_id,
        ),
        expected={
            "kind": entity.kind.value,
            "stable_key": entity.stable_key,
            "display_name": entity.display_name,
            "created_at": entity.created_at,
            "created_by_run_id": entity.created_by_run_id,
        },
    )


def add_organization_name_claim_metadata(
    connection: sqlite3.Connection,
    metadata: OrganizationNameClaimMetadata,
) -> bool:
    return _insert_or_verify_unique(
        connection,
        table="organization_name_claim_metadata",
        lookup_sql="""
            SELECT * FROM organization_name_claim_metadata
            WHERE claim_version_id = ?
        """,
        lookup_params=(metadata.claim_version_id,),
        insert_sql="""
            INSERT OR IGNORE INTO organization_name_claim_metadata(
                claim_version_id, name_type, language_tag, script_code
            ) VALUES (?, ?, ?, ?)
        """,
        params=(
            metadata.claim_version_id,
            metadata.name_type.value,
            metadata.language_tag,
            metadata.script_code,
        ),
        expected={
            "claim_version_id": metadata.claim_version_id,
            "name_type": metadata.name_type.value,
            "language_tag": metadata.language_tag,
            "script_code": metadata.script_code,
        },
    )


def add_organization_identifier_claim_metadata(
    connection: sqlite3.Connection,
    metadata: OrganizationIdentifierClaimMetadata,
) -> bool:
    return _insert_or_verify_unique(
        connection,
        table="organization_identifier_claim_metadata",
        lookup_sql="""
            SELECT * FROM organization_identifier_claim_metadata
            WHERE claim_version_id = ?
        """,
        lookup_params=(metadata.claim_version_id,),
        insert_sql="""
            INSERT OR IGNORE INTO organization_identifier_claim_metadata(
                claim_version_id, scheme, normalized_value, jurisdiction
            ) VALUES (?, ?, ?, ?)
        """,
        params=(
            metadata.claim_version_id,
            metadata.scheme,
            metadata.normalized_value,
            metadata.jurisdiction,
        ),
        expected={
            "claim_version_id": metadata.claim_version_id,
            "scheme": metadata.scheme,
            "normalized_value": metadata.normalized_value,
            "jurisdiction": metadata.jurisdiction,
        },
    )


def add_entity_resolution_run(
    connection: sqlite3.Connection,
    run: EntityResolutionRun,
) -> bool:
    if run.status is not EntityResolutionStatus.RUNNING:
        raise ValueError("an entity resolution run must be created in running state")
    parameters_json = _json(run.parameters)
    return _insert_or_verify(
        connection,
        table="entity_resolution_runs",
        identifier=run.id,
        insert_sql="""
            INSERT OR IGNORE INTO entity_resolution_runs(
                id, started_at, completed_at, status, resolver_version,
                code_version, parameters_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params=(
            run.id,
            run.started_at,
            run.completed_at,
            run.status.value,
            run.resolver_version,
            run.code_version,
            parameters_json,
            run.error,
        ),
        expected={
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "status": run.status.value,
            "resolver_version": run.resolver_version,
            "code_version": run.code_version,
            "parameters_json": parameters_json,
            "error": run.error,
        },
    )


def finalize_entity_resolution_run(
    connection: sqlite3.Connection,
    resolution_run_id: str,
    *,
    status: EntityResolutionStatus,
    completed_at: str,
    error: str | None = None,
) -> bool:
    if not isinstance(resolution_run_id, str) or not resolution_run_id.strip():
        raise ValueError("resolution_run_id is required")
    if status is EntityResolutionStatus.RUNNING:
        raise ValueError("a finalized entity resolution run must be succeeded or failed")
    timestamp = _normalized_timestamp(completed_at, "completed_at")
    if status is EntityResolutionStatus.SUCCEEDED and error is not None:
        raise ValueError("a succeeded entity resolution run cannot have an error")
    if status is EntityResolutionStatus.FAILED and (
        not isinstance(error, str) or not error.strip()
    ):
        raise ValueError("a failed entity resolution run requires an error")
    row = connection.execute(
        "SELECT started_at, completed_at, status, error FROM entity_resolution_runs WHERE id = ?",
        (resolution_run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"entity resolution run does not exist: {resolution_run_id}")
    if row["status"] != EntityResolutionStatus.RUNNING.value:
        if (
            row["status"] == status.value
            and row["completed_at"] == timestamp
            and row["error"] == error
        ):
            return False
        raise ValueError(f"entity resolution run is already finalized: {resolution_run_id}")
    started = datetime.fromisoformat(str(row["started_at"]).replace("Z", "+00:00"))
    completed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if completed <= started:
        raise ValueError("completed_at must be later than started_at")
    connection.execute(
        """
        UPDATE entity_resolution_runs
        SET completed_at = ?, status = ?, error = ?
        WHERE id = ?
        """,
        (timestamp, status.value, error, resolution_run_id),
    )
    return True


def add_entity_resolution_run_input(
    connection: sqlite3.Connection,
    run_input: EntityResolutionRunInput,
) -> bool:
    return _insert_or_verify_unique(
        connection,
        table="entity_resolution_run_inputs",
        lookup_sql="""
            SELECT * FROM entity_resolution_run_inputs
            WHERE resolution_run_id = ? AND ingestion_run_id = ?
        """,
        lookup_params=(run_input.resolution_run_id, run_input.ingestion_run_id),
        insert_sql="""
            INSERT OR IGNORE INTO entity_resolution_run_inputs(
                resolution_run_id, ingestion_run_id
            ) VALUES (?, ?)
        """,
        params=(run_input.resolution_run_id, run_input.ingestion_run_id),
        expected={
            "resolution_run_id": run_input.resolution_run_id,
            "ingestion_run_id": run_input.ingestion_run_id,
        },
    )


def _candidate_target_evidence_ids(
    features: Mapping[str, Any],
) -> tuple[str, ...] | None:
    declared = features.get("target_evidence_claim_version_ids")
    if declared is None:
        return None
    if not isinstance(declared, (list, tuple)) or not declared or any(
        not isinstance(claim_id, str) or not claim_id.strip()
        for claim_id in declared
    ):
        raise ValueError(
            "target_evidence_claim_version_ids must be a non-empty string array"
        )
    result = tuple(declared)
    if result != tuple(sorted(set(result))):
        raise ValueError(
            "target_evidence_claim_version_ids must be sorted and unique"
        )
    return result


def _validate_candidate_target_evidence(
    connection: sqlite3.Connection,
    *,
    resolution_run_id: str,
    candidate_entity_id: str,
    claim_ids: Sequence[str],
) -> None:
    resolution = connection.execute(
        "SELECT started_at FROM entity_resolution_runs WHERE id = ?",
        (resolution_run_id,),
    ).fetchone()
    if resolution is None:
        raise ValueError(f"unknown entity resolution run: {resolution_run_id}")
    for claim_id in claim_ids:
        row = connection.execute(
            """
            SELECT series.subject_entity_id, versions.recorded_at,
                   versions.created_by_run_id, runs.status, runs.completed_at,
                   EXISTS (
                       SELECT 1 FROM claim_evidence
                       WHERE claim_version_id = versions.id
                   ) AS source_backed,
                   EXISTS (
                       SELECT 1 FROM entity_resolution_run_inputs AS inputs
                       WHERE inputs.resolution_run_id = ?
                         AND inputs.ingestion_run_id = versions.created_by_run_id
                   ) AS declared_input
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            LEFT JOIN ingestion_runs AS runs ON runs.id = versions.created_by_run_id
            WHERE versions.id = ?
            """,
            (resolution_run_id, claim_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown target evidence claim: {claim_id}")
        if (
            row["subject_entity_id"] != candidate_entity_id
            or row["source_backed"] != 1
            or row["created_by_run_id"] is None
            or row["status"] != IngestionStatus.SUCCEEDED.value
            or row["completed_at"] is None
            or row["declared_input"] != 1
            or connection.execute(
                "SELECT julianday(?) <= julianday(?)",
                (row["recorded_at"], resolution["started_at"]),
            ).fetchone()[0]
            != 1
            or connection.execute(
                "SELECT julianday(?) <= julianday(?)",
                (row["completed_at"], resolution["started_at"]),
            ).fetchone()[0]
            != 1
        ):
            raise ValueError(
                f"target evidence claim {claim_id} lacks cutoff-safe candidate lineage"
            )


def add_entity_resolution_candidate(
    connection: sqlite3.Connection,
    candidate: EntityResolutionCandidate,
) -> bool:
    target_evidence_ids = _candidate_target_evidence_ids(candidate.features)
    if target_evidence_ids is not None:
        _validate_candidate_target_evidence(
            connection,
            resolution_run_id=candidate.resolution_run_id,
            candidate_entity_id=candidate.candidate_entity_id,
            claim_ids=target_evidence_ids,
        )
    features_json = _json(candidate.features)
    return _insert_or_verify(
        connection,
        table="entity_resolution_candidates",
        identifier=candidate.id,
        insert_sql="""
            INSERT OR IGNORE INTO entity_resolution_candidates(
                id, resolution_run_id, source_record_id, observed_entity_id,
                candidate_entity_id, features_json, score, candidate_rank,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params=(
            candidate.id,
            candidate.resolution_run_id,
            candidate.source_record_id,
            candidate.observed_entity_id,
            candidate.candidate_entity_id,
            features_json,
            float(candidate.score),
            candidate.candidate_rank,
            candidate.created_at,
        ),
        expected={
            "resolution_run_id": candidate.resolution_run_id,
            "source_record_id": candidate.source_record_id,
            "observed_entity_id": candidate.observed_entity_id,
            "candidate_entity_id": candidate.candidate_entity_id,
            "features_json": features_json,
            "score": float(candidate.score),
            "candidate_rank": candidate.candidate_rank,
            "created_at": candidate.created_at,
        },
    )


def add_entity_resolution_decision(
    connection: sqlite3.Connection,
    decision: EntityResolutionDecision,
) -> bool:
    metadata_json = _json(decision.metadata)
    return _insert_or_verify(
        connection,
        table="entity_resolution_decisions",
        identifier=decision.id,
        insert_sql="""
            INSERT OR IGNORE INTO entity_resolution_decisions(
                id, candidate_id, outcome, decided_at, decided_by, reason,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        params=(
            decision.id,
            decision.candidate_id,
            decision.outcome.value,
            decision.decided_at,
            decision.decided_by,
            decision.reason,
            metadata_json,
        ),
        expected={
            "candidate_id": decision.candidate_id,
            "outcome": decision.outcome.value,
            "decided_at": decision.decided_at,
            "decided_by": decision.decided_by,
            "reason": decision.reason,
            "metadata_json": metadata_json,
        },
    )


def add_source_entity_assignment(
    connection: sqlite3.Connection,
    assignment: SourceEntityAssignment,
) -> bool:
    return _insert_or_verify(
        connection,
        table="source_entity_assignments",
        identifier=assignment.id,
        insert_sql="""
            INSERT OR IGNORE INTO source_entity_assignments(
                id, source_record_id, observed_entity_id, canonical_entity_id,
                decision_id, valid_from, valid_to, recorded_at, superseded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params=(
            assignment.id,
            assignment.source_record_id,
            assignment.observed_entity_id,
            assignment.canonical_entity_id,
            assignment.decision_id,
            assignment.valid_from,
            assignment.valid_to,
            assignment.recorded_at,
            assignment.superseded_at,
        ),
        expected={
            "source_record_id": assignment.source_record_id,
            "observed_entity_id": assignment.observed_entity_id,
            "canonical_entity_id": assignment.canonical_entity_id,
            "decision_id": assignment.decision_id,
            "valid_from": assignment.valid_from,
            "valid_to": assignment.valid_to,
            "recorded_at": assignment.recorded_at,
            "superseded_at": assignment.superseded_at,
        },
    )


def supersede_source_entity_assignment(
    connection: sqlite3.Connection,
    assignment_id: str,
    superseded_at: str,
) -> bool:
    if not isinstance(assignment_id, str) or not assignment_id.strip():
        raise ValueError("assignment_id is required")
    timestamp = _normalized_timestamp(superseded_at, "superseded_at")
    row = connection.execute(
        "SELECT recorded_at, superseded_at FROM source_entity_assignments WHERE id = ?",
        (assignment_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"source entity assignment does not exist: {assignment_id}")
    if row["superseded_at"] is not None:
        if row["superseded_at"] == timestamp:
            return False
        raise ValueError(f"source entity assignment is already superseded: {assignment_id}")
    if datetime.fromisoformat(timestamp.replace("Z", "+00:00")) <= datetime.fromisoformat(
        str(row["recorded_at"]).replace("Z", "+00:00")
    ):
        raise ValueError("superseded_at must be later than recorded_at")
    connection.execute(
        "UPDATE source_entity_assignments SET superseded_at = ? WHERE id = ?",
        (timestamp, assignment_id),
    )
    return True


def current_source_entity_assignments(
    connection: sqlite3.Connection,
    *,
    as_of: str,
    recorded_at: str,
    source_record_id: str | None = None,
    observed_entity_id: str | None = None,
) -> list[sqlite3.Row]:
    try:
        parsed_as_of = datetime.fromisoformat(as_of)
    except ValueError as error:
        raise ValueError("as_of must use YYYY-MM-DD") from error
    if parsed_as_of.date().isoformat() != as_of or "T" in as_of:
        raise ValueError("as_of must use YYYY-MM-DD")
    cutoff = _normalized_timestamp(recorded_at, "recorded_at")
    filters = [
        "valid_from <= ?",
        "(valid_to IS NULL OR valid_to > ?)",
        "julianday(recorded_at) <= julianday(?)",
        "(superseded_at IS NULL OR julianday(superseded_at) > julianday(?))",
    ]
    params: list[object] = [as_of, as_of, cutoff, cutoff]
    if source_record_id is not None:
        filters.append("source_record_id = ?")
        params.append(source_record_id)
    if observed_entity_id is not None:
        filters.append("observed_entity_id = ?")
        params.append(observed_entity_id)
    return connection.execute(
        """
        SELECT *
        FROM source_entity_assignments
        WHERE """
        + " AND ".join(filters)
        + " ORDER BY source_record_id, observed_entity_id, valid_from, recorded_at, id",
        params,
    ).fetchall()


def add_claim_series(connection: sqlite3.Connection, series: ClaimSeries) -> bool:
    return _insert_or_verify(
        connection,
        table="claim_series",
        identifier=series.id,
        insert_sql="""
            INSERT OR IGNORE INTO claim_series(
                id, subject_entity_id, stable_key, predicate, value_kind, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        params=(
            series.id,
            series.subject_entity_id,
            series.stable_key,
            series.predicate,
            series.value_kind.value,
            series.created_at,
        ),
        expected={
            "subject_entity_id": series.subject_entity_id,
            "stable_key": series.stable_key,
            "predicate": series.predicate,
            "value_kind": series.value_kind.value,
            "created_at": series.created_at,
        },
    )


def _value_payload(value: ClaimValue) -> dict[str, Any]:
    if isinstance(value, ScalarValue):
        return {
            "kind": value.kind.value,
            "scalar_type": value.scalar_type.value,
            "unit": value.unit,
            "value": (
                float(value.value)
                if value.scalar_type is ScalarType.NUMBER
                else value.value
            ),
        }
    if isinstance(value, GeometryValue):
        return {
            "kind": value.kind.value,
            "geometry": value.geometry,
            "crs": value.crs,
            "precision_m": float(value.precision_m) if value.precision_m is not None else None,
        }
    if isinstance(value, RelationshipValue):
        return {
            "kind": value.kind.value,
            "object_entity_id": value.object_entity_id,
            "relationship_type": value.relationship_type,
            "attributes": value.attributes,
        }
    if isinstance(value, MilestoneValue):
        payload = {
            "kind": value.kind.value,
            "milestone_type": value.milestone_type,
            "status": value.status.value,
            "date_low": value.date_low,
            "date_base": value.date_base,
            "date_high": value.date_high,
        }
        if value.date_precision is not None:
            payload.update(date_precision=value.date_precision, date_literal=value.date_literal)
        return payload
    if isinstance(value, CapabilityValue):
        return {
            "kind": value.kind.value,
            "capability_type": value.capability_type,
            "value": float(value.value) if not isinstance(value.value, str) else value.value,
            "unit": value.unit,
            "qualifier": value.qualifier,
        }
    if isinstance(value, CapacityValue):
        return {
            "kind": value.kind.value,
            "metric": value.metric,
            "basis": value.basis.value,
            "unit": value.unit,
            "low": float(value.low),
            "base": float(value.base),
            "high": float(value.high),
            "period_start": value.period_start,
            "period_end": value.period_end,
        }
    if isinstance(value, ResourceValue):
        return {
            "kind": value.kind.value,
            "resource_type": value.resource_type,
            "unit": value.unit,
            "low": float(value.low),
            "base": float(value.base),
            "high": float(value.high),
            "period_start": value.period_start,
            "period_end": value.period_end,
        }
    if isinstance(value, ConstraintValue):
        return {
            "kind": value.kind.value,
            "constraint_type": value.constraint_type,
            "status": value.status.value,
            "severity": value.severity.value,
            "description": value.description,
            "constrained_entity_id": value.constrained_entity_id,
            "attributes": value.attributes,
        }
    raise TypeError(f"unsupported claim value: {type(value).__name__}")


def value_sha256(value: ClaimValue) -> str:
    encoded = _json(_value_payload(value)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def _atomic(connection: sqlite3.Connection) -> Iterator[None]:
    savepoint = f"claim_insert_{next(_SAVEPOINTS)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except BaseException:
        connection.execute(f"ROLLBACK TO {savepoint}")
        connection.execute(f"RELEASE {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE {savepoint}")


def _insert_typed_value(
    connection: sqlite3.Connection, claim_version_id: str, value: ClaimValue
) -> None:
    connection.execute(
        "INSERT INTO claim_values(claim_version_id, value_kind) VALUES (?, ?)",
        (claim_version_id, value.kind.value),
    )
    if isinstance(value, ScalarValue):
        text_value: str | None = None
        number_value: float | int | None = None
        integer_value: int | None = None
        boolean_value: int | None = None
        if value.scalar_type in {ScalarType.TEXT, ScalarType.DATE, ScalarType.TIMESTAMP}:
            text_value = str(value.value)
        elif value.scalar_type is ScalarType.NUMBER:
            number_value = value.value  # type: ignore[assignment]
        elif value.scalar_type is ScalarType.INTEGER:
            integer_value = int(value.value)
        else:
            boolean_value = int(bool(value.value))
        connection.execute(
            """
            INSERT INTO scalar_values(
                claim_version_id, scalar_type, text_value, number_value, integer_value,
                boolean_value, unit
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim_version_id,
                value.scalar_type.value,
                text_value,
                number_value,
                integer_value,
                boolean_value,
                value.unit,
            ),
        )
    elif isinstance(value, GeometryValue):
        connection.execute(
            """
            INSERT INTO geometry_values(
                claim_version_id, geometry_type, geometry_json, crs, precision_m
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                claim_version_id,
                value.geometry["type"],
                _json(value.geometry),
                value.crs,
                value.precision_m,
            ),
        )
    elif isinstance(value, RelationshipValue):
        connection.execute(
            """
            INSERT INTO relationship_values(
                claim_version_id, object_entity_id, relationship_type, attributes_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                claim_version_id,
                value.object_entity_id,
                value.relationship_type,
                _json(value.attributes),
            ),
        )
    elif isinstance(value, MilestoneValue):
        extra_columns = ", date_precision, date_literal" if value.date_precision is not None else ""
        extra_parameters = ", ?, ?" if extra_columns else ""
        connection.execute(
            f"""
            INSERT INTO milestone_values(
                claim_version_id, milestone_type, status, date_low, date_base, date_high{extra_columns}
            ) VALUES (?, ?, ?, ?, ?, ?{extra_parameters})
            """,
            (
                claim_version_id,
                value.milestone_type,
                value.status.value,
                value.date_low,
                value.date_base,
                value.date_high,
            ) + ((value.date_precision, value.date_literal) if extra_columns else ()),
        )
    elif isinstance(value, CapabilityValue):
        text_value = value.value if isinstance(value.value, str) else None
        number_value = None if isinstance(value.value, str) else float(value.value)
        connection.execute(
            """
            INSERT INTO capability_values(
                claim_version_id, capability_type, text_value, number_value, unit, qualifier
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                claim_version_id,
                value.capability_type,
                text_value,
                number_value,
                value.unit,
                value.qualifier,
            ),
        )
    elif isinstance(value, CapacityValue):
        connection.execute(
            """
            INSERT INTO capacity_values(
                claim_version_id, metric, basis, unit, low, base, high,
                period_start, period_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim_version_id,
                value.metric,
                value.basis.value,
                value.unit,
                value.low,
                value.base,
                value.high,
                value.period_start,
                value.period_end,
            ),
        )
    elif isinstance(value, ResourceValue):
        connection.execute(
            """
            INSERT INTO resource_values(
                claim_version_id, resource_type, unit, low, base, high,
                period_start, period_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim_version_id,
                value.resource_type,
                value.unit,
                value.low,
                value.base,
                value.high,
                value.period_start,
                value.period_end,
            ),
        )
    elif isinstance(value, ConstraintValue):
        connection.execute(
            """
            INSERT INTO constraint_values(
                claim_version_id, constraint_type, status, severity, description,
                constrained_entity_id, attributes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim_version_id,
                value.constraint_type,
                value.status.value,
                value.severity.value,
                value.description,
                value.constrained_entity_id,
                _json(value.attributes),
            ),
        )
    else:
        raise TypeError(f"unsupported claim value: {type(value).__name__}")


def insert_claim(
    connection: sqlite3.Connection,
    version: ClaimVersion,
    value: ClaimValue,
    *,
    evidence: Sequence[EvidenceLink] = (),
    dependencies: Sequence[DependencyLink] = (),
) -> bool:
    if not evidence and not dependencies:
        raise ValueError("a claim requires evidence or a dependency")
    if any(link.depends_on_claim_version_id == version.id for link in dependencies):
        raise ValueError("a claim cannot depend on itself")
    for link in evidence:
        document = connection.execute(
            """
            SELECT retrieved_at,
                   julianday(retrieved_at) <= julianday(?) AS available
            FROM source_documents
            WHERE id = ?
            """,
            (version.recorded_at, link.source_document_id),
        ).fetchone()
        if document is None:
            raise ValueError(f"unknown evidence document: {link.source_document_id}")
        if document["available"] != 1:
            raise ValueError(
                f"evidence document {link.source_document_id} was retrieved after "
                f"claim {version.id} was recorded"
            )
        if link.source_record_id is not None:
            record = connection.execute(
                """
                SELECT records.observed_at, runs.started_at,
                       julianday(records.observed_at) <= julianday(?) AS record_available,
                       julianday(runs.started_at) <= julianday(?) AS run_available
                FROM source_records AS records
                JOIN ingestion_runs AS runs ON runs.id = records.ingestion_run_id
                WHERE records.id = ? AND records.source_document_id = ?
                """,
                (
                    version.recorded_at,
                    version.recorded_at,
                    link.source_record_id,
                    link.source_document_id,
                ),
            ).fetchone()
            if record is None:
                raise ValueError(
                    f"unknown or mismatched evidence source record: {link.source_record_id}"
                )
            if record["record_available"] != 1:
                raise ValueError(
                    f"evidence source record {link.source_record_id} was observed after "
                    f"claim {version.id} was recorded"
                )
            if record["run_available"] != 1:
                raise ValueError(
                    f"evidence source record {link.source_record_id} belongs to an ingestion "
                    f"run started after claim {version.id} was recorded"
                )
    for link in dependencies:
        parent = connection.execute(
            """
            SELECT recorded_at,
                   julianday(recorded_at) <= julianday(?) AS available
            FROM claim_versions
            WHERE id = ?
            """,
            (version.recorded_at, link.depends_on_claim_version_id),
        ).fetchone()
        if parent is None:
            raise ValueError(f"unknown dependency claim: {link.depends_on_claim_version_id}")
        if parent["available"] != 1:
            raise ValueError(
                f"dependency claim {link.depends_on_claim_version_id} was recorded after "
                f"claim {version.id}"
            )
    series = connection.execute(
        """
        SELECT series.value_kind, series.created_at,
               entities.created_at AS entity_created_at,
               julianday(series.created_at) <= julianday(?) AS series_available,
               julianday(entities.created_at) <= julianday(?) AS entity_available
        FROM claim_series AS series
        JOIN entities ON entities.id = series.subject_entity_id
        WHERE series.id = ?
        """,
        (version.recorded_at, version.recorded_at, version.series_id),
    ).fetchone()
    if series is None:
        raise ValueError(f"unknown claim series: {version.series_id}")
    if series["series_available"] != 1:
        raise ValueError(
            f"claim series {version.series_id} was created after claim {version.id} "
            "was recorded"
        )
    if series["entity_available"] != 1:
        raise ValueError(
            f"subject entity for claim series {version.series_id} was created after "
            f"claim {version.id} was recorded"
        )
    if series["value_kind"] != value.kind.value:
        raise ValueError(
            f"claim value kind {value.kind.value!r} does not match series "
            f"kind {series['value_kind']!r}"
        )
    target_entity_id = None
    if isinstance(value, RelationshipValue):
        target_entity_id = value.object_entity_id
    elif isinstance(value, ConstraintValue):
        target_entity_id = value.constrained_entity_id
    if target_entity_id is not None:
        target = connection.execute(
            """
            SELECT julianday(created_at) <= julianday(?) AS available
            FROM entities
            WHERE id = ?
            """,
            (version.recorded_at, target_entity_id),
        ).fetchone()
        if target is None:
            raise ValueError(f"unknown referenced target entity: {target_entity_id}")
        if target["available"] != 1:
            raise ValueError(
                f"referenced target entity {target_entity_id} was created after "
                f"claim {version.id} was recorded"
            )
    fingerprint = value_sha256(value)
    existing = connection.execute(
        "SELECT * FROM claim_versions WHERE id = ?", (version.id,)
    ).fetchone()
    if existing is not None:
        expected = {
            "series_id": version.series_id,
            "value_kind": value.kind.value,
            "value_sha256": fingerprint,
            "valid_from": version.valid_from,
            "valid_to": version.valid_to,
            "recorded_at": version.recorded_at,
            "claim_kind": version.claim_kind.value,
            "method": version.method,
            "confidence": float(version.confidence) if version.confidence is not None else None,
            "created_by_run_id": version.created_by_run_id,
            "notes": version.notes,
        }
        conflicts = [
            key
            for key, expected_value in expected.items()
            if existing[key] != expected_value
        ]
        if conflicts:
            raise ValueError(
                f"claim_versions.{version.id} conflicts on immutable fields: "
                + ", ".join(conflicts)
            )
        expected_evidence = {
            (
                link.source_document_id,
                link.source_record_id,
                link.role.value,
                link.locator,
                link.excerpt,
            )
            for link in evidence
        }
        actual_evidence = {
            tuple(row)
            for row in connection.execute(
                """
                SELECT source_document_id, source_record_id, role, locator, excerpt
                FROM claim_evidence
                WHERE claim_version_id = ?
                """,
                (version.id,),
            )
        }
        expected_dependencies = {
            (link.depends_on_claim_version_id, link.kind.value) for link in dependencies
        }
        actual_dependencies = {
            tuple(row)
            for row in connection.execute(
                """
                SELECT depends_on_claim_version_id, dependency_kind
                FROM claim_dependencies
                WHERE claim_version_id = ?
                """,
                (version.id,),
            )
        }
        if expected_evidence != actual_evidence or expected_dependencies != actual_dependencies:
            raise ValueError(
                f"claim_versions.{version.id} conflicts on immutable lineage"
            )
        return False

    with _atomic(connection):
        later = connection.execute(
            """
            SELECT id FROM claim_versions
            WHERE series_id = ?
              AND valid_from IS ?
              AND superseded_at IS NULL
              AND julianday(recorded_at) >= julianday(?)
            LIMIT 1
            """,
            (version.series_id, version.valid_from, version.recorded_at),
        ).fetchone()
        if later is not None:
            raise ValueError(
                "cannot insert an out-of-order correction behind an active transaction-time version"
            )
        connection.execute(
            """
            UPDATE claim_versions
            SET superseded_at = ?
            WHERE series_id = ?
              AND valid_from IS ?
              AND superseded_at IS NULL
              AND julianday(recorded_at) < julianday(?)
            """,
            (version.recorded_at, version.series_id, version.valid_from, version.recorded_at),
        )
        connection.execute(
            """
            INSERT INTO claim_versions(
                id, series_id, value_kind, value_sha256, valid_from, valid_to,
                recorded_at, claim_kind, method, confidence, created_by_run_id, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version.id,
                version.series_id,
                value.kind.value,
                fingerprint,
                version.valid_from,
                version.valid_to,
                version.recorded_at,
                version.claim_kind.value,
                version.method,
                float(version.confidence) if version.confidence is not None else None,
                version.created_by_run_id,
                version.notes,
            ),
        )
        _insert_typed_value(connection, version.id, value)
        for link in evidence:
            link_id = stable_id(
                "claim-evidence",
                version.id,
                link.source_document_id,
                link.source_record_id or "",
                link.role.value,
                link.locator or "",
                link.excerpt or "",
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO claim_evidence(
                    id, claim_version_id, source_document_id, source_record_id,
                    role, locator, excerpt
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link_id,
                    version.id,
                    link.source_document_id,
                    link.source_record_id,
                    link.role.value,
                    link.locator,
                    link.excerpt,
                ),
            )
        for link in dependencies:
            connection.execute(
                """
                INSERT OR IGNORE INTO claim_dependencies(
                    claim_version_id, depends_on_claim_version_id, dependency_kind
                ) VALUES (?, ?, ?)
                """,
                (version.id, link.depends_on_claim_version_id, link.kind.value),
            )
    return True


def current_claims(
    connection: sqlite3.Connection,
    *,
    as_of: str,
    recorded_at: str,
    subject_entity_id: str | None = None,
    predicate: str | None = None,
) -> list[sqlite3.Row]:
    clock = knowledge_clock_sql(connection)
    return connection.execute(
        """
        WITH eligible AS (
            SELECT versions.*,
                   series.subject_entity_id,
                   series.stable_key AS series_stable_key,
                   series.predicate,
                   ROW_NUMBER() OVER (
                       PARTITION BY versions.series_id
                       ORDER BY versions.valid_from DESC,
                                julianday(versions.recorded_at) DESC,
                                versions.id DESC
                   ) AS temporal_rank
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE versions.valid_from <= ?
              AND (versions.valid_to IS NULL OR ? < versions.valid_to)
              AND julianday(versions.recorded_at) <= julianday(?)
              AND (
                    versions.superseded_at IS NULL
                    OR julianday(?) < julianday(versions.superseded_at)
                  )
              AND (? IS NULL OR series.subject_entity_id = ?)
              AND (? IS NULL OR series.predicate = ?)
        )
        SELECT id, series_id, value_kind, value_sha256, valid_from, valid_to,
               recorded_at,
               CASE
                   WHEN superseded_at IS NOT NULL
                    AND julianday(superseded_at) <= julianday(?)
                   THEN superseded_at
                   ELSE NULL
               END AS superseded_at,
               claim_kind, method, confidence, created_by_run_id, notes,
               subject_entity_id, series_stable_key, predicate, temporal_rank
        FROM eligible
        WHERE temporal_rank = 1
        ORDER BY subject_entity_id, predicate, series_id
        """.replace("julianday(", f"{clock}("),
        (
            as_of,
            as_of,
            recorded_at,
            recorded_at,
            subject_entity_id,
            subject_entity_id,
            predicate,
            predicate,
            recorded_at,
        ),
    ).fetchall()


def known_source_claims(
    connection: sqlite3.Connection,
    *,
    recorded_at: str,
    subject_entity_id: str | None = None,
) -> list[sqlite3.Row]:
    """Visible source statements by knowledge time, never a physical-world state view."""
    recorded_at = _normalized_timestamp(recorded_at, "recorded_at")
    clock = knowledge_clock_sql(connection)
    return connection.execute(
        """
        SELECT versions.id, versions.series_id, versions.value_kind, versions.value_sha256,
               versions.valid_from, versions.valid_to, versions.recorded_at,
               CASE WHEN julianday(versions.superseded_at) <= julianday(?)
                    THEN versions.superseded_at ELSE NULL END AS superseded_at,
               versions.claim_kind, versions.method, versions.confidence,
               versions.created_by_run_id, versions.notes, series.subject_entity_id,
               series.stable_key AS series_stable_key, series.predicate
        FROM claim_versions AS versions
        JOIN claim_series AS series ON series.id = versions.series_id
        WHERE versions.claim_kind = 'source_statement'
          AND julianday(versions.recorded_at) <= julianday(?)
          AND (versions.superseded_at IS NULL OR julianday(?) < julianday(versions.superseded_at))
          AND (? IS NULL OR series.subject_entity_id = ?)
        ORDER BY series.subject_entity_id, series.predicate, series.id,
                 versions.valid_from, versions.recorded_at, versions.id
        """.replace("julianday(", f"{clock}("),
        (recorded_at, recorded_at, recorded_at, subject_entity_id, subject_entity_id),
    ).fetchall()


def _stored_claim_value(
    connection: sqlite3.Connection,
    claim_version_id: str,
    value_kind: str,
) -> ClaimValue:
    table = {
        "scalar": "scalar_values",
        "geometry": "geometry_values",
        "relationship": "relationship_values",
        "milestone": "milestone_values",
        "capability": "capability_values",
        "capacity": "capacity_values",
        "resource": "resource_values",
        "constraint": "constraint_values",
    }[value_kind]
    row = connection.execute(
        f"SELECT * FROM {table} WHERE claim_version_id = ?",
        (claim_version_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"claim {claim_version_id} lacks its {value_kind} value")
    if value_kind == "scalar":
        scalar_type = ScalarType(row["scalar_type"])
        if scalar_type in {ScalarType.TEXT, ScalarType.DATE, ScalarType.TIMESTAMP}:
            scalar: str | float | int | bool = row["text_value"]
        elif scalar_type is ScalarType.NUMBER:
            scalar = float(row["number_value"])
        elif scalar_type is ScalarType.INTEGER:
            scalar = int(row["integer_value"])
        else:
            scalar = bool(row["boolean_value"])
        return ScalarValue(scalar_type, scalar, row["unit"])
    if value_kind == "geometry":
        return GeometryValue(json.loads(row["geometry_json"]), row["crs"], row["precision_m"])
    if value_kind == "relationship":
        return RelationshipValue(
            row["object_entity_id"],
            row["relationship_type"],
            json.loads(row["attributes_json"]),
        )
    if value_kind == "milestone":
        return MilestoneValue(
            row["milestone_type"],
            MilestoneStatus(row["status"]),
            row["date_low"],
            row["date_base"],
            row["date_high"],
            row["date_precision"] if "date_precision" in row.keys() else None,
            row["date_literal"] if "date_literal" in row.keys() else None,
        )
    if value_kind == "capability":
        capability = (
            row["text_value"]
            if row["text_value"] is not None
            else float(row["number_value"])
        )
        return CapabilityValue(
            row["capability_type"], capability, row["unit"], row["qualifier"]
        )
    if value_kind == "capacity":
        return CapacityValue(
            row["metric"],
            CapacityBasis(row["basis"]),
            row["unit"],
            row["low"],
            row["base"],
            row["high"],
            row["period_start"],
            row["period_end"],
        )
    if value_kind == "resource":
        return ResourceValue(
            row["resource_type"],
            row["unit"],
            row["low"],
            row["base"],
            row["high"],
            row["period_start"],
            row["period_end"],
        )
    return ConstraintValue(
        row["constraint_type"],
        ConstraintStatus(row["status"]),
        ConstraintSeverity(row["severity"]),
        row["description"],
        row["constrained_entity_id"],
        json.loads(row["attributes_json"]),
    )


def validate_database(connection: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        errors.append(f"integrity_check: {integrity}")
    for row in connection.execute("PRAGMA foreign_key_check"):
        errors.append(f"foreign_key_check: table={row[0]} rowid={row[1]} parent={row[2]}")

    for row in connection.execute(
        "SELECT id, record_sha256, payload_json FROM source_records ORDER BY id"
    ):
        try:
            payload = json.loads(row["payload_json"])
            actual_hash = source_record_payload_sha256(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"source record {row['id']} payload cannot be hashed: {error}")
            continue
        if actual_hash != row["record_sha256"]:
            errors.append(
                f"source record {row['id']} payload does not match record_sha256"
            )

    for row in connection.execute(
        """
        SELECT records.id, records.ingestion_run_id
        FROM source_records AS records
        JOIN ingestion_runs AS runs ON runs.id = records.ingestion_run_id
        WHERE runs.status != 'succeeded'
        ORDER BY records.id
        """
    ):
        errors.append(
            f"source record {row['id']} belongs to non-succeeded ingestion run "
            f"{row['ingestion_run_id']}"
        )

    for row in connection.execute(
        """
        SELECT entities.id, entities.created_by_run_id
        FROM entities
        JOIN ingestion_runs AS runs ON runs.id = entities.created_by_run_id
        WHERE entities.created_by_run_id IS NOT NULL
          AND runs.status != 'succeeded'
        ORDER BY entities.id
        """
    ):
        errors.append(
            f"entity {row['id']} was produced by non-succeeded ingestion run "
            f"{row['created_by_run_id']}"
        )

    for row in connection.execute(
        """
        SELECT versions.id, versions.created_by_run_id
        FROM claim_versions AS versions
        JOIN ingestion_runs AS runs ON runs.id = versions.created_by_run_id
        WHERE versions.created_by_run_id IS NOT NULL
          AND runs.status != 'succeeded'
        ORDER BY versions.id
        """
    ):
        errors.append(
            f"claim {row['id']} was produced by non-succeeded ingestion run "
            f"{row['created_by_run_id']}"
        )

    typed_rows = connection.execute(
        """
        SELECT versions.id, versions.value_kind,
               registry.value_kind AS registry_kind,
               (scalar_values.claim_version_id IS NOT NULL)
               + (geometry_values.claim_version_id IS NOT NULL)
               + (relationship_values.claim_version_id IS NOT NULL)
               + (milestone_values.claim_version_id IS NOT NULL)
               + (capability_values.claim_version_id IS NOT NULL)
               + (capacity_values.claim_version_id IS NOT NULL)
               + (resource_values.claim_version_id IS NOT NULL)
               + (constraint_values.claim_version_id IS NOT NULL) AS typed_count
        FROM claim_versions AS versions
        LEFT JOIN claim_values AS registry ON registry.claim_version_id = versions.id
        LEFT JOIN scalar_values ON scalar_values.claim_version_id = versions.id
        LEFT JOIN geometry_values ON geometry_values.claim_version_id = versions.id
        LEFT JOIN relationship_values ON relationship_values.claim_version_id = versions.id
        LEFT JOIN milestone_values ON milestone_values.claim_version_id = versions.id
        LEFT JOIN capability_values ON capability_values.claim_version_id = versions.id
        LEFT JOIN capacity_values ON capacity_values.claim_version_id = versions.id
        LEFT JOIN resource_values ON resource_values.claim_version_id = versions.id
        LEFT JOIN constraint_values ON constraint_values.claim_version_id = versions.id
        """
    ).fetchall()
    for row in typed_rows:
        if row["registry_kind"] != row["value_kind"] or row["typed_count"] != 1:
            errors.append(
                f"claim {row['id']} must have exactly one {row['value_kind']} typed value"
            )

    for row in connection.execute(
        "SELECT id, value_kind, value_sha256 FROM claim_versions ORDER BY id"
    ):
        try:
            actual_hash = value_sha256(
                _stored_claim_value(connection, row["id"], row["value_kind"])
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"claim {row['id']} value cannot be reconstructed: {error}")
            continue
        if actual_hash != row["value_sha256"]:
            errors.append(f"claim {row['id']} typed value does not match value_sha256")

    for row in connection.execute(
        """
        SELECT versions.id
        FROM claim_versions AS versions
        WHERE NOT EXISTS (
            SELECT 1 FROM claim_evidence WHERE claim_version_id = versions.id
        )
          AND NOT EXISTS (
            SELECT 1 FROM claim_dependencies WHERE claim_version_id = versions.id
        )
        """
    ):
        errors.append(f"claim {row['id']} has no evidence or dependency lineage")

    cycles = connection.execute(
        """
        WITH RECURSIVE reach(start_id, claim_version_id) AS (
            SELECT claim_version_id, depends_on_claim_version_id
            FROM claim_dependencies
            UNION
            SELECT reach.start_id, dependencies.depends_on_claim_version_id
            FROM reach
            JOIN claim_dependencies AS dependencies
              ON dependencies.claim_version_id = reach.claim_version_id
        )
        SELECT DISTINCT start_id FROM reach WHERE start_id = claim_version_id
        """
    ).fetchall()
    for row in cycles:
        errors.append(f"claim dependency cycle includes {row['start_id']}")

    overlaps = connection.execute(
        """
        SELECT series_id, valid_from, COUNT(*) AS versions
        FROM claim_versions
        WHERE superseded_at IS NULL
        GROUP BY series_id, valid_from
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for row in overlaps:
        errors.append(
            f"claim series {row['series_id']} has overlapping transaction versions "
            f"at {row['valid_from']}"
        )

    for row in connection.execute(
        "SELECT id, valid_from, valid_to, recorded_at, superseded_at, claim_kind FROM claim_versions"
    ):
        try:
            if row["valid_from"] is None:
                if row["claim_kind"] != "source_statement" or row["valid_to"] is not None:
                    raise ValueError("unknown effective time requires an unbounded source statement")
            else:
                datetime.fromisoformat(row["valid_from"])
            if row["valid_to"]:
                datetime.fromisoformat(row["valid_to"])
            datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
            if row["superseded_at"]:
                datetime.fromisoformat(row["superseded_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            errors.append(f"claim {row['id']} contains an invalid temporal value")

    for row in connection.execute(
        """
        SELECT evidence.claim_version_id, evidence.source_document_id,
               documents.retrieved_at, versions.recorded_at
        FROM claim_evidence AS evidence
        JOIN claim_versions AS versions ON versions.id = evidence.claim_version_id
        JOIN source_documents AS documents ON documents.id = evidence.source_document_id
        WHERE julianday(documents.retrieved_at) IS NULL
           OR julianday(versions.recorded_at) IS NULL
           OR julianday(documents.retrieved_at) > julianday(versions.recorded_at)
        ORDER BY evidence.claim_version_id, evidence.source_document_id
        """
    ):
        errors.append(
            f"claim {row['claim_version_id']} uses evidence document "
            f"{row['source_document_id']} retrieved after the claim was recorded"
        )

    for row in connection.execute(
        """
        SELECT evidence.claim_version_id, evidence.source_record_id,
               records.observed_at, versions.recorded_at
        FROM claim_evidence AS evidence
        JOIN claim_versions AS versions ON versions.id = evidence.claim_version_id
        JOIN source_records AS records ON records.id = evidence.source_record_id
        WHERE evidence.source_record_id IS NOT NULL
          AND (
                julianday(records.observed_at) IS NULL
                OR julianday(versions.recorded_at) IS NULL
                OR julianday(records.observed_at) > julianday(versions.recorded_at)
              )
        ORDER BY evidence.claim_version_id, evidence.source_record_id
        """
    ):
        errors.append(
            f"claim {row['claim_version_id']} uses source record "
            f"{row['source_record_id']} observed after the claim was recorded"
        )

    for row in connection.execute(
        """
        SELECT evidence.claim_version_id, evidence.source_record_id,
               runs.id AS ingestion_run_id, runs.started_at, versions.recorded_at
        FROM claim_evidence AS evidence
        JOIN claim_versions AS versions ON versions.id = evidence.claim_version_id
        JOIN source_records AS records ON records.id = evidence.source_record_id
        JOIN ingestion_runs AS runs ON runs.id = records.ingestion_run_id
        WHERE evidence.source_record_id IS NOT NULL
          AND (
                julianday(runs.started_at) IS NULL
                OR julianday(versions.recorded_at) IS NULL
                OR julianday(runs.started_at) > julianday(versions.recorded_at)
              )
        ORDER BY evidence.claim_version_id, evidence.source_record_id
        """
    ):
        errors.append(
            f"claim {row['claim_version_id']} uses source record "
            f"{row['source_record_id']} from later-started ingestion run "
            f"{row['ingestion_run_id']}"
        )

    for row in connection.execute(
        """
        SELECT dependencies.claim_version_id,
               dependencies.depends_on_claim_version_id,
               children.recorded_at AS child_recorded_at,
               parents.recorded_at AS parent_recorded_at
        FROM claim_dependencies AS dependencies
        JOIN claim_versions AS children ON children.id = dependencies.claim_version_id
        JOIN claim_versions AS parents
          ON parents.id = dependencies.depends_on_claim_version_id
        WHERE julianday(children.recorded_at) IS NULL
           OR julianday(parents.recorded_at) IS NULL
           OR julianday(parents.recorded_at) > julianday(children.recorded_at)
        ORDER BY dependencies.claim_version_id,
                 dependencies.depends_on_claim_version_id
        """
    ):
        errors.append(
            f"claim {row['claim_version_id']} depends on later-recorded claim "
            f"{row['depends_on_claim_version_id']}"
        )

    for row in connection.execute(
        """
        SELECT versions.id, series.id AS series_id
        FROM claim_versions AS versions
        JOIN claim_series AS series ON series.id = versions.series_id
        WHERE julianday(series.created_at) IS NULL
           OR julianday(versions.recorded_at) IS NULL
           OR julianday(series.created_at) > julianday(versions.recorded_at)
        ORDER BY versions.id
        """
    ):
        errors.append(
            f"claim {row['id']} predates its claim series {row['series_id']}"
        )

    for row in connection.execute(
        """
        SELECT versions.id, entities.id AS entity_id
        FROM claim_versions AS versions
        JOIN claim_series AS series ON series.id = versions.series_id
        JOIN entities ON entities.id = series.subject_entity_id
        WHERE julianday(entities.created_at) IS NULL
           OR julianday(versions.recorded_at) IS NULL
           OR julianday(entities.created_at) > julianday(versions.recorded_at)
        ORDER BY versions.id
        """
    ):
        errors.append(
            f"claim {row['id']} predates its subject entity {row['entity_id']}"
        )

    for table, entity_column in (
        ("relationship_values", "object_entity_id"),
        ("constraint_values", "constrained_entity_id"),
    ):
        for row in connection.execute(
            f"""
            SELECT versions.id, values_.{entity_column} AS entity_id
            FROM {table} AS values_
            JOIN claim_versions AS versions
              ON versions.id = values_.claim_version_id
            JOIN entities ON entities.id = values_.{entity_column}
            WHERE values_.{entity_column} IS NOT NULL
              AND (
                    julianday(entities.created_at) IS NULL
                    OR julianday(versions.recorded_at) IS NULL
                    OR julianday(entities.created_at) > julianday(versions.recorded_at)
                  )
            ORDER BY versions.id
            """
        ):
            errors.append(
                f"claim {row['id']} predates its referenced target entity "
                f"{row['entity_id']}"
            )

    for row in connection.execute(
        """
        SELECT runs.id, runs.input_document_id
        FROM ingestion_runs AS runs
        JOIN source_documents AS documents ON documents.id = runs.input_document_id
        WHERE runs.input_document_id IS NOT NULL
          AND runs.source_id != documents.source_id
        ORDER BY runs.id
        """
    ):
        errors.append(
            f"ingestion run {row['id']} uses primary document "
            f"{row['input_document_id']} from a different source"
        )

    for row in connection.execute(
        """
        SELECT records.id, records.source_document_id, records.ingestion_run_id
        FROM source_records AS records
        JOIN ingestion_runs AS runs ON runs.id = records.ingestion_run_id
        JOIN source_documents AS documents ON documents.id = records.source_document_id
        WHERE runs.source_id != documents.source_id
        ORDER BY records.id
        """
    ):
        errors.append(
            f"source record {row['id']} links ingestion run {row['ingestion_run_id']} "
            f"to document {row['source_document_id']} from a different source"
        )

    identity_tables = {
        "ingestion_run_documents",
        "organization_name_claim_metadata",
        "organization_identifier_claim_metadata",
        "entity_resolution_runs",
        "entity_resolution_run_inputs",
        "entity_resolution_candidates",
        "entity_resolution_decisions",
        "source_entity_assignments",
    }
    present_identity_tables = {
        row[0]
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name IN (
                'ingestion_run_documents',
                'organization_name_claim_metadata',
                'organization_identifier_claim_metadata',
                'entity_resolution_runs',
                'entity_resolution_run_inputs',
                'entity_resolution_candidates',
                'entity_resolution_decisions',
                'source_entity_assignments'
            )
            """
        )
    }
    if not present_identity_tables:
        return errors
    if present_identity_tables != identity_tables:
        missing = ", ".join(sorted(identity_tables - present_identity_tables))
        errors.append(f"entity identity schema is incomplete; missing tables: {missing}")
        return errors

    for row in connection.execute(
        """
        SELECT links.ingestion_run_id, links.source_document_id
        FROM ingestion_run_documents AS links
        JOIN ingestion_runs AS runs ON runs.id = links.ingestion_run_id
        JOIN source_documents AS documents ON documents.id = links.source_document_id
        WHERE runs.source_id != documents.source_id
        ORDER BY links.ingestion_run_id, links.source_document_id
        """
    ):
        errors.append(
            f"ingestion run {row['ingestion_run_id']} references document "
            f"{row['source_document_id']} from a different source"
        )

    for row in connection.execute(
        """
        SELECT runs.id, runs.input_document_id
        FROM ingestion_runs AS runs
        WHERE runs.input_document_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM ingestion_run_documents AS links
              WHERE links.ingestion_run_id = runs.id
                AND links.source_document_id = runs.input_document_id
                AND links.role = 'primary'
          )
        ORDER BY runs.id
        """
    ):
        errors.append(
            f"ingestion run {row['id']} lacks its primary document link "
            f"{row['input_document_id']}"
        )

    for row in connection.execute(
        """
        SELECT records.id, records.ingestion_run_id, records.source_document_id
        FROM source_records AS records
        WHERE NOT EXISTS (
            SELECT 1
            FROM ingestion_run_documents AS links
            WHERE links.ingestion_run_id = records.ingestion_run_id
              AND links.source_document_id = records.source_document_id
              AND links.role = 'source_record'
        )
        ORDER BY records.id
        """
    ):
        errors.append(
            f"source record {row['id']} lacks its ingestion-run document link"
        )

    for row in connection.execute(
        "SELECT id, parameters_json FROM ingestion_runs ORDER BY id"
    ):
        try:
            parameters = json.loads(row["parameters_json"])
        except (TypeError, json.JSONDecodeError):
            errors.append(f"ingestion run {row['id']} has invalid parameters JSON")
            continue
        if not isinstance(parameters, dict):
            errors.append(f"ingestion run {row['id']} parameters must be an object")
            continue
        for key, role in (
            ("index_document_ids", "parameter:index_document_ids"),
            ("detail_document_ids", "parameter:detail_document_ids"),
        ):
            declared = parameters.get(key)
            if declared is None:
                continue
            if not isinstance(declared, list) or any(
                not isinstance(document_id, str) or not document_id
                for document_id in declared
            ):
                errors.append(
                    f"ingestion run {row['id']} parameter {key} must be a list of document IDs"
                )
                continue
            for document_id in declared:
                linked = connection.execute(
                    """
                    SELECT 1
                    FROM ingestion_run_documents
                    WHERE ingestion_run_id = ? AND source_document_id = ? AND role = ?
                    """,
                    (row["id"], document_id, role),
                ).fetchone()
                if linked is None:
                    errors.append(
                        f"ingestion run {row['id']} parameter {key} lacks document link "
                        f"{document_id}"
                    )

    for metadata_table, other_table, label in (
        (
            "organization_name_claim_metadata",
            "organization_identifier_claim_metadata",
            "name",
        ),
        (
            "organization_identifier_claim_metadata",
            "organization_name_claim_metadata",
            "identifier",
        ),
    ):
        for row in connection.execute(
            f"""
            SELECT metadata.claim_version_id, scalar.scalar_type, entities.kind,
                   (other.claim_version_id IS NOT NULL) AS has_other_type
            FROM {metadata_table} AS metadata
            LEFT JOIN scalar_values AS scalar
              ON scalar.claim_version_id = metadata.claim_version_id
            LEFT JOIN claim_versions AS versions
              ON versions.id = metadata.claim_version_id
            LEFT JOIN claim_series AS series ON series.id = versions.series_id
            LEFT JOIN entities ON entities.id = series.subject_entity_id
            LEFT JOIN {other_table} AS other
              ON other.claim_version_id = metadata.claim_version_id
            WHERE scalar.scalar_type IS NOT 'text'
               OR entities.kind IS NOT 'organization'
               OR other.claim_version_id IS NOT NULL
            ORDER BY metadata.claim_version_id
            """
        ):
            errors.append(
                f"organization {label} metadata {row['claim_version_id']} "
                "does not annotate exactly one organization text claim type"
            )

    for row in connection.execute(
        """
        SELECT id
        FROM entity_resolution_runs
        WHERE julianday(started_at) IS NULL
           OR (status = 'running' AND (completed_at IS NOT NULL OR error IS NOT NULL))
           OR (status = 'succeeded' AND (completed_at IS NULL OR error IS NOT NULL))
           OR (
                status = 'failed'
                AND (completed_at IS NULL OR error IS NULL OR length(trim(error)) = 0)
           )
           OR (
                completed_at IS NOT NULL
                AND (
                    julianday(completed_at) IS NULL
                    OR julianday(completed_at) <= julianday(started_at)
                )
           )
        ORDER BY id
        """
    ):
        errors.append(f"entity resolution run {row['id']} has invalid timestamps")

    for row in connection.execute(
        """
        SELECT inputs.resolution_run_id, inputs.ingestion_run_id
        FROM entity_resolution_run_inputs AS inputs
        JOIN ingestion_runs AS runs ON runs.id = inputs.ingestion_run_id
        JOIN entity_resolution_runs AS resolution_runs
          ON resolution_runs.id = inputs.resolution_run_id
        WHERE runs.status != 'succeeded'
           OR julianday(resolution_runs.started_at) IS NULL
           OR julianday(runs.completed_at) IS NULL
           OR julianday(resolution_runs.started_at) < julianday(runs.completed_at)
        ORDER BY inputs.resolution_run_id, inputs.ingestion_run_id
        """
    ):
        errors.append(
            f"resolution run {row['resolution_run_id']} uses non-succeeded ingestion run "
            f"{row['ingestion_run_id']}"
        )

    for row in connection.execute(
        """
        SELECT candidates.id
        FROM entity_resolution_candidates AS candidates
        JOIN entity_resolution_runs AS resolution_runs
          ON resolution_runs.id = candidates.resolution_run_id
        JOIN source_records AS records ON records.id = candidates.source_record_id
        JOIN entities AS observed ON observed.id = candidates.observed_entity_id
        JOIN entities AS proposed ON proposed.id = candidates.candidate_entity_id
        WHERE observed.kind NOT IN ('organization', 'facility')
           OR proposed.kind != observed.kind
           OR candidates.observed_entity_id = candidates.candidate_entity_id
           OR typeof(candidates.candidate_rank) != 'integer'
           OR candidates.candidate_rank < 1
           OR julianday(candidates.created_at) IS NULL
           OR julianday(candidates.created_at) < julianday(resolution_runs.started_at)
           OR (
                resolution_runs.completed_at IS NOT NULL
                AND julianday(candidates.created_at)
                    > julianday(resolution_runs.completed_at)
           )
           OR julianday(records.observed_at) IS NULL
           OR julianday(candidates.created_at) < julianday(records.observed_at)
           OR julianday(records.observed_at) > julianday(resolution_runs.started_at)
           OR julianday(observed.created_at) IS NULL
           OR julianday(observed.created_at) > julianday(resolution_runs.started_at)
           OR julianday(proposed.created_at) IS NULL
           OR julianday(proposed.created_at) > julianday(resolution_runs.started_at)
           OR NOT EXISTS (
                SELECT 1
                FROM entity_resolution_run_inputs AS inputs
                WHERE inputs.resolution_run_id = candidates.resolution_run_id
                  AND inputs.ingestion_run_id = records.ingestion_run_id
           )
           OR NOT EXISTS (
                SELECT 1
                FROM claim_evidence AS evidence
                JOIN claim_versions AS versions
                  ON versions.id = evidence.claim_version_id
                JOIN claim_series AS series ON series.id = versions.series_id
                WHERE evidence.source_record_id = candidates.source_record_id
                  AND series.subject_entity_id = candidates.observed_entity_id
                  AND julianday(versions.recorded_at)
                      <= julianday(resolution_runs.started_at)
           )
        ORDER BY candidates.id
        """
    ):
        errors.append(f"entity resolution candidate {row['id']} has invalid source lineage")

    for row in connection.execute(
        """
        SELECT id, resolution_run_id, candidate_entity_id, features_json
        FROM entity_resolution_candidates
        ORDER BY id
        """
    ):
        try:
            features = json.loads(row["features_json"])
            evidence_ids = _candidate_target_evidence_ids(features)
            if evidence_ids is not None:
                _validate_candidate_target_evidence(
                    connection,
                    resolution_run_id=str(row["resolution_run_id"]),
                    candidate_entity_id=str(row["candidate_entity_id"]),
                    claim_ids=evidence_ids,
                )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append(
                f"entity resolution candidate {row['id']} has invalid target evidence: "
                f"{error}"
            )

    for row in connection.execute(
        """
        SELECT decisions.id
        FROM entity_resolution_decisions AS decisions
        JOIN entity_resolution_candidates AS candidates
          ON candidates.id = decisions.candidate_id
        JOIN entity_resolution_runs AS resolution_runs
          ON resolution_runs.id = candidates.resolution_run_id
        WHERE julianday(decisions.decided_at) IS NULL
           OR resolution_runs.status != 'succeeded'
           OR julianday(decisions.decided_at) < julianday(candidates.created_at)
           OR julianday(decisions.decided_at) < julianday(resolution_runs.completed_at)
        ORDER BY decisions.id
        """
    ):
        errors.append(f"entity resolution decision {row['id']} predates its candidate")

    for row in connection.execute(
        """
        SELECT assignments.id
        FROM source_entity_assignments AS assignments
        JOIN entity_resolution_decisions AS decisions
          ON decisions.id = assignments.decision_id
        JOIN entity_resolution_candidates AS candidates
          ON candidates.id = decisions.candidate_id
        JOIN source_records AS records ON records.id = assignments.source_record_id
        JOIN entities AS observed ON observed.id = assignments.observed_entity_id
        JOIN entities AS canonical ON canonical.id = assignments.canonical_entity_id
        WHERE decisions.outcome != 'match'
           OR candidates.source_record_id != assignments.source_record_id
           OR candidates.observed_entity_id != assignments.observed_entity_id
           OR candidates.candidate_entity_id != assignments.canonical_entity_id
           OR observed.kind NOT IN ('organization', 'facility')
           OR canonical.kind != observed.kind
           OR length(assignments.valid_from) != 10
           OR date(assignments.valid_from) IS NULL
           OR date(assignments.valid_from) != assignments.valid_from
           OR (
                assignments.valid_to IS NOT NULL
                AND (
                    length(assignments.valid_to) != 10
                    OR date(assignments.valid_to) IS NULL
                    OR date(assignments.valid_to) != assignments.valid_to
                    OR assignments.valid_to <= assignments.valid_from
                )
           )
           OR julianday(assignments.recorded_at) IS NULL
           OR julianday(assignments.recorded_at) < julianday(decisions.decided_at)
           OR julianday(assignments.recorded_at) < julianday(records.observed_at)
           OR julianday(assignments.recorded_at) < julianday(observed.created_at)
           OR julianday(assignments.recorded_at) < julianday(canonical.created_at)
           OR (
                assignments.superseded_at IS NOT NULL
                AND (
                    julianday(assignments.superseded_at) IS NULL
                    OR julianday(assignments.superseded_at)
                       <= julianday(assignments.recorded_at)
                )
           )
        ORDER BY assignments.id
        """
    ):
        errors.append(f"source entity assignment {row['id']} has invalid decision lineage")

    for row in connection.execute(
        """
        SELECT left_.id AS left_id, right_.id AS right_id
        FROM source_entity_assignments AS left_
        JOIN source_entity_assignments AS right_
          ON right_.observed_entity_id = left_.observed_entity_id
         AND right_.id > left_.id
        WHERE left_.valid_from < COALESCE(right_.valid_to, '9999-12-31')
          AND right_.valid_from < COALESCE(left_.valid_to, '9999-12-31')
          AND julianday(left_.recorded_at) < julianday(
              COALESCE(right_.superseded_at, '9999-12-31T23:59:59Z')
          )
          AND julianday(right_.recorded_at) < julianday(
              COALESCE(left_.superseded_at, '9999-12-31T23:59:59Z')
          )
        ORDER BY left_.id, right_.id
        """
    ):
        errors.append(
            f"source entity assignments {row['left_id']} and {row['right_id']} "
            "overlap at current knowledge"
        )
    return errors
