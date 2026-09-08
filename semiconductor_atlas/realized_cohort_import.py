"""Admit frozen-verified retrospective source reports without assigning physical truth."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from scripts import frozen_runtime as runtime, replay_frozen_runtime as replay
from . import database, models, repository


RULE_VERSION = "frozen-verified-realized-fab-cohort-core-v1"
FORMAT = "semiconductor-atlas-realized-cohort-core-admission-v1"
EVENT = "commercial_production_commencement"
BOUNDARIES = dict.fromkeys(("canonical_identity_assigned", "physical_truth_asserted", "capacity_inferred",
    "geometry_inferred", "forecast_created", "forecast_scored", "redistribution_authorized",
    "sidecar_modified", "core_migration_requested", "scheduled_execution_rerouted"), False)


def _now():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _clock(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("admission chronology requires timezone-aware timestamps")
    return parsed


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON number")))


def _disk_codes():
    root = Path(__file__).parent.parent
    paths = {Path(__file__), root / "scripts/import_realized_fab_cohort.py",
             Path(__file__).with_name("source_quantities.py"),
             *(Path(module.__file__) for module in (database, models, repository, runtime, replay)),
             *Path(database.__file__).with_name("migrations").glob("*.sql")}
    return {path.relative_to(root).as_posix(): _hash(runtime.read(path)) for path in sorted(paths)}


_LOADED_CODES = _disk_codes()


def _codes():
    current = _disk_codes()
    if current != _LOADED_CODES:
        raise ValueError("core admission code or migrations changed since module loading")
    return current


def _inputs(paths):
    return {key: runtime.read(path) for key, path in paths.items()}


def _schema(connection):
    if database.schema_version(connection) != 6:
        raise ValueError("realized cohort admission requires existing schema exactly 6; no implicit migration")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValueError("foreign-key enforcement must be enabled")
    wanted = [(int(path.name[:4]), path.stem[5:], _hash(runtime.read(path)))
              for path in sorted(Path(database.__file__).with_name("migrations").glob("*.sql"))
              if int(path.name[:4]) <= 6]
    actual = [tuple(row) for row in connection.execute("SELECT version,name,sha256 FROM schema_migrations ORDER BY version")]
    if actual != wanted:
        raise ValueError("installed migration identities differ from the loaded schema")


def _ids(value, digest):
    source = value["anchor"]["review"]["source"]
    key = "reviewed-realized-fab-source:" + source["source_id"]
    return {"family": repository.stable_id(RULE_VERSION, "family"),
            "source": repository.stable_id(RULE_VERSION, key), "source_key": key,
            "document": repository.stable_id(RULE_VERSION, key, source["content_sha256"], source["retrieved_at"]),
            "run": repository.stable_id(RULE_VERSION, digest)}


def _verification(value, raw, receipt, trusted):
    original = value["anchor"]["review"]
    if (_hash(raw["source-body"]) != original["source"]["content_sha256"]
            or len(raw["source-body"]) != original["source"]["bytes"]
            or _hash(raw["provenance"]) != original["provenance"]["sha256"]):
        raise ValueError("source and provenance bytes differ from the verified cohort bindings")
    expected = {"verified": True, "sha256": _hash(raw["cohort"]), "artifact_sha256": value["sha256"],
        "bytes": len(raw["cohort"]), "table_rows": value["table_rows"], "counts": value["counts"],
        "accepted_source_reports": value["accepted_source_reports"], "source_bytes_verified": len(raw["source-body"]),
        "provenance_sha256": _hash(raw["provenance"]), "database_writes": False, "publisher_bodies_embedded": False}
    if (receipt.get("verified") is not True or receipt.get("operation") != "realized-cohort"
            or receipt.get("runtime_sha256") != trusted["runtime_sha256"]
            or receipt.get("commit") != trusted["runtime_commit"] or receipt.get("operation_receipt") != expected):
        raise ValueError("frozen verification receipt differs from exact supplied inputs")
    return expected


def _parameters(value, trusted, codes, raw, receipt, clocks, admitted):
    return {"rule_version": RULE_VERSION, "trusted": trusted, "code_sha256": codes,
        "cohort": value, "rights": value["rights"], "selection": value["selection"], "boundaries": BOUNDARIES,
        "frozen_verification": receipt, "frozen_operation_receipt": receipt["operation_receipt"],
        "lineage_creation_clocks": clocks, "accepted_at": admitted,
        "acceptance_timestamp_basis": "actual_post_validation_core_admission",
        "inputs": {key: {"sha256": _hash(data), "bytes": len(data)} for key, data in raw.items()}}


def _admission_clocks(started, admitted, receipt, clocks):
    if not (_clock(started) <= _clock(receipt["replay_started_at"])
            <= _clock(receipt["replayed_at"]) <= _clock(admitted)):
        raise ValueError("frozen verification chronology differs from core admission")
    if any(_clock(clock) > _clock(admitted) for clock in clocks.values()):
        raise ValueError("reused lineage clock postdates core admission")


def _populate(connection, value, parameters, started, admitted):
    ids = _ids(value, parameters["trusted"]["cohort_sha256"])
    original = value["anchor"]["review"]
    source, clocks = original["source"], parameters["lineage_creation_clocks"]
    family = models.SourceFamily(ids["family"], RULE_VERSION, "Reviewed retrospective fab source reports", clocks["family"])
    publisher = models.Source(ids["source"], family.id, ids["source_key"], "TSMC operating-fab table source reports",
        "TSMC", source["url"], clocks["source"], "Local review only; no redistribution authorization")
    repository.add_source_family(connection, family)
    repository.add_source(connection, publisher)
    repository.add_source_document(connection, models.SourceDocument(ids["document"], publisher.id, source["url"],
        "TSMC 2025 Form 20-F operating-fab table", source["retrieved_at"], source["content_sha256"],
        published_at=source["published_at"], media_type="text/html", license=publisher.license,
        metadata={"source_native_id": source["source_id"], "published_at_precision": source["published_at_precision"],
            "table_as_of": value["table_as_of"], "rights": value["rights"], "canonical_entity_id": None}))
    repository.add_ingestion_run(connection, models.IngestionRun(ids["run"], publisher.id, started,
        models.IngestionStatus.SUCCEEDED, admitted, parameters["code_sha256"]["semiconductor_atlas/realized_cohort_import.py"],
        input_document_id=ids["document"], parameters=parameters))
    claims, entities, records = {}, {}, {}
    for case in value["cases"]:
        key = case["source_report_key"]
        record_id = repository.stable_id(RULE_VERSION, "record", key)
        payload = {"case": case, "cohort_sha256": parameters["trusted"]["cohort_sha256"],
            "cohort_content_sha256": value["sha256"], "review_sha256": value["review_sha256"],
            "sidecar_recorded_at": case["recorded_at"], "core_admitted_at": admitted,
            "claim_effective_at": None, "canonical_entity_id": None}
        repository.add_source_record(connection, models.SourceRecord(record_id, ids["run"], ids["document"],
            "realized-source-report:" + key, source["retrieved_at"], models.source_record_payload_sha256(payload), payload))
        records[key] = record_id
        if case["decision"] == "defer":
            continue
        subject, event = case["subject"], case["event"]
        entity_id = repository.stable_id(RULE_VERSION, "source-native-facility", subject["source_native_id"])
        repository.add_entity(connection, models.Entity(entity_id, models.EntityKind.FACILITY,
            RULE_VERSION + ":" + subject["source_native_id"], admitted, subject["label"], ids["run"]))
        series_id = repository.stable_id(RULE_VERSION, "series", key)
        repository.add_claim_series(connection, models.ClaimSeries(series_id, entity_id,
            RULE_VERSION + ":" + key, "milestone." + EVENT, models.ValueKind.MILESTONE, admitted))
        claim_id = repository.stable_id(RULE_VERSION, "claim", key)
        evidence = [models.EvidenceLink(ids["document"], role=role, source_record_id=record_id,
            locator=f"Operating-fab table {name}; UTF-8 bytes [{span['start']},{span['end']}); SHA-256 {span['sha256']}")
            for name, role, span in (("row", models.EvidenceRole.SUPPORT, case["evidence"]),
                ("header", models.EvidenceRole.CONTEXT, original["evidence"]["header"]),
                ("intro", models.EvidenceRole.CONTEXT, original["evidence"]["intro"]))]
        repository.insert_claim(connection, models.ClaimVersion(claim_id, series_id, None, admitted,
            models.ClaimKind.SOURCE_STATEMENT, RULE_VERSION, None, created_by_run_id=ids["run"],
            notes="Retrospective publisher-reported commencement, not HVM or independently established physical truth; effective time unknown."),
            models.MilestoneValue(EVENT, models.MilestoneStatus.COMPLETED, event["low"], None,
                event["high"], event["precision"], event["literal"]), evidence=evidence)
        claims[key], entities[key] = claim_id, entity_id
    return {"format": FORMAT, "rule_version": RULE_VERSION, "run_id": ids["run"],
        "cohort_sha256": parameters["trusted"]["cohort_sha256"], "cohort_content_sha256": value["sha256"],
        "document_id": ids["document"], "admitted_at": admitted, "table_rows": value["table_rows"],
        "counts": value["counts"], "claim_ids": claims, "entity_ids": entities, "record_ids": records,
        "selection": value["selection"], "rights": value["rights"], "boundaries": BOUNDARIES}


def _rows(connection, table, where="1", parameters=()):
    return sorted(tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" WHERE {where}', parameters))


def _verify_rows(connection, expected, result):
    ownership = {"ingestion_run_id": [result["run_id"]], "created_by_run_id": [result["run_id"]],
        "source_record_id": list(result["record_ids"].values()), "source_document_id": [result["document_id"]],
        "claim_version_id": list(result["claim_ids"].values()), "depends_on_claim_version_id": list(result["claim_ids"].values()),
        "series_id": [row[0] for row in expected.execute("SELECT id FROM claim_series")]}
    for key in ("subject_entity_id", "observed_entity_id", "canonical_entity_id", "candidate_entity_id",
                "object_entity_id", "constrained_entity_id"):
        ownership[key] = list(result["entity_ids"].values())
    for table, in expected.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_migrations'"):
        columns = list(expected.execute(f'PRAGMA table_info("{table}")'))
        primary = [column["name"] for column in columns if column["pk"]]
        for row in expected.execute(f'SELECT * FROM "{table}"'):
            actual = connection.execute(f'SELECT * FROM "{table}" WHERE ' + " AND ".join(f'"{key}" IS ?' for key in primary),
                [row[key] for key in primary]).fetchone()
            if actual is None or tuple(actual) != tuple(row):
                raise ValueError("recorded cohort import differs in " + table)
        clauses, arguments = [], []
        for column in columns:
            values = ownership.get(column["name"], [])
            if values:
                clauses.append('"' + column["name"] + '" IN (' + ",".join("?" for _ in values) + ")")
                arguments.extend(values)
        if clauses and _rows(connection, table, " OR ".join(clauses), arguments) != _rows(expected, table, " OR ".join(clauses), arguments):
            raise ValueError("unexpected or altered lineage attached to cohort import in " + table)


def accept_cohort(connection, cohort_path, *, source_body, provenance, frozen_runtime_bundle,
                  expected_runtime_sha256, expected_runtime_commit, expected_cohort_sha256, verify_only=False):
    """Own one atomic admission, or verify exact existing rows without target writes."""
    if connection.in_transaction:
        raise ValueError("cohort admission requires ownership of its database transaction")
    _schema(connection)
    trusted = {"runtime_sha256": expected_runtime_sha256, "runtime_commit": expected_runtime_commit,
               "cohort_sha256": expected_cohort_sha256}
    for key, length in (("runtime_sha256", 64), ("runtime_commit", 40), ("cohort_sha256", 64)):
        if not isinstance(trusted[key], str) or not re.fullmatch(r"[0-9a-f]{" + str(length) + "}", trusted[key]):
            raise ValueError("trusted exact input identities are required")
    paths = {name: Path(path).absolute() for name, path in (("cohort", cohort_path), ("source-body", source_body),
        ("provenance", provenance), ("runtime", frozen_runtime_bundle))}
    codes, started, raw = _codes(), _now(), _inputs(paths)
    if _hash(raw["cohort"]) != expected_cohort_sha256 or _hash(raw["runtime"]) != expected_runtime_sha256:
        raise ValueError("trusted cohort or runtime exact-byte hash mismatch")
    receipt = replay.run_verified(paths["runtime"], expected_sha256=expected_runtime_sha256,
        expected_commit=expected_runtime_commit, operation="realized-cohort",
        inputs={name: str(paths[name]) for name in ("cohort", "source-body", "provenance")},
        working_directory=Path(__file__).parent.parent)
    try:
        value = _json(raw["cohort"])
        _verification(value, raw, receipt, trusted)
        if value["format"] != "semiconductor-atlas-realized-fab-table-cohort-v1":
            raise ValueError("unsupported verified cohort format")
        if _clock(value["admitted_at"]) > _clock(started):
            raise ValueError("sidecar admission cannot postdate core validation start")
        for case in value["cases"]:
            if case["subject"]["canonical_entity_id"] is not None or case["subject"]["location"] is not None:
                raise ValueError("core route requires unresolved source-native subjects")
            if case["decision"] == "defer":
                if case["event"] is not None or case["recorded_at"] is not None:
                    raise ValueError("deferred row cannot carry an admitted event")
            elif (case["decision"] not in ("accept_source_report", "carry_forward")
                    or case["event"]["event_type"] != EVENT or case["event"]["base"] is not None
                    or case["event"]["precision"] != "year" or _clock(case["recorded_at"]) > _clock(started)):
                raise ValueError("unsupported source-reported completed event")
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError("malformed verified cohort or receipt") from error
    if _inputs(paths) != raw or _codes() != codes:
        raise ValueError("core admission inputs changed during frozen verification")
    ids = _ids(value, expected_cohort_sha256)
    connection.execute("BEGIN" if verify_only else "BEGIN IMMEDIATE")
    try:
        _schema(connection)
        issues = repository.validate_database(connection)
        if issues:
            raise ValueError("core database validation: " + "; ".join(issues))
        prior = connection.execute("SELECT * FROM ingestion_runs WHERE id=?", (ids["run"],)).fetchone()
        if prior is not None:
            parameters = _json(prior["parameters_json"])
            old_receipt = parameters.get("frozen_verification", {})
            stable = lambda item: {key: data for key, data in item.items() if key not in ("replay_started_at", "replayed_at")}
            clocks = parameters.get("lineage_creation_clocks", {})
            if (stable(old_receipt) != stable(receipt) or set(clocks) != {"family", "source"}
                    or any(_clock(clock) > _clock(prior["completed_at"]) for clock in clocks.values())
                    or not _clock(value["admitted_at"]) <= _clock(prior["started_at"]) < _clock(prior["completed_at"]) <= _clock(started)
                    or parameters != _parameters(value, trusted, codes, raw, old_receipt, clocks, prior["completed_at"])):
                raise ValueError("recorded cohort admission contract differs")
            _admission_clocks(prior["started_at"], prior["completed_at"], old_receipt, clocks)
            expected, _ = database.initialize(":memory:", target_version=6)
            try:
                result = _populate(expected, value, parameters, prior["started_at"], prior["completed_at"])
                _verify_rows(connection, expected, result)
            finally:
                expected.close()
        else:
            if verify_only:
                raise ValueError("cohort has no existing core admission to verify")
            for case in value["cases"]:
                if connection.execute("SELECT 1 FROM source_records WHERE source_record_key=?",
                        ("realized-source-report:" + case["source_report_key"],)).fetchone():
                    raise ValueError("distinct cohort overlaps an already imported source report; correction is unsupported")
            clocks = {}
            for key, table in (("family", "source_families"), ("source", "sources")):
                row = connection.execute(f"SELECT created_at FROM {table} WHERE id=?", (ids[key],)).fetchone()
                clocks[key] = row[0] if row else None
            admitted = _now()
            if _clock(admitted) <= _clock(started):
                raise ValueError("actual core admission clock must follow validation start")
            clocks = {key: clock or admitted for key, clock in clocks.items()}
            _admission_clocks(started, admitted, receipt, clocks)
            parameters = _parameters(value, trusted, codes, raw, receipt, clocks, admitted)
            result = _populate(connection, value, parameters, started, admitted)
            issues = repository.validate_database(connection)
            if issues:
                raise ValueError("core admission database validation: " + "; ".join(issues))
        if _inputs(paths) != raw or _codes() != codes:
            raise ValueError("core inputs or code changed across admission boundary")
        if prior is not None or verify_only:
            connection.rollback()
        else:
            connection.commit()
    except (KeyError, TypeError, AttributeError, OverflowError) as error:
        connection.rollback()
        raise ValueError("malformed recorded cohort admission") from error
    except BaseException:
        connection.rollback()
        raise
    return {**result, "replayed": prior is not None, "database_writes": 0 if prior else "one_atomic_core_admission"}
