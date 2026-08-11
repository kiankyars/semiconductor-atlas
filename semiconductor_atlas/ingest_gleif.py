"""Import reviewed GLEIF Level 1 snapshots into the append-only claim store."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator, Mapping, Sequence

from .adapters.gleif_lei import (
    GLEIFLEIRecord,
    canonical_lei_payload_bytes,
    parse_lei_jsonapi_bytes,
)
from .gleif_review import (
    GLEIFReviewDecision,
    GLEIFReviewManifest,
    parse_gleif_review_bytes,
    read_gleif_review_file,
)
from .gleif_snapshot import (
    GLEIF_API_BASE_URL,
    GLEIF_ATTRIBUTION,
    GLEIF_LICENSE,
    GLEIF_LICENSE_URL,
    GLEIF_TERMS_URL,
    VerifiedGLEIFResponse,
    VerifiedGLEIFSnapshot,
    verify_gleif_snapshot,
)
from .models import (
    ClaimKind,
    ClaimSeries,
    ClaimVersion,
    Entity,
    EntityKind,
    EntityResolutionCandidate,
    EntityResolutionDecision,
    EntityResolutionRun,
    EntityResolutionRunInput,
    EntityResolutionStatus,
    EvidenceLink,
    EvidenceRole,
    IngestionRun,
    IngestionRunDocument,
    IngestionStatus,
    OrganizationIdentifierClaimMetadata,
    OrganizationNameClaimMetadata,
    OrganizationNameType,
    ResolutionDecisionOutcome,
    ScalarType,
    ScalarValue,
    Source,
    SourceDocument,
    SourceEntityAssignment,
    SourceFamily,
    SourceRecord,
    ValueKind,
    source_record_payload_sha256,
)
from .repository import (
    add_claim_series,
    add_entity,
    add_entity_resolution_candidate,
    add_entity_resolution_decision,
    add_entity_resolution_run,
    add_entity_resolution_run_input,
    add_ingestion_run,
    add_ingestion_run_document,
    add_organization_identifier_claim_metadata,
    add_organization_name_claim_metadata,
    add_source,
    add_source_document,
    add_source_entity_assignment,
    add_source_family,
    add_source_record,
    finalize_entity_resolution_run,
    insert_claim,
    stable_id,
    supersede_source_entity_assignment,
    validate_database,
    value_sha256,
)


IMPORTER_VERSION = "gleif-level-1-reviewed-import-v1"
RESOLVER_VERSION = "gleif-level-1-manual-review-v1"
SOURCE_FAMILY_KEY = "gleif"
SOURCE_KEY = "gleif-lei-api-level-1"
ACCEPTANCE_TIMESTAMP_BASES = frozenset(
    {
        "explicit_operator_supplied",
        "process_clock_after_snapshot_and_review_verification",
    }
)

_SAVEPOINTS = itertools.count()
_LANGUAGE_TAG_RE = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")


@dataclass(frozen=True, slots=True)
class GLEIFImportResult:
    source_family_id: str
    source_id: str
    canonical_document_id: str
    raw_document_ids: tuple[str, ...]
    ingestion_run_id: str
    resolution_run_id: str
    accepted_at: str
    knowledge_cutoff_at: str
    acceptance_timestamp_basis: str
    replayed_existing_ingestion: bool
    replayed_existing_resolution: bool
    records_imported: int
    source_documents_created: int
    source_records_created: int
    entities_created: int
    claim_series_created: int
    claims_created: int
    unchanged_claims_reused: int
    prior_open_claims_closed_or_corrected: int
    resolution_candidates_created: int
    resolution_decisions_created: int
    assignments_created: int
    assignments_superseded: int
    observed_entity_ids: tuple[str, ...]
    source_record_ids: tuple[str, ...]
    assignment_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ClaimSpec:
    predicate: str
    dimension: str
    value: ScalarValue
    locator: str
    excerpt: str
    method: str
    notes: str
    name_metadata: tuple[OrganizationNameType, str | None] | None = None
    identifier_metadata: tuple[str, str, str | None] | None = None


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


def _offset(value: str, seconds: int) -> str:
    return (_clock(value) + timedelta(seconds=seconds)).isoformat().replace(
        "+00:00", "Z"
    )


def _as_of_date(timestamp: str) -> str:
    return _clock(timestamp).date().isoformat()


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
    started_outer = not connection.in_transaction
    if started_outer:
        connection.execute("BEGIN IMMEDIATE")
    savepoint = f"gleif_import_{next(_SAVEPOINTS)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except BaseException:
        connection.execute(f"ROLLBACK TO {savepoint}")
        connection.execute(f"RELEASE {savepoint}")
        if started_outer:
            connection.rollback()
        raise
    else:
        connection.execute(f"RELEASE {savepoint}")


def _ensure_document(
    connection: sqlite3.Connection, document: SourceDocument
) -> bool:
    existing = connection.execute(
        "SELECT * FROM source_documents WHERE id = ?", (document.id,)
    ).fetchone()
    if existing is None:
        return add_source_document(connection, document)
    metadata_json = _canonical_json(document.metadata)
    expected = {
        "source_id": document.source_id,
        "document_url": document.document_url,
        "title": document.title,
        "published_at": document.published_at,
        "retrieved_at": document.retrieved_at,
        "content_sha256": document.content_sha256,
        "media_type": document.media_type,
        "license": document.license,
        "metadata_json": metadata_json,
    }
    conflicts = [key for key, expected_value in expected.items() if existing[key] != expected_value]
    if conflicts:
        raise ValueError(
            f"GLEIF source document {document.id} conflicts on immutable fields: "
            + ", ".join(conflicts)
        )
    return False


def _ensure_observed_entity(
    connection: sqlite3.Connection,
    *,
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
        if (
            existing["kind"] != EntityKind.ORGANIZATION.value
            or existing["stable_key"] != stable_key
        ):
            raise ValueError(f"entity {entity_id} conflicts with GLEIF identity")
        return False
    return add_entity(
        connection,
        Entity(
            entity_id,
            EntityKind.ORGANIZATION,
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
    dimension: str,
    created_at: str,
) -> tuple[str, bool]:
    series_id = stable_id(
        "claim-series", entity_id, predicate, ValueKind.SCALAR.value, dimension
    )
    stable_key = f"{entity_key}:claim:{predicate}:{dimension}"
    existing = connection.execute(
        "SELECT * FROM claim_series WHERE id = ?", (series_id,)
    ).fetchone()
    if existing is not None:
        expected = {
            "subject_entity_id": entity_id,
            "stable_key": stable_key,
            "predicate": predicate,
            "value_kind": ValueKind.SCALAR.value,
        }
        conflicts = [key for key, expected_value in expected.items() if existing[key] != expected_value]
        if conflicts:
            raise ValueError(
                f"GLEIF claim series {series_id} conflicts on: {', '.join(conflicts)}"
            )
        return series_id, False
    return series_id, add_claim_series(
        connection,
        ClaimSeries(
            series_id,
            entity_id,
            stable_key,
            predicate,
            ValueKind.SCALAR,
            created_at,
        ),
    )


def _language_tag(value: str | None) -> str | None:
    if value is None or _LANGUAGE_TAG_RE.fullmatch(value) is None:
        return None
    return value


def _semantic_dimension(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(_canonical_json(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def _excerpt(lei: str, path: str, value: object) -> str:
    return _canonical_json({"lei": lei, "path": path, "value": value})


def _claim_specs(record: GLEIFLEIRecord, payload: Mapping[str, Any]) -> tuple[_ClaimSpec, ...]:
    entity_payload = payload["attributes"]["entity"]
    registration_payload = payload["attributes"]["registration"]
    common_notes = (
        "GLEIF Level 1 source statement about a legal entity. It does not establish "
        "facility ownership, operation, production status, or semiconductor capacity."
    )
    specs: list[_ClaimSpec] = []

    def add_text(
        predicate: str,
        dimension: str,
        value: str | None,
        locator: str,
        *,
        method: str,
        notes: str = common_notes,
    ) -> None:
        if value is None:
            return
        specs.append(
            _ClaimSpec(
                predicate,
                dimension,
                ScalarValue(ScalarType.TEXT, value),
                locator,
                _excerpt(record.lei, locator, value),
                method,
                notes,
            )
        )

    def add_timestamp(
        predicate: str,
        dimension: str,
        value: str | None,
        locator: str,
        *,
        method: str,
    ) -> None:
        if value is None:
            return
        normalized = _normalize_timestamp(value, predicate)
        specs.append(
            _ClaimSpec(
                predicate,
                dimension,
                ScalarValue(ScalarType.TIMESTAMP, normalized),
                locator,
                _excerpt(record.lei, locator, value),
                method,
                common_notes,
            )
        )

    def meaningful(value: object) -> bool:
        if value is None:
            return False
        if isinstance(value, Mapping):
            return any(meaningful(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(meaningful(item) for item in value)
        return value != ""

    def add_json(
        predicate: str,
        dimension: str,
        value: object,
        locator: str,
        *,
        method: str,
        notes: str = common_notes,
    ) -> None:
        if not meaningful(value):
            return
        canonical_value = _canonical_json(value)
        specs.append(
            _ClaimSpec(
                predicate,
                dimension,
                ScalarValue(ScalarType.TEXT, canonical_value),
                locator,
                _excerpt(record.lei, locator, value),
                method,
                notes,
            )
        )

    lei_locator = "/data/attributes/lei"
    specs.append(
        _ClaimSpec(
            "organization.identifier",
            "lei",
            ScalarValue(ScalarType.TEXT, record.lei),
            lei_locator,
            _excerpt(record.lei, lei_locator, record.lei),
            "gleif_level_1_lei_capture_v1",
            common_notes,
            identifier_metadata=("LEI", record.lei, record.jurisdiction),
        )
    )
    legal_locator = "/data/attributes/entity/legalName/name"
    specs.append(
        _ClaimSpec(
            "organization.name",
            "legal",
            ScalarValue(ScalarType.TEXT, record.legal_name.name),
            legal_locator,
            _excerpt(record.lei, legal_locator, record.legal_name.name),
            "gleif_level_1_legal_name_capture_v1",
            common_notes,
            name_metadata=(
                OrganizationNameType.LEGAL,
                _language_tag(record.legal_name.language),
            ),
        )
    )
    seen_name_dimensions = {"legal"}
    for prefix, names, metadata_type, path_name in (
        (
            "other",
            record.other_names,
            OrganizationNameType.OTHER,
            "otherNames",
        ),
        (
            "transliterated",
            record.transliterated_other_names,
            OrganizationNameType.TRANSLITERATED,
            "transliteratedOtherNames",
        ),
    ):
        for index, name in enumerate(names):
            dimension = _semantic_dimension(
                prefix, name.name, name.language, name.name_type
            )
            if dimension in seen_name_dimensions:
                continue
            seen_name_dimensions.add(dimension)
            locator = f"/data/attributes/entity/{path_name}/{index}/name"
            specs.append(
                _ClaimSpec(
                    "organization.name",
                    dimension,
                    ScalarValue(ScalarType.TEXT, name.name),
                    locator,
                    _excerpt(
                        record.lei,
                        locator,
                        {
                            "name": name.name,
                            "language": name.language,
                            "type": name.name_type,
                        },
                    ),
                    f"gleif_level_1_{prefix}_name_capture_v1",
                    common_notes,
                    name_metadata=(metadata_type, _language_tag(name.language)),
                )
            )

    add_text(
        "organization.jurisdiction",
        "jurisdiction",
        record.jurisdiction,
        "/data/attributes/entity/jurisdiction",
        method="gleif_level_1_jurisdiction_capture_v1",
    )
    add_text(
        "gleif.entity_status",
        "status",
        record.status,
        "/data/attributes/entity/status",
        method="gleif_level_1_entity_status_capture_v1",
        notes=(
            common_notes
            + " GLEIF entity status is not a facility lifecycle or production-status claim."
        ),
    )
    add_text(
        "gleif.entity_category",
        "category",
        record.category,
        "/data/attributes/entity/category",
        method="gleif_level_1_entity_category_capture_v1",
    )
    add_text(
        "gleif.entity_subcategory",
        "subcategory",
        record.sub_category,
        "/data/attributes/entity/subCategory",
        method="gleif_level_1_entity_subcategory_capture_v1",
    )
    add_text(
        "gleif.registration_entity_id",
        "registration_entity_id",
        record.registration_entity_id,
        "/data/attributes/entity/registeredAs",
        method="gleif_level_1_registered_as_capture_v1",
    )
    add_timestamp(
        "gleif.entity_creation_at",
        "creation_at",
        record.creation_date,
        "/data/attributes/entity/creationDate",
        method="gleif_level_1_entity_creation_capture_v1",
    )
    add_json(
        "gleif.legal_form",
        "legal_form",
        record.legal_form,
        "/data/attributes/entity/legalForm",
        method="gleif_level_1_legal_form_capture_v1",
    )
    add_json(
        "gleif.registration_authority",
        "registration_authority",
        entity_payload["registeredAt"],
        "/data/attributes/entity/registeredAt",
        method="gleif_level_1_registration_authority_capture_v1",
    )
    add_json(
        "gleif.validation_authority",
        "primary_validation_authority",
        registration_payload["validatedAt"],
        "/data/attributes/registration/validatedAt",
        method="gleif_level_1_validation_authority_capture_v1",
    )
    add_json(
        "gleif.expiration",
        "expiration",
        record.expiration,
        "/data/attributes/entity/expiration",
        method="gleif_level_1_expiration_capture_v1",
        notes=(
            common_notes
            + " Legal-entity expiration metadata is not evidence of facility closure."
        ),
    )
    add_json(
        "gleif.successor_entity",
        "successor_entity",
        record.successor_entity,
        "/data/attributes/entity/successorEntity",
        method="gleif_level_1_successor_entity_capture_v1",
        notes=(
            common_notes
            + " A legal-entity successor is not inferred to own or operate any facility."
        ),
    )
    seen_json_dimensions: set[tuple[str, str]] = set()
    for prefix, values, predicate, path_name, method in (
        (
            "successor",
            record.successor_entities,
            "gleif.successor_entity",
            "successorEntities",
            "gleif_level_1_successor_entity_capture_v1",
        ),
        (
            "event_group",
            record.event_groups,
            "gleif.legal_entity_event_group",
            "eventGroups",
            "gleif_level_1_event_group_capture_v1",
        ),
        (
            "validation_authority",
            record.registration.other_validation_authorities,
            "gleif.validation_authority",
            "otherValidationAuthorities",
            "gleif_level_1_validation_authority_capture_v1",
        ),
    ):
        for index, value in enumerate(values):
            dimension = _semantic_dimension(prefix, value)
            semantic_key = (predicate, dimension)
            if semantic_key in seen_json_dimensions:
                continue
            seen_json_dimensions.add(semantic_key)
            add_json(
                predicate,
                dimension,
                value,
                (
                    f"/data/attributes/entity/{path_name}/{index}"
                    if path_name in {"successorEntities", "eventGroups"}
                    else f"/data/attributes/registration/{path_name}/{index}"
                ),
                method=method,
            )
    for predicate, dimension, value, locator, method in (
        (
            "gleif.registration_status",
            "registration_status",
            record.registration.status,
            "/data/attributes/registration/status",
            "gleif_level_1_registration_status_capture_v1",
        ),
        (
            "gleif.managing_lou",
            "managing_lou",
            record.registration.managing_lou,
            "/data/attributes/registration/managingLou",
            "gleif_level_1_managing_lou_capture_v1",
        ),
        (
            "gleif.corroboration_level",
            "corroboration_level",
            record.registration.corroboration_level,
            "/data/attributes/registration/corroborationLevel",
            "gleif_level_1_corroboration_capture_v1",
        ),
        (
            "gleif.validated_as",
            "validated_as",
            record.registration.validated_as,
            "/data/attributes/registration/validatedAs",
            "gleif_level_1_validated_as_capture_v1",
        ),
    ):
        add_text(predicate, dimension, value, locator, method=method)
    for predicate, dimension, value, locator, method in (
        (
            "gleif.initial_registration_at",
            "initial_registration_at",
            record.registration.initial_registration_date,
            "/data/attributes/registration/initialRegistrationDate",
            "gleif_level_1_initial_registration_capture_v1",
        ),
        (
            "gleif.last_update_at",
            "last_update_at",
            record.registration.last_update_date,
            "/data/attributes/registration/lastUpdateDate",
            "gleif_level_1_last_update_capture_v1",
        ),
        (
            "gleif.next_renewal_at",
            "next_renewal_at",
            record.registration.next_renewal_date,
            "/data/attributes/registration/nextRenewalDate",
            "gleif_level_1_next_renewal_capture_v1",
        ),
    ):
        add_timestamp(predicate, dimension, value, locator, method=method)

    for label, key in (
        ("legal", "legalAddress"),
        ("headquarters", "headquartersAddress"),
    ):
        locator = f"/data/attributes/entity/{key}"
        address_value = _canonical_json(entity_payload[key])
        specs.append(
            _ClaimSpec(
                f"organization.{label}_address",
                f"{label}_address",
                ScalarValue(ScalarType.TEXT, address_value),
                locator,
                _excerpt(record.lei, locator, entity_payload[key]),
                f"gleif_level_1_{label}_address_capture_v1",
                common_notes,
            )
        )

    # Accessing the parsed payload here also proves that the canonical source record
    # retained all registration fields used above rather than a reconstructed subset.
    if not isinstance(registration_payload, Mapping):
        raise ValueError("GLEIF canonical registration payload must be an object")
    return tuple(sorted(specs, key=lambda item: (item.predicate, item.dimension)))


def _canonical_record(response: VerifiedGLEIFResponse) -> tuple[GLEIFLEIRecord, dict[str, Any]]:
    parsed = parse_lei_jsonapi_bytes(response.raw_bytes)
    if len(parsed.records) != 1 or parsed.records[0].lei != response.lei:
        raise ValueError(f"verified GLEIF response no longer matches {response.lei}")
    canonical = json.loads(canonical_lei_payload_bytes(parsed).decode("utf-8"))
    payload = canonical["data"][0]
    if not isinstance(payload, dict):
        raise ValueError("GLEIF canonical record must be an object")
    return parsed.records[0], payload


def _active_prior_claims(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    run_id: str,
    accepted_at: str,
) -> dict[str, sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT versions.*, series.subject_entity_id, series.predicate,
               series.stable_key AS series_stable_key
        FROM claim_versions AS versions
        JOIN ingestion_runs AS runs ON runs.id = versions.created_by_run_id
        JOIN claim_series AS series ON series.id = versions.series_id
        WHERE runs.source_id = ?
          AND versions.created_by_run_id != ?
          AND series.predicate != 'gleif.source_record_sha256'
          AND julianday(versions.recorded_at) < julianday(?)
          AND (
                versions.superseded_at IS NULL
                OR julianday(versions.superseded_at) >= julianday(?)
              )
          AND versions.valid_to IS NULL
        ORDER BY versions.series_id, versions.id
        """,
        (source_id, run_id, accepted_at, accepted_at),
    ).fetchall()
    result: dict[str, sqlite3.Row] = {}
    for row in rows:
        series_id = str(row["series_id"])
        if series_id in result:
            raise ValueError(f"GLEIF series {series_id} has multiple active claims")
        result[series_id] = row
    return result


