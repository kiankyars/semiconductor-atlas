from __future__ import annotations

import hashlib
import json
import unittest
from collections import Counter
from pathlib import Path

from semiconductor_atlas.eea_industrial_review import (
    read_eea_industrial_review_file,
    read_eea_industrial_review_queue_file,
)


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "review_plans"
REVIEW = PLAN / "2026-09-07-eea-industrial-v16-scope-admission-v1.json"
AUDIT = PLAN / "2026-09-07-eea-industrial-v16-admission-audit-v1.json"
CORRECTED_REVIEW = PLAN / "2026-09-07-eea-industrial-v16-scope-admission-v2.json"
CORRECTION = PLAN / "2026-09-07-eea-industrial-v16-admission-correction-v2.json"
RESEARCH_HASHES = {
    "2026-09-07-eea-industrial-v16-scope-research-v1.json":
        "916621ebaf3f670ef8812065c1cddb8b13823b476824fb75a47e7f23df8238b4",
    "2026-09-07-eea-industrial-v16-scope-research-v2.json":
        "b6ff8b365d012c5036aec5b5529ee4f844f49ee2ca2cf9368b79b99361f8cba8",
}


class EEAScopeAdmissionArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = read_eea_industrial_review_queue_file(
            PLAN / "2026-07-20-eea-industrial-v16-candidates.json"
        )
        self.review = read_eea_industrial_review_file(REVIEW, queue=self.queue)
        self.audit = json.loads(AUDIT.read_bytes())

    def test_complete_review_is_pinned_without_rewriting_research(self) -> None:
        self.assertEqual(108, len(self.review.decisions))
        self.assertEqual(
            "fa8b87cf136f2ed04f889b916b1d333a7c5aeebcd72d91fbbafd29c4e86ee9f9",
            self.review.raw_sha256,
        )
        self.assertEqual(self.review.raw_sha256, self.audit["review"]["sha256"])
        for name, digest in RESEARCH_HASHES.items():
            self.assertEqual(digest, hashlib.sha256((PLAN / name).read_bytes()).hexdigest())
        first = json.loads((PLAN / next(iter(RESEARCH_HASHES))).read_bytes())
        second = json.loads(
            (PLAN / "2026-09-07-eea-industrial-v16-scope-research-v2.json").read_bytes()
        )
        later = {row["candidate_id"]: row for row in second["records"]}
        self.assertEqual(31, len(first["records"]))
        for row in first["records"]:
            self.assertEqual(row, later[row["candidate_id"]])

    def test_acceptances_require_supported_audit_and_holds_stay_deferred(self) -> None:
        decisions = {row.candidate_id: row for row in self.review.decisions}
        self.assertEqual(
            Counter(accept_in_scope=15, defer=93),
            Counter(row.outcome for row in self.review.decisions),
        )
        rows = self.audit["records"]
        self.assertEqual(61, len(rows))
        self.assertEqual(61, len({row["candidate_id"] for row in rows}))
        supported = {
            row["candidate_id"] for row in rows
            if row["admission_readiness"] == "scope_supported"
        }
        self.assertEqual(
            supported,
            {key for key, row in decisions.items() if row.outcome == "accept_in_scope"},
        )
        for row in rows:
            self.assertEqual(row["final_outcome"], decisions[row["candidate_id"]].outcome)
            if row["original_proposed_outcome"] == "reject_out_of_scope":
                self.assertEqual("defer", row["final_outcome"])
        self.assertTrue(all(row.outcome == "defer" for key, row in decisions.items()
                            if key not in {item["candidate_id"] for item in rows}))

    def test_quoted_evidence_remains_bounded_and_replayable(self) -> None:
        corrected = read_eea_industrial_review_file(CORRECTED_REVIEW, queue=self.queue)
        for review in (self.review, corrected):
            words: Counter[str] = Counter()
            for decision in review.decisions:
                for evidence in decision.evidence:
                    words[evidence.url] += len(evidence.excerpt.split())
            self.assertLessEqual(max(words.values()), 25)
        for path in (REVIEW, AUDIT, CORRECTED_REVIEW, CORRECTION):
            raw = path.read_bytes()
            self.assertEqual(
                raw,
                (json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, indent=2)
                 + "\n").encode(),
            )

    def test_corrected_review_preserves_history_and_restores_newport_scope(self) -> None:
        corrected = read_eea_industrial_review_file(CORRECTED_REVIEW, queue=self.queue)
        correction = json.loads(CORRECTION.read_bytes())
        self.assertEqual(
            "c0f724bac16d64f9dfb30a04a6469e5263874b155bd6dc89146bad3daa5ae34b",
            corrected.raw_sha256,
        )
        for key in ("prior_review", "prior_audit", "review"):
            bound = correction[key]
            self.assertEqual(bound["sha256"], hashlib.sha256(
                (ROOT / bound["path"]).read_bytes()).hexdigest())
        changes = [(old, new) for old, new in
                   zip(self.review.decisions, corrected.decisions, strict=True)
                   if old != new]
        self.assertEqual(1, len(changes))
        old, new = changes[0]
        self.assertEqual("bd2e114b-c141-5798-abc8-82f84df3b35f", new.candidate_id)
        self.assertEqual(old.candidate_id, new.candidate_id)
        self.assertEqual(old.outcome, new.outcome)
        self.assertTrue(set(old.evidence).issubset(new.evidence))
        added = set(new.evidence) - set(old.evidence)
        self.assertEqual(1, len(added))
        issuer = added.pop()
        self.assertEqual(correction["correction"]["url"], issuer.url)
        self.assertEqual("2026-09-07T17:54:49Z", issuer.accessed_at)
        self.assertIn("semiconductor wafer fab", issuer.excerpt)
        self.assertEqual("2026-09-07T17:55:40Z", corrected.reviewed_at)
        self.assertGreater(corrected.reviewed_at, self.review.reviewed_at)
        self.assertEqual(
            Counter(accept_in_scope=15, defer=93),
            Counter(row.outcome for row in corrected.decisions),
        )

    def test_coaddressed_cst_records_remain_distinct(self) -> None:
        cst_ids = {
            "201e9f72-3113-5d9c-bdc4-b007e03f8ea5",
            "c01c2eab-471f-5760-85ff-fcace4910db7",
        }
        selected = [row for row in self.queue.candidates if row.candidate_id in cst_ids]
        self.assertEqual(2, len({row.facility_inspire_id for row in selected}))
        self.assertEqual(
            cst_ids,
            {row.candidate_id for row in self.review.decisions
             if row.candidate_id in cst_ids and row.outcome == "accept_in_scope"},
        )


if __name__ == "__main__":
    unittest.main()
