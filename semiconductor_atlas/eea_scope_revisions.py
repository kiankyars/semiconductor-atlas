"""Append-only EEA scope decisions, separate from retained source statements."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from types import SimpleNamespace

from . import ingest_eea_industrial as seed
from .database import schema_version
from .eea_industrial_review import parse_eea_industrial_review_bytes
from .models import (
    ClaimKind, ClaimVersion, Entity, EntityKind, IngestionRun, IngestionRunDocument,
    IngestionStatus, Source, SourceDocument, SourceFamily, SourceRecord,
)
from .repository import insert_claim, stable_id, validate_database
from .service import source_claim_records


VERSION = "eea-industrial-scope-revision-v3"
REPORT_FORMAT = "semiconductor-atlas-eea-scoped-source-statements-v1"
_VERSIONS = {seed.LEGACY_IMPORTER_VERSION, seed.IMPORTER_VERSION, VERSION}
_UNKNOWN_NOTES = (
    " Claim-effective time and calibrated claim confidence are unknown; "
    "publication is document metadata only."
)
_now = seed._now


def _raw(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
            + "\n").encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else _raw(value)).hexdigest()


def _run_hash(run: dict) -> str:
    return _hash({key: value for key, value in run.items() if key != "review"})


def _stored_review(parameters: dict, queue):
    metadata = parameters["review_artifact"]
    payload = {key: metadata[key] for key in (
        "candidate_queue_sha256", "format", "knowledge_cutoff_at", "reviewed_at",
        "reviewed_by", "snapshot_manifest_sha256", "source_candidate_sha256",
    )}
    payload.update(candidate_count=len(parameters["decisions"]),
                   decisions=parameters["decisions"], filter_version=parameters["filter_version"])
    raw = _raw(payload)
    if metadata["sha256"] != _hash(raw) or metadata["bytes"] != len(raw):
        raise ValueError("EEA stored review bytes/hash differ")
    return parse_eea_industrial_review_bytes(raw, queue=queue)


def _snapshot_metadata(parameters: dict):
    values = dict(parameters["snapshot"])
    return SimpleNamespace(**values)


def _documents(parameters: dict) -> tuple[str, str]:
    snapshot = parameters["snapshot"]
    source_id = stable_id("source", seed.SOURCE_KEY)
    return (
        stable_id("source-document", source_id, seed.EEA_RAW_URL,
                  snapshot["retrieved_at"], snapshot["raw_sha256"], "raw-accdb"),
        stable_id("source-document", source_id, seed.EEA_DOI_URL,
                  snapshot["accepted_at"], snapshot["candidate_sha256"], "candidate-derivative"),
    )


def _verify_source_metadata(connection, parameters: dict) -> None:
    snapshot = _snapshot_metadata(parameters)
    accepted_at = parameters["accepted_at"]
    family_id = stable_id("source-family", seed.SOURCE_FAMILY_KEY)
    source_id = stable_id("source", seed.SOURCE_KEY)
    raw_document, document = _documents(parameters)
    clocks = {}
    for table, identifier in (("source_families", family_id), ("sources", source_id)):
        clock = seed._existing_created_at(connection, table, identifier, accepted_at)
        seed._canonical_timestamp(clock, "EEA source creation")
        if seed._clock(clock) > seed._clock(accepted_at):
            raise ValueError("EEA source metadata was created after genesis")
        clocks[table] = clock
    expected = (
        SourceFamily(family_id, seed.SOURCE_FAMILY_KEY, seed.SOURCE_FAMILY_NAME,
            clocks["source_families"], description=(
                "Official European industrial-reporting records retained as "
                "source-native facility evidence after explicit scope review.")),
        Source(source_id, family_id, seed.SOURCE_KEY, seed.SOURCE_NAME,
            seed.SOURCE_PUBLISHER, seed.EEA_DATASET_URL, clocks["sources"],
            license=seed.EEA_LICENSE),
        SourceDocument(raw_document, source_id, seed.EEA_RAW_URL,
            "EEA Industrial Reporting v16 official Access database", snapshot.retrieved_at,
            snapshot.raw_sha256, published_at=seed.EEA_PUBLICATION_DATE,
            media_type="application/msaccess", license=seed.EEA_LICENSE, metadata={
                "artifact_kind": "retained_official_relational_source",
                "attribution": seed.EEA_ATTRIBUTION, "dataset_id": seed.EEA_DATASET_ID,
                "edition": seed.EEA_EDITION, "retention_path": seed.EEA_RAW_PATH,
                "snapshot_manifest_sha256": snapshot.manifest_sha256}),
        SourceDocument(document, source_id, seed.EEA_DOI_URL,
            "EEA Industrial Reporting v16 semiconductor candidate derivative", snapshot.accepted_at,
            snapshot.candidate_sha256, published_at=seed.EEA_PUBLICATION_DATE,
            media_type="application/x-ndjson", license=seed.EEA_LICENSE, metadata={
                "artifact_kind": "deterministic_privacy_minimized_candidate_derivative",
                "attribution": seed.EEA_ATTRIBUTION, "dataset_id": seed.EEA_DATASET_ID,
                "edition": seed.EEA_EDITION, "filter_version": seed.EEA_FILTER_VERSION,
                "record_count": snapshot.candidate_count,
                "retention_path": seed.EEA_CANDIDATE_FILENAME,
                "snapshot_manifest_sha256": snapshot.manifest_sha256,
                "upstream_document_id": raw_document}),
    )
    for value in expected:
        seed._persist(connection, value, replayed=True)


def _verify_scalar(connection, claim_id: str, value) -> None:
    if value.scalar_type != seed.ScalarType.TEXT:
        raise ValueError("EEA scope materialization requires an exact text scalar")
    row = connection.execute("SELECT * FROM scalar_values WHERE claim_version_id = ?",
                             (claim_id,)).fetchone()
    expected = {"claim_version_id": claim_id, "value_kind": "scalar",
                "scalar_type": "text", "text_value": value.value,
                "number_value": None, "integer_value": None, "boolean_value": None,
                "unit": value.unit}
    if row is None or dict(row) != expected:
        raise ValueError("EEA materialization differs from its exact scalar payload")
    registry = connection.execute("SELECT * FROM claim_values WHERE claim_version_id = ?",
                                  (claim_id,)).fetchone()
    if registry is None or dict(registry) != {"claim_version_id": claim_id, "value_kind": "scalar"}:
        raise ValueError("EEA materialization differs from its scalar registry")
    if connection.execute("SELECT superseded_at FROM claim_versions WHERE id = ?",
                          (claim_id,)).fetchone()[0] is not None:
        raise ValueError("EEA scope history cannot supersede retained source statements")


def _materialization(connection, run: dict, candidates: dict, queue, *, write: bool) -> dict:
    """Create or verify only a candidate's first admission; never reinsert reused facts."""
    parameters = json.loads(run["parameters_json"])
    raw_document, document = _documents(parameters)
    accepted_at = parameters["accepted_at"]
    is_legacy = run["code_version"] == seed.LEGACY_IMPORTER_VERSION
    identities, records, claims, result = [], [], [], {}
    queue_by_id = {item.candidate_id: item for item in queue.candidates}
    for candidate_id, payload in sorted(candidates.items()):
        queued = queue_by_id[candidate_id]
        if seed.source_record_payload_sha256(payload) != queued.source_candidate_sha256:
            raise ValueError("EEA materialization does not match the bound candidate")
        if payload.get("facility_inspire_id") != queued.facility_inspire_id:
            raise ValueError("EEA materialization changes source identity")
        key = seed.ENTITY_KEY_PREFIX + queued.facility_inspire_id
        entity_id = stable_id("entity", key)
        record_id = stable_id("source-record", run["id"], key)
        seed._persist(connection, SourceRecord(record_id, run["id"], document, key,
            parameters["snapshot"]["accepted_at"], queued.source_candidate_sha256,
            payload=payload), replayed=not write)
        seed._persist(connection, Entity(entity_id, EntityKind.FACILITY, key, accepted_at,
            display_name=None, created_by_run_id=run["id"]), replayed=not write)
        valid_from, specs = seed._source_specs(payload, document_id=document, record_id=record_id)
        candidate_claims = []
        for spec in specs:
            series_id, _ = seed._ensure_series(connection, entity_id=entity_id,
                entity_key=key, predicate=spec.predicate, dimension=spec.dimension,
                created_at=accepted_at, replayed=not write)
            if connection.execute("SELECT created_at FROM claim_series WHERE id = ?",
                                  (series_id,)).fetchone()[0] != accepted_at:
                raise ValueError("EEA source series creation differs from first admission")
            claim_id = stable_id("claim-version", run["code_version"], series_id,
                                 record_id, seed.EEA_PUBLICATION_DATE)
            if not write and connection.execute(
                    "SELECT 1 FROM claim_versions WHERE id = ?", (claim_id,)).fetchone() is None:
                raise ValueError("EEA materialization is missing an immutable claim")
            insert_claim(connection, ClaimVersion(claim_id, series_id,
                valid_from if is_legacy else None, accepted_at, ClaimKind.SOURCE_STATEMENT,
                spec.method, 1.0 if is_legacy else None, created_by_run_id=run["id"],
                notes=spec.notes if is_legacy else spec.notes + _UNKNOWN_NOTES),
                spec.value, evidence=spec.evidence)
            _verify_scalar(connection, claim_id, spec.value)
            candidate_claims.append(claim_id)
        identities.append(entity_id)
        records.append(record_id)
        claims.extend(candidate_claims)
        result[candidate_id] = {"entity_id": entity_id, "source_record_id": record_id,
            "claim_ids": sorted(candidate_claims), "first_admission_run_id": run["id"],
            "first_admitted_at": accepted_at}
    seed._assert_semantic_postconditions(connection, run_id=run["id"],
        raw_document_id=raw_document, candidate_document_id=document,
        expected_entity_ids=identities, expected_record_ids=records,
        expected_claim_count=len(claims), importer_version=run["code_version"])
    return result


