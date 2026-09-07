from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from semiconductor_atlas import curated_capture as capture
from semiconductor_atlas import nist_discovery as discovery
from scripts import discover_nist_sources as cli
from tests.test_nist_discovery_parser import award, document, news, pager


class NISTDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "plans").mkdir()
        self.plan_path = self.root / "plans/discovery.json"
        review = b'{"review":"fixture; index links are not acquisition permission"}\n'
        (self.root / "review.json").write_bytes(review)
        repository = Path(__file__).resolve().parents[1]
        baseline = (repository / "baselines/ai_critical_manufacturing_v1.json").read_bytes()
        (self.root / "baseline.json").write_bytes(baseline)
        self.bodies = {
            "nist-robots": b"User-agent: *\nDisallow: /private/\n",
            "nist-rights": b'<main><h1>Copyrights</h1><p>Public information except marked materials.</p><a href="/terms">Terms</a></main>',
            "news-page-0": document(news() + pager()),
            "news-page-1": document(news(url="/news-events/news/2026/07/micron-us-announcement", title="Micron US announcement") + pager(1, None)),
            "awards-page-0": document(award() + pager(), kind="awards"),
            "awards-page-1": document(award(url="/chips/samsung-electronics-texas-taylor").replace("Intel Corporation (Arizona)", "Samsung Electronics (Texas)").replace("Chandler", "Taylor").replace(">AZ<", ">TX<") + pager(1, None), kind="awards"),
        }
        companies = json.loads(baseline)["companies"]
        self.plan = {
            "format": discovery.PLAN_FORMAT, "plan_id": "fixture-nist-discovery",
            "review_scope_id": "fixture-nist-index-review", "checked_source_id": "nist:chips-public-indexes",
            "reviewed_at": "2026-09-06T00:00:00Z", "expires_at": "2026-10-06T00:00:00Z",
            "review_record": {"path": "review.json", "sha256": capture._sha(review)},
            "baseline": {"path": "baseline.json", "sha256": capture._sha(baseline)},
            "company_aliases": [{"company": company, "aliases": [company]} for company in companies],
            "user_agent": "SemiconductorAtlasFixture/0.1", "minimum_interval_seconds": 1,
            "timeout_seconds": 2, "max_response_bytes": 10000, "max_pages_per_index": 4,
            "policies": [
                {"id": identifier, "purpose": purpose, "url": discovery.POLICIES[purpose],
                 "normalization": mode, "normalized_sha256": capture._sha(capture.normalized_bytes(self.bodies[identifier], mode))}
                for identifier, purpose, mode in (("nist-robots", "access_policy", "robots_text_v1"), ("nist-rights", "rights_policy", "html_policy_v2"))
            ],
            "indexes": [{"kind": "news", "url": discovery.ROOTS["news"], "required_text": ["CHIPS News & Releases"]},
                        {"kind": "awards", "url": discovery.ROOTS["awards"], "required_text": ["CHIPS Program Office Awards"]}],
            "notes": "Fixture index-only review; no linked document fetch or facility evidence acceptance.",
        }
        self.calls = []
        self.overrides = {}
        self.now = datetime(2026, 9, 7, 5, tzinfo=timezone.utc)
        for target, name, implementation in ((capture, "_now", self.clock), (discovery, "_now", self.clock), (discovery.time, "sleep", self.advance)):
            patched = patch.object(target, name, side_effect=implementation)
            patched.start()
            self.addCleanup(patched.stop)

    def clock(self) -> str:
        return self.now.isoformat().replace("+00:00", "Z")

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def transport(self, entry: dict, destination: Path, plan: dict) -> dict:
        self.calls.append((entry["id"], entry["url"], self.clock()))
        body = self.bodies[entry["id"]]
        if body is not None:
            destination.write_bytes(body)
        self.advance(0.02)
        return {"http_code": 200, "curl_exit_code": 0, "content_type": entry["media_types"][0],
                "url_effective": entry["url"], "num_redirects": 0, "ssl_verify_result": 0,
                **self.overrides.get(entry["id"], {})}

    def run_capture(self, name: str = "capture") -> tuple[Path, dict]:
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        root = self.root / name
        return root, discovery.capture_indexes(self.plan_path, root, repository_root=self.root, transport=self.transport)

    def call_ids(self) -> list[str]:
        return [row[0] for row in self.calls]

    def test_multi_page_capture_replays_offline_and_never_fetches_linked_documents(self) -> None:
        root, result = self.run_capture()
        self.assertEqual(["nist-robots", "nist-rights", "news-page-0", "news-page-1", "awards-page-0", "awards-page-1"], self.call_ids())
        self.assertEqual(6, result["request_count"])
        self.assertEqual(4, result["observed_entry_count"])
        self.assertEqual(4, result["company_matched_entry_count"])
        self.assertFalse(result["attention_required"])
        self.assertTrue(all(row["page_chain_complete"] for row in result["indexes"]))
        entries = [entry for index in result["indexes"] for entry in index["entries"]]
        self.assertTrue({entry["url"] for entry in entries}.isdisjoint({row[1] for row in self.calls}))
        self.assertTrue(all(not entry["facility_scope_verified"] and not entry["linked_document_acquired"] for entry in entries))
        self.assertEqual(["TSMC"], entries[0]["matched_companies"])
        self.assertEqual({"locality": "Taylor", "region": "TX"}, entries[-1]["source_native_scope"])
        self.assertEqual("2026-07-16T12:00:00Z", entries[0]["index_date"])
        for key in ("claim_acceptance", "publisher_complete", "facility_coverage_complete", "linked_document_acquisition_allowed", "absence_inference_allowed"):
            self.assertIs(result[key], False)
        ledger = json.loads((root / "source_checks.json").read_bytes())
        self.assertEqual("nist:chips-public-indexes", ledger["checked_source_id"])
        self.assertNotIn("checked_facility_key", ledger)
        self.assertTrue(all(capture._instant(b["started_at"]) - capture._instant(a["finished_at"]) >= timedelta(seconds=1)
                            for a, b in zip(ledger["attempts"], ledger["attempts"][1:])))
        with patch.object(discovery, "curl_fetch", side_effect=AssertionError("network during replay")), patch.object(capture, "curl_fetch", side_effect=AssertionError("network during replay")):
            self.assertEqual(result, discovery.validate_capture(root))

    def test_company_aliases_route_but_do_not_drop_unmatched_records_or_match_substrings(self) -> None:
        self.bodies["news-page-0"] = document(news(title="Global semiconductor update") + news(url="/news-events/news/2026/07/research-update", title="Research database and Intellection") + pager())
        _, result = self.run_capture()
        entries = result["indexes"][0]["entries"]
        self.assertEqual(3, len(entries))
        self.assertEqual([[], [], ["Micron"]], [row["matched_companies"] for row in entries])
        self.assertEqual(5, result["observed_entry_count"])
        self.assertEqual(3, result["company_matched_entry_count"])

    def test_policy_content_or_link_change_blocks_both_index_chains(self) -> None:
        original = copy.deepcopy(self.bodies)
        for identifier in ("nist-robots", "nist-rights"):
            with self.subTest(policy=identifier):
                self.bodies = copy.deepcopy(original)
                self.bodies[identifier] += b"Disallow: /chips/\n" if identifier == "nist-robots" else b'<a href="/new-license">New license</a>'
                self.calls.clear()
                root, result = self.run_capture(identifier)
                self.assertEqual(["nist-robots", "nist-rights"], self.call_ids())
                self.assertEqual(["policy_blocked", "policy_blocked"], [row["status"] for row in result["indexes"]])
                self.assertEqual(0, result["observed_entry_count"])
                self.assertTrue(result["attention_required"])
                self.assertIn("policy_changed_requires_review", [row["error"] for row in result["policies"]])
                self.assertEqual(result, discovery.validate_capture(root))

    def test_failed_missing_body_and_redirected_policies_stop_acquisition(self) -> None:
        original = self.bodies["nist-rights"]
        for name, metadata, body, error in (
            ("timeout", {"http_code": None, "curl_exit_code": 28, "transport_error": "timeout"}, None, "failed_check"),
            ("http-error", {"http_code": 403}, b"Forbidden", "failed_check"),
            ("redirect", {"url_effective": "https://www.nist.gov/new-terms", "num_redirects": 1}, original, "unapproved_redirect"),
        ):
            with self.subTest(name=name):
                self.calls.clear()
                self.bodies["nist-rights"] = body
                self.overrides["nist-rights"] = metadata
                root, result = self.run_capture(name)
                self.assertEqual(["nist-robots", "nist-rights"], self.call_ids())
                self.assertEqual(error, result["policies"][1]["error"])
                self.assertTrue(result["attention_required"])
                self.assertTrue((root / "responses/nist-rights.attempt.json").is_file())
                self.assertEqual(body is not None, (root / "responses/nist-rights.body").exists())

    def test_forged_success_without_tls_verification_stops_before_next_request(self) -> None:
        self.overrides["nist-robots"] = {"ssl_verify_result": 1}
        with self.assertRaisesRegex(ValueError, "TLS"):
            self.run_capture()
        self.assertEqual(["nist-robots"], self.call_ids())
        self.assertTrue((self.root / "capture/responses/nist-robots.attempt.json").is_file())

    def test_failed_later_page_preserves_partial_entries_but_not_complete_chain(self) -> None:
        self.bodies["news-page-1"] = b"partial news response"
        self.overrides["news-page-1"] = {"http_code": None, "curl_exit_code": 28, "transport_error": "timeout"}
        root, result = self.run_capture()
        self.assertEqual("failed_check", result["indexes"][0]["status"])
        self.assertFalse(result["indexes"][0]["page_chain_complete"])
        self.assertEqual(1, result["indexes"][0]["entry_count"])
        self.assertTrue(result["indexes"][1]["page_chain_complete"])
        self.assertTrue(result["attention_required"])
        self.assertEqual(b"partial news response", (root / "responses/news-page-1.body").read_bytes())
        self.assertEqual(result, discovery.validate_capture(root))

    def test_truncated_page_and_malformed_next_link_do_not_become_terminal_success(self) -> None:
        for name, body in (
            ("truncated", document(news().replace("</article>", ""))),
            ("offsite-pager", document(news() + pager().replace('href="?page=1"', 'href="https://evil.example/?page=1"'))),
            ("skipped-page", document(news() + pager(0, 2))),
        ):
            with self.subTest(name=name):
                self.calls.clear()
                self.bodies["news-page-0"] = body
                _, result = self.run_capture(name)
                self.assertNotIn("news-page-1", self.call_ids())
                self.assertTrue(result["indexes"][0]["status"].startswith("index_structure_requires_review:"))
                self.assertFalse(result["indexes"][0]["page_chain_complete"])
                self.assertEqual(0, result["indexes"][0]["entry_count"])
                self.assertTrue(result["attention_required"])

    def test_page_limit_preserves_observed_entries_and_reports_unfinished_chains(self) -> None:
        self.plan["max_pages_per_index"] = 1
        _, result = self.run_capture()
        self.assertEqual(["nist-robots", "nist-rights", "news-page-0", "awards-page-0"], self.call_ids())
        self.assertEqual(["page_limit_reached", "page_limit_reached"], [row["status"] for row in result["indexes"]])
        self.assertTrue(result["attention_required"])
        self.assertEqual(2, result["observed_entry_count"])
        self.assertTrue(all(index["pages"][0]["next_url"].endswith("?page=1") for index in result["indexes"]))

    def test_cross_page_duplicate_requires_review_and_does_not_follow_next_page(self) -> None:
        self.bodies["news-page-1"] = document(news() + pager(1, 2))
        _, result = self.run_capture()
        self.assertNotIn("news-page-2", self.call_ids())
        self.assertEqual("duplicate_entries_require_review", result["indexes"][0]["status"])
        self.assertFalse(result["indexes"][0]["page_chain_complete"])
        self.assertTrue(result["attention_required"])

    def test_wrong_media_identity_and_encoding_are_failed_discovery_not_empty_success(self) -> None:
        original = self.bodies["news-page-0"]
        for name, body, metadata, error in (
            ("media", original, {"content_type": "application/json"}, "unexpected_media_type"),
            ("identity", b"<h1>Just a moment</h1>", {}, "document_identity_requires_review"),
            ("encoding", original + b"\xff", {}, "unsupported_encoding"),
            ("empty", b"", {}, "empty_or_oversize_body"),
        ):
            with self.subTest(name=name):
                self.calls.clear()
                self.bodies["news-page-0"] = body
                self.overrides["news-page-0"] = metadata
                _, result = self.run_capture(name)
                self.assertEqual(error, result["indexes"][0]["status"])
                self.assertTrue(result["attention_required"])
                self.assertNotIn("news-page-1", self.call_ids())

    def test_inventory_union_retains_rolled_off_links_and_entries_across_failures(self) -> None:
        first, first_result = self.run_capture("first")
        first_cutoff = self.clock()
        first_document = first_result["indexes"][0]["entries"][0]["url"]
        self.advance(3600)
        self.bodies["news-page-0"] = document(news(url="/news-events/news/2026/07/new-amkor-announcement", title="Amkor new announcement"))
        second, _ = self.run_capture("second")
        self.advance(3600)
        self.bodies["nist-rights"] += b"<p>Changed terms.</p>"
        failed, _ = self.run_capture("failure")
        inventory = discovery.discovery_inventory([failed, second, first], as_of=self.clock())
        self.assertEqual(3, len(inventory["captures"]))
        self.assertEqual(5, inventory["observed_document_count"])
        self.assertEqual(["policy_blocked", "policy_blocked"], [index["status"] for index in inventory["captures"][-1]["indexes"]])
        self.assertTrue(inventory["latest_capture_attention_required"])
        old = next(row for row in inventory["documents"] if row["url"] == first_document)
        self.assertEqual(1, len(old["observations"]))
        self.assertEqual(first_result["indexes"][0]["entries"][0]["observed_at"], old["last_observed_at"])
        self.assertEqual(hashlib.sha256(first_document.encode()).hexdigest(), old["id"])
        self.assertTrue(all(row["review_status"] == "requires_document_scope_and_access_review" for row in inventory["documents"]))
        self.assertFalse(inventory["absence_inference_allowed"])
        self.assertFalse(inventory["claim_acceptance"])
        before = discovery.discovery_inventory([first, second, failed], as_of=first_cutoff)
        self.assertEqual(discovery.discovery_inventory([first], as_of=first_cutoff), before)
        self.assertEqual(4, before["observed_document_count"])
        self.assertFalse(before["latest_capture_attention_required"])
        self.assertEqual(inventory, discovery.discovery_inventory([first, failed, second], as_of=self.clock()))

    def test_copied_packet_reproduces_byte_identical_canonical_inventory(self) -> None:
        original, _ = self.run_capture("original")
        copied = self.root / "restored-packet"
        shutil.copytree(original, copied)
        cutoff = self.clock()
        first = discovery._pretty_bytes(discovery.discovery_inventory([original], as_of=cutoff))
        restored = discovery._pretty_bytes(discovery.discovery_inventory([copied], as_of=cutoff))
        self.assertEqual(first, restored)
        self.assertNotIn(str(original).encode(), first)
        self.assertNotIn(str(copied).encode(), restored)

    def test_inventory_repeat_keeps_stable_document_ids_and_distinct_observations(self) -> None:
        first, _ = self.run_capture("first")
        self.advance(3600)
        second, _ = self.run_capture("second")
        inventory = discovery.discovery_inventory([first, second], as_of=self.clock())
        self.assertEqual(4, inventory["observed_document_count"])
        self.assertTrue(all(len(row["observations"]) == 2 for row in inventory["documents"]))
        self.assertTrue(all(row["first_observed_at"] != row["last_observed_at"] for row in inventory["documents"]))
        empty = discovery.discovery_inventory([first, second], as_of="2026-09-07T04:00:00Z")
        self.assertEqual([], empty["captures"])
        self.assertEqual([], empty["documents"])
        self.assertIsNone(empty["plan_sha256"])

    def test_inventory_rejects_duplicates_and_silent_plan_revision_mixing(self) -> None:
        first, _ = self.run_capture("first")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            discovery.discovery_inventory([first, first], as_of=self.clock())
        self.advance(3600)
        self.plan["plan_id"] = "other-reviewed-plan"
        second, _ = self.run_capture("second")
        with self.assertRaisesRegex(ValueError, "mix"):
            discovery.discovery_inventory([first, second], as_of=self.clock())

    def test_bound_review_baseline_and_full_ordered_cohort_are_required_before_fetch(self) -> None:
        original = copy.deepcopy(self.plan)
        for field in ("review_record", "baseline", "company_aliases", "reviewed_at"):
            with self.subTest(field=field):
                self.plan = copy.deepcopy(original)
                if field in {"review_record", "baseline"}:
                    self.plan[field]["sha256"] = "0" * 64
                elif field == "company_aliases":
                    self.plan[field] = list(reversed(self.plan[field]))
                else:
                    self.plan[field] = "2026-08-01T00:00:00Z"
                with self.assertRaises(ValueError):
                    self.run_capture(field)
                self.assertFalse((self.root / field).exists())
        self.assertEqual([], self.calls)

    def test_unapproved_index_root_archive_route_and_unbounded_limits_reject_before_fetch(self) -> None:
        original = copy.deepcopy(self.plan)
        changes = [
            ("index", "https://www.nist.gov/news-events/news-updates/archive/search"),
            ("index", discovery.ROOTS["news"] + "?page=1"),
            ("index", "https://evil.example/chips/chips-news-releases"),
            ("max_pages_per_index", 17), ("minimum_interval_seconds", 0),
            ("timeout_seconds", True), ("max_response_bytes", 10_000_001),
        ]
        for number, (field, value) in enumerate(changes):
            with self.subTest(field=field, value=value):
                self.plan = copy.deepcopy(original)
                if field == "index":
                    self.plan["indexes"][0]["url"] = value
                else:
                    self.plan[field] = value
                with self.assertRaises(ValueError):
                    self.run_capture(f"invalid-{number}")
        self.assertEqual([], self.calls)

    def test_expiry_mid_run_retains_receipt_and_stops_before_next_request(self) -> None:
        original = self.transport

        def expire(entry, destination, plan):
            result = original(entry, destination, plan)
            self.now = datetime(2026, 10, 6, tzinfo=timezone.utc)
            return result

        self.transport = expire
        with self.assertRaisesRegex(ValueError, "expired"):
            self.run_capture()
        self.assertEqual(["nist-robots"], self.call_ids())
        self.assertTrue((self.root / "capture/responses/nist-robots.attempt.json").is_file())
        self.assertFalse((self.root / "capture/manifest.json").exists())

    def test_existing_output_expired_plan_and_protected_input_overlap_do_not_fetch(self) -> None:
        root, _ = self.run_capture()
        original_manifest = (root / "manifest.json").read_bytes()
        self.calls.clear()
        with self.assertRaises(FileExistsError):
            self.run_capture()
        self.assertEqual(original_manifest, (root / "manifest.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.run_capture("plans/nested")
        self.now = datetime(2026, 10, 6, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "window"):
            self.run_capture("expired")
        self.assertEqual([], self.calls)
        self.assertFalse((self.root / "expired").exists())

    def test_capture_tampering_extra_files_and_symlinks_reject_offline(self) -> None:
        for number, filename in enumerate(("responses/news-page-0.body", "responses/news-page-0.attempt.json", "results.json", "run.json", "review.json", "baseline.json", "plan.json", "manifest.json")):
            with self.subTest(file=filename):
                root, _ = self.run_capture(f"tamper-{number}")
                if filename == "manifest.json":
                    manifest = json.loads((root / filename).read_bytes())
                    manifest["files"]["results.json"]["sha256"] = "0" * 64
                    (root / filename).write_text(json.dumps(manifest), encoding="utf-8")
                else:
                    (root / filename).write_bytes((root / filename).read_bytes() + b" ")
                with self.assertRaises(ValueError):
                    discovery.validate_capture(root)
        root, _ = self.run_capture("extra")
        (root / "extra.txt").write_text("unlisted", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inventory"):
            discovery.validate_capture(root)
        root, _ = self.run_capture("symlink")
        body = root / "responses/news-page-0.body"
        target = self.root / "body-copy"
        target.write_bytes(body.read_bytes())
        body.unlink()
        body.symlink_to(target)
        with self.assertRaises(ValueError):
            discovery.validate_capture(root)

    def test_replay_rejects_unplanned_or_reordered_requests(self) -> None:
        root, _ = self.run_capture()
        ledger = json.loads((root / "source_checks.json").read_bytes())
        extra = copy.deepcopy(ledger)
        extra["attempts"].append({**extra["attempts"][-1], "id": "linked-article", "url": "https://www.nist.gov/news-events/news/2026/07/chips-company-announcement"})
        with self.assertRaisesRegex(ValueError, "unplanned"):
            discovery.derive_results(self.plan, extra, root)
        reordered = copy.deepcopy(ledger)
        reordered["attempts"][2], reordered["attempts"][3] = reordered["attempts"][3], reordered["attempts"][2]
        with self.assertRaisesRegex(ValueError, "order"):
            discovery.derive_results(self.plan, reordered, root)

    def test_replay_checks_pacing_even_when_receipt_and_ledger_agree(self) -> None:
        root, _ = self.run_capture()
        ledger = json.loads((root / "source_checks.json").read_bytes())
        second = ledger["attempts"][1]
        second["started_at"] = (capture._instant(ledger["attempts"][0]["finished_at"]) + timedelta(seconds=0.5)).isoformat().replace("+00:00", "Z")
        (root / "source_checks.json").write_bytes(discovery._pretty_bytes(ledger))
        (root / "responses/nist-rights.attempt.json").write_bytes(discovery._pretty_bytes(second))
        with self.assertRaisesRegex(ValueError, "pacing"):
            discovery.validate_capture(root)

    def test_cli_capture_verify_and_inventory_use_explicit_retained_paths(self) -> None:
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        root = self.root / "cli-capture"
        output = io.StringIO()

        def configured_capture(plan, destination, **kwargs):
            return discovery.capture_indexes(plan, destination, transport=self.transport, **kwargs)

        with patch.object(cli, "capture_indexes", side_effect=configured_capture), patch("sys.argv", ["discover_nist_sources.py", "capture", "--plan", str(self.plan_path), "--output", str(root), "--repository-root", str(self.root)]), redirect_stdout(output):
            cli.main()
        captured = json.loads(output.getvalue())
        self.assertEqual(4, captured["observed_entry_count"])
        output = io.StringIO()
        with patch("sys.argv", ["discover_nist_sources.py", "verify", "--run", str(root)]), redirect_stdout(output):
            cli.main()
        self.assertEqual(captured, json.loads(output.getvalue()))
        target = self.root / "inventory.json"
        arguments = ["discover_nist_sources.py", "inventory", "--run", str(root), "--as-of", self.clock(), "--output", str(target)]
        output = io.StringIO()
        with patch("sys.argv", arguments), redirect_stdout(output):
            cli.main()
        result = json.loads(output.getvalue())
        self.assertEqual(discovery._pretty_bytes(result), target.read_bytes())
        self.assertEqual(4, result["observed_document_count"])
        original = target.read_bytes()
        with patch("sys.argv", arguments), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            cli.main()
        self.assertEqual(1, error.exception.code)
        self.assertEqual(original, target.read_bytes())

    def test_cli_verified_attention_and_invalid_capture_have_distinct_exit_codes(self) -> None:
        self.plan["max_pages_per_index"] = 1
        root, result = self.run_capture()
        output = io.StringIO()
        with patch("sys.argv", ["discover_nist_sources.py", "verify", "--run", str(root)]), redirect_stdout(output), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            cli.main()
        self.assertEqual(2, error.exception.code)
        self.assertEqual(result, json.loads(output.getvalue()))
        (root / "results.json").write_bytes(b"{}")
        with patch("sys.argv", ["discover_nist_sources.py", "verify", "--run", str(root)]), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            cli.main()
        self.assertEqual(1, error.exception.code)


if __name__ == "__main__":
    unittest.main()
