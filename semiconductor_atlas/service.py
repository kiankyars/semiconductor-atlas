"""Historical views, materialization, and semantic validation."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping, Sequence

from .database import schema_version
from .repository import current_claims, validate_database


def default_as_of() -> str:
    return date.today().isoformat()


def default_recorded_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: str | None) -> Any:
    return json.loads(value) if value else {}


_VALUE_TABLES = {
    "scalar": "scalar_values",
    "geometry": "geometry_values",
    "relationship": "relationship_values",
    "milestone": "milestone_values",
    "capability": "capability_values",
    "capacity": "capacity_values",
    "resource": "resource_values",
    "constraint": "constraint_values",
}


def _claim_value_from_row(value_kind: str, row: Mapping[str, Any]) -> dict[str, Any]:
    if value_kind == "scalar":
        if row["scalar_type"] in {"text", "date", "timestamp"}:
            value: Any = row["text_value"]
        elif row["scalar_type"] == "boolean":
            value = bool(row["boolean_value"])
        elif row["scalar_type"] == "integer":
            value = int(row["integer_value"])
        else:
            value = row["number_value"]
        return {"scalar_type": row["scalar_type"], "value": value, "unit": row["unit"]}
    if value_kind == "geometry":
        return {
            "geometry": _json(row["geometry_json"]),
            "crs": row["crs"],
            "precision_m": row["precision_m"],
        }
    if value_kind == "relationship":
        return {
            "object_entity_id": row["object_entity_id"],
            "relationship_type": row["relationship_type"],
            "attributes": _json(row["attributes_json"]),
        }
    if value_kind == "milestone":
        return {
            "milestone_type": row["milestone_type"],
            "status": row["status"],
            "date_low": row["date_low"],
            "date_base": row["date_base"],
            "date_high": row["date_high"],
        }
    if value_kind == "capability":
        value = row["text_value"] if row["text_value"] is not None else row["number_value"]
        return {
            "capability_type": row["capability_type"],
            "value": value,
            "unit": row["unit"],
            "qualifier": row["qualifier"],
        }
    if value_kind == "capacity":
        return {
            "metric": row["metric"],
            "basis": row["basis"],
            "unit": row["unit"],
            "low": row["low"],
            "base": row["base"],
            "high": row["high"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
        }
    if value_kind == "resource":
        return {
            "resource_type": row["resource_type"],
            "unit": row["unit"],
            "low": row["low"],
            "base": row["base"],
            "high": row["high"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
        }
    if value_kind == "constraint":
        return {
            "constraint_type": row["constraint_type"],
            "status": row["status"],
            "severity": row["severity"],
            "description": row["description"],
            "constrained_entity_id": row["constrained_entity_id"],
            "attributes": _json(row["attributes_json"]),
        }
    raise ValueError(f"unsupported claim value kind: {value_kind}")


def claim_value(connection: sqlite3.Connection, claim_id: str, value_kind: str) -> dict[str, Any]:
    table = _VALUE_TABLES.get(value_kind)
    if table is None:
        raise ValueError(f"unsupported claim value kind: {value_kind}")
    row = connection.execute(
        f"SELECT * FROM {table} WHERE claim_version_id = ?", (claim_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"claim {claim_id} lacks a {value_kind} value")
    return _claim_value_from_row(value_kind, row)


def _hydrate_claim_rows(
    connection: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
    *,
    dependency_map: Mapping[str, list[dict[str, str]]] | None = None,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    selected_ids = [str(row["id"]) for row in rows]
    selected_json = json.dumps(selected_ids, separators=(",", ":"))
    values: dict[str, dict[str, Any]] = {}
    for value_kind, table in _VALUE_TABLES.items():
        for value_row in connection.execute(
            f"""
            WITH selected(id) AS (SELECT value FROM json_each(?))
            SELECT values_.*
            FROM {table} AS values_
            JOIN selected ON selected.id = values_.claim_version_id
            ORDER BY values_.claim_version_id
            """,
            (selected_json,),
        ):
            claim_id = str(value_row["claim_version_id"])
            values[claim_id] = _claim_value_from_row(value_kind, value_row)

    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for evidence_row in connection.execute(
        """
        WITH selected(id) AS (SELECT value FROM json_each(?))
        SELECT links.id, links.role, links.locator, links.excerpt,
               links.claim_version_id,
               links.source_record_id, documents.id AS source_document_id,
               documents.document_url, documents.title, documents.published_at,
               documents.retrieved_at, documents.content_sha256, documents.license,
               records.observed_at AS source_record_observed_at,
               records.record_sha256 AS source_record_sha256,
               sources.publisher, sources.stable_key AS source_key,
               families.stable_key AS source_family
        FROM claim_evidence AS links
        JOIN source_documents AS documents ON documents.id = links.source_document_id
        LEFT JOIN source_records AS records
          ON records.id = links.source_record_id
         AND records.source_document_id = links.source_document_id
        JOIN sources ON sources.id = documents.source_id
        JOIN source_families AS families ON families.id = sources.family_id
        JOIN selected ON selected.id = links.claim_version_id
        ORDER BY links.claim_version_id, links.role, documents.document_url,
                 links.locator, links.id
        """,
        (selected_json,),
    ):
        item = dict(evidence_row)
        claim_id = str(item.pop("claim_version_id"))
        evidence[claim_id].append(item)

    if dependency_map is None:
        loaded_dependencies: dict[str, list[dict[str, str]]] = defaultdict(list)
        for dependency_row in connection.execute(
            """
            WITH selected(id) AS (SELECT value FROM json_each(?))
            SELECT dependencies.claim_version_id,
                   dependencies.depends_on_claim_version_id,
                   dependencies.dependency_kind
            FROM claim_dependencies AS dependencies
            JOIN selected ON selected.id = dependencies.claim_version_id
            ORDER BY dependencies.claim_version_id,
                     dependencies.dependency_kind,
                     dependencies.depends_on_claim_version_id
            """,
            (selected_json,),
        ):
            claim_id = str(dependency_row["claim_version_id"])
            loaded_dependencies[claim_id].append(
                {
                    "depends_on_claim_version_id": dependency_row[
                        "depends_on_claim_version_id"
                    ],
                    "dependency_kind": dependency_row["dependency_kind"],
                }
            )
        dependency_map = loaded_dependencies

    records = []
    for row in rows:
        claim_id = str(row["id"])
        value = values.get(claim_id)
        if value is None:
            raise ValueError(
                f"claim {claim_id} lacks its declared {row['value_kind']} value"
            )
        record = dict(row)
        record.pop("temporal_rank", None)
        record["value"] = value
        record["evidence"] = evidence.get(claim_id, [])
        record["dependencies"] = list(dependency_map.get(claim_id, []))
        records.append(record)
    return records


def claim_records(
    connection: sqlite3.Connection,
    *,
    as_of: str,
    recorded_at: str,
    subject_entity_id: str | None = None,
) -> list[dict[str, Any]]:
    rows = current_claims(
        connection,
        as_of=as_of,
        recorded_at=recorded_at,
        subject_entity_id=subject_entity_id,
    )
    return _hydrate_claim_rows(connection, rows)


def claim_history_records(
    connection: sqlite3.Connection,
    *,
    as_of: str,
    recorded_at: str,
    required_claim_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return every claim version known by the two release cutoffs.

    Unlike :func:`claim_records`, this intentionally retains superseded and
    no-longer-current versions.  It is the audit export used to resolve alert
    lineage and reproduce what changed over transaction time.
    """

    available_rows = connection.execute(
        """
        SELECT versions.id, versions.series_id, versions.value_kind,
               versions.value_sha256, versions.valid_from, versions.valid_to,
               versions.recorded_at,
               CASE
                   WHEN versions.superseded_at IS NOT NULL
                    AND julianday(versions.superseded_at) <= julianday(?)
                   THEN versions.superseded_at
                   ELSE NULL
               END AS superseded_at,
               versions.claim_kind, versions.method, versions.confidence,
               versions.created_by_run_id, versions.notes,
               series.subject_entity_id,
               series.stable_key AS series_stable_key, series.predicate
        FROM claim_versions AS versions
        JOIN claim_series AS series ON series.id = versions.series_id
        WHERE julianday(versions.recorded_at) <= julianday(?)
        """,
        (recorded_at, recorded_at),
    ).fetchall()
    rows_by_id = {row["id"]: row for row in available_rows}
    selected_ids = {
        row["id"] for row in available_rows if row["valid_from"] <= as_of
    }
    for claim_id in required_claim_ids:
        if claim_id not in rows_by_id:
            raise ValueError(
                f"required claim history reference {claim_id} is unavailable at "
                f"recorded cutoff {recorded_at}"
            )
        selected_ids.add(claim_id)
    dependencies: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in connection.execute(
        """
        SELECT claim_version_id, depends_on_claim_version_id, dependency_kind
        FROM claim_dependencies
        ORDER BY claim_version_id, dependency_kind, depends_on_claim_version_id
        """
    ):
        dependencies[row["claim_version_id"]].append(
            {
                "depends_on_claim_version_id": row["depends_on_claim_version_id"],
                "dependency_kind": row["dependency_kind"],
            }
        )
    pending = list(selected_ids)
    while pending:
        child_id = pending.pop()
        for dependency in dependencies.get(child_id, []):
            parent_id = dependency["depends_on_claim_version_id"]
            if parent_id not in rows_by_id:
                raise ValueError(
                    f"claim history dependency {parent_id} is unavailable at "
                    f"recorded cutoff {recorded_at}"
                )
            if parent_id not in selected_ids:
                selected_ids.add(parent_id)
                pending.append(parent_id)

    rows = sorted(
        (rows_by_id[claim_id] for claim_id in selected_ids),
        key=lambda row: (
            row["subject_entity_id"],
            row["predicate"],
            row["series_id"],
            row["valid_from"],
            row["recorded_at"],
            row["id"],
        ),
    )
    return _hydrate_claim_rows(connection, rows, dependency_map=dependencies)


