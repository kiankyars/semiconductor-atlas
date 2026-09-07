from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from semiconductor_atlas import curated_capture as capture


class CuratedCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.plans = self.root / "plans"
        self.plans.mkdir()
        self.plan_path = self.plans / "plan.json"
        review = b'{"review":"fixture"}\n'
        (self.root / "review.json").write_bytes(review)
        self.bodies = {"robots": b"User-agent: *\nDisallow: /private/\n", "rights": b'<p>Copyright. Research only.</p><a href="/terms">Terms</a>', "document": b"<h1>Project Update</h1><p>Phase one announced.</p>"}
        common = {"company": "Amkor", "country_code": "US", "source_family": "company-newsroom", "scope": "Exact URL only"}
        self.plan = {
            "format": capture.PLAN_FORMAT, "plan_id": "fixture", "review_scope_id": "fixture",
            "checked_facility_key": "amkor:peoria", "reviewed_at": "2026-01-01T00:00:00Z",
            "expires_at": "2027-01-01T00:00:00Z", "decision": "approved_exact_urls",
            "user_agent": "SemiconductorAtlas/0.1", "minimum_interval_seconds": 1,
            "timeout_seconds": 2, "max_response_bytes": 10000, "notes": "Fixture only",
            "review_record": {"path": "review.json", "sha256": hashlib.sha256(review).hexdigest()},
            "policies": [
                {**common, "id": "robots", "url": "https://example.org/robots.txt", "purpose": "access_policy", "media_types": ["text/plain"], "normalization": "robots_text_v1", "normalized_sha256": capture._sha(capture.normalized_bytes(self.bodies["robots"], "robots_text_v1"))},
                {**common, "id": "rights", "url": "https://example.org/terms", "purpose": "rights_policy", "media_types": ["text/html"], "normalization": "html_policy_v1", "normalized_sha256": capture._sha(capture.normalized_bytes(self.bodies["rights"], "html_policy_v1"))},
            ],
            "documents": [{**common, "id": "document", "url": "https://example.org/document", "purpose": "source_document", "media_types": ["text/html"], "policy_ids": ["robots", "rights"], "required_text": ["Project Update"]}],
        }
        self.calls = []
        self.overrides = {}
        self.clock = "2026-09-07T03:00:00Z"
        sleep = patch.object(capture.time, "sleep")
        sleep.start(); self.addCleanup(sleep.stop)
        now = patch.object(capture, "_now", side_effect=lambda: self.clock)
        now.start(); self.addCleanup(now.stop)

    def transport(self, entry: dict, destination: Path, plan: dict) -> dict:
        self.calls.append(entry["id"])
        destination.write_bytes(self.bodies[entry["id"]])
        return {"http_code": 200, "curl_exit_code": 0, "content_type": entry["media_types"][0], "url_effective": entry["url"], "num_redirects": 0, "ssl_verify_result": 0, **self.overrides.get(entry["id"], {})}

    def run_capture(self, name: str = "run", prior: Path | None = None) -> tuple[Path, dict]:
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        root = self.root / name
        prior_ledger = prior / "last_successful_checks.json" if prior else None
        if prior is not None and not prior_ledger.exists():
            prior_ledger = prior / "source_checks.json"
        result = capture.capture_sources(
            self.plan_path, root, transport=self.transport,
            prior_checks=prior_ledger, prior_root=prior,
        )
        return root, result

    def test_first_observation_and_no_network_replay(self) -> None:
        root, report = self.run_capture()
        self.assertEqual(["robots", "rights", "document"], self.calls)
        self.assertEqual("first_observation_requires_review", report["documents"][0]["status"])
        self.assertFalse(report["absence_inference_allowed"])
        with patch.object(capture, "curl_fetch", side_effect=AssertionError("network")):
            self.assertEqual(report, capture.validate_capture(root))
        self.assertFalse(json.loads((root / "run.json").read_bytes())["claim_acceptance"])

    def test_changed_policy_link_or_robots_blocks_source(self) -> None:
        for changed in ("rights", "robots"):
            original = copy.deepcopy(self.bodies)
            if changed == "rights":
                self.bodies[changed] = self.bodies[changed].replace(b'/terms', b'/new-terms')
            else:
                self.bodies[changed] += b"Disallow: /document\n"
            self.calls.clear()
            _, report = self.run_capture(changed)
            self.assertEqual(["robots", "rights"], self.calls)
            self.assertEqual("not_attempted_policy_blocked", report["documents"][0]["status"])
            self.assertTrue(report["attention_required"])
            self.bodies = original

    def test_redirected_policy_retains_failure_and_never_follows_it(self) -> None:
        self.overrides["rights"] = {"http_code": 302}
        root, report = self.run_capture()
        self.assertEqual(["robots", "rights"], self.calls)
        ledger = json.loads((root / "source_checks.json").read_bytes())
        self.assertEqual("failed", ledger["attempts"][1]["outcome"])
        self.assertIsNotNone(ledger["attempts"][1]["sha256"])
        self.assertEqual("not_attempted_policy_blocked", report["documents"][0]["status"])

    def test_unchanged_markup_and_visible_text_changes_are_distinguished(self) -> None:
        prior, _ = self.run_capture("prior")
        original = self.bodies["document"]
        for name, body, status in (
            ("same", original, "unchanged"),
            ("markup", original.replace(b"<p>", b'<p class="new">'), "raw_bytes_only"),
            ("changed", original.replace(b"announced", b"groundbreaking"), "visible_text_changed_requires_review"),
            ("challenge", b"<h1>Just a moment</h1>", "document_identity_requires_review"),
        ):
            self.bodies["document"] = body
            _, report = self.run_capture(name, prior)
            self.assertEqual(status, report["documents"][0]["status"])
            self.assertIsNotNone(report["documents"][0]["prior_success_at"])

    def test_document_failure_retains_partial_body_and_previous_success(self) -> None:
        prior, old = self.run_capture("prior")
        self.overrides["document"] = {"http_code": None, "curl_exit_code": 28, "transport_error": "timeout"}
        self.bodies["document"] = b"partial bytes"
        root, report = self.run_capture("retry", prior)
        self.assertEqual("failed_check", report["documents"][0]["status"])
        self.assertEqual(old["documents"][0]["current_sha256"], report["documents"][0]["prior_sha256"])
        self.assertEqual(b"partial bytes", (root / "responses/document.body").read_bytes())
        next_root, next_report = self.run_capture("retry-again", root)
        self.assertEqual(old["documents"][0]["current_sha256"], next_report["documents"][0]["prior_sha256"])
        capture.validate_capture(next_root)

    def test_successful_challenge_does_not_replace_last_eligible_document(self) -> None:
        prior, old = self.run_capture("prior")
        original = self.bodies["document"]
        self.bodies["document"] = b"<h1>Just a moment</h1>"
        blocked, report = self.run_capture("challenge", prior)
        self.assertEqual("document_identity_requires_review", report["documents"][0]["status"])
        history = json.loads((blocked / "last_successful_checks.json").read_bytes())
        self.assertEqual(old["documents"][0]["current_sha256"], history["attempts"][0]["sha256"])
        self.bodies["document"] = original
        _, report = self.run_capture("recovered", blocked)
        self.assertEqual("unchanged", report["documents"][0]["status"])

    def test_expiry_mid_run_keeps_receipts_and_stops_network(self) -> None:
        real_transport = self.transport

        def expire(entry: dict, destination: Path, plan: dict) -> dict:
            result = real_transport(entry, destination, plan)
            self.clock = "2027-01-01T00:00:00Z"
            return result

        self.transport = expire
        with self.assertRaisesRegex(ValueError, "expired during"):
            self.run_capture()
        self.assertEqual(["robots"], self.calls)
        self.assertTrue((self.root / "run/responses/robots.attempt.json").is_file())
        self.assertFalse((self.root / "run/manifest.json").exists())

    def test_expired_plan_existing_output_and_future_prior_reject_before_network(self) -> None:
        prior, _ = self.run_capture("prior")
        self.calls.clear()
        with self.assertRaises(FileExistsError):
            self.run_capture("prior")
        self.assertEqual([], self.calls)
        self.clock = "2026-09-06T03:00:00Z"
        with self.assertRaisesRegex(ValueError, "future"):
            self.run_capture("future-prior", prior)
        self.assertFalse((self.root / "future-prior").exists())
        self.clock = "2027-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "validity"):
            self.run_capture("expired")
        self.assertEqual([], self.calls)
        self.assertFalse((self.root / "expired").exists())

    def test_output_nested_in_prior_rejects_before_network(self) -> None:
        prior, _ = self.run_capture("prior")
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, "outside"):
            capture.capture_sources(self.plan_path, prior / "child", prior_checks=prior / "source_checks.json", prior_root=prior, transport=self.transport)
        self.assertEqual([], self.calls)
        self.assertFalse((prior / "child").exists())

    def test_body_and_results_tampering_reject(self) -> None:
        for name in ("responses/document.body", "responses/document.attempt.json", "results.json", "last_successful_checks.json"):
            root, _ = self.run_capture(name.replace("/", "-"))
            (root / name).write_bytes((root / name).read_bytes() + b" ")
            with self.assertRaises(ValueError):
                capture.validate_capture(root)

    def test_bad_media_type_and_encoding_preserve_previous_content(self) -> None:
        prior, old = self.run_capture("prior")
        for name, body, metadata, status in (
            ("wrong-mime", self.bodies["document"], {"content_type": "application/json"}, "unexpected_media_type"),
            ("bad-encoding", b"<h1>Project Update</h1>\xff", {}, "unsupported_encoding"),
            ("empty", b"", {}, "empty_or_oversize_body"),
        ):
            self.bodies["document"] = body
            self.overrides["document"] = metadata
            root, report = self.run_capture(name, prior)
            self.assertEqual(status, report["documents"][0]["status"])
            self.assertTrue(report["attention_required"])
            self.assertFalse(report["documents"][0]["review_required"])
            history = json.loads((root / "last_successful_checks.json").read_bytes())
            self.assertEqual(old["documents"][0]["current_sha256"], history["attempts"][0]["sha256"])

    def test_unbound_review_and_cross_origin_policy_reject_before_network(self) -> None:
        self.plan["review_record"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "review record hash"):
            self.run_capture("bad-review")
        self.assertFalse((self.root / "bad-review").exists())
        self.plan["documents"][0]["url"] = "https://different.example.org/document"
        with self.assertRaisesRegex(ValueError, "same origin"):
            self.run_capture("bad-origin")
        self.assertEqual([], self.calls)

    def test_replay_rejects_unauthorized_attempt_even_with_valid_raw_bytes(self) -> None:
        root, _ = self.run_capture()
        ledger = json.loads((root / "source_checks.json").read_bytes())
        ledger["attempts"].append({**ledger["attempts"][-1], "id": "unplanned"})
        with self.assertRaisesRegex(ValueError, "outside approved plan"):
            capture.derive_results(self.plan, ledger, root)

    def test_replay_rejects_document_fetched_before_policy_completed(self) -> None:
        root, _ = self.run_capture()
        ledger = json.loads((root / "source_checks.json").read_bytes())
        ledger["attempts"][1]["finished_at"] = "2026-09-07T03:00:01Z"
        with self.assertRaisesRegex(ValueError, "predates its policy"):
            capture.derive_results(self.plan, ledger, root)

    def test_curl_invocation_disables_ambient_configuration_and_redirects(self) -> None:
        process = type("Process", (), {"stdout": b'{"http_code":200}', "stderr": b"", "returncode": 0})()
        with patch.object(capture.subprocess, "run", return_value=process) as run:
            capture.curl_fetch(self.plan["documents"][0], self.root / "body", self.plan)
        command = run.call_args.args[0]
        self.assertEqual(["curl", "--disable"], command[:2])
        self.assertIn("--globoff", command)
        self.assertEqual("=https", command[command.index("--proto") + 1])
        self.assertNotIn("--location", command)
        self.assertNotIn("--retry", command)
        self.assertNotIn("--cookie", command)
        self.assertNotIn("--insecure", command)

    def test_policy_v2_normalizes_only_rotating_contact_key(self) -> None:
        def encoded(destination: bytes, key: int) -> str:
            return "/cdn-cgi/l/email-protection#" + bytes([key, *(value ^ key for value in destination)]).hex()
        first = f'<p>Terms</p><a href="{encoded(b"contact@example.org", 12)}">Contact</a>'.encode()
        second = f'<p>Terms</p><a href="{encoded(b"contact@example.org", 53)}">Contact</a>'.encode()
        changed = f'<p>Terms</p><a href="{encoded(b"other@example.org", 53)}">Contact</a>'.encode()
        self.assertNotEqual(capture.normalized_bytes(first, "html_policy_v1"), capture.normalized_bytes(second, "html_policy_v1"))
        self.assertEqual(capture.normalized_bytes(first, "html_policy_v2"), capture.normalized_bytes(second, "html_policy_v2"))
        self.assertNotEqual(capture.normalized_bytes(first, "html_policy_v2"), capture.normalized_bytes(changed, "html_policy_v2"))
        self.assertNotEqual(capture.normalized_bytes(first, "html_policy_v2"), capture.normalized_bytes(first.replace(b"Terms", b"New terms"), "html_policy_v2"))


if __name__ == "__main__":
    unittest.main()
