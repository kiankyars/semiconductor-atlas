"""Selected historical source comparisons, separate from actual collector predecessors."""

from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
from urllib.parse import urlsplit

from . import curated_observation_population as population, snapshot
from . import source_statement_review as statements, discovery_handoff as binding
from .adapters import epa_frs
from .ai_critical_changes import _open_real_directory_fd, _pretty_bytes, _strict_json
from .source_checks import _read


STUDY_FORMAT = "semiconductor-atlas-source-vintage-study-v1"
FORMAT = "semiconductor-atlas-source-vintage-cohort-v1"
REPORT_FORMAT = "semiconductor-atlas-source-vintage-review-v1"
RULE_VERSION = "all-shared-nist-award-urls-latest-eligible-response-v1"
DESIGN = "selected_retrospective_cross_vintage_review_not_independent_or_blind"
SUCCESS_STATUSES = {"first_observation_requires_review", "unchanged", "raw_bytes_only", "visible_text_changed_requires_review"}
BOUNDARIES = {**statements.BOUNDARIES, "collector_predecessors_rewritten": False,
    "detector_performance_established": False, "publication_time_verified": False}
_now, _instant, _hash = population._now, population._instant, population._hash


def _code_hashes() -> dict:
    root = Path(__file__).parent.parent
    paths = (Path(__file__), Path(snapshot.__file__), Path(epa_frs.__file__), root / "scripts/review_source_vintages.py")
    return {**statements._code_hashes(), **{path.relative_to(root).as_posix(): _hash(_read(path)) for path in paths}}


def _study(raw: bytes) -> dict:
    study = binding._keys(_strict_json(raw, "source vintage study"),
        {"format", "study_id", "before_manifest", "after_population", "selection_rule", "selection_rationale"}, "source vintage study")
    if study["format"] != STUDY_FORMAT or study["selection_rule"] != RULE_VERSION:
        raise ValueError("unsupported cross-vintage study or selection rule")
    for field in ("study_id", "selection_rationale"):
        statements._text(study[field])
    return study


def _nist_award(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.netloc == "www.nist.gov"
        and parsed.path.startswith("/chips/") and len(parsed.path.split("/")) == 3
        and bool(parsed.path.split("/")[-1]) and not parsed.query and not parsed.fragment)


def _eligible(row: dict) -> bool:
    return (row["status"] in SUCCESS_STATUSES and row["transport_outcome"] == "succeeded"
        and bool(row["body_path"] and row["body_sha256"] and row["text_sha256"] and row["response_finished_at"]))


