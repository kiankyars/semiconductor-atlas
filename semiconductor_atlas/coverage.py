"""Honest coverage accounting for a bounded atlas release."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any, Sequence

from .models import CapacityBasis
from .service import (
    _accepted_document_runs_sql,
    claim_records,
    materialize_entities,
    summarize,
)


KNOWN_GAPS = (
    "This seed is not a global census and has no measured recall denominator.",
    "NIST coverage is limited to archived CHIPS Program Office award disclosures in this snapshot.",
    "OpenStreetMap rows are low-recall candidate leads, not verified operating facilities.",
    "EPA FRS coverage is limited to exact NAICS 334413 or SIC 3674 rows; these can be stale, "
    "low-precision registry candidates and do not establish a semiconductor facility.",
    "Broader adjacent NAICS material, printed-circuit assembly, and electronic-component codes "
    "are excluded from the current EPA FRS adapter.",
    "EPA FRS has no global recall denominator; it covers U.S. public registry records rather "
    "than the worldwide facility universe.",
    "EPA FRS latitude and longitude are retained as raw NAD83 source scalars; no CRS transform "
    "or FRS geometry is emitted.",
    "EPA FRS record absence, reassignment, or Registry ID merge is not evidence of facility "
    "closure, cancellation, or inactivity.",
    "Cross-source entity resolution and organization-alias consolidation are not yet implemented.",
    "Legacy NIST and OpenStreetMap seed runs use the pinned retrieval timestamp as their database "
    "acceptance clock; EPA FRS refreshes persist acquisition and acceptance separately.",
    "Permits, utilities, filings, trade, equipment shipments, hiring, and satellite observations are not ingested.",
    "Forecast parameters are transparent assumptions without calibration or historical backtests.",
    "Supply-demand, pricing, earnings, and investable-signal models are not implemented.",
)

TAIWAN_MOENV_FAMILY_KEY = "taiwan-moenv-ems"
TAIWAN_MOENV_ENTITY_PREFIX = "taiwan-moenv-ems:ems_s_01:"
TAIWAN_FACTORY_FAMILY_KEY = "taiwan-ida-factory"
TAIWAN_FACTORY_ENTITY_PREFIX = "taiwan-ida-factory:registered-factories:"
TAIWAN_MOF_FAMILY_KEY = "taiwan-mof-tax"
TAIWAN_MOF_ENTITY_PREFIX = "taiwan-mof-tax:bgmopen1:"
REVIEWED_RELATIONSHIP_FAMILY_KEY = "semiconductor-atlas-reviewed-relationships"
TAIWAN_TAX_RELATIONSHIP_REVIEW_VERSION = (
    "taiwan-facility-tax-unit-reviewed-v1"
)
TAIWAN_TAX_RELATIONSHIP_TYPE = "registered_tax_unit_reference"
TAIWAN_TAX_RELATIONSHIP_METHOD = (
    "taiwan_registered_tax_unit_reference_review_v1"
)


def _metadata_value(
    parameters: dict[str, Any],
    metadata: dict[str, Any],
    *names: str,
) -> Any:
    """Read one audited metric from run parameters or source metadata."""

    containers = (
        parameters,
        parameters.get("summary"),
        parameters.get("source_summary"),
        metadata,
        metadata.get("summary"),
        metadata.get("source_summary"),
    )
    for container in containers:
        if not isinstance(container, dict):
            continue
        for name in names:
            if name in container:
                return container[name]
    return None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _count_mapping(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, int] = {}
    for key, count in value.items():
        normalized = _nonnegative_int(count)
        if not isinstance(key, str) or normalized is None:
            return None
        result[key] = normalized
    return dict(sorted(result.items()))


def _json_object(value: Any, context: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{context} is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{context} must be a JSON object")
    return parsed


def reviewed_relationship_claim_ids_if_visible(
    connection: sqlite3.Connection,
    *,
    parameters: dict[str, Any],
    recorded_at: str,
    context: str,
) -> set[str] | None:
    """Validate review action lineage and return IDs once every effect is visible."""

    descriptor_ids: list[str] = []
    for field in ("created_claims", "reaffirmed_claims"):
        descriptors = parameters.get(field)
        if not isinstance(descriptors, list) or any(
            not isinstance(item, dict) for item in descriptors
        ):
            raise ValueError(f"{context}.{field} is malformed")
        for descriptor in descriptors:
            claim_id = descriptor.get("claim_id")
            descriptor_recorded_at = descriptor.get("recorded_at")
            if (
                not isinstance(claim_id, str)
                or not claim_id
                or not isinstance(descriptor_recorded_at, str)
                or not descriptor_recorded_at
            ):
                raise ValueError(f"{context}.{field} descriptor is malformed")
            row = connection.execute(
                "SELECT recorded_at FROM claim_versions WHERE id = ?", (claim_id,)
            ).fetchone()
            if row is None or row["recorded_at"] != descriptor_recorded_at:
                raise ValueError(f"{context}.{field} claim lineage conflicts")
            descriptor_ids.append(claim_id)

    superseded_claim_ids = parameters.get("superseded_claim_ids")
    if not isinstance(superseded_claim_ids, list) or any(
        not isinstance(item, str) or not item for item in superseded_claim_ids
    ):
        raise ValueError(f"{context}.superseded_claim_ids is malformed")
    if len(descriptor_ids) != len(set(descriptor_ids)):
        raise ValueError(f"{context} repeats a created or reaffirmed claim")
    if set(descriptor_ids) & set(superseded_claim_ids):
        raise ValueError(f"{context} both retains and supersedes the same claim")

    timestamps: list[str] = []
    for claim_id in descriptor_ids:
        row = connection.execute(
            "SELECT recorded_at FROM claim_versions WHERE id = ?", (claim_id,)
        ).fetchone()
        assert row is not None
        timestamps.append(str(row["recorded_at"]))
    for claim_id in superseded_claim_ids:
        row = connection.execute(
            "SELECT superseded_at FROM claim_versions WHERE id = ?", (claim_id,)
        ).fetchone()
        if row is None or row["superseded_at"] is None:
            raise ValueError(f"{context}.superseded_claim_ids lineage conflicts")
        timestamps.append(str(row["superseded_at"]))

    for timestamp in timestamps:
        comparison = connection.execute(
            "SELECT julianday(?) <= julianday(?)", (timestamp, recorded_at)
        ).fetchone()[0]
        if comparison is None:
            raise ValueError(f"{context} contains an invalid lineage timestamp")
        if not comparison:
            return None
    return set(descriptor_ids) | set(superseded_claim_ids)


def _taiwan_tax_relationship_review_scope(
    connection: sqlite3.Connection,
    *,
    recorded_at: str,
    claims: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    rows = connection.execute(
        """
        SELECT runs.id AS review_ingestion_run_id,
               sources.stable_key AS source_key,
               runs.started_at, runs.completed_at, runs.code_version,
               runs.parameters_json
        FROM ingestion_runs AS runs
        JOIN sources ON sources.id = runs.source_id
        JOIN source_families AS families ON families.id = sources.family_id
        WHERE families.stable_key = ?
          AND runs.status = 'succeeded'
          AND runs.completed_at IS NOT NULL
          AND julianday(runs.completed_at) <= julianday(?)
          AND runs.code_version = ?
        ORDER BY julianday(runs.completed_at) DESC,
                 julianday(runs.started_at) DESC,
                 runs.id DESC
        """,
        (
            REVIEWED_RELATIONSHIP_FAMILY_KEY,
            recorded_at,
            TAIWAN_TAX_RELATIONSHIP_REVIEW_VERSION,
        ),
    ).fetchall()
    row = None
    parameters = None
    for candidate_row in rows:
        candidate_parameters = _json_object(
            candidate_row["parameters_json"],
            f"review run {candidate_row['review_ingestion_run_id']}.parameters_json",
        )
        if (
            candidate_parameters.get("workflow")
            != TAIWAN_TAX_RELATIONSHIP_REVIEW_VERSION
        ):
            raise ValueError("Taiwan tax-relationship review workflow is malformed")
        if (
            candidate_parameters.get("relationship_type")
            != TAIWAN_TAX_RELATIONSHIP_TYPE
        ):
            raise ValueError("Taiwan tax-relationship review type is malformed")
        visible_claim_ids = reviewed_relationship_claim_ids_if_visible(
            connection,
            parameters=candidate_parameters,
            recorded_at=recorded_at,
            context=f"review run {candidate_row['review_ingestion_run_id']}",
        )
        if visible_claim_ids is None:
            continue
        row = candidate_row
        parameters = candidate_parameters
        break
    if row is None or parameters is None:
        return None

    candidate_artifact = parameters.get("candidate_artifact")
    review_artifact = parameters.get("review_artifact")
    source_bindings = parameters.get("source_bindings")
    decisions = parameters.get("decisions")
    created_claims = parameters.get("created_claims")
    reaffirmed_claims = parameters.get("reaffirmed_claims")
    superseded_claim_ids = parameters.get("superseded_claim_ids")
    if not isinstance(candidate_artifact, dict):
        raise ValueError("Taiwan tax-relationship candidate artifact is malformed")
    if not isinstance(review_artifact, dict):
        raise ValueError("Taiwan tax-relationship review artifact is malformed")
    if not isinstance(source_bindings, dict):
        raise ValueError("Taiwan tax-relationship source bindings are malformed")
    if not isinstance(decisions, list) or any(
        not isinstance(item, dict) for item in decisions
    ):
        raise ValueError("Taiwan tax-relationship decisions are malformed")
    if not isinstance(created_claims, list) or any(
        not isinstance(item, dict) for item in created_claims
    ):
        raise ValueError("Taiwan tax-relationship created lineage is malformed")
    if not isinstance(reaffirmed_claims, list) or any(
        not isinstance(item, dict) for item in reaffirmed_claims
    ):
        raise ValueError("Taiwan tax-relationship reaffirmed lineage is malformed")
    if not isinstance(superseded_claim_ids, list) or any(
        not isinstance(item, str) or not item for item in superseded_claim_ids
    ):
        raise ValueError("Taiwan tax-relationship superseded lineage is malformed")

    outcome_counts = {outcome: 0 for outcome in ("match", "reject", "defer")}
    for decision in decisions:
        outcome = decision.get("outcome")
        if outcome not in outcome_counts:
            raise ValueError("Taiwan tax-relationship decision outcome is malformed")
        outcome_counts[outcome] += 1

    current_relationships = [
        claim
        for claim in claims
        if claim.get("predicate") == TAIWAN_TAX_RELATIONSHIP_TYPE
        and claim.get("value_kind") == "relationship"
        and claim.get("claim_kind") == "reconciled_fact"
        and claim.get("method") == TAIWAN_TAX_RELATIONSHIP_METHOD
        and isinstance(claim.get("value"), dict)
        and claim["value"].get("relationship_type")
        == TAIWAN_TAX_RELATIONSHIP_TYPE
    ]
    tax_units_by_facility: dict[str, set[str]] = {}
    for claim in current_relationships:
        subject = str(claim["subject_entity_id"])
        object_entity_id = str(claim["value"]["object_entity_id"])
        tax_units_by_facility.setdefault(subject, set()).add(object_entity_id)

    return {
        "workflow": parameters["workflow"],
        "relationship_type": parameters["relationship_type"],
        "review_ingestion_run_id": row["review_ingestion_run_id"],
        "review_source_key": row["source_key"],
        "review_run_started_at": row["started_at"],
        "review_run_completed_at": row["completed_at"],
        "accepted_at": parameters.get("accepted_at"),
        "knowledge_cutoff_at": parameters.get("knowledge_cutoff_at"),
        "reviewed_by": parameters.get("reviewed_by"),
        "reviewed_at": parameters.get("reviewed_at"),
        "candidate_artifact": candidate_artifact,
        "review_artifact": review_artifact,
        "selected_source_bindings": source_bindings,
        "candidate_decision_count": len(decisions),
        "decision_counts": outcome_counts,
        "lineage_action_counts": {
            "created": len(created_claims),
            "reaffirmed": len(reaffirmed_claims),
            "superseded": len(superseded_claim_ids),
        },
        "current_relationship_claim_count": len(current_relationships),
        "distinct_facility_subject_count": len(tax_units_by_facility),
        "distinct_tax_unit_object_count": len(
            {
                object_entity_id
                for object_entity_ids in tax_units_by_facility.values()
                for object_entity_id in object_entity_ids
            }
        ),
        "facility_subjects_with_multiple_current_tax_unit_references": sum(
            len(object_entity_ids) > 1
            for object_entity_ids in tax_units_by_facility.values()
        ),
        "semantics": {
            "asserts_exact_selected_source_ubn_agreement": True,
            "asserts_identity": False,
            "asserts_legal_person_identity": False,
            "asserts_ownership": False,
            "asserts_parentage": False,
            "asserts_operator_or_operation": False,
            "asserts_activity": False,
            "asserts_lifecycle": False,
            "allows_multiple_source_references_per_facility": True,
        },
    }


def coverage_report(
    connection: sqlite3.Connection,
    *,
    as_of: str,
    recorded_at: str,
    claims: Sequence[dict[str, Any]] | None = None,
    entities: Sequence[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
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
    summary_view = (
        dict(summary)
        if summary is not None
        else summarize(
            connection,
            as_of=as_of,
            recorded_at=recorded_at,
            claims=claim_view,
            entities=entity_view,
        )
    )
    non_organizations = [
        item for item in entity_view if item["entity_kind"] != "organization"
    ]
    document_runs_sql = _accepted_document_runs_sql(connection)
    source_rows = connection.execute(
        f"""
        WITH document_runs AS (
            {document_runs_sql}
        ), accepted_documents AS (
            SELECT document_id, MIN(started_at) AS database_accepted_at
            FROM document_runs
            WHERE julianday(started_at) <= julianday(?)
            GROUP BY document_id
        )
        SELECT families.stable_key AS source_family,
               families.name AS source_family_name,
               COUNT(DISTINCT sources.id) AS source_count,
               COUNT(documents.id) AS document_count,
               MIN(documents.retrieved_at) AS first_retrieved_at,
               MAX(documents.retrieved_at) AS last_retrieved_at,
               MIN(accepted_documents.database_accepted_at) AS first_database_accepted_at,
               MAX(accepted_documents.database_accepted_at) AS last_database_accepted_at
        FROM accepted_documents
        JOIN source_documents AS documents
          ON documents.id = accepted_documents.document_id
        JOIN sources ON sources.id = documents.source_id
        JOIN source_families AS families ON families.id = sources.family_id
        GROUP BY families.id
        ORDER BY families.stable_key
        """,
        (recorded_at,),
    ).fetchall()
    namespace_counts = dict(
        sorted(
            Counter(
                "nist"
                if entity["stable_key"].startswith("nist:")
                else "openstreetmap"
                if entity["stable_key"].startswith("osm:")
                else "epa_frs"
                if entity["stable_key"].startswith("epa:frs:")
                else "gleif"
                if entity["stable_key"].startswith("gleif:lei:")
                else "taiwan_moenv"
                if entity["stable_key"].startswith(TAIWAN_MOENV_ENTITY_PREFIX)
                else "taiwan_ida_factory"
                if entity["stable_key"].startswith(TAIWAN_FACTORY_ENTITY_PREFIX)
                else "taiwan_mof_tax"
                if entity["stable_key"].startswith(TAIWAN_MOF_ENTITY_PREFIX)
                else "other"
                for entity in entity_view
            ).items()
        )
    )
    basis_counts = {basis.value: 0 for basis in CapacityBasis}
    for claim in claim_view:
        if claim["value_kind"] == "capacity":
            basis_counts[str(claim["value"]["basis"])] += 1
    source_families = [dict(row) for row in source_rows]
    frs_entities = [
        item for item in entity_view if item["stable_key"].startswith("epa:frs:")
    ]
    frs_document = connection.execute(
        """
        SELECT documents.metadata_json, runs.started_at AS processing_run_accepted_at,
               runs.id AS processing_run_id, runs.parameters_json,
               MIN(runs.started_at) OVER (
                   PARTITION BY documents.id
               ) AS source_document_first_accepted_at
        FROM source_documents AS documents
        JOIN sources ON sources.id = documents.source_id
        JOIN source_families AS families ON families.id = sources.family_id
        JOIN ingestion_runs AS runs ON runs.input_document_id = documents.id
        WHERE families.stable_key = 'epa-frs'
          AND runs.status = 'succeeded'
          AND julianday(runs.started_at) <= julianday(?)
          AND date(json_extract(runs.parameters_json, '$.as_of_date')) <= date(?)
        ORDER BY date(json_extract(runs.parameters_json, '$.as_of_date')) DESC,
                 runs.started_at DESC, documents.retrieved_at DESC,
                 documents.id DESC, runs.id DESC
        LIMIT 1
        """,
        (recorded_at, as_of),
    ).fetchone()
    frs_candidate_scope = None
    if frs_document is not None:
        metadata = json.loads(frs_document["metadata_json"])
        parameters = json.loads(frs_document["parameters_json"])
        candidate_count = parameters.get(
            "candidate_count", metadata.get("candidate_count")
        )
        upstream_total_rows = parameters.get(
            "upstream_total_rows", metadata.get("upstream_total_rows")
        )
        if (
            isinstance(candidate_count, int)
            and not isinstance(candidate_count, bool)
            and isinstance(upstream_total_rows, int)
            and not isinstance(upstream_total_rows, bool)
            and upstream_total_rows > 0
        ):
            raw_coordinate_pairs = sum(
                "frs.latitude83_nad83_raw" in item["scalar_fields"]
                and "frs.longitude83_nad83_raw" in item["scalar_fields"]
                for item in frs_entities
            )
            frs_candidate_scope = {
                "complete": parameters.get(
                    "complete",
                    parameters.get("snapshot_is_complete", metadata.get("complete")),
                )
                is True,
                "coverage": parameters.get("coverage", metadata.get("coverage")),
                "data_as_of": parameters.get("as_of_date", metadata.get("data_as_of")),
                "data_as_of_basis": parameters.get(
                    "data_as_of_basis", metadata.get("data_as_of_basis")
                ),
                "data_as_of_source_url": parameters.get(
                    "data_as_of_source_url", metadata.get("data_as_of_source_url")
                ),
                "filter_version": parameters.get(
                    "filter_version", metadata.get("filter_version")
                ),
                "naics_codes": parameters.get(
                    "naics_codes", metadata.get("naics_codes")
                ),
                "sic_codes": parameters.get("sic_codes", metadata.get("sic_codes")),
                "upstream_total_rows": upstream_total_rows,
                "snapshot_candidate_count": candidate_count,
                "candidate_fraction_of_upstream_rows": round(
                    candidate_count / upstream_total_rows, 8
                ),
                "current_candidate_entity_count": len(frs_entities),
                "raw_nad83_coordinate_pair_count": raw_coordinate_pairs,
                "raw_retention": parameters.get(
                    "raw_retention", metadata.get("raw_retention")
                ),
                "retrieval_timestamp_basis": parameters.get(
                    "retrieval_timestamp_basis",
                    metadata.get("retrieval_timestamp_basis"),
                ),
                "database_accepted_at": frs_document[
                    "source_document_first_accepted_at"
                ],
                "source_document_first_accepted_at": frs_document[
                    "source_document_first_accepted_at"
                ],
                "processing_run_accepted_at": frs_document[
                    "processing_run_accepted_at"
                ],
                "processing_run_id": frs_document["processing_run_id"],
                "acceptance_timestamp_basis": parameters.get(
                    "acceptance_timestamp_basis", "legacy_retrieval_clock"
                ),
                "geojson_geometry_count": sum(
                    item["geometry"] is not None for item in frs_entities
                ),
            }
    moenv_entities = [
        item
        for item in entity_view
        if item["stable_key"].startswith(TAIWAN_MOENV_ENTITY_PREFIX)
    ]
    current_claim_entity_ids = {str(claim["subject_entity_id"]) for claim in claim_view}
    current_moenv_entities = [
        item
        for item in moenv_entities
        if str(item["entity_id"]) in current_claim_entity_ids
    ]
    moenv_document = connection.execute(
        """
        SELECT documents.id AS source_document_id,
               documents.metadata_json, documents.retrieved_at,
               runs.started_at AS processing_run_accepted_at,
               runs.id AS processing_run_id, runs.parameters_json,
               MIN(runs.started_at) OVER (
                   PARTITION BY documents.id
               ) AS source_document_first_accepted_at
        FROM ingestion_runs AS runs
        JOIN source_documents AS documents
          ON documents.id = runs.input_document_id
        JOIN sources ON sources.id = documents.source_id
        JOIN source_families AS families ON families.id = sources.family_id
        WHERE families.stable_key = ?
          AND runs.status = 'succeeded'
          AND julianday(runs.started_at) <= julianday(?)
          AND date(
                json_extract(runs.parameters_json, '$.dataset_updated_at'),
                '+8 hours'
              ) <= date(?)
        ORDER BY date(
                     json_extract(runs.parameters_json, '$.dataset_updated_at'),
                     '+8 hours'
                 ) DESC,
                 runs.started_at DESC, documents.retrieved_at DESC,
                 documents.id DESC, runs.id DESC
        LIMIT 1
        """,
        (TAIWAN_MOENV_FAMILY_KEY, recorded_at, as_of),
    ).fetchone()
    moenv_candidate_scope = None
    if moenv_document is not None:
        metadata_value = json.loads(moenv_document["metadata_json"])
        parameters_value = json.loads(moenv_document["parameters_json"])
        metadata = metadata_value if isinstance(metadata_value, dict) else {}
        parameters = parameters_value if isinstance(parameters_value, dict) else {}

        upstream_row_count = _nonnegative_int(
            _metadata_value(parameters, metadata, "upstream_row_count")
        )
        industry_group_row_count = _nonnegative_int(
            _metadata_value(parameters, metadata, "industry_group_row_count")
        )
        raw_matching_row_count = _nonnegative_int(
            _metadata_value(parameters, metadata, "raw_matching_row_count")
        )
        deduplicated_variant_count = _nonnegative_int(
            _metadata_value(
                parameters,
                metadata,
                "deduplicated_variant_count",
                "variant_count",
                "record_count",
                "candidate_count",
            )
        )
        facility_count = _nonnegative_int(
            _metadata_value(parameters, metadata, "facility_count")
        )
        conflicting_facility_count = _nonnegative_int(
            _metadata_value(parameters, metadata, "conflicting_facility_count")
        )
        current_regulation_facility_count = _nonnegative_int(
            _metadata_value(
                parameters,
                metadata,
                "current_regulation_facility_count",
                "current_regulation_count",
            )
        )
        valid_coordinate_facility_count = _nonnegative_int(
            _metadata_value(
                parameters,
                metadata,
                "valid_coordinate_facility_count",
                "valid_coordinate_count",
            )
        )
        exact_code_counts = _count_mapping(
            _metadata_value(
                parameters,
                metadata,
                "exact_industry_code_variant_counts",
            )
        )
        exact_codes_value = _metadata_value(
            parameters, metadata, "exact_industry_codes"
        )
        exact_codes = (
            dict(sorted(exact_codes_value.items()))
            if isinstance(exact_codes_value, dict)
            and all(
                isinstance(code, str) and isinstance(label, str)
                for code, label in exact_codes_value.items()
            )
            else None
        )
        required_metrics = (
            upstream_row_count,
            industry_group_row_count,
            raw_matching_row_count,
            deduplicated_variant_count,
            facility_count,
            conflicting_facility_count,
            current_regulation_facility_count,
            valid_coordinate_facility_count,
            exact_code_counts,
        )
        metrics_complete = all(metric is not None for metric in required_metrics)
        metrics_consistent = None
        if metrics_complete:
            assert upstream_row_count is not None
            assert industry_group_row_count is not None
            assert raw_matching_row_count is not None
            assert deduplicated_variant_count is not None
            assert facility_count is not None
            assert conflicting_facility_count is not None
            assert current_regulation_facility_count is not None
            assert valid_coordinate_facility_count is not None
            assert exact_code_counts is not None
            metrics_consistent = (
                set(exact_code_counts) == {"2611", "2612", "2613"}
                and sum(exact_code_counts.values()) == deduplicated_variant_count
                and 0
                <= conflicting_facility_count
                <= facility_count
                <= deduplicated_variant_count
                <= raw_matching_row_count
                <= industry_group_row_count
                <= upstream_row_count
                and current_regulation_facility_count <= facility_count
                and valid_coordinate_facility_count <= facility_count
            )
        moenv_candidate_scope = {
            "complete_refresh": parameters.get("complete_refresh") is True,
            "exact_filter_complete_within_archived_package": True,
            "source_assertion_interval_closure_enabled": (
                parameters.get("complete_refresh") is True
            ),
            "source_scope": _metadata_value(parameters, metadata, "source_scope"),
            "coverage": _metadata_value(parameters, metadata, "coverage"),
            "dataset_updated_at": _metadata_value(
                parameters, metadata, "dataset_updated_at"
            ),
            "dataset_updated_at_basis": _metadata_value(
                parameters, metadata, "dataset_updated_at_basis"
            ),
            "filter_version": _metadata_value(parameters, metadata, "filter_version"),
            "industry_group": _metadata_value(parameters, metadata, "industry_group"),
            "exact_industry_codes": exact_codes,
            "upstream_row_count": upstream_row_count,
            "industry_group_row_count": industry_group_row_count,
            "raw_matching_row_count": raw_matching_row_count,
            "deduplicated_variant_count": deduplicated_variant_count,
            "snapshot_candidate_count": deduplicated_variant_count,
            "facility_count": facility_count,
            "snapshot_facility_count": facility_count,
            "exact_industry_code_variant_counts": exact_code_counts,
            "current_regulation_count": current_regulation_facility_count,
            "current_regulation_facility_count": current_regulation_facility_count,
            "valid_coordinate_count": valid_coordinate_facility_count,
            "valid_coordinate_facility_count": valid_coordinate_facility_count,
            "conflicting_facility_count": conflicting_facility_count,
            "metric_definitions": {
                "current_regulation_facility_count": (
                    "facilities where any exact source environmental-control flag is 1; "
                    "not an operating-status count"
                ),
                "valid_coordinate_facility_count": (
                    "facilities with a valid invariant Taiwan WGS84 point across exact "
                    "source variants"
                ),
                "conflicting_facility_count": (
                    "facilities with more than one distinct exact source payload variant"
                ),
            },
            "metadata_metrics_complete": metrics_complete,
            "metadata_metrics_consistent": metrics_consistent,
            "current_candidate_entity_count": len(current_moenv_entities),
            "current_materialized_geometry_count": sum(
                item["geometry"] is not None for item in current_moenv_entities
            ),
            "source_retrieved_at": parameters.get(
                "source_retrieved_at", moenv_document["retrieved_at"]
            ),
            "retrieval_timestamp_basis": _metadata_value(
                parameters, metadata, "retrieval_timestamp_basis"
            ),
            "manifest_sha256": _metadata_value(parameters, metadata, "manifest_sha256"),
            "candidate_derivative": parameters.get("candidate_derivative"),
            "raw_retention": parameters.get("raw_archive"),
            "rights": parameters.get("rights"),
            "source_document_id": moenv_document["source_document_id"],
            "database_accepted_at": moenv_document["source_document_first_accepted_at"],
            "source_document_first_accepted_at": moenv_document[
                "source_document_first_accepted_at"
            ],
            "processing_run_accepted_at": moenv_document["processing_run_accepted_at"],
            "processing_run_id": moenv_document["processing_run_id"],
            "acceptance_timestamp_basis": parameters.get(
                "acceptance_timestamp_basis", "legacy_retrieval_clock"
            ),
        }
    factory_entities = [
        item
        for item in entity_view
        if item["stable_key"].startswith(TAIWAN_FACTORY_ENTITY_PREFIX)
    ]
    current_factory_entities = [
        item
        for item in factory_entities
        if str(item["entity_id"]) in current_claim_entity_ids
    ]
    factory_document = connection.execute(
        """
        SELECT documents.id AS source_document_id,
               documents.metadata_json, documents.retrieved_at,
               runs.started_at AS processing_run_accepted_at,
               runs.id AS processing_run_id, runs.parameters_json,
               MIN(runs.started_at) OVER (
                   PARTITION BY documents.id
               ) AS source_document_first_accepted_at
        FROM ingestion_runs AS runs
        JOIN source_documents AS documents
          ON documents.id = runs.input_document_id
        JOIN sources ON sources.id = documents.source_id
        JOIN source_families AS families ON families.id = sources.family_id
        WHERE families.stable_key = ?
          AND runs.status = 'succeeded'
          AND julianday(runs.started_at) <= julianday(?)
          AND date(
                json_extract(runs.parameters_json, '$.source_updated_at'),
                '+8 hours'
              ) <= date(?)
        ORDER BY date(
                     json_extract(runs.parameters_json, '$.source_updated_at'),
                     '+8 hours'
                 ) DESC,
                 runs.started_at DESC, documents.retrieved_at DESC,
                 documents.id DESC, runs.id DESC
        LIMIT 1
        """,
        (TAIWAN_FACTORY_FAMILY_KEY, recorded_at, as_of),
    ).fetchone()
    factory_candidate_scope = None
    if factory_document is not None:
        metadata_value = json.loads(factory_document["metadata_json"])
        parameters_value = json.loads(factory_document["parameters_json"])
        metadata = metadata_value if isinstance(metadata_value, dict) else {}
        parameters = parameters_value if isinstance(parameters_value, dict) else {}
        upstream_row_count = _nonnegative_int(
            _metadata_value(parameters, metadata, "upstream_row_count")
        )
        raw_matching_row_count = _nonnegative_int(
            _metadata_value(parameters, metadata, "raw_matching_row_count")
        )
        candidate_count = _nonnegative_int(
            _metadata_value(parameters, metadata, "candidate_count", "record_count")
        )
        business_number_count = _nonnegative_int(
            _metadata_value(
                parameters,
                metadata,
                "business_number_count",
                "distinct_unified_business_number_count",
            )
        )
        registration_status_counts = _count_mapping(
            _metadata_value(parameters, metadata, "registration_status_counts")
        )
        required_metrics = (
            upstream_row_count,
            raw_matching_row_count,
            candidate_count,
            business_number_count,
            registration_status_counts,
        )
        metrics_complete = all(metric is not None for metric in required_metrics)
        metrics_consistent = None
        if metrics_complete:
            assert upstream_row_count is not None
            assert raw_matching_row_count is not None
            assert candidate_count is not None
            assert business_number_count is not None
            assert registration_status_counts is not None
            metrics_consistent = (
                0
                <= business_number_count
                <= candidate_count
                == raw_matching_row_count
                <= upstream_row_count
                and sum(registration_status_counts.values()) == candidate_count
            )
        factory_candidate_scope = {
            "complete_refresh": parameters.get("complete_refresh") is True,
            "exact_filter_complete_within_archived_package": True,
            "source_assertion_interval_closure_enabled": (
                parameters.get("complete_refresh") is True
            ),
            "source_scope": _metadata_value(parameters, metadata, "source_scope"),
            "coverage": _metadata_value(parameters, metadata, "coverage"),
            "source_updated_at": _metadata_value(
                parameters, metadata, "source_updated_at"
            ),
            "source_updated_at_basis": _metadata_value(
                parameters, metadata, "source_updated_at_basis"
            ),
            "filter_version": _metadata_value(parameters, metadata, "filter_version"),
            "exact_principal_product_token": "261半導體",
            "upstream_row_count": upstream_row_count,
            "raw_matching_row_count": raw_matching_row_count,
            "snapshot_candidate_count": candidate_count,
            "distinct_unified_business_number_count": business_number_count,
            "registration_status_counts": registration_status_counts,
            "metadata_metrics_complete": metrics_complete,
            "metadata_metrics_consistent": metrics_consistent,
            "current_candidate_entity_count": len(current_factory_entities),
            "source_retrieved_at": parameters.get(
                "source_retrieved_at", factory_document["retrieved_at"]
            ),
            "retrieval_timestamp_basis": _metadata_value(
                parameters, metadata, "retrieval_timestamp_basis"
            ),
            "manifest_sha256": _metadata_value(parameters, metadata, "manifest_sha256"),
            "candidate_derivative": parameters.get("candidate_derivative"),
            "raw_retention": parameters.get("raw_archive"),
            "privacy": parameters.get("privacy"),
            "rights": parameters.get("rights"),
            "source_document_id": factory_document["source_document_id"],
            "database_accepted_at": factory_document[
                "source_document_first_accepted_at"
            ],
            "source_document_first_accepted_at": factory_document[
                "source_document_first_accepted_at"
            ],
            "processing_run_accepted_at": factory_document[
                "processing_run_accepted_at"
            ],
            "processing_run_id": factory_document["processing_run_id"],
            "acceptance_timestamp_basis": parameters.get(
                "acceptance_timestamp_basis", "legacy_retrieval_clock"
            ),
        }
    mof_entities = [
        item
        for item in entity_view
        if item["stable_key"].startswith(TAIWAN_MOF_ENTITY_PREFIX)
    ]
    current_mof_entities = [
        item
        for item in mof_entities
        if str(item["entity_id"]) in current_claim_entity_ids
    ]
    mof_document = connection.execute(
        """
        SELECT documents.id AS source_document_id,
               documents.metadata_json, documents.retrieved_at,
               runs.started_at AS processing_run_accepted_at,
               runs.id AS processing_run_id, runs.parameters_json,
               MIN(runs.started_at) OVER (
                   PARTITION BY documents.id
               ) AS source_document_first_accepted_at
        FROM ingestion_runs AS runs
        JOIN source_documents AS documents
          ON documents.id = runs.input_document_id
        JOIN sources ON sources.id = documents.source_id
        JOIN source_families AS families ON families.id = sources.family_id
        WHERE families.stable_key = ?
          AND runs.status = 'succeeded'
          AND julianday(runs.started_at) <= julianday(?)
          AND date(json_extract(runs.parameters_json, '$.publisher_date'))
              <= date(?)
        ORDER BY date(json_extract(runs.parameters_json, '$.publisher_date')) DESC,
                 runs.started_at DESC, documents.retrieved_at DESC,
                 documents.id DESC, runs.id DESC
        LIMIT 1
        """,
        (TAIWAN_MOF_FAMILY_KEY, recorded_at, as_of),
    ).fetchone()
    mof_tax_registration_scope = None
    if mof_document is not None:
        metadata_value = json.loads(mof_document["metadata_json"])
        parameters_value = json.loads(mof_document["parameters_json"])
        metadata = metadata_value if isinstance(metadata_value, dict) else {}
        parameters = parameters_value if isinstance(parameters_value, dict) else {}
        upstream_row_count = _nonnegative_int(
            _metadata_value(
                parameters,
                metadata,
                "upstream_row_count_after_header_including_publisher_date",
            )
        )
        active_tax_registration_row_count = _nonnegative_int(
            _metadata_value(parameters, metadata, "active_tax_registration_row_count")
        )
        allowlist_value = parameters.get("allowlist")
        allowlist_count = _nonnegative_int(
            allowlist_value.get("record_count")
            if isinstance(allowlist_value, dict)
            else None
        )
        matched_count = _nonnegative_int(
            _metadata_value(parameters, metadata, "matched_count", "record_count")
        )
        missing_count = _nonnegative_int(
            _metadata_value(parameters, metadata, "missing_count")
        )
        organization_type_counts = _count_mapping(
            _metadata_value(parameters, metadata, "organization_type_counts")
        )
        required_metrics = (
            upstream_row_count,
            active_tax_registration_row_count,
            allowlist_count,
            matched_count,
            missing_count,
            organization_type_counts,
        )
        metrics_complete = all(metric is not None for metric in required_metrics)
        metrics_consistent = None
        if metrics_complete:
            assert upstream_row_count is not None
            assert active_tax_registration_row_count is not None
            assert allowlist_count is not None
            assert matched_count is not None
            assert missing_count is not None
            assert organization_type_counts is not None
            metrics_consistent = (
                0
                <= matched_count
                <= active_tax_registration_row_count
                <= upstream_row_count
                and matched_count + missing_count == allowlist_count
                and sum(organization_type_counts.values()) == matched_count
            )
        mof_tax_registration_scope = {
            "complete_refresh": parameters.get("complete_refresh") is True,
            "exact_allowlist_filter_complete_within_archived_package": True,
            "source_assertion_interval_closure_enabled": (
                parameters.get("complete_refresh") is True
            ),
            "active_tax_registrations_only": True,
            "source_scope": _metadata_value(parameters, metadata, "source_scope"),
            "coverage": _metadata_value(parameters, metadata, "coverage"),
            "filter_version": _metadata_value(parameters, metadata, "filter_version"),
            "filter": parameters.get("filter"),
            "upstream_row_count_after_header_including_publisher_date": (
                upstream_row_count
            ),
            "active_tax_registration_row_count": (active_tax_registration_row_count),
            "allowlist_count": allowlist_count,
            "snapshot_matched_count": matched_count,
            "snapshot_missing_count": missing_count,
            "matched_organization_type_counts": organization_type_counts,
            "metadata_metrics_complete": metrics_complete,
            "metadata_metrics_consistent": metrics_consistent,
            "current_tax_unit_entity_count": len(current_mof_entities),
            "publisher_date": _metadata_value(parameters, metadata, "publisher_date"),
            "publisher_date_raw": _metadata_value(
                parameters, metadata, "publisher_date_raw"
            ),
            "publisher_date_basis": _metadata_value(
                parameters, metadata, "publisher_date_basis"
            ),
            "source_updated_at": _metadata_value(
                parameters, metadata, "source_updated_at"
            ),
            "source_updated_at_basis": _metadata_value(
                parameters, metadata, "source_updated_at_basis"
            ),
            "source_retrieved_at": parameters.get(
                "source_retrieved_at", mof_document["retrieved_at"]
            ),
            "retrieval_timestamp_basis": _metadata_value(
                parameters, metadata, "retrieval_timestamp_basis"
            ),
            "manifest_sha256": _metadata_value(parameters, metadata, "manifest_sha256"),
            "matched_derivative": parameters.get("matched_derivative"),
            "allowlist": parameters.get("allowlist"),
            "raw_retention": parameters.get("raw_archive"),
            "snapshot_lineage": parameters.get("snapshot_lineage"),
            "privacy": parameters.get("privacy"),
            "rights": parameters.get("rights"),
            "limitations": parameters.get("limitations"),
            "source_document_id": mof_document["source_document_id"],
            "database_accepted_at": mof_document["source_document_first_accepted_at"],
            "source_document_first_accepted_at": mof_document[
                "source_document_first_accepted_at"
            ],
            "processing_run_accepted_at": mof_document["processing_run_accepted_at"],
            "processing_run_id": mof_document["processing_run_id"],
            "acceptance_timestamp_basis": parameters.get(
                "acceptance_timestamp_basis", "legacy_retrieval_clock"
            ),
        }
    mapped_count = sum(item["geometry"] is not None for item in non_organizations)
    known_gaps = list(KNOWN_GAPS)
    if summary_view.get("schema_version", 0) >= 3:
        legacy_identity_gap = (
            "Cross-source entity resolution and organization-alias consolidation are not yet "
            "implemented."
        )
        reviewed_identity_gap = (
            "Cross-source entity resolution is limited to explicit reviewed assignments; broad "
            "organization-alias consolidation is not implemented."
        )
        known_gaps = [
            reviewed_identity_gap if gap == legacy_identity_gap else gap
            for gap in known_gaps
        ]
        gleif_entity_count = namespace_counts.get("gleif", 0)
        if gleif_entity_count:
            known_gaps.append(
                "GLEIF Level 1 coverage is limited to "
                f"{gleif_entity_count} explicitly reviewed exact-LEI source record"
                f"{'s' if gleif_entity_count != 1 else ''}; it is not a global legal-entity "
                "or facility census, and no Level 2 relationships are ingested."
            )
    moenv_present = any(
        family["source_family"] == TAIWAN_MOENV_FAMILY_KEY for family in source_families
    )
    if moenv_present:
        known_gaps.extend(
            (
                "Taiwan MOENV EMS_S_01 registry membership does not establish facility "
                "operation, production, ownership, operator relationships, or capacity.",
                "Taiwan MOENV release-from-environmental-control dates and record absence "
                "do not establish facility closure, inactivity, cancellation, or a "
                "production stop.",
                "The Taiwan MOENV exact industry-code filter is complete only within the "
                "verified archived EMS_S_01 package; it is not a national semiconductor "
                "facility census and has no measured national recall denominator.",
            )
        )
        if (
            moenv_candidate_scope is not None
            and not moenv_candidate_scope["metadata_metrics_complete"]
        ):
            known_gaps.append(
                "The accepted Taiwan MOENV run lacks one or more audited source-summary "
                "metrics; missing counts are reported as null rather than reconstructed "
                "from release materializations."
            )
        elif (
            moenv_candidate_scope is not None
            and moenv_candidate_scope["metadata_metrics_consistent"] is False
        ):
            known_gaps.append(
                "The accepted Taiwan MOENV run contains internally inconsistent audited "
                "source-summary counts; the recorded values are surfaced without repair."
            )
    factory_present = any(
        family["source_family"] == TAIWAN_FACTORY_FAMILY_KEY
        for family in source_families
    )
    if factory_present:
        known_gaps.extend(
            (
                "Taiwan registered-factory status is an administrative source field; it "
                "does not establish observed operation, production, output, utilization, "
                "yield, ownership, operator relationships, lifecycle, or capacity.",
                "Taiwan registered-factory record absence closes only a prior source-assertion "
                "interval after a verified complete same-filter refresh; it does not establish "
                "facility closure, inactivity, cancellation, or a production stop.",
                "The exact principal-product token 261半導體 is candidate evidence complete "
                "only within the retained national registered-factory archive. It is not a "
                "complete Taiwan semiconductor facility census and has no measured recall "
                "denominator.",
            )
        )
        if (
            factory_candidate_scope is not None
            and not factory_candidate_scope["metadata_metrics_complete"]
        ):
            known_gaps.append(
                "The accepted Taiwan registered-factory run lacks one or more audited "
                "source-summary metrics; missing counts are reported as null."
            )
        elif (
            factory_candidate_scope is not None
            and factory_candidate_scope["metadata_metrics_consistent"] is False
        ):
            known_gaps.append(
                "The accepted Taiwan registered-factory run contains internally inconsistent "
                "audited source-summary counts; recorded values are surfaced without repair."
            )
    mof_present = any(
        family["source_family"] == TAIWAN_MOF_FAMILY_KEY for family in source_families
    )
    if mof_present:
        known_gaps.extend(
            (
                "Taiwan MOF BGMOPEN1 rows are active tax-registration units, not a legal-company, "
                "parent, owner, operator, or facility census; head-office UBN is retained only as "
                "a contextual source scalar.",
                "Taiwan MOF record absence closes only a prior same-source assertion interval "
                "after a verified complete same-filter refresh; it does not establish legal "
                "closure, tax inactivity, facility closure, or a production stop.",
                "The Taiwan MOF exact UBN allowlist is derived from the accepted MOENV and "
                "registered-factory candidate snapshots. It is not a national semiconductor "
                "organization census and has no measured recall denominator.",
            )
        )
        if (
            mof_tax_registration_scope is not None
            and not mof_tax_registration_scope["metadata_metrics_complete"]
        ):
            known_gaps.append(
                "The accepted Taiwan MOF run lacks one or more audited source-summary metrics; "
                "missing counts are reported as null."
            )
        elif (
            mof_tax_registration_scope is not None
            and mof_tax_registration_scope["metadata_metrics_consistent"] is False
        ):
            known_gaps.append(
                "The accepted Taiwan MOF run contains internally inconsistent audited "
                "source-summary counts; recorded values are surfaced without repair."
            )
    taiwan_tax_relationship_review_scope = _taiwan_tax_relationship_review_scope(
        connection,
        recorded_at=recorded_at,
        claims=claim_view,
    )
    if taiwan_tax_relationship_review_scope is not None:
        known_gaps.append(
            "Reviewed registered-tax-unit references prove only exact selected-source UBN "
            "agreement; they do not establish identity, ownership, parentage, operator, "
            "activity, or lifecycle. Multiple source references may coexist."
        )
    report = {
        "format": "semiconductor-atlas-coverage-v1",
        "scope": "bounded_open_source_seed",
        "as_of": as_of,
        "recorded_at": recorded_at,
        "source_families": source_families,
        "source_family_count": len(source_families),
        "source_document_count": summary_view["source_document_count"],
        "entity_count": summary_view["entities_total"],
        "entity_namespace_counts": namespace_counts,
        "non_organization_entity_count": len(non_organizations),
        "entities_with_geometry": mapped_count,
        "geometry_coverage_fraction": (
            round(mapped_count / len(non_organizations), 6)
            if non_organizations
            else 0.0
        ),
        "current_claim_count": summary_view["current_claim_count"],
        "capacity_claims_by_basis": basis_counts,
        "epa_frs_candidate_scope": frs_candidate_scope,
        "known_gaps": known_gaps,
        "comparative_coverage_claim": None,
        "comparative_coverage_note": (
            "No superiority or parity claim is made without a lawfully licensed, normalized common benchmark."
        ),
    }
    if moenv_present:
        report["taiwan_moenv_candidate_scope"] = moenv_candidate_scope
    if factory_present:
        report["taiwan_factory_candidate_scope"] = factory_candidate_scope
    if mof_present:
        report["taiwan_mof_tax_registration_scope"] = mof_tax_registration_scope
    if taiwan_tax_relationship_review_scope is not None:
        report["taiwan_tax_relationship_review_scope"] = (
            taiwan_tax_relationship_review_scope
        )
    return report
