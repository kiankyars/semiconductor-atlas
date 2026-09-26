"""Policy-gated NIST index discovery; linked documents are never acquired."""

from __future__ import annotations

import re
import time
from pathlib import Path

from .ai_critical import ensure_real_directory, load_baseline
from .ai_critical_changes import _pretty_bytes, _strict_json
from .adapters.nist_discovery import parse_index
from .curated_capture import (
    _attempt, _content_error, _instant, _inventory, _keys, _now, _sha, _text, _write,
    curl_fetch, normalized_bytes,
)
from .curated_coverage import _bound_file
from .source_checks import SOURCE_FORMAT, _read, validate_source_checks


PLAN_FORMAT = "semiconductor-atlas-nist-discovery-plan-v1"
RUN_FORMAT = "semiconductor-atlas-nist-discovery-run-v1"
RESULT_FORMAT = "semiconductor-atlas-nist-discovery-result-v1"
MANIFEST_FORMAT = "semiconductor-atlas-nist-discovery-manifest-v1"
ROOTS = {"news": "https://www.nist.gov/chips/chips-news-releases",
         "awards": "https://www.nist.gov/chips/chips-program-office-awards"}
POLICIES = {"access_policy": "https://www.nist.gov/robots.txt",
            "rights_policy": "https://www.nist.gov/copyrights-disclaimers"}


def _json(path: Path) -> dict:
    return _strict_json(_read(path), str(path))