def _select(study: dict, manifest: dict, before: dict, after: dict, root: Path) -> dict:
    completed = [row for row in after["observations"] if row["window_membership"] == "in_window"]
    incomplete = [row for row in statements._cases(after).values() if row["kind"] == "incomplete_document"]
    observed_urls = {row["url"] for row in completed + incomplete}
    shared = sorted(set(before) & observed_urls)
    cases = []
    for url in shared:
        checks = sorted((row for row in completed if row["url"] == url), key=lambda row: row["observation_id"])
        pending = sorted((row for row in incomplete if row["url"] == url), key=lambda row: row["case_id"])
        successes = [row for row in checks if _eligible(row)]
        selected, ties, status = None, [], "no_eligible_successful_after"
        if successes:
            latest = max(_instant(row["response_finished_at"]) for row in successes)
            ties = sorted((row for row in successes if _instant(row["response_finished_at"]) == latest),
                          key=lambda row: row["observation_id"])
            if len({(row["body_sha256"], row["text_sha256"]) for row in ties}) > 1:
                status = "ambiguous_latest_successful_bodies"
            else:
                selected = ties[0]
                status = "paired" if _instant(manifest["retrieved_at"]) < latest else "nonincreasing_knowledge_clocks"
        old = before[url]
        identifier = _hash({"rule_version": RULE_VERSION, "study_id": study["study_id"], "url": url,
            "before_manifest_sha256": study["before_manifest"]["sha256"], "before_body_sha256": old["body_sha256"],
            "after_population_sha256": study["after_population"]["sha256"],
            "latest_observation_ids": [row["observation_id"] for row in ties]})
        change = None
        if status == "paired":
            prior_raw = _read(population._path(root, old["body_path"]))
            current_raw = _read(population._path(root, selected["body_path"]))
            if (len(prior_raw) != old["bytes"] or _hash(prior_raw) != old["body_sha256"]
                    or {"bytes": len(current_raw), "sha256": _hash(current_raw)} != after["snapshot"]["files"][selected["body_path"]]
                    or _hash(current_raw) != selected["body_sha256"]):
                raise ValueError("comparison body changed after source verification")
            normalized = statements.capture.normalized_bytes
            change = ("unchanged_raw_bytes" if prior_raw == current_raw else "raw_bytes_only"
                if normalized(prior_raw, "html_visible_text_v1") == normalized(current_raw, "html_visible_text_v1")
                else "visible_text_changed")
        cases.append({"case_id": identifier, "case_kind": "retrospective_cross_vintage_document_pair",
            "url": url, "status": status, "comparison_eligible": status == "paired",
            "before": old, "after": selected, "latest_tied_observation_ids": [row["observation_id"] for row in ties],
            "after_checks": checks, "incomplete_after_cases": pending, "document_change": change,
            "comparison_basis": "selected_manifest_anchor_not_the_after_observations_actual_collector_predecessor"})
    excluded_before = [{"url": url, "reason": "no_exact_url_in_after_window", "before": before[url]}
                       for url in sorted(set(before) - observed_urls)]
    excluded_checks = [row for row in completed if row["url"] not in before]
    excluded_incomplete = [row for row in incomplete if row["url"] not in before]
    shared_checks = [row for case in cases for row in case["after_checks"]]
    selected_ids = {case["after"]["observation_id"] for case in cases if case["after"]}
    return {"cases": sorted(cases, key=lambda row: row["case_id"]), "excluded_before": excluded_before,
        "excluded_after_checks": excluded_checks, "excluded_after_incomplete": excluded_incomplete,
        "counts": {"historical_award_urls": len(before), "shared_urls": len(shared),
            "paired_urls": sum(case["comparison_eligible"] for case in cases),
            "uncomparable_urls": sum(not case["comparison_eligible"] for case in cases),
            "after_document_checks": len(completed), "shared_after_checks": len(shared_checks),
            "selected_after_observations": len(selected_ids),
            "unselected_eligible_after_observations": sum(_eligible(row) and row["observation_id"] not in selected_ids for row in shared_checks),
            "ineligible_shared_after_checks": sum(not _eligible(row) for row in shared_checks),
            "excluded_after_checks": len(excluded_checks), "excluded_historical_award_urls": len(excluded_before),
            "after_incomplete_document_cases": len(incomplete),
            "shared_incomplete_document_cases": len(incomplete) - len(excluded_incomplete),
            "excluded_incomplete_document_cases": len(excluded_incomplete),
            "document_change_categories": dict(sorted(Counter(case["document_change"] for case in cases if case["comparison_eligible"]).items()))}}


def _collect(study: dict, root: Path) -> dict:
    old_path, old_raw = binding._binding(root, study["before_manifest"])
    after_path, after_raw = binding._binding(root, study["after_population"])
    if old_path.name != "manifest.json":
        raise ValueError("historical anchor must be the source snapshot manifest.json")
    manifest = _strict_json(old_raw, "historical source manifest")
    statements._text(manifest.get("retrieval_timestamp_basis"))
    _instant(manifest["retrieved_at"])
    verified = snapshot.verify_snapshot(old_path.parent)
    if verified.manifest_sha256 != _hash(old_raw):
        raise ValueError("historical manifest changed during source verification")
    source_verification = population.verify_population_sources(after_path, reference_root=root)
    if source_verification["frozen_sha256"] != _hash(after_raw):
        raise ValueError("after population changed during source verification")
    after = _strict_json(after_raw, "frozen after population")
    after_study = population._study(population.blobs._unblob(after["study"]))
    files = dict(after["snapshot"]["files"])
    files[old_path.relative_to(root).as_posix()] = {"bytes": len(old_raw), "sha256": _hash(old_raw)}
    before, excluded_inputs = {}, []
    for item in verified.inputs:
        relative = item.path.relative_to(root).as_posix()
        population._path(root, relative)
        files[relative] = {"bytes": item.size, "sha256": item.sha256}
        if item.record_type != "award_detail_page" or not _nist_award(item.url):
            excluded_inputs.append({"url": item.url, "body_path": relative,
                "record_type": item.record_type, "reason": "not_nist_award_detail_input"})
            continue
        if item.url in before:
            raise ValueError("duplicate historical award URL")
        if item.metadata.get("content_type") != "text/html":
            raise ValueError("historical award detail must be HTML")
        before[item.url] = {"url": item.url, "body_path": relative, "body_sha256": item.sha256,
            "bytes": item.size, "record_type": item.record_type,
            "manifest_sha256": verified.manifest_sha256, "knowledge_clock": manifest["retrieved_at"],
            "knowledge_clock_basis": manifest["retrieval_timestamp_basis"]}
    projection = _select(study, manifest, before, after, root)
    protected = {str(Path(name).parent) for name in files}
    protected.update(after["snapshot"]["root_entries"])
    protected.update(row["path"] for row in after["snapshot"]["captures"])
    return {"files": files, **projection, "excluded_historical_inputs": excluded_inputs,
        "historical_input_count": len(verified.inputs),
        "historical_source_scopes": verified.source_scopes,
        "historical_retrieved_at": manifest["retrieved_at"],
        "historical_retrieval_timestamp_basis": manifest["retrieval_timestamp_basis"],
        "after_population_frozen_at": after["frozen_at"],
        "after_window": {key: after_study[key] for key in ("start", "end")},
        "source_verification": source_verification, "protected_directories": sorted(protected)}