def _history(connection, queue) -> tuple[list[dict], dict]:
    if schema_version(connection) < 5:
        raise ValueError("EEA scope revisions require an existing schema-5 database")
    source_id = stable_id("source", seed.SOURCE_KEY)
    runs = [dict(row) for row in connection.execute(
        "SELECT * FROM ingestion_runs WHERE source_id = ?", (source_id,))]
    if not runs:
        raise ValueError("EEA scope revision requires a verified v1/v2 genesis import")
    if any(run["code_version"] not in _VERSIONS for run in runs):
        raise ValueError("EEA history has an unsupported ingestion route")
    genesis = [run for run in runs if run["code_version"] != VERSION]
    if len(genesis) != 1:
        raise ValueError("EEA history must have exactly one v1/v2 genesis")
    by_id = {run["id"]: run for run in runs}
    chain, materialized, seen_reviews = [], {}, set()
    previous = None
    current = genesis[0]
    base_snapshot = None
    while current is not None:
        parameters = json.loads(current["parameters_json"])
        review = _stored_review(parameters, queue)
        accepted = seed._canonical_timestamp(parameters["accepted_at"], "stored acceptance")
        started = seed._canonical_timestamp(current["started_at"], "stored start")
        completed = seed._canonical_timestamp(current["completed_at"], "stored completion")
        if (current["status"] != "succeeded" or current["error"] is not None
                or not seed._clock(started) < seed._clock(completed)
                or not seed._clock(started) <= seed._clock(accepted) <= seed._clock(completed)
                or seed._clock(accepted) < seed._clock(review.reviewed_at)
                or seed._clock(accepted) < seed._clock(review.knowledge_cutoff_at)):
            raise ValueError("EEA run has invalid admission clocks/status")
        if review.raw_sha256 in seen_reviews:
            raise ValueError("EEA review artifact was admitted more than once")
        seen_reviews.add(review.raw_sha256)
        if base_snapshot is None:
            base_snapshot = parameters["snapshot"]
            if (base_snapshot["manifest_sha256"] != queue.snapshot_manifest_sha256
                    or base_snapshot["candidate_sha256"] != queue.source_candidate_sha256
                    or base_snapshot["candidate_count"] != queue.source_candidate_count
                    or base_snapshot["accepted_at"] != queue.snapshot_accepted_at):
                raise ValueError("EEA genesis snapshot does not match the exact candidate queue")
            _verify_source_metadata(connection, parameters)
        elif parameters["snapshot"] != base_snapshot:
            raise ValueError("scope revision cannot change the EEA snapshot")
        expected_parameters = seed._run_parameters(snapshot=_snapshot_metadata(parameters),
            queue=queue, review=review, accepted_at=accepted, importer_version=current["code_version"])
        selected = {row.candidate_id for row in review.decisions if row.outcome == "accept_in_scope"}
        new_ids = selected - materialized.keys()
        if previous is None:
            expected_id = stable_id("ingestion-run", current["code_version"],
                queue.snapshot_manifest_sha256, queue.raw_sha256, review.raw_sha256)
        else:
            prior_review = previous["review"]
            prior_parameters = json.loads(previous["parameters_json"])
            if (seed._clock(accepted) <= seed._clock(prior_parameters["accepted_at"])
                    or seed._clock(review.reviewed_at) <= seed._clock(prior_review.reviewed_at)
                    or seed._clock(review.knowledge_cutoff_at) < seed._clock(prior_review.knowledge_cutoff_at)):
                raise ValueError("EEA revision clocks do not advance")
            expected_id = stable_id("ingestion-run", VERSION, queue.snapshot_manifest_sha256,
                queue.raw_sha256, review.raw_sha256, previous["id"])
            expected_parameters["scope_revision"] = {
                "format": VERSION, "genesis_run_id": genesis[0]["id"],
                "genesis_run_sha256": _run_hash(genesis[0]),
                "previous_run_id": previous["id"], "previous_run_sha256": _run_hash(previous),
                "new_candidate_ids": sorted(new_ids),
                "retained_materializations": materialized,
                "review_utf8": review.raw_bytes.decode("utf-8"),
            }
        if (current["id"] != expected_id or parameters != expected_parameters
                or current["parameters_json"] != seed._canonical_json(expected_parameters)):
            raise ValueError("EEA revision parameters or predecessor binding differ")
        raw_document, document = _documents(parameters)
        if {row[0] for row in connection.execute(
                "SELECT id FROM source_documents WHERE source_id = ?", (source_id,))} != {raw_document, document}:
            raise ValueError("EEA history has unexpected source documents")
        if current["input_document_id"] != document:
            raise ValueError("EEA revision has the wrong primary document")
        for document_id, digest in ((raw_document, base_snapshot["raw_sha256"]),
                                    (document, queue.source_candidate_sha256)):
            row = connection.execute("SELECT * FROM source_documents WHERE id = ?", (document_id,)).fetchone()
            if row is None or row["source_id"] != source_id or row["content_sha256"] != digest:
                raise ValueError("EEA history source document binding differs")
        by_candidate = {}
        queue_by_id = {row.candidate_id: row for row in queue.candidates}
        for candidate_id in sorted(new_ids):
            key = seed.ENTITY_KEY_PREFIX + queue_by_id[candidate_id].facility_inspire_id
            row = connection.execute("SELECT payload_json FROM source_records WHERE id = ?",
                (stable_id("source-record", current["id"], key),)).fetchone()
            if row is None:
                raise ValueError("EEA scope history lacks first-admission source record")
            by_candidate[candidate_id] = json.loads(row["payload_json"])
        materialized = {**materialized,
            **_materialization(connection, current, by_candidate, queue, write=False)}
        current["review"] = review
        chain.append(current)
        successors = [run for run in runs if run["code_version"] == VERSION and
            json.loads(run["parameters_json"]).get("scope_revision", {}).get("previous_run_id") == current["id"]]
        if len(successors) > 1:
            raise ValueError("EEA scope history forks")
        previous, current = current, successors[0] if successors else None
        if current is not None and any(run["id"] == current["id"] for run in chain):
            raise ValueError("EEA scope history has a cycle")
    if len(chain) != len(by_id):
        raise ValueError("EEA scope history has a missing predecessor or disconnected run")
    expected_entities = {row["entity_id"] for row in materialized.values()}
    actual_entities = {row[0] for row in connection.execute(
        "SELECT id FROM entities WHERE substr(stable_key, 1, ?) = ?",
        (len(seed.ENTITY_KEY_PREFIX), seed.ENTITY_KEY_PREFIX))}
    if actual_entities != expected_entities:
        raise ValueError("EEA history has unexpected source-local entities")
    expected_claims = {claim for row in materialized.values() for claim in row["claim_ids"]}
    if expected_entities:
        marks = ",".join("?" for _ in expected_entities)
        actual_claims = {row[0] for row in connection.execute(
            f"SELECT versions.id FROM claim_versions AS versions JOIN claim_series AS series "
            f"ON series.id = versions.series_id WHERE series.subject_entity_id IN ({marks})",
            tuple(sorted(expected_entities)))}
        actual_series = {row[0] for row in connection.execute(
            f"SELECT id FROM claim_series WHERE subject_entity_id IN ({marks})", tuple(sorted(expected_entities)))}
        claim_series = {row[0] for row in connection.execute(
            f"SELECT DISTINCT versions.series_id FROM claim_versions AS versions JOIN claim_series AS series "
            f"ON series.id = versions.series_id WHERE series.subject_entity_id IN ({marks})",
            tuple(sorted(expected_entities)))}
        if actual_claims != expected_claims or actual_series != claim_series:
            raise ValueError("EEA history has unexpected claim materialization")
    return chain, materialized


