"""Deterministic, auditable release bundles."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .adapters.moenv_ems import MOENV_ATTRIBUTION
from .adapters.taiwan_factory_registry import TAIWAN_FACTORY_ATTRIBUTION
from .adapters.taiwan_mof_tax_registry import TAIWAN_MOF_ATTRIBUTION
from .database import knowledge_clock_sql, schema_version
from .coverage import (
    REVIEWED_RELATIONSHIP_FAMILY_KEY,
    TAIWAN_FACTORY_FAMILY_KEY,
    TAIWAN_MOF_FAMILY_KEY,
    TAIWAN_MOENV_FAMILY_KEY,
    coverage_report,
    reviewed_relationship_claim_ids_if_visible,
)
from .service import (
    _admission_sql,
    claim_history_records,
    claim_records,
    export_geojson,
    materialize_entities,
    source_claim_records,
    summarize,
    validate_semantics,
)


FORMAT = "semiconductor-atlas-release-v1"
EPA_FRS_FAMILY_KEY = "epa-frs"
EPA_FRS_SOURCE_KEY = "epa-frs-national-single:epa-frs-semiconductor-direct-v1"
EPA_FRS_ATTRIBUTION = (
    "Source: U.S. Environmental Protection Agency, Facility Registry Service "
    "(public-domain U.S. Government data; no EPA endorsement)"
)

_ENTITY_IDENTITY_TABLES = frozenset(
    {
        "ingestion_run_documents",
        "organization_name_claim_metadata",
        "organization_identifier_claim_metadata",
        "entity_resolution_runs",
        "entity_resolution_run_inputs",
        "entity_resolution_candidates",
        "entity_resolution_decisions",
        "source_entity_assignments",
    }
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Iterable[object]) -> bytes:
    return (
        "".join(
            json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            + "\n"
            for row in rows
        )
    ).encode("utf-8")


def _csv_bytes(
    fieldnames: Sequence[str], rows: Iterable[Mapping[str, object]]
) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {key: _spreadsheet_safe_cell(value) for key, value in row.items()}
        )
    return stream.getvalue().encode("utf-8")


def _spreadsheet_safe_cell(value: object) -> object:
    """Prevent text fields from being interpreted as spreadsheet formulas."""

    if not isinstance(value, str) or not value:
        return value
    stripped = value.lstrip()
    if value.startswith(("\t", "\r", "\n")) or stripped.startswith(
        ("=", "+", "-", "@")
    ):
        return "'" + value
    return value


def _has_entity_identity_schema(connection: sqlite3.Connection) -> bool:
    placeholders = ",".join("?" for _ in _ENTITY_IDENTITY_TABLES)
    present = {
        str(row[0])
        for row in connection.execute(
            f"""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name IN ({placeholders})
            """,
            tuple(sorted(_ENTITY_IDENTITY_TABLES)),
        )
    }
    return present == _ENTITY_IDENTITY_TABLES


def _write(path: Path, raw: bytes) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _existing_release_files(
    output: Path,
    new_names: set[str],
) -> tuple[list[Path], list[Path]]:
    """Validate an existing release and identify stale managed and preserved files."""

    if not output.exists():
        return [], []
    if not output.is_dir():
        raise ValueError(f"release output is not a directory: {output}")
    existing_names = {path.name for path in output.iterdir()}
    if not existing_names:
        return [], []

    manifest_path = output / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(
            "non-empty release output lacks a prior manifest; refusing to overwrite"
        )
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("prior release manifest must be a regular file")
    try:
        prior_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(
            "prior release manifest is unreadable; refusing to overwrite"
        ) from error
    if prior_manifest.get("format") != FORMAT or not isinstance(
        prior_manifest.get("files"), dict
    ):
        raise ValueError("prior release manifest is not a compatible managed release")

    prior_names = set()
    prior_metadata: dict[str, dict[str, object]] = {}
    for name, metadata in prior_manifest["files"].items():
        if (
            not isinstance(name, str)
            or name in {".", ".."}
            or Path(name).name != name
            or name == "manifest.json"
        ):
            raise ValueError(
                "prior release manifest contains an unsafe managed filename"
            )
        if not isinstance(metadata, dict):
            raise ValueError(f"prior release manifest has invalid metadata for {name}")
        expected_bytes = metadata.get("bytes")
        expected_sha256 = metadata.get("sha256")
        if (
            isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 0
            or not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            raise ValueError(f"prior release manifest has invalid metadata for {name}")
        prior_names.add(name)
        prior_metadata[name] = metadata

    missing_managed = sorted(prior_names - existing_names)
    if missing_managed:
        raise ValueError(
            "managed release files listed in the prior manifest are missing: "
            + ", ".join(missing_managed)
            + "; refusing to repair an incomplete release implicitly"
        )

    managed_names = prior_names | {"manifest.json"}
    unknown_names = existing_names - managed_names
    collisions = sorted(unknown_names & (new_names | {"manifest.json"}))
    if collisions:
        raise ValueError(
            "release output contains unmanaged files that would be overwritten: "
            + ", ".join(collisions)
        )
    unknown_files = []
    for name in sorted(unknown_names):
        path = output / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"unmanaged release path must be a regular file to preserve it: {name}"
            )
        unknown_files.append(path)
    for name in sorted(existing_names & managed_names):
        path = output / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"managed release path is not a regular file: {name}")
        if name != "manifest.json":
            raw = path.read_bytes()
            metadata = prior_metadata[name]
            if (
                len(raw) != metadata["bytes"]
                or hashlib.sha256(raw).hexdigest() != metadata["sha256"]
            ):
                raise ValueError(
                    f"managed release file differs from prior manifest: {name}; "
                    "refusing to overwrite or delete"
                )
    stale = [
        output / name for name in sorted((prior_names - new_names) & existing_names)
    ]
    return stale, unknown_files


def _validate_admission_clocks(connection: sqlite3.Connection) -> None:
    if schema_version(connection) < 5:
        return
    for row in connection.execute(
        "SELECT id, started_at, completed_at, parameters_json FROM ingestion_runs WHERE status='succeeded'"
    ):
        parameters = json.loads(row["parameters_json"])
        if "accepted_at" not in parameters:
            continue
        try:
            accepted = datetime.fromisoformat(parameters["accepted_at"].replace("Z", "+00:00"))
            started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
            completed = datetime.fromisoformat(row["completed_at"].replace("Z", "+00:00"))
            if accepted.utcoffset() is None or not started <= accepted <= completed:
                raise ValueError("admission is outside its processing interval")
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(f"ingestion run {row['id']} has invalid explicit accepted_at") from error


def _source_inputs(
    connection: sqlite3.Connection,
    *,
    recorded_at: str,
) -> list[dict[str, Any]]:
    has_run_document_ledger = (
        connection.execute(
            """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'ingestion_run_documents'
        """
        ).fetchone()
        is not None
    )
    if has_run_document_ledger:
        document_runs_sql = """
            SELECT runs.id AS run_id, links.source_document_id AS document_id,
                   runs.started_at, runs.completed_at,
                   runs.code_version, runs.parameters_json,
                   json_group_array(links.role) AS document_roles_json
            FROM ingestion_run_documents AS links
            JOIN ingestion_runs AS runs ON runs.id = links.ingestion_run_id
            WHERE runs.status = 'succeeded'
            GROUP BY runs.id, links.source_document_id
        """
    else:
        document_runs_sql = """
            SELECT runs.id AS run_id, runs.input_document_id AS document_id,
                   runs.started_at, runs.completed_at,
                   runs.code_version, runs.parameters_json,
                   NULL AS document_roles_json
            FROM ingestion_runs AS runs
            WHERE runs.status = 'succeeded' AND runs.input_document_id IS NOT NULL
            UNION
            SELECT runs.id AS run_id, records.source_document_id AS document_id,
                   runs.started_at, runs.completed_at,
                   runs.code_version, runs.parameters_json,
                   NULL AS document_roles_json
            FROM source_records AS records
            JOIN ingestion_runs AS runs ON runs.id = records.ingestion_run_id
            WHERE runs.status = 'succeeded'
            UNION
            SELECT runs.id AS run_id, inputs.value AS document_id,
                   runs.started_at, runs.completed_at,
                   runs.code_version, runs.parameters_json,
                   NULL AS document_roles_json
            FROM ingestion_runs AS runs,
                 json_each(runs.parameters_json, '$.index_document_ids') AS inputs
            WHERE runs.status = 'succeeded'
            UNION
            SELECT runs.id AS run_id, inputs.value AS document_id,
                   runs.started_at, runs.completed_at,
                   runs.code_version, runs.parameters_json,
                   NULL AS document_roles_json
            FROM ingestion_runs AS runs,
                 json_each(runs.parameters_json, '$.detail_document_ids') AS inputs
            WHERE runs.status = 'succeeded'
        """
    accepted_at_sql = _admission_sql(connection)
    clock = knowledge_clock_sql(connection)
    rows = connection.execute(
        f"""
        WITH document_runs AS (
            {document_runs_sql}
        ), accepted AS (
            SELECT document_runs.*, {accepted_at_sql} AS admitted_at
            FROM document_runs
            WHERE julianday({accepted_at_sql}) <= julianday(?)
        )
        SELECT documents.id, documents.document_url, documents.title,
               documents.published_at, documents.retrieved_at,
               documents.content_sha256, documents.media_type, documents.license,
               documents.metadata_json, sources.stable_key AS source_key,
               sources.publisher, families.stable_key AS source_family,
               accepted.run_id AS accepted_by_run_id,
               accepted.admitted_at AS database_accepted_at,
               accepted.code_version AS acceptance_code_version,
               accepted.parameters_json AS acceptance_parameters_json,
               accepted.document_roles_json
        FROM source_documents AS documents
        JOIN sources ON sources.id = documents.source_id
        JOIN source_families AS families ON families.id = sources.family_id
        JOIN accepted ON accepted.document_id = documents.id
        ORDER BY families.stable_key, sources.stable_key, documents.document_url,
                 accepted.admitted_at, documents.retrieved_at, documents.id,
                 accepted.run_id
        """.replace("julianday(", f"{clock}("),
        (recorded_at,),
    ).fetchall()
    inputs: list[dict[str, Any]] = []
    by_document: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        parameters = json.loads(item.pop("acceptance_parameters_json"))
        roles_json = item.pop("document_roles_json")
        processing_run = {
            "ingestion_run_id": item["accepted_by_run_id"],
            "database_accepted_at": item["database_accepted_at"],
            "code_version": item["acceptance_code_version"],
            "acceptance_timestamp_basis": parameters.get(
                "acceptance_timestamp_basis", "legacy_retrieval_clock"
            ),
            "parameters": parameters,
        }
        if roles_json is not None:
            processing_run["document_roles"] = sorted(json.loads(roles_json))
        document_id = str(item["id"])
        existing = by_document.get(document_id)
        if existing is not None:
            existing["processing_runs"].append(processing_run)
            continue
        item["metadata"] = json.loads(item.pop("metadata_json"))
        item["acceptance_timestamp_basis"] = parameters.get(
            "acceptance_timestamp_basis", "legacy_retrieval_clock"
        )
        item["processing_runs"] = [processing_run]
        by_document[document_id] = item
        inputs.append(item)
    return inputs


def _reviewed_relationship_runs(
    connection: sqlite3.Connection,
    *,
    recorded_at: str,
) -> tuple[list[dict[str, Any]], set[str]]:
    rows = connection.execute(
        """
        SELECT runs.id AS review_ingestion_run_id,
               families.stable_key AS source_family,
               sources.stable_key AS source_key,
               runs.started_at, runs.completed_at, runs.status,
               runs.code_version, runs.parameters_json, runs.error
        FROM ingestion_runs AS runs
        JOIN sources ON sources.id = runs.source_id
        JOIN source_families AS families ON families.id = sources.family_id
        WHERE families.stable_key = ?
          AND runs.status = 'succeeded'
          AND runs.completed_at IS NOT NULL
          AND julianday(runs.completed_at) <= julianday(?)
        ORDER BY julianday(runs.completed_at), julianday(runs.started_at), runs.id
        """,
        (REVIEWED_RELATIONSHIP_FAMILY_KEY, recorded_at),
    ).fetchall()
    result: list[dict[str, Any]] = []
    required_claim_ids: set[str] = set()
    for row in rows:
        try:
            parameters = json.loads(row["parameters_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(
                f"review run {row['review_ingestion_run_id']} parameters are invalid JSON"
            ) from error
        if not isinstance(parameters, dict):
            raise ValueError(
                f"review run {row['review_ingestion_run_id']} parameters must be an object"
            )
        visible_claim_ids = reviewed_relationship_claim_ids_if_visible(
            connection,
            parameters=parameters,
            recorded_at=recorded_at,
            context=f"review run {row['review_ingestion_run_id']}",
        )
        if visible_claim_ids is None:
            continue
        required_claim_ids.update(visible_claim_ids)
        result.append(
            {
                "review_ingestion_run_id": row["review_ingestion_run_id"],
                "source_family": row["source_family"],
                "source_key": row["source_key"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "status": row["status"],
                "code_version": row["code_version"],
                "parameters": parameters,
                "error": row["error"],
            }
        )
    return result, required_claim_ids


def _source_observations(
    connection: sqlite3.Connection,
    *,
    recorded_at: str,
) -> list[dict[str, Any]]:
    accepted_at_sql = _admission_sql(connection, "runs.")
    clock = knowledge_clock_sql(connection)
    rows = connection.execute(
        f"""
        SELECT records.id AS source_record_id,
               records.source_record_key,
               records.observed_at AS source_record_observed_at,
               records.record_sha256,
               records.source_document_id,
               documents.document_url,
               documents.retrieved_at,
               documents.content_sha256,
               runs.id AS ingestion_run_id,
               {accepted_at_sql} AS database_accepted_at,
               runs.code_version,
               runs.parameters_json,
               sources.stable_key AS source_key,
               families.stable_key AS source_family
        FROM source_records AS records
        JOIN ingestion_runs AS runs ON runs.id = records.ingestion_run_id
        JOIN source_documents AS documents ON documents.id = records.source_document_id
        JOIN sources ON sources.id = documents.source_id
        JOIN source_families AS families ON families.id = sources.family_id
        WHERE julianday({accepted_at_sql}) <= julianday(?)
          AND runs.status = 'succeeded'
        ORDER BY families.stable_key, sources.stable_key,
                 database_accepted_at, records.source_record_key, records.id
        """.replace("julianday(", f"{clock}("),
        (recorded_at,),
    ).fetchall()
    observations = []
    for row in rows:
        item = dict(row)
        parameters = json.loads(item.pop("parameters_json"))
        item["acceptance_timestamp_basis"] = parameters.get(
            "acceptance_timestamp_basis", "legacy_retrieval_clock"
        )
        observations.append(item)
    return observations


def _organization_claim_metadata(
    connection: sqlite3.Connection,
    *,
    claim_history: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    claim_ids = sorted(str(claim["id"]) for claim in claim_history)
    if not claim_ids:
        return []
    rows = connection.execute(
        """
        WITH selected(id) AS (SELECT value FROM json_each(?))
        SELECT names.claim_version_id, 'name' AS metadata_kind,
               names.name_type, names.language_tag, names.script_code,
               NULL AS scheme, NULL AS normalized_value, NULL AS jurisdiction
        FROM organization_name_claim_metadata AS names
        JOIN selected ON selected.id = names.claim_version_id
        UNION ALL
        SELECT identifiers.claim_version_id, 'identifier' AS metadata_kind,
               NULL AS name_type, NULL AS language_tag, NULL AS script_code,
               identifiers.scheme, identifiers.normalized_value,
               identifiers.jurisdiction
        FROM organization_identifier_claim_metadata AS identifiers
        JOIN selected ON selected.id = identifiers.claim_version_id
        ORDER BY claim_version_id, metadata_kind
        """,
        (json.dumps(claim_ids, separators=(",", ":")),),
    ).fetchall()
    return [dict(row) for row in rows]


def _entity_resolution_runs(
    connection: sqlite3.Connection,
    *,
    recorded_at: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, started_at, completed_at, status, resolver_version,
               code_version, parameters_json, error
        FROM entity_resolution_runs
        WHERE status IN ('succeeded', 'failed')
          AND julianday(completed_at) <= julianday(?)
        ORDER BY started_at, id
        """,
        (recorded_at,),
    ).fetchall()
    if not rows:
        return []
    run_ids = [str(row["id"]) for row in rows]
    input_rows = connection.execute(
        """
        WITH selected(id) AS (SELECT value FROM json_each(?))
        SELECT inputs.resolution_run_id, inputs.ingestion_run_id
        FROM entity_resolution_run_inputs AS inputs
        JOIN selected ON selected.id = inputs.resolution_run_id
        ORDER BY inputs.resolution_run_id, inputs.ingestion_run_id
        """,
        (json.dumps(run_ids, separators=(",", ":")),),
    ).fetchall()
    inputs_by_run: dict[str, list[str]] = {}
    for row in input_rows:
        inputs_by_run.setdefault(str(row["resolution_run_id"]), []).append(
            str(row["ingestion_run_id"])
        )
    scope_rows = connection.execute(
        """
        WITH selected(id) AS (SELECT value FROM json_each(?))
        SELECT DISTINCT candidates.resolution_run_id, observed.kind
        FROM entity_resolution_candidates AS candidates
        JOIN selected ON selected.id = candidates.resolution_run_id
        JOIN entities AS observed ON observed.id = candidates.observed_entity_id
        ORDER BY candidates.resolution_run_id, observed.kind
        """,
        (json.dumps(run_ids, separators=(",", ":")),),
    ).fetchall()
    scopes_by_run: dict[str, list[str]] = {}
    for row in scope_rows:
        scopes_by_run.setdefault(str(row["resolution_run_id"]), []).append(
            f"{row['kind']}_identity"
        )
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        run_id = str(item["id"])
        item["parameters"] = json.loads(item.pop("parameters_json"))
        item["input_ingestion_run_ids"] = inputs_by_run.get(run_id, [])
        item["identity_scopes"] = scopes_by_run.get(run_id, [])
        result.append(item)
    return result


