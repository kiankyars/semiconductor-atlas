"""Synthetic engineering tables; no real observation or independence claim."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semiconductor_atlas import realized_milestones as realized


def fixture(*, intro=None, headers=None, rows=None, prefix="", suffix="", selected=0, cell_attributes=""):
    def row(cells):
        return "<tr>" + "".join(f"<td{cell_attributes}><div>{cell}</div></td>" for cell in cells) + "</tr>"
    introduction = intro or ("<p>The following table lists our wafer fabs and those of our subsidiaries in operation as of "
        "February 28, 2026, together with the year of commencement of commercial production, wafer size "
        "and the most advanced technology for volume production:</p>")
    header = row(headers or realized._HEADERS)
    data = [row(cells) for cells in (rows or [["21", "", "2024", "", "12-inch", "", "5"],
                                            ["23", "", "2024", "", "12-inch", "", "28"]])]
    table = "<table>" + row([""] * 7) + header + "".join(data) + "</table>"
    body = ("<html><body><p>é 工厂 engineering-only fixture</p>" + prefix + introduction + table + suffix + "</body></html>").encode()
    source = {"source_id": realized.SOURCE_ID, "url": realized.SOURCE_URL, "content_sha256": realized._hash(body),
              "bytes": len(body), "published_at": "2026-04-16", "published_at_precision": "day", "retrieved_at": "2026-08-20T16:11:52Z"}
    source_row = {**source, "acquired_at": source["retrieved_at"], "media_type": "text/html",
                  "source_type": "official_primary", "ingestion_run_id": "engineering-run", "title": "Engineering only"}
    run = {"run_id": "engineering-run", "source_id": realized.SOURCE_ID, "outcome": "succeeded",
           "started_at": "2026-08-20T17:00:00Z", "finished_at": "2026-08-20T17:01:00Z",
           "inputs": [{"source_id": realized.SOURCE_ID, "content_sha256": source["content_sha256"], "document_role": "primary"}]}
    provenance = realized.canonical_bytes({"format": "semiconductor-atlas-ai-critical-input-v1", "sources": [source_row],
                                          "ingestion_runs": [run], "recorded_at": "2026-08-20T17:02:00Z"})
    def span(text):
        raw = text.encode()
        start = body.index(raw)
        return {"start": start, "end": start + len(raw), "sha256": realized._hash(raw)}
    review = {"format": realized.REVIEW_FORMAT, "validation_rule": realized.RULE, "subject": copy.deepcopy(realized.SUBJECT),
              "event": {"event_type": "commercial_production_commencement", "low": "2024-01-01", "base": None,
                        "high": "2024-12-31", "precision": "year", "literal": "2024"}, "source": source,
              "provenance": {"sha256": realized._hash(provenance), "reference": realized.PROVENANCE_REFERENCE},
              "evidence": {"intro": span(introduction), "table": span(table), "header": span(header), "row": span(data[selected])},
              "review": {"reviewed_by": "Engineering fixture", "reviewed_at": "2026-09-01T00:00:00Z",
                         "prior_exposure": "Synthetic exposed fixture authored to test the contract; not independent evidence.",
                         "rationale": "Engineering table header, exact facility row and year cells are tested together. This is not a real publisher observation, a forecast, capacity or a blind review."}}
    return review, body, provenance


class RealizedMilestoneTests(unittest.TestCase):
    def setUp(self):
        self.review, self.body, self.provenance = fixture()

    def validate(self, review=None, body=None, provenance=None):
        with mock.patch.object(realized, "_now", return_value="2026-09-07T21:00:00Z"):
            return realized.validate_review(review or self.review, body=self.body if body is None else body,
                                             provenance=self.provenance if provenance is None else provenance)

    def admit(self):
        with mock.patch.object(realized, "_now", side_effect=["2026-09-07T20:00:00Z", "2026-09-07T20:00:01Z", "2026-09-07T20:00:02Z"]):
            return realized.admit(self.review, body=self.body, provenance=self.provenance)

    def verify(self, artifact):
        with mock.patch.object(realized, "_now", return_value="2026-09-07T21:00:00Z"):
            return realized.verify(artifact, body=self.body, provenance=self.provenance)

    def test_exact_year_null_base_without_forecast(self):
        checked = self.validate()
        self.assertEqual(checked["local_evidence_text"]["row"], "21 2024 12-inch 5")
        artifact = self.admit()
        self.assertEqual(artifact["admitted_at"], "2026-09-07T20:00:02Z")
        result = self.verify(realized.canonical_bytes(artifact))
        self.assertEqual(result["event"]["base"], None)
        self.assertEqual(result["event"]["low"], "2024-01-01")
        self.assertFalse(result["boundaries"]["forecast_score"])

    def test_body_provenance_and_excerpts_never_embedded(self):
        artifact = self.admit()
        serialized = realized.canonical_bytes(artifact)
        for forbidden in (self.body, self.provenance, b"local_evidence_text", b"<table>", b"<td>", b"base64"):
            self.assertNotIn(forbidden, serialized)
        self.assertFalse(artifact["rights"]["redistribution_authorized"])

    def test_write_once_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "synthetic.json"
            artifact = self.admit()
            realized.write_new(path, artifact)
            self.assertEqual(self.verify(path.read_bytes())["sha256"], artifact["sha256"])
            with self.assertRaises(FileExistsError):
                realized.write_new(path, artifact)

    def test_source_native_subject_not_an_alias(self):
        for field, value in [("source_native_id", "nist:first-fab"), ("kind", "project"), ("label", "Fab 23"),
                             ("canonical_entity_id", "tsmc:fab21-arizona"), ("location", "Phoenix")]:
            with self.subTest(field=field):
                review = copy.deepcopy(self.review)
                review["subject"][field] = value
                with self.assertRaises(ValueError): self.validate(review)

    def test_event_aliases_vague_dates_and_midpoints_rejected(self):
        mutations = [("event_type", "production_start"), ("event_type", "high_volume_production"), ("event_type", "opening"), ("event_type", []),
                     ("base", "2024-07-01"), ("precision", "quarter"), ("precision", "day"),
                     ("low", "2024-12-31"), ("literal", "at the end of 2024")]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                review = copy.deepcopy(self.review); review["event"][key] = value
                with self.assertRaises(ValueError): self.validate(review)

    def test_wrong_row_same_year_rejected(self):
        review, body, provenance = fixture(selected=1)
        with self.assertRaisesRegex(ValueError, "Fab 21"):
            self.validate(review, body, provenance)

    def test_year_cannot_be_borrowed_from_other_row_or_table_state(self):
        for year in ("2025", "2026"):
            review = copy.deepcopy(self.review)
            review["event"].update(literal=year, low=year + "-01-01", high=year + "-12-31")
            with self.assertRaises(ValueError): self.validate(review)

    def test_changed_semantic_header_rejects_even_with_matching_byte_hashes(self):
        for text in ("Year of high-volume production", "Year of plant opening", "Year of expected commercial production"):
            headers = list(realized._HEADERS); headers[2] = text
            review, body, provenance = fixture(headers=headers)
            with self.assertRaisesRegex(ValueError, "header"):
                self.validate(review, body, provenance)

    def test_wrong_header_span_rejected(self):
        review = copy.deepcopy(self.review); review["evidence"]["header"] = review["evidence"]["row"]
        with self.assertRaises(ValueError): self.validate(review)

    def test_duplicate_subject_rejected(self):
        row = ["21", "", "2024", "", "12-inch", "", "5"]
        review, body, provenance = fixture(rows=[row, row])
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.validate(review, body, provenance)

    def test_merged_missing_shifted_and_nested_cells_rejected(self):
        variants = [{"cell_attributes": ' colspan="2"'}, {"cell_attributes": ' rowspan="2"'},
                    {"rows": [["21", "2024", "", "", "12-inch", "", "5"]]},
                    {"rows": [["21", "2024", "12-inch", "5"]]},
                    {"rows": [["21", "", "2024", "", "<table><tr><td>12-inch</td></tr></table>", "", "5"]]}]
        for variant in variants:
            with self.subTest(variant=variant):
                review, body, provenance = fixture(**variant)
                with self.assertRaises(ValueError): self.validate(review, body, provenance)

    def test_hidden_context_and_closed_controls_rejected(self):
        for opening, closing in [("<div hidden>", "</div>"), ("<details>", "</details>"), ("<dialog>", "</dialog>"),
                                 ('<div style="display:none">', "</div>"), ('<div aria-hidden="true">', "</div>")]:
            review, body, provenance = fixture(prefix=opening, suffix=closing)
            with self.assertRaisesRegex(ValueError, "hidden|collapsed"):
                self.validate(review, body, provenance)

    def test_intro_negation_or_scope_changed_rejected(self):
        for text in ("This table does not report commercial production.", "The following table describes planned projects."):
            review, body, provenance = fixture(intro="<p>" + text + "</p>")
            with self.assertRaisesRegex(ValueError, "introduction"):
                self.validate(review, body, provenance)

    def test_intro_cannot_skip_intervening_context(self):
        review = copy.deepcopy(self.review)
        review["evidence"]["intro"]["end"] -= 4
        span = review["evidence"]["intro"]
        span["sha256"] = realized._hash(self.body[span["start"]:span["end"]])
        with self.assertRaisesRegex(ValueError, "contiguous"):
            self.validate(review)

    def test_unparsed_table_or_row_annotation_is_not_ignored(self):
        for before, after in [(b"<table>", b"<table><caption>Illustrative plans only</caption>"),
                              (b"<tr>", b"<tr>Not commercial production")]:
            review, body, provenance = fixture()
            revised = body.replace(before, after)
            metadata = json.loads(provenance)
            review["source"].update(content_sha256=realized._hash(revised), bytes=len(revised))
            metadata["sources"][0].update(review["source"])
            metadata["ingestion_runs"][0]["inputs"][0]["content_sha256"] = realized._hash(revised)
            for span in review["evidence"].values():
                selected = body[span["start"]:span["end"]].replace(before, after)
                start = revised.index(selected)
                span.update(start=start, end=start + len(selected), sha256=realized._hash(selected))
            raw = realized.canonical_bytes(metadata); review["provenance"]["sha256"] = realized._hash(raw)
            with self.assertRaisesRegex(ValueError, "annotation|context"):
                self.validate(review, revised, raw)

    def test_utf8_offsets_are_bytes_not_characters(self):
        review = copy.deepcopy(self.review)
        review["evidence"]["row"]["start"] -= 5
        with self.assertRaisesRegex(ValueError, "span hash"):
            self.validate(review)

    def test_required_exact_retained_bytes(self):
        with self.assertRaises(ValueError): self.validate(body=self.body + b" ")
        with self.assertRaises(ValueError): self.validate(provenance=self.provenance + b" ")
        with self.assertRaises(ValueError): self.validate(body=b"")

    def test_metadata_fields_and_unique_source_id_bound(self):
        for field, value in [("url", "https://example.test"), ("bytes", len(self.body) + 1),
                             ("published_at", "2026-04-17"), ("retrieved_at", "2026-08-21T00:00:00Z")]:
            with self.subTest(field=field):
                metadata = json.loads(self.provenance); metadata["sources"][0][field] = value
                raw = realized.canonical_bytes(metadata); review = copy.deepcopy(self.review)
                review["provenance"]["sha256"] = realized._hash(raw)
                with self.assertRaises(ValueError): self.validate(review, provenance=raw)
        metadata = json.loads(self.provenance); metadata["sources"] *= 2
        raw = realized.canonical_bytes(metadata); review = copy.deepcopy(self.review)
        review["provenance"]["sha256"] = realized._hash(raw)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.validate(review, provenance=raw)

    def test_ingestion_lineage_bound(self):
        metadata = json.loads(self.provenance)
        metadata["ingestion_runs"][0]["inputs"][0]["content_sha256"] = "b" * 64
        raw = realized.canonical_bytes(metadata); review = copy.deepcopy(self.review)
        review["provenance"]["sha256"] = realized._hash(raw)
        with self.assertRaisesRegex(ValueError, "lineage"):
            self.validate(review, provenance=raw)

    def test_no_backdated_future_or_empty_reviews(self):
        for field, value in [("reviewed_at", "2024-01-01T00:00:00Z"), ("reviewed_at", "2099-01-01T00:00:00Z"),
                             ("prior_exposure", ""), ("rationale", "Approved")]:
            review = copy.deepcopy(self.review); review["review"][field] = value
            with self.assertRaises(ValueError): self.validate(review)

    def test_backwards_actual_admission_clock_rejected(self):
        with mock.patch.object(realized, "_now", side_effect=["2026-09-07T20:00:02Z", "2026-09-07T20:00:03Z", "2026-09-07T20:00:01Z"]):
            with self.assertRaisesRegex(ValueError, "backwards"):
                realized.admit(self.review, body=self.body, provenance=self.provenance)

    def test_resealed_artifact_cannot_bypass_semantics_or_boundaries(self):
        artifact = self.admit()
        for field, value in [("source_native_id", "nist:first-fab"), ("kind", "project")]:
            changed = copy.deepcopy(artifact)
            changed["review"]["subject"][field] = value
            changed["review_sha256"] = realized._hash(changed["review"])
            changed.pop("sha256")
            with self.assertRaises(ValueError): self.verify(realized._benchmark._seal(changed))
        for field in ("rights", "boundaries", "code_sha256"):
            changed = copy.deepcopy(artifact); changed[field] = {}; changed.pop("sha256")
            with self.assertRaises(ValueError): self.verify(realized._benchmark._seal(changed))
        changed = copy.deepcopy(artifact); changed["rights"]["redistribution_authorized"] = 0; changed.pop("sha256")
        with self.assertRaises(ValueError): self.verify(realized._benchmark._seal(changed))

    def test_resealed_future_admission_clock_rejected(self):
        artifact = self.admit(); artifact["admitted_at"] = "2099-01-01T00:00:00Z"; artifact.pop("sha256")
        with self.assertRaises(ValueError): self.verify(realized._benchmark._seal(artifact))

    def test_loaded_code_must_still_match_retained_dependency_bytes(self):
        with mock.patch.object(realized, "_disk_codes", return_value={"changed.py": "0" * 64}):
            with self.assertRaisesRegex(ValueError, "module loading"):
                self.admit()

    def test_duplicate_json_and_unknown_review_keys_rejected(self):
        duplicate = realized.canonical_bytes(self.review).replace(b'{\n', b'{\n  "format": "duplicate",\n', 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.validate(duplicate)
        review = copy.deepcopy(self.review); review["independent"] = True
        with self.assertRaises(ValueError): self.validate(review)


if __name__ == "__main__":
    unittest.main()