def validate_history(connection, *, candidate_queue) -> dict:
    queue = seed._load_queue(candidate_queue)
    chain, materialized = _history(connection, queue)
    return {"genesis_run_id": chain[0]["id"], "head_run_id": chain[-1]["id"],
            "revision_count": len(chain) - 1, "materialized_candidate_count": len(materialized)}


def scope_records(connection, *, candidate_queue, recorded_at: str) -> dict:
    queue = seed._load_queue(candidate_queue)
    cutoff = seed._canonical_timestamp(recorded_at, "scope knowledge cutoff")
    if connection.execute("SELECT 1 FROM sources WHERE id = ?",
            (stable_id("source", seed.SOURCE_KEY),)).fetchone() is None:
        chain, materialized = [], {}
    else:
        chain, materialized = _history(connection, queue)
    visible = [run for run in chain if seed._clock(
        json.loads(run["parameters_json"])["accepted_at"]) <= seed._clock(cutoff)]
    dispositions, claims = [], []
    if visible:
        selected = visible[-1]
        queue_by_id = {row.candidate_id: row for row in queue.candidates}
        for decision in selected["review"].decisions:
            identity = materialized.get(decision.candidate_id)
            if identity and seed._clock(identity["first_admitted_at"]) > seed._clock(cutoff):
                identity = None
            dispositions.append({**seed._decision_payload(decision),
                "facility_inspire_id": queue_by_id[decision.candidate_id].facility_inspire_id,
                "materialization": identity})
            if decision.outcome == "accept_in_scope":
                if identity is None:
                    raise ValueError("accepted scope lacks cutoff-visible source materialization")
                rows = source_claim_records(connection, recorded_at=cutoff,
                    subject_entity_id=identity["entity_id"])
                if {row["id"] for row in rows} != set(identity["claim_ids"]):
                    raise ValueError("scoped source statements differ from retained materialization")
                claims.extend(rows)
    return {"format": REPORT_FORMAT, "recorded_at": cutoff,
        "candidate_queue_sha256": queue.raw_sha256,
        "snapshot_manifest_sha256": queue.snapshot_manifest_sha256,
        "head_run_id": visible[-1]["id"] if visible else None,
        "history": [{"run_id": run["id"], "code_version": run["code_version"],
            "accepted_at": json.loads(run["parameters_json"])["accepted_at"],
            "review_sha256": run["review"].raw_sha256} for run in visible],
        "outcome_counts": dict(sorted(Counter(row["outcome"] for row in dispositions).items())),
        "dispositions": dispositions, "source_statements": sorted(claims, key=lambda row: row["id"]),
        "boundaries": {"canonical_identity": False, "physical_world_state": False,
            "operating_capacity": False, "scope_withdrawal_means_closure": False,
            "legacy_claims_rewritten": False}}


