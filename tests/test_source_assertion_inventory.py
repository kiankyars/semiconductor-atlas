from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from semiconductor_atlas import source_assertion_inventory as assertions
from semiconductor_atlas import curated_poll as poll
from semiconductor_atlas.ai_critical_changes import _pretty_bytes
from scripts import inventory_source_assertions as cli
from tests import test_curated_observation_population as fixtures
from tests.test_source_assertion_detector import AMKOR_TITLE, amkor_html


URL = "https://example.org/document"


class SourceAssertionInventoryTests(unittest.TestCase):
    """Real collection/census fixtures; a scripted parser isolates accounting tests."""

    def setUp(self):
        self.fixture = fixtures.CuratedObservationPopulationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.poll = self.fixture.root, self.fixture.fixture
        self.capture = self.poll.capture
        self.frozen_path = self.fixture.frozen_path
        self.output = self.root / "assertion-inventory.json"
        supported = patch.object(assertions.detector, "SUPPORTED_URLS", (URL,))
        supported.start()
        self.addCleanup(supported.stop)
        parser = patch.object(assertions.detector, "analyze", side_effect=self.parsed)
        self.analyze = parser.start()
        self.addCleanup(parser.stop)

    @staticmethod
    def parsed(url, before, after):
        return {"result": "extraction_only" if before is None else
                "no_candidate" if before == after else "revision_candidate",
                "reason": "scripted_fixture_accounting_only",
                "extractions": {"before": {"status": "absent" if before is None else "extracted"},
                                "after": {"status": "extracted"}}}

    def freeze(self):
        self.frozen = self.fixture.freeze()
        self.frozen_path.write_bytes(_pretty_bytes(self.frozen))

    def paired(self):
        self.poll.tick()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.freeze()

    def inventory(self):
        return assertions.inventory(self.frozen_path, reference_root=self.root)

    def write(self, output=None):
        return assertions.write_inventory(self.frozen_path, output or self.output, reference_root=self.root)

    def test_complete_denominator_first_vs_pair_repeat_and_no_mutation(self):
        self.paired()
        prior = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        calls = list(self.capture.calls)
        report = self.inventory()
        self.assertEqual(report, self.inventory())
        self.assertEqual(2, report["counts"]["document_opportunities"])
        self.assertEqual(1, report["counts"]["collector_comparable_opportunities"])
        self.assertEqual({"extraction_only": 1, "no_candidate": 1}, report["counts"]["result_counts"])
        self.assertEqual(2, report["counts"]["after_extractions"])
        self.assertTrue(all(value is False for value in report["boundaries"].values()))
        self.assertTrue(all(value is None for value in report["unscored_metrics"].values()))
        self.assertEqual(calls, self.capture.calls)
        self.assertTrue(all(p.read_bytes() == raw for p, raw in prior.items()))

    def test_unsupported_routes_remain_in_population(self):
        self.paired()
        with patch.object(assertions.detector, "SUPPORTED_URLS", ()):
            report = self.inventory()
        self.analyze.assert_not_called()
        self.assertEqual({"unsupported_exact_url": 2}, report["counts"]["result_counts"])
        self.assertEqual(1, report["counts"]["collector_comparable_opportunities"])
        self.assertEqual(0, report["counts"]["supported_route_opportunities"])

    def test_failed_response_body_never_becomes_extraction(self):
        self.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        self.poll.tick()
        self.freeze()
        report = self.inventory()
        self.analyze.assert_not_called()
        self.assertEqual({"uncomparable_source_unavailable": 1}, report["counts"]["result_counts"])
        self.assertIsNone(report["cases"][0]["after"])

    def test_blocked_policy_is_not_a_no_change_result(self):
        self.capture.bodies["rights"] += b"<p>Changed policy</p>"
        self.poll.tick()
        self.freeze()
        row = self.inventory()["cases"][0]
        self.assertEqual("not_attempted_policy_blocked", row["source_status"])
        self.assertEqual("uncomparable_source_unavailable", row["result"])
        self.analyze.assert_not_called()

    def test_incomplete_opportunity_is_retained(self):
        with patch.object(poll, "capture_sources", side_effect=ValueError("fixture interruption")):
            self.poll.tick()
        self.freeze()
        row = self.inventory()["cases"][0]
        self.assertEqual("incomplete_document", row["kind"])
        self.assertEqual("uncomparable_source_unavailable", row["result"])
        self.assertIsNotNone(row["intent_started_at"])
        self.analyze.assert_not_called()

    def test_repeated_checks_are_not_deduplicated_events(self):
        self.poll.tick()
        self.poll.tick("2026-09-07T04:00:00Z")
        self.poll.tick("2026-09-07T05:00:00Z")
        self.freeze()
        report = self.inventory()
        self.assertEqual(3, len(report["cases"]))
        self.assertEqual(1, len(report["exact_pair_repetition"]))
        self.assertEqual(2, report["exact_pair_repetition"][0]["actual_checks"])

    def test_first_observation_cannot_receive_paired_result(self):
        self.poll.tick()
        self.freeze()
        self.analyze.side_effect = lambda *args: {"result": "revision_candidate"}
        with self.assertRaisesRegex(ValueError, "uncomparable source"):
            self.write()
        self.assertFalse(self.output.exists())

    def test_tampered_retained_body_rejects_before_analysis(self):
        self.paired()
        path = self.root / self.frozen["observations"][0]["body_path"]
        path.write_bytes(path.read_bytes() + b"tampered")
        with self.assertRaises(ValueError):
            self.write()
        self.analyze.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_code_drift_during_analysis_rejects(self):
        self.paired()
        real = assertions._code_hashes
        code = real()
        changed = {**code, "fixture-code-drift": "0" * 64}
        with patch.object(assertions, "_code_hashes", side_effect=[code, changed]):
            with self.assertRaisesRegex(ValueError, "code changed"):
                self.write()
        self.assertFalse(self.output.exists())

    def test_mid_analysis_source_change_rejects(self):
        self.paired()
        path = self.root / self.frozen["observations"][0]["body_path"]
        def mutate(*args):
            result = self.parsed(*args)
            path.write_bytes(path.read_bytes() + b"changed after read")
            return result
        self.analyze.side_effect = mutate
        with self.assertRaises(ValueError):
            self.write()
        self.assertFalse(self.output.exists())

    def test_mid_analysis_queue_prefix_change_rejects(self):
        self.paired()
        real = assertions.population.queue.export_events
        changed = False
        def read_queue(*args, **kwargs):
            result = real(*args, **kwargs)
            return {**result, "events": []} if changed else result
        def mutate(*args):
            nonlocal changed
            changed = True
            return self.parsed(*args)
        self.analyze.side_effect = mutate
        with patch.object(assertions.population.queue, "export_events", side_effect=read_queue):
            with self.assertRaisesRegex(ValueError, "queue history changed"):
                self.write()
        self.assertFalse(self.output.exists())

    def test_queue_prefix_change_before_output_rejects(self):
        self.paired()
        report = self.inventory()
        real = assertions.population.queue.export_events
        def read_queue(*args, **kwargs):
            return {**real(*args, **kwargs), "events": []}
        with patch.object(assertions, "inventory", return_value=report):
            with patch.object(assertions.population.queue, "export_events", side_effect=read_queue):
                with self.assertRaisesRegex(ValueError, "queue history changed"):
                    self.write()
        self.assertFalse(self.output.exists())

    def test_genuine_later_queue_append_keeps_frozen_inventory_identical(self):
        self.paired()
        before = self.inventory()
        self.poll.tick("2026-09-07T08:00:00Z")
        self.assertEqual(before, self.inventory())

    def test_cli_writes_new_only_and_rebuild_is_byte_identical(self):
        self.paired()
        args = ["inventory", "--frozen", str(self.frozen_path), "--reference-root", str(self.root), "--output", str(self.output)]
        with patch("sys.argv", args), redirect_stdout(io.StringIO()):
            cli.main()
        raw = self.output.read_bytes()
        self.assertEqual(_pretty_bytes(self.inventory()), raw)
        with patch("sys.argv", args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main()
        self.assertEqual(raw, self.output.read_bytes())

    def test_public_api_accepts_string_paths(self):
        self.paired()
        self.assertEqual(str(self.output), assertions.write_inventory(str(self.frozen_path),
            str(self.output), reference_root=str(self.root))["output"])

    def test_frozen_change_during_output_protection_rejects(self):
        self.paired()
        real = assertions.population._json
        def mutate(path):
            result = real(path)
            if path == self.frozen_path:
                result["fixture_unverified_protection"] = True
            return result
        with patch.object(assertions, "inventory", return_value=self.inventory()):
            with patch.object(assertions.population, "_json", side_effect=mutate):
                with self.assertRaisesRegex(ValueError, "frozen census changed"):
                    self.write()
        self.assertFalse(self.output.exists())

    def test_cli_rejects_source_and_polling_output_paths(self):
        self.paired()
        for name in ("poll-captures/report.json", "poll-state/report.json"):
            target = self.root / name
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "retained source"):
                self.write(target)
            self.assertFalse(target.exists())

    def test_cli_rejects_symlink_output_parent(self):
        self.paired()
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.write(alias / "unsafe.json")
        self.assertFalse((self.root / "unsafe.json").exists())

    def test_size_limit_does_not_truncate_population(self):
        self.paired()
        with patch.object(assertions.population, "MAX_BYTES", len(self.frozen_path.read_bytes()) + 1):
            self.analyze.side_effect = lambda *args: {**self.parsed(*args), "large_fixture": "X" * len(self.frozen_path.read_bytes())}
            with self.assertRaisesRegex(ValueError, "20 MB"):
                self.write()
        self.assertFalse(self.output.exists())


