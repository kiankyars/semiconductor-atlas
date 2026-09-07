"""Explicitly reviewed, unknown-effective project target statements in the core store."""

from __future__ import annotations

import re
import os
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

from . import curated_capture, curated_review, database, models, repository
from .ai_critical_changes import _strict_json
from .curated_capture import _instant, _now, _text
from .discovery_handoff import _binding, _candidate, _current_source, _digest, _file_state
from .discovery_handoff import _history, _keys, _one, _ref
from .source_checks import _read


REVIEW_FORMAT = "semiconductor-atlas-project-target-claim-review-v1"
RULE_VERSION = "reviewed-source-native-project-target-v1"
DECISION = "accept_two_source_native_project_target_statements"
BOUNDARIES = dict.fromkeys(("canonical_facility_assignment", "baseline_modified", "capacity_claim",
    "manufacturing_attainment", "delivery_eligible", "raw_redistribution",
    "independent_corroboration", "calibrated_forecast", "exact_acceleration"), False)


def _relative(root: Path, value: str) -> Path:
    path = Path(_text(value))
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("reference must be a canonical repository-relative path")
    return root / path


def _snapshot(path: Path, root: Path) -> dict:
    review = _strict_json(_read(path), "project target review")
    bindings = [review["access_review"], review["source_review"], review["source"]["events"],
                review["before"]["manifest"], review["before"]["body"], review["after"]["body"]]
    files = {str(path): _file_state(path)}
    for binding in bindings:
        target, raw = _binding(root, binding)
        files[str(target)] = _file_state(target, raw)
    _, raw = _binding(root, review["source"]["events"])
    events = _strict_json(raw, "source events")["events"]
    curated_review._validate_events(events)
    captures = {}
    for event in events:
        if event["payload"]["kind"] != "capture_imported":
            continue
        packet = Path(event["payload"]["source_path"])
        inventory = curated_capture._inventory(packet)
        captures[str(packet)] = inventory
        for relative, expected in inventory.items():
            item = packet / relative
            state = _file_state(item)
            if any(state[key] != expected[key] for key in ("sha256", "bytes")):
                raise ValueError("capture changed during dependency inventory")
            files[str(item)] = state
        files[str(packet / "manifest.json")] = _file_state(packet / "manifest.json")
    for code in _code_files():
        files[str(code)] = _file_state(code)
    return {"files": files, "captures": captures}


def _code_files() -> set[Path]:
    pending, seen, result = [sys.modules[__name__]], set(), set()
    while pending:
        module = pending.pop()
        if module.__name__ in seen:
            continue
        seen.add(module.__name__)
        if getattr(module, "__file__", None):
            result.add(Path(module.__file__))
        for value in vars(module).values():
            name = value.__name__ if isinstance(value, ModuleType) else getattr(value, "__module__", "")
            if isinstance(name, str) and name.startswith("semiconductor_atlas.") and name not in seen and name in sys.modules:
                pending.append(sys.modules[name])
    result.update(Path(database.__file__).parent.joinpath("migrations").glob("*.sql"))
    return result


def _code_fingerprint(snapshot: dict) -> dict:
    package_root = Path(__file__).parent.parent
    return {path.relative_to(package_root).as_posix(): snapshot["files"][str(path)]["sha256"]
            for path in sorted(_code_files())}


def _target(row: object) -> models.MilestoneValue:
    item = _keys(row, {"literal", "precision", "date_low", "date_high"}, "target")
    literal = _text(item["literal"])
    year = re.fullmatch(r"([0-9]{4})", literal)
    half = re.fullmatch(r"(first|second) half of ([0-9]{4})", literal)
    if year:
        expected = ("year", f"{year[1]}-01-01", f"{year[1]}-12-31")
    elif half:
        expected = ("half_year", f"{half[2]}-{'01-01' if half[1] == 'first' else '07-01'}",
                    f"{half[2]}-{'06-30' if half[1] == 'first' else '12-31'}")
    else:
        raise ValueError("unsupported reviewed target literal; never infer a date midpoint")
    if (item["precision"], item["date_low"], item["date_high"]) != expected:
        raise ValueError("target bounds or precision contradict the source literal")
    return models.MilestoneValue("production_start", models.MilestoneStatus.EXPECTED,
        item["date_low"], None, item["date_high"], item["precision"], literal)


