"""Verify bounded acquisition attempts without treating failed checks as absence."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from .ai_critical import _iso_timestamp
from .ai_critical_changes import (
    _open_real_directory_fd, _read_regular_file_at, _strict_json,
)


FORMAT = "semiconductor-atlas-curated-source-checks-v1"
SOURCE_FORMAT = "semiconductor-atlas-publisher-source-checks-v1"


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be nonempty text")
    return value


def _natural(value: object, context: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{context} must be a nonnegative integer")
    return value


def _https(value: object, context: str) -> str:
    value = _text(value, context)
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError(f"{context} must be public HTTPS without credentials")
    return value


def _read(path: Path) -> bytes:
    _, descriptor = _open_real_directory_fd(path.parent, "source checks parent", create=False)
    try:
        return _read_regular_file_at(descriptor, path.name, "source checks file")
    finally:
        os.close(descriptor)


def _later(first: str, second: str | None) -> str:
    if second is None:
        return first
    return max((first, second), key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))


def validate_source_checks(
    ledger_path: str | Path, archive_root: str | Path,
) -> dict[str, Any]:
    """Bind every retained response to its recorded hash and summarize checks.

    Successful transport is not review acceptance, publisher completeness, or
    proof of a claim. Failed attempts remain in the denominator. A missing
    invocation clock is permitted only for failures with an observation clock.
    """
    raw = _read(Path(ledger_path))
    ledger = _strict_json(raw, "source checks ledger")
    if not isinstance(ledger, dict) or ledger.get("format") not in (FORMAT, SOURCE_FORMAT):
        raise ValueError("invalid source checks format")
    scope_field = "checked_source_id" if ledger["format"] == SOURCE_FORMAT else "checked_facility_key"
    if scope_field == "checked_source_id" and "checked_facility_key" in ledger:
        raise ValueError("publisher checks cannot assert a facility scope")
    if ledger.get("absence_inference_allowed") is not False:
        raise ValueError("curated URL checks cannot permit absence inference")
    for field in ("review_scope_id", scope_field, "clock_precision"):
        _text(ledger.get(field), field)
    attempts = ledger.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise ValueError("source checks require a nonempty attempts array")
    seen: set[str] = set()
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    urls: dict[str, dict[str, Any]] = {}
    root = Path(archive_root)
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise ValueError("source check must be an object")
        identifier = _text(attempt.get("id"), "attempt.id")
        if identifier in seen:
            raise ValueError("duplicate source check id")
        seen.add(identifier)
        url = _https(attempt.get("url"), "attempt.url")
        _https(attempt.get("final_url"), "attempt.final_url")
        _text(attempt.get("scope"), "attempt.scope")
        if attempt.get("negative_evidence_eligible") is not False:
            raise ValueError("curated URL checks cannot supply negative evidence")
        purpose = attempt.get("purpose")
        if purpose not in {"source_document", "access_policy", "rights_policy"}:
            raise ValueError("unknown source check purpose")
        status = attempt.get("http_status")
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise ValueError("invalid HTTP status")
        exit_code = _natural(attempt.get("curl_exit_code"), "curl_exit_code")
        successful = exit_code == 0 and status is not None and 200 <= status < 300
        if attempt.get("outcome") != ("succeeded" if successful else "failed"):
            raise ValueError("outcome contradicts HTTP/transport result")
        if successful:
            if attempt.get("error") is not None:
                raise ValueError("successful source check has an error")
            if type(attempt.get("tls_verification_result")) is not int or attempt["tls_verification_result"] != 0:
                raise ValueError("successful source check requires verified TLS")
        else:
            _text(attempt.get("error"), "failed source check error")
        start, finish = attempt.get("started_at"), attempt.get("finished_at")
        if start is None or finish is None:
            if start is not None or finish is not None or successful:
                raise ValueError("successful checks require both invocation clocks")
            clock = _iso_timestamp(attempt.get("observed_at"), "observed_at")
            _text(attempt.get("clock_note"), "clock_note")
        else:
            start = _iso_timestamp(start, "started_at")
            clock = _iso_timestamp(finish, "finished_at")
            if datetime.fromisoformat(clock.replace("Z", "+00:00")) < datetime.fromisoformat(start.replace("Z", "+00:00")):
                raise ValueError("source check finishes before it starts")
        count = _natural(attempt.get("bytes"), "attempt.bytes")
        path = attempt.get("path")
        if path is None:
            if successful or count != 0 or attempt.get("sha256") is not None:
                raise ValueError("missing response body cannot be successful or hash-bound")
        else:
            relative = PurePosixPath(_text(path, "attempt.path"))
            if relative.is_absolute() or ".." in relative.parts or str(relative) != path:
                raise ValueError("source response path must be a canonical relative path")
            body = _read(root.joinpath(*relative.parts))
            if len(body) != count or hashlib.sha256(body).hexdigest() != attempt.get("sha256"):
                raise ValueError("source response does not match its recorded bytes and hash")
        key = tuple(_text(attempt.get(field), field) for field in ("company", "country_code", "source_family"))
        if len(key[1]) != 2 or not key[1].isalpha() or not key[1].isupper():
            raise ValueError("country_code must use alpha-2 form")
        group = groups.setdefault(key, {
            "company": key[0], "country_code": key[1], "source_family": key[2],
            "attempt_count": 0, "succeeded": 0, "failed": 0,
            "source_document_successes": 0, "last_document_success_at": None,
            "last_failed_check_at": None,
        })
        group["attempt_count"] += 1
        group["succeeded" if successful else "failed"] += 1
        endpoint = urls.setdefault(url, {
            "url": url, "last_success_at": None, "last_failure_at": None,
            "last_success_sha256": None,
        })
        if successful:
            if endpoint["last_success_at"] is None or _later(clock, endpoint["last_success_at"]) == clock:
                endpoint.update(last_success_at=clock, last_success_sha256=attempt["sha256"])
            if purpose == "source_document":
                group["source_document_successes"] += 1
                group["last_document_success_at"] = _later(clock, group["last_document_success_at"])
        else:
            endpoint["last_failure_at"] = _later(clock, endpoint["last_failure_at"])
            group["last_failed_check_at"] = _later(clock, group["last_failed_check_at"])
    return {
        "format": ("semiconductor-atlas-publisher-source-check-report-v1" if ledger["format"] == SOURCE_FORMAT
                   else "semiconductor-atlas-curated-source-check-report-v1"),
        "review_scope_id": ledger["review_scope_id"],
        scope_field: ledger[scope_field],
        "ledger_sha256": hashlib.sha256(raw).hexdigest(),
        "attempt_count": len(attempts),
        "absence_inference_allowed": False,
        "verification_scope": "Recorded attempt consistency and exact retained response bytes; not claim truth or complete publisher coverage.",
        "groups": [groups[key] for key in sorted(groups)],
        "urls": [urls[key] for key in sorted(urls)],
    }