def freeze(study_path: str | Path, *, reference_root: str | Path) -> dict:
    root, path = Path(reference_root).resolve(), Path(study_path).absolute()
    raw = binding._bounded_read(path)
    study = _study(raw)
    started, codes = _now(), _code_hashes()
    collected = _collect(study, root)
    if any(_instant(collected[key]) > _instant(started) for key in ("after_population_frozen_at", "historical_retrieved_at")):
        raise ValueError("cross-vintage freeze cannot precede input knowledge clocks")
    if collected != _collect(study, root) or _read(path) != raw or codes != _code_hashes():
        raise ValueError("cross-vintage inputs changed across freeze")
    finished = _now()
    if _instant(finished) < _instant(started):
        raise ValueError("invalid cross-vintage freeze clocks")
    result = {"format": FORMAT, "rule_version": RULE_VERSION, "design": DESIGN,
        "study": population.blobs._blob(raw), "started_at": started, "frozen_at": finished,
        "snapshot": collected, "snapshot_sha256": _hash(collected), "code_sha256": codes,
        "boundaries": BOUNDARIES.copy()}
    if len(_pretty_bytes(result)) > population.MAX_BYTES:
        raise ValueError("complete cross-vintage cohort exceeds 20 MB; never truncate")
    return result


def validate(frozen_path: str | Path, *, reference_root: str | Path) -> tuple[dict, bytes]:
    path, root = Path(frozen_path).absolute(), Path(reference_root).resolve()
    raw = binding._bounded_read(path)
    frozen = binding._keys(_strict_json(raw, "cross-vintage cohort"), {"format", "rule_version", "design",
        "study", "started_at", "frozen_at", "snapshot", "snapshot_sha256", "code_sha256", "boundaries"}, "cross-vintage cohort")
    if (frozen["format"] != FORMAT or frozen["rule_version"] != RULE_VERSION or frozen["design"] != DESIGN
            or frozen["boundaries"] != BOUNDARIES or any(value is not False for value in frozen["boundaries"].values())
            or frozen["code_sha256"] != _code_hashes() or frozen["snapshot_sha256"] != _hash(frozen["snapshot"])
            or raw != _pretty_bytes(frozen)):
        raise ValueError("frozen cross-vintage contract, bytes or code differs")
    if not _instant(frozen["started_at"]) <= _instant(frozen["frozen_at"]) <= _instant(_now()):
        raise ValueError("invalid cross-vintage freeze clocks")
    study = _study(population.blobs._unblob(frozen["study"]))
    current = _collect(study, root)
    if any(_instant(current[key]) > _instant(frozen["started_at"]) for key in ("after_population_frozen_at", "historical_retrieved_at")):
        raise ValueError("cross-vintage freeze precedes retained input knowledge")
    if current != frozen["snapshot"] or current != _collect(study, root):
        raise ValueError("selected source population does not replay or changed during replay")
    if _read(path) != raw or frozen["code_sha256"] != _code_hashes():
        raise ValueError("cross-vintage frozen bytes or code changed during replay")
    return frozen, raw


