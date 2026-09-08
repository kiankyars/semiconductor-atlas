"""Execute an interval-only source-guidance comparator on a complete reviewed screen.

This is a local prospective sidecar, not a fitted forecast, a new core admission,
or an assertion that retained guidance remains current. No source is acquired here.
"""

from __future__ import annotations

import base64
import calendar
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from . import milestone_benchmark as benchmark
from .source_target_detector import _Document
from .guidance_diagnostics import evaluate_case


SCREEN_FORMAT = "semiconductor-atlas-prospective-milestone-roster-screen-v1"
INPUT_FORMAT = "semiconductor-atlas-guidance-interval-inputs-v1"
VINTAGE_FORMAT = "semiconductor-atlas-guidance-interval-vintage-v1"
REVIEW_FORMAT = "semiconductor-atlas-guidance-interval-outcomes-v1"
REPORT_FORMAT = "semiconductor-atlas-guidance-interval-report-v1"
METHOD = "literal-calendar-interval-no-midpoint-v1"
BOUNDARIES = {
    "fitted_model": False, "calibration_established": False,
    "independent_review_verified": False, "global_coverage": False,
    "fresh_exhaustive_source_search": False, "canonical_claim_acceptance": False,
    "external_timestamp_attestation": False, "redistribution_authorized": False,
    "capacity_forecast": False, "geographic_holdout": False,
}
POLICY = {
    "population": "All screened cases retained; exclusions and abstentions never dropped.",
    "related_cases": "Site cases in one project group are not independent observations or holdouts.",
    "inputs": "Cutoff-bound accepted text or null-midpoint expected milestones; source guidance only.",
    "intervals": "Literal calendar years and half-years only; base and probability are null.",
    "outcomes": "Exact project scope and event; attained production, explicit nonoccurrence, cancellation or unknown.",
    "guidance_not_outcome": "New guidance, construction completion and opening are not attained production.",
    "missing": "Missing reports remain unknown, not nonoccurrence or evidence of a successful forecast.",
    "censoring": "Explicit nonoccurrence through or beyond the prediction high proves a window miss.",
    "timing": "Pre-vintage and straddling outcomes stay visible but do not become prospective timing scores.",
    "diagnostics": "Guaranteed containment, possible overlap, disjoint misses; no midpoint error or calibration.",
    "review_schedule": "Twenty full calendar quarters after the vintage quarter; review due 31 days after each cutoff.",
    "review_independence": "Reviewers disclose prior exposure; local review is not certified independent or blind.",
}


def _now():
    return benchmark._now()


