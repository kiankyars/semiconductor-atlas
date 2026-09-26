"""Complete reviewed operating-table cohort, preserving the prior observation."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from . import milestone_benchmark as benchmark, realized_milestones as realized


REVIEW_FORMAT = "semiconductor-atlas-realized-fab-table-review-v1"
ARTIFACT_FORMAT = "semiconductor-atlas-realized-fab-table-cohort-v1"
SCOPE = "all_data_rows_in_verified_operating_fab_table"
EVENT = "commercial_production_commencement"
SELECTION = {
    "population": "issuer_reported_fabs_in_operation_at_table_as_of",
    "excluded_from_enumeration": "unlisted_projects_including_unbuilt_delayed_cancelled_or_closed",
    "survivorship_bias": True,
    "global_or_issuer_historical_project_census": False,
}
BOUNDARIES = {**realized.BOUNDARIES, "training_dataset_established": False,
              "geographic_generalization_established": False,
              "global_first_observation_time_established": False}
canonical_bytes = benchmark.canonical_bytes
write_new = benchmark.write_new


def _now():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _disk_codes():
    return {**realized._codes(), Path(__file__).name: benchmark._hash(Path(__file__).read_bytes())}


_LOADED_CODES = _disk_codes()


def _codes():
    current = _disk_codes()
    if current != _LOADED_CODES:
        raise ValueError("cohort producer changed since module loading")
    return current


def _text(value, field):
    if not isinstance(value, str) or not 20 <= len(value.strip()) <= 4_000:
        raise ValueError(field + " requires substantive bounded review text")


def _review(value):
    value = realized._json(value if isinstance(value, bytes) else canonical_bytes(value))
    benchmark._keys(value, {"format", "scope", "anchor_file_sha256", "reviewed_by", "reviewed_at",
                           "prior_exposure", "rationale", "rows"}, "cohort review")
    if value["format"] != REVIEW_FORMAT or value["scope"] != SCOPE:
        raise ValueError("unsupported complete-table review contract")
    benchmark._digest(value["anchor_file_sha256"])
    benchmark._text(value["reviewed_by"], "reviewer")
    benchmark._clock(value["reviewed_at"])
    _text(value["prior_exposure"], "prior exposure")
    _text(value["rationale"], "cohort rationale")
    if not isinstance(value["rows"], list) or not 1 <= len(value["rows"]) <= 1_000:
        raise ValueError("cohort review requires a bounded complete row inventory")
    for row in value["rows"]:
        benchmark._keys(row, {"fab", "year", "evidence", "decision", "reason"}, "reviewed table row")
        if not isinstance(row["fab"], str) or not re.fullmatch(r"[1-9][0-9]*", row["fab"]):
            raise ValueError("fab must retain the exact source-native integer label")
        if not isinstance(row["year"], str) or not re.fullmatch(r"[12][0-9]{3}", row["year"]):
            raise ValueError("row year must retain its source literal")
        if row["decision"] not in ("accept_source_report", "carry_forward", "defer"):
            raise ValueError("unsupported row decision")
        _text(row["reason"], "row rationale")
        span = benchmark._keys(row["evidence"], {"start", "end", "sha256"}, "row evidence")
        benchmark._digest(span["sha256"])
        if type(span["start"]) is not int or type(span["end"]) is not int or not 0 <= span["start"] < span["end"]:
            raise ValueError("row evidence requires exact UTF-8 byte offsets")
    return value


def _inventory(anchor, body):
    spans = anchor["review"]["evidence"]
    document = realized._EvidenceHTML(body, spans["intro"]["start"], spans["table"]["end"])
    matched = realized._INTRO.fullmatch(document.text(spans["intro"]))
    as_of = datetime.strptime(matched["date"], "%B %d, %Y").date().isoformat()
    found_header, rows = False, []
    for node in document.nodes:
        if node.tag != "tr":
            continue
        cells = [child.text() for child in node.children
                 if isinstance(child, realized._Node) and child.tag == "td"]
        if cells == realized._HEADERS:
            found_header = True
            continue
        if not found_header:
            continue
        fab, year = cells[0], cells[2]
        if not re.fullmatch(r"[1-9][0-9]*", fab) or not re.fullmatch(r"[12][0-9]{3}", year):
            raise ValueError("unsupported source-native fab label or year literal")
        if year + "-12-31" > as_of or year + "-12-31" > anchor["review"]["source"]["published_at"]:
            raise ValueError("reported realized year postdates table state or publication")
        rows.append({"fab": fab, "year": year, "evidence": {
            "start": node.start, "end": node.end,
            "sha256": benchmark._hash(body[node.start:node.end])}})
    if not 1 <= len(rows) <= 1_000 or len({row["fab"] for row in rows}) != len(rows):
        raise ValueError("duplicate or unbounded source-native table population")
    return rows, as_of


def validate_review(review, *, anchor: bytes, body: bytes, provenance: bytes):
    """Verify the prior admission and every reviewed table row without recording anything."""
    review = _review(review)
    if not isinstance(anchor, bytes) or benchmark._hash(anchor) != review["anchor_file_sha256"]:
        raise ValueError("exact prior observation artifact bytes mismatch")
    prior = benchmark._load(anchor, realized.ARTIFACT_FORMAT)
    realized.verify(anchor, body=body, provenance=provenance)
    if not (benchmark._clock(prior["admitted_at"]) <= benchmark._clock(review["reviewed_at"])
            <= benchmark._clock(_now())):
        raise ValueError("cohort review must follow the existing admission and cannot be future-dated")
    inventory, as_of = _inventory(prior, body)
    actual = [{key: row[key] for key in ("fab", "year", "evidence")} for row in review["rows"]]
    if actual != inventory:
        raise ValueError("review must enumerate every exact table row once in source order")
    for row in review["rows"]:
        is_prior = row["fab"] == "21"
        if is_prior != (row["decision"] == "carry_forward"):
            raise ValueError("the verified prior Fab 21 must be carried forward exactly once, never readmitted or dropped")
        if is_prior and row["year"] != prior["review"]["event"]["literal"]:
            raise ValueError("prior observation event differs from table row")
    return {"review": review, "anchor": prior, "table_as_of": as_of, "table_rows": len(inventory)}


def _materialize(checked, started, admitted, codes):
    review, prior = checked["review"], checked["anchor"]
    cases = []
    counts = {"newly_accepted": 0, "carried_forward": 0, "deferred": 0}
    for row in review["rows"]:
        accepted = row["decision"] != "defer"
        carried = row["decision"] == "carry_forward"
        counts["carried_forward" if carried else "newly_accepted" if accepted else "deferred"] += 1
        subject = {"source_native_id": realized.SOURCE_ID + ":fab:" + row["fab"],
                   "kind": "facility", "label": "Fab " + row["fab"],
                   "scope": "issuer_operating_fab_table", "canonical_entity_id": None, "location": None}
        event = {"event_type": EVENT, "low": row["year"] + "-01-01", "base": None,
                 "high": row["year"] + "-12-31", "precision": "year", "literal": row["year"]} if accepted else None
        if carried and (subject != prior["review"]["subject"] or event != prior["review"]["event"]):
            raise ValueError("carried observation differs from the verified original admission")
        key = benchmark._hash({"document_sha256": prior["review"]["source"]["content_sha256"],
                               "source_native_id": subject["source_native_id"], "event_type": EVENT})
        cases.append({"source_report_key": key, "subject": subject, "decision": row["decision"],
                      "event": event, "evidence": row["evidence"], "reason": row["reason"],
                      "recorded_at": prior["admitted_at"] if carried else admitted if accepted else None,
                      "prior_artifact_sha256": prior["sha256"] if carried else None})
    return benchmark._seal({"format": ARTIFACT_FORMAT, "review": review,
        "review_sha256": benchmark._hash(review), "anchor": prior,
        "table_as_of": checked["table_as_of"], "selection": SELECTION,
        "table_rows": len(cases), "counts": counts,
        "accepted_source_reports": counts["newly_accepted"] + counts["carried_forward"],
        "cases": cases, "validation_started_at": started, "admitted_at": admitted,
        "code_sha256": codes, "rights": realized.RIGHTS, "boundaries": BOUNDARIES})


def admit(review, *, anchor: bytes, body: bytes, provenance: bytes):
    codes, started = _codes(), _now()
    checked = validate_review(review, anchor=anchor, body=body, provenance=provenance)
    admitted = _now()
    if not benchmark._clock(checked["review"]["reviewed_at"]) <= benchmark._clock(started) <= benchmark._clock(admitted):
        raise ValueError("cohort admission clock precedes review or regressed")
    artifact = _materialize(checked, started, admitted, codes)
    if _codes() != codes:
        raise ValueError("cohort producer changed during admission")
    return artifact


def verify(artifact, *, body: bytes, provenance: bytes):
    codes = _codes()
    value = benchmark._load(artifact, ARTIFACT_FORMAT)
    benchmark._keys(value, {"format", "review", "review_sha256", "anchor", "table_as_of", "selection", "table_rows",
        "counts", "accepted_source_reports", "cases", "validation_started_at", "admitted_at", "code_sha256",
        "rights", "boundaries", "sha256"}, "cohort artifact")
    checked = validate_review(value["review"], anchor=canonical_bytes(value["anchor"]), body=body, provenance=provenance)
    if not (benchmark._clock(checked["review"]["reviewed_at"]) <= benchmark._clock(value["validation_started_at"])
            <= benchmark._clock(value["admitted_at"]) <= benchmark._clock(_now())):
        raise ValueError("invalid cohort admission chronology")
    expected = _materialize(checked, value["validation_started_at"], value["admitted_at"], codes)
    if canonical_bytes(value) != canonical_bytes(expected) or _codes() != codes:
        raise ValueError("cohort rows, clocks, scope, rights or producer differ from exact source replay")
    return {"verified": True, "sha256": value["sha256"], "table_rows": value["table_rows"],
            "counts": value["counts"], "accepted_source_reports": value["accepted_source_reports"],
            "table_as_of": value["table_as_of"], "admitted_at": value["admitted_at"],
            "selection": value["selection"], "boundaries": value["boundaries"]}
