"""Import archived OpenStreetMap Overpass candidates into the claim store."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import urlparse

from .adapters.osm import OSM_ATTRIBUTION, OSM_LICENSE, OSMCandidate, parse_overpass
from .models import (
    CapabilityValue,
    ClaimKind,
    ClaimSeries,
    ClaimValue,
    ClaimVersion,
    DependencyLink,
    Entity,
    EntityKind,
    EvidenceLink,
    GeometryValue,
    IngestionRun,
    IngestionStatus,
    MilestoneStatus,
    MilestoneValue,
    RelationshipValue,
    ScalarType,
    ScalarValue,
    Source,
    SourceDocument,
    SourceFamily,
    SourceRecord,
    ValueKind,
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
)


IMPORTER_VERSION = "osm-overpass-import-v1"
OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
OSM_CANONICAL_URL = "https://www.openstreetmap.org/"
OSM_COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"
ODBL_LICENSE_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
_SAVEPOINTS = itertools.count()


@dataclass(frozen=True, slots=True)
class OSMImportResult:
    source_family_id: str
    source_id: str
    source_document_id: str
    ingestion_run_id: str
    elements_examined: int
    candidates_imported: int
    source_records_created: int
    entities_created: int
    claim_series_created: int
    claims_created: int
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


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _validate_inputs(
    retrieved_at: str,
    as_of_date: str,
    query: str | None,
    document_url: str,
) -> None:
    if query is not None and not isinstance(query, str):
        raise ValueError("query must be text or None")
    parsed_url = urlparse(document_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("document_url must be an absolute HTTP(S) URL")
    try:
        parsed_timestamp = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError("retrieved_at must be an ISO timestamp") from error
    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    try:
        parsed_date = date.fromisoformat(as_of_date)
    except (AttributeError, ValueError) as error:
        raise ValueError("as_of_date must use YYYY-MM-DD") from error
    if parsed_date.isoformat() != as_of_date:
        raise ValueError("as_of_date must use YYYY-MM-DD")


def _completed_at(started_at: str) -> str:
    parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    completed = parsed + timedelta(seconds=1)
    encoded = completed.isoformat()
    if started_at.endswith("Z"):
        return encoded.replace("+00:00", "Z")
    return encoded


@contextmanager
def _atomic_import(connection: sqlite3.Connection) -> Iterator[None]:
    savepoint = f"osm_import_{next(_SAVEPOINTS)}"
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
    kind: EntityKind,
    stable_key: str,
    created_at: str,
    run_id: str,
) -> bool:
    row = connection.execute("SELECT * FROM entities WHERE id = ?", (identifier,)).fetchone()
    if row is not None:
        if row["kind"] != kind.value or row["stable_key"] != stable_key:
            raise ValueError(f"entity {identifier} conflicts with the OSM identity")
        return False
    return add_entity(
        connection,
        Entity(
            identifier,
            kind,
            stable_key,
            created_at,
            created_by_run_id=run_id,
        ),
    )


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
    row = connection.execute("SELECT * FROM claim_series WHERE id = ?", (identifier,)).fetchone()
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


def _element_index(payload: Mapping[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    index: dict[tuple[str, int], dict[str, Any]] = {}
    for element in payload["elements"]:
        if not isinstance(element, dict):
            continue
        element_type = element.get("type")
        element_id = element.get("id")
        if element_type not in {"node", "way", "relation"} or not isinstance(element_id, int):
            continue
        key = (element_type, element_id)
        previous = index.get(key)
        if previous is not None and _canonical_json(previous) != _canonical_json(element):
            raise ValueError(f"OSM archive contains conflicting duplicate element {element_type}/{element_id}")
        index[key] = element
    return index


def _deduplicated_candidates(candidates: Sequence[OSMCandidate]) -> list[OSMCandidate]:
    result: list[OSMCandidate] = []
    seen: set[tuple[str, int]] = set()
    for candidate in candidates:
        key = (candidate.element_type, candidate.element_id)
        if key not in seen:
            result.append(candidate)
            seen.add(key)
    return result


def _address(tags: Mapping[str, str]) -> str | None:
    full = tags.get("addr:full", "").strip()
    if full:
        return full
    parts = [
        tags.get("addr:housenumber", "").strip(),
        tags.get("addr:street", "").strip(),
        tags.get("addr:city", "").strip(),
        tags.get("addr:state", "").strip(),
        tags.get("addr:postcode", "").strip(),
        tags.get("addr:country", "").strip(),
    ]
    values = [part for part in parts if part]
    return ", ".join(values) if values else None


def _operator_names(tags: Mapping[str, str]) -> tuple[str, ...]:
    names = []
    for value in tags.get("operator", "").split(";"):
        normalized = " ".join(value.split())
        if normalized and normalized not in names:
            names.append(normalized)
    return tuple(names)


def _operator_stable_key(name: str) -> str:
    label = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:48] or "unnamed"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"osm:organization:operator:{label}:{digest}"


def _has_explicit_construction_tag(tags: Mapping[str, str]) -> bool:
    normalized = {
        str(key).strip().lower().replace("-", "_").replace(" ", "_"):
        str(value).strip().lower().replace("-", "_").replace(" ", "_")
        for key, value in tags.items()
    }
    negative_values = {"", "0", "false", "no", "none", "not_construction"}
    return (
        any(
            (key == "construction" or key.startswith("construction:"))
            and value not in negative_values
            for key, value in normalized.items()
        )
        or normalized.get("building") == "construction"
    )


def import_osm_candidates(
    connection: sqlite3.Connection,
    input_path: str | Path,
    retrieved_at: str,
    as_of_date: str,
    query: str | None = None,
    document_url: str = OVERPASS_API_URL,
) -> OSMImportResult:
    """Import explicit Overpass candidates without promoting them to known facilities.

    OSM tags are recorded as source statements. Candidate classification, lifecycle,
    activity, and construction observations are derived claims with an explicit
    dependency on the corresponding raw-tag claim.
    """

    _validate_inputs(retrieved_at, as_of_date, query, document_url)
    retrieved_at = (
        datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )
    archive = Path(input_path)
    archive_bytes = archive.read_bytes()
    try:
        payload = json.loads(archive_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("OSM input must be UTF-8 Overpass JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("OSM input must be a JSON object")
    candidates = _deduplicated_candidates(parse_overpass(payload))
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise ValueError("OSM input must contain an elements array")
    element_index = _element_index(payload)
    content_sha256 = hashlib.sha256(archive_bytes).hexdigest()

    family_id = stable_id("source-family", "openstreetmap")
    source_id = stable_id("source", "openstreetmap", "overpass")
    query_identity = "query:none" if query is None else f"query:{query}"
    document_id = stable_id(
        "source-document", source_id, document_url, retrieved_at, content_sha256, query_identity
    )
    run_id = stable_id(
        "ingestion-run", IMPORTER_VERSION, document_id, as_of_date, query_identity
    )

    records_created = 0
    entities_created = 0
    series_created = 0
    claims_created = 0
    site_entity_ids: list[str] = []
    source_record_ids: list[str] = []
    named_operators: set[str] = set()

    with _atomic_import(connection):
        family_created_at = _existing_created_at(
            connection, "source_families", family_id, retrieved_at
        )
        add_source_family(
            connection,
            SourceFamily(
                family_id,
                "openstreetmap",
                "OpenStreetMap",
                family_created_at,
                description="Community-contributed geospatial source; candidate signals only.",
            ),
        )
        source_created_at = _existing_created_at(connection, "sources", source_id, retrieved_at)
        add_source(
            connection,
            Source(
                source_id,
                family_id,
                "openstreetmap:overpass",
                "OpenStreetMap Overpass API",
                "OpenStreetMap contributors",
                OSM_CANONICAL_URL,
                source_created_at,
                license=OSM_LICENSE,
            ),
        )
        osm3s_metadata = payload.get("osm3s")
        document_metadata: dict[str, Any] = {
            "attribution": OSM_ATTRIBUTION,
            "attribution_url": OSM_COPYRIGHT_URL,
            "license": OSM_LICENSE,
            "license_url": ODBL_LICENSE_URL,
            "query": query,
            "request_endpoint": document_url,
            "importer_version": IMPORTER_VERSION,
        }
        if isinstance(osm3s_metadata, dict):
            document_metadata["osm3s"] = osm3s_metadata
        add_source_document(
            connection,
            SourceDocument(
                document_id,
                source_id,
                document_url,
                "Archived OpenStreetMap Overpass semiconductor candidates",
                retrieved_at,
                content_sha256,
                media_type="application/json",
                license=OSM_LICENSE,
                metadata=document_metadata,
            ),
        )
        add_ingestion_run(
            connection,
            IngestionRun(
                run_id,
                source_id,
                retrieved_at,
                status=IngestionStatus.SUCCEEDED,
                completed_at=_completed_at(retrieved_at),
                code_version=IMPORTER_VERSION,
                input_document_id=document_id,
                parameters={
                    "as_of_date": as_of_date,
                    "candidate_count": len(candidates),
                    "query": query,
                },
            ),
        )

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
            series_id, created = _ensure_claim_series(
                connection,
                subject_entity_id=subject_entity_id,
                subject_stable_key=subject_stable_key,
                predicate=predicate,
                value_kind=value.kind,
                created_at=retrieved_at,
                dimension=dimension,
            )
            series_created += int(created)
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
                    retrieved_at,
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
            claims_created += int(created)
            return version_id

        for candidate in candidates:
            element_key = (candidate.element_type, candidate.element_id)
            raw_element = element_index.get(element_key)
            if raw_element is None:
                raise ValueError(
                    f"parsed OSM candidate has no archived element: {candidate.element_type}/{candidate.element_id}"
                )
            record_payload = {
                "attribution": OSM_ATTRIBUTION,
                "element": raw_element,
                "element_timestamp": candidate.published_at,
                "license": OSM_LICENSE,
                "source_url": candidate.source_url,
            }
            record_sha256 = _sha256_json(record_payload)
            record_key = f"osm:{candidate.element_type}/{candidate.element_id}"
            record_id = stable_id("source-record", run_id, record_key, record_sha256)
            records_created += int(
                add_source_record(
                    connection,
                    SourceRecord(
                        record_id,
                        run_id,
                        document_id,
                        record_key,
                        candidate.published_at or retrieved_at,
                        record_sha256,
                        payload=record_payload,
                    ),
                )
            )
            source_record_ids.append(record_id)

            site_stable_key = f"osm:site:{candidate.element_type}/{candidate.element_id}"
            site_id = stable_id("entity", site_stable_key)
            entities_created += int(
                _ensure_entity(
                    connection,
                    identifier=site_id,
                    kind=EntityKind.SITE,
                    stable_key=site_stable_key,
                    created_at=retrieved_at,
                    run_id=run_id,
                )
            )
            site_entity_ids.append(site_id)
            evidence = EvidenceLink(
                document_id,
                source_record_id=record_id,
                locator=candidate.source_url,
                excerpt=_canonical_json(candidate.tags),
            )

            raw_tags_version_id = write_claim(
                subject_entity_id=site_id,
                subject_stable_key=site_stable_key,
                predicate="raw_tags",
                value=ScalarValue(ScalarType.TEXT, _canonical_json(candidate.tags)),
                record_id=record_id,
                claim_kind=ClaimKind.SOURCE_STATEMENT,
                method="osm_raw_tag_capture",
                confidence=1.0,
                evidence=(evidence,),
            )
            raw_dependency = (DependencyLink(raw_tags_version_id),)

            if candidate.tags.get("name", "").strip():
                name_kind = ClaimKind.SOURCE_STATEMENT
                name_evidence: Sequence[EvidenceLink] = (evidence,)
                name_dependencies: Sequence[DependencyLink] = ()
                name_method = "osm_name_tag_extraction"
                name_confidence = 0.9
            else:
                name_kind = ClaimKind.DERIVED_ESTIMATE
                name_evidence = ()
                name_dependencies = raw_dependency
                name_method = "osm_generated_candidate_label"
                name_confidence = 0.2
            write_claim(
                subject_entity_id=site_id,
                subject_stable_key=site_stable_key,
                predicate="name",
                value=ScalarValue(ScalarType.TEXT, candidate.name),
                record_id=record_id,
                claim_kind=name_kind,
                method=name_method,
                confidence=name_confidence,
                evidence=name_evidence,
                dependencies=name_dependencies,
            )
            if candidate.geometry is not None:
                write_claim(
                    subject_entity_id=site_id,
                    subject_stable_key=site_stable_key,
                    predicate="geometry",
                    value=GeometryValue(candidate.geometry),
                    record_id=record_id,
                    claim_kind=ClaimKind.SOURCE_STATEMENT,
                    method="osm_geometry_extraction",
                    confidence=0.7,
                    evidence=(evidence,),
                )
            write_claim(
                subject_entity_id=site_id,
                subject_stable_key=site_stable_key,
                predicate="candidate_classification",
                value=ScalarValue(ScalarType.TEXT, "semiconductor_facility_candidate"),
                record_id=record_id,
                claim_kind=ClaimKind.DERIVED_ESTIMATE,
                method="osm_explicit_semiconductor_rule_v1",
                confidence=0.65,
                dependencies=raw_dependency,
                notes="Candidate classification only; this does not establish an operational facility.",
            )

            explicit_construction = _has_explicit_construction_tag(candidate.tags)
            lifecycle = candidate.lifecycle
            lifecycle_method = candidate.lifecycle_method
            lifecycle_confidence = candidate.lifecycle_confidence
            if lifecycle == "under_construction" and not explicit_construction:
                lifecycle = "unknown"
                lifecycle_method = "osm_candidate_does_not_prove_operations"
                lifecycle_confidence = 0.35
            write_claim(
                subject_entity_id=site_id,
                subject_stable_key=site_stable_key,
                predicate="lifecycle_state",
                value=ScalarValue(ScalarType.TEXT, lifecycle),
                record_id=record_id,
                claim_kind=ClaimKind.DERIVED_ESTIMATE,
                method=lifecycle_method,
                confidence=lifecycle_confidence,
                dependencies=raw_dependency,
                notes="OSM lifecycle interpretation; unknown is retained unless an explicit lifecycle tag is present.",
            )
            if lifecycle == "under_construction" and explicit_construction:
                write_claim(
                    subject_entity_id=site_id,
                    subject_stable_key=site_stable_key,
                    predicate="construction_status",
                    value=MilestoneValue(
                        "construction_observed",
                        MilestoneStatus.STARTED,
                        as_of_date,
                        as_of_date,
                        as_of_date,
                    ),
                    record_id=record_id,
                    claim_kind=ClaimKind.DERIVED_ESTIMATE,
                    method=lifecycle_method,
                    confidence=lifecycle_confidence,
                    dependencies=raw_dependency,
                    notes="Explicit OSM construction tag observed as of this date; not an asserted construction start date.",
                )

            for activity in candidate.facility_activities:
                write_claim(
                    subject_entity_id=site_id,
                    subject_stable_key=site_stable_key,
                    predicate="facility_activity",
                    value=CapabilityValue(
                        "facility_activity",
                        activity,
                        qualifier="candidate",
                    ),
                    record_id=record_id,
                    claim_kind=ClaimKind.DERIVED_ESTIMATE,
                    method="osm_activity_tag_rule_v1",
                    confidence=0.6,
                    dependencies=raw_dependency,
                    dimension=activity,
                    notes="Candidate activity signal only; no qualification or capacity is inferred.",
                )

            address = _address(candidate.tags)
            if address is not None:
                write_claim(
                    subject_entity_id=site_id,
                    subject_stable_key=site_stable_key,
                    predicate="address",
                    value=ScalarValue(ScalarType.TEXT, address),
                    record_id=record_id,
                    claim_kind=ClaimKind.SOURCE_STATEMENT,
                    method="osm_address_tag_extraction",
                    confidence=0.7,
                    evidence=(evidence,),
                )

            for operator_name in _operator_names(candidate.tags):
                operator_stable_key = _operator_stable_key(operator_name)
                operator_id = stable_id("entity", operator_stable_key)
                entities_created += int(
                    _ensure_entity(
                        connection,
                        identifier=operator_id,
                        kind=EntityKind.ORGANIZATION,
                        stable_key=operator_stable_key,
                        created_at=retrieved_at,
                        run_id=run_id,
                    )
                )
                if operator_id not in named_operators:
                    write_claim(
                        subject_entity_id=operator_id,
                        subject_stable_key=operator_stable_key,
                        predicate="name",
                        value=ScalarValue(ScalarType.TEXT, operator_name),
                        record_id=record_id,
                        claim_kind=ClaimKind.SOURCE_STATEMENT,
                        method="osm_operator_tag_extraction",
                        confidence=0.7,
                        evidence=(evidence,),
                    )
                    named_operators.add(operator_id)
                relationship_attributes = {"source_tag": "operator"}
                if candidate.tags.get("operator:wikidata"):
                    relationship_attributes["operator:wikidata"] = candidate.tags[
                        "operator:wikidata"
                    ]
                write_claim(
                    subject_entity_id=site_id,
                    subject_stable_key=site_stable_key,
                    predicate="operator",
                    value=RelationshipValue(
                        operator_id,
                        "operated_by",
                        relationship_attributes,
                    ),
                    record_id=record_id,
                    claim_kind=ClaimKind.SOURCE_STATEMENT,
                    method="osm_operator_tag_extraction",
                    confidence=0.65,
                    evidence=(evidence,),
                    dimension=operator_id,
                )

        validation_errors = validate_database(connection)
        if validation_errors:
            raise RuntimeError(
                "OSM import failed claim-store validation: " + "; ".join(validation_errors)
            )

    return OSMImportResult(
        source_family_id=family_id,
        source_id=source_id,
        source_document_id=document_id,
        ingestion_run_id=run_id,
        elements_examined=len(elements),
        candidates_imported=len(candidates),
        source_records_created=records_created,
        entities_created=entities_created,
        claim_series_created=series_created,
        claims_created=claims_created,
        site_entity_ids=tuple(site_entity_ids),
        source_record_ids=tuple(source_record_ids),
    )


__all__ = ["OSMImportResult", "import_osm_candidates"]