def _json(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= benchmark.MAX_BYTES:
        raise ValueError("expected bounded JSON bytes")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _codes():
    root = Path(__file__).resolve().parents[1]
    files = sorted(path for path in (root / "semiconductor_atlas").rglob("*")
                   if path.suffix in {".py", ".sql"})
    files += [root / "scripts/milestone_benchmark.py", root / "scripts/guidance_interval_baseline.py"]
    return {str(path.relative_to(root)): benchmark._hash(path.read_bytes()) for path in files}


def _runtime():
    return {"python": sys.version, "sqlite": sqlite3.sqlite_version,
            "executable_sha256": benchmark._hash(Path(sys.executable).read_bytes()),
            "hermetic_environment": False}


def _open(database_bytes: bytes):
    if not isinstance(database_bytes, bytes) or not 0 < len(database_bytes) <= 1_000_000_000:
        raise ValueError("database must be a bounded exact SQLite image")
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(database_bytes)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
    except Exception:
        connection.close()
        raise


def _schedule(frozen_at: str):
    day = benchmark._clock(frozen_at).date()
    quarter = day.year * 4 + (day.month - 1) // 3
    result = []
    for offset in range(1, 21):
        year, index = divmod(quarter + offset, 4)
        month = 3 * (index + 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        result.append({"quarter": f"{year}-Q{index + 1}",
                       "evidence_cutoff_at": end.isoformat() + "T23:59:59Z",
                       "review_due_by": (end + timedelta(days=31)).isoformat()})
    return result


def _calendar(literal: str):
    match = re.fullmatch(r"(?:(first|second) half of )?(20\d{2})", literal)
    if not match:
        return None
    half, year = match.groups()
    low = f"{year}-07-01" if half == "second" else f"{year}-01-01"
    high = f"{year}-06-30" if half == "first" else f"{year}-12-31"
    return {"low": low, "base": None, "high": high}


def _literal(value, text: str, expected_event: str):
    if value["kind"] == "milestone":
        if (value["status"] != "expected" or value["milestone_type"] != expected_event
                or value["date_base"] is not None):
            raise ValueError("selected milestone must be the exact expected event with null midpoint")
        literal = value["date_literal"]
        interval = _calendar(literal) if isinstance(literal, str) else None
        precision = "half_year" if isinstance(literal, str) and "half of" in literal else "year"
        if (interval is None or literal not in text or value["date_precision"] != precision
                or interval != {"low": value["date_low"], "base": value["date_base"], "high": value["date_high"]}):
            raise ValueError("milestone literal, source text and stored interval disagree")
        return literal, interval
    if value.get("kind") != "scalar" or value.get("scalar_type") != "text":
        raise ValueError("guidance requires accepted scalar text or literal milestone")
    literal = " ".join(value["value"].split())
    if literal not in text:
        raise ValueError("accepted scalar text is absent from the exact visible source span")
    event = "mass_production" if literal.startswith("Mass production ") else "production_start" if literal.startswith("Production ") else None
    if event is None or event != expected_event:
        raise ValueError("source formulation does not support the exact event contract")
    match = re.fullmatch(r"(?:Production will begin in |Mass production expected to begin )((?:(?:first|second) half of )?20\d{2})", literal)
    return literal, _calendar(match[1]) if match else None


def _node(document, span):
    benchmark._keys(span, {"start", "end", "sha256"}, "reviewed source span")
    start, end = span["start"], span["end"]
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(document.raw)
            or benchmark._hash(document.raw[start:end]) != benchmark._digest(span["sha256"])):
        raise ValueError("reviewed source span differs from exact source bytes")
    nodes = [node for node in document.nodes if node.start == start and node.end == end and not node.hidden]
    if len(nodes) != 1 or not nodes[0].text():
        raise ValueError("evidence must be one complete visible element in the full HTML document")
    return nodes[0]


def _screen(connection, raw):
    screen = _json(raw)
    if screen.get("format") != SCREEN_FORMAT or screen.get("status") != "review_screen_not_registered_study_or_forecast":
        raise ValueError("unsupported complete screen")
    cases = screen.get("candidates")
    if not isinstance(cases, list) or not 1 <= len(cases) <= benchmark.MAX_CASES:
        raise ValueError("screen needs a bounded complete candidate array")
    ids = [row["case_id"] for row in cases]
    if len(set(ids)) != len(ids) or len({row["project_entity_id"] for row in cases}) != len(cases):
        raise ValueError("duplicate case or project in screen")
    actual = {row["claim_version_id"] for row in benchmark._rows(connection, "SELECT claim_version_id FROM milestone_values WHERE status='expected'")}
    reviewed = []
    for case in cases:
        for field in ("case_id", "project_entity_id", "project_stable_key", "scope", "project_group", "geography", "reason"):
            benchmark._text(case.get(field), "screen " + field)
        benchmark._project_entity(connection, case)
        if case["partition"] is not None or case["event_type"] not in {"production_start", "mass_production", "high_volume_production"}:
            raise ValueError("interval comparator has exact events and no fitted partitions")
        for item in case["stored_expected_claims"]:
            claim = benchmark._one(connection, "claim_versions", item["claim_id"])
            series = benchmark._one(connection, "claim_series", claim["series_id"])
            if series["subject_entity_id"] != case["project_entity_id"]:
                raise ValueError("screened expected claim belongs to another project")
            value = benchmark.repository._value_payload(benchmark.repository._stored_claim_value(connection, claim["id"], claim["value_kind"]))
            source = {**claim, **value, "claim_id": claim["id"], "predicate": series["predicate"]}
            if any(source.get(key) != val for key, val in item.items()):
                raise ValueError("screened expected claim differs from actual typed database record")
            reviewed.append(claim["id"])
    if len(reviewed) != len(set(reviewed)) or set(reviewed) != actual:
        raise ValueError("screen does not retain the complete expected-claim population")
    return screen


def _inputs(connection, database_bytes, screen_raw, review_raw, bodies, cutoff, started):
    screen, review = _screen(connection, screen_raw), _json(review_raw)
    benchmark._keys(review, {"format", "screen_sha256", "database_sha256", "reviewer", "reviewed_at", "prior_exposure", "corrections", "cases"}, "guidance input review")
    digest = benchmark._hash(database_bytes)
    if (review["format"] != INPUT_FORMAT or review["screen_sha256"] != benchmark._hash(screen_raw)
            or review["database_sha256"] != digest or screen["database"]["sha256"] != digest):
        raise ValueError("input review screen/database hash mismatch")
    for key in ("reviewer", "prior_exposure"):
        benchmark._text(review[key], key)
    if not isinstance(review["corrections"], list):
        raise ValueError("review corrections must be an array")
    for correction in review["corrections"]:
        benchmark._text(correction, "review correction")
    if not benchmark._clock(screen["reviewed_at"]) <= benchmark._clock(review["reviewed_at"]) <= benchmark._clock(started):
        raise ValueError("input review chronology is invalid")
    if not benchmark._clock(review["reviewed_at"]) <= benchmark._clock(cutoff) <= benchmark._clock(started):
        raise ValueError("input cutoff must include the review and precede execution")
    rows = review["cases"]
    if not isinstance(rows, list) or [row["case_id"] for row in rows] != sorted(row["case_id"] for row in screen["candidates"]):
        raise ValueError("input review must retain every case exactly once in sorted order")
    if not isinstance(bodies, dict) or sum(len(raw) for raw in bodies.values()) > benchmark.MAX_BYTES:
        raise ValueError("source bodies require a bounded exact hash-to-bytes mapping")
    expected_bodies = {row["document_sha256"] for row in rows}
    if set(bodies) != expected_bodies:
        raise ValueError("source bodies must exactly cover reviewed source hashes")
    documents = {}
    for digest, raw in bodies.items():
        if not isinstance(raw, bytes) or benchmark._hash(raw) != benchmark._digest(digest):
            raise ValueError("retained source body hash mismatch")
        documents[digest] = _Document(raw)
    cases = {row["case_id"]: row for row in screen["candidates"]}
    results = []
    for row in rows:
        benchmark._keys(row, {"case_id", "input_claims", "timeline_claim_id", "scope_claim_id", "selected_document_id", "document_sha256", "local_source_path", "timeline_span", "scope_span", "selection_reason"}, "guidance case inputs")
        benchmark._text(row["selection_reason"], "source selection reason")
        if not Path(row["local_source_path"]).is_absolute():
            raise ValueError("review source path must be explicit and absolute")
        case = cases[row["case_id"]]
        graph = benchmark._graph(connection, row["input_claims"], cutoff, case["project_entity_id"])
        screen_refs = sorted(({key: item[key] for key in ("claim_id", "value_sha256")}
                              for item in case["stored_expected_claims"]), key=lambda item: item["claim_id"])
        screen_graph = benchmark._graph(connection, screen_refs, cutoff, case["project_entity_id"])
        if any(item["kind"] == "project" and item["id"] != case["project_entity_id"] for item in graph["entities"]):
            raise ValueError("input lineage crosses the exact reviewed project")
        refs = {item["claim_id"] for item in row["input_claims"]}
        claims = {item["id"]: item for item in graph["claims"]}
        selected = next((item for item in graph["documents"] if item["id"] == row["selected_document_id"]), None)
        if selected is None or selected["content_sha256"] != row["document_sha256"]:
            raise ValueError("selected source body is not the exact document in this case lineage")
        for identifier in (row["timeline_claim_id"], row["scope_claim_id"]):
            if identifier is None:
                continue
            if identifier not in refs or claims[identifier]["claim_kind"] != "source_statement":
                raise ValueError("selected timeline/scope must be accepted source-statement roots")
            if not any(link["claim_version_id"] == identifier and link["source_document_id"] == selected["id"] and link["role"] == "support" for link in graph["evidence"]):
                raise ValueError("selected source does not directly support the selected root claim")
        document = documents[row["document_sha256"]]
        timeline, scope = _node(document, row["timeline_span"]), _node(document, row["scope_span"])
        if scope.tag != "table" or timeline.tag != "tr" or not scope.contains(timeline):
            raise ValueError("timeline element must be inside its reviewed source-scope element")
        if row["scope_claim_id"] is not None:
            scope_value = claims[row["scope_claim_id"]]["value"]
            if (scope_value.get("kind") != "scalar" or scope_value.get("scalar_type") != "text"
                    or " ".join(scope_value["value"].split()) not in scope.text()):
                raise ValueError("accepted scope scalar is absent from the reviewed visible scope")
        value = claims[row["timeline_claim_id"]]["value"]
        excluded = case["disposition"].startswith("screen_out_")
        if not (excluded or case["disposition"].startswith(("future_literal_interval", "abstain_"))):
            raise ValueError("unsupported reviewed screen disposition")
        if excluded:
            if value.get("kind") != "scalar" or " ".join(value["value"].split()) not in timeline.text():
                raise ValueError("excluded aggregate input must still bind to its own visible source")
            literal, interval = value["value"], None
        else:
            literal, interval = _literal(value, timeline.text(), case["event_type"])
        if value["kind"] == "milestone":
            # A reviewed literal must retain its scoped production formulation, not a bare date.
            if case["event_type"] == "production_start" and not re.search(r"\b(?:start|begin) production\b", timeline.text(), re.I):
                raise ValueError("milestone evidence lacks the exact production-start formulation")
            if row["scope_claim_id"] is None:
                if not {item["claim_id"] for item in case["stored_expected_claims"]} <= refs:
                    raise ValueError("reviewed source-version alternatives must remain in the input graph")
                prefix = "reviewed-project-source:" + selected["document_url"] + ":"
                if not case["project_stable_key"].startswith(prefix):
                    raise ValueError("milestone subject lacks reviewed publisher/phase correspondence")
                subject = case["project_stable_key"][len(prefix):]
                clause = re.escape(subject) + r": Expected to begin production in " + re.escape(value["date_literal"])
                if not re.search(r"(?<!\w)" + clause + r"(?=$|[ .]| Fab \d+:)", timeline.text()):
                    raise ValueError("exact named project clause does not support the selected milestone")
                alternatives = [item for item in graph["documents"] if any(
                    link["source_document_id"] == item["id"] and link["claim_version_id"] in refs and link["role"] == "support"
                    for link in graph["evidence"])]
                if any(benchmark._clock(item["retrieved_at"]) > benchmark._clock(selected["retrieved_at"]) for item in alternatives):
                    raise ValueError("selected document is not the latest retained reviewed statement")
        if interval is not None and case["reviewed_source_period"] is not None:
            if interval != {key: case["reviewed_source_period"][key] for key in ("low", "base", "high")}:
                raise ValueError("executed literal interval differs from the reviewed source period")
        held = case["disposition"].startswith("abstain_")
        results.append({"case": case, "input": row, "lineage": graph, "screen_lineage": screen_graph,
                        "selected_document": selected, "source_literal": literal,
                        "guidance_interval": interval, "scope_excluded": excluded,
                        "scope_abstention": held})
    return screen, review, results


def _assemble(screen_raw, review_raw, results, cutoff, started, frozen, codes, runtime):
    if not benchmark._clock(cutoff) <= benchmark._clock(started) <= benchmark._clock(frozen):
        raise ValueError("execution/freeze clock regressed")
    schedule = _schedule(frozen)
    horizon = schedule[-1]["evidence_cutoff_at"][:10]
    day = benchmark._clock(frozen).date()
    rows = []
    for result in results:
        row = dict(result)
        interval = row["guidance_interval"]
        if row["scope_excluded"]:
            disposition, reason = "excluded", row["case"]["reason"]
        elif row["scope_abstention"]:
            disposition, reason = "abstained", row["case"]["reason"]
        elif interval is None:
            disposition, reason = "abstained", "No supported literal calendar-year or half-year interval."
        elif not day < benchmark._day(interval["low"]) <= benchmark._day(interval["high"]) <= benchmark._day(horizon):
            disposition, reason = "abstained", "Guidance interval is not wholly future and inside the frozen horizon."
        else:
            disposition, reason = "issued", None
        row.update(disposition=disposition, reason=reason,
                   prediction=interval if disposition == "issued" else None,
                   source_age_days=(day - benchmark._clock(row["selected_document"]["retrieved_at"]).date()).days)
        rows.append(row)
    counts = Counter(row["disposition"] for row in rows)
    groups = {}
    for row in rows:
        case = row["case"]
        group = groups.setdefault(case["project_group"], {"case_ids": [], "geographies": set()})
        group["case_ids"].append(case["case_id"])
        group["geographies"].add(case["geography"])
    return benchmark._seal({"format": VINTAGE_FORMAT, "method": METHOD,
        "started_at": started, "frozen_at": frozen, "evidence_cutoff_at": cutoff,
        "horizon_end": horizon, "review_schedule": schedule,
        "screen_base64": base64.b64encode(screen_raw).decode(), "screen_sha256": benchmark._hash(screen_raw),
        "review_base64": base64.b64encode(review_raw).decode(), "review_sha256": benchmark._hash(review_raw),
        "database_sha256": _json(review_raw)["database_sha256"], "cases": rows,
        "groups": [{"project_group": key, "case_ids": val["case_ids"], "geographies": sorted(val["geographies"])} for key, val in sorted(groups.items())],
        "denominators": {"screened": len(rows), "retained": len(rows) - counts["excluded"],
                         "issued": counts["issued"], "abstained": counts["abstained"], "excluded": counts["excluded"]},
        "policy": POLICY, "policy_sha256": benchmark._hash(POLICY),
        "code_sha256": codes, "runtime": runtime, "boundaries": BOUNDARIES,
        "training_release": None, "probability_calibration": None,
        "rights": {"source_bodies_embedded": False, "local_review_only": True, "redistribution_authorized": False}})


def freeze(database_bytes: bytes, screen_raw: bytes, review_raw: bytes, bodies: dict[str, bytes], *, evidence_cutoff_at: str):
    """Run the pinned literal comparator and only then seal its actual current vintage."""
    codes, runtime, started = _codes(), _runtime(), _now()
    connection = _open(database_bytes)
    try:
        with benchmark._snapshot(connection):
            _, _, rows = _inputs(connection, database_bytes, screen_raw, review_raw, bodies, evidence_cutoff_at, started)
        frozen = _now()
        if _codes() != codes or _runtime() != runtime:
            raise ValueError("execution code/runtime changed during freeze")
        return _assemble(screen_raw, review_raw, rows, evidence_cutoff_at, started, frozen, codes, runtime)
    finally:
        connection.close()


def verify(database_bytes: bytes, vintage_raw: bytes, bodies: dict[str, bytes], *, trusted_sha256: str):
    """Re-execute from exact inputs and compare all bytes to an externally retained hash."""
    if benchmark._hash(vintage_raw) != benchmark._digest(trusted_sha256):
        raise ValueError("vintage differs from the independently retained artifact hash")
    vintage = benchmark._load(vintage_raw, VINTAGE_FORMAT)
    if vintage["code_sha256"] != _codes() or vintage["runtime"] != _runtime():
        raise ValueError("use the exact frozen code and recorded local runtime to replay")
    if benchmark._clock(vintage["frozen_at"]) > benchmark._clock(_now()):
        raise ValueError("vintage freeze is in the future")
    screen_raw = base64.b64decode(vintage["screen_base64"], validate=True)
    review_raw = base64.b64decode(vintage["review_base64"], validate=True)
    connection = _open(database_bytes)
    try:
        with benchmark._snapshot(connection):
            _, _, rows = _inputs(connection, database_bytes, screen_raw, review_raw, bodies,
                                 vintage["evidence_cutoff_at"], vintage["started_at"])
        rebuilt = _assemble(screen_raw, review_raw, rows, vintage["evidence_cutoff_at"],
                            vintage["started_at"], vintage["frozen_at"], _codes(), _runtime())
        if benchmark.canonical_bytes(rebuilt) != vintage_raw:
            raise ValueError("executed interval baseline differs from frozen bytes")
        return rebuilt
    finally:
        connection.close()


def _review_rows(vintage, review, started, bodies):
    benchmark._keys(review, {"reviewed_by", "prior_exposure", "evidence_cutoff_at", "outcomes"}, "outcome review")
    benchmark._text(review["reviewed_by"], "reviewer")
    benchmark._text(review["prior_exposure"], "prior exposure")
    cutoff = review["evidence_cutoff_at"]
    if cutoff not in {row["evidence_cutoff_at"] for row in vintage["review_schedule"]}:
        raise ValueError("outcome evidence cutoff must be a prespecified quarterly checkpoint")
    if not benchmark._clock(vintage["frozen_at"]) < benchmark._clock(cutoff) <= benchmark._clock(started):
        raise ValueError("outcome checkpoint has not occurred or precedes the vintage")
    outcomes = review["outcomes"]
    if not isinstance(outcomes, list) or len(outcomes) != len(vintage["cases"]):
        raise ValueError("outcome review must retain all screened cases")
    adapted_rows, adapted_cases = [], []
    kinds = {"observed": "attainment", "right_censored": "explicit_nonoccurrence",
             "cancelled": "project_cancellation", "unknown": "unresolved"}
    for row, frozen in zip(outcomes, vintage["cases"]):
        benchmark._keys(row, {"case_id", "project_entity_id", "scope", "event_type", "status", "event_interval", "censor_at", "cancellation_interval", "reason", "evidence", "evidence_kind", "evidence_interpretation"}, "reviewed outcome")
        case = frozen["case"]
        if any(row[field] != case[field] for field in ("case_id", "project_entity_id", "scope", "event_type")):
            raise ValueError("outcome must match the exact frozen project scope and event")
        if row["evidence_kind"] != kinds.get(row["status"]):
            raise ValueError("outcome requires explicit attainment/nonoccurrence/cancellation interpretation")
        benchmark._text(row["evidence_interpretation"], "reviewed evidence interpretation")
        if frozen["disposition"] == "excluded" and (row["status"] != "unknown" or row["evidence"]):
            raise ValueError("unresolved aggregate exclusion cannot acquire an allocated outcome in this study")
        # Reuse evidence-byte and availability checks; 'phase' is only the older helper's field name.
        adapted = {key: value for key, value in row.items() if key not in {"scope", "evidence_kind", "evidence_interpretation"}}
        adapted["phase"] = row["scope"]
        adapted_rows.append(adapted)
        adapted_cases.append({"case": {**case, "phase": case["scope"]}})
    benchmark._outcome_rows({"cases": adapted_cases}, adapted_rows, cutoff, bodies)
    return outcomes


def review_outcomes(database_bytes, vintage_raw, input_bodies, *, trusted_vintage_sha256, review, bodies):
    """Accept a separate complete evidence review at a genuinely later actual clock."""
    vintage = verify(database_bytes, vintage_raw, input_bodies, trusted_sha256=trusted_vintage_sha256)
    started = _now()
    review = _json(benchmark.canonical_bytes(review))
    _review_rows(vintage, review, started, bodies)
    admitted = _now()
    if benchmark._clock(admitted) < benchmark._clock(started) or _codes() != vintage["code_sha256"]:
        raise ValueError("outcome admission clock/code changed during validation")
    return benchmark._seal({"format": REVIEW_FORMAT, "vintage_file_sha256": trusted_vintage_sha256,
        "vintage_sha256": vintage["sha256"], "review": review, "started_at": started,
        "admitted_at": admitted,
        "review_due_by": next(row["review_due_by"] for row in vintage["review_schedule"] if row["evidence_cutoff_at"] == review["evidence_cutoff_at"]),
        "source_bodies": [{"sha256": key, "bytes": len(raw)} for key, raw in sorted(bodies.items())],
        "code_sha256": vintage["code_sha256"], "policy_sha256": vintage["policy_sha256"],
        "boundaries": BOUNDARIES, "rights": benchmark.OUTCOME_RIGHTS})


def score(database_bytes, vintage_raw, input_bodies, outcome_raw, *, trusted_vintage_sha256, trusted_sha256, bodies):
    """Re-execute the vintage, verify later evidence, retain every case and classification."""
    vintage = verify(database_bytes, vintage_raw, input_bodies, trusted_sha256=trusted_vintage_sha256)
    if benchmark._hash(outcome_raw) != benchmark._digest(trusted_sha256):
        raise ValueError("outcomes differ from the independently retained artifact hash")
    review = benchmark._load(outcome_raw, REVIEW_FORMAT)
    benchmark._keys(review, {"format", "vintage_file_sha256", "vintage_sha256", "review", "started_at", "admitted_at", "review_due_by", "source_bodies", "code_sha256", "policy_sha256", "boundaries", "rights", "sha256"}, "frozen outcome review")
    if (review["vintage_file_sha256"] != trusted_vintage_sha256 or review["vintage_sha256"] != vintage["sha256"]
            or review["code_sha256"] != _codes() or review["policy_sha256"] != vintage["policy_sha256"]
            or review["boundaries"] != BOUNDARIES or review["rights"] != benchmark.OUTCOME_RIGHTS):
        raise ValueError("outcome review vintage/code/policy binding mismatch")
    evaluated = _now()
    if not benchmark._clock(vintage["frozen_at"]) < benchmark._clock(review["started_at"]) <= benchmark._clock(review["admitted_at"]) <= benchmark._clock(evaluated):
        raise ValueError("outcome review chronology is invalid")
    outcomes = _review_rows(vintage, review["review"], review["started_at"], bodies)
    due = next(row["review_due_by"] for row in vintage["review_schedule"] if row["evidence_cutoff_at"] == review["review"]["evidence_cutoff_at"])
    if review["review_due_by"] != due or review["source_bodies"] != [{"sha256": key, "bytes": len(raw)} for key, raw in sorted(bodies.items())]:
        raise ValueError("outcome review schedule or evidence inventory differs")
    rows = []
    for frozen, outcome in zip(vintage["cases"], outcomes):
        status = outcome["status"]
        diagnostic_input = {"status": status}
        if status in {"observed", "cancelled"}:
            interval = outcome["event_interval"] if status == "observed" else outcome["cancellation_interval"]
            diagnostic_input.update(low=interval["low"], high=interval["high"])
        elif status == "right_censored":
            diagnostic_input["through"] = outcome["censor_at"]
        diagnostic = evaluate_case(frozen["prediction"], diagnostic_input,
                                   vintage["frozen_at"][:10], vintage["horizon_end"])
        if frozen["disposition"] == "excluded":
            diagnostic["classification"] = "screened_out"
        rows.append({"case_id": frozen["case"]["case_id"], "project_group": frozen["case"]["project_group"],
                     "geography": frozen["case"]["geography"], "event_type": frozen["case"]["event_type"],
                     "disposition": frozen["disposition"], **diagnostic})
    issued = [row for row in rows if row["prediction_issued"]]
    retained = [row for row in rows if row["disposition"] != "excluded"]
    if _codes() != vintage["code_sha256"]:
        raise ValueError("code changed during scoring")
    return benchmark._seal({"format": REPORT_FORMAT, "vintage_file_sha256": trusted_vintage_sha256,
        "outcomes_file_sha256": trusted_sha256, "evaluated_at": evaluated,
        "outcome_evidence_cutoff_at": review["review"]["evidence_cutoff_at"],
        "review_late": benchmark._clock(review["admitted_at"]).date() > benchmark._day(due),
        "denominators": vintage["denominators"], "cases": rows, "groups": vintage["groups"],
        "classification_counts": dict(sorted(Counter(row["classification"] for row in rows).items())),
        "retained_outcome_status_counts": dict(sorted(Counter(row["outcome_status"] for row in retained).items())),
        "issued_outcome_status_counts": dict(sorted(Counter(row["outcome_status"] for row in issued).items())),
        "issued_proven_window_misses": sum(row["window_miss"] is True for row in issued),
        "issued_guaranteed_containments": sum(row["guaranteed_containment"] is True for row in issued),
        "issued_window_result_unresolved": sum(row["window_miss"] is None for row in issued),
        "probability_calibration": None, "point_error": None,
        "code_sha256": vintage["code_sha256"], "policy_sha256": vintage["policy_sha256"], "boundaries": BOUNDARIES})
