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


REPOSITORY = Path(__file__).resolve().parents[1]
PLAN_PATH = REPOSITORY / "acquisition_plans/micron_singapore_mti_v1.json"


class MicronMTIRepositoryTests(unittest.TestCase):
    def test_exact_government_speech_has_identity_and_bound_policy_review(self):
        plan, _ = capture.load_plan(PLAN_PATH)
        self.assertEqual("micron:singapore-hbm-packaging-project", plan["checked_facility_key"])
        self.assertEqual(1, len(plan["documents"]))
        document = plan["documents"][0]
        self.assertEqual("national-government-speech", document["source_family"])
        self.assertEqual("SG", document["country_code"])
        self.assertEqual(["Micron", "HBM Advanced Packaging facility", "groundbreaking", "8 January 2025", "Singapore"],
                         document["required_text"])
        self.assertTrue(document["url"].startswith("https://www.mti.gov.sg/newsroom/"))
        self.assertIn("January 2026 NAND-wafer-fab", document["scope"])
        self.assertEqual({"https://www.mti.gov.sg/robots.txt", "https://www.mti.gov.sg/terms-of-use/"},
                         {policy["url"] for policy in plan["policies"]})
        raw = (REPOSITORY / plan["review_record"]["path"]).read_bytes()
        self.assertEqual(plan["review_record"]["sha256"], capture._sha(raw))
        record = json.loads(raw)
        self.assertEqual("unknown_no_open_license_identified", record["rights"]["license_status"])
        self.assertFalse(record["rights"]["raw_redistribution"])
        self.assertFalse(record["rights"]["model_training"])
        self.assertFalse(record["acquisition_boundary"]["claim_acceptance"])
        self.assertFalse(record["acquisition_boundary"]["discovery"])
        self.assertEqual("2025-01-08", record["document"]["event_date"])

    def test_catalog_adds_only_micron_without_changing_existing_plans_or_cadence(self):
        old = coverage.load_catalog(REPOSITORY / "acquisition_plans/ai_critical_coverage_v3.json")
        new = coverage.load_catalog(REPOSITORY / "acquisition_plans/ai_critical_coverage_v4.json")
        self.assertEqual([0, 1, 2, 3, 5], [i for i, row in enumerate(new["targets"]) if row["plan"]])
        self.assertEqual(7, len(new["targets"]))
        for index in (0, 1, 2, 4, 5, 6):
            self.assertEqual(old["catalog"]["facilities"][index], new["catalog"]["facilities"][index])
        self.assertIsNone(old["catalog"]["facilities"][3]["plan"])
        self.assertEqual(old["catalog"]["baseline"], new["catalog"]["baseline"])
        self.assertEqual(old["catalog"]["format"], new["catalog"]["format"])
        previous = poll.load_config(REPOSITORY / "acquisition_plans/ai_critical_poll_v3.json")
        current = poll.load_config(REPOSITORY / "acquisition_plans/ai_critical_poll_v4.json")
        for key in ("queue_path", "capture_root", "state_root", "interval_seconds"):
            self.assertEqual(previous["config"][key], current["config"][key])
        self.assertEqual(new["catalog_sha256"], current["catalog"]["catalog_sha256"])

    def test_original_baseline_and_producer_remain_exact(self):
        for name, expected in {
            "semiconductor_atlas/ai_critical.py": "98f97bd49cfcfb2d350742f422e43892c9b55c170ecbe73c93538bdbc1c38969",
            "baselines/ai_critical_manufacturing_v1.json": "8eec7a20a9d2d900161efce778532756f9224520f2ec0056bb329c5118198958",
        }.items():
            self.assertEqual(expected, capture._sha((REPOSITORY / name).read_bytes()))

    def test_adjudication_keeps_historical_day_precision_and_no_revision_boundary(self):
        path = REPOSITORY / "review_plans/2026-09-07-micron-mti-text-adjudication.json"
        record = json.loads(path.read_bytes())
        self.assertEqual("dismiss_reviewed_text_version_without_baseline_revision", record["decision"])
        self.assertEqual("2025-01-08", record["assessment"]["publication_date"])
        self.assertEqual("2025-01-08", record["assessment"]["event_date"])
        self.assertFalse(record["independent_text_check"]["precise_publication_timestamp_metadata_found"])
        for field in ("claim_acceptance", "delivery_eligible", "baseline_modified"):
            self.assertFalse(record[field])
        self.assertEqual(record["baseline"]["sha256"],
                         capture._sha((REPOSITORY / record["baseline"]["path"]).read_bytes()))


class MicronMTIIsolationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CuratedCoverageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.collector = self.fixture.capture
        self.fixture.catalog["format"] = coverage.CATALOG_FORMAT_V2
        real, _ = capture.load_plan(PLAN_PATH)
        self.plan = copy.deepcopy(self.collector.plan)
        self.plan.update(plan_id="micron-mti-fixture", checked_facility_key=real["checked_facility_key"])
        for entry in self.plan["policies"] + self.plan["documents"]:
            entry.update(company="Micron", country_code="SG", source_family="national-government-speech")
        self.plan["documents"][0].update(scope=real["documents"][0]["scope"],
                                       required_text=real["documents"][0]["required_text"])
        self.path = self.collector.plans / "micron.json"
        self.path.write_text(json.dumps(self.plan))
        self.fixture.catalog["facilities"][3].update(
            plan={"path": "plans/micron.json", "sha256": capture._sha(self.path.read_bytes())},
            unmonitored_reason=None,
        )
        self.fixture.write_catalog()
        self.body = b"<h1>Micron HBM Advanced Packaging facility</h1><p>Singapore groundbreaking, 8 January 2025.</p>"
        self.collector.bodies["document"] = self.body
        self.baseline_path = self.fixture.root / self.fixture.catalog["baseline"]["path"]
        self.baseline_bytes = self.baseline_path.read_bytes()

    def acquire(self, name, prior=None):
        output = self.fixture.root / name
        result = capture.capture_sources(self.path, output, transport=self.collector.transport,
            prior_checks=prior / "last_successful_checks.json" if prior else None, prior_root=prior)
        review.import_capture(self.fixture.queue, output)
        return output, result

    def test_historical_speech_enters_text_review_not_canonical_current_state(self):
        output, result = self.acquire("first")
        self.assertEqual(result, capture.validate_capture(output))
        queue = review.queue_report(self.fixture.queue)
        self.assertEqual(1, queue["pending_count"])
        candidate = queue["candidates"][0]
        self.assertEqual("micron:singapore-hbm-packaging-project", candidate["facility_key"])
        with self.assertRaises(ValueError):
            review.record_decision(self.fixture.queue, candidate["id"], action="accept", reviewer="fixture",
                reason="A new read of a historical speech is not new production evidence.", expected_event_id=candidate["last_event_id"])
        report = self.fixture.report()
        document = report["facilities"][3]["documents"][0]
        self.assertEqual("fresh", document["freshness"])
        self.assertIn("January 8, 2025", document["scope"])
        self.assertFalse(report["claim_acceptance"])
        self.assertFalse(queue["delivery_eligible"])
        self.assertEqual(self.baseline_bytes, self.baseline_path.read_bytes())

    def test_nand_or_generic_micron_pages_do_not_refresh_hbm_document(self):
        for index, marker in enumerate(self.plan["documents"][0]["required_text"]):
            with self.subTest(marker=marker):
                self.collector.bodies["document"] = self.body.replace(marker.encode(), b"NAND wafer fab 2026")
                _, result = self.acquire(f"missing-{index}")
                self.assertEqual("document_identity_requires_review", result["documents"][0]["status"])
        document = self.fixture.report()["facilities"][3]["documents"][0]
        self.assertEqual("never_observed", document["freshness"])
        self.assertEqual(0, review.queue_report(self.fixture.queue)["pending_count"])

    def test_changed_policy_blocks_document_without_erasing_prior_observation(self):
        prior, _ = self.acquire("first")
        self.collector.calls.clear()
        self.collector.bodies["rights"] += b"<p>Changed policy requires a new review.</p>"
        output, result = self.acquire("blocked", prior)
        self.assertEqual(["robots", "rights"], self.collector.calls)
        self.assertEqual("not_attempted_policy_blocked", result["documents"][0]["status"])
        capture.validate_capture(output)
        document = self.fixture.report()["facilities"][3]["documents"][0]
        self.assertEqual("fresh", document["freshness"])
        self.assertEqual("requires_attention", document["check_health"])
        self.assertEqual(1, document["attempt_count"])

    def test_reviewed_historical_version_stays_closed_until_text_changes(self):
        prior, _ = self.acquire("first")
        candidate = review.queue_report(self.fixture.queue)["candidates"][0]
        review.record_decision(self.fixture.queue, candidate["id"], action="dismiss", reviewer="fixture",
            reason="Historical groundbreaking does not prove a new current milestone.", expected_event_id=candidate["last_event_id"])
        unchanged, _ = self.acquire("unchanged", prior)
        self.assertEqual(0, review.queue_report(self.fixture.queue)["pending_count"])
        self.collector.clock = "2026-09-07T03:20:00Z"
        self.fixture.clock = "2026-09-07T03:25:00Z"
        self.collector.bodies["document"] += b"<footer>Site-wide updated date changed.</footer>"
        _, result = self.acquire("changed", unchanged)
        self.assertEqual("visible_text_changed_requires_review", result["documents"][0]["status"])
        self.assertEqual(1, review.queue_report(self.fixture.queue)["pending_count"])
        self.assertEqual(self.baseline_bytes, self.baseline_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
