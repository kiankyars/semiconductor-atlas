"""Import exact-code EPA FRS facility candidates into the claim store."""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import urlparse

from .adapters.epa_frs import (
    FRS_ARCHIVE_MEMBER,
    FRS_ATTRIBUTION,
    FRS_COVERAGE,
    FRS_DATA_AS_OF_BASIS,
    FRS_DATA_AS_OF_SOURCE_URL,
    FRS_DOCUMENTATION_MEMBER,
    FRS_FILTER_VERSION,
    FRS_LANDING_PAGE_URL,
    FRS_LICENSE,
    FRS_LICENSE_URL,
    FRS_NAICS_CODES,
    FRS_OFFICIAL_ARCHIVE_URL,
    FRS_RETRIEVAL_TIMESTAMP_BASES,
    FRS_SIC_CODES,
    FRSCandidate,
    parse_candidate_jsonl_bytes,
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
    EvidenceRole,
    EvidenceLink,
    IngestionRun,
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
    add_source,
    add_source_document,
    add_source_family,
    add_source_record,
    insert_claim,
    stable_id,
    validate_database,
    value_sha256,
)


IMPORTER_VERSION = "epa-frs-candidate-import-v2"
SOURCE_FAMILY_KEY = "epa-frs"
SOURCE_KEY = f"epa-frs-national-single:{FRS_FILTER_VERSION}"
SOURCE_SCOPE = "epa_frs_semiconductor_candidates"
ACCEPTANCE_TIMESTAMP_BASES = frozenset(
    {
        "explicit_operator_supplied",
        "process_clock_after_snapshot_verification",
    }
)
_SAVEPOINTS = itertools.count()

# These fields are useful, bounded source statements. Free-form LOCATION_DESCRIPTION,
# federal/tribal metadata, and other unused cells remain in the non-public source record
# payload but are not promoted into release-facing claims.
_TEXT_FIELD_PREDICATES = (
    ("PRIMARY_NAME", "name"),
    ("LOCATION_ADDRESS", "address.street"),
    ("SUPPLEMENTAL_LOCATION", "address.supplemental"),
    ("CITY_NAME", "address.city"),
    ("COUNTY_NAME", "address.county"),
    ("FIPS_CODE", "address.fips_code"),
    ("STATE_CODE", "address.state_code"),
    ("STATE_NAME", "address.state_name"),
    ("COUNTRY_NAME", "address.country_name"),
    ("POSTAL_CODE", "address.postal_code"),
    ("EPA_REGION_CODE", "frs.epa_region_code"),
    ("SITE_TYPE_NAME", "frs.site_type"),
    ("CREATE_DATE", "frs.create_date_raw"),
    ("UPDATE_DATE", "frs.update_date_raw"),
    ("PGM_SYS_ACRNMS", "frs.program_system_acronyms_raw"),
    ("INTEREST_TYPES", "frs.interest_types_raw"),
    ("LATITUDE83", "frs.latitude83_nad83_raw"),
    ("LONGITUDE83", "frs.longitude83_nad83_raw"),
    ("CONVEYOR", "frs.coordinate_conveyor_raw"),
    ("COLLECT_DESC", "frs.coordinate_collection_method_raw"),
    ("ACCURACY_VALUE", "frs.coordinate_accuracy_m_raw"),
    ("REF_POINT_DESC", "frs.coordinate_reference_point_raw"),
    ("HDATUM_DESC", "frs.coordinate_horizontal_datum_raw"),
    ("SOURCE_DESC", "frs.coordinate_source_raw"),
)


@dataclass(frozen=True, slots=True)
class EPAFRSImportResult:
    source_family_id: str
    source_id: str
    source_document_id: str
    ingestion_run_id: str
    accepted_at: str
    acceptance_timestamp_basis: str
    replayed_existing_run: bool
    candidates_imported: int
    source_records_created: int
    entities_created: int
    claim_series_created: int
    claims_created: int
    unchanged_claims_reused: int
    prior_open_claims_closed_or_corrected: int
    candidate_entities_no_longer_selected: int
    site_entity_ids: tuple[str, ...]
    source_record_ids: tuple[str, ...]


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


def _validate_date(value: str, field_name: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (AttributeError, ValueError) as error:
        raise ValueError(f"{field_name} must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must use YYYY-MM-DD")
    return value


def _completed_at(started_at: str) -> str:
    parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=1)).astimezone(UTC).isoformat().replace(
        "+00:00", "Z"
    )


def _required_string(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"EPA FRS scope metadata requires {key}")
    return value


def _required_integer(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"EPA FRS scope metadata requires a nonnegative {key}")
    return value


