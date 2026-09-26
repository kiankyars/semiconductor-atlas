"""Explicit cohort coverage and freshness; no collection or claim-truth inference."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path, PurePosixPath

from .ai_critical import _facility_evidence_ids, load_baseline
from .ai_critical_changes import _strict_json
from .curated_capture import _instant, _keys, _sha, _text, load_plan
from .curated_review import ELIGIBLE, OPEN_STATUSES, verify_queue
from .source_checks import _read


CATALOG_FORMAT = "semiconductor-atlas-curated-coverage-catalog-v1"
CATALOG_FORMAT_V2 = "semiconductor-atlas-curated-coverage-catalog-v2"


def _bound_file(root: Path, binding: dict) -> tuple[Path, bytes]:
    _keys(binding, {"path", "sha256"})
    name = PurePosixPath(_text(binding["path"]))
    if name.is_absolute() or ".." in name.parts or str(name) != binding["path"]:
        raise ValueError("coverage bindings require canonical repository-relative paths")
    if not isinstance(binding["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", binding["sha256"]):
        raise ValueError("invalid coverage binding hash")
    path = root.joinpath(*name.parts)
    raw = _read(path)
    if _sha(raw) != binding["sha256"]:
        raise ValueError("coverage binding hash mismatch")
    return path, raw


def load_catalog(path: str | Path, *, repository_root: str | Path | None = None) -> dict:
    path = Path(path).absolute()
    root = Path(repository_root).absolute() if repository_root is not None else path.parent.parent
    raw = _read(path)
    catalog = _keys(_strict_json(raw, "coverage catalog"), {
        "format", "catalog_id", "recorded_at", "baseline", "freshness_seconds", "facilities", "notes",
    })
    if catalog["format"] not in {CATALOG_FORMAT, CATALOG_FORMAT_V2}:
        raise ValueError("unsupported coverage catalog")
    _text(catalog["catalog_id"])
    _text(catalog["notes"])
    recorded = _instant(catalog["recorded_at"])
    if type(catalog["freshness_seconds"]) is not int or not 1 <= catalog["freshness_seconds"] <= 31_536_000:
        raise ValueError("freshness_seconds must be a positive integer no greater than one year")
    baseline_path, baseline_raw = _bound_file(root, catalog["baseline"])
    baseline = load_baseline(baseline_path, root, verify_source_bytes=False)
    if baseline.input_bytes != baseline_raw or _instant(baseline.spec["recorded_at"]) > recorded:
        raise ValueError("coverage baseline changed or was not yet known")
    entries = catalog["facilities"]
    if not isinstance(entries, list) or len(entries) != len(baseline.facilities):
        raise ValueError("coverage catalog must include every baseline facility")
    targets = []
    for entry, facility in zip(entries, baseline.facilities):
        _keys(entry, {"facility_key", "plan", "unmonitored_reason"})
        if entry["facility_key"] != facility["facility_key"]:
            raise ValueError("coverage catalog must preserve the exact ordered cohort")
        plan = None
        if entry["plan"] is None:
            _text(entry["unmonitored_reason"])
        else:
            if entry["unmonitored_reason"] is not None:
                raise ValueError("configured target cannot also be marked unmonitored")
            plan_path, plan_raw = _bound_file(root, entry["plan"])
            plan, loaded_raw = load_plan(plan_path)
            if loaded_raw != plan_raw or _instant(plan["reviewed_at"]) > recorded:
                raise ValueError("coverage plan changed or was not yet reviewed")
            _bound_file(root, plan["review_record"])
            if plan["checked_facility_key"] != facility["facility_key"]:
                raise ValueError("coverage plan is bound to a different facility")
            for document in plan["documents"]:
                if (document["company"] != facility["company"]
                        or document["country_code"] != facility["geography"]["country_code"]):
                    raise ValueError("coverage plan company or geography contradicts the cohort")
        source_ids = {baseline.evidence[key]["source_id"] for key in _facility_evidence_ids(facility)}
        targets.append({"entry": entry, "facility": facility, "plan": plan,
                        "baseline_sources": [baseline.sources[key] for key in sorted(source_ids)]})
    return {"catalog": catalog, "catalog_sha256": _sha(raw), "targets": targets,
            "baseline_source_bytes_verified": False}


def _latest(observations: list[dict], field: str) -> list[dict]:
    timed = [item for item in observations if item[field] is not None]
    clock = max((_instant(item[field]) for item in timed), default=None)
    return [item for item in timed if _instant(item[field]) == clock]


def _document_report(document: dict, runs: list[dict], as_of: str, freshness_seconds: int,
                     *, include_scope: bool = False) -> dict:
    observations = []
    for run in runs:
        for row in run["documents"]:
            if row["id"] != document["id"]:
                continue
            if any(row[key] != document[key] for key in ("url", "company", "country_code", "source_family")):
                raise ValueError("captured document contradicts configured coverage target")
            response_at = row["response_finished_at"]
            observations.append({"run_id": run["run_id"], "imported_at": run["imported_at"],
                                 "response_finished_at": response_at, "assessment_at": run["finished_at"],
                                 "check_clock": response_at or run["finished_at"], "status": row["status"],
                                 "current_sha256": row["current_sha256"],
                                 "current_text_sha256": row["current_text_sha256"]})
    latest = _latest(observations, "check_clock")
    eligible = _latest([row for row in observations if row["status"] in ELIGIBLE], "response_finished_at")
    last_at = eligible[0]["response_finished_at"] if eligible else None
    age = (_instant(as_of) - _instant(last_at)).total_seconds() if last_at is not None else None
    due = (_instant(last_at) + timedelta(seconds=freshness_seconds)).isoformat().replace("+00:00", "Z") if last_at else None
    freshness = "never_observed" if age is None else ("stale" if age >= freshness_seconds else "fresh")
    conflict = len({row["current_text_sha256"] for row in eligible}) > 1
    health = "never_checked" if not latest else (
        "requires_attention" if any(row["status"] not in ELIGIBLE for row in latest)
        else "conflicting_observations" if conflict else "ok")
    return {"id": document["id"], "url": document["url"], "source_family": document["source_family"],
            **({"scope": document["scope"]} if include_scope else {}),
            "freshness": freshness, "check_health": health,
            "last_eligible_response_at": last_at, "eligible_age_seconds": age, "next_check_due_at": due,
            "latest_checks": latest, "last_eligible_observations": eligible,
            "attempt_count": sum(row["response_finished_at"] is not None for row in observations),
            "policy_blocked_count": sum(row["status"] == "not_attempted_policy_blocked" for row in observations),
            "attention_required": freshness != "fresh" or health != "ok"}


def coverage_report(
    catalog_path: str | Path, queue_path: str | Path, *, as_of: str,
    repository_root: str | Path | None = None,
) -> dict:
    bound = load_catalog(catalog_path, repository_root=repository_root)
    catalog = bound["catalog"]
    include_scope = catalog["format"] == CATALOG_FORMAT_V2
    cutoff = _instant(as_of)
    if cutoff < _instant(catalog["recorded_at"]):
        raise ValueError("coverage cutoff predates the catalog; use a catalog known at that time")
    queue = verify_queue(queue_path, as_of=as_of)
    facilities = []
    groups = []
    for target in bound["targets"]:
        entry, facility, plan = target["entry"], target["facility"], target["plan"]
        runs = [run for run in queue["runs"] if plan is not None
                and run["plan_sha256"] == entry["plan"]["sha256"]
                and run["facility_key"] == facility["facility_key"]]
        documents = [_document_report(doc, runs, as_of, catalog["freshness_seconds"], include_scope=include_scope)
                     for doc in plan["documents"]] if plan else []
        plan_state = "not_configured" if plan is None else (
            "review_expired" if cutoff >= _instant(plan["expires_at"]) else "within_review_window")
        candidates = [row for row in queue["candidates"] if row["facility_key"] == facility["facility_key"]]
        pending = sum(row["status"] in OPEN_STATUSES for row in candidates)
        recheck = sum(row["requires_reopen"] for row in candidates)
        source_families = sorted({source["source_family"] for source in target["baseline_sources"]}
                                 | {doc["source_family"] for doc in documents})
        baseline_sources = [{key: source[key] for key in ("source_id", "source_family", "url", "retrieved_at")}
                            for source in target["baseline_sources"]]
        item = {"facility_key": facility["facility_key"], "company": facility["company"],
                "country_code": facility["geography"]["country_code"], "facility_name": facility["name"],
                "plan_state": plan_state, "plan_id": plan["plan_id"] if plan else None,
                "plan_sha256": entry["plan"]["sha256"] if plan else None,
                "plan_expires_at": plan["expires_at"] if plan else None,
                "unmonitored_reason": entry["unmonitored_reason"],
                "baseline_source_metadata": baseline_sources, "documents": documents,
                "configured_document_count": len(documents), "pending_count": pending, "recheck_count": recheck,
                "attention_required": plan_state != "within_review_window" or bool(pending or recheck)
                or any(doc["attention_required"] for doc in documents)}
        facilities.append(item)
        for family in source_families:
            selected = [doc for doc in documents if doc["source_family"] == family]
            groups.append({"company": item["company"], "country_code": item["country_code"],
                           "facility_key": item["facility_key"], "source_family": family,
                           "baseline_document_count": sum(source["source_family"] == family for source in baseline_sources),
                           "configured_document_count": len(selected),
                           "fresh_document_count": sum(doc["freshness"] == "fresh" for doc in selected),
                           "stale_document_count": sum(doc["freshness"] == "stale" for doc in selected),
                           "never_observed_document_count": sum(doc["freshness"] == "never_observed" for doc in selected),
                           "unhealthy_document_count": sum(doc["check_health"] != "ok" for doc in selected),
                           "plan_state": plan_state if selected else "not_configured"})
    documents = [doc for facility in facilities for doc in facility["documents"]]
    return {"format": "semiconductor-atlas-curated-coverage-report-v2" if include_scope
            else "semiconductor-atlas-curated-coverage-report-v1", "as_of": as_of,
            "catalog_id": catalog["catalog_id"], "catalog_sha256": bound["catalog_sha256"],
            "catalog_recorded_at": catalog["recorded_at"], "baseline": catalog["baseline"],
            "baseline_source_bytes_verified": False,
            "queue_head_event_id": queue["head_event_id"], "queue_verified_capture_count": queue["verified_capture_count"],
            "freshness_seconds": catalog["freshness_seconds"],
            "summary": {"cohort_facility_count": len(facilities),
                        "configured_facility_count": sum(row["plan_id"] is not None for row in facilities),
                        "unmonitored_facility_count": sum(row["plan_id"] is None for row in facilities),
                        "expired_plan_count": sum(row["plan_state"] == "review_expired" for row in facilities),
                        "configured_document_count": len(documents),
                        "fresh_document_count": sum(row["freshness"] == "fresh" for row in documents),
                        "stale_document_count": sum(row["freshness"] == "stale" for row in documents),
                        "never_observed_document_count": sum(row["freshness"] == "never_observed" for row in documents),
                        "unhealthy_document_count": sum(row["check_health"] != "ok" for row in documents),
                        "pending_count": queue["pending_count"], "recheck_count": queue["recheck_count"]},
            "attention_required": queue["attention_required"] or any(row["attention_required"] for row in facilities),
            "coverage_complete": False, "claim_acceptance": False, "absence_inference_allowed": False,
            "delivery_eligible": False, "continuous_operation_proven": False,
            "coverage_note": "Exact configured URLs only, not company-wide or global coverage. Unimported packets and intermediate versions may be missing. Baseline source metadata is not a fresh check or claim validation. Freshness is a local review threshold, not publisher permission or a production observation.",
            "facilities": facilities, "groups": groups}