def _stored_scalar(connection: sqlite3.Connection, claim_id: str) -> ScalarValue:
    row = connection.execute(
        "SELECT * FROM scalar_values WHERE claim_version_id = ?", (claim_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"GLEIF prior claim {claim_id} lacks a scalar value")
    scalar_type = ScalarType(row["scalar_type"])
    if scalar_type in {ScalarType.TEXT, ScalarType.DATE, ScalarType.TIMESTAMP}:
        value: str | float | int | bool = str(row["text_value"])
    elif scalar_type is ScalarType.NUMBER:
        value = float(row["number_value"])
    elif scalar_type is ScalarType.INTEGER:
        value = int(row["integer_value"])
    else:
        value = bool(row["boolean_value"])
    return ScalarValue(scalar_type, value, row["unit"])


def _prior_evidence(
    connection: sqlite3.Connection, claim_id: str
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
            (claim_id,),
        )
    )


def _copy_organization_metadata(
    connection: sqlite3.Connection, prior_claim_id: str, new_claim_id: str
) -> None:
    name = connection.execute(
        "SELECT * FROM organization_name_claim_metadata WHERE claim_version_id = ?",
        (prior_claim_id,),
    ).fetchone()
    if name is not None:
        add_organization_name_claim_metadata(
            connection,
            OrganizationNameClaimMetadata(
                new_claim_id,
                OrganizationNameType(name["name_type"]),
                language_tag=name["language_tag"],
                script_code=name["script_code"],
            ),
        )
    identifier = connection.execute(
        "SELECT * FROM organization_identifier_claim_metadata WHERE claim_version_id = ?",
        (prior_claim_id,),
    ).fetchone()
    if identifier is not None:
        add_organization_identifier_claim_metadata(
            connection,
            OrganizationIdentifierClaimMetadata(
                new_claim_id,
                str(identifier["scheme"]),
                str(identifier["normalized_value"]),
                jurisdiction=identifier["jurisdiction"],
            ),
        )