def _preferred_name(entity: sqlite3.Row, claims: list[dict[str, Any]]) -> str:
    priorities = ("name", "entity_name", "site_name", "project_name", "organization_name")
    for predicate in priorities:
        for claim in claims:
            if claim["predicate"] == predicate and claim["value_kind"] == "scalar":
                return str(claim["value"]["value"])
    return entity["display_name"] or entity["stable_key"]


def materialize_entities(
    connection: sqlite3.Connection,
    *,
    as_of: str,
    recorded_at: str,
    additional_entity_ids: Iterable[str] = (),
    claims: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    claims_by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    referenced_entity_ids: set[str] = set()
    claim_view = (
        list(claims)
        if claims is not None
        else claim_records(connection, as_of=as_of, recorded_at=recorded_at)
    )
    for claim in claim_view:
        claims_by_entity[claim["subject_entity_id"]].append(claim)
        if claim["value_kind"] == "relationship":
            referenced_entity_ids.add(claim["value"]["object_entity_id"])
        elif claim["value_kind"] == "constraint":
            constrained_entity_id = claim["value"]["constrained_entity_id"]
            if constrained_entity_id is not None:
                referenced_entity_ids.add(constrained_entity_id)

    records = []
    entities = connection.execute(
        """
        SELECT id, kind, stable_key, display_name, created_at
        FROM entities
        WHERE julianday(created_at) <= julianday(?)
        ORDER BY kind, id
        """,
        (recorded_at,),
    ).fetchall()
    included_entity_ids = (
        set(claims_by_entity) | referenced_entity_ids | set(additional_entity_ids)
    )
    for entity in entities:
        if entity["id"] not in included_entity_ids:
            continue
        claims = claims_by_entity.get(entity["id"], [])
        scalar_fields: dict[str, list[dict[str, Any]]] = defaultdict(list)
        geometry = None
        capabilities = []
        capacities = []
        relationships = []
        milestones = []
        resources = []
        constraints = []
        source_urls = set()
        evidence_ids = set()
        for claim in claims:
            for evidence in claim["evidence"]:
                source_urls.add(evidence["document_url"])
                locator = evidence["locator"]
                if isinstance(locator, str) and locator.startswith(("https://", "http://")):
                    source_urls.add(locator)
                evidence_ids.add(evidence["id"])
            summary = {
                "claim_id": claim["id"],
                "predicate": claim["predicate"],
                "claim_kind": claim["claim_kind"],
                "method": claim["method"],
                "confidence": claim["confidence"],
                "valid_from": claim["valid_from"],
                **claim["value"],
            }
            if claim["value_kind"] == "scalar":
                scalar_fields[claim["predicate"]].append(summary)
            elif claim["value_kind"] == "geometry" and geometry is None:
                geometry = claim["value"]["geometry"]
            elif claim["value_kind"] == "capability":
                capabilities.append(summary)
            elif claim["value_kind"] == "capacity":
                capacities.append(summary)
            elif claim["value_kind"] == "relationship":
                relationships.append(summary)
            elif claim["value_kind"] == "milestone":
                milestones.append(summary)
            elif claim["value_kind"] == "resource":
                resources.append(summary)
            elif claim["value_kind"] == "constraint":
                constraints.append(summary)
        records.append(
            {
                "entity_id": entity["id"],
                "entity_kind": entity["kind"],
                "stable_key": entity["stable_key"],
                "name": _preferred_name(entity, claims),
                "geometry": geometry,
                "scalar_fields": dict(sorted(scalar_fields.items())),
                "capabilities": sorted(capabilities, key=lambda item: (item["predicate"], item["claim_id"])),
                "capacities": sorted(capacities, key=lambda item: (item["metric"], item["basis"], item["claim_id"])),
                "relationships": sorted(relationships, key=lambda item: (item["relationship_type"], item["claim_id"])),
                "milestones": sorted(milestones, key=lambda item: (item["milestone_type"], item["claim_id"])),
                "resources": sorted(resources, key=lambda item: (item["resource_type"], item["claim_id"])),
                "constraints": sorted(constraints, key=lambda item: (item["constraint_type"], item["claim_id"])),
                "claim_count": len(claims),
                "evidence_link_ids": sorted(evidence_ids),
                "source_urls": sorted(source_urls),
            }
        )
    return records


def _geojson_source_terms(
    connection: sqlite3.Connection,
    *,
    claim_ids: Iterable[str],
    recorded_at: str,
) -> tuple[list[str], list[str]]:
    """Collect source terms reachable from selected claims at the cutoff."""

    seeds = sorted(set(claim_ids))
    if not seeds:
        return [], []
    rows = connection.execute(
        """
        WITH RECURSIVE
        seeds(id) AS (
            SELECT value FROM json_each(?)
        ),
        reachable(id) AS (
            SELECT id FROM seeds
            UNION
            SELECT dependencies.depends_on_claim_version_id
            FROM claim_dependencies AS dependencies
            JOIN reachable ON reachable.id = dependencies.claim_version_id
            JOIN claim_versions AS parents
              ON parents.id = dependencies.depends_on_claim_version_id
            WHERE julianday(parents.recorded_at) <= julianday(?)
        )
        SELECT DISTINCT
               json_extract(documents.metadata_json, '$.attribution') AS attribution,
               COALESCE(
                   documents.license,
                   json_extract(documents.metadata_json, '$.license')
               ) AS license
        FROM reachable
        JOIN claim_evidence AS evidence
          ON evidence.claim_version_id = reachable.id
        JOIN source_documents AS documents
          ON documents.id = evidence.source_document_id
        WHERE julianday(documents.retrieved_at) <= julianday(?)
        ORDER BY attribution, license
        """,
        (json.dumps(seeds), recorded_at, recorded_at),
    ).fetchall()
    attributions = sorted({row["attribution"] for row in rows if row["attribution"]})
    licenses = sorted({row["license"] for row in rows if row["license"]})
    return attributions, licenses


def export_geojson(
    connection: sqlite3.Connection,
    *,
    as_of: str,
    recorded_at: str,
    claims: Sequence[dict[str, Any]] | None = None,
    entities: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    features = []
    claim_view = (
        list(claims)
        if claims is not None
        else claim_records(connection, as_of=as_of, recorded_at=recorded_at)
    )
    attributions, licenses = _geojson_source_terms(
        connection,
        claim_ids=(claim["id"] for claim in claim_view),
        recorded_at=recorded_at,
    )
    entity_view = (
        list(entities)
        if entities is not None
        else materialize_entities(
            connection,
            as_of=as_of,
            recorded_at=recorded_at,
            claims=claim_view,
        )
    )
    for record in entity_view:
        if record["entity_kind"] == "organization":
            continue
        properties = dict(record)
        geometry = properties.pop("geometry")
        features.append(
            {
                "type": "Feature",
                "id": record["entity_id"],
                "geometry": geometry,
                "properties": properties,
            }
        )
    features.sort(key=lambda item: (item["properties"]["entity_kind"], item["id"]))
    return {
        "type": "FeatureCollection",
        "atlas_as_of": as_of,
        "atlas_recorded_at": recorded_at,
        "attribution": attributions,
        "licenses": licenses,
        "features": features,
    }


def summarize(
    connection: sqlite3.Connection,
    *,
    as_of: str,
    recorded_at: str,
    claims: Sequence[dict[str, Any]] | None = None,
    entities: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    claim_view = (
        list(claims)
        if claims is not None
        else claim_records(connection, as_of=as_of, recorded_at=recorded_at)
    )
    entity_view = (
        list(entities)
        if entities is not None
        else materialize_entities(
            connection,
            as_of=as_of,
            recorded_at=recorded_at,
            claims=claim_view,
        )
    )
    entity_counts = dict(
        sorted(Counter(entity["entity_kind"] for entity in entity_view).items())
    )
    document_runs_sql = _accepted_document_runs_sql(connection)
    document_counts = connection.execute(
        f"""
        WITH document_runs AS (
            {document_runs_sql}
        ), accepted_documents AS (
            SELECT DISTINCT document_id
            FROM document_runs
            WHERE julianday(started_at) <= julianday(?)
        )
        SELECT COUNT(DISTINCT documents.id) AS document_count,
               COUNT(DISTINCT documents.source_id) AS source_count
        FROM accepted_documents
        JOIN source_documents AS documents ON documents.id = accepted_documents.document_id
        """,
        (recorded_at,),
    ).fetchone()
    documents = document_counts["document_count"]
    sources = document_counts["source_count"]
    basis_counts = Counter(
        claim["value"]["basis"]
        for claim in claim_view
        if claim["value_kind"] == "capacity"
    )
    return {
        "schema_version": schema_version(connection),
        "as_of": as_of,
        "recorded_at": recorded_at,
        "entities_total": sum(entity_counts.values()),
        "entities_by_kind": entity_counts,
        "entities_with_geometry": sum(
            entity["geometry"] is not None for entity in entity_view
        ),
        "source_count": sources,
        "source_document_count": documents,
        "current_claim_count": len(claim_view),
        "claims_by_kind": dict(
            sorted(Counter(claim["claim_kind"] for claim in claim_view).items())
        ),
        "claims_by_value_kind": dict(
            sorted(Counter(claim["value_kind"] for claim in claim_view).items())
        ),
        "capacity_claims_by_basis": dict(sorted(basis_counts.items())),
    }


def _accepted_document_runs_sql(connection: sqlite3.Connection) -> str:
    """Return the cutoff-ready document/run ledger with a schema-v2 fallback."""

    has_ledger = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'ingestion_run_documents'
        """
    ).fetchone() is not None
    if has_ledger:
        return """
            SELECT DISTINCT links.source_document_id AS document_id,
                   runs.started_at, runs.completed_at
            FROM ingestion_run_documents AS links
            JOIN ingestion_runs AS runs ON runs.id = links.ingestion_run_id
            WHERE runs.status = 'succeeded'
        """
    return """
        SELECT input_document_id AS document_id, started_at, completed_at
        FROM ingestion_runs
        WHERE status = 'succeeded' AND input_document_id IS NOT NULL
        UNION
        SELECT records.source_document_id AS document_id,
               runs.started_at, runs.completed_at
        FROM source_records AS records
        JOIN ingestion_runs AS runs ON runs.id = records.ingestion_run_id
        WHERE runs.status = 'succeeded'
        UNION
        SELECT inputs.value AS document_id, runs.started_at, runs.completed_at
        FROM ingestion_runs AS runs,
             json_each(runs.parameters_json, '$.index_document_ids') AS inputs
        WHERE runs.status = 'succeeded'
        UNION
        SELECT inputs.value AS document_id, runs.started_at, runs.completed_at
        FROM ingestion_runs AS runs,
             json_each(runs.parameters_json, '$.detail_document_ids') AS inputs
        WHERE runs.status = 'succeeded'
    """


def validate_semantics(connection: sqlite3.Connection) -> list[str]:
    errors = validate_database(connection)
    supporting_evidence = {
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT claim_version_id FROM claim_evidence WHERE role = 'support'"
        )
    }
    dependencies: dict[str, list[str]] = defaultdict(list)
    for row in connection.execute(
        "SELECT claim_version_id, depends_on_claim_version_id FROM claim_dependencies"
    ):
        dependencies[row[0]].append(row[1])
    claims = {
        row["id"]: row["claim_kind"]
        for row in connection.execute("SELECT id, claim_kind FROM claim_versions")
    }
    memo: dict[str, bool] = {}

    def reaches_evidence(claim_id: str, stack: set[str]) -> bool:
        if claim_id in memo:
            return memo[claim_id]
        if claim_id in supporting_evidence:
            memo[claim_id] = True
            return True
        if claim_id in stack:
            return False
        result = bool(dependencies.get(claim_id)) and all(
            reaches_evidence(parent, {*stack, claim_id}) for parent in dependencies[claim_id]
        )
        memo[claim_id] = result
        return result

    for claim_id, claim_kind in claims.items():
        if not reaches_evidence(claim_id, set()):
            errors.append(f"claim {claim_id} does not recursively reach source evidence")
        if claim_kind in {"source_statement", "direct_observation"} and claim_id not in supporting_evidence:
            errors.append(f"{claim_kind} claim {claim_id} requires direct supporting evidence")
        if claim_kind == "derived_estimate" and not dependencies.get(claim_id):
            errors.append(f"derived_estimate claim {claim_id} requires claim dependencies")
    return sorted(set(errors))
