from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts import record_realized_milestone as cli
from semiconductor_atlas import milestone_benchmark as benchmark


class RealizedMilestoneCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        intro = (b'<p>The following table lists our wafer fabs and those of our subsidiaries in operation '
                 b'as of February 28, 2026, together with the year of commencement of commercial production, '
                 b'wafer size and the most advanced technology for volume production:</p>')
        sizing = b'<tr>' + b'<td></td>' * 7 + b'</tr>'
        header = (b'<tr><td>Fab(1)</td><td></td><td>Year of commencement of commercial production</td>'
                  b'<td></td><td>Wafer size</td><td></td>'
                  b'<td>The most advanced technology for volume production(2)</td></tr>')
        row = b'<tr><td>21</td><td></td><td>2024</td><td></td><td>12-inch</td><td></td><td>5</td></tr>'
        table = b'<table>' + sizing + header + row + b'</table>'
        self.body = b'<html><body>' + intro + table + b'</body></html>'
        self.source = {"source_id": "tsmc-2025-20f",
            "url": "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm",
            "content_sha256": self._hash(self.body), "bytes": len(self.body),
            "published_at": "2026-04-16", "published_at_precision": "day",
            "retrieved_at": "2026-08-20T16:11:52Z"}
        self.provenance = benchmark.canonical_bytes({
            "format": "semiconductor-atlas-ai-critical-input-v1",
            "recorded_at": "2026-08-20T16:13:00Z",
            "sources": [{**self.source, "media_type": "text/html", "source_type": "official_primary",
                "acquired_at": self.source["retrieved_at"], "ingestion_run_id": "fixture-run"}],
            "ingestion_runs": [{"run_id": "fixture-run", "source_id": "tsmc-2025-20f",
                "outcome": "succeeded", "started_at": "2026-08-20T16:11:52Z",
                "finished_at": "2026-08-20T16:12:00Z", "inputs": [{"source_id": "tsmc-2025-20f",
                    "content_sha256": self.source["content_sha256"], "document_role": "primary"}]}]})
        self.review = {"format": "semiconductor-atlas-realized-milestone-review-v1",
            "validation_rule": "tsmc_operating_fab_commercial_production_year_v1",
            "subject": {"source_native_id": "tsmc-2025-20f:fab:21", "kind": "facility",
                "label": "Fab 21", "scope": "issuer_operating_fab_table",
                "canonical_entity_id": None, "location": None},
            "event": {"event_type": "commercial_production_commencement", "low": "2024-01-01",
                "base": None, "high": "2024-12-31", "precision": "year", "literal": "2024"},
            "source": self.source,
            "provenance": {"sha256": self._hash(self.provenance),
                "reference": "baselines/ai_critical_manufacturing_v1.json"},
            "evidence": {name: self._span(raw) for name, raw in
                         (("intro", intro), ("table", table), ("header", header), ("row", row))},
            "review": {"reviewed_by": "Engineering fixture author", "reviewed_at": "2026-09-01T00:00:00Z",
                "prior_exposure": "Fully exposed synthetic engineering fixture, not real source evidence.",
                "rationale": "Test exact source table binding, calendar precision and preserved unknowns; never a real manufacturing observation."}}
        self.body_path = self.root / "source.html"
        self.body_path.write_bytes(self.body)
        self.provenance_path = self.root / "source-metadata.json"
        self.provenance_path.write_bytes(self.provenance)
        self.review_path = self.root / "review.json"
        self.review_path.write_bytes(benchmark.canonical_bytes(self.review))
        self.output = self.root / "observation.json"

    @staticmethod
    def _hash(raw):
        return hashlib.sha256(raw).hexdigest()

    def _span(self, raw):
        start = self.body.index(raw)
        return {"start": start, "end": start + len(raw), "sha256": self._hash(raw)}

    def _call(self, command, *, expected=0, body=None):
        args = [command, "--source-body", str(body or self.body_path),
                "--provenance", str(self.provenance_path)]
        if command == "admit":
            args += ["--review", str(self.review_path), "--output", str(self.output)]
        else:
            args += ["--observation", str(self.output)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(args)
        self.assertEqual(expected, code, stderr.getvalue())
        return json.loads(stdout.getvalue()) if code == 0 else stderr.getvalue()

    def test_admit_verify_and_no_overwrite_leave_inputs_unchanged(self):
        receipt = self._call("admit")
        saved = self.output.read_bytes()
        self.assertEqual(self._hash(saved), receipt["sha256"])
        self.assertFalse(receipt["database_writes"])
        self.assertFalse(receipt["publisher_bodies_embedded"])
        self.assertEqual(saved, benchmark.canonical_bytes(json.loads(saved)))
        self.assertNotIn("<table>", saved.decode())
        verified = self._call("verify")
        self.assertTrue(verified["verified"])
        self.assertEqual(receipt["artifact_sha256"], verified["artifact_sha256"])
        self.assertEqual(receipt["sha256"], verified["sha256"])
        self.assertEqual(receipt["bytes"], verified["bytes"])
        self._call("admit", expected=1)
        self.assertEqual(saved, self.output.read_bytes())
        self.assertEqual(self.body, self.body_path.read_bytes())
        self.assertEqual(self.provenance, self.provenance_path.read_bytes())

    def test_mismatched_source_does_not_publish_an_observation(self):
        self.body_path.write_bytes(self.body.replace(b'2024', b'2023'))
        self._call("admit", expected=1)
        self.assertFalse(self.output.exists())

    def test_duplicate_review_keys_and_symlinked_body_fail_without_output(self):
        self.review_path.write_bytes(b'{"format":"a","format":"b"}')
        self._call("admit", expected=1)
        self.review_path.write_bytes(benchmark.canonical_bytes(self.review))
        alias = self.root / "body-link.html"
        alias.symlink_to(self.body_path)
        self._call("admit", expected=1, body=alias)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