def _spans(body: bytes, rows: object, project: dict, target: dict) -> list[dict]:
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError("both narrative and timeline support are required")
    result = []
    for row in rows:
        item = _keys(row, {"role", "start", "end", "sha256", "locator"}, "evidence span")
        start, end = item["start"], item["end"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(body):
            raise ValueError("invalid UTF-8 byte span")
        fragment = body[start:end]
        if curated_review._hash(fragment) != _digest(item["sha256"]):
            raise ValueError("evidence span hash mismatch")
        visible = curated_capture.normalized_bytes(fragment, "html_visible_text_v1").decode()
        _text(item["locator"])
        literal = re.escape(target["literal"]).replace(r"half\ of", r"half (?:of|in)")
        if item["role"] == "timeline":
            pattern = re.escape(project["source_native_subject"]) + r"\s*:\s*Expected to begin production in " + literal + r"\b"
        elif item["role"] == "narrative":
            pattern = r"production (?:beginning )?in the " + re.escape(project["narrative_subject"]) + r" (?:in |targeted for the )" + literal + r"\b"
        else:
            raise ValueError("unknown evidence span role")
        if not re.search(pattern, visible):
            raise ValueError("evidence does not bind this project and target together")
        result.append({**item, "excerpt": visible})
    if {row["role"] for row in result} != {"narrative", "timeline"}:
        raise ValueError("one narrative and one timeline span required")
    return result


def _load(path: Path, root: Path) -> dict:
    raw = _read(path)
    review = _keys(_strict_json(raw, "project target review"), {"format", "reviewed_at", "reviewer",
        "decision", "source_url", "project", "before", "after", "source", "access_review",
        "source_review", "rationale", "boundaries"}, "project target review")
    if review["format"] != REVIEW_FORMAT or review["decision"] != DECISION:
        raise ValueError("separate explicit project claim acceptance is required")
    if review["boundaries"] != BOUNDARIES or any(value is not False for value in review["boundaries"].values()):
        raise ValueError("project target review cannot assert facility assignment, attainment or delivery")
    for field in ("reviewer", "source_url", "rationale"):
        _text(review[field])
    if urlsplit(review["source_url"]).scheme != "https" or urlsplit(review["source_url"]).netloc != "www.nist.gov":
        raise ValueError("this reviewed acquisition route requires an exact official NIST URL")
    project = _keys(review["project"], {"label", "source_native_subject", "narrative_subject", "scope",
        "canonical_facility_assignment"}, "source-native project")
    for field in ("label", "source_native_subject", "narrative_subject", "scope"):
        _text(project[field])
    if project["canonical_facility_assignment"] is not None:
        raise ValueError("canonical facility assignment is unresolved")
    source = _keys(review["source"], {"events", "candidate_id", "expected_event_id", "run_id",
        "document_id", "plan_sha256", "text_sha256"}, "source handoff")
    for field in ("candidate_id", "expected_event_id", "run_id", "plan_sha256", "text_sha256"):
        _digest(source[field])
    now = _now()
    if _instant(review["reviewed_at"]) > _instant(now):
        raise ValueError("claim review is in the future")
    events, report = _history(root, source["events"], curated_review, now)
    candidate = _candidate(report, source, "handed_off")
    if candidate["source_url"] != review["source_url"] or candidate["current_text_sha256"] != source["text_sha256"]:
        raise ValueError("source URL or reviewed text differs from handoff")
    _current_source(report, {"facility_key": candidate["facility_key"], "source_url": review["source_url"], "source": source})
    run = _one(report["runs"], "run_id", source["run_id"], "source capture")
    observation = _one(candidate["observations"], "run_id", source["run_id"], "source observation")
    packet = Path(run["source_path"])
    plan = _strict_json(_read(packet / "plan.json"), "capture plan")
    document = _one(plan["documents"], "id", source["document_id"], "exact source document")
    if (run["plan_sha256"] != source["plan_sha256"] or plan["review_record"] != review["access_review"]
            or document["url"] != review["source_url"] or observation["document_id"] != source["document_id"]):
        raise ValueError("source plan or document binding differs")
    access_path, access_raw = _binding(root, review["access_review"])
    access = _strict_json(access_raw, "access review")
    if access_raw != _read(packet / "review.json") or access.get("reviewed_at") != plan["reviewed_at"]:
        raise ValueError("access review differs from the captured approval")
    if access.get("decision") != "approve_exact_nist_documents_for_source_text_review_only":
        raise ValueError("unsupported bounded source acquisition approval")
    approved = _one(access.get("documents", []), "url", review["source_url"], "approved source")
    if approved.get("facility_key") != candidate["facility_key"] or approved.get("scope") != document["scope"]:
        raise ValueError("access review scope differs")
    if any(access.get("acquisition_boundary", {}).get(key) is not False for key in ("claim_acceptance", "delivery_eligible", "raw_redistribution")):
        raise ValueError("access approval must remain outside claim acceptance")
    _text(access.get("reviewer"))
    _, text_raw = _binding(root, review["source_review"])
    text_review = _strict_json(text_raw, "source text review")
    decision = candidate["decisions"][-1]
    if (decision["evidence_ref"] != _ref(review["source_review"])
            or text_review.get("decision") != "handoff_reviewed_text_version_for_separate_claim_review"
            or text_review.get("candidate_id") != source["candidate_id"]
            or text_review.get("expected_event_id") != decision["expected_event_id"]
            or text_review.get("facility_key") != candidate["facility_key"]
            or any(text_review.get(key) is not False for key in ("claim_acceptance", "baseline_modified", "delivery_eligible"))):
        raise ValueError("separate source-text handoff review is missing or mismatched")
    _text(text_review.get("reviewer"))
    at_text = curated_review._fold(events, as_of=text_review.get("reviewed_at"))
    pre = _one(at_text["candidates"], "id", candidate["id"], "source at review")
    if pre["status"] not in curated_review.OPEN_STATUSES or pre["requires_reopen"] or pre["last_event_id"] != decision["expected_event_id"]:
        raise ValueError("source-text predecision CAS was not current at review")
    _current_source(at_text, {"facility_key": candidate["facility_key"], "source_url": review["source_url"], "source": source})
    reviewed_capture = text_review.get("source_capture", {})
    if any(reviewed_capture.get(key) != value for key, value in {
            "manifest_sha256": source["run_id"], "body_sha256": observation["current_sha256"],
            "normalized_sha256": source["text_sha256"], "normalization": "html_visible_text_v1",
            "retrieved_at": observation["captured_at"]}.items()):
        raise ValueError("source-text review capture hashes or clock differ")
    variants = {}
    for name in ("before", "after"):
        fields = {"body", "retrieved_at", "target", "spans"}
        if name == "before":
            fields |= {"manifest", "retrieval_timestamp_basis"}
        row = _keys(review[name], fields, f"{name} document")
        body_path, body = _binding(root, row["body"])
        value = _target(row["target"])
        spans = _spans(body, row["spans"], project, row["target"])
        variants[name] = {"value": value, "spans": spans, "body_path": body_path}
        if name == "before":
            manifest_path, manifest_raw = _binding(root, row["manifest"])
            manifest = _strict_json(manifest_raw, "old source manifest")
            old = _one(manifest.get("inputs", []), "url", review["source_url"], "old source manifest record")
            if (manifest.get("format") != "semiconductor-atlas-source-inputs-v1"
                    or manifest_path.parent / old["path"] != body_path
                    or old.get("sha256") != row["body"]["sha256"] or old.get("bytes") != len(body)
                    or manifest.get("retrieved_at") != row["retrieved_at"]
                    or manifest.get("retrieval_timestamp_basis") != row["retrieval_timestamp_basis"]):
                raise ValueError("old source manifest or corrected batch clock binding differs")
            _text(row["retrieval_timestamp_basis"])
        elif (body_path != packet / "responses" / f"{source['document_id']}.body"
                or row["body"]["sha256"] != observation["current_sha256"]
                or row["retrieved_at"] != observation["captured_at"]):
            raise ValueError("after document differs from the handed-off observation")
    if review["before"]["body"]["sha256"] == review["after"]["body"]["sha256"]:
        raise ValueError("comparison requires distinct retained document versions")
    clocks = [plan["reviewed_at"], run["started_at"], run["finished_at"], observation["imported_at"],
              text_review["reviewed_at"], decision["recorded_at"], review["reviewed_at"], now]
    if any(_instant(a) > _instant(b) for a, b in zip(clocks, clocks[1:])):
        raise ValueError("approval, capture, source review and claim review chronology differs")
    if _instant(review["before"]["retrieved_at"]) >= _instant(review["after"]["retrieved_at"]):
        raise ValueError("before document must precede the after observation")
    if any(_instant(event["recorded_at"]) > _instant(review["reviewed_at"]) for event in events):
        raise ValueError("claim review binds source queue events it could not yet know")
    return {"review": review, "review_sha256": curated_review._hash(raw), "events": events,
            "variants": variants, "facility_key": candidate["facility_key"],
            "source_admitted_at": observation["imported_at"], "source_handoff_at": decision["recorded_at"]}


def _ids(data: dict) -> dict:
    review = data["review"]
    source_key = "reviewed-project-source:" + review["source_url"]
    project_key = source_key + ":" + review["project"]["source_native_subject"]
    return {"family_id": repository.stable_id(RULE_VERSION, "family"),
        "source_id": repository.stable_id(RULE_VERSION, "source", source_key), "source_key": source_key,
        "entity_id": repository.stable_id(RULE_VERSION, "project", project_key), "project_key": project_key,
        "run_id": repository.stable_id(RULE_VERSION, data["review_sha256"])}


def _populate(connection: sqlite3.Connection, data: dict, started: str, admitted: str, clocks: dict) -> dict:
    review, ids = data["review"], _ids(data)
    family = models.SourceFamily(ids["family_id"], "reviewed-source-project-targets", "Reviewed source-native project targets", clocks["family"])
    source = models.Source(ids["source_id"], family.id, ids["source_key"], "Reviewed project statements: " + review["source_url"],
        "NIST", review["source_url"], clocks["source"], "NIST public information; marked and third-party exceptions excluded; raw retained locally")
    repository.add_source_family(connection, family)
    repository.add_source(connection, source)
    parameters = {"rule_version": RULE_VERSION, "review_sha256": data["review_sha256"],
        "acceptance_timestamp_basis": "actual_database_admission",
        "accepted_at": admitted,
        "dependency_code_sha256": data["code_sha256"],
        "review": review, "lineage_creation_clocks": clocks}
    repository.add_ingestion_run(connection, models.IngestionRun(ids["run_id"], source.id, started,
        models.IngestionStatus.SUCCEEDED, admitted, curated_review._hash(_read(Path(__file__))), parameters=parameters))
    repository.add_entity(connection, models.Entity(ids["entity_id"], models.EntityKind.PROJECT, ids["project_key"],
        clocks["entity"], review["project"]["label"], created_by_run_id=clocks["entity_run"]))
    claims, documents = {}, {}
    for name in ("before", "after"):
        row, verified = review[name], data["variants"][name]
        document_id = repository.stable_id(ids["source_id"], row["body"]["sha256"], row["retrieved_at"])
        metadata = {"source_native_project_scope": review["project"], "retained_body": row["body"],
            "claim_effective_at": None, "publication_time": None,
            "retrieval_timestamp_basis": row.get("retrieval_timestamp_basis", "UTC microsecond response-finished invocation clock")}
        repository.add_source_document(connection, models.SourceDocument(document_id, source.id, review["source_url"],
            "NIST project target statement: " + review["project"]["label"], row["retrieved_at"], row["body"]["sha256"],
            media_type="text/html", license=source.license, metadata=metadata))
        payload = {"project": review["project"], "target": row["target"], "spans": verified["spans"],
            "review_sha256": data["review_sha256"], "source_capture": review["source"] if name == "after" else row["manifest"],
            "reviewed_at": review["reviewed_at"], "reviewer": review["reviewer"], "claim_effective_at": None,
            "retrieval_timestamp_basis": metadata["retrieval_timestamp_basis"]}
        record_id = repository.stable_id(ids["run_id"], document_id, "record")
        repository.add_source_record(connection, models.SourceRecord(record_id, ids["run_id"], document_id,
            review["project"]["source_native_subject"] + ":" + document_id,
            row["retrieved_at"], models.source_record_payload_sha256(payload), payload))
        series_id = repository.stable_id(ids["entity_id"], document_id, "milestone.production_start")
        repository.add_claim_series(connection, models.ClaimSeries(series_id, ids["entity_id"],
            ids["project_key"] + ":" + document_id + ":production_start", "milestone.production_start", models.ValueKind.MILESTONE,
            clocks["series"].get(series_id, admitted)))
        claim_id = repository.stable_id(series_id, data["review_sha256"])
        repository.insert_claim(connection, models.ClaimVersion(claim_id, series_id, None, admitted,
            models.ClaimKind.SOURCE_STATEMENT, RULE_VERSION, None, created_by_run_id=ids["run_id"],
            notes="Reviewed document-version target, not attainment; effective time and calibrated confidence unknown."), verified["value"],
            evidence=[models.EvidenceLink(document_id, source_record_id=record_id,
                locator=f"{span['locator']}; UTF-8 bytes [{span['start']},{span['end']}); SHA-256 {span['sha256']}",
                excerpt=span["excerpt"]) for span in verified["spans"]])
        claims[name], documents[name] = claim_id, document_id
    return {"format": "semiconductor-atlas-project-target-acceptance-v1", "rule_version": RULE_VERSION,
        "run_id": ids["run_id"], "entity_id": ids["entity_id"], "review_sha256": data["review_sha256"],
        "admitted_at": admitted, "claim_ids": claims, "document_ids": documents,
        "comparison": {"kind": "source_stated_target_reaffirmation" if review["before"]["target"] == review["after"]["target"] else "source_stated_target_revision", "before": review["before"]["target"],
            "after": review["after"]["target"], "exact_acceleration_days": None}, "boundaries": BOUNDARIES}


def _verify_replay(connection: sqlite3.Connection, data: dict, prior: sqlite3.Row) -> dict:
    params = _strict_json(prior["parameters_json"].encode(), "recorded review parameters")
    if params.get("review") != data["review"] or params.get("review_sha256") != data["review_sha256"]:
        raise ValueError("recorded review content conflicts")
    if params.get("dependency_code_sha256") != data["code_sha256"]:
        raise ValueError("acceptance-time helper code or migration fingerprint differs")
    expected, _ = database.initialize(":memory:")
    try:
        origin = params["lineage_creation_clocks"]["entity_run"]
        if origin != prior["id"]:
            for table, key, identifier in (("source_families", "id", _ids(data)["family_id"]),
                    ("sources", "id", _ids(data)["source_id"]), ("ingestion_runs", "id", origin)):
                row = connection.execute(f"SELECT * FROM {table} WHERE {key}=?", (identifier,)).fetchone()
                if row is None:
                    raise ValueError("missing original project lineage")
                expected.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in row)})", tuple(row))
        result = _populate(expected, data, prior["started_at"], prior["completed_at"], params["lineage_creation_clocks"])
        tables = [row[0] for row in expected.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'schema_migrations'")]
        for table in tables:
            for row in expected.execute(f"SELECT * FROM {table}"):
                keys = [col["name"] for col in expected.execute(f"PRAGMA table_info({table})") if col["pk"]]
                actual = connection.execute(f"SELECT * FROM {table} WHERE " + " AND ".join(f"{key} IS ?" for key in keys), [row[key] for key in keys]).fetchone()
                actual_values = dict(actual) if actual is not None else None
                if (table == "claim_versions" and actual_values is not None and row["superseded_at"] is None
                        and actual_values["superseded_at"] is not None
                        and _instant(actual_values["superseded_at"]) > _instant(prior["completed_at"])):
                    actual_values["superseded_at"] = None
                if actual_values != dict(row):
                    raise ValueError(f"recorded project import differs in {table}")
        for name, claim in result["claim_ids"].items():
            for table in ("claim_evidence", "claim_dependencies"):
                wanted = sorted(tuple(row) for row in expected.execute(f"SELECT * FROM {table} WHERE claim_version_id=?", (claim,)))
                actual = sorted(tuple(row) for row in connection.execute(f"SELECT * FROM {table} WHERE claim_version_id=?", (claim,)))
                if wanted != actual:
                    raise ValueError("recorded project claim lineage differs")
        for table, owner in (("source_records", "ingestion_run_id"),
                             ("ingestion_run_documents", "ingestion_run_id"),
                             ("claim_versions", "created_by_run_id"), ("entities", "created_by_run_id")):
            wanted = [dict(row) for row in expected.execute(f"SELECT * FROM {table} WHERE {owner}=?", (prior["id"],))]
            actual = [dict(row) for row in connection.execute(f"SELECT * FROM {table} WHERE {owner}=?", (prior["id"],))]
            if table == "claim_versions":
                for row in actual:
                    if row["superseded_at"] is not None and _instant(row["superseded_at"]) > _instant(prior["completed_at"]):
                        row["superseded_at"] = None
            if sorted(map(curated_review._hash, wanted)) != sorted(map(curated_review._hash, actual)):
                raise ValueError(f"unexpected or altered {table} attached to reviewed admission")
        if repository.validate_database(connection):
            raise ValueError("recorded database integrity or supersession lineage differs")
        return {**result, "replayed": True, "database_writes": 0}
    finally:
        expected.close()


def accept_review(connection: sqlite3.Connection, review_path: str | Path, *, reference_root: str | Path,
                  source_queue: str | Path) -> dict:
    """Own one atomic admission; exact replay checks rows without writing to this database."""
    if connection.in_transaction:
        raise ValueError("project acceptance requires ownership of its database transaction")
    if database.schema_version(connection) < 5:
        raise ValueError("project targets require schema 5; migrate a working copy first")
    queue_path = Path(source_queue)
    for entry in connection.execute("PRAGMA database_list"):
        if entry["file"] and os.path.samefile(entry["file"], queue_path):
            raise ValueError("source queue and core database must be different physical files")
    path, root = Path(review_path).resolve(), Path(reference_root).resolve()
    started = _now()
    try:
        snapshot = _snapshot(path, root)
        data = _load(path, root)
        data["code_sha256"] = _code_fingerprint(snapshot)
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError("malformed project target review") from error
    ids = _ids(data)
    prior = connection.execute("SELECT * FROM ingestion_runs WHERE id=?", (ids["run_id"],)).fetchone()
    live = curated_review.export_events(source_queue)["events"]
    if (live[:len(data["events"])] != data["events"] or not prior and live != data["events"]):
        raise ValueError("current source queue differs from reviewed export lineage")
    if prior:
        result = _verify_replay(connection, data, prior)
        if _snapshot(path, root) != snapshot:
            raise ValueError("project inputs changed across replay boundary")
        return result
    admitted = _now()
    if _instant(admitted) <= _instant(started) or _instant(data["review"]["reviewed_at"]) > _instant(admitted):
        raise ValueError("actual admission clock must follow validation start and review")
    clocks = {}
    for kind, table, identifier in (("family", "source_families", ids["family_id"]),
                                  ("source", "sources", ids["source_id"]), ("entity", "entities", ids["entity_id"])):
        row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
        clocks[kind] = row["created_at"] if row else admitted
        if kind == "entity":
            clocks["entity_run"] = row["created_by_run_id"] if row else ids["run_id"]
    clocks["series"] = {row["id"]: row["created_at"] for row in connection.execute(
        "SELECT id, created_at FROM claim_series WHERE subject_entity_id=?", (ids["entity_id"],))}
    with curated_review._connection(source_queue, write=True) as reserved:
        if curated_review._events(reserved) != live:
            raise ValueError("source queue changed before acceptance reservation")
        connection.execute("BEGIN IMMEDIATE")
        try:
            result = _populate(connection, data, started, admitted, clocks)
            errors = repository.validate_database(connection)
            if errors:
                raise ValueError("project acceptance database validation: " + "; ".join(errors))
            if _snapshot(path, root) != snapshot or curated_review._events(reserved) != live:
                raise ValueError("project inputs or queue changed across acceptance boundary")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return {**result, "replayed": False, "database_writes": "one_atomic_core_admission"}
