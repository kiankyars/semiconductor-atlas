"""Portable proposals comparing accepted core project statements, never physical alerts.

Packets have packet_id, comparison_id (the accepted run), series_id (source-native
entity and predicate), accepted_at, generated_at, change {before, after,
classification}, proposals (zero or one), and provenance. Claim envelopes retain
typed values, source documents, source-record payloads and bounded evidence.
Offline validation checks internal consistency, not publisher bytes or admission
authenticity. Only the local builder performs the separate source-acceptance replay.
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from . import models, project_target_review as acceptance, repository
from .ai_critical_changes import _pretty_bytes, _strict_json
from .curated_capture import _instant, _now
from .curated_review import _hash
from .discovery_handoff import _digest, _keys, _text
from .source_checks import _read


FORMAT = "semiconductor-atlas-project-target-change-packet-v1"
RULE_VERSION = "accepted-source-project-target-period-comparison-v1"
VALIDATION_SCOPE = "portable_internal_consistency_only_not_source_acceptance_replay"
PREDICATE = "milestone.production_start"
MAX_PACKET_BYTES = 300_000
MAX_EXCERPT_CHARS = 8_000


def _period(value: object) -> models.MilestoneValue:
    row = _keys(value, {"milestone_type", "status", "date_low", "date_base", "date_high",
                        "date_precision", "date_literal"}, "source target period")
    if row["milestone_type"] != "production_start" or row["status"] != "expected" or row["date_base"] is not None:
        raise ValueError("comparison requires expected production targets without midpoints")
    return models.MilestoneValue(row["milestone_type"], models.MilestoneStatus.EXPECTED,
        row["date_low"], None, row["date_high"], row["date_precision"], row["date_literal"])


def classify_periods(before: dict, after: dict) -> str:
    """Compare inclusive calendar bounds, not probability intervals or elapsed work."""
    _period(before); _period(after)
    prior = before["date_low"], before["date_high"]
    current = after["date_low"], after["date_high"]
    if prior == current:
        return "reaffirmation"
    if current[1] < prior[0]:
        return "disjoint_earlier"
    if current[0] > prior[1]:
        return "disjoint_later"
    return "overlapping_changed"


def _series_id(entity_id: str) -> str:
    return repository.stable_id(RULE_VERSION, "source-native-series", entity_id, PREDICATE)


def _review_structure(review: dict) -> None:
    for key in ("reviewer", "rationale", "source_url"):
        _text(review[key])
    url = urlsplit(review["source_url"])
    if url.scheme != "https" or url.netloc != "www.nist.gov":
        raise ValueError("this accepted-source route requires the reviewed official NIST URL")
    project = _keys(review["project"], {"label", "source_native_subject", "narrative_subject", "scope", "canonical_facility_assignment"}, "reviewed project")
    for key in ("label", "source_native_subject", "narrative_subject", "scope"):
        _text(project[key])
    if any(value is not False for value in review["boundaries"].values()):
        raise ValueError("review boundaries must be literal false")
    source = _keys(review["source"], {"events", "candidate_id", "expected_event_id", "run_id", "document_id", "plan_sha256", "text_sha256"}, "source handoff")
    for key in ("candidate_id", "expected_event_id", "run_id", "plan_sha256", "text_sha256"):
        _digest(source[key])
    _text(source["document_id"])
    bindings = [review["access_review"], review["source_review"], source["events"]]
    for name in ("before", "after"):
        fields = {"body", "retrieved_at", "target", "spans"}
        if name == "before":
            fields |= {"manifest", "retrieval_timestamp_basis"}
        item = _keys(review[name], fields, "reviewed document")
        bindings.append(item["body"])
        if name == "before":
            bindings.append(item["manifest"])
            _text(item["retrieval_timestamp_basis"])
        if not isinstance(item["spans"], list) or len(item["spans"]) != 2:
            raise ValueError("review requires one narrative and one timeline span")
        roles = set()
        for span in item["spans"]:
            _keys(span, {"role", "start", "end", "sha256", "locator"}, "reviewed span")
            if type(span["start"]) is not int or type(span["end"]) is not int or not 0 <= span["start"] < span["end"]:
                raise ValueError("invalid reviewed byte offsets")
            _digest(span["sha256"]); _text(span["locator"])
            roles.add(span["role"])
        if roles != {"narrative", "timeline"}:
            raise ValueError("review must contain independent narrative and timeline locators")
    if review["before"]["body"]["sha256"] == review["after"]["body"]["sha256"] or _instant(review["before"]["retrieved_at"]) >= _instant(review["after"]["retrieved_at"]):
        raise ValueError("comparison requires distinct ordered retained source versions")
    for binding in bindings:
        _keys(binding, {"path", "sha256"}, "retained reference")
        relative = Path(_text(binding["path"]))
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != binding["path"]:
            raise ValueError("review references must remain canonical relative paths")
        _digest(binding["sha256"])


def _proposals(packet: dict) -> list[dict]:
    change = packet["change"]
    classification = classify_periods(change["before"], change["after"])
    if classification == "reaffirmation":
        return []
    before, after = packet["claims"]["before"], packet["claims"]["after"]
    wording = {"disjoint_earlier": "a wholly earlier stated period", "disjoint_later": "a wholly later stated period",
               "overlapping_changed": "changed, overlapping stated periods"}[classification]
    summary = (f"{packet['provenance']['project']['display_name']}: retained source versions state "
        f"{before['value']['date_literal']} then {after['value']['date_literal']} ({wording}). "
        "Source-stated target comparison only; no manufacturing attainment or exact acceleration is established.")
    return [{"fingerprint": _hash({"rule_version": RULE_VERSION, "comparison_id": packet["comparison_id"],
                "series_id": packet["series_id"], "before_claim_id": before["id"], "after_claim_id": after["id"],
                "before_value_sha256": before["value_sha256"], "after_value_sha256": after["value_sha256"],
                "classification": classification}),
        "rule_id": "source_target_period_revision", "rule_version": RULE_VERSION,
        "comparison_id": packet["comparison_id"], "series_id": packet["series_id"],
        "classification": classification, "summary": summary, "effective_at": None,
        "confidence": None, "exact_acceleration_days": None, "delivery_eligible": False}]


def _row(connection: sqlite3.Connection, table: str, identifier: str) -> dict:
    row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise ValueError(f"missing accepted core {table} row")
    return dict(row)


def _claim_envelope(connection: sqlite3.Connection, claim_id: str) -> dict:
    claim = _row(connection, "claim_versions", claim_id)
    series = _row(connection, "claim_series", claim["series_id"])
    values = connection.execute("SELECT * FROM milestone_values WHERE claim_version_id=?", (claim_id,)).fetchone()
    if values is None:
        raise ValueError("accepted target has no typed milestone value")
    value = {key: values[key] for key in ("milestone_type", "status", "date_low", "date_base", "date_high", "date_precision", "date_literal")}
    links = [dict(row) for row in connection.execute("SELECT * FROM claim_evidence WHERE claim_version_id=? ORDER BY id", (claim_id,))]
    if len(links) != 2 or len({row["source_document_id"] for row in links}) != 1 or len({row["source_record_id"] for row in links}) != 1:
        raise ValueError("accepted comparison requires two bounded supports for one document version")
    document = _row(connection, "source_documents", links[0]["source_document_id"])
    record = _row(connection, "source_records", links[0]["source_record_id"])
    record["payload"] = _strict_json(record.pop("payload_json").encode(), "accepted source record")
    metadata = _strict_json(document["metadata_json"].encode(), "source document metadata")
    if connection.execute("SELECT 1 FROM claim_dependencies WHERE claim_version_id=?", (claim_id,)).fetchone():
        raise ValueError("source target proposals do not accept unreviewed derived dependencies")
    for link in links:
        link["evidence_id"] = link.pop("id")
        fragment = re.search(r"; SHA-256 ([0-9a-f]{64})$", link["locator"])
        if fragment is None:
            raise ValueError("accepted evidence locator lacks its exact fragment hash")
        link["fragment_sha256"] = fragment[1]
    return {"id": claim_id, "core_series_id": claim["series_id"], "entity_id": series["subject_entity_id"],
        "predicate": series["predicate"], "claim_kind": claim["claim_kind"], "method": claim["method"],
        "value_kind": claim["value_kind"], "value_sha256": claim["value_sha256"], "value": value,
        "valid_from": claim["valid_from"], "valid_to": claim["valid_to"], "recorded_at": claim["recorded_at"],
        "confidence": claim["confidence"], "created_by_run_id": claim["created_by_run_id"],
        "document": {"id": document["id"], "source_id": document["source_id"], "url": document["document_url"],
            "content_sha256": document["content_sha256"], "retrieved_at": document["retrieved_at"],
            "published_at": document["published_at"], "retrieval_timestamp_basis": metadata["retrieval_timestamp_basis"]},
        "source_record": record, "evidence": links}


def _validate_claim(item: object, *, name: str, review: dict, packet: dict) -> None:
    claim = _keys(item, {"id", "core_series_id", "entity_id", "predicate", "claim_kind", "method", "value_kind",
        "value_sha256", "value", "valid_from", "valid_to", "recorded_at", "confidence", "created_by_run_id",
        "document", "source_record", "evidence"}, "accepted claim envelope")
    project, source = packet["provenance"]["project"], packet["provenance"]["source"]
    variant = review[name]
    expected_value = acceptance._target(variant["target"])
    actual_value = _period(claim["value"])
    if actual_value != expected_value or repository.value_sha256(actual_value) != _digest(claim["value_sha256"]):
        raise ValueError("accepted period hash, literal or precision differs from claim review")
    if any(claim[key] is not None for key in ("valid_from", "valid_to", "confidence")):
        raise ValueError("effective time and calibrated confidence must remain unknown")
    if (claim["entity_id"] != project["entity_id"] or claim["predicate"] != PREDICATE
            or claim["claim_kind"] != "source_statement" or claim["method"] != acceptance.RULE_VERSION
            or claim["value_kind"] != "milestone" or claim["recorded_at"] != packet["accepted_at"]
            or claim["created_by_run_id"] != packet["comparison_id"]):
        raise ValueError("claim kind, source scope, predicate or actual admission differs")
    document = _keys(claim["document"], {"id", "source_id", "url", "content_sha256", "retrieved_at", "published_at",
                                        "retrieval_timestamp_basis"}, "source document")
    expected_document_id = repository.stable_id(source["id"], variant["body"]["sha256"], variant["retrieved_at"])
    expected_series = repository.stable_id(project["entity_id"], expected_document_id, PREDICATE)
    if (document["id"] != expected_document_id or document["source_id"] != source["id"]
            or document["url"] != review["source_url"] or document["content_sha256"] != variant["body"]["sha256"]
            or document["retrieved_at"] != variant["retrieved_at"] or document["published_at"] is not None
            or claim["core_series_id"] != expected_series
            or claim["id"] != repository.stable_id(expected_series, packet["provenance"]["review"]["sha256"])):
        raise ValueError("source document or claim identifiers do not match the reviewed version")
    basis = variant.get("retrieval_timestamp_basis", "UTC microsecond response-finished invocation clock")
    if document["retrieval_timestamp_basis"] != basis:
        raise ValueError("source retrieval clock basis was changed")
    record = _keys(claim["source_record"], {"id", "ingestion_run_id", "source_document_id", "source_record_key", "observed_at",
                                          "record_sha256", "payload"}, "source record")
    record_id = repository.stable_id(packet["comparison_id"], document["id"], "record")
    if (record["id"] != record_id or record["ingestion_run_id"] != packet["comparison_id"]
            or record["source_document_id"] != document["id"] or record["observed_at"] != document["retrieved_at"]
            or record["source_record_key"] != review["project"]["source_native_subject"] + ":" + document["id"]
            or models.source_record_payload_sha256(record["payload"]) != _digest(record["record_sha256"])):
        raise ValueError("source record identity, hash or admission lineage differs")
    payload = _keys(record["payload"], {"project", "target", "spans", "review_sha256", "source_capture", "reviewed_at", "reviewer",
                                      "claim_effective_at", "retrieval_timestamp_basis"}, "source record payload")
    for key, expected in {"project": review["project"], "target": variant["target"],
            "review_sha256": packet["provenance"]["review"]["sha256"],
            "source_capture": review["source"] if name == "after" else variant["manifest"],
            "reviewed_at": review["reviewed_at"], "reviewer": review["reviewer"], "claim_effective_at": None,
            "retrieval_timestamp_basis": basis}.items():
        if payload[key] != expected:
            raise ValueError("source record payload differs from the reviewed comparison")
    if not isinstance(payload["spans"], list) or len(payload["spans"]) != 2 or not isinstance(claim["evidence"], list) or len(claim["evidence"]) != 2:
        raise ValueError("exactly two bounded evidence spans and links are required")
    evidence = []
    for span, approved in zip(payload["spans"], variant["spans"]):
        _keys(span, {"role", "start", "end", "sha256", "locator", "excerpt"}, "evidence span")
        if {key: value for key, value in span.items() if key != "excerpt"} != approved:
            raise ValueError("evidence span differs from explicit claim review")
        excerpt = _text(span["excerpt"])
        if len(excerpt) > MAX_EXCERPT_CHARS or re.search(r"</?[a-zA-Z][^>]*>", excerpt):
            raise ValueError("portable evidence must be bounded plain text, not raw publisher HTML")
        literal = re.escape(variant["target"]["literal"]).replace(r"half\ of", r"half (?:of|in)")
        if span["role"] == "timeline":
            expression = re.escape(review["project"]["source_native_subject"]) + r"\s*:\s*Expected to begin production in " + literal + r"\b"
        else:
            expression = r"production (?:beginning )?in the " + re.escape(review["project"]["narrative_subject"]) + r" (?:in |targeted for the )" + literal + r"\b"
        if not re.search(expression, excerpt):
            raise ValueError("retained excerpt does not support the reviewed subject and target together")
        locator = f"{span['locator']}; UTF-8 bytes [{span['start']},{span['end']}); SHA-256 {span['sha256']}"
        evidence.append({"evidence_id": repository.stable_id("claim-evidence", claim["id"], document["id"], record_id, "support", locator, excerpt),
            "fragment_sha256": span["sha256"], "claim_version_id": claim["id"], "source_document_id": document["id"], "source_record_id": record_id,
            "role": "support", "locator": locator, "excerpt": excerpt})
    if sorted(claim["evidence"], key=lambda row: row["evidence_id"]) != sorted(evidence, key=lambda row: row["evidence_id"]):
        raise ValueError("exact evidence links differ from the source record spans")


def _validate_packet(packet: object, clock: str) -> dict:
    row = _keys(packet, {"format", "rule_version", "packet_id", "comparison_id", "series_id", "accepted_at", "generated_at",
                        "change", "proposals", "provenance", "subject", "claims"}, "project change packet")
    if row["format"] != FORMAT or row["rule_version"] != RULE_VERSION:
        raise ValueError("unsupported project change packet")
    if len(_pretty_bytes(row)) > MAX_PACKET_BYTES:
        raise ValueError("portable project packet exceeds its bounded payload limit")
    if _hash({key: value for key, value in row.items() if key != "packet_id"}) != _digest(row["packet_id"]):
        raise ValueError("project packet content digest differs")
    provenance = _keys(row["provenance"], {"review", "acceptance", "source", "project", "producer", "verification_scope", "boundaries"}, "packet provenance")
    if provenance["verification_scope"] != VALIDATION_SCOPE or provenance["boundaries"] != acceptance.BOUNDARIES or any(value is not False for value in provenance["boundaries"].values()):
        raise ValueError("portable validation cannot claim source re-verification, attainment or delivery")
    review_bound = _keys(provenance["review"], {"sha256", "content", "raw_json"}, "retained claim review")
    if not isinstance(review_bound["raw_json"], str):
        raise ValueError("claim review must retain exact JSON bytes as UTF-8 text")
    raw = review_bound["raw_json"].encode("utf-8")
    review = _strict_json(raw, "retained claim review")
    if _hash(raw) != _digest(review_bound["sha256"]) or review != review_bound["content"]:
        raise ValueError("review exact-byte digest or parsed content differs")
    _keys(review, {"format", "reviewed_at", "reviewer", "decision", "source_url", "project", "before", "after", "source",
                  "access_review", "source_review", "rationale", "boundaries"}, "review")
    if review["format"] != acceptance.REVIEW_FORMAT or review["decision"] != acceptance.DECISION or review["boundaries"] != acceptance.BOUNDARIES:
        raise ValueError("packet lacks the separate explicit source-project claim review")
    _review_structure(review)
    ids = acceptance._ids({"review": review, "review_sha256": review_bound["sha256"]})
    project = _keys(provenance["project"], {"entity_id", "kind", "stable_key", "display_name", "source_native_scope", "canonical_facility_assignment"}, "project")
    if row["subject"] != project:
        raise ValueError("proposal subject differs from bound source-native project")
    if (project["entity_id"] != ids["entity_id"] or project["stable_key"] != ids["project_key"] or project["kind"] != "project"
            or project["display_name"] != review["project"]["label"] or project["source_native_scope"] != review["project"]
            or project["canonical_facility_assignment"] is not None or review["project"]["canonical_facility_assignment"] is not None):
        raise ValueError("source-native project scope or unresolved identity differs")
    source = _keys(provenance["source"], {"id", "publisher", "url"}, "source")
    if source != {"id": ids["source_id"], "publisher": "NIST", "url": review["source_url"]}:
        raise ValueError("source publisher or identity differs")
    if row["comparison_id"] != ids["run_id"] or row["series_id"] != _series_id(ids["entity_id"]):
        raise ValueError("comparison or source-native proposal series identity differs")
    run = _keys(provenance["acceptance"], {"run_id", "started_at", "completed_at", "rule_version", "code_sha256", "dependency_code_sha256",
                                         "timestamp_basis", "replayed_at_build"}, "acceptance provenance")
    if (run["run_id"] != row["comparison_id"] or run["completed_at"] != row["accepted_at"] or run["rule_version"] != acceptance.RULE_VERSION
            or run["timestamp_basis"] != "actual_database_admission" or run["replayed_at_build"] is not True):
        raise ValueError("packet does not identify a separately replayed actual core admission")
    _digest(run["code_sha256"])
    codes = run["dependency_code_sha256"]
    if not isinstance(codes, dict) or not codes or codes.get("semiconductor_atlas/project_target_review.py") != run["code_sha256"]:
        raise ValueError("acceptance importer fingerprint differs from its dependency ledger")
    for key, value in codes.items():
        if not isinstance(key, str) or not key.startswith("semiconductor_atlas/") or ".." in Path(key).parts:
            raise ValueError("invalid acceptance dependency path")
        _digest(value)
    producer = _keys(provenance["producer"], {"path", "sha256"}, "proposal producer")
    if producer["path"] != "semiconductor_atlas/project_target_changes.py":
        raise ValueError("unsupported proposal producer path")
    _digest(producer["sha256"])
    change = _keys(row["change"], {"before", "after", "classification"}, "reviewed change")
    claims = _keys(row["claims"], {"before", "after"}, "accepted before/after claims")
    for name in ("before", "after"):
        _validate_claim(claims[name], name=name, review=review, packet=row)
        if change[name] != claims[name]["value"]:
            raise ValueError("comparison period differs from its exact accepted claim")
    clocks = [review["before"]["retrieved_at"], review["after"]["retrieved_at"], review["reviewed_at"],
              run["started_at"], row["accepted_at"], row["generated_at"], clock]
    if any(_instant(a) > _instant(b) for a, b in zip(clocks, clocks[1:])) or _instant(run["started_at"]) >= _instant(row["accepted_at"]):
        raise ValueError("packet observation, review, acceptance or generation clocks are inconsistent")
    if change["classification"] != classify_periods(change["before"], change["after"]):
        raise ValueError("project period classification differs")
    if row["proposals"] != _proposals(row):
        raise ValueError("proposal values, identity, semantics or delivery boundary differ")
    return copy.deepcopy(row)


def validate_packet(packet: object, *, clock: str) -> dict:
    """Pure offline internal validation; no filesystem, database, source fetch or code reads."""
    try:
        return _validate_packet(packet, clock)
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError("malformed portable project change packet") from error


def _build_snapshot_packet(connection: sqlite3.Connection, review_path: str | Path, *, reference_root: str | Path,
                           source_queue: str | Path) -> dict:
    if connection.in_transaction:
        raise ValueError("project proposal builder requires no active database transaction")
    path = Path(review_path)
    raw = _read(path)
    review = _strict_json(raw, "claim review")
    review_sha = _hash(raw)
    run_id = repository.stable_id(acceptance.RULE_VERSION, review_sha)
    run = _row(connection, "ingestion_runs", run_id)
    params = _strict_json(run["parameters_json"].encode(), "accepted run parameters")
    if params.get("review_sha256") != review_sha or params.get("review") != review:
        raise ValueError("claim review must already exist as this immutable accepted run")
    initial_code = _hash(_read(Path(__file__)))
    changes_before = connection.total_changes
    query_only = connection.execute("PRAGMA query_only").fetchone()[0]
    connection.execute("PRAGMA query_only=ON")
    try:
        replay = acceptance.accept_review(connection, path, reference_root=reference_root, source_queue=source_queue)
        if replay.get("replayed") is not True or replay.get("database_writes") != 0:
            raise ValueError("proposal builder must never perform a new claim admission")
        source = _row(connection, "sources", run["source_id"])
        entity = _row(connection, "entities", replay["entity_id"])
        packet = {"format": FORMAT, "rule_version": RULE_VERSION, "comparison_id": run_id,
            "series_id": _series_id(entity["id"]), "accepted_at": replay["admitted_at"], "generated_at": _now(),
            "claims": {name: _claim_envelope(connection, claim_id) for name, claim_id in replay["claim_ids"].items()}, "proposals": [],
            "provenance": {"review": {"sha256": review_sha, "content": review, "raw_json": raw.decode("utf-8")},
                "acceptance": {"run_id": run_id, "started_at": run["started_at"], "completed_at": run["completed_at"],
                    "rule_version": params["rule_version"], "code_sha256": run["code_version"],
                    "dependency_code_sha256": params["dependency_code_sha256"], "timestamp_basis": params["acceptance_timestamp_basis"],
                    "replayed_at_build": True},
                "source": {"id": source["id"], "publisher": source["publisher"], "url": source["canonical_url"]},
                "project": {"entity_id": entity["id"], "kind": entity["kind"], "stable_key": entity["stable_key"],
                    "display_name": entity["display_name"], "source_native_scope": review["project"], "canonical_facility_assignment": None},
                "producer": {"path": "semiconductor_atlas/project_target_changes.py", "sha256": initial_code},
                "verification_scope": VALIDATION_SCOPE, "boundaries": copy.deepcopy(acceptance.BOUNDARIES)}}
        packet["subject"] = copy.deepcopy(packet["provenance"]["project"])
        packet["change"] = {name: copy.deepcopy(claim["value"]) for name, claim in packet["claims"].items()}
        packet["change"]["classification"] = classify_periods(packet["change"]["before"], packet["change"]["after"])
        packet["proposals"] = _proposals(packet)
        acceptance.accept_review(connection, path, reference_root=reference_root, source_queue=source_queue)
        if _read(path) != raw or _hash(_read(Path(__file__))) != initial_code or connection.total_changes != changes_before:
            raise ValueError("proposal inputs or target database changed across build boundary")
        packet["generated_at"] = _now()
        packet["packet_id"] = _hash(packet)
        return validate_packet(packet, clock=_now())
    finally:
        connection.execute(f"PRAGMA query_only={int(query_only)}")


def build_packet(connection: sqlite3.Connection, review_path: str | Path, *, reference_root: str | Path,
                 source_queue: str | Path) -> dict:
    """Read-only derivative of a coherent SQLite backup of an already accepted run."""
    if connection.in_transaction:
        raise ValueError("project proposal builder requires no active database transaction")
    raw = _read(Path(review_path))
    run_id = repository.stable_id(acceptance.RULE_VERSION, _hash(raw))
    _row(connection, "ingestion_runs", run_id)
    snapshot = sqlite3.connect(":memory:")
    snapshot.row_factory = sqlite3.Row
    try:
        connection.backup(snapshot)
        if _read(Path(review_path)) != raw:
            raise ValueError("claim review changed during the coherent core snapshot")
        return _build_snapshot_packet(snapshot, review_path, reference_root=reference_root, source_queue=source_queue)
    finally:
        snapshot.close()
