from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas.source_checks import validate_source_checks


class SourceCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.body = b"official document fixture"
        (self.root / "response.html").write_bytes(self.body)
        self.attempt = {
            "id": "first", "url": "https://example.org/document",
            "final_url": "https://example.org/document", "path": "response.html",
            "started_at": "2026-09-07T02:00:00Z", "finished_at": "2026-09-07T02:00:01Z",
            "http_status": 200, "curl_exit_code": 0, "error": None,
            "tls_verification_result": 0, "outcome": "succeeded",
            "bytes": len(self.body), "sha256": hashlib.sha256(self.body).hexdigest(),
            "purpose": "source_document", "company": "Amkor", "country_code": "US",
            "source_family": "company-newsroom", "scope": "Exact URL only",
            "negative_evidence_eligible": False,
        }
        self.ledger = {
            "format": "semiconductor-atlas-curated-source-checks-v1",
            "review_scope_id": "test", "checked_facility_key": "amkor:peoria",
            "clock_precision": "second", "absence_inference_allowed": False,
            "attempts": [self.attempt],
        }

    def verify(self) -> dict:
        path = self.root / "checks.json"
        path.write_text(json.dumps(self.ledger), encoding="utf-8")
        return validate_source_checks(path, self.root)

    def test_failed_retry_preserves_last_success_and_counts_failure(self) -> None:
        failure = copy.deepcopy(self.attempt)
        failure.update(
            id="retry", path=None, bytes=0, sha256=None, http_status=None,
            curl_exit_code=28, error="timeout", outcome="failed",
            started_at=None, finished_at=None, observed_at="2026-09-07T03:00:00Z",
            clock_note="Invocation clocks not retained",
        )
        self.ledger["attempts"].append(failure)
        report = self.verify()
        self.assertFalse(report["absence_inference_allowed"])
        self.assertEqual(2, report["attempt_count"])
        self.assertEqual(1, report["groups"][0]["failed"])
        self.assertEqual(1, report["groups"][0]["source_document_successes"])
        self.assertEqual("2026-09-07T02:00:01Z", report["urls"][0]["last_success_at"])
        self.assertEqual("2026-09-07T03:00:00Z", report["urls"][0]["last_failure_at"])
        self.assertEqual(self.attempt["sha256"], report["urls"][0]["last_success_sha256"])

    def test_retained_error_page_is_failed_not_document_success(self) -> None:
        self.attempt.update(http_status=403, error="HTTP 403", outcome="failed")
        report = self.verify()
        self.assertEqual(1, report["groups"][0]["failed"])
        self.assertEqual(0, report["groups"][0]["source_document_successes"])
        self.assertIsNone(report["urls"][0]["last_success_at"])

    def test_rejects_forged_status_hash_clock_and_absence_permission(self) -> None:
        original = copy.deepcopy(self.attempt)
        for field, bad in (
            ("http_status", 404), ("http_status", True),
            ("curl_exit_code", True), ("tls_verification_result", 1),
            ("outcome", "failed"), ("sha256", "a" * 64), ("bytes", 0),
            ("path", None), ("path", "../response.html"),
            ("url", "http://example.org/document"),
            ("final_url", "https://user:secret@example.org/document"),
            ("finished_at", "2026-09-07T01:00:00Z"), ("started_at", None),
            ("negative_evidence_eligible", True), ("purpose", "complete_search"),
        ):
            with self.subTest(field=field):
                self.ledger["attempts"] = [{**original, field: bad}]
                with self.assertRaises(ValueError):
                    self.verify()
        self.ledger["attempts"] = [original]
        self.ledger["absence_inference_allowed"] = True
        with self.assertRaises(ValueError):
            self.verify()

    def test_duplicate_attempt_and_changed_or_symlink_body_are_rejected(self) -> None:
        self.ledger["attempts"].append(copy.deepcopy(self.attempt))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.verify()
        self.ledger["attempts"].pop()
        (self.root / "response.html").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "recorded bytes"):
            self.verify()
        (self.root / "target").write_bytes(self.body)
        (self.root / "response.html").unlink()
        (self.root / "response.html").symlink_to(self.root / "target")
        with self.assertRaises(ValueError):
            self.verify()

    def test_policy_success_does_not_refresh_document_clock(self) -> None:
        self.attempt["purpose"] = "rights_policy"
        report = self.verify()
        self.assertEqual(1, report["groups"][0]["succeeded"])
        self.assertEqual(0, report["groups"][0]["source_document_successes"])
        self.assertIsNone(report["groups"][0]["last_document_success_at"])

    def test_fractional_clock_order_is_chronological_not_lexical(self) -> None:
        self.attempt.update(started_at="2026-09-07T02:00:00.500000Z", finished_at="2026-09-07T02:00:00Z")
        with self.assertRaisesRegex(ValueError, "finishes before"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
