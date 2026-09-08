"""Source-specific enrichment of an immutable TSMC core snapshot, not entity resolution.

The trusted review and its parent snapshot stay immutable. Verification reconstructs
the whole successor from that parent; the old import verifier still targets its parent.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from scripts import frozen_runtime as runtime
from . import database, models, repository, realized_milestones


RULE = "tsmc-operating-fab-correspondence-v1"
FORMAT = "semiconductor-atlas-tsmc-fab-correspondence-review-v1"
PARENT_RULE = "frozen-verified-realized-fab-cohort-core-v1"
SOURCE_SHA = "c3ebd05cd8fb383f53fc21a0ac497ee12cf908b709c380f9bec4f39c4916647b"
EVENT = "commercial_production_commencement"
LOCATION_GROUPS = (
    (("2", "3", "5", "8", "12", "20"), "Hsinchu Science Park",
     "Fabs 2, 3, 5, 8, 12 and Fab 20 are located in Hsinchu Science Park."),
    (("6", "14", "18", "22"), "Southern Taiwan Science Park",
     "Fab 6, Fab 14, Fab 18, and Fab 22 are located in the Southern Taiwan Science Park."),
    (("15",), "Central Taiwan Science Park", "Fab 15 is located in Central Taiwan Science Park."),
    (("11",), "Washington State, United States", "Fab 11 is located in the Washington State, United States."),
    (("10",), "Shanghai, China", "Fab 10 is located in Shanghai, China,"),
    (("16",), "Nanjing, China", "Fab 16 is located in Nanjing, China,"),
    (("21",), "Arizona, U.S.", "Fab 21 is located in Arizona, U.S."),
    (("23",), "Kumamoto, Japan", "Fab 23 is located in Kumamoto, Japan."),
)
MONTHS = {
    "11": {"literal": "October 1998", "low": "1998-10-01", "high": "1998-10-31", "evidence": "washington"},
    "16": {"literal": "April 2018", "low": "2018-04-01", "high": "2018-04-30", "evidence": "nanjing"},
}
BOUNDARIES = dict.fromkeys(("canonical_identity_assigned", "project_phase_assigned",
    "geometry_inferred", "historical_location_inferred", "capacity_inferred", "physical_truth_asserted",
    "independent_corroboration", "forecast_created", "forecast_scored", "redistribution_authorized",
    "parent_modified", "scheduled_execution_rerouted"), False)


def now():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def clock(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timezone-aware chronology required")
    return parsed


def codes():
    root = Path(__file__).parent.parent
    paths = [Path(module.__file__) for module in (database, models, repository, realized_milestones, runtime)]
    paths += [Path(__file__), root / "scripts/enrich_tsmc_fab_correspondence.py"]
    paths += list(Path(database.__file__).with_name("migrations").glob("*.sql"))
    return {str(path.relative_to(root)): runtime.digest(runtime.read(path)) for path in sorted(paths)}


_LOADED_CODES = codes()


def validate_review(raw, body, expected_sha256):
    runtime._hex(expected_sha256, 64)
    if runtime.digest(raw) != expected_sha256:
        raise ValueError("trusted review hash mismatch")
    review = realized_milestones._json(raw)
    runtime._keys(review, {"format", "reviewed_at", "reviewed_by", "prior_exposure", "rationale",
        "parent_database_sha256", "parent_run_id", "cohort_sha256", "source_sha256", "source_bytes",
        "evidence", "rows", "excluded_or_unresolved"})
    if review["format"] != FORMAT or review["source_sha256"] != SOURCE_SHA:
        raise ValueError("unsupported source-specific review")
    if runtime.digest(body) != SOURCE_SHA or len(body) != review["source_bytes"]:
        raise ValueError("retained source bytes differ")
    for key in ("parent_database_sha256", "cohort_sha256"):
        runtime._hex(review[key], 64)
    clock(review["reviewed_at"])
    for key in ("reviewed_by", "prior_exposure", "rationale"):
        if not isinstance(review[key], str) or not review[key].strip():
            raise ValueError("review identity, exposure and rationale required")
    runtime._keys(review["evidence"], {"locations", "washington", "china", "nanjing", "arizona", "jasm"})
    texts = {}
    for key, span in review["evidence"].items():
        runtime._keys(span, {"start", "end", "sha256"})
        if (type(span["start"]) is not int or type(span["end"]) is not int
                or not 0 <= span["start"] < span["end"] <= len(body)
                or runtime.digest(body[span["start"]:span["end"]]) != span["sha256"]):
            raise ValueError("evidence span mismatch")
        texts[key] = realized_milestones._EvidenceHTML(body, span["start"], span["end"]).text(span)
    locations = {}
    for fabs, literal, sentence in LOCATION_GROUPS:
        if sentence not in texts["locations"]:
            raise ValueError("geographic subject correspondence not supported")
        locations.update(dict.fromkeys(fabs, literal))
    expected_rows = [{"fab": fab, "location": locations[fab], "commercial_month": MONTHS.get(fab)}
                     for fab in sorted(locations, key=int)]
    if review["rows"] != expected_rows:
        raise ValueError("complete source-ordered sixteen-row scope or exact assertion differs")
    for fab, month in MONTHS.items():
        text = texts[month["evidence"]]
        phrase = ("commercial production commenced in " if fab == "11" else "commenced commercial production in ")
        if f"manufacturing entity is Fab {fab})" not in text or phrase + month["literal"] + "." not in text:
            raise ValueError("same-fab exact commercial commencement month not supported")
    for item in review["excluded_or_unresolved"]:
        runtime._keys(item, {"candidate", "decision", "evidence", "reason"})
        if (item["decision"] not in {"reject", "defer"} or not item["reason"]
                or not item["evidence"] or not set(item["evidence"]) <= set(texts)):
            raise ValueError("invalid excluded correspondence review")
    return review


def open_image(raw):
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if database.schema_version(connection) != 6 or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("expected intact schema-6 parent image")
        return connection
    except BaseException:
        connection.close()
        raise


def populate(connection, review, review_sha256, started, admitted, code_sha256):
    parent = connection.execute("SELECT * FROM ingestion_runs WHERE id=?", (review["parent_run_id"],)).fetchone()
    if parent is None or not (clock(parent["completed_at"]) <= clock(review["reviewed_at"]) <= clock(started) < clock(admitted)):
        raise ValueError("parent/review/admission chronology differs")
    parameters = json.loads(parent["parameters_json"])
    if (parameters["rule_version"] != PARENT_RULE
            or parameters["trusted"]["cohort_sha256"] != review["cohort_sha256"]):
        raise ValueError("parent cohort identity differs")
    cohort = parameters["cohort"]
    source = cohort["anchor"]["review"]["source"]
    if source["content_sha256"] != review["source_sha256"] or clock(source["retrieved_at"]) > clock(review["reviewed_at"]):
        raise ValueError("parent source identity or chronology differs")
    document_id = parent["input_document_id"]
    document = connection.execute("SELECT * FROM source_documents WHERE id=?", (document_id,)).fetchone()
    if document is None or document["content_sha256"] != SOURCE_SHA:
        raise ValueError("parent source document differs")
    cases = cohort["cases"]
    if ([case["subject"]["source_native_id"] for case in cases]
            != ["tsmc-2025-20f:fab:" + row["fab"] for row in review["rows"]]
            or any(case["decision"] == "defer" for case in cases)):
        raise ValueError("parent admitted cohort inventory differs")
    run_id = repository.stable_id(RULE, review_sha256)
    if connection.execute("SELECT 1 FROM ingestion_runs WHERE id=?", (run_id,)).fetchone():
        raise ValueError("successor already contains this review")
    repository.add_ingestion_run(connection, models.IngestionRun(run_id, parent["source_id"], started,
        models.IngestionStatus.SUCCEEDED, admitted, code_sha256["semiconductor_atlas/fab_correspondence.py"],
        document_id, {"rule_version": RULE, "review_sha256": review_sha256, "review": review,
            "code_sha256": code_sha256, "boundaries": BOUNDARIES, "rights": cohort["rights"],
            "selection": cohort["selection"], "accepted_at": admitted, "interpreter": runtime.interpreter(),
            "verification_contract": "exact_successor_reconstruction_from_retained_parent_snapshot"}))
    result = {"rule_version": RULE, "run_id": run_id, "admitted_at": admitted,
              "review_sha256": review_sha256, "parent_database_sha256": review["parent_database_sha256"],
              "boundaries": BOUNDARIES, "claims": []}
    for row, case in zip(review["rows"], cases, strict=True):
        matches = connection.execute("""SELECT e.id AS entity_id, c.id AS claim_id, m.*
            FROM entities e JOIN claim_series s ON s.subject_entity_id=e.id
            JOIN claim_versions c ON c.series_id=s.id JOIN milestone_values m ON m.claim_version_id=c.id
            WHERE e.stable_key=? AND c.created_by_run_id=?""",
            (PARENT_RULE + ":" + case["subject"]["source_native_id"], parent["id"])).fetchall()
        if (len(matches) != 1 or matches[0]["milestone_type"] != EVENT
                or matches[0]["date_literal"] != case["event"]["literal"]):
            raise ValueError("exact source-native parent subject/year missing")
        subject = matches[0]
        record_id = repository.stable_id(RULE, review_sha256, row["fab"], "record")
        payload = {"review_row": row, "review_sha256": review_sha256, "core_admitted_at": admitted,
            "parent_year_claim_id": subject["claim_id"], "source_native_id": case["subject"]["source_native_id"],
            "project_phase_correspondence": "unresolved", "canonical_entity_id": None}
        repository.add_source_record(connection, models.SourceRecord(record_id, run_id, document_id,
            RULE + ":fab:" + row["fab"], source["retrieved_at"], models.source_record_payload_sha256(payload), payload))
        assertions = [("location", "facility.source_reported_location", models.ScalarValue(models.ScalarType.TEXT,
            row["location"]), "locations")]
        month = row["commercial_month"]
        if month:
            if not subject["date_low"] <= month["low"] <= month["high"] <= subject["date_high"]:
                raise ValueError("narrative month must refine the parent year")
            assertions.append(("commercial_month", "milestone." + EVENT,
                models.MilestoneValue(EVENT, models.MilestoneStatus.COMPLETED, month["low"], None,
                    month["high"], "month", month["literal"]), month["evidence"]))
        for name, predicate, value, support in assertions:
            key = RULE + ":fab:" + row["fab"] + ":" + name
            series_id, claim_id = (repository.stable_id(key, kind) for kind in ("series", "claim"))
            repository.add_claim_series(connection, models.ClaimSeries(series_id, subject["entity_id"], key,
                predicate, value.kind, admitted))
            evidence = []
            for label, role, span in ((support, models.EvidenceRole.SUPPORT, review["evidence"][support]),
                    ("table row", models.EvidenceRole.CONTEXT, case["evidence"]),
                    ("table header", models.EvidenceRole.CONTEXT, cohort["anchor"]["review"]["evidence"]["header"])):
                evidence.append(models.EvidenceLink(document_id, role, record_id,
                    f"{label}; UTF-8 bytes [{span['start']},{span['end']}); SHA-256 {span['sha256']}"))
            repository.insert_claim(connection, models.ClaimVersion(claim_id, series_id, None, admitted,
                models.ClaimKind.SOURCE_STATEMENT, RULE, None, created_by_run_id=run_id,
                notes="Same-document source correspondence; unknown effective time, no canonical project phase or independent physical corroboration."),
                value, evidence=evidence, dependencies=[models.DependencyLink(subject["claim_id"])])
            result["claims"].append({"fab": row["fab"], "assertion": name, "claim_id": claim_id,
                "entity_id": subject["entity_id"], "parent_year_claim_id": subject["claim_id"]})
    issues = repository.validate_database(connection)
    if issues:
        raise ValueError("successor database validation failed: " + str(issues[:3]))
    connection.commit()
    return result


def enrich(*, parent, review_path, source_body, expected_review_sha256, output, verify_only=False,
           expected_output_sha256=None):
    started, current_codes = now(), codes()
    if current_codes != _LOADED_CODES:
        raise ValueError("loaded implementation changed")
    if verify_only:
        runtime._hex(expected_output_sha256, 64)
    elif expected_output_sha256 is not None:
        raise ValueError("successor hash is supplied for verification, not new admission")
    paths = {"parent": Path(parent), "review": Path(review_path), "source": Path(source_body)}
    raw = {key: runtime.read(path, limit=1_000_000_000 if key == "parent" else 20_000_000)
           for key, path in paths.items()}
    review = validate_review(raw["review"], raw["source"], expected_review_sha256)
    if runtime.digest(raw["parent"]) != review["parent_database_sha256"]:
        raise ValueError("trusted parent database hash mismatch")
    target = Path(output)
    if not verify_only and (target.exists() or target.is_symlink()):
        raise ValueError("successor output must not exist")
    connection = open_image(raw["parent"])
    try:
        if verify_only:
            successor = runtime.read(target, limit=1_000_000_000)
            if runtime.digest(successor) != expected_output_sha256:
                raise ValueError("trusted successor hash mismatch")
            actual = open_image(successor)
            try:
                run_id = repository.stable_id(RULE, expected_review_sha256)
                run = actual.execute("SELECT * FROM ingestion_runs WHERE id=?", (run_id,)).fetchone()
                if run is None or clock(run["completed_at"]) > clock(started):
                    raise ValueError("missing or future successor admission")
                admission_start, admitted = run["started_at"], run["completed_at"]
            finally:
                actual.close()
        else:
            admission_start, admitted = started, now()
        result = populate(connection, review, expected_review_sha256, admission_start, admitted, current_codes)
        generated = connection.serialize()
        if verify_only and generated != successor:
            raise ValueError("successor differs from exact parent-plus-reviewed-assertions reconstruction")
        for key, path in paths.items():
            if runtime.read(path, limit=1_000_000_000 if key == "parent" else 20_000_000) != raw[key]:
                raise ValueError("input changed during enrichment")
        if codes() != current_codes:
            raise ValueError("implementation changed during enrichment")
        if verify_only and runtime.read(target, limit=1_000_000_000) != successor:
            raise ValueError("successor changed during verification")
        if not verify_only:
            runtime.write_new(target, generated)
        return {**result, "database_sha256": runtime.digest(generated), "database_bytes": len(generated),
                "verified": verify_only, "database_writes": not verify_only,
                "source_reported_locations": 16, "additional_month_assertions": 2,
                "distinct_retrospective_fabs": 16, "unresolved_project_phases": 16}
    finally:
        connection.close()
