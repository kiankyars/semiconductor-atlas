"""Cutoff-aware discovery of registry entities, not identity resolution."""

from __future__ import annotations

import sqlite3
import unicodedata
from datetime import date

from .database import _timestamp_microseconds, knowledge_clock_sql, schema_version
from .evidence_dossier import _clock
from .models import EntityKind


FORMAT = "semiconductor-atlas-evidence-search-v1"
BOUNDARIES = [
    "Discovery over retained registry entries, not a global facility census or an identity decision.",
    "Exact entity IDs remain separate; no alias expansion, fuzzy matching, canonical assignment or phase allocation.",
    "Counts describe the selected entity, not only claims matching the query; zero records is not zero physical capacity.",
    "Source statements use knowledge time. World-current selection ranks all eligible kinds before excluding source statements.",
    "Source labels come only from directly linked evidence on selected claims, including support, context and refute roles.",
    "Historical superseded sources and transitive dependency sources are not searched; use the dossier for history and lineage.",
    "Source-family and document counts are metadata associations, not independent corroboration or confidence.",
    "Creation/admission and direct evidence clocks are filtered. Full transitive lineage and typed-value/source-byte verification are not performed; a dossier can still reject inconsistent lineage.",
    "No source acquisition, lifecycle/capacity inference, forecast, alert delivery or database mutation.",
]


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", value).casefold())


def _publication_available(published, retrieved) -> int:
    if published is None:
        return 1
    try:
        if len(published) == 10:
            return int(date.fromisoformat(published) <= _clock(retrieved).date())
        return int(_clock(published) <= _clock(retrieved))
    except (TypeError, ValueError):
        return 0


def _rows(connection: sqlite3.Connection, *, as_of: str, cutoff: int):
    clock = knowledge_clock_sql(connection)
    connection.create_function("atlas_discovery_publication_available", 2, _publication_available,
                               deterministic=True)
    return connection.execute(f"""
        WITH known_runs AS (
            SELECT r.id FROM ingestion_runs r
            JOIN sources s ON s.id=r.source_id JOIN source_families f ON f.id=s.family_id
            WHERE r.status='succeeded' AND {clock}(r.started_at)<=:cutoff
              AND {clock}(r.completed_at)<=:cutoff
              AND {clock}(s.created_at)<=:cutoff AND {clock}(f.created_at)<=:cutoff
              AND (r.input_document_id IS NULL OR r.input_document_id IN (SELECT id FROM known_documents))
              AND NOT EXISTS (SELECT 1 FROM ingestion_run_documents inputs
                  WHERE inputs.ingestion_run_id=r.id
                    AND inputs.source_document_id NOT IN (SELECT id FROM known_documents))
              AND json_valid(r.parameters_json)
              AND (json_extract(CASE WHEN json_valid(r.parameters_json) THEN r.parameters_json ELSE '{{}}' END,
                                '$.accepted_at') IS NULL
                   OR {clock}(json_extract(CASE WHEN json_valid(r.parameters_json) THEN r.parameters_json ELSE '{{}}' END,
                                          '$.accepted_at'))<=:cutoff)
        ), known_entities AS (
            SELECT e.* FROM entities e WHERE {clock}(e.created_at)<=:cutoff
              AND (e.created_by_run_id IS NULL OR e.created_by_run_id IN (SELECT id FROM known_runs))
        ), active_versions AS (
            SELECT v.*,s.subject_entity_id FROM claim_versions v
            JOIN claim_series s ON s.id=v.series_id JOIN known_entities e ON e.id=s.subject_entity_id
            WHERE {clock}(v.recorded_at)<=:cutoff AND {clock}(s.created_at)<=:cutoff
              AND (v.superseded_at IS NULL OR {clock}(v.superseded_at)>:cutoff)
        ), ranked_world AS (
            SELECT v.*,ROW_NUMBER() OVER (PARTITION BY series_id
                ORDER BY valid_from DESC,{clock}(recorded_at) DESC,id DESC) AS temporal_rank
            FROM active_versions v WHERE valid_from<=:as_of AND (valid_to IS NULL OR :as_of<valid_to)
        ), selected_claims AS (
            SELECT id,subject_entity_id,claim_kind,recorded_at FROM active_versions
            WHERE claim_kind='source_statement'
              AND (created_by_run_id IS NULL OR created_by_run_id IN (SELECT id FROM known_runs))
            UNION ALL
            SELECT id,subject_entity_id,claim_kind,recorded_at FROM ranked_world
            WHERE temporal_rank=1 AND claim_kind!='source_statement'
              AND (created_by_run_id IS NULL OR created_by_run_id IN (SELECT id FROM known_runs))
        ), claim_counts AS (
            SELECT subject_entity_id,
                   sum(claim_kind='source_statement') AS known_source_statement_count,
                   sum(claim_kind!='source_statement') AS world_current_claim_count
            FROM selected_claims GROUP BY subject_entity_id
        ), known_documents AS (
            SELECT d.id,s.id AS source_id,s.publisher,s.stable_key AS source_key,s.name AS source_name,
                   f.id AS source_family_id,f.stable_key AS source_family,f.name AS source_family_name
            FROM source_documents d JOIN sources s ON s.id=d.source_id
            JOIN source_families f ON f.id=s.family_id
            WHERE {clock}(d.retrieved_at)<=:cutoff AND {clock}(s.created_at)<=:cutoff
              AND {clock}(f.created_at)<=:cutoff
              AND atlas_discovery_publication_available(d.published_at,d.retrieved_at)
        ), direct_sources AS (
            SELECT DISTINCT c.subject_entity_id,d.*,e.role FROM selected_claims c
            JOIN claim_evidence e ON e.claim_version_id=c.id
            JOIN known_documents d ON d.id=e.source_document_id
            LEFT JOIN source_records r ON r.id=e.source_record_id AND r.source_document_id=e.source_document_id
            WHERE e.source_record_id IS NULL OR
                ({clock}(r.observed_at)<=:cutoff AND r.ingestion_run_id IN (SELECT id FROM known_runs))
        )
        SELECT e.id,e.kind,e.stable_key,e.display_name,e.created_at,
               coalesce(c.known_source_statement_count,0) AS known_source_statement_count,
               coalesce(c.world_current_claim_count,0) AS world_current_claim_count,
               d.id AS document_id,d.source_id,d.publisher,d.source_key,d.source_name,
               d.source_family_id,d.source_family,d.source_family_name,d.role
        FROM known_entities e LEFT JOIN claim_counts c ON c.subject_entity_id=e.id
        LEFT JOIN direct_sources d ON d.subject_entity_id=e.id
        ORDER BY e.stable_key,e.id,d.source_key,d.id,d.role
        """, {"as_of": as_of, "cutoff": cutoff})


