"""Read-only, exact-subject evidence dossiers with explicit knowledge cutoffs."""

from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import database, repository, service


FORMAT = "semiconductor-atlas-evidence-dossier-v1"
BOUNDARIES = [
    "Exact entity only; no automatic identity, phase, child, ownership or facility allocation.",
    "Source statements are publisher reports, not independently verified physical-current facts.",
    "World-current claims retain their observation, reconciliation or estimate kind; unknowns remain null.",
    "Source quantities and monthly rates are not promoted to physical capacity or measured outcomes.",
    "Multiple documents in one source family are not independent corroboration; family counts are not confidence.",
    "History retains superseded versions; dependency context is not additional evidence about this exact subject.",
    "Local derivative of recorded claims; no fresh source check, source-byte revalidation, forecast or alert delivery.",
    "Excerpts, source-record payloads, claim notes, run parameters and verbatim rate wording are omitted. Publication requires separate rights review.",
]
_CLAIM_FIELDS = (
    "id", "series_id", "series_stable_key", "subject_entity_id", "predicate", "value_kind",
    "value_sha256", "valid_from", "valid_to", "recorded_at", "superseded_at", "claim_kind",
    "method", "confidence", "created_by_run_id", "value", "evidence", "dependencies",
)
_EVIDENCE_FIELDS = (
    "id", "role", "locator", "source_record_id", "source_document_id", "document_url", "title",
    "published_at", "retrieved_at", "content_sha256", "license", "source_record_observed_at",
    "source_record_sha256", "publisher", "source_key", "source_family",
)
_RATE_FIELDS = (
    "metric", "scope_kind", "scope_label", "numerator_unit", "denominator_unit",
    "native_wafer_diameter_inches", "reference_period_kind", "reference_period_start",
    "reference_period_end", "source_reference_period_literal", "source_number_literal",
    "numeric_qualifier", "source_qualifier_literal", "assertion_kind", "source_definition",
    "physical_capacity_basis", "numeric_interpretation",
)


