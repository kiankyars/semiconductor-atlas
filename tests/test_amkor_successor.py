"""Regression contract for the reviewed Amkor vintage, without local raw inputs."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime
import unittest
from pathlib import Path

from semiconductor_atlas.ai_critical import load_baseline, materialize_baseline


ROOT = Path(__file__).resolve().parents[1]


class AmkorSuccessorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.prior_path = ROOT / "baselines/ai_critical_manufacturing_v1.json"
        cls.current_path = ROOT / "baselines/ai_critical_manufacturing_v1_amkor_2026-09-07.json"
        cls.prior = json.loads(cls.prior_path.read_bytes())
        cls.current = json.loads(cls.current_path.read_bytes())

    def test_other_six_facilities_and_release_producer_are_unchanged(self) -> None:
        for field in ("code_sha256", "builder_script_sha256", "atlas_template_sha256", "atlas_generator_sha256"):
            self.assertEqual(self.prior[field], self.current[field])
        prior = {row["company"]: row for row in self.prior["facilities"]}
        current = {row["company"]: row for row in self.current["facilities"]}
        self.assertEqual(set(prior), set(current))
        for company in set(prior) - {"Amkor"}:
            with self.subTest(company=company):
                self.assertEqual(prior[company], current[company])
        self.assertEqual(prior["Amkor"]["facility_key"], current["Amkor"]["facility_key"])

    def test_groundbreaking_not_current_construction_or_installed_capacity(self) -> None:
        amkor = next(row for row in self.current["facilities"] if row["company"] == "Amkor")
        self.assertEqual("site_preparation", amkor["lifecycle"]["state"])
        self.assertEqual("2025-10-06", amkor["lifecycle"]["as_of"])
        self.assertIn("does not establish the current maximum construction stage", amkor["lifecycle"]["statement"])
        self.assertEqual([], amkor["capacities"])
        self.assertEqual("planned", amkor["capabilities"][0]["readiness"])
        self.assertEqual(5, len(amkor["unknowns"]["capacity_bases"]))
        self.assertIsNone(amkor["geography"]["latitude"])
        self.assertIsNone(amkor["geography"]["longitude"])
        for metric in ("yield", "utilization", "qualification"):
            self.assertEqual("unknown", amkor["unknowns"][metric])
        old = next(row for row in self.prior["facilities"] if row["company"] == "Amkor")
        self.assertEqual(2, len(old["capacities"]))

    def test_clean_clone_materialization_and_minimal_new_excerpts(self) -> None:
        baseline = load_baseline(self.current_path, ROOT, verify_source_bytes=False)
        materialized = materialize_baseline(baseline)
        self.assertEqual(73, len(materialized["claims"]))
        self.assertFalse(any(row["value_kind"] == "capacity" for row in materialized["claims"]))
        for source in self.current["sources"]:
            if source["source_id"].startswith("amkor-"):
                self.assertEqual("metadata_and_excerpt_only", source["rights"]["redistribution"])
                excerpts = [row["excerpt"] for row in self.current["evidence"] if row["source_id"] == source["source_id"]]
                self.assertLessEqual(sum(len(excerpt.replace("...", "").split()) for excerpt in excerpts), 25)

    def test_review_precedes_acceptance_and_binds_inputs_and_check_ledger(self) -> None:
        review = json.loads((ROOT / "review_plans/2026-09-07-amkor-peoria-successor.json").read_bytes())
        self.assertEqual(self.current["recorded_at"], review["knowledge_cutoff"])
        self.assertLessEqual(datetime.fromisoformat(review["reviewed_at"]), datetime.fromisoformat(review["knowledge_cutoff"]))
        for side, path in (("prior", self.prior_path), ("successor", self.current_path)):
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), review[side]["input_sha256"])
        ledger_raw = (ROOT / "review_plans/2026-09-07-amkor-peoria-source-checks.json").read_bytes()
        self.assertEqual(hashlib.sha256(ledger_raw).hexdigest(), review["source_checks"]["sha256"])
        ledger = json.loads(ledger_raw)
        self.assertEqual(11, len(ledger["attempts"]))
        self.assertEqual(4, sum(row["outcome"] == "failed" for row in ledger["attempts"]))