def search_evidence(connection: sqlite3.Connection, *, as_of: str, recorded_at: str,
                    query: str | None = None, kind: str | None = None,
                    limit: int = 50, offset: int = 0) -> dict:
    """Search literal metadata in one snapshot; never hydrate or validate every claim."""
    if not isinstance(as_of, str) or date.fromisoformat(as_of).isoformat() != as_of:
        raise ValueError("as_of must use YYYY-MM-DD")
    recorded_at = _clock(recorded_at).isoformat().replace("+00:00", "Z")
    cutoff = _timestamp_microseconds(recorded_at)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer between 1 and 200")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if kind is not None and (not isinstance(kind, str) or kind not in {item.value for item in EntityKind}):
        raise ValueError("unsupported entity kind")
    if query is not None:
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("query must contain 1 to 200 characters; omit it to browse")
        query = unicodedata.normalize("NFC", query.strip())
    needle = _normalized(query) if query is not None else None
    connection.execute("SAVEPOINT evidence_search_read")
    try:
        version = schema_version(connection)
        if version < 5:
            raise ValueError("evidence search requires existing schema 5 or later; no migration performed")
        entities = {}
        from .source_family_review import apply_resolution, resolutions
        family_mapping = resolutions(connection, recorded_at=recorded_at)
        for row in _rows(connection, as_of=as_of, cutoff=cutoff):
            entity_id = row["id"]
            if entity_id not in entities:
                entities[entity_id] = {key: row[key] for key in (
                    "id", "kind", "stable_key", "display_name", "created_at",
                    "world_current_claim_count", "known_source_statement_count")}
                entities[entity_id]["direct_sources"] = {}
            if row["source_id"] is not None:
                sources = entities[entity_id]["direct_sources"]
                if row["source_id"] not in sources:
                    sources[row["source_id"]] = {key: row[key] for key in (
                        "source_id", "source_key", "source_name", "publisher", "source_family_id",
                        "source_family", "source_family_name")}
                    sources[row["source_id"]].update(document_ids=set(), evidence_roles=set())
                    apply_resolution(sources[row["source_id"]], row["source_id"], family_mapping)
                sources[row["source_id"]]["document_ids"].add(row["document_id"])
                sources[row["source_id"]]["evidence_roles"].add(row["role"])
        matches = []
        for entity in entities.values():
            if kind is not None and entity["kind"] != kind:
                continue
            sources = list(entity["direct_sources"].values())
            fields = {key: entity[key] for key in ("id", "stable_key", "display_name")}
            for source in sources:
                for key in ("source_key", "source_name", "publisher", "source_family", "source_family_name"):
                    fields[f"direct_sources.{source['source_id']}.{key}"] = source[key]
                for key in ("original_source_family", "original_source_family_name"):
                    if "source_family_resolution" in source:
                        fields[f"direct_sources.{source['source_id']}.source_family_resolution.{key}"] = source["source_family_resolution"][key]
            matched = sorted(key for key, value in fields.items()
                             if needle is not None and value is not None and needle in _normalized(value))
            if needle is not None and not matched:
                continue
            documents, families = set(), set()
            for source in sources:
                documents.update(source["document_ids"])
                families.add(source["source_family_id"])
                source["document_ids"] = sorted(source["document_ids"])
                source["document_count"] = len(source["document_ids"])
                source["evidence_roles"] = sorted(source["evidence_roles"])
            entity.update(direct_sources=sources, direct_source_document_count=len(documents),
                          direct_source_family_count=len(families), matched_fields=matched)
            entity["dossier_selection"] = {"entity_id": entity["id"], "as_of": as_of,
                                           "recorded_at": recorded_at}
            matches.append(entity)
        page = matches[offset:offset + limit]
        return {
            "format": FORMAT, "schema_version": version, "as_of": as_of, "recorded_at": recorded_at,
            "query": query, "kind": kind, "matching": "NFC/casefold literal substring in each allowed metadata field; outer query whitespace trimmed",
            "visible_registry_entities": len(entities), "total_matched_entities": len(matches),
            "offset": offset, "limit": limit, "returned_entities": len(page),
            "next_offset": offset + len(page) if offset + len(page) < len(matches) else None,
            "ordering": "immutable stable_key then entity ID, ascending; no relevance or identity score",
            "results": page, "boundaries": BOUNDARIES.copy(),
        }
    finally:
        connection.execute("RELEASE SAVEPOINT evidence_search_read")
