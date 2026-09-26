"""Replay a full retained census through a separate, exposed assertion detector."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import curated_observation_population as population
from . import source_assertion_detector as detector
from . import source_statement_review as statements
from . import source_target_detector as html
from . import source_vintage_review as vintage
from .ai_critical_changes import _pretty_bytes
from .source_checks import _read


FORMAT = "semiconductor-atlas-source-assertion-inventory-v1"
RULE_VERSION = "all-retained-opportunities-scoped-source-assertions-v1"
DESIGN = "exposed_retrospective_engineering_inventory_not_performance"
SUCCESS_STATUSES = {"first_observation_requires_review", "unchanged", "raw_bytes_only", "visible_text_changed_requires_review"}
BOUNDARIES = {**population.BOUNDARIES, "canonical_identity_verified": False,
    "attained_hvm_verified": False, "event_date_inferred": False,
    "raw_redistribution": False, "scheduled_detector_changed": False,
    "registered_study_changed": False, "independent_outcome_adjudication": False}


def _code_hashes() -> dict:
    root = Path(__file__).resolve().parents[1]
    paths = (Path(__file__), Path(detector.__file__), Path(html.__file__), Path(vintage.__file__),
             root / "scripts/inventory_source_assertions.py")
    return {**statements._code_hashes(), **{p.relative_to(root).as_posix(): population._hash(_read(p)) for p in paths}}


def _summary(cases: list[dict]) -> dict:
    return {
        "document_opportunities": len(cases),
        "supported_route_opportunities": sum(row["parser_route_supported"] for row in cases),
        "collector_comparable_opportunities": sum(row["comparison_eligible"] for row in cases),
        "supported_route_comparable_opportunities": sum(row["parser_route_supported"] and row["comparison_eligible"] for row in cases),
        "after_extractions": sum(row["analysis"] is not None and
            row["analysis"]["extractions"]["after"]["status"] == "extracted" for row in cases),
        "result_counts": dict(sorted(Counter(row["result"] for row in cases).items())),
        "collector_status_counts": dict(sorted(Counter(row["source_status"] for row in cases).items())),
        "distinct_urls": len({row["url"] for row in cases}),
    }


def _recheck(inputs: dict[Path, bytes], code: dict) -> None:
    if any(_read(path) != raw for path, raw in inputs.items()) or _code_hashes() != code:
        raise ValueError("source assertion inventory inputs or code changed during analysis")


def _recheck_queue_prefix(frozen: dict, root: Path) -> None:
    study = population._study(population.blobs._unblob(frozen["study"]))
    expected = frozen["snapshot"]["queue_history"]["events"]
    current = population.queue.export_events(population._path(root, study["queue_path"]))["events"]
    if current[:len(expected)] != expected:
        raise ValueError("original source queue history changed or disappeared")


def inventory(frozen_path: str | Path, *, reference_root: str | Path) -> dict:
    """No labels, requests, queue changes, canonical claims or forecast outputs."""
    root, path = Path(reference_root).resolve(), Path(frozen_path).absolute()
    code = _code_hashes()
    raw = _read(path)
    verification = population.verify_population_sources(path, reference_root=root)
    if verification["frozen_sha256"] != population._hash(raw):
        raise ValueError("frozen census changed across verification")
    frozen = population._json(path)
    if _pretty_bytes(frozen) != raw:
        raise ValueError("frozen census bytes differ from verified input")
    inputs, cases = {path: raw}, []
    for case in statements._cases(frozen).values():
        supported = case["url"] in detector.SUPPORTED_URLS
        after_eligible = (case["kind"] == "document_check" and case["status"] in SUCCESS_STATUSES
            and case.get("transport_outcome") == "succeeded"
            and bool(case.get("body_path") and case.get("body_sha256")))
        row = {"case_id": case["case_id"], "kind": case["kind"], "url": case["url"],
            "document_id": case["document_id"], "source_scope": case["scope"],
            "assessment_at": case.get("assessment_at"), "intent_started_at": case.get("intent_started_at"),
            "source_status": case["status"], "comparison_eligible": case["comparison_eligible"],
            "parser_route_supported": supported, "after_extraction_eligible": after_eligible,
            "before": None, "after": None, "result": "unsupported_exact_url", "analysis": None}
        if supported:
            if not after_eligible:
                row["result"] = "uncomparable_source_unavailable"
            else:
                after, row["after"] = statements._body(case, "after", root, frozen, inputs)
                before = None
                if case["comparison_eligible"]:
                    before, row["before"] = statements._body(case, "before", root, frozen, inputs)
                row["analysis"] = detector.analyze(case["url"], before, after)
                row["result"] = row["analysis"]["result"]
                if not case["comparison_eligible"] and row["result"] not in {"extraction_only", "abstain"}:
                    raise ValueError("an uncomparable source cannot become a paired assertion verdict")
        cases.append(row)
    cases.sort(key=lambda row: row["case_id"])
    # Keep every actual check. Repeated source pairs are not independent events.
    pairs = Counter((row["url"], row["source_scope"], row["before"]["body_sha256"], row["after"]["body_sha256"])
        for row in cases if row["before"] is not None and row["after"] is not None)
    pair_diagnostics = [{"pair_key": population._hash(list(key)), "url": key[0], "source_scope": key[1],
        "before_sha256": key[2], "after_sha256": key[3], "actual_checks": count}
        for key, count in sorted(pairs.items())]
    # Recheck the whole retained input population, including unsupported/failed rows.
    for relative, expected in frozen["snapshot"]["files"].items():
        source = population._path(root, relative)
        body = _read(source)
        if expected != {"bytes": len(body), "sha256": population._hash(body)}:
            raise ValueError("retained population source changed during assertion inventory")
        inputs[source] = body
    _recheck_queue_prefix(frozen, root)
    _recheck(inputs, code)
    result = {"format": FORMAT, "rule_version": RULE_VERSION, "design": DESIGN,
        "frozen_sha256": verification["frozen_sha256"], "population_scope": frozen["scope"],
        "population_frozen_at": frozen["frozen_at"], "code_sha256": code,
        "supported_exact_urls": list(detector.SUPPORTED_URLS),
        "counts": _summary(cases), "cases": cases,
        "per_url": [{"url": url, **_summary([row for row in cases if row["url"] == url])}
                    for url in sorted({row["url"] for row in cases})],
        "exact_pair_repetition": pair_diagnostics,
        "interpretation": "Source wording extraction and comparison only; no_candidate is not unchanged physical state or complete scoped assertion coverage. First observations are extraction-only. Literal revision candidates require review and need not be substantive manufacturing changes.",
        "unscored_metrics": dict.fromkeys(("precision", "recall", "false_positive_burden", "publication_detection_lag", "physical_accuracy", "forecast_calibration")),
        "network_requests": 0, "boundaries": BOUNDARIES.copy()}
    if len(_pretty_bytes(result)) > population.MAX_BYTES:
        raise ValueError("assertion inventory exceeds 20 MB; no truncation is allowed")
    return result


def write_inventory(frozen_path: str | Path, output: str | Path, *, reference_root: str | Path) -> dict:
    root, path = Path(reference_root).resolve(), Path(frozen_path).absolute()
    result = inventory(path, reference_root=root)
    raw = _read(path)
    if population._hash(raw) != result["frozen_sha256"] or _code_hashes() != result["code_sha256"]:
        raise ValueError("inventory input or code changed before output protection")
    frozen = population._json(path)
    if _pretty_bytes(frozen) != raw:
        raise ValueError("frozen census changed before output protection")
    protected = {str(Path(name).parent) for name in frozen["snapshot"]["files"]}
    protected.update(frozen["snapshot"]["root_entries"])
    protected.update(row["path"] for row in frozen["snapshot"]["captures"])
    for relative, expected in frozen["snapshot"]["files"].items():
        body = _read(population._path(root, relative))
        if expected != {"bytes": len(body), "sha256": population._hash(body)}:
            raise ValueError("retained source changed before inventory output")
    _recheck_queue_prefix(frozen, root)
    _recheck({path: raw}, result["code_sha256"])
    return {**vintage.write_new(Path(output), result, reference_root=root,
        protected_directories=sorted(protected)), "counts": result["counts"]}