def _clock(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("knowledge clocks must be timezone-aware timestamps")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid knowledge timestamp") from error
    if parsed.utcoffset() is None:
        raise ValueError("knowledge clocks must include a timezone")
    return parsed.astimezone(UTC)


def _before(value: str, cutoff: datetime, field: str) -> None:
    if _clock(value) > cutoff:
        raise ValueError(f"{field} is unavailable at the knowledge cutoff")


def _history(connection, subject: str, as_of: str, recorded_at: str) -> list[dict]:
    clock = database.knowledge_clock_sql(connection)
    rows = connection.execute(f"""
        WITH RECURSIVE selected(id) AS (
            SELECT v.id FROM claim_versions v JOIN claim_series s ON s.id=v.series_id
            WHERE s.subject_entity_id=? AND {clock}(v.recorded_at)<={clock}(?)
              AND (v.valid_from IS NULL OR v.valid_from<=? OR v.claim_kind='source_statement')
            UNION
            SELECT d.depends_on_claim_version_id FROM claim_dependencies d
            JOIN selected ON selected.id=d.claim_version_id
        )
        SELECT v.id,v.series_id,v.value_kind,v.value_sha256,v.valid_from,v.valid_to,
               v.recorded_at,
               CASE WHEN {clock}(v.superseded_at)<={clock}(?) THEN v.superseded_at
                    ELSE NULL END AS superseded_at,
               v.claim_kind,v.method,v.confidence,v.created_by_run_id,
               s.subject_entity_id,s.stable_key AS series_stable_key,s.predicate
        FROM selected JOIN claim_versions v ON v.id=selected.id
        JOIN claim_series s ON s.id=v.series_id
        ORDER BY s.subject_entity_id,s.predicate,s.id,v.valid_from,{clock}(v.recorded_at),v.id
        """, (subject, recorded_at, as_of, recorded_at)).fetchall()
    return service._hydrate_claim_rows(connection, rows)


def _check_support(connection, claims: list[dict], cutoff: datetime, entity: dict) -> list[dict]:
    """Check selected typed values and provenance clocks, not the original source bytes."""
    claim_ids = {c["id"] for c in claims}
    pending_counts, children = {}, defaultdict(set)
    run_ids = {entity["created_by_run_id"]} - {None}
    document_ids, record_ids, record_documents = set(), set(), defaultdict(set)
    for claim in claims:
        _before(claim["recorded_at"], cutoff, "claim admission")
        stored = repository._stored_claim_value(connection, claim["id"], claim["value_kind"])
        if repository.value_sha256(stored) != claim["value_sha256"]:
            raise ValueError("stored typed value does not match its claim hash")
        row = connection.execute("""SELECT s.created_at AS series_created,
            e.created_at AS entity_created,e.created_by_run_id
            FROM claim_series s JOIN entities e ON e.id=s.subject_entity_id WHERE s.id=?""",
            (claim["series_id"],)).fetchone()
        _before(row["series_created"], cutoff, "series creation")
        _before(row["entity_created"], cutoff, "entity creation")
        run_ids.update({row["created_by_run_id"], claim["created_by_run_id"]} - {None})
        target_id = None
        if claim["value_kind"] == "relationship":
            target_id = claim["value"]["object_entity_id"]
        elif claim["value_kind"] == "constraint":
            target_id = claim["value"]["constrained_entity_id"]
        if target_id is not None:
            target = connection.execute("SELECT created_at,created_by_run_id FROM entities WHERE id=?",
                                        (target_id,)).fetchone()
            if target is None:
                raise ValueError("referenced entity is unavailable at the knowledge cutoff")
            _before(target["created_at"], cutoff, "referenced entity creation")
            run_ids.update({target["created_by_run_id"]} - {None})
        parents = {d["depends_on_claim_version_id"] for d in claim["dependencies"]}
        if not parents <= claim_ids:
            raise ValueError("claim dependency is unavailable at the knowledge cutoff")
        if not parents and not any(e["role"] == "support" for e in claim["evidence"]):
            raise ValueError("claim lineage has no supporting evidence")
        pending_counts[claim["id"]] = len(parents)
        for parent in parents:
            children[parent].add(claim["id"])
        for evidence in claim["evidence"]:
            document_ids.add(evidence["source_document_id"])
            if evidence["source_record_id"] is not None:
                record_ids.add(evidence["source_record_id"])
                record_documents[evidence["source_record_id"]].add(evidence["source_document_id"])
    ready = [identifier for identifier, count in pending_counts.items() if count == 0]
    visited = 0
    while ready:
        visited += 1
        for child in children[ready.pop()]:
            pending_counts[child] -= 1
            if pending_counts[child] == 0:
                ready.append(child)
    if visited != len(claims):
        raise ValueError("cyclic claim lineage")

    for identifier in sorted(record_ids):
        row = connection.execute("SELECT observed_at,ingestion_run_id,source_document_id FROM source_records WHERE id=?",
                                 (identifier,)).fetchone()
        if row is None:
            raise ValueError("source record is missing")
        if record_documents[identifier] != {row["source_document_id"]}:
            raise ValueError("source record/document mismatch")
        _before(row["observed_at"], cutoff, "source record observation")
        run_ids.add(row["ingestion_run_id"])
    runs = []
    for identifier in sorted(run_ids):
        row = connection.execute("SELECT * FROM ingestion_runs WHERE id=?", (identifier,)).fetchone()
        if row is None or row["status"] != "succeeded" or row["completed_at"] is None:
            raise ValueError("input lineage requires succeeded ingestion runs")
        for field in ("started_at", "completed_at"):
            _before(row[field], cutoff, "run " + field)
        source = connection.execute("""SELECT s.created_at AS source_created,
            f.created_at AS family_created FROM sources s
            JOIN source_families f ON f.id=s.family_id WHERE s.id=?""", (row["source_id"],)).fetchone()
        if source is None:
            raise ValueError("ingestion run source is missing")
        for field in ("source_created", "family_created"):
            _before(source[field], cutoff, "run " + field)
        parameters = json.loads(row["parameters_json"])
        if not isinstance(parameters, dict):
            raise ValueError("invalid ingestion-run parameters")
        admitted = parameters.get("accepted_at")
        if admitted is not None:
            _before(admitted, cutoff, "actual run admission")
        runs.append({key: row[key] for key in
                     ("id", "source_id", "started_at", "completed_at", "status", "code_version")}
                    | {"accepted_at": admitted})
        if row["input_document_id"] is not None:
            document_ids.add(row["input_document_id"])
        document_ids.update(r[0] for r in connection.execute(
            "SELECT source_document_id FROM ingestion_run_documents WHERE ingestion_run_id=?", (identifier,)))
    for identifier in sorted(document_ids):
        row = connection.execute("""SELECT d.retrieved_at,d.published_at,
            s.created_at AS source_created,f.created_at AS family_created
            FROM source_documents d JOIN sources s ON s.id=d.source_id
            JOIN source_families f ON f.id=s.family_id WHERE d.id=?""", (identifier,)).fetchone()
        if row is None:
            raise ValueError("source document is missing")
        for field in ("retrieved_at", "source_created", "family_created"):
            _before(row[field], cutoff, field)
        published = row["published_at"]
        if published is not None:
            if len(published) == 10:
                if date.fromisoformat(published) > _clock(row["retrieved_at"]).date():
                    raise ValueError("publication date postdates retrieval")
            elif _clock(published) > _clock(row["retrieved_at"]):
                raise ValueError("publication clock postdates retrieval")
    return runs


def _public_claim(claim: dict) -> dict:
    result = {key: claim[key] for key in _CLAIM_FIELDS}
    result["evidence"] = [{k: e[k] for k in _EVIDENCE_FIELDS} for e in claim["evidence"]]
    result["value"] = dict(claim["value"])
    if "source_rate" in result["value"]:
        result["value"]["source_rate"] = {
            k: result["value"]["source_rate"][k] for k in _RATE_FIELDS}
    return result


def build_evidence_dossier(connection: sqlite3.Connection, *, subject_entity_id: str,
                           as_of: str, recorded_at: str) -> dict:
    if not isinstance(subject_entity_id, str) or not subject_entity_id.strip():
        raise ValueError("an exact entity ID is required")
    if not isinstance(as_of, str) or date.fromisoformat(as_of).isoformat() != as_of:
        raise ValueError("as_of must use YYYY-MM-DD")
    cutoff = _clock(recorded_at)
    recorded_at = cutoff.isoformat().replace("+00:00", "Z")
    connection.execute("SAVEPOINT evidence_dossier_read")
    try:
        version = database.schema_version(connection)
        if version < 5:
            raise ValueError("evidence dossier requires existing schema 5 or later; no migration performed")
        clock = database.knowledge_clock_sql(connection)
        row = connection.execute(f"""SELECT id,kind,stable_key,display_name,created_at,created_by_run_id
            FROM entities WHERE id=? AND {clock}(created_at)<={clock}(?)""",
            (subject_entity_id, recorded_at)).fetchone()
        if row is None:
            raise ValueError("exact entity is unavailable at the knowledge cutoff")
        entity = dict(row)
        history = _history(connection, subject_entity_id, as_of, recorded_at)
        runs = _check_support(connection, history, cutoff, entity)
        current_ids = {r["id"] for r in repository.current_claims(connection, as_of=as_of,
            recorded_at=recorded_at, subject_entity_id=subject_entity_id)
            if r["claim_kind"] != "source_statement"}
        source_ids = {r["id"] for r in repository.known_source_claims(connection,
            recorded_at=recorded_at, subject_entity_id=subject_entity_id)}
        records = [_public_claim(c) for c in history]
        roots = [c for c in records if c["subject_entity_id"] == subject_entity_id
                 and (c["valid_from"] is None or c["valid_from"] <= as_of
                      or c["claim_kind"] == "source_statement")]
        root_ids = {c["id"] for c in roots}
        if not (current_ids | source_ids) <= root_ids:
            raise ValueError("selected view is missing its known history")
        evidence = {e["id"]: e for c in records for e in c["evidence"]}
        families = sorted({e["source_family"] for e in evidence.values()})
        return {
            "format": FORMAT, "schema_version": version, "entity": entity,
            "as_of": as_of, "recorded_at": recorded_at, "boundaries": BOUNDARIES.copy(),
            "world_current_claims": [c for c in roots if c["id"] in current_ids],
            "source_statements": [c for c in roots if c["id"] in source_ids],
            "recorded_history": roots,
            "dependency_context": [c for c in records if c["id"] not in root_ids],
            "ingestion_runs": runs,
            "evidence_summary": {"claim_count_including_dependencies": len(records),
                "evidence_link_count": len(evidence),
                "source_document_count": len({e["source_document_id"] for e in evidence.values()}),
                "source_families": families, "source_family_count": len(families),
                "independent_corroboration": "not_assessed"},
        }
    finally:
        connection.execute("RELEASE SAVEPOINT evidence_dossier_read")


def json_bytes(report: dict) -> bytes:
    return (json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()


def _text(value) -> str:
    if value is None:
        return "null (unknown or not applicable)"
    text = str(value) if not isinstance(value, (dict, list)) else json.dumps(value, ensure_ascii=False)
    text = html.escape(text, quote=True)
    text = re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)
    return text.replace("\r", " ").replace("\n", " ")


def _link(url) -> str:
    if not isinstance(url, str):
        return _text(url)
    try:
        parsed = urlsplit(url)
    except ValueError:
        return _text(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        return _text(url)
    target = quote(url, safe=":/?#@!$&'*+,;=%-._~")
    return f"[{_text(url)}](<{target}>)"


def _fields(value: dict, prefix: str = ""):
    for key, item in value.items():
        field = prefix + key
        if isinstance(item, dict):
            yield from _fields(item, field + ".")
        else:
            yield f"| {_text(field)} | {_text(item)} |"


def _value_summary(claim: dict) -> str:
    value = claim["value"]
    if "source_rate" in value:
        rate = value["source_rate"]
        return (f"{rate['source_number_literal']} {rate['numerator_unit']}/{rate['denominator_unit']}; "
                f"{rate['numeric_qualifier']}; reference interval {rate['reference_period_kind']}; "
                f"{rate['scope_kind']}; not physical capacity")
    if "source_quantity" in value:
        quantity = value["source_quantity"]
        return (f"{quantity['source_number_literal']} × {quantity['source_unit_scale']} {quantity['unit']}; "
                f"{quantity['numeric_qualifier']}; {quantity['period_kind']} "
                f"{quantity['period_start']} to {quantity['period_end']}; {quantity['scope_kind']}")
    if claim["value_kind"] == "milestone":
        return (f"{value['status']}: {value['milestone_type']}; {value['date_low']} to {value['date_high']}; "
                f"precision {value.get('date_precision', 'not specified')}; midpoint {value['date_base'] or 'null (unknown)'}")
    if claim["value_kind"] == "scalar":
        return str(value["value"]) + (" " + value["unit"] if value["unit"] else "")
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def markdown_bytes(report: dict) -> bytes:
    entity = report["entity"]
    lines = ["# Evidence dossier: " + _text(entity["display_name"] or entity["stable_key"]), "",
        "Exact entity: " + _text(entity["id"]) + " (" + _text(entity["kind"]) + ")", "",
        "World date: " + _text(report["as_of"]) + "; knowledge cutoff: " + _text(report["recorded_at"]), "",
        "## At a glance", "",
        "These are recorded claims, not a certification of physical state. Source statements remain source reports.", "",
        "| Kind | Claim | Recorded value |", "| --- | --- | --- |",
        *["| " + _text(c["claim_kind"]) + " | " + _text(c["predicate"]) + " | " + _text(_value_summary(c)) + " |"
          for c in report["world_current_claims"] + report["source_statements"]], "",
        "## Interpretation", "", *["- " + _text(b) for b in report["boundaries"]], "",
        "## Evidence coverage", "", "| Field | Value |", "| --- | --- |",
        *_fields(report["evidence_summary"]), ""]
    sections = [
        ("World-current claims", "world_current_claims",
         "Observations, reconciled facts and estimates, as labeled. Source statements are excluded."),
        ("Source statements known at the cutoff", "source_statements",
         "Not superseded in knowledge time. Unknown or future effective dates do not imply current physical state."),
        ("Recorded history for this exact entity", "recorded_history",
         "Includes superseded versions. Old claims are not additional current corroboration."),
        ("Transitive dependency context", "dependency_context",
         "Dependency-only claims, possibly about other entities or outside the world-date selection. No automatic identity or phase assignment."),
    ]
    for title, key, explanation in sections:
        lines += ["## " + title, "", explanation, ""]
        if not report[key]:
            lines += ["No eligible records. This is not evidence of zero capacity, closure or inactivity.", ""]
        for claim in report[key]:
            lines += ["### " + _text(claim["predicate"]), "",
                "Claim: " + _text(claim["id"]), "",
                "Kind: " + _text(claim["claim_kind"]) + "; method: " + _text(claim["method"]), "",
                "| Field | Value |", "| --- | --- |",
                *_fields({k: claim[k] for k in ("subject_entity_id", "series_id", "series_stable_key",
                    "value_kind", "value_sha256", "valid_from", "valid_to", "recorded_at",
                    "superseded_at", "confidence", "created_by_run_id")}),
                *_fields(claim["value"]), "", "Evidence:", ""]
            for evidence in claim["evidence"]:
                lines += ["- " + _text(evidence["publisher"]) + " / family: " + _text(evidence["source_family"])
                    + "; " + _link(evidence["document_url"]),
                    "  - Role: " + _text(evidence["role"]) + "; locator: " + _text(evidence["locator"]),
                    "  - Title: " + _text(evidence["title"]) + "; source key: " + _text(evidence["source_key"])
                    + "; license: " + _text(evidence["license"]),
                    "  - Published: " + _text(evidence["published_at"]) + "; retrieved: " + _text(evidence["retrieved_at"])
                    + "; record observed: " + _text(evidence["source_record_observed_at"]),
                    "  - Document ID: " + _text(evidence["source_document_id"]) + "; SHA-256: " + _text(evidence["content_sha256"]),
                    "  - Evidence ID: " + _text(evidence["id"]) + "; source record ID: " + _text(evidence["source_record_id"])
                    + "; record SHA-256: " + _text(evidence["source_record_sha256"])]
            if not claim["evidence"]:
                lines += ["No direct evidence links; see the explicit dependency chain."]
            lines += ["", "Dependencies: " + _text(claim["dependencies"]), ""]
    lines += ["## Ingestion clocks", ""]
    if not report["ingestion_runs"]:
        lines += ["No explicit ingestion run references on the selected lineage.", ""]
    for run in report["ingestion_runs"]:
        lines += ["| Field | Value |", "| --- | --- |", *_fields(run), ""]
    return ("\n".join(lines).rstrip() + "\n").encode()


def write_dossier(database_path: Path, output: Path, *, subject_entity_id: str,
                  as_of: str, recorded_at: str, output_format: str = "markdown") -> dict:
    from .milestone_benchmark import write_new_bytes

    if output_format not in {"markdown", "json"}:
        raise ValueError("dossier format must be markdown or json")
    path = Path(database_path).absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError("database must be an existing regular, non-symlink file")
    protected = {path.resolve(), *(Path(str(path.resolve()) + suffix) for suffix in ("-wal", "-shm", "-journal"))}
    if Path(output).resolve() in protected:
        raise ValueError("dossier output cannot replace the database or its SQLite sidecars")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        report = build_evidence_dossier(connection, subject_entity_id=subject_entity_id,
            as_of=as_of, recorded_at=recorded_at)
    finally:
        connection.close()
    raw = markdown_bytes(report) if output_format == "markdown" else json_bytes(report)
    write_new_bytes(output, raw)
    return {"output": str(Path(output).absolute()), "format": output_format,
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "entity_id": subject_entity_id, "as_of": report["as_of"], "recorded_at": report["recorded_at"],
            "world_current_claims": len(report["world_current_claims"]),
            "source_statements": len(report["source_statements"]),
            "recorded_history": len(report["recorded_history"]),
            "dependency_context": len(report["dependency_context"])}
