"""Bounded fixed-URL acquisition with reviewed policy gates and offline replay."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .ai_critical import _html_visible_text, _iso_timestamp, ensure_real_directory
from .ai_critical_changes import _pretty_bytes, _strict_json
from .source_checks import FORMAT as CHECK_FORMAT, _https, _read, validate_source_checks


PLAN_FORMAT = "semiconductor-atlas-curated-acquisition-plan-v1"
RUN_FORMAT = "semiconductor-atlas-curated-acquisition-run-v2"
RULE_VERSION = "fixed-url-review-candidates-v1"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(_iso_timestamp(value, "clock").replace("Z", "+00:00"))


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonempty text")
    return value


def _keys(row: object, required: set[str]) -> dict:
    if not isinstance(row, dict) or set(row) != required:
        raise ValueError(f"expected exactly fields {sorted(required)}")
    return row


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"a", "area"}:
            self.links.extend(value for key, value in attrs if key == "href" and value is not None)


def normalized_bytes(raw: bytes, normalization: str) -> bytes:
    text = raw.decode("utf-8", errors="strict")
    if normalization == "robots_text_v1":
        return (text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n").encode("utf-8")
    if normalization == "html_visible_text_v1":
        return _html_visible_text(text).encode("utf-8")
    if normalization in {"html_policy_v1", "html_policy_v2"}:
        parser = _Links()
        parser.feed(text)
        links = parser.links
        if normalization == "html_policy_v2":
            links = [_policy_link(value) for value in links]
        return (_html_visible_text(text) + "\nLINKS\n" + json.dumps(
            sorted(links), ensure_ascii=False, separators=(",", ":")
        ) + "\n").encode("utf-8")
    raise ValueError("unsupported normalization")


def _policy_link(value: str) -> str:
    # Cloudflare rotates the XOR key for publicly rendered contact links.
    # Bind the decoded destination hash, not the per-response key.
    match = re.fullmatch(r"/cdn-cgi/l/email-protection#([0-9a-fA-F]+)", value)
    if match and len(match[1]) >= 4 and len(match[1]) % 2 == 0:
        encoded = bytes.fromhex(match[1])
        destination = bytes(byte ^ encoded[0] for byte in encoded[1:])
        return "/cdn-cgi/l/email-protection#destination-sha256=" + _sha(destination)
    return value


def load_plan(path: str | Path) -> tuple[dict[str, Any], bytes]:
    raw = _read(Path(path))
    plan = _keys(_strict_json(raw, "acquisition plan"), {
        "format", "plan_id", "review_scope_id", "checked_facility_key", "reviewed_at",
        "expires_at", "decision", "user_agent", "minimum_interval_seconds", "timeout_seconds",
        "max_response_bytes", "policies", "documents", "notes", "review_record",
    })
    if plan["format"] != PLAN_FORMAT or plan["decision"] != "approved_exact_urls":
        raise ValueError("plan must approve only exact reviewed URLs")
    for key in ("plan_id", "review_scope_id", "checked_facility_key", "notes", "user_agent"):
        _text(plan[key])
    if any(character in plan["user_agent"] for character in "\r\n"):
        raise ValueError("invalid user agent")
    if not _instant(plan["reviewed_at"]) < _instant(plan["expires_at"]):
        raise ValueError("plan expiry must follow review")
    review = _keys(plan["review_record"], {"path", "sha256"})
    path_value = Path(_text(review["path"]))
    if path_value.is_absolute() or ".." in path_value.parts:
        raise ValueError("review record path must be repository-relative")
    if not isinstance(review["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", review["sha256"]):
        raise ValueError("invalid review record hash")
    for field, low, high in (("minimum_interval_seconds", 1, 60), ("timeout_seconds", 1, 45), ("max_response_bytes", 1, 10_000_000)):
        if type(plan[field]) is not int or not low <= plan[field] <= high:
            raise ValueError(f"invalid {field}")
    common = {"id", "url", "purpose", "company", "country_code", "source_family", "scope", "media_types"}
    ids: set[str] = set()
    urls: set[str] = set()
    for kind in ("policies", "documents"):
        if not isinstance(plan[kind], list) or not 1 <= len(plan[kind]) <= 50:
            raise ValueError("plan requires bounded nonempty policies/documents")
        for entry in plan[kind]:
            _keys(entry, common | ({"normalization", "normalized_sha256"} if kind == "policies" else {"policy_ids", "required_text"}))
            if not isinstance(entry["id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", entry["id"]):
                raise ValueError("invalid entry id")
            url = _https(entry["url"], "plan URL")
            parsed = urlsplit(url)
            if parsed.query or parsed.fragment or parsed.port not in {None, 443} or parsed.hostname in {"localhost", "localhost.localdomain"}:
                raise ValueError("plan URL must be a canonical public document URL")
            if entry["id"] in ids or url in urls:
                raise ValueError("duplicate plan entry id or URL")
            ids.add(entry["id"]); urls.add(url)
            for field in ("company", "source_family", "scope"):
                _text(entry[field])
            if not isinstance(entry["country_code"], str) or not re.fullmatch(r"[A-Z]{2}", entry["country_code"]):
                raise ValueError("invalid country code")
            if not isinstance(entry["media_types"], list) or not entry["media_types"] or any(value not in {"text/html", "text/plain"} for value in entry["media_types"]):
                raise ValueError("unsupported media types")
            if kind == "policies":
                if entry["purpose"] not in {"access_policy", "rights_policy"}:
                    raise ValueError("invalid policy purpose")
                expected_normalizations = {"robots_text_v1"} if entry["purpose"] == "access_policy" else {"html_policy_v1", "html_policy_v2"}
                if entry["normalization"] not in expected_normalizations:
                    raise ValueError("policy normalization does not match purpose")
                if not isinstance(entry["normalized_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["normalized_sha256"]):
                    raise ValueError("invalid policy hash")
                if entry["purpose"] == "access_policy" and parsed.path != "/robots.txt":
                    raise ValueError("access policy must be origin robots.txt")
            else:
                if entry["purpose"] != "source_document":
                    raise ValueError("invalid document purpose")
                if not isinstance(entry["required_text"], list) or not entry["required_text"]:
                    raise ValueError("document requires reviewed identity text")
                for marker in entry["required_text"]:
                    _text(marker)
    policies = {row["id"]: row for row in plan["policies"]}
    for entry in plan["documents"]:
        policy_ids = entry["policy_ids"]
        if not isinstance(policy_ids, list) or len(policy_ids) != 2 or any(not isinstance(value, str) or value not in policies for value in policy_ids):
            raise ValueError("each document requires two known policy IDs")
        bound = [policies[key] for key in policy_ids]
        if {row["purpose"] for row in bound} != {"access_policy", "rights_policy"}:
            raise ValueError("document requires both access and rights review")
        if any(urlsplit(row["url"]).netloc != urlsplit(entry["url"]).netloc for row in bound):
            raise ValueError("document policies must belong to the same origin")
    return plan, raw


def curl_fetch(entry: dict, destination: Path, plan: dict) -> dict[str, Any]:
    """One identified HTTPS GET. No redirects, retries, cookies, or curlrc."""
    limit = min(plan["max_response_bytes"], 65536) if entry["purpose"] == "access_policy" else plan["max_response_bytes"]
    command = [
        "curl", "--disable", "--silent", "--show-error", "--globoff",
        "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "0",
        "--connect-timeout", "10", "--max-time", str(plan["timeout_seconds"]),
        "--max-filesize", str(limit), "--user-agent", plan["user_agent"],
        "--header", "Accept: " + ",".join(entry["media_types"]),
        "--output", str(destination), "--write-out", "%{json}", "--url", entry["url"],
    ]
    try:
        process = subprocess.run(command, capture_output=True, timeout=plan["timeout_seconds"] + 5, check=False)
        metadata = json.loads(process.stdout) if process.stdout.strip() else {}
        if not isinstance(metadata, dict):
            raise ValueError("curl metadata must be an object")
        metadata["curl_exit_code"] = process.returncode
        metadata["transport_error"] = process.stderr.decode("utf-8", errors="replace").strip() or None
        return metadata
    except subprocess.TimeoutExpired:
        return {"curl_exit_code": 28, "transport_error": "Process deadline exceeded; response may be incomplete"}
    except (OSError, ValueError):
        return {"curl_exit_code": 1, "transport_error": "Transport unavailable or returned invalid metadata"}


def _write(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _attempt(entry: dict, root: Path, plan: dict, transport: Callable) -> dict:
    relative = f"responses/{entry['id']}.body"
    target = root / relative
    started = _now()
    metadata = transport(entry, target, plan)
    finished = _now()
    status = metadata.get("http_code") or None
    exit_code = metadata.get("curl_exit_code", 1)
    content_type = metadata.get("content_type") or None
    succeeded = exit_code == 0 and type(status) is int and 200 <= status < 300
    body = _read(target) if target.exists() else None
    error = metadata.get("transport_error")
    if not succeeded and not error:
        error = f"HTTP {status}" if status else "No completed response"
    result = {
        **{key: entry[key] for key in ("id", "url", "purpose", "company", "country_code", "source_family", "scope")},
        "started_at": started, "finished_at": finished,
        "http_status": status, "content_type": content_type,
        "curl_exit_code": exit_code, "error": None if succeeded else error,
        "final_url": metadata.get("url_effective") or entry["url"],
        "http_version": metadata.get("http_version", "0"),
        "redirect_count": metadata.get("num_redirects", 0),
        "transport_elapsed_seconds": metadata.get("time_total"),
        "tls_verification_result": metadata.get("ssl_verify_result"),
        "path": relative if body is not None else None,
        "bytes": len(body) if body is not None else 0,
        "sha256": _sha(body) if body is not None else None,
        "outcome": "succeeded" if succeeded else "failed",
        "negative_evidence_eligible": False,
    }
    return result


def _content_error(entry: dict, attempt: dict, root: Path, plan: dict) -> str | None:
    if attempt["outcome"] != "succeeded":
        return "failed_check"
    if attempt["final_url"] != entry["url"] or attempt["redirect_count"] != 0:
        return "unapproved_redirect"
    if (attempt["content_type"] or "").split(";", 1)[0].strip().lower() not in entry["media_types"]:
        return "unexpected_media_type"
    if not 0 < attempt["bytes"] <= plan["max_response_bytes"] or attempt["path"] is None:
        return "empty_or_oversize_body"
    if entry["purpose"] == "source_document":
        try:
            visible = normalized_bytes(_read(root / attempt["path"]), "html_visible_text_v1").decode("utf-8")
        except UnicodeError:
            return "unsupported_encoding"
        if not visible:
            return "empty_visible_text"
        if any(marker not in visible for marker in entry["required_text"]):
            return "document_identity_requires_review"
    return None


def _previous_documents(plan: dict, prior_ledger: dict | None, root: Path) -> dict:
    previous = {}
    entries = {row["url"]: row for row in plan["documents"]}
    for item in prior_ledger["attempts"] if prior_ledger else []:
        entry = entries.get(item["url"])
        if entry and item["purpose"] == "source_document" and _content_error(entry, item, root / "prior", plan) is None:
            old = previous.get(item["url"])
            if old is None or _instant(item["finished_at"]) > _instant(old["finished_at"]):
                previous[item["url"]] = item
    return previous


def _last_successful_checks(plan: dict, ledger: dict, root: Path, prior: dict | None) -> dict | None:
    previous = _previous_documents(plan, prior, root)
    selected = {url: {**item, "path": "prior/" + item["path"]} for url, item in previous.items()}
    entries = {row["url"]: row for row in plan["documents"]}
    for item in ledger["attempts"]:
        entry = entries.get(item["url"])
        if entry and _content_error(entry, item, root, plan) is None:
            selected[item["url"]] = item
    if not selected:
        return None
    return {
        "format": CHECK_FORMAT, "review_scope_id": plan["review_scope_id"],
        "checked_facility_key": plan["checked_facility_key"],
        "clock_precision": "Original source-check clocks; carried observations are not new retrievals",
        "absence_inference_allowed": False,
        "attempts": [{**item, "id": entries[url]["id"]} for url, item in sorted(selected.items())],
    }


def derive_results(plan: dict, ledger: dict, root: Path, prior_ledger: dict | None = None) -> dict:
    """Replay every gate and review candidate from retained bytes, with no network."""
    attempts = {row["id"]: row for row in ledger["attempts"]}
    if len(attempts) != len(ledger["attempts"]):
        raise ValueError("duplicate attempt")
    expected_ids = {row["id"] for row in plan["policies"] + plan["documents"]}
    if not set(attempts) <= expected_ids:
        raise ValueError("attempt outside approved plan")
    for entry in plan["policies"] + plan["documents"]:
        if entry["id"] in attempts:
            for field in ("url", "purpose", "company", "country_code", "source_family", "scope"):
                if attempts[entry["id"]][field] != entry[field]:
                    raise ValueError("attempt metadata does not match plan")
    policy_results = []
    for policy in plan["policies"]:
        attempt = attempts.get(policy["id"])
        if attempt is None:
            raise ValueError("missing planned policy check")
        reason = _content_error(policy, attempt, root, plan)
        digest = None
        if reason is None:
            try:
                digest = _sha(normalized_bytes(_read(root / attempt["path"]), policy["normalization"]))
                if digest != policy["normalized_sha256"]:
                    reason = "policy_changed_requires_review"
            except UnicodeError:
                reason = "unsupported_encoding"
        policy_results.append({"id": policy["id"], "accepted": reason is None, "reason": reason, "normalized_sha256": digest})
    accepted = {row["id"] for row in policy_results if row["accepted"]}
    previous = _previous_documents(plan, prior_ledger, root)
    documents = []
    for entry in plan["documents"]:
        prior = previous.get(entry["url"])
        result = {
            "id": entry["id"], "url": entry["url"], "company": entry["company"],
            "country_code": entry["country_code"], "source_family": entry["source_family"],
            "status": None, "review_required": False, "current_sha256": None,
            "prior_success_at": prior["finished_at"] if prior else None,
            "prior_sha256": prior["sha256"] if prior else None,
            "current_text_sha256": None, "prior_text_sha256": None,
            "negative_evidence_eligible": False,
        }
        if not set(entry["policy_ids"]) <= accepted:
            if entry["id"] in attempts:
                raise ValueError("source document fetched despite failed policy gate")
            result["status"] = "not_attempted_policy_blocked"
        else:
            attempt = attempts.get(entry["id"])
            if attempt is None:
                raise ValueError("approved document has no attempt record")
            if any(
                _instant(attempt["started_at"]) < _instant(attempts[key]["finished_at"])
                for key in entry["policy_ids"]
            ):
                raise ValueError("document acquisition predates its policy checks")
            result["current_sha256"] = attempt["sha256"]
            reason = _content_error(entry, attempt, root, plan)
            if reason:
                result["status"] = reason
            else:
                try:
                    current_text = normalized_bytes(_read(root / attempt["path"]), "html_visible_text_v1")
                    result["current_text_sha256"] = _sha(current_text)
                    if not current_text.strip():
                        result["status"] = "empty_visible_text"
                    elif prior is None:
                        result["status"] = "first_observation_requires_review"
                    else:
                        prior_text = normalized_bytes(_read(root / "prior" / prior["path"]), "html_visible_text_v1")
                        result["prior_text_sha256"] = _sha(prior_text)
                        result["status"] = (
                            "unchanged" if attempt["sha256"] == prior["sha256"]
                            else "raw_bytes_only" if current_text == prior_text
                            else "visible_text_changed_requires_review"
                        )
                    result["review_required"] = result["status"] in {"first_observation_requires_review", "visible_text_changed_requires_review"}
                except UnicodeError:
                    result["status"] = "unsupported_encoding"
        documents.append(result)
    return {
        "format": "semiconductor-atlas-curated-acquisition-results-v1", "rule_version": RULE_VERSION,
        "policies": policy_results, "documents": documents,
        "review_required_count": sum(row["review_required"] for row in documents),
        "attention_required": any(not row["accepted"] for row in policy_results) or any(row["status"] not in {"unchanged", "raw_bytes_only"} for row in documents),
        "coverage_scope": "Exact plan URLs only; not complete publisher coverage or accepted claim updates.",
        "absence_inference_allowed": False,
    }


def _inventory(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("capture contains a symlink")
        if path.is_dir() or path == root / "manifest.json":
            continue
        raw = _read(path)
        result[str(path.relative_to(root))] = {"bytes": len(raw), "sha256": _sha(raw)}
    return result


def validate_capture(root_value: str | Path) -> dict:
    root = Path(root_value)
    plan, plan_raw = load_plan(root / "plan.json")
    if _sha(_read(root / "review.json")) != plan["review_record"]["sha256"]:
        raise ValueError("review record hash mismatch")
    run = _keys(_strict_json(_read(root / "run.json"), "acquisition run"), {
        "format", "plan_sha256", "started_at", "finished_at", "prior_ledger_sha256",
        "capture_code_sha256", "source_checks_code_sha256", "raw_redistribution", "claim_acceptance",
    })
    if run.get("format") not in {RUN_FORMAT, "semiconductor-atlas-curated-acquisition-run-v1"} or run.get("plan_sha256") != _sha(plan_raw):
        raise ValueError("invalid acquisition run or plan binding")
    if run["raw_redistribution"] is not False or run["claim_acceptance"] is not False:
        raise ValueError("capture cannot grant redistribution or accept claims")
    for key in ("capture_code_sha256", "source_checks_code_sha256"):
        if not isinstance(run[key], str) or not re.fullmatch(r"[0-9a-f]{64}", run[key]):
            raise ValueError("invalid code hash")
    if not _instant(plan["reviewed_at"]) <= _instant(run["started_at"]) <= _instant(run["finished_at"]) < _instant(plan["expires_at"]):
        raise ValueError("run is outside reviewed plan validity")
    validate_source_checks(root / "source_checks.json", root)
    ledger = _strict_json(_read(root / "source_checks.json"), "checks")
    if ledger["checked_facility_key"] != plan["checked_facility_key"] or ledger["review_scope_id"] != plan["review_scope_id"]:
        raise ValueError("ledger scope differs from plan")
    for attempt in ledger["attempts"]:
        if not _instant(run["started_at"]) <= _instant(attempt["started_at"]) <= _instant(attempt["finished_at"]) <= _instant(run["finished_at"]):
            raise ValueError("attempt clocks outside run")
        if attempt["path"] not in {None, f"responses/{attempt['id']}.body"}:
            raise ValueError("response path does not match its attempt")
        if _read(root / "responses" / f"{attempt['id']}.attempt.json") != _pretty_bytes(attempt):
            raise ValueError("attempt receipt differs from completed ledger")
    prior = None
    if run["prior_ledger_sha256"] is not None:
        prior_path = root / "prior" / "source_checks.json"
        prior_raw = _read(prior_path)
        if _sha(prior_raw) != run["prior_ledger_sha256"]:
            raise ValueError("prior ledger binding mismatch")
        validate_source_checks(prior_path, root / "prior")
        prior = _strict_json(prior_raw, "prior checks")
        for attempt in prior["attempts"]:
            if _instant(attempt.get("finished_at") or attempt["observed_at"]) > _instant(run["started_at"]):
                raise ValueError("prior checks contain future knowledge")
    expected = derive_results(plan, ledger, root, prior)
    if _read(root / "results.json") != _pretty_bytes(expected):
        raise ValueError("acquisition results do not replay exactly")
    expected_paths = {"plan.json", "review.json", "run.json", "source_checks.json", "results.json"}
    history = _last_successful_checks(plan, ledger, root, prior)
    if history and run["format"] == RUN_FORMAT:
        expected_paths.add("last_successful_checks.json")
        if _read(root / "last_successful_checks.json") != _pretty_bytes(history):
            raise ValueError("last successful checks do not replay exactly")
        validate_source_checks(root / "last_successful_checks.json", root)
    for attempt in ledger["attempts"]:
        expected_paths.add(f"responses/{attempt['id']}.attempt.json")
        if attempt["path"] is not None:
            expected_paths.add(attempt["path"])
    if prior is not None:
        expected_paths.add("prior/source_checks.json")
        expected_paths.update(f"prior/{item['path']}" for item in prior["attempts"] if item["path"] is not None)
    if set(_inventory(root)) != expected_paths:
        raise ValueError("capture contains missing or unmanaged files")
    manifest = _strict_json(_read(root / "manifest.json"), "capture manifest")
    if manifest != {"format": "semiconductor-atlas-curated-acquisition-manifest-v1", "files": _inventory(root)}:
        raise ValueError("capture inventory or bytes differ from manifest")
    return expected


def capture_sources(
    plan_path: str | Path, output_dir: str | Path, *,
    prior_checks: str | Path | None = None, prior_root: str | Path | None = None,
    review_root: str | Path | None = None,
    transport: Callable = curl_fetch,
) -> dict:
    """Create a fresh run; incomplete directories are retained and never reused."""
    plan, raw = load_plan(plan_path)
    review_base = Path(review_root) if review_root is not None else Path(plan_path).resolve().parent.parent
    review_raw = _read(review_base / plan["review_record"]["path"])
    if _sha(review_raw) != plan["review_record"]["sha256"]:
        raise ValueError("review record hash mismatch")
    started = _now()
    if not _instant(plan["reviewed_at"]) <= _instant(started) < _instant(plan["expires_at"]):
        raise ValueError("plan is not currently within its reviewed validity window")
    if (prior_checks is None) != (prior_root is None):
        raise ValueError("provide both prior_checks and prior_root")
    root = Path(output_dir)
    root_parent = ensure_real_directory(root.parent, "capture output parent")
    root = root_parent / root.name
    for protected in (Path(plan_path).parent, Path(prior_root) if prior_root is not None else None):
        if protected is not None and root.resolve().is_relative_to(protected.resolve()):
            raise ValueError("output must be outside prior inputs and plan directory")
    prior_raw = None
    prior = None
    if prior_checks is not None:
        validate_source_checks(prior_checks, prior_root)
        prior_raw = _read(Path(prior_checks))
        prior = _strict_json(prior_raw, "prior checks")
        for attempt in prior["attempts"]:
            if _instant(attempt.get("finished_at") or attempt["observed_at"]) > _instant(started):
                raise ValueError("prior checks contain future knowledge")
    root.mkdir(mode=0o700, exist_ok=False)
    (root / "responses").mkdir()
    _write(root / "plan.json", raw)
    _write(root / "review.json", review_raw)
    if prior is not None:
        (root / "prior").mkdir()
        _write(root / "prior" / "source_checks.json", prior_raw)
        for attempt in prior["attempts"]:
            if attempt["path"] is not None:
                target = root / "prior" / attempt["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    _write(target, _read(Path(prior_root) / attempt["path"]))
    attempts = []
    accepted = set()
    for entry in plan["policies"] + plan["documents"]:
        if entry["purpose"] == "source_document" and not set(entry["policy_ids"]) <= accepted:
            continue
        if attempts:
            time.sleep(plan["minimum_interval_seconds"])
        if _instant(_now()) >= _instant(plan["expires_at"]):
            raise ValueError("plan expired during acquisition; incomplete output retained")
        attempt = _attempt(entry, root, plan, transport)
        attempts.append(attempt)
        _write(root / "responses" / f"{entry['id']}.attempt.json", _pretty_bytes(attempt))
        if entry["purpose"] != "source_document" and _content_error(entry, attempt, root, plan) is None:
            try:
                digest = _sha(normalized_bytes(_read(root / attempt["path"]), entry["normalization"]))
                if digest == entry["normalized_sha256"]:
                    accepted.add(entry["id"])
            except UnicodeError:
                pass
    ledger = {
        "format": CHECK_FORMAT, "review_scope_id": plan["review_scope_id"],
        "checked_facility_key": plan["checked_facility_key"],
        "clock_precision": "UTC microsecond invocation clocks", "absence_inference_allowed": False,
        "attempts": attempts,
    }
    _write(root / "source_checks.json", _pretty_bytes(ledger))
    _write(root / "results.json", _pretty_bytes(derive_results(plan, ledger, root, prior)))
    history = _last_successful_checks(plan, ledger, root, prior)
    if history:
        _write(root / "last_successful_checks.json", _pretty_bytes(history))
    _write(root / "run.json", _pretty_bytes({
        "format": RUN_FORMAT, "plan_sha256": _sha(raw), "started_at": started, "finished_at": _now(),
        "prior_ledger_sha256": _sha(prior_raw) if prior_raw is not None else None,
        "capture_code_sha256": _sha(Path(__file__).read_bytes()),
        "source_checks_code_sha256": _sha(Path(__file__).with_name("source_checks.py").read_bytes()),
        "raw_redistribution": False, "claim_acceptance": False,
    }))
    _write(root / "manifest.json", _pretty_bytes({
        "format": "semiconductor-atlas-curated-acquisition-manifest-v1", "files": _inventory(root),
    }))
    return validate_capture(root)