def _archive_metadata(
    scope_metadata: Mapping[str, Any], document_url: str
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if scope_metadata.get("filter_version") != FRS_FILTER_VERSION:
        raise ValueError("EPA FRS scope filter version does not match the importer")
    if scope_metadata.get("naics_codes") != list(FRS_NAICS_CODES):
        raise ValueError("EPA FRS scope NAICS codes do not match the importer")
    if scope_metadata.get("sic_codes") != list(FRS_SIC_CODES):
        raise ValueError("EPA FRS scope SIC codes do not match the importer")
    if scope_metadata.get("documentation_member") != FRS_DOCUMENTATION_MEMBER:
        raise ValueError("EPA FRS scope identifies an unexpected documentation member")
    archive = scope_metadata.get("upstream_archive")
    csv_metadata = scope_metadata.get("upstream_csv")
    if not isinstance(archive, Mapping) or not isinstance(csv_metadata, Mapping):
        raise ValueError("EPA FRS scope requires upstream archive and CSV metadata")
    archive_url = _required_string(archive, "url")
    if archive_url != document_url or archive_url != FRS_OFFICIAL_ARCHIVE_URL:
        raise ValueError("EPA FRS import requires the official national single-file URL")
    digest = _required_string(archive, "sha256")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("EPA FRS upstream archive SHA-256 is invalid")
    _required_integer(archive, "bytes")
    if _required_string(csv_metadata, "member") != FRS_ARCHIVE_MEMBER:
        raise ValueError("EPA FRS scope identifies an unexpected CSV member")
    csv_digest = _required_string(csv_metadata, "sha256")
    if len(csv_digest) != 64 or any(
        character not in "0123456789abcdef" for character in csv_digest
    ):
        raise ValueError("EPA FRS upstream CSV SHA-256 is invalid")
    _required_integer(csv_metadata, "bytes")
    return archive, csv_metadata


def _validate_rights(scope_metadata: Mapping[str, Any]) -> None:
    rights = scope_metadata.get("rights")
    if not isinstance(rights, Mapping):
        raise ValueError("EPA FRS scope requires a bounded rights decision")
    expected = {
        "decision": "pass_for_exact_public_archive",
        "access_level": "public",
        "no_warranty": True,
        "scope": "exact_public_national_single_archive",
    }
    if any(rights.get(key) != value for key, value in expected.items()):
        raise ValueError("EPA FRS scope rights decision is not approved for this archive")
    _validate_date(
        _required_string(rights, "reviewed_at"), "EPA FRS rights reviewed_at"
    )
    license_url = _required_string(rights, "license_url")
    if license_url != FRS_LICENSE_URL:
        raise ValueError("EPA FRS scope rights license_url does not match the approved license")
    parsed = urlparse(license_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("EPA FRS rights license_url must be absolute HTTP(S)")


def _validate_snapshot_provenance(
    scope_metadata: Mapping[str, Any],
    *,
    upstream_sha256: str,
) -> None:
    if scope_metadata.get("retrieval_timestamp_basis") not in (
        FRS_RETRIEVAL_TIMESTAMP_BASES
    ):
        raise ValueError("EPA FRS scope has an invalid retrieval timestamp basis")
    if scope_metadata.get("data_as_of_basis") != FRS_DATA_AS_OF_BASIS:
        raise ValueError("EPA FRS scope has an invalid data_as_of basis")
    if scope_metadata.get("data_as_of_source_url") != FRS_DATA_AS_OF_SOURCE_URL:
        raise ValueError("EPA FRS scope has an invalid data_as_of source URL")
    raw_retention = scope_metadata.get("raw_retention")
    if not isinstance(raw_retention, Mapping):
        raise ValueError("EPA FRS scope must declare raw archive retention status")
    retained = raw_retention.get("retained_in_snapshot")
    if retained is not True:
        raise ValueError("EPA FRS raw archive must be retained in the snapshot")
    locator = raw_retention.get("blob_locator")
    expected_locator = f"raw/sha256/{upstream_sha256}.zip"
    if locator != expected_locator:
        raise ValueError("EPA FRS retained raw archive requires a blob locator")
    if raw_retention.get("reason") is not None:
        raise ValueError("EPA FRS retained raw archive must not declare a retention gap")
    if raw_retention.get("upstream_sha256") != upstream_sha256:
        raise ValueError("EPA FRS raw retention digest disagrees with the upstream archive")
    archive = scope_metadata.get("upstream_archive")
    if not isinstance(archive, Mapping) or raw_retention.get("bytes") != archive.get(
        "bytes"
    ):
        raise ValueError("EPA FRS raw retention byte count disagrees with the archive")


@contextmanager
def _atomic_import(connection: sqlite3.Connection) -> Iterator[None]:
    savepoint = f"epa_frs_import_{next(_SAVEPOINTS)}"
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


def _ensure_entity(
    connection: sqlite3.Connection,
    *,
    identifier: str,
    stable_key: str,
    created_at: str,
    run_id: str,
) -> bool:
    row = connection.execute("SELECT * FROM entities WHERE id = ?", (identifier,)).fetchone()
    if row is not None:
        if row["kind"] != EntityKind.SITE.value or row["stable_key"] != stable_key:
            raise ValueError(f"entity {identifier} conflicts with the EPA FRS identity")
        return False
    return add_entity(
        connection,
        Entity(
            identifier,
            EntityKind.SITE,
            stable_key,
            created_at,
            created_by_run_id=run_id,
        ),
    )


def _ensure_source_document(
    connection: sqlite3.Connection, document: SourceDocument
) -> bool:
    """Reuse an acquired byte object across importer metadata versions."""

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
    conflicts = [key for key, value in expected.items() if existing[key] != value]
    if conflicts:
        raise ValueError(
            f"source document {document.id} conflicts on byte-intrinsic fields: "
            + ", ".join(conflicts)
        )
    return False


def _ensure_claim_series(
    connection: sqlite3.Connection,
    *,
    subject_entity_id: str,
    subject_stable_key: str,
    predicate: str,
    value_kind: ValueKind,
    created_at: str,
    dimension: str = "",
) -> tuple[str, bool]:
    identifier = stable_id(
        "claim-series", subject_entity_id, predicate, value_kind.value, dimension
    )
    stable_key = f"{subject_stable_key}:claim:{predicate}"
    if dimension:
        stable_key = f"{stable_key}:{dimension}"
    row = connection.execute(
        "SELECT * FROM claim_series WHERE id = ?", (identifier,)
    ).fetchone()
    if row is not None:
        expected = {
            "subject_entity_id": subject_entity_id,
            "stable_key": stable_key,
            "predicate": predicate,
            "value_kind": value_kind.value,
        }
        conflicts = [key for key, value in expected.items() if row[key] != value]
        if conflicts:
            raise ValueError(
                f"claim series {identifier} conflicts on: {', '.join(conflicts)}"
            )
        return identifier, False
    return identifier, add_claim_series(
        connection,
        ClaimSeries(
            identifier,
            subject_entity_id,
            stable_key,
            predicate,
            value_kind,
            created_at,
        ),
    )


def _assert_chronological_run(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    run_id: str,
    accepted_at: str,
) -> None:
    later = connection.execute(
        """
        SELECT id, started_at
        FROM ingestion_runs
        WHERE source_id = ?
          AND id != ?
          AND julianday(started_at) >= julianday(?)
        ORDER BY started_at, id
        LIMIT 1
        """,
        (source_id, run_id, accepted_at),
    ).fetchone()
    if later is not None:
        raise ValueError(
            "EPA FRS snapshots must be imported in database-acceptance order; "
            f"run {later['id']} is not earlier than this snapshot"
        )


def _assert_chronological_retrieval(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    run_id: str,
    retrieved_at: str,
) -> None:
    later = connection.execute(
        """
        SELECT documents.id, documents.retrieved_at
        FROM ingestion_runs AS runs
        JOIN source_documents AS documents
          ON documents.id = runs.input_document_id
        WHERE runs.source_id = ?
          AND runs.id != ?
          AND julianday(documents.retrieved_at) > julianday(?)
        ORDER BY documents.retrieved_at, documents.id
        LIMIT 1
        """,
        (source_id, run_id, retrieved_at),
    ).fetchone()
    if later is not None:
        raise ValueError(
            "EPA FRS snapshots must be imported in source-retrieval order; "
            f"document {later['id']} was retrieved later than this snapshot"
        )


def _assert_non_decreasing_data_as_of(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    run_id: str,
    as_of_date: str,
) -> None:
    latest_prior_date: str | None = None
    latest_prior_run: str | None = None
    for row in connection.execute(
        """
        SELECT id, parameters_json
        FROM ingestion_runs
        WHERE source_id = ?
          AND id != ?
          AND status = 'succeeded'
        ORDER BY started_at, id
        """,
        (source_id, run_id),
    ):
        try:
            parameters = json.loads(row["parameters_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(
                f"prior EPA FRS run {row['id']} has invalid parameters metadata"
            ) from error
        if not isinstance(parameters, Mapping):
            raise ValueError(
                f"prior EPA FRS run {row['id']} has invalid parameters metadata"
            )
        prior_date = _validate_date(
            _required_string(parameters, "as_of_date"),
            f"prior EPA FRS run {row['id']} as_of_date",
        )
        if latest_prior_date is None or prior_date > latest_prior_date:
            latest_prior_date = prior_date
            latest_prior_run = str(row["id"])
    if latest_prior_date is not None and as_of_date < latest_prior_date:
        raise ValueError(
            "EPA FRS data_as_of must be non-decreasing across imports; "
            f"prior run {latest_prior_run} already reached {latest_prior_date}"
        )


def _active_prior_claims(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    run_id: str,
    accepted_at: str,
) -> tuple[
    dict[str, sqlite3.Row],
    dict[str, tuple[DependencyLink, ...]],
]:
    rows = connection.execute(
        """
        SELECT versions.id, versions.series_id, versions.value_kind,
               versions.value_sha256, versions.valid_from, versions.recorded_at,
               versions.superseded_at,
               versions.claim_kind, versions.method, versions.confidence,
               versions.notes, series.subject_entity_id, series.predicate,
               entities.stable_key
        FROM ingestion_runs AS runs
        JOIN claim_versions AS versions
          ON versions.created_by_run_id = runs.id
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
                f"EPA FRS series {row['series_id']} has multiple active open claims"
            )
        claims[str(row["series_id"])] = row
    dependencies: dict[str, list[DependencyLink]] = {
        str(row["id"]): [] for row in rows
    }
    for row in connection.execute(
        """
        SELECT dependencies.claim_version_id,
               dependencies.depends_on_claim_version_id,
               dependencies.dependency_kind
        FROM claim_dependencies AS dependencies
        JOIN claim_versions AS versions
          ON versions.id = dependencies.claim_version_id
        JOIN ingestion_runs AS runs
          ON runs.id = versions.created_by_run_id
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
    return claims, {
        claim_id: tuple(links) for claim_id, links in dependencies.items()
    }


def _stored_scalar_value(
    connection: sqlite3.Connection, claim_version_id: str
) -> ScalarValue:
    row = connection.execute(
        "SELECT * FROM scalar_values WHERE claim_version_id = ?",
        (claim_version_id,),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"EPA FRS claim {claim_version_id} lacks its expected scalar value"
        )
    scalar_type = ScalarType(row["scalar_type"])
    if scalar_type in {ScalarType.TEXT, ScalarType.DATE, ScalarType.TIMESTAMP}:
        value: str | float | int | bool = row["text_value"]
    elif scalar_type is ScalarType.NUMBER:
        value = float(row["number_value"])
    elif scalar_type is ScalarType.INTEGER:
        value = int(row["integer_value"])
    else:
        value = bool(row["boolean_value"])
    return ScalarValue(scalar_type, value, row["unit"])


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


def _claim_semantically_matches(
    row: sqlite3.Row,
    *,
    value: ClaimValue,
    claim_kind: ClaimKind,
    method: str,
    confidence: float,
    notes: str | None,
    dependencies: Sequence[DependencyLink],
    prior_dependencies: Mapping[str, Sequence[DependencyLink]],
) -> bool:
    if (
        row["value_sha256"] != value_sha256(value)
        or row["claim_kind"] != claim_kind.value
        or row["method"] != method
        or float(row["confidence"]) != float(confidence)
        or row["notes"] != notes
    ):
        return False
    expected_dependencies = {
        (dependency.depends_on_claim_version_id, dependency.kind.value)
        for dependency in dependencies
    }
    actual_dependencies = {
        (dependency.depends_on_claim_version_id, dependency.kind.value)
        for dependency in prior_dependencies.get(str(row["id"]), ())
    }
    return expected_dependencies == actual_dependencies


def _close_prior_claim(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    run_id: str,
    document_id: str,
    accepted_at: str,
    as_of_date: str,
    closure_ids: dict[str, str],
    closure_basis: str,
    prior_dependencies: Mapping[str, Sequence[DependencyLink]],
    verify_only: bool,
) -> None:
    older = connection.execute(
        "SELECT julianday(?) < julianday(?)",
        (row["recorded_at"], accepted_at),
    ).fetchone()[0]
    if older != 1:
        raise ValueError(
            "EPA FRS snapshot was not accepted after an active prior claim; "
            "out-of-order refresh is not allowed"
        )
    if row["valid_from"] > as_of_date:
        raise ValueError(
            "EPA FRS data_as_of precedes an active prior claim; "
            "out-of-order valid-time closure is not allowed"
        )
    if row["valid_from"] == as_of_date:
        if verify_only:
            if row["superseded_at"] != accepted_at:
                raise ValueError(
                    f"EPA FRS replay expected claim {row['id']} to be superseded "
                    "at the original acceptance time"
                )
            return
        connection.execute(
            "UPDATE claim_versions SET superseded_at = ? WHERE id = ?",
            (accepted_at, row["id"]),
        )
        return
    if row["value_kind"] != ValueKind.SCALAR.value:
        raise ValueError(
            f"EPA FRS claim {row['id']} has unexpected value kind {row['value_kind']}"
        )
    dependencies = tuple(
        DependencyLink(
            closure_ids.get(
                dependency.depends_on_claim_version_id,
                dependency.depends_on_claim_version_id,
            ),
            dependency.kind,
        )
        for dependency in prior_dependencies.get(str(row["id"]), ())
    )
    evidence = _prior_evidence(connection, row["id"])
    if ClaimKind(row["claim_kind"]) is not ClaimKind.DERIVED_ESTIMATE:
        registry_id = str(row["stable_key"]).removeprefix("epa:frs:")
        evidence = (
            *evidence,
            EvidenceLink(
                document_id,
                role=EvidenceRole.CONTEXT,
                locator=(
                    f"{FRS_ARCHIVE_MEMBER};data_as_of={as_of_date};"
                    f"REGISTRY_ID={registry_id};interval_closure"
                ),
                excerpt=_canonical_json(
                    {
                        "REGISTRY_ID": registry_id,
                        "closure_basis": closure_basis,
                        "valid_to": as_of_date,
                    }
                ),
            ),
        )
    closure_id = stable_id(
        "claim-version",
        IMPORTER_VERSION,
        "valid-time-closure",
        row["id"],
        run_id,
        as_of_date,
    )
    if verify_only and row["superseded_at"] != accepted_at:
        raise ValueError(
            f"EPA FRS replay expected claim {row['id']} to be superseded "
            "at the original acceptance time"
        )
    created = insert_claim(
        connection,
        ClaimVersion(
            closure_id,
            row["series_id"],
            row["valid_from"],
            accepted_at,
            ClaimKind(row["claim_kind"]),
            "epa_frs_snapshot_interval_closure_v2",
            float(row["confidence"]),
            valid_to=as_of_date,
            created_by_run_id=run_id,
            notes=(
                "The prior FRS source value remains valid for historical world-state "
                f"queries before {as_of_date}. This interval closure records only a "
                "later observation of the identical source/filter; absence, field change, "
                "or code removal is not facility closure."
            ),
        ),
        _stored_scalar_value(connection, row["id"]),
        evidence=evidence,
        dependencies=dependencies,
    )
    if verify_only and created:
        raise ValueError(
            f"EPA FRS replay was missing deterministic closure claim {closure_id}"
        )
    closure_ids[row["id"]] = closure_id


def _field_evidence(
    document_id: str,
    record_id: str,
    candidate: FRSCandidate,
    column: str,
    *,
    extracted_value: str | None = None,
) -> EvidenceLink:
    excerpt: dict[str, str] = {
        "REGISTRY_ID": candidate.registry_id,
        column: candidate.row[column],
    }
    if extracted_value is not None:
        excerpt["extracted_value"] = extracted_value
    locator = (
        f"{FRS_ARCHIVE_MEMBER};row={candidate.row_number};"
        f"REGISTRY_ID={candidate.registry_id};column={column}"
    )
    return EvidenceLink(
        document_id,
        source_record_id=record_id,
        locator=locator,
        excerpt=_canonical_json(excerpt),
    )


def import_epa_frs_candidates(
    connection: sqlite3.Connection,
    input_path: str | Path,
    retrieved_at: str,
    as_of_date: str,
    *,
    accepted_at: str,
    acceptance_timestamp_basis: str = "explicit_operator_supplied",
    document_url: str = FRS_OFFICIAL_ARCHIVE_URL,
    scope_metadata: Mapping[str, Any],
    snapshot_is_complete: bool = False,
) -> EPAFRSImportResult:
    """Import exact-code registry candidates without asserting facility operation.

    The filtered JSONL is a deterministic replay artifact. The source document in
    the database represents the exact upstream ZIP bytes identified by the scope
    metadata, while the filtered-artifact hash is retained in document/run metadata.
    """

    if not isinstance(snapshot_is_complete, bool):
        raise ValueError("snapshot_is_complete must be a boolean")
    if scope_metadata.get("complete") is not snapshot_is_complete:
        raise ValueError("EPA FRS scope complete flag does not match import mode")
    if snapshot_is_complete and scope_metadata.get("coverage") != FRS_COVERAGE:
        raise ValueError("complete EPA FRS import requires exact direct-code coverage")
    _validate_rights(scope_metadata)
    parsed_url = urlparse(document_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("document_url must be an absolute HTTP(S) URL")
    retrieved_at = _normalize_timestamp(retrieved_at, "retrieved_at")
    requested_accepted_at = _normalize_timestamp(accepted_at, "accepted_at")
    if acceptance_timestamp_basis not in ACCEPTANCE_TIMESTAMP_BASES:
        raise ValueError("EPA FRS import has an invalid acceptance timestamp basis")
    if datetime.fromisoformat(requested_accepted_at.replace("Z", "+00:00")) < (
        datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    ):
        raise ValueError("accepted_at must not be earlier than retrieved_at")
    as_of_date = _validate_date(as_of_date, "as_of_date")
    archive_metadata, csv_metadata = _archive_metadata(scope_metadata, document_url)
    data_as_of = _validate_date(
        _required_string(scope_metadata, "data_as_of"), "EPA FRS data_as_of"
    )
    if data_as_of != as_of_date:
        raise ValueError("EPA FRS as_of_date must match the snapshot data_as_of")
    if date.fromisoformat(data_as_of) > datetime.fromisoformat(
        retrieved_at.replace("Z", "+00:00")
    ).date():
        raise ValueError("EPA FRS data_as_of must not be later than retrieved_at")

    archive = Path(input_path)
    if archive.is_symlink() or not archive.is_file():
        raise ValueError(f"EPA FRS candidate input must be a regular file: {archive}")
    try:
        selected_raw = archive.read_bytes()
    except OSError as error:
        raise ValueError(f"EPA FRS candidate input is not readable: {archive}") from error
    selected_sha256 = hashlib.sha256(selected_raw).hexdigest()
    selected_bytes = len(selected_raw)
    candidates = parse_candidate_jsonl_bytes(selected_raw)
    declared_candidates = _required_integer(scope_metadata, "candidate_count")
    if declared_candidates != len(candidates):
        raise ValueError(
            "EPA FRS candidate count does not match the verified filtered artifact"
        )

    family_id = stable_id("source-family", SOURCE_FAMILY_KEY)
    source_id = stable_id("source", SOURCE_KEY)
    upstream_sha256 = str(archive_metadata["sha256"])
    _validate_snapshot_provenance(
        scope_metadata,
        upstream_sha256=upstream_sha256,
    )
    document_id = stable_id(
        "source-document", source_id, document_url, retrieved_at, upstream_sha256
    )
    run_id = stable_id(
        "ingestion-run",
        IMPORTER_VERSION,
        document_id,
        as_of_date,
        selected_sha256,
        str(snapshot_is_complete).casefold(),
    )
    existing_run = connection.execute(
        "SELECT started_at FROM ingestion_runs WHERE id = ?", (run_id,)
    ).fetchone()
    replayed_existing_run = existing_run is not None
    if existing_run is not None and existing_run["started_at"] != requested_accepted_at:
        raise ValueError(
            "EPA FRS replay accepted_at conflicts with the immutable original run"
        )
    accepted_at = requested_accepted_at

    records_created = 0
    entities_created = 0
    series_created = 0
    claims_created = 0
    unchanged_claims_reused = 0
    prior_open_claims_closed_or_corrected = 0
    site_entity_ids: list[str] = []
    source_record_ids: list[str] = []

    current_site_ids = {
        stable_id("entity", f"epa:frs:{candidate.registry_id}")
        for candidate in candidates
    }

    with _atomic_import(connection):
        family_created_at = _existing_created_at(
            connection, "source_families", family_id, accepted_at
        )
        add_source_family(
            connection,
            SourceFamily(
                family_id,
                SOURCE_FAMILY_KEY,
                "U.S. EPA Facility Registry Service",
                family_created_at,
                description=(
                    "Integrated government facility registry; exact industry-code "
                    "matches are discovery candidates, not verified operations."
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
                "EPA FRS National Single File semiconductor candidates",
                "U.S. Environmental Protection Agency",
                FRS_LANDING_PAGE_URL,
                source_created_at,
                license=FRS_LICENSE,
            ),
        )
        document_metadata = {
            "artifact_kind": "upstream_official_archive",
            "attribution": FRS_ATTRIBUTION,
            "data_as_of": data_as_of,
            "data_as_of_basis": scope_metadata.get("data_as_of_basis"),
            "data_as_of_source_url": scope_metadata.get("data_as_of_source_url"),
            "documentation_member": scope_metadata.get("documentation_member"),
            "license": FRS_LICENSE,
            "upstream_total_rows": _required_integer(
                scope_metadata, "upstream_total_rows"
            ),
            "upstream_archive": dict(archive_metadata),
            "upstream_csv": dict(csv_metadata),
            "retrieval_timestamp_basis": scope_metadata.get(
                "retrieval_timestamp_basis"
            ),
        }
        _ensure_source_document(
            connection,
            SourceDocument(
                document_id,
                source_id,
                document_url,
                "EPA FRS National Single File archive",
                retrieved_at,
                upstream_sha256,
                media_type="application/zip",
                license=FRS_LICENSE,
                metadata=document_metadata,
            ),
        )
        if not replayed_existing_run:
            _assert_chronological_retrieval(
                connection,
                source_id=source_id,
                run_id=run_id,
                retrieved_at=retrieved_at,
            )
            _assert_chronological_run(
                connection,
                source_id=source_id,
                run_id=run_id,
                accepted_at=accepted_at,
            )
            _assert_non_decreasing_data_as_of(
                connection,
                source_id=source_id,
                run_id=run_id,
                as_of_date=as_of_date,
            )
        prior_claims, prior_dependencies = _active_prior_claims(
            connection,
            source_id=source_id,
            run_id=run_id,
            accepted_at=accepted_at,
        )
        prior_site_ids = {
            str(row["subject_entity_id"]) for row in prior_claims.values()
        }
        prior_claims_by_entity: dict[str, list[tuple[str, sqlite3.Row]]] = {}
        for prior_series_id, prior in prior_claims.items():
            prior_claims_by_entity.setdefault(
                str(prior["subject_entity_id"]), []
            ).append((prior_series_id, prior))
        add_ingestion_run(
            connection,
            IngestionRun(
                run_id,
                source_id,
                accepted_at,
                status=IngestionStatus.SUCCEEDED,
                completed_at=_completed_at(accepted_at),
                code_version=IMPORTER_VERSION,
                input_document_id=document_id,
                parameters={
                    "as_of_date": as_of_date,
                    "accepted_at": accepted_at,
                    "acceptance_timestamp_basis": acceptance_timestamp_basis,
                    "candidate_count": len(candidates),
                    "complete": snapshot_is_complete,
                    "coordinate_policy": (
                        "Raw NAD83 coordinate scalars only; no GeoJSON geometry without an "
                        "explicit EPSG:4269-to-EPSG:4326 transform."
                    ),
                    "coverage": scope_metadata.get("coverage"),
                    "data_as_of_basis": scope_metadata.get("data_as_of_basis"),
                    "data_as_of_source_url": scope_metadata.get(
                        "data_as_of_source_url"
                    ),
                    "filter_version": FRS_FILTER_VERSION,
                    "naics_codes": list(scope_metadata.get("naics_codes") or ()),
                    "rights": dict(scope_metadata.get("rights") or {}),
                    "selected_artifact_bytes": selected_bytes,
                    "selected_artifact_kind": "deterministic_filtered_derivative",
                    "selected_artifact_sha256": selected_sha256,
                    "sic_codes": list(scope_metadata.get("sic_codes") or ()),
                    "snapshot_is_complete": snapshot_is_complete,
                    "source_snapshot": dict(
                        scope_metadata.get("source_snapshot") or {}
                    ),
                    "source_scope": SOURCE_SCOPE,
                    "source_retrieved_at": retrieved_at,
                    "raw_retention": dict(scope_metadata.get("raw_retention") or {}),
                    "retrieval_timestamp_basis": scope_metadata.get(
                        "retrieval_timestamp_basis"
                    ),
                    "upstream_archive_sha256": upstream_sha256,
                    "upstream_total_rows": _required_integer(
                        scope_metadata, "upstream_total_rows"
                    ),
                },
            ),
        )
        seen_series_ids: set[str] = set()
        closed_prior_series_ids: set[str] = set()
        closure_ids: dict[str, str] = {}

        def write_claim(
            *,
            subject_entity_id: str,
            subject_stable_key: str,
            predicate: str,
            value: ClaimValue,
            record_id: str,
            claim_kind: ClaimKind,
            method: str,
            confidence: float,
            evidence: Sequence[EvidenceLink] = (),
            dependencies: Sequence[DependencyLink] = (),
            dimension: str = "",
            notes: str | None = None,
        ) -> str:
            nonlocal series_created, claims_created
            nonlocal unchanged_claims_reused
            nonlocal prior_open_claims_closed_or_corrected
            series_id, created = _ensure_claim_series(
                connection,
                subject_entity_id=subject_entity_id,
                subject_stable_key=subject_stable_key,
                predicate=predicate,
                value_kind=value.kind,
                created_at=accepted_at,
                dimension=dimension,
            )
            series_created += int(created)
            if replayed_existing_run and created:
                raise ValueError(
                    f"EPA FRS replay was missing claim series {series_id}"
                )
            seen_series_ids.add(series_id)
            prior = prior_claims.get(series_id)
            if prior is not None and _claim_semantically_matches(
                prior,
                value=value,
                claim_kind=claim_kind,
                method=method,
                confidence=confidence,
                notes=notes,
                dependencies=dependencies,
                prior_dependencies=prior_dependencies,
            ):
                if (
                    replayed_existing_run
                    and prior["superseded_at"] is not None
                    and datetime.fromisoformat(
                        str(prior["superseded_at"]).replace("Z", "+00:00")
                    )
                    <= datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
                ):
                    raise ValueError(
                        f"EPA FRS replay found unchanged claim {prior['id']} "
                        "superseded by the target run"
                    )
                unchanged_claims_reused += 1
                return str(prior["id"])
            if prior is not None:
                _close_prior_claim(
                    connection,
                    prior,
                    run_id=run_id,
                    document_id=document_id,
                    accepted_at=accepted_at,
                    as_of_date=as_of_date,
                    closure_ids=closure_ids,
                    closure_basis="later_same_filter_changed_value",
                    prior_dependencies=prior_dependencies,
                    verify_only=replayed_existing_run,
                )
                closed_prior_series_ids.add(series_id)
                prior_open_claims_closed_or_corrected += int(
                    not replayed_existing_run
                )
            version_id = stable_id(
                "claim-version",
                IMPORTER_VERSION,
                series_id,
                record_id,
                as_of_date,
            )
            created = insert_claim(
                connection,
                ClaimVersion(
                    version_id,
                    series_id,
                    as_of_date,
                    accepted_at,
                    claim_kind,
                    method,
                    confidence,
                    created_by_run_id=run_id,
                    notes=notes,
                ),
                value,
                evidence=evidence,
                dependencies=dependencies,
            )
            if replayed_existing_run and created:
                raise ValueError(
                    f"EPA FRS replay was missing deterministic claim {version_id}"
                )
            claims_created += int(created)
            return version_id

        for candidate in candidates:
            record_payload = {
                "archive_member": candidate.archive_member,
                "row_number": candidate.row_number,
                "row": candidate.row,
            }
            record_sha256 = source_record_payload_sha256(record_payload)
            record_key = f"epa-frs:{candidate.registry_id}"
            record_id = stable_id("source-record", run_id, record_key, record_sha256)
            record_created = add_source_record(
                connection,
                SourceRecord(
                    record_id,
                    run_id,
                    document_id,
                    record_key,
                    retrieved_at,
                    record_sha256,
                    payload=record_payload,
                ),
            )
            if replayed_existing_run and record_created:
                raise ValueError(
                    f"EPA FRS replay was missing source record {record_id}"
                )
            records_created += int(record_created)
            source_record_ids.append(record_id)

            site_stable_key = f"epa:frs:{candidate.registry_id}"
            site_id = stable_id("entity", site_stable_key)
            entity_created = _ensure_entity(
                connection,
                identifier=site_id,
                stable_key=site_stable_key,
                created_at=accepted_at,
                run_id=run_id,
            )
            if replayed_existing_run and entity_created:
                raise ValueError(f"EPA FRS replay was missing entity {site_id}")
            entities_created += int(entity_created)
            site_entity_ids.append(site_id)

            write_claim(
                subject_entity_id=site_id,
                subject_stable_key=site_stable_key,
                predicate="frs.registry_id",
                value=ScalarValue(ScalarType.TEXT, candidate.registry_id),
                record_id=record_id,
                claim_kind=ClaimKind.SOURCE_STATEMENT,
                method="epa_frs_registry_id_capture",
                confidence=1.0,
                evidence=(
                    _field_evidence(
                        document_id, record_id, candidate, "REGISTRY_ID"
                    ),
                ),
                notes=(
                    "FRS-scoped registry identity only; it is not an Atlas cross-source "
                    "facility resolution."
                ),
            )

            for column, predicate in _TEXT_FIELD_PREDICATES:
                value = candidate.row[column].strip()
                if not value:
                    continue
                write_claim(
                    subject_entity_id=site_id,
                    subject_stable_key=site_stable_key,
                    predicate=predicate,
                    value=ScalarValue(ScalarType.TEXT, value),
                    record_id=record_id,
                    claim_kind=ClaimKind.SOURCE_STATEMENT,
                    method=f"epa_frs_{column.casefold()}_capture",
                    confidence=1.0,
                    evidence=(
                        _field_evidence(
                            document_id, record_id, candidate, column
                        ),
                    ),
                    notes=(
                        "Raw EPA FRS registry statement; it may be stale, duplicated in "
                        "upstream systems, or different from current real-world status."
                    ),
                )

            qualifying_claim_ids: list[str] = []
            for code in candidate.naics_codes:
                claim_id = write_claim(
                    subject_entity_id=site_id,
                    subject_stable_key=site_stable_key,
                    predicate="frs.naics_code",
                    value=ScalarValue(ScalarType.TEXT, code),
                    record_id=record_id,
                    claim_kind=ClaimKind.SOURCE_STATEMENT,
                    method="epa_frs_naics_exact_token_capture",
                    confidence=1.0,
                    evidence=(
                        _field_evidence(
                            document_id,
                            record_id,
                            candidate,
                            "NAICS_CODES",
                            extracted_value=code,
                        ),
                    ),
                    dimension=code,
                )
                if code in candidate.qualifying_naics_codes:
                    qualifying_claim_ids.append(claim_id)
            for code in candidate.sic_codes:
                claim_id = write_claim(
                    subject_entity_id=site_id,
                    subject_stable_key=site_stable_key,
                    predicate="frs.sic_code",
                    value=ScalarValue(ScalarType.TEXT, code),
                    record_id=record_id,
                    claim_kind=ClaimKind.SOURCE_STATEMENT,
                    method="epa_frs_sic_exact_token_capture",
                    confidence=1.0,
                    evidence=(
                        _field_evidence(
                            document_id,
                            record_id,
                            candidate,
                            "SIC_CODES",
                            extracted_value=code,
                        ),
                    ),
                    dimension=code,
                )
                if code in candidate.qualifying_sic_codes:
                    qualifying_claim_ids.append(claim_id)
            if snapshot_is_complete:
                for prior_series_id, prior in prior_claims_by_entity.get(site_id, ()):
                    if (
                        prior_series_id in seen_series_ids
                        or prior_series_id in closed_prior_series_ids
                        or prior["claim_kind"] == ClaimKind.DERIVED_ESTIMATE.value
                    ):
                        continue
                    _close_prior_claim(
                        connection,
                        prior,
                        run_id=run_id,
                        document_id=document_id,
                        accepted_at=accepted_at,
                        as_of_date=as_of_date,
                        closure_ids=closure_ids,
                        closure_basis="later_same_filter_seen_entity_field_absence",
                        prior_dependencies=prior_dependencies,
                        verify_only=replayed_existing_run,
                    )
                    closed_prior_series_ids.add(prior_series_id)
                    prior_open_claims_closed_or_corrected += int(
                        not replayed_existing_run
                    )
            else:
                for prior_series_id, prior in prior_claims_by_entity.get(site_id, ()):
                    if prior_series_id in seen_series_ids:
                        continue
                    prior_value = _stored_scalar_value(connection, prior["id"])
                    if (
                        prior["predicate"] == "frs.naics_code"
                        and prior_value.value in FRS_NAICS_CODES
                    ) or (
                        prior["predicate"] == "frs.sic_code"
                        and prior_value.value in FRS_SIC_CODES
                    ):
                        qualifying_claim_ids.append(str(prior["id"]))
            if not qualifying_claim_ids:
                raise ValueError(
                    f"EPA FRS candidate {candidate.registry_id} lacks a qualifying code claim"
                )
            write_claim(
                subject_entity_id=site_id,
                subject_stable_key=site_stable_key,
                predicate="candidate_classification",
                value=ScalarValue(
                    ScalarType.TEXT, "semiconductor_facility_candidate"
                ),
                record_id=record_id,
                claim_kind=ClaimKind.DERIVED_ESTIMATE,
                method=FRS_FILTER_VERSION,
                confidence=1.0,
                dependencies=tuple(
                    DependencyLink(claim_id)
                    for claim_id in sorted(set(qualifying_claim_ids))
                ),
                notes=(
                    "Discovery lead only. An FRS industry code may be stale, broad, or "
                    "misclassified and does not establish semiconductor operation, "
                    "lifecycle, facility subtype, ownership, or capacity. Confidence "
                    "applies only to deterministic filter membership, not the probability "
                    "of an operating semiconductor facility."
                ),
            )

        stale_prior = [
            (prior_series_id, prior)
            for prior_series_id, prior in prior_claims.items()
            if prior_series_id not in seen_series_ids
            and prior_series_id not in closed_prior_series_ids
            and snapshot_is_complete
        ]
        stale_prior.sort(
            key=lambda item: (
                item[1]["claim_kind"] == ClaimKind.DERIVED_ESTIMATE.value,
                item[0],
            )
        )
        for prior_series_id, prior in stale_prior:
            _close_prior_claim(
                connection,
                prior,
                run_id=run_id,
                document_id=document_id,
                accepted_at=accepted_at,
                as_of_date=as_of_date,
                closure_ids=closure_ids,
                closure_basis="later_complete_same_filter_nonselection",
                prior_dependencies=prior_dependencies,
                verify_only=replayed_existing_run,
            )
            closed_prior_series_ids.add(prior_series_id)
            prior_open_claims_closed_or_corrected += int(
                not replayed_existing_run
            )

        validation_errors = validate_database(connection)
        if validation_errors:
            raise RuntimeError(
                "EPA FRS import failed claim-store validation: "
                + "; ".join(validation_errors)
            )

    return EPAFRSImportResult(
        source_family_id=family_id,
        source_id=source_id,
        source_document_id=document_id,
        ingestion_run_id=run_id,
        accepted_at=accepted_at,
        acceptance_timestamp_basis=acceptance_timestamp_basis,
        replayed_existing_run=replayed_existing_run,
        candidates_imported=len(candidates),
        source_records_created=records_created,
        entities_created=entities_created,
        claim_series_created=series_created,
        claims_created=claims_created,
        unchanged_claims_reused=unchanged_claims_reused,
        prior_open_claims_closed_or_corrected=(
            prior_open_claims_closed_or_corrected
        ),
        candidate_entities_no_longer_selected=len(prior_site_ids - current_site_ids)
        if snapshot_is_complete
        else 0,
        site_entity_ids=tuple(site_entity_ids),
        source_record_ids=tuple(source_record_ids),
    )


__all__ = [
    "ACCEPTANCE_TIMESTAMP_BASES",
    "EPAFRSImportResult",
    "import_epa_frs_candidates",
]