def _apply_spec_metadata(
    connection: sqlite3.Connection, claim_id: str, spec: _ClaimSpec
) -> None:
    if spec.name_metadata is not None:
        name_type, language = spec.name_metadata
        add_organization_name_claim_metadata(
            connection,
            OrganizationNameClaimMetadata(
                claim_id,
                name_type,
                language_tag=language,
            ),
        )
    if spec.identifier_metadata is not None:
        scheme, value, jurisdiction = spec.identifier_metadata
        add_organization_identifier_claim_metadata(
            connection,
            OrganizationIdentifierClaimMetadata(
                claim_id,
                scheme,
                value,
                jurisdiction=jurisdiction,
            ),
        )


def _close_prior_claim(
    connection: sqlite3.Connection,
    prior: sqlite3.Row,
    *,
    run_id: str,
    accepted_at: str,
    valid_to: str,
    context_document_id: str,
    context_record_id: str,
    reason: str,
    verify_only: bool,
) -> bool:
    if str(prior["valid_from"]) > valid_to:
        raise ValueError("GLEIF Golden Copy predates an active prior source claim")
    if verify_only:
        if prior["superseded_at"] != accepted_at:
            raise ValueError(
                f"GLEIF replay expected claim {prior['id']} superseded at {accepted_at}"
            )
    else:
        connection.execute(
            "UPDATE claim_versions SET superseded_at = ? WHERE id = ?",
            (accepted_at, prior["id"]),
        )
    if str(prior["valid_from"]) == valid_to:
        return False
    prior_value = _stored_scalar(connection, str(prior["id"]))
    evidence = (
        *_prior_evidence(connection, str(prior["id"])),
        EvidenceLink(
            context_document_id,
            role=EvidenceRole.CONTEXT,
            source_record_id=context_record_id,
            locator="/data;interval_closure",
            excerpt=_canonical_json(
                {
                    "closure_basis": reason,
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
            ClaimKind(str(prior["claim_kind"])),
            "gleif_level_1_interval_closure_v1",
            float(prior["confidence"]),
            valid_to=valid_to,
            created_by_run_id=run_id,
            notes=(
                "A later GLEIF Level 1 record changed or omitted this source field; "
                "the prior statement remains queryable before the closure date."
            ),
        ),
        prior_value,
        evidence=evidence,
    )
    if verify_only and created:
        raise ValueError(f"GLEIF replay was missing closure claim {closure_id}")
    _copy_organization_metadata(connection, str(prior["id"]), closure_id)
    return created


def _validate_review_bindings(
    snapshot: VerifiedGLEIFSnapshot, review: GLEIFReviewManifest
) -> None:
    if review.snapshot_manifest_sha256 != snapshot.manifest_sha256:
        raise ValueError("GLEIF review does not bind the verified snapshot manifest")
    if _clock(review.golden_copy_publish_date) != _clock(
        snapshot.golden_copy_publish_date
    ):
        raise ValueError("GLEIF review Golden Copy does not match the snapshot")
    review_leis = tuple(decision.lei for decision in review.decisions)
    if review_leis != snapshot.leis:
        raise ValueError("GLEIF review must contain exactly one decision per snapshot LEI")


def _validate_target_evidence(
    connection: sqlite3.Connection,
    decision: GLEIFReviewDecision,
    *,
    reviewed_at: str,
) -> tuple[str, ...]:
    target = connection.execute(
        "SELECT * FROM entities WHERE id = ?", (decision.target_entity_id,)
    ).fetchone()
    if target is None:
        raise ValueError(f"unknown GLEIF review target entity {decision.target_entity_id}")
    if (
        target["kind"] != EntityKind.ORGANIZATION.value
        or target["stable_key"] != decision.target_entity_stable_key
    ):
        raise ValueError(
            f"GLEIF review target identity conflicts for {decision.target_entity_id}"
        )
    producer_runs: set[str] = set()
    for claim_id in decision.target_evidence_claim_version_ids:
        row = connection.execute(
            """
            SELECT versions.created_by_run_id, versions.recorded_at,
                   versions.superseded_at, series.subject_entity_id,
                   runs.status, runs.completed_at,
                   EXISTS (
                       SELECT 1 FROM claim_evidence
                       WHERE claim_version_id = versions.id
                   ) AS source_backed
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            LEFT JOIN ingestion_runs AS runs ON runs.id = versions.created_by_run_id
            WHERE versions.id = ?
            """,
            (claim_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown GLEIF target evidence claim {claim_id}")
        if row["subject_entity_id"] != decision.target_entity_id:
            raise ValueError(
                f"GLEIF target evidence claim {claim_id} belongs to another entity"
            )
        if (
            row["source_backed"] != 1
            or row["created_by_run_id"] is None
            or row["status"] != IngestionStatus.SUCCEEDED.value
            or row["completed_at"] is None
        ):
            raise ValueError(
                f"GLEIF target evidence claim {claim_id} lacks succeeded source lineage"
            )
        if _clock(str(row["completed_at"])) > _clock(reviewed_at):
            raise ValueError(
                f"GLEIF target evidence claim {claim_id} was produced after the review"
            )
        if _clock(str(row["recorded_at"])) > _clock(reviewed_at) or (
            row["superseded_at"] is not None
            and _clock(str(row["superseded_at"])) <= _clock(reviewed_at)
        ):
            raise ValueError(
                f"GLEIF target evidence claim {claim_id} was not current when reviewed"
            )
        producer_runs.add(str(row["created_by_run_id"]))
    return tuple(sorted(producer_runs))


def _assert_chronology(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    run_id: str,
    accepted_at: str,
    retrieved_at: str,
    golden_copy_publish_date: str,
) -> None:
    later = connection.execute(
        """
        SELECT id FROM ingestion_runs
        WHERE source_id = ? AND id != ?
          AND julianday(started_at) >= julianday(?)
        LIMIT 1
        """,
        (source_id, run_id, accepted_at),
    ).fetchone()
    if later is not None:
        raise ValueError("GLEIF snapshots must be accepted in strict transaction order")
    later_retrieval = connection.execute(
        """
        SELECT documents.id
        FROM ingestion_runs AS runs
        JOIN source_documents AS documents ON documents.id = runs.input_document_id
        WHERE runs.source_id = ? AND runs.id != ?
          AND julianday(documents.retrieved_at) > julianday(?)
        LIMIT 1
        """,
        (source_id, run_id, retrieved_at),
    ).fetchone()
    if later_retrieval is not None:
        raise ValueError("GLEIF snapshots must be imported in retrieval order")
    current_gc = _clock(golden_copy_publish_date)
    for row in connection.execute(
        """
        SELECT id, started_at, completed_at, parameters_json
        FROM ingestion_runs
        WHERE source_id = ? AND id != ? AND status = 'succeeded'
        ORDER BY started_at, id
        """,
        (source_id, run_id),
    ):
        try:
            parameters = json.loads(row["parameters_json"])
            prior_gc = _clock(parameters["golden_copy_publish_date"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(
                f"prior GLEIF run {row['id']} has invalid Golden Copy metadata"
            ) from error
        if prior_gc > current_gc:
            raise ValueError("GLEIF Golden Copy publication must be non-decreasing")
        prior_cutoff_raw = parameters.get("knowledge_cutoff_at", row["completed_at"])
        try:
            prior_cutoff = _clock(str(prior_cutoff_raw))
        except ValueError as error:
            raise ValueError(
                f"prior GLEIF run {row['id']} has invalid knowledge cutoff metadata"
            ) from error
        if _clock(accepted_at) <= prior_cutoff:
            raise ValueError(
                "GLEIF accepted_at must be later than every prior GLEIF knowledge cutoff"
            )


def _verify_snapshot_identity(
    expected: VerifiedGLEIFSnapshot, actual: VerifiedGLEIFSnapshot
) -> None:
    fields = (
        "manifest_sha256",
        "manifest_size",
        "retrieved_at",
        "golden_copy_publish_date",
        "leis",
        "allowlist_sha256",
        "canonical_sha256",
        "attempts_used",
    )
    if any(getattr(expected, field) != getattr(actual, field) for field in fields):
        raise ValueError("GLEIF snapshot changed before database acceptance")
    if (
        expected.manifest_bytes != actual.manifest_bytes
        or expected.allowlist_bytes != actual.allowlist_bytes
        or expected.canonical_bytes != actual.canonical_bytes
        or expected.responses != actual.responses
    ):
        raise ValueError("GLEIF snapshot bytes changed before database acceptance")
    for response in actual.responses:
        if (
            len(response.raw_bytes) != response.size
            or hashlib.sha256(response.raw_bytes).hexdigest() != response.sha256
        ):
            raise ValueError("GLEIF verified response has inconsistent retained bytes")


def _verify_review_identity(
    expected: GLEIFReviewManifest, actual: GLEIFReviewManifest
) -> None:
    if (
        expected.format != actual.format
        or expected.reviewed_by != actual.reviewed_by
        or expected.reviewed_at != actual.reviewed_at
        or expected.snapshot_manifest_sha256 != actual.snapshot_manifest_sha256
        or expected.golden_copy_publish_date != actual.golden_copy_publish_date
        or expected.decisions != actual.decisions
        or expected.raw_sha256 != actual.raw_sha256
        or expected.raw_bytes != actual.raw_bytes
        or expected.canonical_sha256 != actual.canonical_sha256
        or expected.canonical_bytes != actual.canonical_bytes
    ):
        raise ValueError("GLEIF review changed before database acceptance")


def _verify_resolution_replay(
    connection: sqlite3.Connection,
    *,
    resolution_run: EntityResolutionRun,
    completed_at: str,
    run_inputs: Sequence[str],
    candidates: Sequence[EntityResolutionCandidate],
    decisions: Sequence[EntityResolutionDecision],
    expected_assignment_ids: Sequence[str],
) -> None:
    row = connection.execute(
        "SELECT * FROM entity_resolution_runs WHERE id = ?", (resolution_run.id,)
    ).fetchone()
    expected_parameters = _canonical_json(resolution_run.parameters)
    if row is None or any(
        row[key] != value
        for key, value in {
            "started_at": resolution_run.started_at,
            "completed_at": completed_at,
            "status": EntityResolutionStatus.SUCCEEDED.value,
            "resolver_version": resolution_run.resolver_version,
            "code_version": resolution_run.code_version,
            "parameters_json": expected_parameters,
            "error": None,
        }.items()
    ):
        raise ValueError("GLEIF resolution replay conflicts with its immutable run")
    actual_inputs = {
        str(item[0])
        for item in connection.execute(
            "SELECT ingestion_run_id FROM entity_resolution_run_inputs WHERE resolution_run_id = ?",
            (resolution_run.id,),
        )
    }
    if actual_inputs != set(run_inputs):
        raise ValueError("GLEIF resolution replay has different ingestion inputs")
    for candidate, decision in zip(candidates, decisions):
        candidate_row = connection.execute(
            "SELECT * FROM entity_resolution_candidates WHERE id = ?", (candidate.id,)
        ).fetchone()
        decision_row = connection.execute(
            "SELECT * FROM entity_resolution_decisions WHERE id = ?", (decision.id,)
        ).fetchone()
        if candidate_row is None or decision_row is None:
            raise ValueError("GLEIF resolution replay is missing candidate history")
        candidate_expected = {
            "resolution_run_id": candidate.resolution_run_id,
            "source_record_id": candidate.source_record_id,
            "observed_entity_id": candidate.observed_entity_id,
            "candidate_entity_id": candidate.candidate_entity_id,
            "features_json": _canonical_json(candidate.features),
            "score": float(candidate.score),
            "candidate_rank": candidate.candidate_rank,
            "created_at": candidate.created_at,
        }
        decision_expected = {
            "candidate_id": decision.candidate_id,
            "outcome": decision.outcome.value,
            "decided_at": decision.decided_at,
            "decided_by": decision.decided_by,
            "reason": decision.reason,
            "metadata_json": _canonical_json(decision.metadata),
        }
        if any(candidate_row[key] != value for key, value in candidate_expected.items()):
            raise ValueError("GLEIF resolution replay candidate conflicts")
        if any(decision_row[key] != value for key, value in decision_expected.items()):
            raise ValueError("GLEIF resolution replay decision conflicts")
    actual_assignment_ids = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT assignments.id
            FROM source_entity_assignments AS assignments
            JOIN entity_resolution_decisions AS decisions
              ON decisions.id = assignments.decision_id
            JOIN entity_resolution_candidates AS candidates
              ON candidates.id = decisions.candidate_id
            WHERE candidates.resolution_run_id = ?
            """,
            (resolution_run.id,),
        )
    }
    if actual_assignment_ids != set(expected_assignment_ids):
        raise ValueError("GLEIF resolution replay assignment history conflicts")


def import_gleif_level1(
    connection: sqlite3.Connection,
    snapshot: VerifiedGLEIFSnapshot,
    review: GLEIFReviewManifest,
    *,
    accepted_at: str,
    acceptance_timestamp_basis: str = "explicit_operator_supplied",
) -> GLEIFImportResult:
    """Import a fully reviewed exact-allowlist GLEIF Level 1 snapshot.

    GLEIF-native claims remain on GLEIF-native organization entities. A reviewed
    resolution decision may map each source record/entity pair to a pre-existing
    canonical organization, but it never copies source claims onto that target.
    The caller should keep an outer ``BEGIN IMMEDIATE`` transaction open through
    any additional semantic checks and commit-boundary file verification.
    """

    if not isinstance(snapshot, VerifiedGLEIFSnapshot):
        raise TypeError("snapshot must be a VerifiedGLEIFSnapshot")
    if not isinstance(review, GLEIFReviewManifest):
        raise TypeError("review must be a GLEIFReviewManifest")
    refreshed_snapshot = verify_gleif_snapshot(snapshot.root)
    _verify_snapshot_identity(snapshot, refreshed_snapshot)
    if review.path is None:
        refreshed_review = parse_gleif_review_bytes(review.raw_bytes)
    else:
        refreshed_review = read_gleif_review_file(review.path)
    _verify_review_identity(review, refreshed_review)
    snapshot = refreshed_snapshot
    review = refreshed_review
    accepted_at = _normalize_timestamp(accepted_at, "accepted_at")
    if acceptance_timestamp_basis not in ACCEPTANCE_TIMESTAMP_BASES:
        raise ValueError("GLEIF import has an invalid acceptance timestamp basis")
    _validate_review_bindings(snapshot, review)
    if _clock(snapshot.retrieved_at) > _clock(accepted_at):
        raise ValueError("GLEIF accepted_at must not predate snapshot retrieval")
    if _clock(review.reviewed_at) < _clock(snapshot.retrieved_at):
        raise ValueError("GLEIF review must not predate the verified snapshot retrieval")
    if _clock(review.reviewed_at) > _clock(accepted_at):
        raise ValueError("GLEIF accepted_at must not predate the completed review")
    if len(snapshot.responses) != len(snapshot.leis):
        raise ValueError("GLEIF verified snapshot response cardinality is invalid")

    family_id = stable_id("source-family", SOURCE_FAMILY_KEY)
    source_id = stable_id("source", SOURCE_KEY)
    canonical_document_id = stable_id(
        "source-document",
        source_id,
        GLEIF_API_BASE_URL,
        snapshot.retrieved_at,
        snapshot.canonical_sha256,
    )
    run_id = stable_id(
        "ingestion-run",
        IMPORTER_VERSION,
        snapshot.manifest_sha256,
        accepted_at,
    )
    resolution_run_id = stable_id(
        "entity-resolution-run",
        RESOLVER_VERSION,
        run_id,
        review.canonical_sha256,
    )
    ingestion_completed_at = _offset(accepted_at, 1)
    resolution_started_at = _offset(accepted_at, 2)
    candidate_created_at = _offset(accepted_at, 3)
    resolution_completed_at = _offset(accepted_at, 4)
    decision_recorded_at = _offset(accepted_at, 5)
    assignment_recorded_at = _offset(accepted_at, 6)
    valid_from = _as_of_date(snapshot.golden_copy_publish_date)

    existing_ingestion = connection.execute(
        "SELECT id FROM ingestion_runs WHERE id = ?", (run_id,)
    ).fetchone()
    replayed_existing_ingestion = existing_ingestion is not None
    existing_resolution = connection.execute(
        "SELECT id FROM entity_resolution_runs WHERE id = ?", (resolution_run_id,)
    ).fetchone()
    replayed_existing_resolution = existing_resolution is not None

    source_documents_created = 0
    source_records_created = 0
    entities_created = 0
    claim_series_created = 0
    claims_created = 0
    unchanged_claims_reused = 0
    prior_closed = 0
    candidates_created = 0
    decisions_created = 0
    assignments_created = 0
    assignments_superseded = 0
    raw_document_ids: list[str] = []
    source_record_ids: list[str] = []
    observed_entity_ids: list[str] = []
    assignment_ids: list[str] = []

    with _atomic_import(connection):
        target_input_runs: dict[str, tuple[str, ...]] = {}
        for decision in review.decisions:
            target_input_runs[decision.lei] = _validate_target_evidence(
                connection,
                decision,
                reviewed_at=review.reviewed_at,
            )
        family_created_at = _existing_created_at(
            connection, "source_families", family_id, accepted_at
        )
        add_source_family(
            connection,
            SourceFamily(
                family_id,
                SOURCE_FAMILY_KEY,
                "Global Legal Entity Identifier Foundation",
                family_created_at,
                description=(
                    "GLEIF legal-entity identity records used as organization evidence; "
                    "Level 2 relationship data is outside this source scope."
                ),
            ),
        )
        source_created_at = _existing_created_at(
            connection, "sources", source_id, accepted_at
        )
        add_source(
            connection,
            Source(
                source_id,
                family_id,
                SOURCE_KEY,
                "GLEIF LEI API Level 1",
                "Global Legal Entity Identifier Foundation",
                GLEIF_API_BASE_URL,
                source_created_at,
                license=GLEIF_LICENSE,
            ),
        )
        source_documents_created += int(
            _ensure_document(
                connection,
                SourceDocument(
                    canonical_document_id,
                    source_id,
                    GLEIF_API_BASE_URL,
                    "GLEIF Level 1 canonical exact-allowlist derivative",
                    snapshot.retrieved_at,
                    snapshot.canonical_sha256,
                    published_at=snapshot.golden_copy_publish_date,
                    media_type="application/json",
                    license=GLEIF_LICENSE,
                    metadata={
                        "artifact_kind": "deterministic_canonical_derivative",
                        "attribution": GLEIF_ATTRIBUTION,
                        "bytes": len(snapshot.canonical_bytes),
                        "canonical_format": "gleif-lei-jsonapi-level-1-v1",
                        "golden_copy_publish_date": snapshot.golden_copy_publish_date,
                        "license_url": GLEIF_LICENSE_URL,
                        "record_count": len(snapshot.leis),
                        "terms_url": GLEIF_TERMS_URL,
                    },
                ),
            )
        )
        if not replayed_existing_ingestion:
            _assert_chronology(
                connection,
                source_id=source_id,
                run_id=run_id,
                accepted_at=accepted_at,
                retrieved_at=snapshot.retrieved_at,
                golden_copy_publish_date=snapshot.golden_copy_publish_date,
            )
        raw_hashes = [
            {
                "lei": response.lei,
                "retrieved_at": response.retrieved_at,
                "sha256": response.sha256,
                "size": response.size,
            }
            for response in snapshot.responses
        ]
        ingestion_created = add_ingestion_run(
            connection,
            IngestionRun(
                run_id,
                source_id,
                accepted_at,
                status=IngestionStatus.SUCCEEDED,
                completed_at=ingestion_completed_at,
                code_version=IMPORTER_VERSION,
                input_document_id=canonical_document_id,
                parameters={
                    "accepted_at": accepted_at,
                    "acceptance_timestamp_basis": acceptance_timestamp_basis,
                    "allowlist_sha256": snapshot.allowlist_sha256,
                    "canonical_sha256": snapshot.canonical_sha256,
                    "coverage": "exact_declared_lei_allowlist_level_1_only",
                    "golden_copy_publish_date": snapshot.golden_copy_publish_date,
                    "knowledge_cutoff_at": assignment_recorded_at,
                    "leis": list(snapshot.leis),
                    "manifest_sha256": snapshot.manifest_sha256,
                    "raw_responses": raw_hashes,
                    "relationships_included": False,
                    "rights": {
                        "attribution": GLEIF_ATTRIBUTION,
                        "license": GLEIF_LICENSE,
                        "license_url": GLEIF_LICENSE_URL,
                        "terms_url": GLEIF_TERMS_URL,
                    },
                    "source_retrieved_at": snapshot.retrieved_at,
                },
            ),
        )
        if replayed_existing_ingestion and ingestion_created:
            raise ValueError("GLEIF replay was missing its ingestion run")

        prior_claims = _active_prior_claims(
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

        record_by_lei: dict[str, str] = {}
        entity_by_lei: dict[str, str] = {}

        for response in snapshot.responses:
            record, record_payload = _canonical_record(response)
            raw_document_id = stable_id(
                "source-document",
                source_id,
                response.url,
                response.retrieved_at,
                response.sha256,
            )
            raw_document_ids.append(raw_document_id)
            source_documents_created += int(
                _ensure_document(
                    connection,
                    SourceDocument(
                        raw_document_id,
                        source_id,
                        response.url,
                        f"GLEIF Level 1 API response for {response.lei}",
                        response.retrieved_at,
                        response.sha256,
                        published_at=response.golden_copy_publish_date,
                        media_type=response.content_type,
                        license=GLEIF_LICENSE,
                        metadata={
                            "artifact_kind": "upstream_official_api_response",
                            "attribution": GLEIF_ATTRIBUTION,
                            "bytes": response.size,
                            "canonical_record_sha256": source_record_payload_sha256(
                                record_payload
                            ),
                            "gleif_lei": response.lei,
                            "golden_copy_publish_date": response.golden_copy_publish_date,
                            "license_url": GLEIF_LICENSE_URL,
                            "terms_url": GLEIF_TERMS_URL,
                        },
                    ),
                )
            )
            add_ingestion_run_document(
                connection,
                IngestionRunDocument(run_id, raw_document_id, f"raw_response:{response.lei}"),
            )
            source_record_id = stable_id(
                "source-record", run_id, f"gleif:lei:{response.lei}"
            )
            source_record_ids.append(source_record_id)
            record_by_lei[response.lei] = source_record_id
            source_records_created += int(
                add_source_record(
                    connection,
                    SourceRecord(
                        source_record_id,
                        run_id,
                        raw_document_id,
                        f"gleif:lei:{response.lei}",
                        response.retrieved_at,
                        source_record_payload_sha256(record_payload),
                        payload=record_payload,
                    ),
                )
            )
            entity_key = f"gleif:lei:{response.lei}"
            observed_entity_id = stable_id("entity", entity_key)
            observed_entity_ids.append(observed_entity_id)
            entity_by_lei[response.lei] = observed_entity_id
            entities_created += int(
                _ensure_observed_entity(
                    connection,
                    entity_id=observed_entity_id,
                    stable_key=entity_key,
                    display_name=record.legal_name.name,
                    created_at=accepted_at,
                    run_id=run_id,
                )
            )
            seen_series: set[str] = set()
            observation_series_id, observation_series_created = _ensure_series(
                connection,
                entity_id=observed_entity_id,
                entity_key=entity_key,
                predicate="gleif.source_record_sha256",
                dimension=f"observation:{source_record_id}",
                created_at=accepted_at,
            )
            claim_series_created += int(observation_series_created)
            if replayed_existing_ingestion and observation_series_created:
                raise ValueError(
                    f"GLEIF replay was missing observation series {observation_series_id}"
                )
            observation_claim_id = stable_id(
                "claim-version",
                IMPORTER_VERSION,
                observation_series_id,
                source_record_id,
                valid_from,
            )
            observation_created = insert_claim(
                connection,
                ClaimVersion(
                    observation_claim_id,
                    observation_series_id,
                    valid_from,
                    accepted_at,
                    ClaimKind.SOURCE_STATEMENT,
                    "gleif_level_1_source_record_observation_v1",
                    1.0,
                    created_by_run_id=run_id,
                    notes=(
                        "Immutable hash anchor for this exact GLEIF source observation; "
                        "it carries candidate lineage and is not a legal-entity field."
                    ),
                ),
                ScalarValue(ScalarType.TEXT, response.sha256),
                evidence=(
                    EvidenceLink(
                        raw_document_id,
                        source_record_id=source_record_id,
                        locator="/data",
                        excerpt=_canonical_json(
                            {
                                "lei": response.lei,
                                "raw_response_sha256": response.sha256,
                            }
                        ),
                    ),
                ),
            )
            if replayed_existing_ingestion and observation_created:
                raise ValueError(
                    f"GLEIF replay was missing observation claim {observation_claim_id}"
                )
            claims_created += int(observation_created)
            for spec in _claim_specs(record, record_payload):
                series_id, series_created = _ensure_series(
                    connection,
                    entity_id=observed_entity_id,
                    entity_key=entity_key,
                    predicate=spec.predicate,
                    dimension=spec.dimension,
                    created_at=accepted_at,
                )
                claim_series_created += int(series_created)
                if replayed_existing_ingestion and series_created:
                    raise ValueError(f"GLEIF replay was missing claim series {series_id}")
                seen_series.add(series_id)
                prior = prior_claims.get(series_id)
                if (
                    prior is not None
                    and prior["value_sha256"] == value_sha256(spec.value)
                    and prior["claim_kind"] == ClaimKind.SOURCE_STATEMENT.value
                    and prior["method"] == spec.method
                    and float(prior["confidence"]) == 1.0
                    and prior["notes"] == spec.notes
                ):
                    _apply_spec_metadata(connection, str(prior["id"]), spec)
                    unchanged_claims_reused += 1
                    continue
                if prior is not None:
                    claims_created += int(
                        _close_prior_claim(
                            connection,
                            prior,
                            run_id=run_id,
                            accepted_at=accepted_at,
                            valid_to=valid_from,
                            context_document_id=raw_document_id,
                            context_record_id=source_record_id,
                            reason="later_same_lei_changed_value",
                            verify_only=replayed_existing_ingestion,
                        )
                    )
                    prior_closed += int(not replayed_existing_ingestion)
                claim_id = stable_id(
                    "claim-version",
                    IMPORTER_VERSION,
                    series_id,
                    source_record_id,
                    valid_from,
                )
                created = insert_claim(
                    connection,
                    ClaimVersion(
                        claim_id,
                        series_id,
                        valid_from,
                        accepted_at,
                        ClaimKind.SOURCE_STATEMENT,
                        spec.method,
                        1.0,
                        created_by_run_id=run_id,
                        notes=spec.notes,
                    ),
                    spec.value,
                    evidence=(
                        EvidenceLink(
                            raw_document_id,
                            source_record_id=source_record_id,
                            locator=spec.locator,
                            excerpt=spec.excerpt,
                        ),
                    ),
                )
                if replayed_existing_ingestion and created:
                    raise ValueError(f"GLEIF replay was missing claim {claim_id}")
                claims_created += int(created)
                _apply_spec_metadata(connection, claim_id, spec)

            for series_id, prior in prior_by_entity.get(observed_entity_id, {}).items():
                if series_id in seen_series:
                    continue
                claims_created += int(
                    _close_prior_claim(
                        connection,
                        prior,
                        run_id=run_id,
                        accepted_at=accepted_at,
                        valid_to=valid_from,
                        context_document_id=raw_document_id,
                        context_record_id=source_record_id,
                        reason="later_same_lei_field_absence",
                        verify_only=replayed_existing_ingestion,
                    )
                )
                prior_closed += int(not replayed_existing_ingestion)

        all_resolution_inputs = {run_id}
        for producer_runs in target_input_runs.values():
            all_resolution_inputs.update(producer_runs)
        resolution_parameters = {
            "accepted_at": accepted_at,
            "candidate_count": len(review.decisions),
            "golden_copy_publish_date": snapshot.golden_copy_publish_date,
            "manifest_sha256": snapshot.manifest_sha256,
            "review_canonical_sha256": review.canonical_sha256,
            "review_raw_sha256": review.raw_sha256,
            "reviewed_at": review.reviewed_at,
            "reviewed_by": review.reviewed_by,
            "target_evidence_claim_version_ids": sorted(
                {
                    claim_id
                    for decision in review.decisions
                    for claim_id in decision.target_evidence_claim_version_ids
                }
            ),
        }
        resolution_run = EntityResolutionRun(
            resolution_run_id,
            resolution_started_at,
            RESOLVER_VERSION,
            code_version=IMPORTER_VERSION,
            parameters=resolution_parameters,
        )
        candidate_models: list[EntityResolutionCandidate] = []
        decision_models: list[EntityResolutionDecision] = []
        expected_assignment_ids: list[str] = []
        for reviewed in review.decisions:
            candidate_id = stable_id(
                "entity-resolution-candidate",
                resolution_run_id,
                record_by_lei[reviewed.lei],
                reviewed.target_entity_id,
            )
            candidate_models.append(
                EntityResolutionCandidate(
                    candidate_id,
                    resolution_run_id,
                    record_by_lei[reviewed.lei],
                    entity_by_lei[reviewed.lei],
                    reviewed.target_entity_id,
                    reviewed.score,
                    reviewed.candidate_rank,
                    candidate_created_at,
                    features={
                        "lei": reviewed.lei,
                        "review_canonical_sha256": review.canonical_sha256,
                        "target_entity_stable_key": reviewed.target_entity_stable_key,
                        "target_evidence_claim_version_ids": list(
                            reviewed.target_evidence_claim_version_ids
                        ),
                    },
                )
            )
            decision_id = stable_id(
                "entity-resolution-decision", candidate_id, review.canonical_sha256
            )
            decision_models.append(
                EntityResolutionDecision(
                    decision_id,
                    candidate_id,
                    ResolutionDecisionOutcome(reviewed.outcome),
                    decision_recorded_at,
                    review.reviewed_by,
                    reviewed.reason,
                    metadata={
                        "assignment_valid_from": reviewed.assignment_valid_from,
                        "review_canonical_sha256": review.canonical_sha256,
                        "review_raw_sha256": review.raw_sha256,
                        "reviewed_at": review.reviewed_at,
                    },
                )
            )
            if reviewed.outcome == ResolutionDecisionOutcome.MATCH.value:
                expected_assignment_ids.append(
                    stable_id(
                        "source-entity-assignment",
                        decision_id,
                        reviewed.assignment_valid_from,
                        assignment_recorded_at,
                    )
                )

        if replayed_existing_resolution:
            _verify_resolution_replay(
                connection,
                resolution_run=resolution_run,
                completed_at=resolution_completed_at,
                run_inputs=tuple(sorted(all_resolution_inputs)),
                candidates=candidate_models,
                decisions=decision_models,
                expected_assignment_ids=expected_assignment_ids,
            )
            assignment_ids.extend(expected_assignment_ids)
        else:
            add_entity_resolution_run(connection, resolution_run)
            for input_run_id in sorted(all_resolution_inputs):
                add_entity_resolution_run_input(
                    connection,
                    EntityResolutionRunInput(resolution_run_id, input_run_id),
                )
            for candidate in candidate_models:
                candidates_created += int(
                    add_entity_resolution_candidate(connection, candidate)
                )
            finalize_entity_resolution_run(
                connection,
                resolution_run_id,
                status=EntityResolutionStatus.SUCCEEDED,
                completed_at=resolution_completed_at,
            )
            for reviewed, decision_model in zip(review.decisions, decision_models):
                decisions_created += int(
                    add_entity_resolution_decision(connection, decision_model)
                )
                current_assignments = connection.execute(
                    """
                    SELECT * FROM source_entity_assignments
                    WHERE observed_entity_id = ?
                      AND valid_to IS NULL
                      AND superseded_at IS NULL
                    ORDER BY recorded_at, id
                    """,
                    (entity_by_lei[reviewed.lei],),
                ).fetchall()
                for current in current_assignments:
                    supersede_source_entity_assignment(
                        connection, str(current["id"]), assignment_recorded_at
                    )
                    assignments_superseded += 1
                if reviewed.outcome != ResolutionDecisionOutcome.MATCH.value:
                    continue
                assignment_id = stable_id(
                    "source-entity-assignment",
                    decision_model.id,
                    reviewed.assignment_valid_from,
                    assignment_recorded_at,
                )
                assignment_ids.append(assignment_id)
                assignments_created += int(
                    add_source_entity_assignment(
                        connection,
                        SourceEntityAssignment(
                            assignment_id,
                            record_by_lei[reviewed.lei],
                            entity_by_lei[reviewed.lei],
                            reviewed.target_entity_id,
                            decision_model.id,
                            str(reviewed.assignment_valid_from),
                            assignment_recorded_at,
                        ),
                    )
                )

        validation_errors = validate_database(connection)
        if validation_errors:
            raise RuntimeError(
                "GLEIF import failed claim-store validation: "
                + "; ".join(validation_errors)
            )

        refreshed_snapshot = verify_gleif_snapshot(snapshot.root)
        _verify_snapshot_identity(snapshot, refreshed_snapshot)
        if review.path is None:
            refreshed_review = parse_gleif_review_bytes(review.raw_bytes)
        else:
            refreshed_review = read_gleif_review_file(review.path)
        _verify_review_identity(review, refreshed_review)

    return GLEIFImportResult(
        source_family_id=family_id,
        source_id=source_id,
        canonical_document_id=canonical_document_id,
        raw_document_ids=tuple(raw_document_ids),
        ingestion_run_id=run_id,
        resolution_run_id=resolution_run_id,
        accepted_at=accepted_at,
        knowledge_cutoff_at=assignment_recorded_at,
        acceptance_timestamp_basis=acceptance_timestamp_basis,
        replayed_existing_ingestion=replayed_existing_ingestion,
        replayed_existing_resolution=replayed_existing_resolution,
        records_imported=len(snapshot.responses),
        source_documents_created=source_documents_created,
        source_records_created=source_records_created,
        entities_created=entities_created,
        claim_series_created=claim_series_created,
        claims_created=claims_created,
        unchanged_claims_reused=unchanged_claims_reused,
        prior_open_claims_closed_or_corrected=prior_closed,
        resolution_candidates_created=candidates_created,
        resolution_decisions_created=decisions_created,
        assignments_created=assignments_created,
        assignments_superseded=assignments_superseded,
        observed_entity_ids=tuple(observed_entity_ids),
        source_record_ids=tuple(source_record_ids),
        assignment_ids=tuple(assignment_ids),
    )


__all__ = [
    "ACCEPTANCE_TIMESTAMP_BASES",
    "GLEIFImportResult",
    "IMPORTER_VERSION",
    "RESOLVER_VERSION",
    "SOURCE_FAMILY_KEY",
    "SOURCE_KEY",
    "import_gleif_level1",
]
