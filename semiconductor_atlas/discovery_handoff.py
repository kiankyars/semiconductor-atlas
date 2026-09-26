"""Offline proof of a reviewed discovery-to-source route, never claim acceptance."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from . import curated_capture, curated_review, discovery_review, nist_discovery, source_checks
from . import ai_critical, ai_critical_changes
from .adapters import nist_discovery as discovery_parser
from .ai_critical_changes import _open_real_directory_fd, _pretty_bytes, _strict_json, _read_regular_file_at
from .curated_capture import _instant, _now, _text
from .curated_review import _hash
from .source_checks import _read


REVIEW_FORMAT = "semiconductor-atlas-discovery-handoff-review-v1"
ARTIFACT_FORMAT = "semiconductor-atlas-discovery-handoff-v1"
RULE_VERSION = "discovery-to-reviewed-source-v1"
BOUNDARIES = {
    "claim_acceptance": False, "delivery_eligible": False,
    "linked_document_acquisition_allowed": False, "raw_redistribution": False,
    "baseline_modified": False, "absence_inference_allowed": False,
    "publisher_complete": False, "facility_scope_verified_by_discovery": False,
}


def _keys(value: object, fields: set[str], context: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"invalid {context} fields")
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid SHA-256 or event identifier")
    return value


def _bounded_read(path: Path) -> bytes:
    raw = _read(path)
    if len(raw) > 20_000_000:
        raise ValueError("handoff input exceeds 20 MB")
    return raw


def _binding(root: Path, value: object) -> tuple[Path, bytes]:
    row = _keys(value, {"path", "sha256"}, "file binding")
    relative = Path(_text(row["path"]))
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != row["path"]:
        raise ValueError("handoff references must be canonical repository-relative paths")
    path = root / relative
    raw = _bounded_read(path)
    if _hash(raw) != _digest(row["sha256"]):
        raise ValueError("handoff reference hash mismatch")
    return path, raw


def _ref(binding: dict) -> str:
    return f"{binding['path']}#sha256={binding['sha256']}"


def _code_paths() -> dict:
    modules = (curated_capture, curated_review, discovery_review, nist_discovery,
               discovery_parser, source_checks, ai_critical, ai_critical_changes)
    return {"discovery_handoff": Path(__file__),
            **{module.__name__: Path(module.__file__) for module in modules}}


def _code_hashes() -> dict:
    return {name: _hash(_read(path)) for name, path in _code_paths().items()}


def _file_state(path: Path, expected: bytes | None = None) -> dict:
    def identity():
        value = path.lstat()
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                value.st_ctime_ns, value.st_mode)

    before = identity()
    raw = _read(path)
    if before != identity() or expected is not None and raw != expected:
        raise ValueError("handoff input changed while taking a dependency snapshot")
    return {"identity": before, "bytes": len(raw), "sha256": _hash(raw)}


def _collect_input_snapshot(path: Path, root: Path) -> dict:
    """Inventory the actual local dependency graph, including bytes and inode clocks."""
    files = {}
    raw = _bounded_read(path)
    review = _strict_json(raw, "handoff review")
    files[str(path)] = _file_state(path, raw)
    captures = set()
    for binding in (review["discovery"]["events"], review["source"]["events"],
                    review["access_review"], review["source_review"]):
        source, body = _binding(root, binding)
        files[str(source)] = _file_state(source, body)
    for name in ("discovery", "source"):
        _, body = _binding(root, review[name]["events"])
        events = _strict_json(body, "handoff history")["events"]
        curated_review._validate_events(events)
        for event in events:
            payload = event["payload"]
            if payload["kind"] in {"capture_imported", "discovery_capture_imported"}:
                captures.add(payload["source_path"])
    inventory = {}
    for name in sorted(captures):
        packet = Path(name)
        inventory[name] = curated_capture._inventory(packet)
        files[str(packet / "manifest.json")] = _file_state(packet / "manifest.json")
        for relative, expected in inventory[name].items():
            source = packet / relative
            state = _file_state(source)
            if any(state[key] != expected[key] for key in ("bytes", "sha256")):
                raise ValueError("capture changed while taking a dependency snapshot")
            files[str(source)] = state
    codes = {}
    for name, source in _code_paths().items():
        state = _file_state(source)
        files[str(source)] = state
        codes[name] = state["sha256"]
    return {"files": files, "captures": inventory, "code_sha256": codes}


def _input_snapshot(path: Path, root: Path) -> dict:
    try:
        return _collect_input_snapshot(path, root)
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError("malformed handoff dependency graph") from error


def _check_snapshot(snapshot: dict, path: Path, root: Path) -> None:
    if _input_snapshot(path, root) != snapshot:
        raise ValueError("handoff dependencies changed across the acceptance boundary")


def _history(root: Path, binding: dict, module, now: str) -> tuple[list[dict], dict]:
    _, raw = _binding(root, binding)
    data = _keys(_strict_json(raw, "queue export"), {"format", "events"}, "queue export")
    expected = (discovery_review.EXPORT_FORMAT if module is discovery_review
                else "semiconductor-atlas-curated-review-events-v1")
    if data["format"] != expected:
        raise ValueError("wrong handoff queue export format")
    events = data["events"]
    curated_review._validate_events(events)
    if any(_instant(event["recorded_at"]) > _instant(now) for event in events):
        raise ValueError("queue export contains future admissions")
    report = module._fold(events)
    for run in report["runs"]:
        expected_payload = {key: value for key, value in run.items()
                            if key not in {"imported_at", "event_id"}}
        if module._capture_payload(run["source_path"]) != expected_payload:
            raise ValueError("retained packet differs from queue admission")
    return events, report


def _one(rows: list[dict], field: str, value: str, context: str) -> dict:
    matches = [row for row in rows if row[field] == value]
    if len(matches) != 1:
        raise ValueError(f"missing or ambiguous {context}")
    return matches[0]


def _candidate(report: dict, row: dict, status: str) -> dict:
    candidate = _one(report["candidates"], "id", row["candidate_id"], "review candidate")
    if (candidate["last_event_id"] != row["expected_event_id"]
            or candidate["requires_reopen"] or candidate["status"] != status):
        raise ValueError("stale expected event or unresolved candidate")
    return candidate


def _current_source(report: dict, review: dict) -> None:
    latest = _one([row for row in report["sources"] if row["facility_key"] == review["facility_key"]],
                  "source_url", review["source_url"], "latest source checks")
    if latest["attention_required"] or any(row["current_text_sha256"] != review["source"]["text_sha256"] for row in latest["latest_checks"]):
        raise ValueError("source review is superseded by a changed or failed check")


def _access_approval(access: dict, plan: dict, document: dict) -> None:
    if access.get("reviewed_at") != plan["reviewed_at"]:
        raise ValueError("access review clock differs from approved plan")
    access_document, boundary = access.get("document"), access.get("acquisition_boundary")
    if (access.get("decision") != "approve_one_exact_discovered_document_for_local_source_text_review"
            or access.get("facility_key") != plan["checked_facility_key"]
            or not isinstance(access_document, dict)
            or any(access_document.get(field) != document[field] for field in ("id", "url", "scope"))
            or not isinstance(boundary, dict)
            or any(boundary.get(field) is not False for field in (
                "claim_acceptance", "delivery_eligible", "raw_redistribution", "linked_resource_acquisition"))):
        raise ValueError("access review does not independently approve this exact bounded document")
    _text(access.get("reviewer"))


def _load(review_path: Path, root: Path) -> tuple[dict, bytes, list[dict], list[dict], dict]:
    raw = _bounded_read(review_path)
    review = _keys(_strict_json(raw, "handoff review"), {
        "format", "reviewed_at", "reviewer", "facility_key", "source_url",
        "document_scope", "scope_limitation", "outcome", "rationale",
        "discovery", "source", "access_review", "source_review", "boundaries",
    }, "handoff review")
    if review["format"] != REVIEW_FORMAT or review["boundaries"] != BOUNDARIES:
        raise ValueError("handoff cannot grant permissions, modify baselines or accept claims")
    if any(value is not False for value in review["boundaries"].values()):
        raise ValueError("handoff boundary values must be boolean false")
    for field in ("reviewer", "facility_key", "source_url", "document_scope", "scope_limitation", "rationale"):
        _text(review[field])
    outcomes = {"no_baseline_revision": "dismissed", "requires_claim_review": "handed_off"}
    if not isinstance(review["outcome"], str) or review["outcome"] not in outcomes:
        raise ValueError("handoff outcome must remain outside claim acceptance")
    now = _now()
    if _instant(review["reviewed_at"]) > _instant(now):
        raise ValueError("handoff review is in the future")
    discovery = _keys(review["discovery"], {"events", "candidate_id", "expected_event_id", "run_id"}, "discovery link")
    source = _keys(review["source"], {"events", "candidate_id", "expected_event_id", "run_id", "document_id", "plan_sha256", "body_sha256", "text_sha256"}, "source link")
    for row in (discovery, source):
        for field in ("candidate_id", "expected_event_id", "run_id"):
            _digest(row[field])
    for field in ("plan_sha256", "body_sha256", "text_sha256"):
        _digest(source[field])
    _text(source["document_id"])
    discovery_events, _ = _history(root, discovery["events"], discovery_review, now)
    source_events, _ = _history(root, source["events"], curated_review, now)
    at = review["reviewed_at"]
    discovered_report = discovery_review._fold(discovery_events, as_of=at)
    discovered = _candidate(discovered_report, discovery, "handed_off")
    acquired_report = curated_review._fold(source_events, as_of=at)
    acquired = _candidate(acquired_report, source, outcomes[review["outcome"]])
    if discovered["source_url"] != review["source_url"] or acquired["source_url"] != review["source_url"]:
        raise ValueError("discovered URL must equal the acquired exact document URL")
    if acquired["facility_key"] != review["facility_key"] or acquired["current_text_sha256"] != source["text_sha256"]:
        raise ValueError("acquired facility or reviewed text binding differs")
    observations = [row for row in discovered["observations"] if row["run_id"] == discovery["run_id"]]
    if not observations:
        raise ValueError("discovered URL was not observed in the bound packet")
    discovery_run = _one(discovered_report["runs"], "run_id", discovery["run_id"], "discovery capture")
    baseline = ai_critical.load_baseline(Path(discovery_run["source_path"]) / "baseline.json",
                                         Path(discovery_run["source_path"]), verify_source_bytes=False)
    facility = _one(baseline.facilities, "facility_key", review["facility_key"], "baseline facility")
    source_run = _one(acquired_report["runs"], "run_id", source["run_id"], "source capture")
    observed = _one(acquired["observations"], "run_id", source["run_id"], "source observation")
    if (observed["status"] not in curated_review.ELIGIBLE
            or observed["document_id"] != source["document_id"]
            or observed["current_sha256"] != source["body_sha256"]
            or observed["current_text_sha256"] != source["text_sha256"]
            or observed["plan_sha256"] != source["plan_sha256"]):
        raise ValueError("failed, blocked or mismatched acquired source observation")
    _current_source(acquired_report, review)
    plan, plan_raw = curated_capture.load_plan(Path(source_run["source_path"]) / "plan.json")
    document = _one(plan["documents"], "id", source["document_id"], "approved plan document")
    if (_hash(plan_raw) != source["plan_sha256"] or document["url"] != review["source_url"]
            or plan["checked_facility_key"] != review["facility_key"]
            or document["company"] != facility["company"]
            or document["country_code"] != facility["geography"]["country_code"]
            or document["scope"] != review["document_scope"]
            or plan["review_record"] != review["access_review"]):
        raise ValueError("acquisition plan or document scope binding differs")
    _, access_raw = _binding(root, review["access_review"])
    if access_raw != _read(Path(source_run["source_path"]) / "review.json"):
        raise ValueError("access review differs from the acquired packet")
    access = _strict_json(access_raw, "access review")
    _, source_review_raw = _binding(root, review["source_review"])
    text_review = _strict_json(source_review_raw, "source text review")
    if not isinstance(access, dict) or not isinstance(text_review, dict):
        raise ValueError("supporting reviews must be JSON objects")
    _access_approval(access, plan, document)
    _text(text_review.get("reviewer"))
    discovery_decision = discovered["decisions"][-1]
    source_decision = acquired["decisions"][-1]
    approval = access.get("discovery")
    at_approval = discovery_review._fold(discovery_events, as_of=access["reviewed_at"])
    approved_candidate = _one(at_approval["candidates"], "id", discovered["id"], "discovery at approval time")
    if (not isinstance(approval, dict) or approval.get("candidate_id") != discovered["id"]
            or approval.get("expected_event_id") != approved_candidate["last_event_id"]
            or approval.get("expected_event_id") != discovery_decision["expected_event_id"]
            or approval.get("latest_capture_manifest_sha256") != discovery["run_id"]
            or approval.get("review_fingerprint") != approved_candidate["review_fingerprint"]
            or approved_candidate["status"] not in discovery_review.OPEN_STATUSES
            or approved_candidate["requires_reopen"]):
        raise ValueError("access approval does not bind the discovery state known at its review time")
    latest_seen = max((_instant(row["observed_at"]) for row in approved_candidate["observations"]))
    if not any(row["run_id"] == discovery["run_id"] and _instant(row["observed_at"]) == latest_seen
               for row in approved_candidate["observations"]):
        raise ValueError("access approval does not select its latest admitted discovery observation")
    if (discovery_decision["evidence_ref"] != _ref(review["access_review"])
            or source_decision["evidence_ref"] != _ref(review["source_review"])):
        raise ValueError("queue decision does not bind the retained supporting review")
    reviewed_capture = text_review.get("source_capture")
    disposition = {"no_baseline_revision": "dismiss_reviewed_text_version_without_baseline_revision",
                   "requires_claim_review": "handoff_reviewed_text_version_for_separate_claim_review"}
    if (text_review.get("decision") != disposition[review["outcome"]]
            or any(text_review.get(field) is not False for field in ("claim_acceptance", "delivery_eligible", "baseline_modified"))
            or text_review.get("facility_key") != review["facility_key"]
            or text_review.get("candidate_id") != acquired["id"]
            or text_review.get("expected_event_id") != source_decision["expected_event_id"]
            or not isinstance(reviewed_capture, dict)
            or reviewed_capture.get("manifest_sha256") != source["run_id"]
            or reviewed_capture.get("body_sha256") != source["body_sha256"]
            or reviewed_capture.get("normalized_sha256") != source["text_sha256"]
            or reviewed_capture.get("normalization") != "html_visible_text_v1"
            or reviewed_capture.get("retrieved_at") != observed["captured_at"]):
        raise ValueError("supporting source review does not bind this admitted text version")
    at_text_review = curated_review._fold(source_events, as_of=text_review["reviewed_at"])
    reviewed_candidate = _one(at_text_review["candidates"], "id", acquired["id"], "source at review time")
    if (reviewed_candidate["last_event_id"] != text_review["expected_event_id"]
            or reviewed_candidate["status"] not in curated_review.OPEN_STATUSES
            or reviewed_candidate["requires_reopen"]):
        raise ValueError("source predecision token was not applicable at its supporting review time")
    _current_source(at_text_review, review)
    clocks = [max((row["imported_at"] for row in observations), key=_instant),
              plan["reviewed_at"], discovery_decision["recorded_at"], source_run["started_at"],
              source_run["finished_at"], observed["imported_at"], text_review.get("reviewed_at"),
              source_decision["recorded_at"], review["reviewed_at"], now]
    if any(_instant(first) > _instant(second) for first, second in zip(clocks, clocks[1:])):
        raise ValueError("handoff clocks violate discovery, approval, acquisition and review order")
    if (_instant(source_decision["recorded_at"]) < _instant(observed["imported_at"])
            or _instant(discovery_decision["recorded_at"]) < _instant(observations[0]["imported_at"])):
        raise ValueError("review precedes its admitted observation")
    facts = {
        "source_url": review["source_url"], "facility_key": review["facility_key"],
        "document_scope": review["document_scope"], "scope_limitation": review["scope_limitation"],
        "discovery_candidate_id": discovered["id"], "discovery_run_id": discovery["run_id"],
        "discovery_observed_at": min((row["observed_at"] for row in observations), key=_instant),
        "discovery_admitted_at": observations[0]["imported_at"],
        "discovery_handoff_at": discovery_decision["recorded_at"],
        "access_reviewed_at": plan["reviewed_at"], "source_run_id": source["run_id"],
        "access_reviewer": access["reviewer"], "source_text_reviewer": text_review["reviewer"],
        "source_candidate_id": acquired["id"], "source_captured_at": observed["captured_at"],
        "source_admitted_at": observed["imported_at"], "source_reviewed_at": text_review["reviewed_at"],
        "source_decision_at": source_decision["recorded_at"], "source_status": acquired["status"],
        "body_sha256": source["body_sha256"], "text_sha256": source["text_sha256"],
        "outcome": review["outcome"], "rationale": review["rationale"],
    }
    for binding in (discovery["events"], source["events"], review["access_review"], review["source_review"]):
        _binding(root, binding)
    return review, raw, discovery_events, source_events, facts


def build_handoff(review_path: str | Path, *, reference_root: str | Path | None = None) -> dict:
    """Validate retained inputs and derive deterministic output, with no writes or network."""
    path = Path(review_path).absolute()
    root = Path(reference_root).absolute() if reference_root is not None else path.parent.parent
    try:
        snapshot = _input_snapshot(path, root)
        code_hashes = _code_hashes()
        if code_hashes != snapshot["code_sha256"]:
            raise ValueError("handoff validator code changed before derivation")
        review, raw, discovery_events, source_events, facts = _load(path, root)
        captures = sorted({event["payload"]["source_path"] for events in (discovery_events, source_events)
                           for event in events if event["payload"]["kind"] in {"capture_imported", "discovery_capture_imported"}})
        result = {
            "format": ARTIFACT_FORMAT, "rule_version": RULE_VERSION,
            "review": {"path": str(path), "sha256": _hash(raw)}, "reference_root": str(root),
            "reviewed_at": review["reviewed_at"], "reviewer": review["reviewer"],
            "linked": True, "facts": facts, "boundaries": dict(BOUNDARIES),
            "replay_dependencies": {"self_contained": False, "capture_paths": captures,
                "code_sha256": code_hashes,
                "note": "Requires this review and its hash-bound event exports/supporting reviews under reference_root, every original capture packet at its admitted path, and the exact validator code. No raw bodies are packaged here. Hashes do not authenticate a reviewer or externally attest clocks. Dependencies are byte- and identity-checked around derivation and publication for a quiescent local corpus, not atomically locked against arbitrary concurrent writers. This is a bounded reviewer assertion, not publisher permission or manufacturing claim acceptance."},
        }
        if _read(path) != raw:
            raise ValueError("handoff review changed during verification")
        _check_snapshot(snapshot, path, root)
        return result
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError("malformed handoff review or retained queue history") from error


def write_handoff(review_path: str | Path, output: str | Path, *, reference_root: str | Path | None = None) -> dict:
    review_path = Path(review_path).absolute()
    root = Path(reference_root).absolute() if reference_root is not None else review_path.parent.parent
    snapshot = _input_snapshot(review_path, root)
    result = build_handoff(review_path, reference_root=reference_root)
    path = Path(output).absolute()
    if any(path.resolve().is_relative_to(Path(root).resolve()) for root in result["replay_dependencies"]["capture_paths"]):
        raise ValueError("handoff output must be outside immutable captures")
    _, descriptor = _open_real_directory_fd(path.parent, "handoff output parent", create=False)
    try:
        try:
            os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(f"handoff destination already exists: {path}")
        parent_stat = os.fstat(descriptor)
        encoded = _pretty_bytes(result)
        with tempfile.TemporaryDirectory(prefix=".handoff-", dir=path.parent) as temporary:
            staging = Path(temporary).resolve() / "candidate.json"
            curated_capture._write(staging, encoded)
            identity = staging.stat()
            _check_snapshot(snapshot, review_path, root)
            if _read(staging) != encoded:
                raise ValueError("staged handoff bytes changed before publication")
            published = False
            try:
                ai_critical.install_file_exclusive(staging, path,
                    expected_destination_parent=(parent_stat.st_dev, parent_stat.st_ino))
                published = True
                _check_snapshot(snapshot, review_path, root)
                if _read_regular_file_at(descriptor, path.name, "published handoff") != encoded:
                    raise ValueError("published handoff bytes changed before acceptance")
            except BaseException:
                if published:
                    # Remove only this unchanged newly published inode, never a replacement.
                    current = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                    if ((current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)
                            and _read_regular_file_at(descriptor, path.name, "failed handoff") == encoded):
                        os.unlink(path.name, dir_fd=descriptor)
                raise
    finally:
        os.close(descriptor)
    return result


def validate_handoff(path: str | Path) -> dict:
    path = Path(path)
    raw = _bounded_read(path)
    original = _file_state(path, raw)
    artifact = _strict_json(raw, "handoff artifact")
    if not isinstance(artifact, dict) or artifact.get("format") != ARTIFACT_FORMAT:
        raise ValueError("invalid handoff artifact")
    try:
        expected = build_handoff(artifact["review"]["path"], reference_root=artifact["reference_root"])
        if raw != _pretty_bytes(expected):
            raise ValueError("handoff artifact does not replay exactly")
        if _file_state(path) != original:
            raise ValueError("handoff artifact changed during verification")
        return expected
    except (KeyError, TypeError) as error:
        raise ValueError("malformed handoff artifact") from error


def replay_handoff(path: str | Path, *, as_of: str) -> dict:
    """Retrospective target-selected admission projection; later decisions stay hidden."""
    artifact = validate_handoff(path)
    if _instant(as_of) > _instant(artifact["reviewed_at"]):
        raise ValueError("handoff projection ends at its reviewed cutoff, not live queue state")
    review_path, root = Path(artifact["review"]["path"]), Path(artifact["reference_root"])
    snapshot = _input_snapshot(review_path, root)
    review, _, discovery_events, source_events, _ = _load(review_path, root)
    result = {"format": "semiconductor-atlas-discovery-handoff-history-v1", "as_of": as_of,
              "selection": "retrospective_target_selected_not_blind", "discovery": None, "source": None,
              "link_review_available": _instant(as_of) >= _instant(review["reviewed_at"]),
              "linked": False, "facts": None, "boundaries": dict(BOUNDARIES)}
    for name, module, events in (("discovery", discovery_review, discovery_events), ("source", curated_review, source_events)):
        report = module._fold(events, as_of=as_of)
        candidate = next((row for row in report["candidates"] if row["id"] == review[name]["candidate_id"]), None)
        if candidate is not None:
            result[name] = {key: candidate[key] for key in ("id", "source_url", "status", "first_recorded_at", "last_event_id", "requires_reopen")}
    if result["link_review_available"]:
        result["linked"], result["facts"] = True, artifact["facts"]
    _check_snapshot(snapshot, review_path, root)
    return result
