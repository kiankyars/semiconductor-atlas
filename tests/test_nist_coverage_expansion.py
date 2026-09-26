from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from semiconductor_atlas import curated_capture as capture
from semiconductor_atlas import curated_coverage as coverage
from semiconductor_atlas import curated_poll as poll
from semiconductor_atlas import curated_review as review
from tests import test_curated_coverage as fixtures


class NISTCoverageRepositoryTests(unittest.TestCase):
    def test_reviewed_plans_preserve_exact_urls_and_nist_only_access(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        specifications = (
            ("tsmc_arizona_nist_v1.json", "tsmc:fab21-arizona", "TSMC",
             "https://www.nist.gov/chips/tsmc-arizona-phoenix", ("Phoenix", "Fab 1")),
            ("samsung_taylor_nist_v1.json", "samsung:taylor-leading-edge-project", "Samsung",
             "https://www.nist.gov/chips/samsung-electronics-texas-austin", ("Taylor", "Austin")),
        )
        for name, facility, company, url, scope_markers in specifications:
            with self.subTest(plan=name):
                plan, _ = capture.load_plan(repository / "acquisition_plans" / name)
                self.assertEqual(facility, plan["checked_facility_key"])
                self.assertEqual("approved_exact_urls", plan["decision"])
                self.assertEqual([url], [document["url"] for document in plan["documents"]])
                policies = {policy["url"]: policy for policy in plan["policies"]}
                self.assertEqual({"https://www.nist.gov/robots.txt",
                                  "https://www.nist.gov/copyrights-disclaimers"}, set(policies))
                self.assertEqual("0fed8806709b6ff2716921723b5077d1c19d152f760db983af60988cae0031dd",
                                 policies["https://www.nist.gov/robots.txt"]["normalized_sha256"])
                self.assertEqual("html_policy_v2", policies["https://www.nist.gov/copyrights-disclaimers"]["normalization"])
                self.assertEqual("1130ffcd18cbe4ab88cf0eb2b079e046d9576efb57a5cfbc75cc23472fa48c46",
                                 policies["https://www.nist.gov/copyrights-disclaimers"]["normalized_sha256"])
                document = plan["documents"][0]
                self.assertEqual(company, document["company"])
                self.assertEqual("US", document["country_code"])
                self.assertEqual("nist-chips-award", document["source_family"])
                for marker in scope_markers:
                    self.assertIn(marker.casefold(), document["scope"].casefold())
                self.assertGreaterEqual(len(document["required_text"]), 2)
                review_path = repository / plan["review_record"]["path"]
                self.assertEqual(plan["review_record"]["sha256"], capture._sha(review_path.read_bytes()))

    def test_catalog_enables_only_three_facilities_without_rewriting_v1(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        old = coverage.load_catalog(repository / "acquisition_plans/ai_critical_coverage_v1.json")
        new = coverage.load_catalog(repository / "acquisition_plans/ai_critical_coverage_v2.json")
        self.assertEqual(7, len(new["targets"]))
        self.assertEqual([0, 1, 5], [index for index, row in enumerate(new["targets"]) if row["plan"]])
        self.assertEqual([5], [index for index, row in enumerate(old["targets"]) if row["plan"]])
        self.assertEqual(old["catalog"]["baseline"], new["catalog"]["baseline"])
        self.assertEqual(old["catalog"]["facilities"][5], new["catalog"]["facilities"][5])
        for index in (2, 3, 4, 6):
            self.assertTrue(new["catalog"]["facilities"][index]["unmonitored_reason"])
        config = poll.load_config(repository / "acquisition_plans/ai_critical_poll_v2.json")
        self.assertEqual(new["catalog_sha256"], config["catalog"]["catalog_sha256"])


class NISTCoverageIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.CuratedCoverageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.collector = self.fixture.capture
        self.fixture.catalog["format"] = coverage.CATALOG_FORMAT_V2
        self.plans = {}
        for index, company, facility, slug, scope, markers in (
            (0, "TSMC", "tsmc:fab21-arizona", "tsmc-arizona-phoenix",
             "Phoenix three-fab project; only Fab 1 can support the named baseline facility.",
             ["TSMC Arizona", "Phoenix", "Fab 1"]),
            (1, "Samsung", "samsung:taylor-leading-edge-project", "samsung-electronics-texas-austin",
             "Texas multi-site award. Taylor two-fab project only; exclude Austin and the R&D fab.",
             ["Samsung Electronics", "Taylor", "two leading-edge logic"]),
        ):
            plan = copy.deepcopy(self.collector.plan)
            plan.update(plan_id=f"fixture-{company.lower()}", checked_facility_key=facility)
            for entry in plan["policies"] + plan["documents"]:
                entry.update(company=company, source_family="nist-chips-award", scope=scope)
                entry["url"] = entry["url"].replace("example.org", "www.nist.gov")
            plan["documents"][0].update(url=f"https://www.nist.gov/chips/{slug}", required_text=markers)
            path = self.root / "plans" / f"{company.lower()}.json"
            path.write_text(json.dumps(plan))
            self.plans[company] = (path, plan)
            self.fixture.catalog["facilities"][index].update(
                plan={"path": path.relative_to(self.root).as_posix(), "sha256": capture._sha(path.read_bytes())},
                unmonitored_reason=None,
            )
        self.fixture.write_catalog()
        self.baseline_path = self.root / self.fixture.catalog["baseline"]["path"]
        self.baseline_bytes = self.baseline_path.read_bytes()

    def acquire(self, company: str, name: str) -> tuple[Path, dict]:
        path, plan = self.plans[company]
        self.collector.bodies["document"] = (
            "<h1>" + " | ".join(plan["documents"][0]["required_text"]) + "</h1><p>Project announcement.</p>"
        ).encode()
        output = self.root / name
        result = capture.capture_sources(path, output, transport=self.collector.transport)
        review.import_capture(self.fixture.queue, output)
        return output, result

    def test_report_names_project_scope_and_preserves_seven_facilities(self) -> None:
        self.acquire("TSMC", "tsmc")
        report = self.fixture.report()
        self.assertEqual("semiconductor-atlas-curated-coverage-report-v2", report["format"])
        self.assertEqual(7, report["summary"]["cohort_facility_count"])
        self.assertEqual(3, report["summary"]["configured_facility_count"])
        self.assertEqual(4, report["summary"]["unmonitored_facility_count"])
        for index, company in ((0, "TSMC"), (1, "Samsung")):
            document = report["facilities"][index]["documents"][0]
            self.assertEqual(self.plans[company][1]["documents"][0]["scope"], document["scope"])
        self.assertEqual("fresh", report["facilities"][0]["documents"][0]["freshness"])
        self.assertEqual("never_observed", report["facilities"][1]["documents"][0]["freshness"])
        self.assertEqual("never_observed", report["facilities"][5]["documents"][0]["freshness"])

    def test_v1_report_shape_remains_unchanged(self) -> None:
        self.acquire("TSMC", "tsmc")
        newer = self.fixture.report()
        self.fixture.catalog["format"] = coverage.CATALOG_FORMAT
        self.fixture.write_catalog()
        older = self.fixture.report()
        self.assertEqual("semiconductor-atlas-curated-coverage-report-v1", older["format"])
        self.assertNotEqual(newer["catalog_sha256"], older["catalog_sha256"])
        expected = copy.deepcopy(newer)
        expected.update(format=older["format"], catalog_sha256=older["catalog_sha256"])
        for facility in expected["facilities"]:
            for document in facility["documents"]:
                del document["scope"]
        self.assertEqual(expected, older)

    def test_old_plan_hash_cannot_refresh_active_plan_or_another_facility(self) -> None:
        self.acquire("TSMC", "tsmc-old-plan")
        path, plan = self.plans["TSMC"]
        plan["notes"] = "New reviewed acquisition boundary, requiring its own observations."
        path.write_text(json.dumps(plan))
        self.fixture.catalog["facilities"][0]["plan"]["sha256"] = capture._sha(path.read_bytes())
        self.fixture.write_catalog()
        self.acquire("Samsung", "samsung")
        report = self.fixture.report()
        self.assertEqual(2, report["queue_verified_capture_count"])
        self.assertEqual("never_observed", report["facilities"][0]["documents"][0]["freshness"])
        self.assertEqual("fresh", report["facilities"][1]["documents"][0]["freshness"])
        self.assertEqual(1, report["summary"]["fresh_document_count"])
        self.acquire("TSMC", "tsmc-current-plan")
        self.assertEqual(2, self.fixture.report()["summary"]["fresh_document_count"])

    def test_project_document_candidate_does_not_accept_manufacturing_claims(self) -> None:
        root, result = self.acquire("Samsung", "samsung")
        queue = review.queue_report(self.fixture.queue)
        self.assertEqual("first_observation_requires_review", result["documents"][0]["status"])
        self.assertEqual(1, queue["pending_count"])
        candidate = queue["candidates"][0]
        self.assertEqual("samsung:taylor-leading-edge-project", candidate["facility_key"])
        self.assertEqual("pending", candidate["status"])
        with self.assertRaises(ValueError):
            review.record_decision(self.fixture.queue, candidate["id"], action="accept", reviewer="fixture",
                                   reason="An exact-page check cannot accept manufacturing claims.",
                                   expected_event_id=candidate["last_event_id"])
        for report in (queue, self.fixture.report(), json.loads((root / "run.json").read_bytes())):
            self.assertFalse(report["claim_acceptance"])
        self.assertFalse(queue["delivery_eligible"])
        self.assertEqual(self.baseline_bytes, self.baseline_path.read_bytes())

    def test_austin_only_body_fails_taylor_identity_without_refresh(self) -> None:
        path, _ = self.plans["Samsung"]
        self.collector.bodies["document"] = b"<h1>Samsung Electronics</h1><p>Austin factory update.</p>"
        root = self.root / "austin-only"
        result = capture.capture_sources(path, root, transport=self.collector.transport)
        review.import_capture(self.fixture.queue, root)
        self.assertEqual("document_identity_requires_review", result["documents"][0]["status"])
        report = self.fixture.report()
        self.assertEqual("never_observed", report["facilities"][1]["documents"][0]["freshness"])
        self.assertEqual("requires_attention", report["facilities"][1]["documents"][0]["check_health"])
        self.assertEqual(0, report["summary"]["pending_count"])
        self.assertEqual(0, report["summary"]["fresh_document_count"])

    def test_changed_nist_policy_blocks_both_new_plans_before_documents(self) -> None:
        self.collector.bodies["robots"] += b"Disallow: /chips/\n"
        for company in ("TSMC", "Samsung"):
            self.collector.calls.clear()
            _, result = self.acquire(company, f"blocked-{company.lower()}")
            self.assertEqual(["robots", "rights"], self.collector.calls)
            self.assertEqual("not_attempted_policy_blocked", result["documents"][0]["status"])
        report = self.fixture.report()
        self.assertEqual(0, report["summary"]["fresh_document_count"])
        self.assertEqual(0, report["summary"]["pending_count"])
        for index in (0, 1):
            document = report["facilities"][index]["documents"][0]
            self.assertEqual(0, document["attempt_count"])
            self.assertEqual(1, document["policy_blocked_count"])


if __name__ == "__main__":
    unittest.main()