def _plan(raw: bytes) -> dict:
    plan = _keys(_strict_json(raw, "discovery plan"), {
        "format", "plan_id", "review_scope_id", "checked_source_id", "reviewed_at", "expires_at",
        "review_record", "baseline", "company_aliases", "user_agent", "minimum_interval_seconds",
        "timeout_seconds", "max_response_bytes", "max_pages_per_index", "policies", "indexes", "notes",
    })
    if plan["format"] != PLAN_FORMAT or plan["checked_source_id"] != "nist:chips-public-indexes":
        raise ValueError("unsupported publisher discovery plan")
    for field in ("plan_id", "review_scope_id", "notes", "user_agent"):
        _text(plan[field])
    if any(c in plan["user_agent"] for c in "\r\n"):
        raise ValueError("invalid discovery user agent")
    if not _instant(plan["reviewed_at"]) < _instant(plan["expires_at"]):
        raise ValueError("discovery expiry must follow review")
    for field, maximum in (("minimum_interval_seconds", 60), ("timeout_seconds", 45),
                           ("max_response_bytes", 10_000_000), ("max_pages_per_index", 16)):
        if type(plan[field]) is not int or not 1 <= plan[field] <= maximum:
            raise ValueError(f"invalid discovery {field}")
    for field in ("review_record", "baseline"):
        binding = _keys(plan[field], {"path", "sha256"})
        name = Path(_text(binding["path"]))
        if name.is_absolute() or ".." in name.parts or name.as_posix() != binding["path"]:
            raise ValueError("discovery binding must be canonical repository-relative")
        if not isinstance(binding["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", binding["sha256"]):
            raise ValueError("invalid discovery binding hash")
    if not isinstance(plan["policies"], list) or len(plan["policies"]) != 2:
        raise ValueError("discovery requires exactly two NIST policies")
    purposes = set()
    for policy in plan["policies"]:
        _keys(policy, {"id", "url", "purpose", "normalization", "normalized_sha256"})
        if not isinstance(policy["purpose"], str) or policy["purpose"] not in POLICIES or policy["url"] != POLICIES[policy["purpose"]]:
            raise ValueError("discovery policy origin or path is not approved")
        purposes.add(policy["purpose"])
        if policy["id"] != ("nist-robots" if policy["purpose"] == "access_policy" else "nist-rights"):
            raise ValueError("unexpected discovery policy id")
        permitted = {"robots_text_v1"} if policy["purpose"] == "access_policy" else {"html_policy_v2"}
        if not isinstance(policy["normalization"], str) or policy["normalization"] not in permitted or not isinstance(policy["normalized_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", policy["normalized_sha256"]):
            raise ValueError("invalid discovery policy normalization")
    if purposes != set(POLICIES):
        raise ValueError("missing discovery policy purpose")
    if not isinstance(plan["indexes"], list) or len(plan["indexes"]) != 2:
        raise ValueError("discovery requires the two explicitly reviewed indexes")
    if any(not isinstance(row, dict) for row in plan["indexes"]) or [row.get("kind") for row in plan["indexes"]] != ["news", "awards"]:
        raise ValueError("discovery index order must be news, awards")
    for index in plan["indexes"]:
        _keys(index, {"kind", "url", "required_text"})
        if index["url"] != ROOTS[index["kind"]]:
            raise ValueError("unapproved discovery index URL")
        if not isinstance(index["required_text"], list) or not index["required_text"]:
            raise ValueError("discovery index requires identity markers")
        for marker in index["required_text"]:
            _text(marker)
    if not isinstance(plan["company_aliases"], list) or not plan["company_aliases"]:
        raise ValueError("discovery requires reviewed company aliases")
    companies = set()
    for item in plan["company_aliases"]:
        _keys(item, {"company", "aliases"})
        company = _text(item["company"])
        if company in companies or not isinstance(item["aliases"], list) or not item["aliases"]:
            raise ValueError("duplicate company or empty aliases")
        companies.add(company)
        aliases = [_text(alias).casefold() for alias in item["aliases"]]
        if len(aliases) != len(set(aliases)):
            raise ValueError("duplicate discovery alias")
    return plan


def load_plan(path: str | Path, *, repository_root: str | Path | None = None) -> tuple[dict, bytes]:
    path = Path(path).absolute()
    root = Path(repository_root).absolute() if repository_root is not None else path.parent.parent
    raw = _read(path)
    plan = _plan(raw)
    _bound_file(root, plan["review_record"])
    baseline_path, _ = _bound_file(root, plan["baseline"])
    baseline = load_baseline(baseline_path, root, verify_source_bytes=False)
    if [row["company"] for row in plan["company_aliases"]] != [row["company"] for row in baseline.facilities]:
        raise ValueError("discovery aliases must retain the complete ordered baseline cohort")
    if _instant(baseline.spec["recorded_at"]) > _instant(plan["reviewed_at"]):
        raise ValueError("discovery review predates its baseline")
    return plan, raw


def _entry(policy: dict | None = None, *, kind: str | None = None, number: int = 0,
           url: str | None = None, markers: list[str] | None = None) -> dict:
    common = {"company": "NIST", "country_code": "US", "source_family": "nist-chips-index",
              "scope": "Publisher index only; the organization and country describe the publisher, not a facility"}
    if policy is not None:
        return {**common, **policy, "media_types": ["text/plain" if policy["purpose"] == "access_policy" else "text/html"]}
    return {**common, "id": f"{kind}-page-{number}", "url": url, "purpose": "source_document",
            "media_types": ["text/html"], "required_text": markers}


def _matched(plan: dict, entry: dict) -> list[str]:
    text = " ".join(str(entry.get(field) or "") for field in ("title", "summary", "source_native_scope"))
    return [row["company"] for row in plan["company_aliases"] if any(
        re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text, re.IGNORECASE) for alias in row["aliases"])]


def _page(entry: dict, attempt: dict, root: Path, plan: dict, kind: str) -> tuple[dict | None, str | None]:
    error = _content_error(entry, attempt, root, plan)
    if error:
        return None, error
    try:
        page = parse_index(_read(root / attempt["path"]), page_url=entry["url"], kind=kind)
        return page, None
    except (ValueError, UnicodeError) as error:
        return None, f"index_structure_requires_review: {error}"


def derive_results(plan: dict, ledger: dict, root: Path) -> dict:
    attempts = ledger["attempts"]
    position = 0
    policies = []
    for policy in plan["policies"]:
        if position >= len(attempts):
            raise ValueError("missing discovery policy attempt")
        attempt = attempts[position]
        position += 1
        entry = _entry(policy)
        if any(attempt[field] != entry[field] for field in ("id", "url", "purpose", "company", "country_code", "source_family", "scope")):
            raise ValueError("discovery policy attempt differs from plan")
        error = _content_error(entry, attempt, root, plan)
        digest = None
        if error is None:
            try:
                digest = _sha(normalized_bytes(_read(root / attempt["path"]), policy["normalization"]))
                if digest != policy["normalized_sha256"]:
                    error = "policy_changed_requires_review"
            except UnicodeError:
                error = "unsupported_policy_encoding"
        policies.append({"id": policy["id"], "accepted": error is None, "error": error, "normalized_sha256": digest})
    allowed = all(row["accepted"] for row in policies)
    indexes = []
    for index in plan["indexes"]:
        url = index["url"]
        pages, entries, seen = [], [], set()
        status = "policy_blocked" if not allowed else "page_limit_reached"
        if allowed:
            for number in range(plan["max_pages_per_index"]):
                if position >= len(attempts):
                    raise ValueError("missing discovery page attempt")
                attempt = attempts[position]
                position += 1
                entry = _entry(kind=index["kind"], number=number, url=url, markers=index["required_text"])
                if any(attempt[field] != entry[field] for field in ("id", "url", "purpose", "company", "country_code", "source_family", "scope")):
                    raise ValueError("unapproved discovery page or attempt order")
                page, error = _page(entry, attempt, root, plan, index["kind"])
                pages.append({"url": url, "attempt_id": attempt["id"], "sha256": attempt["sha256"],
                              "response_finished_at": attempt["finished_at"], "status": error or "parsed",
                              "parsed_entry_count": len(page["entries"]) if page else 0,
                              "visible_record_count": page["record_count"] if page else None,
                              "excluded_record_count": page["excluded_count"] if page else None,
                              "duplicate_record_count": page["duplicate_count"] if page else None,
                              "next_url": page["next_url"] if page else None})
                if error:
                    status = error
                    break
                duplicated = False
                for record in page["entries"]:
                    if record["url"] in seen:
                        duplicated = True
                    seen.add(record["url"])
                    entries.append({**record, "index_kind": index["kind"], "index_url": url,
                                    "index_response_sha256": attempt["sha256"], "observed_at": attempt["finished_at"],
                                    "matched_companies": _matched(plan, record), "facility_scope_verified": False,
                                    "linked_document_acquired": False})
                if duplicated:
                    status = "duplicate_entries_require_review"
                    break
                url = page["next_url"]
                if url is None:
                    status = "terminal_page_reached"
                    break
        indexes.append({"kind": index["kind"], "root_url": index["url"], "status": status,
                        "page_chain_complete": status == "terminal_page_reached", "pages": pages,
                        "entry_count": len(entries), "entries": entries})
    if position != len(attempts):
        raise ValueError("unplanned discovery requests")
    entries = [row for index in indexes for row in index["entries"]]
    return {"format": RESULT_FORMAT, "plan_id": plan["plan_id"], "checked_source_id": plan["checked_source_id"],
            "policies": policies, "indexes": indexes, "request_count": len(attempts),
            "observed_entry_count": len(entries), "company_matched_entry_count": sum(bool(row["matched_companies"]) for row in entries),
            "attention_required": not allowed or any(not row["page_chain_complete"] for row in indexes),
            "discovery_scope": "Observed current NIST news and award index page chains only; older news archive and unlinked documents are not covered.",
            "publisher_complete": False, "facility_coverage_complete": False, "claim_acceptance": False,
            "linked_document_acquisition_allowed": False, "absence_inference_allowed": False}


def validate_capture(root_value: str | Path) -> dict:
    root = Path(root_value).absolute()
    plan_raw = _read(root / "plan.json")
    plan = _plan(plan_raw)
    if _sha(_read(root / "review.json")) != plan["review_record"]["sha256"]:
        raise ValueError("discovery review binding mismatch")
    if _sha(_read(root / "baseline.json")) != plan["baseline"]["sha256"]:
        raise ValueError("discovery baseline binding mismatch")
    baseline = load_baseline(root / "baseline.json", root, verify_source_bytes=False)
    if [row["company"] for row in plan["company_aliases"]] != [row["company"] for row in baseline.facilities]:
        raise ValueError("discovery cohort binding mismatch")
    if _instant(baseline.spec["recorded_at"]) > _instant(plan["reviewed_at"]):
        raise ValueError("discovery review predates its baseline")
    run = _keys(_json(root / "run.json"), {"format", "started_at", "finished_at", "plan_sha256",
                                         "code_sha256", "parser_sha256", "claim_acceptance", "raw_redistribution"})
    if run["format"] != RUN_FORMAT or run["plan_sha256"] != _sha(plan_raw):
        raise ValueError("invalid discovery run")
    if run["claim_acceptance"] is not False or run["raw_redistribution"] is not False:
        raise ValueError("discovery run cannot accept claims or grant rights")
    for field in ("code_sha256", "parser_sha256"):
        if not isinstance(run[field], str) or not re.fullmatch(r"[0-9a-f]{64}", run[field]):
            raise ValueError("invalid discovery code binding")
    if not _instant(plan["reviewed_at"]) <= _instant(run["started_at"]) <= _instant(run["finished_at"]) < _instant(plan["expires_at"]):
        raise ValueError("discovery run outside reviewed interval")
    validate_source_checks(root / "source_checks.json", root)
    ledger = _json(root / "source_checks.json")
    if ledger["format"] != SOURCE_FORMAT or ledger["checked_source_id"] != plan["checked_source_id"] or ledger["review_scope_id"] != plan["review_scope_id"]:
        raise ValueError("discovery ledger scope mismatch")
    expected_paths = {"plan.json", "review.json", "baseline.json", "run.json", "source_checks.json", "results.json"}
    previous = None
    for attempt in ledger["attempts"]:
        if not _instant(run["started_at"]) <= _instant(attempt["started_at"]) <= _instant(attempt["finished_at"]) <= _instant(run["finished_at"]):
            raise ValueError("discovery attempt outside run clocks")
        if previous is not None and (_instant(attempt["started_at"])-_instant(previous)).total_seconds() < plan["minimum_interval_seconds"]:
            raise ValueError("discovery requests violate pacing")
        previous = attempt["finished_at"]
        receipt = f"responses/{attempt['id']}.attempt.json"
        if _read(root / receipt) != _pretty_bytes(attempt):
            raise ValueError("discovery attempt receipt mismatch")
        expected_paths.add(receipt)
        if attempt["path"] is not None:
            if attempt["path"] != f"responses/{attempt['id']}.body":
                raise ValueError("discovery response path mismatch")
            expected_paths.add(attempt["path"])
    expected = derive_results(plan, ledger, root)
    if _read(root / "results.json") != _pretty_bytes(expected):
        raise ValueError("discovery results do not replay")
    files = _inventory(root)
    if set(files) != expected_paths or _json(root / "manifest.json") != {"format": MANIFEST_FORMAT, "files": files}:
        raise ValueError("discovery manifest inventory mismatch")
    return expected


def capture_indexes(plan_path: str | Path, output: str | Path, *, repository_root: str | Path | None = None,
                    transport=curl_fetch) -> dict:
    plan_path = Path(plan_path).absolute()
    repository = Path(repository_root).absolute() if repository_root is not None else plan_path.parent.parent
    plan, raw = load_plan(plan_path, repository_root=repository)
    started = _now()
    if not _instant(plan["reviewed_at"]) <= _instant(started) < _instant(plan["expires_at"]):
        raise ValueError("discovery plan not currently within review window")
    output = Path(output).absolute()
    protected = [plan_path.parent, repository / plan["review_record"]["path"], repository / plan["baseline"]["path"]]
    if any(output.resolve().is_relative_to(p.resolve()) or p.resolve().is_relative_to(output.resolve()) for p in protected):
        raise ValueError("discovery output overlaps protected inputs")
    parent = ensure_real_directory(output.parent, "discovery output parent")
    root = parent / output.name
    root.mkdir(mode=0o700)
    (root / "responses").mkdir()
    _write(root / "plan.json", raw)
    for field, name in (("review_record", "review.json"), ("baseline", "baseline.json")):
        _, body = _bound_file(repository, plan[field])
        _write(root / name, body)
    attempts = []

    def request(entry: dict) -> dict:
        if attempts:
            time.sleep(plan["minimum_interval_seconds"])
        if _instant(_now()) >= _instant(plan["expires_at"]):
            raise ValueError("discovery review expired; incomplete receipts retained")
        attempt = _attempt(entry, root, plan, transport)
        attempts.append(attempt)
        _write(root / "responses" / f"{entry['id']}.attempt.json", _pretty_bytes(attempt))
        if attempt["outcome"] == "succeeded" and (type(attempt["tls_verification_result"]) is not int or attempt["tls_verification_result"] != 0):
            raise ValueError("unverified TLS; incomplete receipts retained without further requests")
        return attempt

    accepted = True
    for policy in plan["policies"]:
        entry = _entry(policy)
        attempt = request(entry)
        error = _content_error(entry, attempt, root, plan)
        try:
            accepted &= error is None and _sha(normalized_bytes(_read(root / attempt["path"]), policy["normalization"])) == policy["normalized_sha256"]
        except UnicodeError:
            accepted = False
    if accepted:
        for index in plan["indexes"]:
            url, seen = index["url"], set()
            for number in range(plan["max_pages_per_index"]):
                entry = _entry(kind=index["kind"], number=number, url=url, markers=index["required_text"])
                attempt = request(entry)
                page, error = _page(entry, attempt, root, plan, index["kind"])
                if error:
                    break
                urls = [record["url"] for record in page["entries"]]
                if seen.intersection(urls):
                    break
                seen.update(urls)
                url = page["next_url"]
                if url is None:
                    break
    ledger = {"format": SOURCE_FORMAT, "review_scope_id": plan["review_scope_id"],
              "checked_source_id": plan["checked_source_id"], "clock_precision": "UTC microsecond invocation clocks",
              "absence_inference_allowed": False, "attempts": attempts}
    _write(root / "source_checks.json", _pretty_bytes(ledger))
    _write(root / "results.json", _pretty_bytes(derive_results(plan, ledger, root)))
    _write(root / "run.json", _pretty_bytes({"format": RUN_FORMAT, "started_at": started, "finished_at": _now(),
           "plan_sha256": _sha(raw), "code_sha256": _sha(_read(Path(__file__))),
           "parser_sha256": _sha(_read(Path(__file__).with_name("adapters") / "nist_discovery.py")),
           "claim_acceptance": False, "raw_redistribution": False}))
    _write(root / "manifest.json", _pretty_bytes({"format": MANIFEST_FORMAT, "files": _inventory(root)}))
    return validate_capture(root)


def discovery_inventory(captures: list[str | Path], *, as_of: str) -> dict:
    cutoff = _instant(as_of)
    runs, documents, plans = [], {}, set()
    seen_runs = set()
    for path_value in captures:
        root = Path(path_value).absolute()
        result = validate_capture(root)
        manifest_hash = _sha(_read(root / "manifest.json"))
        if manifest_hash in seen_runs:
            raise ValueError("duplicate discovery capture input")
        seen_runs.add(manifest_hash)
        run = _json(root / "run.json")
        if _instant(run["finished_at"]) > cutoff:
            continue
        plans.add(run["plan_sha256"])
        runs.append({"manifest_sha256": manifest_hash, "finished_at": run["finished_at"],
                     "attention_required": result["attention_required"],
                     "indexes": [{key: index[key] for key in ("kind", "status", "page_chain_complete", "entry_count")} for index in result["indexes"]]})
        for index in result["indexes"]:
            for entry in index["entries"]:
                row = documents.setdefault(entry["url"], {"id": _sha(entry["url"].encode()), "url": entry["url"], "observations": []})
                row["observations"].append({**entry, "capture_manifest_sha256": manifest_hash})
    if len(plans) > 1:
        raise ValueError("inventory cannot silently mix discovery plan revisions")
    for row in documents.values():
        row["observations"].sort(key=lambda value: (_instant(value["observed_at"]), value["capture_manifest_sha256"], value["index_url"]))
        row["first_observed_at"] = row["observations"][0]["observed_at"]
        row["last_observed_at"] = row["observations"][-1]["observed_at"]
        row["matched_companies"] = sorted({company for item in row["observations"] for company in item["matched_companies"]})
        row["review_status"] = "requires_document_scope_and_access_review"
    runs.sort(key=lambda value: (_instant(value["finished_at"]), value["manifest_sha256"]))
    rows = sorted(documents.values(), key=lambda value: value["url"])
    return {"format": "semiconductor-atlas-nist-discovery-inventory-v1", "as_of": as_of,
            "plan_sha256": next(iter(plans)) if plans else None, "captures": runs,
            "latest_capture_attention_required": runs[-1]["attention_required"] if runs else True,
            "observed_document_count": len(rows), "company_matched_document_count": sum(bool(row["matched_companies"]) for row in rows),
            "documents": rows, "claim_acceptance": False, "facility_coverage_complete": False, "publisher_complete": False,
            "linked_document_acquisition_allowed": False, "absence_inference_allowed": False,
            "knowledge_note": "Union of explicitly supplied retained index captures through this cutoff. Entries survive later failures or rolling-window disappearance. This is discovery inventory, not a resolved reviewer queue, complete publisher history, or accepted facility evidence."}