def _body(case: dict, side: str, root: Path, files: dict, inputs: dict) -> bytes:
    source = case[side]
    if source is None or not source["body_path"] or not source["body_sha256"]:
        raise ValueError("selected comparison side has no source body")
    path = population._path(root, source["body_path"])
    raw = _read(path)
    if (files.get(source["body_path"]) != {"bytes": len(raw), "sha256": _hash(raw)}
            or _hash(raw) != source["body_sha256"]):
        raise ValueError("selected comparison body differs from frozen binding")
    if path in inputs and inputs[path] != raw:
        raise ValueError("selected comparison body changed across review")
    inputs[path] = raw
    return raw


def _adjudicate(labels: dict, frozen: dict, reviews: dict, root: Path, inputs: dict) -> tuple[dict, list]:
    cases = {row["case_id"]: row for row in frozen["snapshot"]["cases"]}
    judgments, targets = {}, []
    for row in labels["adjudications"]:
        binding._keys(row, {"case_id", "disposition", "reason", "review_sha256", "locator", "reviewed_regions", "targets"}, "cross-vintage adjudication")
        key = statements._text(row["case_id"])
        if key not in cases or key in judgments:
            raise ValueError("unknown or duplicate cross-vintage case")
        if binding._digest(row["review_sha256"]) not in reviews:
            raise ValueError("cross-vintage adjudication lacks a bound supporting review")
        for field in ("reason", "locator"):
            statements._text(row[field])
        disposition = statements._text(row["disposition"])
        if disposition not in statements.DISPOSITIONS:
            raise ValueError("invalid cross-vintage disposition")
        case = cases[key]
        if not case["comparison_eligible"] and disposition != "uncomparable":
            raise ValueError("unpaired selected URL must remain uncomparable")
        regions, bodies = {"before": [], "after": []}, {}
        for region in statements._array(row["reviewed_regions"], "reviewed regions", maximum=100):
            binding._keys(region, {"side", "span"}, "reviewed region")
            side = statements._text(region["side"])
            if side not in regions:
                raise ValueError("region must identify before or after")
            bodies[side] = _body(case, side, root, frozen["snapshot"]["files"], inputs)
            if not statements._span(region["span"], bodies[side]):
                raise ValueError("reviewed region has no full-body normalized source text")
            regions[side].append(region["span"])
        values = statements._array(row["targets"], "target pairs", maximum=1000)
        if (disposition == "reviewed_targets") != bool(values):
            raise ValueError("only reviewed_targets has nonempty targets")
        if disposition in {"reviewed_targets", "no_relevant_scoped_target"} and not all(regions.values()):
            raise ValueError("target review needs both selected source regions")
        groups = set()
        for target in values:
            binding._keys(target, {"source_native_subject", "milestone", "formulation", "scope", "verdict", "reason", "before", "after"}, "cross-vintage target")
            group = {"url": case["url"]}
            for field in ("source_native_subject", "milestone", "formulation", "scope"):
                value = statements._text(target[field])
                if value != " ".join(value.split()):
                    raise ValueError("target group fields require canonical whitespace")
                group[field] = value
            group_id = _hash(group)
            if group_id in groups:
                raise ValueError("duplicate target formulation")
            groups.add(group_id)
            statements._text(target["reason"])
            if statements._text(target["verdict"]) not in statements.VERDICTS:
                raise ValueError("invalid target verdict")
            evidence = {side: statements._side(target[side], bodies[side]) for side in ("before", "after")}
            for side in ("before", "after"):
                for span in [target[side]["fragment"], *target[side]["context"]]:
                    if not any(region["start"] <= span["start"] < span["end"] <= region["end"] for region in regions[side]):
                        raise ValueError("target evidence is outside its declared reviewed regions")
            if target["verdict"] == "revision" and (case["document_change"] != "visible_text_changed" or evidence["before"] == evidence["after"]):
                raise ValueError("identical selected source text cannot establish revision")
            targets.append({"case_id": key, "group_id": group_id, "evidence_pair_id": _hash(evidence), **group, **target,
                "body_bindings": {side: {field: case[side][field] for field in ("body_path", "body_sha256")} for side in ("before", "after")}})
        judgments[key] = row
    return judgments, sorted(targets, key=lambda row: (row["case_id"], row["group_id"]))


