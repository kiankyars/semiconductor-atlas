from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from semiconductor_atlas import realized_milestones as observations
from tests import test_realized_milestone_cli as fixtures


class RealizedMilestoneReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RealizedMilestoneCLITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_retained_capture_cannot_postdate_its_provenance_record(self):
        fixture = self.fixture
        provenance = json.loads(fixture.provenance)
        later = "2026-08-22T00:00:00Z"
        provenance["sources"][0]["retrieved_at"] = later
        provenance["sources"][0]["acquired_at"] = later
        fixture.review["source"]["retrieved_at"] = later
        raw = observations.canonical_bytes(provenance)
        fixture.review["provenance"]["sha256"] = fixture._hash(raw)
        with self.assertRaisesRegex(ValueError, "clock|capture|retriev|acquir|provenance"):
            observations.validate_review(fixture.review, body=fixture.body, provenance=raw)

    def test_admission_rejects_producer_bytes_changing_during_validation(self):
        fixture = self.fixture
        original = observations._codes()
        changed = {**original, "realized_milestones.py": "0" * 64}
        with patch.object(observations, "_codes", side_effect=[original, changed]):
            with self.assertRaisesRegex(ValueError, "code|implementation|producer"):
                observations.admit(fixture.review, body=fixture.body, provenance=fixture.provenance)

    def test_contradictory_caption_cannot_be_ignored_by_table_validation(self):
        fixture = self.fixture
        caption = b'<caption>These are planned targets only. Fab 21 has not commenced commercial production.</caption>'
        offset = fixture.body.index(b'<table>') + len(b'<table>')
        body = fixture.body[:offset] + caption + fixture.body[offset:]
        review = fixture.review
        for span in review["evidence"].values():
            if span["start"] >= offset:
                span["start"] += len(caption)
            if span["end"] >= offset:
                span["end"] += len(caption)
            span["sha256"] = fixture._hash(body[span["start"]:span["end"]])
        review["source"]["bytes"] = len(body)
        review["source"]["content_sha256"] = fixture._hash(body)
        provenance = json.loads(fixture.provenance)
        provenance["sources"][0].update(review["source"])
        provenance["ingestion_runs"][0]["inputs"][0]["content_sha256"] = fixture._hash(body)
        raw = observations.canonical_bytes(provenance)
        review["provenance"]["sha256"] = fixture._hash(raw)
        with self.assertRaisesRegex(ValueError, "table|caption|content|unrecognized"):
            observations.validate_review(review, body=body, provenance=raw)


if __name__ == "__main__":
    unittest.main()