def _entity_resolution_candidates(
    connection: sqlite3.Connection,
    *,
    recorded_at: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT candidates.id, candidates.resolution_run_id,
               candidates.source_record_id, records.source_record_key,
               candidates.observed_entity_id,
               observed.stable_key AS observed_entity_stable_key,
               observed.kind AS observed_entity_kind,
               candidates.candidate_entity_id,
               proposed.stable_key AS candidate_entity_stable_key,
               proposed.kind AS candidate_entity_kind,
               observed.kind || '_identity' AS identity_scope,
               candidates.features_json, candidates.score,
               candidates.candidate_rank, candidates.created_at
        FROM entity_resolution_candidates AS candidates
        JOIN entity_resolution_runs AS runs
          ON runs.id = candidates.resolution_run_id
        JOIN source_records AS records ON records.id = candidates.source_record_id
        JOIN entities AS observed ON observed.id = candidates.observed_entity_id
        JOIN entities AS proposed ON proposed.id = candidates.candidate_entity_id
        WHERE runs.status IN ('succeeded', 'failed')
          AND julianday(runs.completed_at) <= julianday(?)
          AND julianday(candidates.created_at) <= julianday(?)
        ORDER BY runs.started_at, candidates.resolution_run_id,
                 candidates.source_record_id, candidates.observed_entity_id,
                 candidates.candidate_rank, candidates.id
        """,
        (recorded_at, recorded_at),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["features"] = json.loads(item.pop("features_json"))
        result.append(item)
    return result


def _entity_resolution_decisions(
    connection: sqlite3.Connection,
    *,
    recorded_at: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT decisions.id, decisions.candidate_id,
               candidates.resolution_run_id, decisions.outcome,
               decisions.decided_at, decisions.decided_by, decisions.reason,
               decisions.metadata_json,
               observed.kind AS observed_entity_kind,
               proposed.kind AS candidate_entity_kind,
               observed.kind || '_identity' AS identity_scope
        FROM entity_resolution_decisions AS decisions
        JOIN entity_resolution_candidates AS candidates
          ON candidates.id = decisions.candidate_id
        JOIN entity_resolution_runs AS runs
          ON runs.id = candidates.resolution_run_id
        JOIN entities AS observed ON observed.id = candidates.observed_entity_id
        JOIN entities AS proposed ON proposed.id = candidates.candidate_entity_id
        WHERE runs.status = 'succeeded'
          AND julianday(runs.completed_at) <= julianday(?)
          AND julianday(decisions.decided_at) <= julianday(?)
        ORDER BY decisions.decided_at, decisions.id
        """,
        (recorded_at, recorded_at),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        result.append(item)
    return result


def _source_entity_assignments(
    connection: sqlite3.Connection,
    *,
    as_of: str,
    recorded_at: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT assignments.id, assignments.source_record_id,
               records.source_record_key, assignments.observed_entity_id,
               observed.stable_key AS observed_entity_stable_key,
               observed.kind AS observed_entity_kind,
               assignments.canonical_entity_id,
               canonical.stable_key AS canonical_entity_stable_key,
               canonical.kind AS canonical_entity_kind,
               observed.kind || '_identity' AS identity_scope,
               assignments.decision_id, assignments.valid_from,
               assignments.valid_to, assignments.recorded_at,
               CASE
                   WHEN assignments.superseded_at IS NOT NULL
                    AND julianday(assignments.superseded_at) <= julianday(?)
                   THEN assignments.superseded_at
                   ELSE NULL
               END AS superseded_at
        FROM source_entity_assignments AS assignments
        JOIN source_records AS records ON records.id = assignments.source_record_id
        JOIN entities AS observed ON observed.id = assignments.observed_entity_id
        JOIN entities AS canonical ON canonical.id = assignments.canonical_entity_id
        JOIN entity_resolution_decisions AS decisions
          ON decisions.id = assignments.decision_id
        WHERE assignments.valid_from <= ?
          AND julianday(assignments.recorded_at) <= julianday(?)
          AND julianday(decisions.decided_at) <= julianday(?)
        ORDER BY assignments.source_record_id, assignments.observed_entity_id,
                 assignments.valid_from, assignments.recorded_at, assignments.id
        """,
        (recorded_at, as_of, recorded_at, recorded_at),
    ).fetchall()
    return [dict(row) for row in rows]


def _entity_identity_views(
    connection: sqlite3.Connection,
    *,
    as_of: str,
    recorded_at: str,
    claim_history: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]] | None:
    if not _has_entity_identity_schema(connection):
        return None
    return {
        "organization_claim_metadata": _organization_claim_metadata(
            connection,
            claim_history=claim_history,
        ),
        "entity_resolution_runs": _entity_resolution_runs(
            connection,
            recorded_at=recorded_at,
        ),
        "entity_resolution_candidates": _entity_resolution_candidates(
            connection,
            recorded_at=recorded_at,
        ),
        "entity_resolution_decisions": _entity_resolution_decisions(
            connection,
            recorded_at=recorded_at,
        ),
        "source_entity_assignments": _source_entity_assignments(
            connection,
            as_of=as_of,
            recorded_at=recorded_at,
        ),
    }


def _evidence_rows(claims: list[dict[str, Any]]) -> list[dict[str, object]]:
    rows = []
    for claim in claims:
        for evidence in claim["evidence"]:
            rows.append(
                {
                    "claim_id": claim["id"],
                    "entity_id": claim["subject_entity_id"],
                    "predicate": claim["predicate"],
                    "role": evidence["role"],
                    "evidence_link_id": evidence["id"],
                    "source_document_id": evidence["source_document_id"],
                    "source_record_id": evidence["source_record_id"],
                    "source_record_observed_at": evidence["source_record_observed_at"],
                    "source_record_sha256": evidence["source_record_sha256"],
                    "source_family": evidence["source_family"],
                    "publisher": evidence["publisher"],
                    "document_url": evidence["document_url"],
                    "published_at": evidence["published_at"],
                    "retrieved_at": evidence["retrieved_at"],
                    "content_sha256": evidence["content_sha256"],
                    "license": evidence["license"],
                    "locator": evidence["locator"],
                    "excerpt": evidence["excerpt"],
                }
            )
    return sorted(
        rows, key=lambda row: (str(row["claim_id"]), str(row["evidence_link_id"]))
    )


def _capacity_rows(claims: list[dict[str, Any]]) -> list[dict[str, object]]:
    rows = []
    for claim in claims:
        if claim["value_kind"] != "capacity":
            continue
        value = claim["value"]
        rows.append(
            {
                "claim_id": claim["id"],
                "entity_id": claim["subject_entity_id"],
                "predicate": claim["predicate"],
                "claim_kind": claim["claim_kind"],
                "metric": value["metric"],
                "basis": value["basis"],
                "unit": value["unit"],
                "low": value["low"],
                "base": value["base"],
                "high": value["high"],
                "period_start": value["period_start"],
                "period_end": value["period_end"],
                "valid_from": claim["valid_from"],
                "recorded_at": claim["recorded_at"],
                "method": claim["method"],
                "confidence": claim["confidence"],
                "evidence_link_count": len(claim["evidence"]),
                "dependency_count": len(claim["dependencies"]),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            str(row["entity_id"]),
            str(row["metric"]),
            str(row["basis"]),
            str(row["claim_id"]),
        ),
    )


def _attributions(inputs: list[dict[str, Any]]) -> list[str]:
    values = set()
    for item in inputs:
        metadata = item.get("metadata") or {}
        attribution = (
            metadata.get("attribution") if isinstance(metadata, dict) else None
        )
        source_family = item.get("source_family")
        if source_family == TAIWAN_MOENV_FAMILY_KEY:
            values.add(MOENV_ATTRIBUTION)
        elif source_family == TAIWAN_FACTORY_FAMILY_KEY:
            values.add(TAIWAN_FACTORY_ATTRIBUTION)
        elif source_family == TAIWAN_MOF_FAMILY_KEY:
            values.add(TAIWAN_MOF_ATTRIBUTION)
        elif attribution:
            values.add(str(attribution))
        if source_family == "openstreetmap":
            values.add("© OpenStreetMap contributors (ODbL 1.0)")
        elif source_family == "nist-chips":
            values.add("Source: U.S. Department of Commerce, NIST CHIPS for America")
        elif source_family == EPA_FRS_FAMILY_KEY:
            values.add(EPA_FRS_ATTRIBUTION)
    return sorted(values)


def _recover_interrupted_install(output: Path) -> None:
    if output.is_symlink():
        raise ValueError(f"release output must not be a symlink: {output}")
    backup = output.parent / f".{output.name}.semiconductor-atlas-previous"
    if backup.is_symlink():
        raise ValueError(f"release recovery path must not be a symlink: {backup}")
    if not backup.exists():
        return
    if output.exists():
        raise ValueError(
            f"release recovery backup already exists: {backup}; inspect it before retrying"
        )
    if backup.is_symlink() or not backup.is_dir():
        raise ValueError(f"release recovery path is not a directory: {backup}")
    backup.rename(output)


def _install_release_directory(
    output: Path,
    files: Mapping[str, bytes],
    manifest: Mapping[str, object],
    preserved_files: Sequence[Path],
) -> None:
    if not output.name or output.name in {".", ".."}:
        raise ValueError(f"unsafe release output directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    _recover_interrupted_install(output)
    backup = output.parent / f".{output.name}.semiconductor-atlas-previous"
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.semiconductor-atlas-stage-",
            dir=output.parent,
        )
    )
    try:
        for path in preserved_files:
            shutil.copy2(path, stage / path.name)
        for name, raw in sorted(files.items()):
            _write(stage / name, raw)
        _write(stage / "manifest.json", _json_bytes(manifest))

        if output.exists():
            output.rename(backup)
        try:
            stage.rename(output)
        except BaseException:
            if not output.exists() and backup.exists():
                backup.rename(output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _collect_release_views(
    connection: sqlite3.Connection,
    *,
    as_of: str,
    recorded_at: str,
) -> dict[str, object]:
    errors = validate_semantics(connection)
    if errors:
        raise ValueError("database validation failed: " + "; ".join(errors))
    _validate_admission_clocks(connection)
    claims = claim_records(connection, as_of=as_of, recorded_at=recorded_at)
    source_claims = (
        source_claim_records(connection, recorded_at=recorded_at)
        if schema_version(connection) >= 5 else None
    )
    reviewed_relationship_runs, required_review_claim_ids = (
        _reviewed_relationship_runs(connection, recorded_at=recorded_at)
    )
    required_identity_claim_ids: set[str] = set()
    if _has_entity_identity_schema(connection):
        for candidate in _entity_resolution_candidates(
            connection, recorded_at=recorded_at
        ):
            declared = candidate["features"].get("target_evidence_claim_version_ids")
            if declared is None:
                continue
            if not isinstance(declared, list) or any(
                not isinstance(claim_id, str) or not claim_id for claim_id in declared
            ):
                raise ValueError(
                    "entity-resolution candidate target evidence IDs must be strings"
                )
            required_identity_claim_ids.update(declared)
    claim_history = claim_history_records(
        connection,
        as_of=as_of,
        recorded_at=recorded_at,
        required_claim_ids=sorted(
            required_identity_claim_ids | required_review_claim_ids
            | {claim["id"] for claim in source_claims or []}
        ),
    )
    identity = _entity_identity_views(
        connection,
        as_of=as_of,
        recorded_at=recorded_at,
        claim_history=claim_history,
    )
    history_entity_ids = {claim["subject_entity_id"] for claim in claim_history}
    for claim in claim_history:
        if claim["value_kind"] == "relationship":
            history_entity_ids.add(claim["value"]["object_entity_id"])
        elif claim["value_kind"] == "constraint":
            constrained_entity_id = claim["value"]["constrained_entity_id"]
            if constrained_entity_id is not None:
                history_entity_ids.add(constrained_entity_id)
    if identity is not None:
        for candidate in identity["entity_resolution_candidates"]:
            history_entity_ids.add(candidate["observed_entity_id"])
            history_entity_ids.add(candidate["candidate_entity_id"])
        for assignment in identity["source_entity_assignments"]:
            history_entity_ids.add(assignment["observed_entity_id"])
            history_entity_ids.add(assignment["canonical_entity_id"])
    entities = materialize_entities(
        connection,
        as_of=as_of,
        recorded_at=recorded_at,
        additional_entity_ids=history_entity_ids,
        claims=claims,
    )
    summary = summarize(
        connection,
        as_of=as_of,
        recorded_at=recorded_at,
        claims=claims,
        entities=entities,
    )
    inputs = _source_inputs(connection, recorded_at=recorded_at)
    observations = _source_observations(connection, recorded_at=recorded_at)
    geojson = export_geojson(
        connection,
        as_of=as_of,
        recorded_at=recorded_at,
        claims=claims,
        entities=entities,
    )
    coverage = coverage_report(
        connection,
        as_of=as_of,
        recorded_at=recorded_at,
        claims=claims,
        entities=entities,
        summary=summary,
    )
    return {
        "claims": claims,
        "source_claims": source_claims,
        "claim_history": claim_history,
        "entities": entities,
        "summary": summary,
        "inputs": inputs,
        "reviewed_relationship_runs": reviewed_relationship_runs,
        "observations": observations,
        "geojson": geojson,
        "coverage": coverage,
        "identity": identity,
    }


def write_release(
    connection: sqlite3.Connection,
    output_dir: str | Path,
    *,
    as_of: str,
    recorded_at: str,
    extra_files: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN")
    try:
        views = _collect_release_views(connection, as_of=as_of, recorded_at=recorded_at)
    except BaseException:
        if owns_transaction:
            connection.rollback()
        raise
    else:
        if owns_transaction:
            connection.commit()

    output = Path(output_dir)
    if output.is_symlink():
        raise ValueError(f"release output must not be a symlink: {output}")
    claims = views["claims"]
    source_claims = views["source_claims"]
    claim_history = views["claim_history"]
    entities = views["entities"]
    summary = views["summary"]
    inputs = views["inputs"]
    reviewed_relationship_runs = views["reviewed_relationship_runs"]
    observations = views["observations"]
    geojson = views["geojson"]
    coverage = views["coverage"]
    identity = views["identity"]
    assert isinstance(claims, list)
    assert source_claims is None or isinstance(source_claims, list)
    assert isinstance(claim_history, list)
    assert isinstance(entities, list)
    assert isinstance(summary, dict)
    assert isinstance(inputs, list)
    assert isinstance(reviewed_relationship_runs, list)
    assert isinstance(observations, list)
    assert isinstance(geojson, dict)
    assert isinstance(coverage, dict)
    assert identity is None or isinstance(identity, dict)
    evidence = _evidence_rows(claim_history)
    capacities = _capacity_rows(claims)
    attributions = _attributions(inputs)

    files: dict[str, bytes] = {
        "entities.jsonl": _jsonl_bytes(entities),
        "claims.jsonl": _jsonl_bytes(claims),
        "claim_history.jsonl": _jsonl_bytes(claim_history),
        "evidence.csv": _csv_bytes(
            (
                "claim_id",
                "entity_id",
                "predicate",
                "role",
                "evidence_link_id",
                "source_document_id",
                "source_record_id",
                "source_family",
                "publisher",
                "source_record_observed_at",
                "source_record_sha256",
                "document_url",
                "published_at",
                "retrieved_at",
                "content_sha256",
                "license",
                "locator",
                "excerpt",
            ),
            evidence,
        ),
        "capacity.csv": _csv_bytes(
            (
                "claim_id",
                "entity_id",
                "predicate",
                "claim_kind",
                "metric",
                "basis",
                "unit",
                "low",
                "base",
                "high",
                "period_start",
                "period_end",
                "valid_from",
                "recorded_at",
                "method",
                "confidence",
                "evidence_link_count",
                "dependency_count",
            ),
            capacities,
        ),
        "atlas.geojson": _json_bytes(geojson),
        "coverage.json": _json_bytes(coverage),
        "summary.json": _json_bytes(summary),
        "source_inputs.json": _json_bytes(inputs),
        "source_observations.jsonl": _jsonl_bytes(observations),
        "ATTRIBUTION.txt": (
            "\n".join(attributions) + ("\n" if attributions else "")
        ).encode("utf-8"),
    }
    if identity is not None:
        for name, rows in identity.items():
            files[f"{name}.jsonl"] = _jsonl_bytes(rows)
    if source_claims is not None:
        files["source_claims.jsonl"] = _jsonl_bytes(source_claims)
    if reviewed_relationship_runs:
        files["reviewed_relationship_runs.jsonl"] = _jsonl_bytes(
            reviewed_relationship_runs
        )
    extra_names = sorted(extra_files or {})
    extra_text = ""
    if extra_names:
        extra_text = (
            "\nAdditional generated outputs: "
            + ", ".join(f"`{name}`" for name in extra_names)
            + ". Forecast outputs are experimental assumptions, not calibrated facts.\n"
        )
    readme = f"""# Semiconductor Atlas release

Format: `{FORMAT}`  
World-state cutoff: `{as_of}`  
Knowledge cutoff: `{recorded_at}`

This is an evidence-first seed, not a complete global census. NIST records establish government
award disclosures; OpenStreetMap records are candidate leads only. Announced capacity, physical
construction, installed tools, qualified capacity, and economically usable capacity remain separate.
EPA Facility Registry Service rows are U.S. registry candidate leads only; they establish neither
operation, lifecycle state, nor capacity. Raw NAD83 latitude/longitude scalars are source fields,
not GeoJSON geometry; only explicit geometry claims appear in `atlas.geojson`.
The immutable EPA FRS source snapshot retains the exact raw ZIP at a content-addressed locator and
deep-verifies the filtered derivative against it. The ZIP is not duplicated into this release;
`source_inputs.json` preserves its snapshot directory name, manifest hash, raw locator, content
hash, byte count, and rights metadata in each FRS processing-run record.
Every exported claim carries direct evidence or a dependency chain to evidence.
`claims.jsonl` is the current bitemporal view. `claim_history.jsonl` retains superseded versions
and the transitive dependency closure needed to resolve every exported lineage reference.
`entities.jsonl` includes current materializations plus any history-only subjects or referenced
entities needed to preserve that referential closure.
EPA FRS acquisition and database acceptance clocks are persisted separately. Semantically unchanged
refresh rows remain in `source_observations.jsonl` without manufacturing duplicate claim versions.
Legacy NIST and OSM seed runs use their pinned retrieval timestamp as the acceptance clock.
NIST `site_amount_usd`, `project_amount_usd`, and `program_amount_usd` predicates have different
scopes and must not be added without an explicit allocation model. Repeated raw source statements
are not a reconciled-fact layer.
Spreadsheet-facing CSV text that could be interpreted as a formula is prefixed with an apostrophe;
the exact source text remains available in the JSONL claim exports.

Files: `entities.jsonl`, `claims.jsonl`, `claim_history.jsonl`, `evidence.csv`,
`capacity.csv`, `atlas.geojson`, `coverage.json`, `source_inputs.json`,
`source_observations.jsonl`, `summary.json`,
and `ATTRIBUTION.txt`.
{extra_text}
"""
    if any(item.get("source_family") == TAIWAN_MOENV_FAMILY_KEY for item in inputs):
        readme = readme.replace(
            "Every exported claim carries direct evidence or a dependency chain to evidence.",
            "Taiwan MOENV EMS_S_01 records are source-native environmental-control registry "
            "candidates selected by exact industry codes 2611, 2612, and 2613. Registry "
            "membership and release-from-control dates establish neither operation, production, "
            "ownership, facility closure, nor capacity. The current-registry indicator is a "
            "mechanical test that any source environmental-control flag equals 1, not an "
            "operating-status estimate. The filtered candidate derivative is "
            "complete only within the verified archived EMS_S_01 package; it is not a national "
            "semiconductor facility census. Valid invariant WGS84 points alone become geometry, "
            "and conflicting source variants remain parallel claims. The immutable raw archive "
            "is retained by content hash in the source snapshot and is not duplicated into this "
            "release.\n"
            "Every exported claim carries direct evidence or a dependency chain to evidence.",
        )
    if any(item.get("source_family") == TAIWAN_FACTORY_FAMILY_KEY for item in inputs):
        readme = readme.replace(
            "Every exported claim carries direct evidence or a dependency chain to evidence.",
            "Taiwan registered-factory records are source-native administrative registry "
            "candidates selected only when the principal-product list contains the exact "
            "token 261半導體. The responsible person's name is retained only inside the "
            "immutable raw archive and is omitted from the privacy-minimized derivative, "
            "source records, claims, and release. Factory-registration identity, publisher "
            "registration status, and the exact product token establish neither observed "
            "operation, output, lifecycle, ownership, capacity, utilization, nor yield. "
            "A complete refresh can close only prior same-filter source-assertion intervals; "
            "partial refresh omissions remain open, and no absence becomes facility-closure "
            "evidence. The raw archive is retained by content hash in the source snapshot and "
            "is not duplicated into this release.\n"
            "Every exported claim carries direct evidence or a dependency chain to evidence.",
        )
    if any(item.get("source_family") == TAIWAN_MOF_FAMILY_KEY for item in inputs):
        readme = readme.replace(
            "Every exported claim carries direct evidence or a dependency chain to evidence.",
            "Taiwan MOF BGMOPEN1 records are source-native active tax-registration "
            "organizations selected by an exact UBN allowlist re-derived from the accepted "
            "MOENV and registered-factory snapshots. A tax unit or branch is not automatically "
            "a legal company, parent, facility owner, operator, or proof of activity, and its "
            "head-office UBN remains contextual source data rather than inferred parentage. "
            "Capital and invoice-use fields are omitted. A complete refresh can close only "
            "prior same-source assertion intervals; partial omissions remain open, and absence "
            "never establishes legal closure, tax inactivity, facility lifecycle, or a "
            "production stop. The matched derivative, exact allowlist, and immutable raw "
            "archive remain bound by content hash and source-snapshot lineage; the raw archive "
            "is not duplicated into this release.\n"
            "Every exported claim carries direct evidence or a dependency chain to evidence.",
        )
    if identity is not None:
        identity_scope_text = (
            "Schema-v4 same-kind entity identity provenance is exported without inferring new "
            "merges. Organization-to-organization and facility-to-facility identity are explicit "
            "scopes; cross-kind assignments are prohibited and facility-to-organization links "
            "remain typed relationships rather than identity. "
            if summary["schema_version"] >= 4
            else "Schema-v3 organization identity provenance is exported without inferring new merges. "
        )
        readme = readme.replace(
            "EPA FRS acquisition and database acceptance clocks are persisted separately.",
            identity_scope_text
            + "Name and identifier metadata join to `claim_history.jsonl` by claim version ID. "
            "Resolution candidates are exported only for terminal runs, decisions are "
            "knowledge-cutoff correct, and source assignments retain their bitemporal history.\n"
            "GLEIF Level 1 claims, when present, remain on source-native organizations. Legal and "
            "headquarters addresses are organization attributes rather than facility geometry; "
            "LEI registration status is not evidence of facility ownership or operation.\n"
            "EPA FRS acquisition and database acceptance clocks are persisted separately.",
        )
        readme = readme.replace(
            "`source_observations.jsonl`, `summary.json`,\nand `ATTRIBUTION.txt`.",
            "`source_observations.jsonl`, `summary.json`, "
            "`organization_claim_metadata.jsonl`,\n"
            "`entity_resolution_runs.jsonl`, `entity_resolution_candidates.jsonl`, "
            "`entity_resolution_decisions.jsonl`,\n"
            "`source_entity_assignments.jsonl`, and `ATTRIBUTION.txt`.",
        )
    has_taiwan_tax_relationship_review = any(
        run["parameters"].get("workflow")
        == "taiwan-facility-tax-unit-reviewed-v1"
        and run["parameters"].get("relationship_type")
        == "registered_tax_unit_reference"
        for run in reviewed_relationship_runs
    )
    if has_taiwan_tax_relationship_review:
        readme = readme.replace(
            "Every exported claim carries direct evidence or a dependency chain to evidence.",
            "Reviewed `registered_tax_unit_reference` claims record exact same-time UBN "
            "references only. They do not assert identity, legal-person identity, ownership, "
            "parentage, operator or operation, activity, or lifecycle. Multiple conflicting "
            "source references remain separate. `reviewed_relationship_runs.jsonl` exports "
            "candidate and review hashes, selected source bindings, complete decisions, and "
            "created, reaffirmed, and superseded lineage.\n"
            "Every exported claim carries direct evidence or a dependency chain to evidence.",
        )
    if reviewed_relationship_runs:
        readme = readme.replace(
            "`capacity.csv`, `atlas.geojson`, `coverage.json`, `source_inputs.json`,\n",
            "`capacity.csv`, `atlas.geojson`, `coverage.json`, `source_inputs.json`,\n"
            "`reviewed_relationship_runs.jsonl`, ",
        )
    if source_claims is not None:
        readme += (
            "\n`source_claims.jsonl` is a knowledge-time view of source statements, "
            "not a physical-world state view. It includes statements with unknown or future "
            "effective dates; `claims.jsonl` retains its world-state cutoff. Null confidence "
            "means uncalibrated, and a null milestone midpoint is not an exact date. "
            "Source-stated period bounds describe calendar precision, not a forecast "
            "probability interval. All source-claim versions and their dependencies are "
            "retained in `claim_history.jsonl`.\n"
        )
    files["README.md"] = readme.encode("utf-8")
    if extra_files:
        for name, raw in extra_files.items():
            if (
                name in {".", ".."}
                or Path(name).name != name
                or name == "manifest.json"
            ):
                raise ValueError(f"invalid extra release filename: {name}")
            if name in files:
                raise ValueError(
                    f"extra release file conflicts with a core file: {name}"
                )
            if not isinstance(raw, bytes):
                raise ValueError(f"extra release file must be bytes: {name}")
            files[name] = raw

    _recover_interrupted_install(output)
    _, preserved_files = _existing_release_files(output, set(files))
    file_manifest = {
        name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        for name, raw in sorted(files.items())
    }
    manifest = {
        "format": FORMAT,
        "as_of": as_of,
        "recorded_at": recorded_at,
        "entities": len(entities),
        "claims": len(claims),
        "claim_history": len(claim_history),
        "evidence_links": len(evidence),
        "capacity_claims": len(capacities),
        "source_documents": len(inputs),
        "source_observations": len(observations),
        "files": file_manifest,
    }
    if identity is not None:
        manifest["schema_version"] = summary["schema_version"]
        manifest.update({name: len(rows) for name, rows in identity.items()})
    if source_claims is not None:
        manifest["source_claims"] = len(source_claims)
    if reviewed_relationship_runs:
        manifest["reviewed_relationship_runs"] = len(reviewed_relationship_runs)
    _install_release_directory(output, files, manifest, preserved_files)
    return manifest
