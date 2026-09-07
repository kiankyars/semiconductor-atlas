"""Prospective, source-bound milestone timing sidecars; never capacity calibration.

No function writes to the claim database. A caller must retain newly frozen sidecars
before separately reviewing outcomes. Local clocks and hashes are not external
timestamp attestations, proof of reviewer independence, or evidence authentication.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import database, models, repository


STUDY_FORMAT = "semiconductor-atlas-milestone-benchmark-study-v1"
VINTAGE_FORMAT = "semiconductor-atlas-milestone-benchmark-vintage-v1"
OUTCOME_FORMAT = "semiconductor-atlas-milestone-benchmark-outcomes-v1"
REPORT_FORMAT = "semiconductor-atlas-milestone-benchmark-report-v1"
EVENT_TYPES = {"production_start", "high_volume_production"}
PARTITIONS = {"train", "time_test", "geography_test"}
MAX_BYTES = 20_000_000
MAX_CASES = 1_000
BOUNDARIES = {
    "capacity_calibration": False, "calibration_established": False,
    "independence_verified": False, "publisher_complete": False,
    "canonical_claim_acceptance": False, "external_timestamp_attestation": False,
}
OUTCOME_RIGHTS = {"source_bodies_embedded": False,
                  "excerpts": "local_review_only_not_cleared_for_redistribution",
                  "redistribution_authorized": False}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4_000:
        raise ValueError(f"{field} requires bounded nonempty text")
    return value


def _keys(value, fields: set[str], field: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{field} requires exactly {sorted(fields)}")
    return value


def _clock(value) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        raise ValueError("clock must be a UTC Z timestamp with at most microseconds")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _day(value) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("interval bounds must be ISO calendar dates")
    return date.fromisoformat(value)


def _digest(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("expected lowercase SHA-256")
    return value


def canonical_bytes(value: dict) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("sidecar exceeds the 20 MB bound")
    return raw


def _hash(value) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else canonical_bytes(value)).hexdigest()


def _seal(value: dict) -> dict:
    payload = json.loads(canonical_bytes(value))
    return {**payload, "sha256": _hash(payload)}


def _load(value, format_: str) -> dict:
    if isinstance(value, bytes):
        if len(value) > MAX_BYTES:
            raise ValueError("sidecar exceeds the 20 MB bound")
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = item
            return result
        raw = value
        value = json.loads(raw, object_pairs_hook=unique)
        if canonical_bytes(value) != raw:
            raise ValueError("sidecar JSON must be canonical")
    else:
        value = json.loads(canonical_bytes(value))
    if not isinstance(value, dict) or value.get("format") != format_:
        raise ValueError("unsupported sidecar format")
    digest = value.pop("sha256", None)
    if _digest(digest) != _hash(value):
        raise ValueError("sidecar hash mismatch")
    return {**value, "sha256": digest}


def _codes() -> dict:
    paths = [Path(__file__), Path(database.__file__), Path(models.__file__), Path(repository.__file__)]
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _rows(connection, sql: str, parameters=()) -> list[dict]:
    cursor = connection.execute(sql, parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _one(connection, table: str, identifier: str) -> dict:
    rows = _rows(connection, f"SELECT * FROM {table} WHERE id=?", (identifier,))
    if len(rows) != 1:
        raise ValueError(f"missing or ambiguous {table} reference {identifier}")
    return rows[0]


@contextmanager
def _snapshot(connection):
    if connection.in_transaction:
        raise ValueError("finish the caller transaction before freezing a readonly snapshot")
    connection.execute("BEGIN")
    try:
        if database.schema_version(connection) < 5:
            raise ValueError("milestone benchmark requires existing schema 5")
        yield
    finally:
        connection.rollback()


def _before(value: str, cutoff: str, field: str):
    if _clock(value) > _clock(cutoff):
        raise ValueError(f"{field} is after the evidence cutoff")


def _publication(value, retrieved: str):
    if value is None:
        return
    if len(value) == 10:
        if _day(value) > _clock(retrieved).date():
            raise ValueError("publication date postdates retrieval")
    elif _clock(value) > _clock(retrieved):
        raise ValueError("publication clock postdates retrieval")


def _references(rows) -> list[dict]:
    if not isinstance(rows, list) or not rows or len(rows) > MAX_CASES:
        raise ValueError("claim references require a bounded nonempty array")
    identifiers = []
    for row in rows:
        _keys(row, {"claim_id", "value_sha256"}, "claim reference")
        identifiers.append(_text(row["claim_id"], "claim ID"))
        _digest(row["value_sha256"])
    if identifiers != sorted(set(identifiers)):
        raise ValueError("claim references must be unique and sorted")
    return rows


def _graph(connection, references: list[dict], cutoff: str, project: str) -> dict:
    """Read exact transitive provenance; no supplied hydrated claim is trusted."""
    refs = {row["claim_id"]: row["value_sha256"] for row in _references(references)}
    claims, series, entities, documents, records, runs, sources, families = {}, {}, {}, {}, {}, {}, {}, {}
    evidence, dependencies, run_documents = [], [], []
    visiting = set()

    def run(identifier):
        if identifier in runs:
            return
        row = _one(connection, "ingestion_runs", identifier)
        if row["status"] != "succeeded" or row["completed_at"] is None:
            raise ValueError("input lineage requires succeeded ingestion runs")
        for field in ("started_at", "completed_at"):
            _before(row[field], cutoff, f"run {field}")
        parameters = json.loads(row["parameters_json"])
        if not isinstance(parameters, dict) or _clock(row["completed_at"]) < _clock(row["started_at"]):
            raise ValueError("invalid input ingestion-run parameters or clocks")
        if parameters.get("accepted_at") is not None:
            _before(parameters["accepted_at"], cutoff, "actual run admission")
        runs[identifier] = row
        source(row["source_id"])
        links = _rows(connection, "SELECT * FROM ingestion_run_documents WHERE ingestion_run_id=? ORDER BY source_document_id,role", (identifier,))
        run_documents.extend(links)
        if row["input_document_id"] is not None and not any(
            link["source_document_id"] == row["input_document_id"] and link["role"] == "primary" for link in links
        ):
            raise ValueError("missing run primary-document linkage")
        for link in links:
            document(link["source_document_id"])

    def document(identifier):
        if identifier in documents:
            return
        row = _one(connection, "source_documents", identifier)
        _before(row["retrieved_at"], cutoff, "document retrieval")
        _publication(row["published_at"], row["retrieved_at"])
        _digest(row["content_sha256"])
        documents[identifier] = row
        source(row["source_id"])

    def source(identifier):
        row = _one(connection, "sources", identifier)
        family = _one(connection, "source_families", row["family_id"])
        _before(row["created_at"], cutoff, "source creation")
        _before(family["created_at"], cutoff, "source family creation")
        sources[row["id"]], families[family["id"]] = row, family

    def claim(identifier):
        if identifier in visiting:
            raise ValueError("cyclic claim lineage")
        if identifier in claims:
            return
        if len(claims) + len(visiting) >= 5_000:
            raise ValueError("claim lineage exceeds bounded population")
        visiting.add(identifier)
        row = _one(connection, "claim_versions", identifier)
        _before(row["recorded_at"], cutoff, "claim admission")
        if row["value_kind"] not in {"scalar", "milestone"}:
            raise ValueError("v1 inputs are scalar or milestone statements, not capacity models")
        stored = repository._stored_claim_value(connection, identifier, row["value_kind"])
        if repository.value_sha256(stored) != row["value_sha256"]:
            raise ValueError("stored typed value does not match its hash")
        if identifier in refs and refs[identifier] != row["value_sha256"]:
            raise ValueError("supplied input value hash differs from the actual database")
        if row["superseded_at"] is not None:
            if _clock(row["superseded_at"]) > _clock(cutoff):
                row["superseded_at"] = None
            elif identifier in refs:
                raise ValueError("root input was superseded by the cutoff")
        row["value"] = repository._value_payload(stored)
        s = _one(connection, "claim_series", row["series_id"])
        entity = _one(connection, "entities", s["subject_entity_id"])
        _before(s["created_at"], cutoff, "series creation")
        _before(entity["created_at"], cutoff, "entity creation")
        if identifier in refs and s["subject_entity_id"] != project:
            raise ValueError("root input belongs to another exact project")
        if row["created_by_run_id"] is None:
            raise ValueError("input claim requires explicit ingestion-run provenance")
        run(row["created_by_run_id"])
        if entity["created_by_run_id"] is not None:
            run(entity["created_by_run_id"])
        series[s["id"]], entities[entity["id"]] = s, entity
        links = _rows(connection, "SELECT * FROM claim_evidence WHERE claim_version_id=? ORDER BY id", (identifier,))
        parents = _rows(connection, "SELECT * FROM claim_dependencies WHERE claim_version_id=? ORDER BY depends_on_claim_version_id,dependency_kind", (identifier,))
        if not parents and not any(link["role"] == "support" for link in links):
            raise ValueError("input lineage has no supporting source evidence")
        for link in links:
            document(link["source_document_id"])
            if link["source_record_id"] is not None:
                record = _one(connection, "source_records", link["source_record_id"])
                if record["source_document_id"] != link["source_document_id"]:
                    raise ValueError("source-record/document mismatch")
                _before(record["observed_at"], cutoff, "source record observation")
                if models.source_record_payload_sha256(json.loads(record["payload_json"])) != record["record_sha256"]:
                    raise ValueError("source-record payload hash mismatch")
                run(record["ingestion_run_id"])
                if runs[record["ingestion_run_id"]]["source_id"] != documents[record["source_document_id"]]["source_id"]:
                    raise ValueError("source-record run belongs to another source")
                if not any(item["ingestion_run_id"] == record["ingestion_run_id"] and item["source_document_id"] == record["source_document_id"] and item["role"] == "source_record" for item in run_documents):
                    raise ValueError("missing source-record run linkage")
                records[record["id"]] = record
        evidence.extend(links)
        dependencies.extend(parents)
        for parent in parents:
            claim(parent["depends_on_claim_version_id"])
        claims[identifier] = row
        visiting.remove(identifier)

    for identifier in refs:
        claim(identifier)
    graph = {key: [value[identifier] for identifier in sorted(value)] for key, value in (
        ("claims", claims), ("series", series), ("entities", entities), ("documents", documents),
        ("records", records), ("runs", runs), ("sources", sources), ("families", families))}
    graph.update(evidence=sorted(evidence, key=lambda row: row["id"]),
                 dependencies=sorted(dependencies, key=lambda row: (row["claim_version_id"], row["depends_on_claim_version_id"], row["dependency_kind"])),
                 run_documents=sorted(run_documents, key=lambda row: (row["ingestion_run_id"], row["source_document_id"], row["role"])))
    return _seal(graph)


def _study_spec(value: dict) -> dict:
    _keys(value, {"study_id", "roster_scope", "reviewer", "reviewed_at", "time_split_at", "held_out_geographies", "roster"}, "study specification")
    for field in ("study_id", "roster_scope", "reviewer"):
        _text(value[field], field)
    _clock(value["reviewed_at"]); _clock(value["time_split_at"])
    geographies = value["held_out_geographies"]
    if not isinstance(geographies, list) or not geographies or geographies != sorted(set(geographies)):
        raise ValueError("held-out geographies must be a nonempty sorted unique list")
    for geography in geographies:
        _text(geography, "geography")
    roster = value["roster"]
    if not isinstance(roster, list) or not 1 <= len(roster) <= MAX_CASES:
        raise ValueError("study requires a bounded complete declared roster")
    cases, projects, groups, scopes = [], {}, {}, set()
    for row in roster:
        _keys(row, {"case_id", "project_entity_id", "project_stable_key", "phase", "event_type", "project_group", "geography", "partition", "scope_reason", "scope_claims"}, "roster case")
        for key in ("case_id", "project_entity_id", "project_stable_key", "phase", "project_group", "geography", "scope_reason"):
            _text(row[key], key)
        if row["event_type"] not in EVENT_TYPES or row["partition"] not in PARTITIONS:
            raise ValueError("unsupported exact event type or partition")
        if (row["partition"] == "geography_test") != (row["geography"] in geographies):
            raise ValueError("held-out geography cannot enter training or temporal test")
        scope = (row["project_entity_id"], row["phase"], row["event_type"])
        if scope in scopes:
            raise ValueError("duplicate project/phase/event benchmark unit")
        scopes.add(scope)
        membership = (row["partition"], row["geography"])
        group_membership = (row["project_group"], row["phase"], *membership)
        if row["project_entity_id"] in projects and projects[row["project_entity_id"]] != group_membership:
            raise ValueError("one project cannot cross split groups or phase boundaries")
        if row["project_group"] in groups and groups[row["project_group"]] != membership:
            raise ValueError("one project group cannot cross partitions/geographies")
        projects[row["project_entity_id"]], groups[row["project_group"]] = group_membership, membership
        _references(row["scope_claims"])
        cases.append(row["case_id"])
    if cases != sorted(set(cases)):
        raise ValueError("roster must contain unique sorted case IDs")
    return value


def _project_entity(connection, case):
    entity = _one(connection, "entities", case["project_entity_id"])
    if entity["kind"] != "project" or entity["stable_key"] != case["project_stable_key"]:
        raise ValueError("roster requires the exact source-native project identity")
    return entity


def freeze_study(connection: sqlite3.Connection, specification: dict) -> dict:
    """Seal a reviewed roster and split policy at the actual current clock."""
    codes = _codes()
    started = _now(); _clock(started)
    spec = _study_spec(json.loads(canonical_bytes(specification)))
    if _clock(spec["reviewed_at"]) > _clock(started):
        raise ValueError("scope review is in the future")
    with _snapshot(connection):
        snapshots = {}
        for row in spec["roster"]:
            entity = _project_entity(connection, row)
            snapshots[row["case_id"]] = _graph(connection, row["scope_claims"], started, entity["id"])
        frozen = _now()
        if not _clock(started) <= _clock(frozen) < _clock(spec["time_split_at"]):
            raise ValueError("study must freeze before the time split; actual clock cannot regress")
    if _codes() != codes:
        raise ValueError("benchmark code changed during study freeze")
    return _seal({"format": STUDY_FORMAT, "specification": spec, "evidence_cutoff_at": started,
                  "frozen_at": frozen, "scope_lineage": snapshots, "code_sha256": codes, "boundaries": BOUNDARIES})


def _study(value) -> dict:
    value = _load(value, STUDY_FORMAT)
    _keys(value, {"format", "specification", "evidence_cutoff_at", "frozen_at", "scope_lineage", "code_sha256", "boundaries", "sha256"}, "frozen study")
    spec = _study_spec(value["specification"])
    if not _clock(spec["reviewed_at"]) <= _clock(value["evidence_cutoff_at"]) <= _clock(value["frozen_at"]) < _clock(spec["time_split_at"]):
        raise ValueError("invalid study clock ordering")
    if value["code_sha256"] != _codes() or value["boundaries"] != BOUNDARIES:
        raise ValueError("study implementation or interpretation boundaries drifted")
    if set(value["scope_lineage"]) != {row["case_id"] for row in spec["roster"]}:
        raise ValueError("study scope lineage does not cover the roster")
    return value


def _prediction(value):
    _keys(value, {"low", "base", "high"}, "timing scenario")
    if not _day(value["low"]) <= _day(value["base"]) <= _day(value["high"]):
        raise ValueError("timing prediction must be ordered")


def _event_inputs(graph, references, case):
    roots = {ref["claim_id"] for ref in references}
    for claim in graph["claims"]:
        if claim["id"] in roots and claim["value_kind"] == "milestone":
            value = claim["value"]
            if value["milestone_type"] != case["event_type"] or value["status"] != "expected":
                raise ValueError("milestone input must be the exact expected event, without HVM aliases or realized outcomes")


def _split_inputs(graph, case, study):
    registered = {row["project_entity_id"]: row for row in study["specification"]["roster"]}
    for entity in graph["entities"]:
        if entity["kind"] != "project":
            continue
        source_case = registered.get(entity["id"])
        if source_case is None or any(source_case[key] != case[key] for key in (
            "project_group", "geography", "partition"
        )):
            raise ValueError("transitive input project violates the frozen split/project-group boundary")


def freeze_vintage(connection: sqlite3.Connection, study, *, evidence_cutoff_at: str,
                   horizon_end: str, predictions: list[dict], model_artifact: bytes,
                   configuration: dict) -> dict:
    """Seal supplied, unfitted timing scenarios now; never manufacture a past vintage.

    Predictions exactly cover the active split, including explicit abstentions.
    Actual database rows, transitive lineage, and every input clock are verified.
    The opaque model artifact is retained, not executed or authenticated by this API.
    """
    started = _now(); cutoff = _clock(evidence_cutoff_at)
    study = _study(study); spec = study["specification"]
    if not _clock(study["frozen_at"]) <= cutoff <= _clock(started):
        raise ValueError("vintage or input cutoff precedes registration or is in the future")
    if not isinstance(model_artifact, bytes) or not 1 <= len(model_artifact) <= 1_000_000:
        raise ValueError("model artifact must be bounded exact bytes")
    if not isinstance(configuration, dict):
        raise ValueError("configuration must be an explicit JSON object")
    configuration = json.loads(canonical_bytes(configuration))
    active_partitions = {"train"} if _clock(started) < _clock(spec["time_split_at"]) else {"time_test", "geography_test"}
    active = {row["case_id"]: row for row in spec["roster"] if row["partition"] in active_partitions}
    if not isinstance(predictions, list):
        raise ValueError("predictions must be an array")
    supplied = {}
    for row in json.loads(canonical_bytes(predictions)):
        _keys(row, {"case_id", "input_claims", "prediction", "abstention_reason"}, "prediction case")
        if row["case_id"] not in active or row["case_id"] in supplied:
            raise ValueError("prediction has duplicate, unknown, or inactive case ID")
        if not isinstance(row["input_claims"], list):
            raise ValueError("input claims must be an array, including for abstentions")
        if row["prediction"] is None:
            _text(row["abstention_reason"], "abstention reason")
            if row["input_claims"]:
                _references(row["input_claims"])
        else:
            _prediction(row["prediction"]); _references(row["input_claims"])
            if row["abstention_reason"] is not None:
                raise ValueError("a prediction cannot also abstain")
        supplied[row["case_id"]] = row
    if set(supplied) != set(active):
        raise ValueError("predictions must cover every active roster case, including abstentions")
    with _snapshot(connection):
        _verify_study_source(connection, study)
        cases = []
        for case in spec["roster"]:
            requested = supplied.get(case["case_id"])
            graph = None
            if requested and requested["input_claims"]:
                graph = _graph(connection, requested["input_claims"], evidence_cutoff_at, case["project_entity_id"])
                _event_inputs(graph, requested["input_claims"], case)
                _split_inputs(graph, case, study)
            cases.append({"case": case, "eligible": requested is not None,
                          "input": requested, "lineage": graph})
        frozen = _now()
        if _clock(frozen) < _clock(started) or (
            _clock(started) < _clock(spec["time_split_at"]) <= _clock(frozen)
        ):
            raise ValueError("actual clock regressed or crossed the split during freeze")
        if _day(horizon_end) <= _clock(frozen).date():
            raise ValueError("forecast horizon must follow actual freeze, not retrospective evidence time")
        for row in supplied.values():
            if row["prediction"] is not None and not (
                _clock(frozen).date() < _day(row["prediction"]["low"])
                <= _day(row["prediction"]["high"]) <= _day(horizon_end)
            ):
                raise ValueError("predicted interval must be strictly after actual freeze and within horizon")
    if _codes() != study["code_sha256"]:
        raise ValueError("benchmark code changed during vintage freeze")
    return _seal({"format": VINTAGE_FORMAT, "study": study, "study_sha256": study["sha256"],
        "frozen_at": frozen, "evidence_cutoff_at": evidence_cutoff_at, "horizon_end": horizon_end,
        "cases": cases, "model": {"artifact_base64": base64.b64encode(model_artifact).decode(),
            "artifact_sha256": _hash(model_artifact), "configuration": configuration,
            "configuration_sha256": _hash(configuration), "training_release": None,
            "status": "unfitted_timing_scenario_not_probability_quantiles"},
        "code_sha256": _codes(), "boundaries": BOUNDARIES})


def _verify_study_source(connection, study):
    for case in study["specification"]["roster"]:
        _project_entity(connection, case)
        graph = _graph(connection, case["scope_claims"], study["evidence_cutoff_at"], case["project_entity_id"])
        if graph != study["scope_lineage"][case["case_id"]]:
            raise ValueError("frozen study source lineage differs from the actual database")


def _vintage(value) -> dict:
    value = _load(value, VINTAGE_FORMAT)
    _keys(value, {"format", "study", "study_sha256", "frozen_at", "evidence_cutoff_at", "horizon_end", "cases", "model", "code_sha256", "boundaries", "sha256"}, "vintage")
    study = _study(value["study"])
    if value["study_sha256"] != study["sha256"] or value["code_sha256"] != _codes() or value["boundaries"] != BOUNDARIES:
        raise ValueError("vintage study/code/boundary binding mismatch")
    if not _clock(study["frozen_at"]) <= _clock(value["evidence_cutoff_at"]) <= _clock(value["frozen_at"]):
        raise ValueError("vintage clock precedes inputs or study")
    if _day(value["horizon_end"]) <= _clock(value["frozen_at"]).date():
        raise ValueError("vintage horizon is not prospective")
    model = _keys(value["model"], {"artifact_base64", "artifact_sha256", "configuration", "configuration_sha256", "training_release", "status"}, "model")
    try:
        raw = base64.b64decode(model["artifact_base64"], validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("invalid model artifact bytes") from error
    if not isinstance(model["configuration"], dict) or not 1 <= len(raw) <= 1_000_000 or model["artifact_sha256"] != _hash(raw) or model["configuration_sha256"] != _hash(model["configuration"]):
        raise ValueError("model artifact/configuration hash mismatch")
    if model["training_release"] is not None or model["status"] != "unfitted_timing_scenario_not_probability_quantiles":
        raise ValueError("v1 cannot attest fitted training or probability quantiles")
    roster = study["specification"]["roster"]
    if not isinstance(value["cases"], list) or len(value["cases"]) != len(roster):
        raise ValueError("vintage must retain the complete roster")
    partitions = {"train"} if _clock(value["frozen_at"]) < _clock(study["specification"]["time_split_at"]) else {"time_test", "geography_test"}
    for row, case in zip(value["cases"], roster):
        _keys(row, {"case", "eligible", "input", "lineage"}, "frozen case")
        if row["case"] != case or type(row["eligible"]) is not bool or row["eligible"] != (case["partition"] in partitions):
            raise ValueError("vintage roster/partition mismatch")
        if not row["eligible"]:
            if row["input"] is not None or row["lineage"] is not None:
                raise ValueError("inactive case contains a prediction")
            continue
        item = _keys(row["input"], {"case_id", "input_claims", "prediction", "abstention_reason"}, "frozen prediction")
        if item["case_id"] != case["case_id"]:
            raise ValueError("prediction/project case mismatch")
        if not isinstance(item["input_claims"], list):
            raise ValueError("input claims must be an array")
        if item["input_claims"]:
            _references(item["input_claims"])
        elif row["lineage"] is not None:
            raise ValueError("empty inputs cannot have a lineage graph")
        if item["prediction"] is None:
            _text(item["abstention_reason"], "abstention reason")
        else:
            _prediction(item["prediction"]); _references(item["input_claims"])
            if item["abstention_reason"] is not None or not _clock(value["frozen_at"]).date() < _day(item["prediction"]["low"]) <= _day(item["prediction"]["high"]) <= _day(value["horizon_end"]):
                raise ValueError("invalid prospective prediction interval")
    return value


def verify_vintage(connection: sqlite3.Connection, vintage) -> dict:
    """Replay source linkage at the frozen cutoff, tolerating genuinely later claims."""
    value = _vintage(vintage)
    with _snapshot(connection):
        _verify_study_source(connection, value["study"])
        for row in value["cases"]:
            if row["input"] is not None and row["input"]["input_claims"]:
                actual = _graph(connection, row["input"]["input_claims"], value["evidence_cutoff_at"], row["case"]["project_entity_id"])
                _event_inputs(actual, row["input"]["input_claims"], row["case"])
                _split_inputs(actual, row["case"], value["study"])
                if actual != row["lineage"]:
                    raise ValueError("frozen input lineage differs from actual database cutoff")
    return value


def _interval(value):
    _keys(value, {"low", "high", "literal"}, "reviewed event interval")
    _text(value["literal"], "source date literal")
    if _day(value["low"]) > _day(value["high"]):
        raise ValueError("outcome interval bounds are reversed")


def _outcome_rows(vintage: dict, outcomes, admitted: str, bodies: dict[str, bytes]) -> list[dict]:
    if not isinstance(bodies, dict) or not all(isinstance(raw, bytes) for raw in bodies.values()):
        raise ValueError("outcome review requires caller-supplied local body bytes")
    if not isinstance(outcomes, list) or len(outcomes) != len(vintage["cases"]):
        raise ValueError("outcomes must retain every roster case, including unknowns and inactive cases")
    results = []
    for row, frozen in zip(outcomes, vintage["cases"]):
        _keys(row, {"case_id", "project_entity_id", "phase", "event_type", "status", "event_interval", "censor_at", "cancellation_interval", "reason", "evidence"}, "outcome case")
        case = frozen["case"]
        if any(row[field] != case[field] for field in ("case_id", "project_entity_id", "phase", "event_type")):
            raise ValueError("outcome exact project/phase/event identity mismatch")
        _text(row["reason"], "outcome reason")
        status = row["status"]
        if status not in {"observed", "unknown", "right_censored", "cancelled"}:
            raise ValueError("unsupported outcome status")
        interval = row["event_interval"] if status == "observed" else row["cancellation_interval"] if status == "cancelled" else None
        if interval is not None:
            _interval(interval)
        if ((status == "observed") != (row["event_interval"] is not None)
                or (status == "cancelled") != (row["cancellation_interval"] is not None)
                or (status == "right_censored") != (row["censor_at"] is not None)):
            raise ValueError("outcome dates must match observed/censored/cancelled semantics")
        event_high = _day(interval["high"]) if interval is not None else _day(row["censor_at"]) if row["censor_at"] is not None else None
        evidence = row["evidence"]
        if not isinstance(evidence, list) or len(evidence) > 20 or (status != "unknown" and not evidence):
            raise ValueError("resolved/censored outcomes require bounded source evidence")
        seen = set()
        for item in evidence:
            _keys(item, {"document_sha256", "document_url", "published_at", "retrieved_at", "start", "end", "excerpt", "locator"}, "outcome evidence")
            digest = _digest(item["document_sha256"])
            raw = bodies.get(digest)
            if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_BYTES or _hash(raw) != digest:
                raise ValueError("outcome body is missing or hash-mismatched")
            _text(item["locator"], "evidence locator"); _text(item["excerpt"], "evidence excerpt")
            if len(item["excerpt"].encode("utf-8")) > 1_000:
                raise ValueError("outcome excerpt exceeds the 1,000 UTF-8 byte local-review bound")
            url = urlsplit(_text(item["document_url"], "source URL"))
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("outcome evidence requires public HTTPS without query or credentials")
            _before(item["retrieved_at"], admitted, "outcome retrieval")
            _publication(item["published_at"], item["retrieved_at"])
            if event_high is not None and event_high > _clock(item["retrieved_at"]).date():
                raise ValueError("reported realized/censor date postdates source retrieval")
            start, end = item["start"], item["end"]
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw):
                raise ValueError("invalid UTF-8 evidence byte span")
            if raw[start:end] != item["excerpt"].encode("utf-8"):
                raise ValueError("outcome excerpt differs from exact retained UTF-8 bytes")
            key = (digest, start, end)
            if key in seen:
                raise ValueError("duplicate outcome evidence span")
            seen.add(key)
        if interval is not None and not any(interval["literal"] in item["excerpt"] for item in evidence):
            raise ValueError("event date literal must occur in retained outcome evidence")
        results.append(row)
    used = {item["document_sha256"] for row in results for item in row["evidence"]}
    if set(bodies) != used or sum(len(raw) for raw in bodies.values()) > MAX_BYTES:
        raise ValueError("outcome bodies must exactly match bounded evidence references")
    return results


def review_outcomes(vintage, *, reviewed_by: str, prior_exposure: str,
                    outcomes: list[dict], bodies: dict[str, bytes]) -> dict:
    """Admit a separate complete outcome review now; caller judges source semantics."""
    started = _now(); value = _vintage(vintage)
    _text(reviewed_by, "reviewer"); _text(prior_exposure, "prior exposure disclosure")
    if _clock(started) <= _clock(value["frozen_at"]):
        raise ValueError("outcome review must follow the frozen prediction vintage")
    rows = _outcome_rows(value, json.loads(canonical_bytes(outcomes)), started, bodies)
    admitted = _now()
    if _clock(admitted) < _clock(started):
        raise ValueError("outcome admission clock regressed")
    if _codes() != value["code_sha256"]:
        raise ValueError("benchmark code changed during outcome review")
    return _seal({"format": OUTCOME_FORMAT, "vintage_sha256": value["sha256"],
        "reviewed_by": reviewed_by, "prior_exposure": prior_exposure,
        "review_started_at": started, "admitted_at": admitted, "outcomes": rows,
        "source_bodies": [{"sha256": key, "bytes": len(raw)} for key, raw in sorted(bodies.items())],
        "rights": OUTCOME_RIGHTS, "boundaries": BOUNDARIES, "code_sha256": _codes()})


def score(connection: sqlite3.Connection, vintage, outcome_review, *, bodies: dict[str, bytes]) -> dict:
    """Report interval-aware milestone timing diagnostics, never calibrated probabilities."""
    vintage = verify_vintage(connection, vintage)
    review = _load(outcome_review, OUTCOME_FORMAT)
    _keys(review, {"format", "vintage_sha256", "reviewed_by", "prior_exposure", "review_started_at", "admitted_at", "outcomes", "source_bodies", "rights", "boundaries", "code_sha256", "sha256"}, "outcome review")
    if review["vintage_sha256"] != vintage["sha256"] or review["boundaries"] != BOUNDARIES or review["code_sha256"] != _codes():
        raise ValueError("outcome review vintage/code/boundary mismatch")
    if review["rights"] != OUTCOME_RIGHTS:
        raise ValueError("outcome excerpt/source-body rights boundary drifted")
    _text(review["reviewed_by"], "reviewer"); _text(review["prior_exposure"], "prior exposure")
    if not _clock(vintage["frozen_at"]) < _clock(review["review_started_at"]) <= _clock(review["admitted_at"]) <= _clock(_now()):
        raise ValueError("outcome review chronology is invalid")
    if not isinstance(bodies, dict) or not all(isinstance(raw, bytes) for raw in bodies.values()):
        raise ValueError("scoring requires caller-supplied local outcome body bytes")
    source_manifest = [{"sha256": key, "bytes": len(raw)} for key, raw in sorted(bodies.items())]
    if not isinstance(review["source_bodies"], list):
        raise ValueError("outcome source manifest must be an array")
    for reference in review["source_bodies"]:
        _keys(reference, {"sha256", "bytes"}, "outcome source reference")
        _digest(reference["sha256"])
        if type(reference["bytes"]) is not int or not 1 <= reference["bytes"] <= MAX_BYTES:
            raise ValueError("outcome source reference requires a bounded integer byte count")
    if review["source_bodies"] != source_manifest:
        raise ValueError("local outcome bodies differ from the frozen reference-only source manifest")
    outcomes = _outcome_rows(vintage, review["outcomes"], review["review_started_at"], bodies)
    rows, errors, counts = [], [], Counter()
    for frozen, outcome in zip(vintage["cases"], outcomes):
        prediction = frozen["input"]["prediction"] if frozen["eligible"] else None
        status = outcome["status"]
        classification = "inactive_partition" if not frozen["eligible"] else "abstained" if prediction is None else status
        interval = outcome["event_interval"]
        bounds = coverage = None
        if classification == "observed":
            if _day(interval["low"]) <= _clock(vintage["frozen_at"]).date():
                classification = "pre_vintage_or_straddling_outcome"
            elif _day(interval["low"]) > _day(vintage["horizon_end"]):
                classification = "event_after_horizon"
            elif _day(interval["high"]) > _day(vintage["horizon_end"]):
                classification = "outcome_straddles_horizon"
            else:
                lower = (_day(prediction["base"]) - _day(interval["high"])).days
                upper = (_day(prediction["base"]) - _day(interval["low"])).days
                bounds = {"low": lower, "high": upper, "sign": "positive_means_predicted_later"}
                coverage = {"guaranteed": _day(prediction["low"]) <= _day(interval["low"]) <= _day(interval["high"]) <= _day(prediction["high"]),
                            "possible": _day(prediction["low"]) <= _day(interval["high"]) and _day(interval["low"]) <= _day(prediction["high"])}
                if lower == upper:
                    errors.append(lower)
        elif classification == "right_censored" and _day(outcome["censor_at"]) <= _clock(vintage["frozen_at"]).date():
            classification = "pre_vintage_censor"
        counts[classification] += 1
        rows.append({"case_id": outcome["case_id"], "partition": frozen["case"]["partition"],
                     "project_group": frozen["case"]["project_group"], "geography": frozen["case"]["geography"],
                     "outcome_status": status, "classification": classification,
                     "signed_error_days": bounds, "scenario_interval_coverage": coverage})
    partitions = {}
    for partition in sorted(PARTITIONS):
        members = [row for row in rows if row["partition"] == partition]
        exact = [row["signed_error_days"]["low"] for row in members if row["signed_error_days"] is not None
                 and row["signed_error_days"]["low"] == row["signed_error_days"]["high"]]
        partitions[partition] = {"roster_cases": len(members),
            "counts": dict(sorted(Counter(row["classification"] for row in members).items())),
            "exact_date_error_denominator": len(exact),
            "exact_date_mean_absolute_error_days": sum(abs(value) for value in exact) / len(exact) if exact else None,
            "exact_date_bias_days": sum(exact) / len(exact) if exact else None}
    return _seal({"format": REPORT_FORMAT, "vintage_sha256": vintage["sha256"], "outcomes_sha256": review["sha256"],
        "roster_cases": len(rows), "eligible_cases": sum(row["eligible"] for row in vintage["cases"]),
        "counts": dict(sorted(counts.items())), "cases": rows, "partitions": partitions,
        "exact_date_error_denominator": len(errors),
        "exact_date_mean_absolute_error_days": sum(abs(error) for error in errors) / len(errors) if errors else None,
        "exact_date_bias_days": sum(errors) / len(errors) if errors else None,
        "probability_calibration": None, "boundaries": BOUNDARIES,
        "limitations": ["Complete only within the declared frozen roster; scope/geography are reviewed assertions.",
            "Unfitted supplied timing scenarios; model execution and lack of training are not authenticated.",
            "Unknown, censored, cancelled, abstained, inactive and pre-vintage cases remain in the denominator.",
            "Source date semantics and outcome support require substantive review; exact spans alone do not prove truth.",
            "No midpoint is invented for source intervals; exact-date metrics condition on their reported denominator."]})


def _parent_descriptor(destination: Path) -> int:
    descriptor = os.open(destination.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in destination.parent.parts[1:]:
            next_descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _assert_parent_path(destination: Path, descriptor: int) -> None:
    try:
        current = _parent_descriptor(destination)
    except OSError as error:
        raise ValueError("sidecar parent pathname changed during publication") from error
    try:
        expected, actual = os.fstat(descriptor), os.fstat(current)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("sidecar parent pathname changed during publication")
    finally:
        os.close(current)


def write_new(path: str | Path, artifact: dict) -> None:
    """Stage, verify and publish with a no-replace hard link; never delete the target.

    Failure after publication can leave a complete target requiring caller verification.
    Cleanup is limited to the staging inode created by this invocation.
    """
    raw = canonical_bytes(artifact)
    destination = Path(path).absolute()
    if ".." in destination.parts:
        raise ValueError("sidecar destination cannot traverse parent components")
    parent = _parent_descriptor(destination)
    try:
        staging = ".milestone-benchmark-" + secrets.token_hex(16)
        descriptor = os.open(staging, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        identity = os.fstat(descriptor)
        try:
            pending = memoryview(raw)
            while pending:
                written = os.write(descriptor, pending)
                if written <= 0:
                    raise OSError("short sidecar staging write")
                pending = pending[written:]
            os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            retained = bytearray()
            while len(retained) <= len(raw):
                block = os.read(descriptor, min(65_536, len(raw) + 1 - len(retained)))
                if not block:
                    break
                retained.extend(block)
            actual = os.stat(staging, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (identity.st_dev, identity.st_ino)
                    or bytes(retained) != raw):
                raise ValueError("sidecar staging inode or exact bytes changed")
            _assert_parent_path(destination, parent)
            os.link(staging, destination.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            published = os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
            if (published.st_dev, published.st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError("sidecar target changed during publication")
            os.fsync(parent)
            _assert_parent_path(destination, parent)
            published = os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
            if (published.st_dev, published.st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError("sidecar target changed after publication")
        finally:
            try:
                staged = os.stat(staging, dir_fd=parent, follow_symlinks=False)
                if (staged.st_dev, staged.st_ino) == (identity.st_dev, identity.st_ino):
                    os.unlink(staging, dir_fd=parent)
            except FileNotFoundError:
                pass
            finally:
                os.close(descriptor)
    finally:
        os.close(parent)