def accept_revision(connection, *, snapshot, candidate_queue, review,
                    expected_predecessor_run_id: str, accepted_at: str | None = None) -> dict:
    """Atomically append a complete scope review; explicit clocks are replay-only."""
    started = seed._canonical_timestamp(_now(), "revision start")
    verified = seed._verified_snapshot(snapshot)
    queue = seed._load_queue(candidate_queue)
    seed._assert_queue_matches_snapshot(verified, queue)
    reviewed = seed._load_review(review, queue=queue)
    if accepted_at is not None:
        seed._canonical_timestamp(accepted_at, "replay acceptance")
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    try:
        with seed._atomic_import(connection):
            chain, materialized = _history(connection, queue)
            matching = [run for run in chain if run["review"].raw_sha256 == reviewed.raw_sha256]
            if matching:
                original = matching[0]
                parameters = json.loads(original["parameters_json"])
                predecessor = parameters.get("scope_revision", {}).get("previous_run_id")
                if predecessor != expected_predecessor_run_id:
                    raise ValueError("exact review replay has a different predecessor")
                if accepted_at is not None and accepted_at != parameters["accepted_at"]:
                    raise ValueError("exact revision replay has a different acceptance clock")
                seed._reverify_inputs(verified, queue, reviewed)
                result = {"ingestion_run_id": original["id"], "head_run_id": chain[-1]["id"],
                    "accepted_at": parameters["accepted_at"], "replayed_existing_run": True,
                    "new_candidates": 0, "claims_created": 0}
            else:
                if accepted_at is not None:
                    raise ValueError("accepted_at is replay-only for scope revisions")
                previous = chain[-1]
                if previous["id"] != expected_predecessor_run_id:
                    raise ValueError("stale expected predecessor for EEA scope revision")
                if (seed._clock(reviewed.reviewed_at) <= seed._clock(previous["review"].reviewed_at)
                        or seed._clock(reviewed.knowledge_cutoff_at) < seed._clock(previous["review"].knowledge_cutoff_at)):
                    raise ValueError("new review clocks must advance predecessor review")
                issues = validate_database(connection)
                if issues:
                    raise ValueError("EEA revision preflight: " + "; ".join(issues))
                seed._reverify_inputs(verified, queue, reviewed)
                acceptance = seed._canonical_timestamp(_now(), "revision admission")
                prior_parameters = json.loads(previous["parameters_json"])
                if not (seed._clock(acceptance) > seed._clock(started)
                        and seed._clock(acceptance) > seed._clock(prior_parameters["accepted_at"])
                        and seed._clock(acceptance) >= seed._clock(reviewed.reviewed_at)):
                    raise ValueError("actual revision admission clock does not follow inputs/start")
                parameters = seed._run_parameters(snapshot=verified, queue=queue, review=reviewed,
                    accepted_at=acceptance, importer_version=VERSION)
                if parameters["snapshot"] != prior_parameters["snapshot"]:
                    raise ValueError("scope revision cannot change snapshot bindings")
                new_ids = {row.candidate_id for row in reviewed.decisions
                           if row.outcome == "accept_in_scope"} - materialized.keys()
                parameters["scope_revision"] = {"format": VERSION,
                    "genesis_run_id": chain[0]["id"], "genesis_run_sha256": _run_hash(chain[0]),
                    "previous_run_id": previous["id"], "previous_run_sha256": _run_hash(previous),
                    "new_candidate_ids": sorted(new_ids), "retained_materializations": materialized,
                    "review_utf8": reviewed.raw_bytes.decode("utf-8")}
                identifier = stable_id("ingestion-run", VERSION, queue.snapshot_manifest_sha256,
                    queue.raw_sha256, reviewed.raw_sha256, previous["id"])
                raw_document, document = _documents(parameters)
                run = IngestionRun(identifier, stable_id("source", seed.SOURCE_KEY), started,
                    status=IngestionStatus.SUCCEEDED, completed_at=acceptance, code_version=VERSION,
                    input_document_id=document, parameters=parameters)
                seed._persist(connection, run, replayed=False)
                for doc_id, role in ((raw_document, "raw_accdb"), (document, "candidate_derivative")):
                    seed._persist(connection, IngestionRunDocument(identifier, doc_id, role), replayed=False)
                run_row = dict(connection.execute("SELECT * FROM ingestion_runs WHERE id = ?", (identifier,)).fetchone())
                candidates = seed._source_candidate_map(verified)
                queue_by_id = {row.candidate_id: row for row in queue.candidates}
                added = _materialization(connection, run_row,
                    {key: candidates[queue_by_id[key].facility_inspire_id] for key in new_ids}, queue, write=True)
                _history(connection, queue)
                issues = validate_database(connection)
                if issues:
                    raise ValueError("EEA revision validation: " + "; ".join(issues))
                seed._reverify_inputs(verified, queue, reviewed)
                if seed._clock(seed._canonical_timestamp(_now(), "final verification")) < seed._clock(acceptance):
                    raise ValueError("EEA revision clock moved backwards")
                result = {"ingestion_run_id": identifier, "head_run_id": identifier,
                    "accepted_at": acceptance, "replayed_existing_run": False,
                    "new_candidates": len(added),
                    "claims_created": sum(len(row["claim_ids"]) for row in added.values())}
        if owns_transaction:
            connection.commit()
        return result
    except BaseException:
        if owns_transaction:
            connection.rollback()
        raise