class SourceAssertionIntegrationTests(unittest.TestCase):
    """Synthetic transport, real collector, frozen census and assertion parser."""

    def setUp(self):
        self.fixture = fixtures.CuratedObservationPopulationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.poll = self.fixture.root, self.fixture.fixture
        self.capture = self.poll.capture
        self.capture.plan["documents"][0].update(
            url=assertions.detector.AMKOR_GROUNDBREAKING, required_text=[AMKOR_TITLE])
        self.capture.plan["policies"][0]["url"] = "https://amkor.com/robots.txt"
        self.capture.plan["policies"][1]["url"] = "https://amkor.com/terms"
        self.capture.bodies["document"] = amkor_html()
        self.poll.repin_plan()

    def inventory(self):
        frozen = self.fixture.freeze()
        self.fixture.frozen_path.write_bytes(_pretty_bytes(frozen))
        return assertions.inventory(self.fixture.frozen_path, reference_root=self.root)

    def test_real_parser_binds_first_and_changed_source_bytes(self):
        self.poll.tick()
        self.capture.bodies["document"] = amkor_html(progress="continues")
        self.poll.tick("2026-09-07T04:00:00Z")
        report = self.inventory()
        self.assertEqual({"extraction_only": 1, "revision_candidate": 1}, report["counts"]["result_counts"])
        changed = next(row for row in report["cases"] if row["result"] == "revision_candidate")
        self.assertNotEqual(changed["before"]["body_sha256"], changed["after"]["body_sha256"])
        self.assertEqual(changed["before"]["body_sha256"], changed["analysis"]["body_sha256"]["before"])
        self.assertEqual(changed["after"]["body_sha256"], changed["analysis"]["body_sha256"]["after"])
        self.assertEqual(1, sum(row["status"] == "source_assertion_literal_changed" for row in changed["analysis"]["assertions"]))
        self.assertTrue(all(value is False for value in changed["analysis"]["boundaries"].values()))
        self.assertTrue(all(value is None for value in report["unscored_metrics"].values()))

    def test_unparsed_equal_bodies_remain_abstentions_in_real_inventory(self):
        self.capture.bodies["document"] = amkor_html(extra="<p>Completion: unknown.</p>")
        self.poll.tick()
        self.poll.tick("2026-09-07T04:00:00Z")
        report = self.inventory()
        self.assertEqual(1, report["counts"]["collector_comparable_opportunities"])
        self.assertEqual({"abstain": 2}, report["counts"]["result_counts"])

    def test_failed_intermediate_body_does_not_replace_actual_predecessor(self):
        self.poll.tick()
        original = self.capture.bodies["document"]
        self.capture.bodies["document"] = amkor_html(progress="continues")
        self.capture.overrides["document"] = {"http_code": None, "curl_exit_code": 28}
        self.poll.tick("2026-09-07T04:00:00Z")
        self.capture.overrides.clear()
        self.capture.bodies["document"] = original
        self.poll.tick("2026-09-07T05:00:00Z")
        report = self.inventory()
        self.assertEqual({"extraction_only": 1, "no_candidate": 1, "uncomparable_source_unavailable": 1},
            report["counts"]["result_counts"])
        pair = next(row for row in report["cases"] if row["result"] == "no_candidate")
        self.assertEqual(pair["before"]["body_sha256"], pair["after"]["body_sha256"])


if __name__ == "__main__":
    unittest.main()