def review(frozen_path: str | Path, labels_path: str | Path, *, reference_root: str | Path) -> dict:
    root, label_path = Path(reference_root).resolve(), Path(labels_path).absolute()
    codes = _code_hashes()
    frozen, frozen_raw = validate(frozen_path, reference_root=root)
    raw = binding._bounded_read(label_path)
    labels, reviews, inputs = statements._labels(raw, frozen, frozen_raw, label_path)
    judgments, targets = _adjudicate(labels, frozen, reviews, root, inputs)
    cases = [{**case, "adjudication": judgments.get(case["case_id"]),
        "disposition": judgments[case["case_id"]]["disposition"] if case["case_id"] in judgments else "unlabelled"}
        for case in frozen["snapshot"]["cases"]]
    revised = [target for target in targets if target["verdict"] == "revision"]
    result = {"format": REPORT_FORMAT, "rule_version": RULE_VERSION, "design": DESIGN,
        "frozen_sha256": _hash(frozen_raw), "labels_sha256": _hash(raw), "frozen_at": frozen["frozen_at"],
        **{field: labels[field] for field in ("reviewer", "reviewed_at", "prior_exposure", "coverage_statement", "target_scope", "supporting_reviews")},
        "selection_counts": frozen["snapshot"]["counts"],
        "counts": {"selected_url_cases": len(cases), "labelled_cases": len(judgments), "unlabelled_cases": len(cases) - len(judgments),
            "dispositions": dict(sorted(Counter(case["disposition"] for case in cases).items())),
            "target_pairs": len(targets), "target_verdicts": dict(sorted(Counter(target["verdict"] for target in targets).items())),
            "url_cases_with_reviewed_revision": len({target["case_id"] for target in revised}),
            "reviewer_described_subject_scopes": len({(target["url"], target["source_native_subject"], target["scope"]) for target in targets}),
            "reviewer_described_revised_subject_scopes": len({(target["url"], target["source_native_subject"], target["scope"]) for target in revised}),
            "unique_target_formulation_groups": len({target["group_id"] for target in targets}),
            "unique_normalized_evidence_pairs": len({target["evidence_pair_id"] for target in targets})},
        "cases": cases, "targets": targets,
        "exclusions": {key: frozen["snapshot"][key] for key in ("excluded_before", "excluded_after_checks", "excluded_after_incomplete", "excluded_historical_inputs")},
        "metrics": dict.fromkeys(("alert_precision", "detection_recall", "false_positive_burden", "publication_detection_lag", "forecast_calibration")),
        "boundaries": BOUNDARIES.copy(), "code_sha256": codes,
        "limitations": ["Selection is the complete exact-URL intersection of two declared retained inputs, not publisher completeness.",
            "Before anchors are selected historical versions; actual after observations and collector predecessors remain unchanged.",
            "Historical batch, response, assessment, import, freeze and review clocks have different meanings; publication and change-effective times remain unknown.",
            "Text-change categories are mechanical document differences, not target predictions or manufacturing alerts.",
            "Reviewer labels are exposed retrospective source interpretations, not independent truth or blind detector evaluation.",
            "Multiple narrative/table formulations for one described subject do not establish independent events.",
            "Literal deadlines are not exact dates or forecast intervals; no canonical facility identity or production attainment follows.",
            "Targets missing on one side remain unresolved; this version cannot represent an added or removed target as a paired formulation."]}
    if len(_pretty_bytes(result)) > population.MAX_BYTES:
        raise ValueError("complete cross-vintage review exceeds 20 MB; never truncate")
    if validate(frozen_path, reference_root=root)[1] != frozen_raw:
        raise ValueError("frozen cohort changed across review")
    if any(_read(path) != content for path, content in [(label_path, raw), *inputs.items()]) or codes != _code_hashes():
        raise ValueError("cross-vintage labels, supporting evidence or code changed across review")
    return result


def write_new(path: Path, data: dict, *, reference_root: Path, protected_directories: list[str]) -> dict:
    if ".." in path.parts:
        raise ValueError("artifact output cannot contain parent traversal")
    output, root = path.absolute(), reference_root.resolve()
    protected = [population._path(root, relative) for relative in protected_directories]
    if any(output == directory or output.is_relative_to(directory) for directory in protected):
        raise ValueError("artifact output cannot be inside retained source or polling directories")
    raw = _pretty_bytes(data)
    if len(raw) > population.MAX_BYTES:
        raise ValueError("artifact exceeds 20 MB")
    _, directory = _open_real_directory_fd(output.parent, "cross-vintage output parent", create=False)
    try:
        descriptor = os.open(output.name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644, dir_fd=directory)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(directory)
    return {"output": str(output), "bytes": len(raw), "sha256": _hash(raw)}
