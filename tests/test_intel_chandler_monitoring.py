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
PLAN_PATH = REPOSITORY / "acquisition_plans/intel_fab52_chandler_v1.json"


class IntelChandlerRepositoryTests(unittest.TestCase):
    def test_plan_has_exact_unit_identity_and_both_reviewed_policies(self) -> None:
        plan, _ = capture.load_plan(PLAN_PATH)
        self.assertEqual("intel:fab52-chandler", plan["checked_facility_key"])
        self.assertEqual(1, len(plan["documents"]))
        document = plan["documents"][0]
        self.assertEqual("https://www.chandleraz.gov/business/economic-development/"
                         "key-industries-and-employers/advanced-manufacturing", document["url"])
        self.assertEqual(["Advanced Manufacturing", "Intel", "Fab 52", "Ocotillo", "Chandler"],
                         document["required_text"])
        self.assertEqual("municipal-economic-development", document["source_family"])
        self.assertIn("multiple companies", document["scope"])
        self.assertIn("cannot be allocated", document["scope"])
        self.assertEqual({"https://www.chandleraz.gov/robots.txt",
                          "https://www.chandleraz.gov/security-and-privacy-notice"},
                         {entry["url"] for entry in plan["policies"]})
        raw = (REPOSITORY / plan["review_record"]["path"]).read_bytes()
        self.assertEqual(plan["review_record"]["sha256"], capture._sha(raw))
        rights = json.loads(raw)["rights"]
        self.assertEqual("unknown_no_open_license_identified", rights["license_status"])
        self.assertFalse(rights["raw_redistribution"])
        self.assertFalse(rights["model_training"])

    def test_catalog_adds_only_intel_and_preserves_prior_poll_cadence(self) -> None:
        old = coverage.load_catalog(REPOSITORY / "acquisition_plans/ai_critical_coverage_v2.json")
        new = coverage.load_catalog(REPOSITORY / "acquisition_plans/ai_critical_coverage_v3.json")
        self.assertEqual([0, 1, 2, 5], [i for i, row in enumerate(new["targets"]) if row["plan"]])
        self.assertEqual(7, len(new["targets"]))
        for index in (0, 1, 3, 4, 5, 6):
            self.assertEqual(old["catalog"]["facilities"][index], new["catalog"]["facilities"][index])
        self.assertIsNone(old["catalog"]["facilities"][2]["plan"])
        self.assertEqual(old["catalog"]["baseline"], new["catalog"]["baseline"])
        self.assertEqual(old["catalog"]["format"], new["catalog"]["format"])
        previous = poll.load_config(REPOSITORY / "acquisition_plans/ai_critical_poll_v2.json")
        current = poll.load_config(REPOSITORY / "acquisition_plans/ai_critical_poll_v3.json")
        for key in ("queue_path", "capture_root", "state_root", "interval_seconds"):
            self.assertEqual(previous["config"][key], current["config"][key])
        self.assertEqual(new["catalog_sha256"], current["catalog"]["catalog_sha256"])

    def test_adjudication_preserves_unknown_dates_and_pinned_baseline(self) -> None:
        adjudication = json.loads((REPOSITORY / "review_plans/2026-09-07-intel-chandler-text-adjudication.json").read_bytes())
        self.assertEqual("dismiss_reviewed_text_version_without_baseline_revision", adjudication["decision"])
        for key in ("publication_date", "event_date"):
            self.assertIsNone(adjudication["assessment"][key])
        for key in ("claim_acceptance", "delivery_eligible", "baseline_modified"):
            self.assertFalse(adjudication[key])
        self.assertEqual(adjudication["baseline"]["sha256"],
                         capture._sha((REPOSITORY / adjudication["baseline"]["path"]).read_bytes()))
        self.assertEqual("98f97bd49cfcfb2d350742f422e43892c9b55c170ecbe73c93538bdbc1c38969",
                         capture._sha((REPOSITORY / "semiconductor_atlas/ai_critical.py").read_bytes()))


class IntelChandlerIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.CuratedCoverageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.collector = self.fixture.capture
        self.fixture.catalog["format"] = coverage.CATALOG_FORMAT_V2
        real, _ = capture.load_plan(PLAN_PATH)
        self.plan = copy.deepcopy(self.collector.plan)
        self.plan.update(plan_id="chandler-fixture", checked_facility_key=real["checked_facility_key"])
        for entry in self.plan["policies"] + self.plan["documents"]:
            entry.update(company="Intel", source_family="municipal-economic-development")
        self.plan["documents"][0].update(scope=real["documents"][0]["scope"],
                                         required_text=real["documents"][0]["required_text"])
        self.path = self.collector.plans / "intel.json"
        self.path.write_text(json.dumps(self.plan))
        self.fixture.catalog["facilities"][2].update(
            plan={"path": "plans/intel.json", "sha256": capture._sha(self.path.read_bytes())},
            unmonitored_reason=None,
        )
        self.fixture.write_catalog()
        self.body = b"<h1>Advanced Manufacturing</h1><p>Intel Fab 52, Ocotillo, Chandler: production described.</p>"
        self.collector.bodies["document"] = self.body
        self.baseline_path = self.fixture.root / self.fixture.catalog["baseline"]["path"]
        self.baseline_bytes = self.baseline_path.read_bytes()

    def acquire(self, name: str, prior: Path | None = None) -> tuple[Path, dict]:
        output = self.fixture.root / name
        result = capture.capture_sources(self.path, output, transport=self.collector.transport,
                                         prior_checks=prior / "last_successful_checks.json" if prior else None,
                                         prior_root=prior)
        review.import_capture(self.fixture.queue, output)
        return output, result

    def test_undated_text_is_review_work_not_a_manufacturing_update(self) -> None:
        output, result = self.acquire("first")
        self.assertEqual(result, capture.validate_capture(output))
        queue = review.queue_report(self.fixture.queue)
        self.assertEqual(1, queue["pending_count"])
        candidate = queue["candidates"][0]
        self.assertEqual("intel:fab52-chandler", candidate["facility_key"])
        with self.assertRaises(ValueError):
            review.record_decision(self.fixture.queue, candidate["id"], action="accept", reviewer="fixture",
                                   reason="A fresh undated source is not an accepted event.",
                                   expected_event_id=candidate["last_event_id"])
        report = self.fixture.report()
        self.assertEqual("fresh", report["facilities"][2]["documents"][0]["freshness"])
        self.assertEqual(self.plan["documents"][0]["scope"], report["facilities"][2]["documents"][0]["scope"])
        self.assertFalse(report["claim_acceptance"])
        self.assertFalse(queue["delivery_eligible"])
        self.assertEqual(self.baseline_bytes, self.baseline_path.read_bytes())

    def test_campus_only_page_cannot_refresh_fab52_coverage(self) -> None:
        for index, marker in enumerate(self.plan["documents"][0]["required_text"]):
            with self.subTest(marker=marker):
                self.collector.bodies["document"] = self.body.replace(marker.encode(), b"different scope")
                _, result = self.acquire(f"missing-{index}")
                self.assertEqual("document_identity_requires_review", result["documents"][0]["status"])
        report = self.fixture.report()
        self.assertEqual("never_observed", report["facilities"][2]["documents"][0]["freshness"])
        self.assertEqual(0, report["summary"]["pending_count"])

    def test_changed_policy_blocks_document_and_preserves_last_success(self) -> None:
        prior, _ = self.acquire("first")
        self.collector.calls.clear()
        self.collector.bodies["rights"] += b"<p>Changed policy</p>"
        output, result = self.acquire("blocked", prior)
        self.assertEqual(["robots", "rights"], self.collector.calls)
        self.assertEqual("not_attempted_policy_blocked", result["documents"][0]["status"])
        capture.validate_capture(output)
        document = self.fixture.report()["facilities"][2]["documents"][0]
        self.assertEqual("fresh", document["freshness"])
        self.assertEqual("requires_attention", document["check_health"])
        self.assertEqual(1, document["attempt_count"])

    def test_closed_version_does_not_suppress_later_whole_page_changes(self) -> None:
        prior, _ = self.acquire("first")
        candidate = review.queue_report(self.fixture.queue)["candidates"][0]
        review.record_decision(self.fixture.queue, candidate["id"], action="dismiss", reviewer="fixture",
                               reason="Reviewed text does not support a new facility event.",
                               expected_event_id=candidate["last_event_id"])
        unchanged, _ = self.acquire("unchanged", prior)
        self.assertEqual(0, review.queue_report(self.fixture.queue)["pending_count"])
        self.collector.clock = "2026-09-07T03:20:00Z"
        self.fixture.clock = "2026-09-07T03:25:00Z"
        self.collector.bodies["document"] += b"<p>An unrelated city's employer changed its office plan.</p>"
        _, result = self.acquire("changed", unchanged)
        self.assertEqual("visible_text_changed_requires_review", result["documents"][0]["status"])
        self.assertEqual(1, review.queue_report(self.fixture.queue)["pending_count"])
        self.assertEqual(self.baseline_bytes, self.baseline_path.read_bytes())
