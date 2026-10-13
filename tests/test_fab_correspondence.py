"""Synthetic successor/replay checks; no real publisher evidence is modified.

Only the pinned publisher-body SHA is substituted for the small HTML fixture.
Review validation, core mapping and exact successor replay otherwise run unmocked.
The synthetic parent mirrors the imported cohort shape, not its historical verifier.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semiconductor_atlas import database, fab_correspondence as correspondence, models, repository


OBSERVED = "2026-08-20T01:00:00Z"
PARENT_START = "2026-09-08T10:00:00Z"
PARENT_ADMITTED = "2026-09-08T10:00:01Z"
REVIEWED = "2026-09-08T11:00:00Z"
STARTED = "2026-09-08T12:00:00Z"
ADMITTED = "2026-09-08T12:00:01Z"
BEFORE = "2026-09-08T12:00:00.999999Z"
LATER = "2026-09-08T13:00:00Z"
YEARS = {"2": "1990", "3": "1995", "5": "1997", "6": "2000", "8": "1998", "10": "2004",
         "11": "1998", "12": "2001", "14": "2004", "15": "2012", "16": "2018", "18": "2020",
         "20": "2025", "21": "2024", "22": "2025", "23": "2024"}
LOCATIONS = {"2": "Hsinchu Science Park", "3": "Hsinchu Science Park", "5": "Hsinchu Science Park",
    "6": "Southern Taiwan Science Park", "8": "Hsinchu Science Park", "10": "Shanghai, China",
    "11": "Washington State, United States", "12": "Hsinchu Science Park", "14": "Southern Taiwan Science Park",
    "15": "Central Taiwan Science Park", "16": "Nanjing, China", "18": "Southern Taiwan Science Park",
    "20": "Hsinchu Science Park", "21": "Arizona, U.S.", "22": "Southern Taiwan Science Park", "23": "Kumamoto, Japan"}
MONTHS = {
    "11": {"literal": "October 1998", "low": "1998-10-01", "high": "1998-10-31", "evidence": "washington"},
    "16": {"literal": "April 2018", "low": "2018-04-01", "high": "2018-04-30", "evidence": "nanjing"},
}
TEXTS = {
    "locations": "Fabs 2, 3, 5, 8, 12 and Fab 20 are located in Hsinchu Science Park. "
        "Fab 6, Fab 14, Fab 18, and Fab 22 are located in the Southern Taiwan Science Park. "
        "Fab 15 is located in Central Taiwan Science Park. "
        "Fab 11 is located in the Washington State, United States. "
        "Fab 10 is located in Shanghai, China, and Fab 16 is located in Nanjing, China, "
        "Fab 21 is located in Arizona, U.S. Fab 23 is located in Kumamoto, Japan.",
    "washington": "The subsidiary (the manufacturing entity is Fab 11) began trial production in July 1998; "
        "commercial production commenced in October 1998.",
    "china": "The manufacturing entity is Fab 10. Commercial production began in late 2004.",
    "nanjing": "The subsidiary (the manufacturing entity is Fab 16) commenced commercial production in April 2018.",
    "arizona": "Fab 21 covers the Arizona manufacturing entity and multiple facilities, not a unique project phase.",
    "jasm": "Fab 23 covers the manufacturing entity and a second facility project, not unique phase correspondence.",
}


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode()


def fixture(texts=None):
    body = bytearray(b"<html><body>")
    evidence, rows = {}, {}

    def append(fragment):
        raw = fragment.encode()
        start = len(body)
        body.extend(raw)
        return {"start": start, "end": len(body), "sha256": correspondence.runtime.digest(raw)}

    for key, text in (texts or TEXTS).items():
        evidence[key] = append("<p>" + text + "</p>")
    append("<table>")
    header = append("<tr><td>Fab</td><td>Commercial production year</td></tr>")
    for fab, year in YEARS.items():
        rows[fab] = append(f"<tr><td>{fab}</td><td>{year}</td></tr>")
    append("</table></body></html>")
    review = {"format": correspondence.FORMAT, "reviewed_at": REVIEWED, "reviewed_by": "synthetic:test-reviewer",
        "prior_exposure": "Synthetic, exposed test fixture; not independent source corroboration.",
        "rationale": "Explicit same-document location and commercial-month test assertions.",
        "parent_database_sha256": "a" * 64, "parent_run_id": "parent-run", "cohort_sha256": "b" * 64,
        "source_sha256": correspondence.runtime.digest(body), "source_bytes": len(body), "evidence": evidence,
        "rows": [{"fab": fab, "location": LOCATIONS[fab], "commercial_month": copy.deepcopy(MONTHS.get(fab))}
                 for fab in YEARS],
        "excluded_or_unresolved": [
            {"candidate": "Fab 11 July commercial commencement", "decision": "reject", "evidence": ["washington"],
             "reason": "July describes trial production, not commercial production."},
            {"candidate": "Fab 21 first-facility equality", "decision": "defer", "evidence": ["arizona"],
             "reason": "Manufacturing-entity scope is not unique project-phase correspondence."}]}
    return bytes(body), review, rows, header


def parent_image(review, row_spans, header, *, years=None, parameters_change=None):
    connection, _ = database.initialize(":memory:", target_version=6)
    try:
        years = years or YEARS
        repository.add_source_family(connection, models.SourceFamily("family", "synthetic-family", "Synthetic", OBSERVED))
        repository.add_source(connection, models.Source("source", "family", "synthetic-source", "Synthetic TSMC table",
            "Synthetic publisher", "https://example.test/filing", OBSERVED))
        repository.add_source_document(connection, models.SourceDocument("document", "source", "https://example.test/filing",
            "Synthetic operating-fab filing", OBSERVED, review["source_sha256"], published_at="2026-03-01"))
        cases = [{"subject": {"source_native_id": "tsmc-2025-20f:fab:" + fab}, "decision": "accept_source_report",
                  "event": {"literal": year}, "evidence": row_spans[fab]} for fab, year in years.items()]
        parameters = {"rule_version": correspondence.PARENT_RULE, "trusted": {"cohort_sha256": review["cohort_sha256"]},
            "accepted_at": PARENT_ADMITTED, "cohort": {"cases": cases,
                "anchor": {"review": {"source": {"content_sha256": review["source_sha256"], "retrieved_at": OBSERVED},
                                       "evidence": {"header": header}}},
                "rights": {"raw_redistribution": False}, "selection": {"survivorship_bias": True}}}
        if parameters_change:
            parameters_change(parameters)
        repository.add_ingestion_run(connection, models.IngestionRun("parent-run", "source", PARENT_START,
            models.IngestionStatus.SUCCEEDED, PARENT_ADMITTED, input_document_id="document", parameters=parameters))
        for fab, year in years.items():
            entity, series, claim, record = (f"parent-{kind}-{fab}" for kind in ("entity", "series", "claim", "record"))
            payload = {"fab": fab, "year": year}
            repository.add_source_record(connection, models.SourceRecord(record, "parent-run", "document", "fab:" + fab,
                OBSERVED, models.source_record_payload_sha256(payload), payload))
            repository.add_entity(connection, models.Entity(entity, models.EntityKind.FACILITY,
                correspondence.PARENT_RULE + ":tsmc-2025-20f:fab:" + fab, PARENT_ADMITTED, "Fab " + fab, "parent-run"))
            repository.add_claim_series(connection, models.ClaimSeries(series, entity, "parent-year:" + fab,
                "milestone." + correspondence.EVENT, models.ValueKind.MILESTONE, PARENT_ADMITTED))
            repository.insert_claim(connection, models.ClaimVersion(claim, series, None, PARENT_ADMITTED,
                models.ClaimKind.SOURCE_STATEMENT, "synthetic-parent", None, created_by_run_id="parent-run"),
                models.MilestoneValue(correspondence.EVENT, models.MilestoneStatus.COMPLETED,
                    year + "-01-01", None, year + "-12-31", "year", year),
                evidence=[models.EvidenceLink("document", source_record_id=record, locator="Synthetic table row")])
        connection.commit()
        if repository.validate_database(connection):
            raise AssertionError("synthetic parent must be valid")
        return connection.serialize()
    finally:
        connection.close()


def table_rows(connection):
    return {row[0]: [tuple(item) for item in connection.execute(f'SELECT * FROM "{row[0]}" ORDER BY rowid')]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}


class FabCorrespondenceReviewTests(unittest.TestCase):
    def validate(self, review, body, *, digest=None):
        raw = canonical(review)
        with mock.patch.object(correspondence, "SOURCE_SHA", correspondence.runtime.digest(body)):
            return correspondence.validate_review(raw, body, digest or correspondence.runtime.digest(raw))

    def test_complete_review_preserves_source_specific_scope(self):
        body, review, _, _ = fixture()
        self.assertEqual(review, self.validate(review, body))
        self.assertEqual(16, len(review["rows"]))
        self.assertEqual(["11", "16"], [r["fab"] for r in review["rows"] if r["commercial_month"]])

    def test_altered_review_or_body_cannot_reuse_trusted_digest(self):
        body, review, _, _ = fixture()
        with self.assertRaisesRegex(ValueError, "review hash"):
            self.validate(review, body, digest="0" * 64)
        with mock.patch.object(correspondence, "SOURCE_SHA", review["source_sha256"]):
            with self.assertRaisesRegex(ValueError, "source bytes differ"):
                correspondence.validate_review(canonical(review), body + b" ", correspondence.runtime.digest(canonical(review)))

    def test_incomplete_reordered_or_invented_rows_are_rejected(self):
        body, original, _, _ = fixture()
        changes = [lambda r: r["rows"].pop(), lambda r: r["rows"].reverse(),
            lambda r: r["rows"].append(copy.deepcopy(r["rows"][0])),
            lambda r: r["rows"][0].update(location="Arizona, U.S."),
            lambda r: r["rows"][13].update(commercial_month=copy.deepcopy(MONTHS["16"]))]
        for change in changes:
            review = copy.deepcopy(original)
            change(review)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "sixteen-row scope"):
                self.validate(review, body)

    def test_trial_date_midpoint_or_invented_month_bounds_are_rejected(self):
        body, original, _, _ = fixture()
        for update in ({"literal": "July 1998", "low": "1998-07-01", "high": "1998-07-31"},
                       {"high": "1998-10-30"}, {"low": "1998-10-02"}, {"base": "1998-10-15"},
                       {"evidence": "china"}):
            review = copy.deepcopy(original)
            review["rows"][6]["commercial_month"].update(update)
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, "exact assertion differs"):
                self.validate(review, body)

    def test_same_fab_commercial_wording_is_required_not_trial_or_other_fab(self):
        variants = [dict(TEXTS, washington=TEXTS["washington"].replace("Fab 11)", "Fab 10)")),
            dict(TEXTS, washington=TEXTS["washington"].replace("commercial production commenced", "trial production commenced")),
            dict(TEXTS, nanjing=TEXTS["nanjing"].replace("Fab 16)", "Fab 21)")),
            dict(TEXTS, nanjing=TEXTS["nanjing"].replace("April 2018", "April 2019"))]
        for texts in variants:
            body, review, _, _ = fixture(texts)
            with self.subTest(texts=texts), self.assertRaisesRegex(ValueError, "same-fab exact commercial"):
                self.validate(review, body)

    def test_location_wording_and_exact_evidence_span_required(self):
        body, review, _, _ = fixture(dict(TEXTS, locations=TEXTS["locations"].replace("Hsinchu Science Park", "Arizona")))
        with self.assertRaisesRegex(ValueError, "geographic subject"):
            self.validate(review, body)
        body, original, _, _ = fixture()
        for change in ({"end": len(body) + 1}, {"start": True}, {"sha256": "0" * 64}):
            review = copy.deepcopy(original)
            review["evidence"]["locations"].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "evidence span"):
                self.validate(review, body)

    def test_invalid_review_metadata_and_exclusion_decisions_rejected(self):
        body, original, _, _ = fixture()
        changes = [lambda r: r.update(reviewed_at="2026-09-08T11:00:00"),
            lambda r: r.update(reviewed_by=" "), lambda r: r.update(parent_database_sha256="not-a-hash"),
            lambda r: r.update(source_bytes=r["source_bytes"] + 1),
            lambda r: r["excluded_or_unresolved"][0].update(decision="accept"),
            lambda r: r["excluded_or_unresolved"][0].update(evidence=["invented"])]
        for change in changes:
            review = copy.deepcopy(original)
            change(review)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(review, body)


class FabCorrespondenceSuccessorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="atlas-fab-correspondence-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.body, self.review, self.row_spans, self.header = fixture()
        self.parent = self.root / "parent.sqlite"
        self.review_path = self.root / "review.json"
        self.source = self.root / "source.html"
        self.output = self.root / "successor.sqlite"
        self.source.write_bytes(self.body)
        self.install_parent(parent_image(self.review, self.row_spans, self.header))
        patch = mock.patch.object(correspondence, "SOURCE_SHA", self.review["source_sha256"])
        patch.start()
        self.addCleanup(patch.stop)

    def save_review(self):
        self.review_raw = canonical(self.review)
        self.review_path.write_bytes(self.review_raw)
        self.review_sha = correspondence.runtime.digest(self.review_raw)

    def install_parent(self, raw):
        self.parent_raw = raw
        self.parent.write_bytes(raw)
        self.review["parent_database_sha256"] = correspondence.runtime.digest(raw)
        self.save_review()

    def enrich(self, *, verify=False, clocks=None, expected_output="recorded"):
        if expected_output == "recorded":
            expected_output = getattr(self, "output_sha", "0" * 64) if verify else None
        with mock.patch.object(correspondence, "now", side_effect=clocks or ([LATER] if verify else [STARTED, ADMITTED])):
            result = correspondence.enrich(parent=self.parent, review_path=self.review_path, source_body=self.source,
                expected_review_sha256=self.review_sha, output=self.output, verify_only=verify,
                expected_output_sha256=expected_output)
        if not verify:
            self.output_sha = result["database_sha256"]
        return result

    def open(self, path):
        connection = correspondence.open_image(path.read_bytes())
        self.addCleanup(connection.close)
        return connection

    def test_successor_adds_eighteen_source_assertions_without_new_physical_identity(self):
        old = table_rows(self.open(self.parent))
        result = self.enrich()
        successor = self.open(self.output)
        self.assertEqual(18, len(result["claims"]))
        self.assertEqual(16, result["source_reported_locations"])
        self.assertEqual(2, result["additional_month_assertions"])
        self.assertEqual(16, result["distinct_retrospective_fabs"])
        self.assertEqual(16, result["unresolved_project_phases"])
        self.assertFalse(any(result["boundaries"].values()))
        self.assertEqual(self.parent_raw, self.parent.read_bytes())
        current = table_rows(successor)
        for table, rows in old.items():
            with self.subTest(table=table):
                self.assertTrue(set(rows) <= set(current[table]), "all existing rows must be retained byte-for-byte")
        for table in ("entities", "geometry_values", "capacity_values", "relationship_values",
                      "source_entity_assignments", "entity_resolution_decisions", "entity_resolution_candidates",
                      "source_documents", "source_families", "sources"):
            self.assertEqual(old[table], current[table], table)
        self.assertEqual(34, len(current["claim_versions"]))
        self.assertEqual(18, len(current["claim_dependencies"]))
        self.assertEqual(16, len(current["scalar_values"]))
        self.assertEqual(18, len(current["milestone_values"]))
        self.assertEqual([], repository.validate_database(successor))

    def test_month_assertions_refine_without_superseding_original_year_or_changing_knowledge_time(self):
        result = self.enrich()
        connection = self.open(self.output)
        for item in result["claims"]:
            row = connection.execute("SELECT * FROM claim_versions WHERE id=?", (item["claim_id"],)).fetchone()
            self.assertEqual("source_statement", row["claim_kind"])
            self.assertEqual(ADMITTED, row["recorded_at"])
            self.assertIsNone(row["valid_from"])
            self.assertIsNone(row["confidence"])
            self.assertIsNone(row["superseded_at"])
            if item["assertion"] == "commercial_month":
                value = connection.execute("SELECT * FROM milestone_values WHERE claim_version_id=?", (item["claim_id"],)).fetchone()
                self.assertEqual("completed", value["status"])
                self.assertEqual("month", value["date_precision"])
                self.assertIsNone(value["date_base"])
                month = MONTHS[item["fab"]]
                self.assertEqual((month["low"], month["high"], month["literal"]),
                    (value["date_low"], value["date_high"], value["date_literal"]))
        for row in connection.execute("SELECT * FROM claim_versions WHERE created_by_run_id='parent-run'"):
            self.assertEqual(PARENT_ADMITTED, row["recorded_at"])
            self.assertIsNone(row["superseded_at"])
        self.assertEqual(16, len(repository.known_source_claims(connection, recorded_at=BEFORE)))
        self.assertEqual(34, len(repository.known_source_claims(connection, recorded_at=ADMITTED)))
        self.assertEqual([], repository.current_claims(connection, as_of="2026-09-08", recorded_at=LATER))

    def test_exact_replay_has_no_file_writes_and_keeps_original_admission(self):
        original = self.enrich()
        paths = (self.parent, self.review_path, self.source, self.output)
        raw = {path: path.read_bytes() for path in paths}
        with mock.patch.object(correspondence.runtime, "write_new", side_effect=AssertionError("verification attempted a write")):
            result = self.enrich(verify=True)
        self.assertTrue(result["verified"])
        self.assertFalse(result["database_writes"])
        self.assertEqual(original["database_sha256"], result["database_sha256"])
        self.assertEqual(original["admitted_at"], result["admitted_at"])
        self.assertEqual(raw, {path: path.read_bytes() for path in paths})

    def test_changed_input_files_reject_before_successor_creation(self):
        for path in (self.parent, self.review_path, self.source):
            original = path.read_bytes()
            try:
                path.write_bytes(original + b" ")
                with self.subTest(path=path.name), self.assertRaises(ValueError):
                    self.enrich()
                self.assertFalse(self.output.exists())
            finally:
                path.write_bytes(original)

    def test_altered_but_intact_successor_fails_exact_reconstruction(self):
        self.enrich()
        connection = self.open(self.output)
        repository.add_source_family(connection, models.SourceFamily("extra-family", "extra-family", "Unrelated addition", LATER))
        connection.commit()
        changed = connection.serialize()
        self.output.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "trusted successor hash mismatch"):
            self.enrich(verify=True)
        with self.assertRaisesRegex(ValueError, "exact parent-plus-reviewed-assertions reconstruction"):
            self.enrich(verify=True, expected_output=correspondence.runtime.digest(changed))
        self.assertEqual(changed, self.output.read_bytes())
        self.assertEqual(self.parent_raw, self.parent.read_bytes())

    def test_successor_hash_is_required_for_verify_and_not_accepted_for_admit(self):
        with self.assertRaisesRegex(ValueError, "not new admission"):
            self.enrich(expected_output="0" * 64)
        self.assertFalse(self.output.exists())
        self.enrich()
        before = self.output.read_bytes()
        for digest in (None, "not-a-hash", "0" * 64):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                self.enrich(verify=True, expected_output=digest)
        self.assertEqual(before, self.output.read_bytes())

    def test_successor_mutation_or_removal_during_verification_is_detected(self):
        self.enrich()
        original_bytes = self.output.read_bytes()
        original = correspondence.populate
        for remove in (False, True):
            self.output.write_bytes(original_bytes)
            def change_target(*args, **kwargs):
                result = original(*args, **kwargs)
                if remove:
                    self.output.unlink()
                else:
                    self.output.write_bytes(b"changed after initial target read")
                return result
            with mock.patch.object(correspondence, "populate", side_effect=change_target):
                with self.subTest(remove=remove), self.assertRaises((ValueError, OSError)):
                    self.enrich(verify=True)
            self.assertEqual(self.parent_raw, self.parent.read_bytes())
            if remove:
                self.assertFalse(self.output.exists())
            else:
                self.assertEqual(b"changed after initial target read", self.output.read_bytes())

    def test_missing_successor_and_existing_output_do_not_create_or_overwrite(self):
        with self.assertRaises(OSError):
            self.enrich(verify=True)
        self.assertFalse(self.output.exists())
        self.output.write_bytes(b"existing unrelated file")
        with self.assertRaisesRegex(ValueError, "output must not exist"):
            self.enrich()
        self.assertEqual(b"existing unrelated file", self.output.read_bytes())

    def test_duplicate_review_in_successor_rejected(self):
        self.enrich()
        connection = self.open(self.output)
        before = table_rows(connection)
        with self.assertRaisesRegex(ValueError, "already contains this review"):
            correspondence.populate(connection, self.review, self.review_sha, STARTED, ADMITTED, correspondence.codes())
        self.assertEqual(before, table_rows(connection))

    def test_parent_review_and_admission_clock_regressions_are_rejected(self):
        for clocks in ([STARTED, STARTED], [STARTED, REVIEWED], [PARENT_ADMITTED, ADMITTED]):
            with self.subTest(clocks=clocks), self.assertRaisesRegex(ValueError, "chronology differs"):
                self.enrich(clocks=clocks)
            self.assertFalse(self.output.exists())
        self.review["reviewed_at"] = PARENT_START
        self.save_review()
        with self.assertRaisesRegex(ValueError, "chronology differs"):
            self.enrich()
        self.assertFalse(self.output.exists())

    def test_future_successor_admission_cannot_verify(self):
        self.enrich()
        before = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, "future successor admission"):
            self.enrich(verify=True, clocks=[BEFORE])
        self.assertEqual(before, self.output.read_bytes())

    def test_parent_cohort_identity_and_complete_inventory_required(self):
        variants = [lambda p: p["trusted"].update(cohort_sha256="c" * 64),
            lambda p: p["cohort"]["cases"][0]["subject"].update(source_native_id="tsmc-2025-20f:fab:99"),
            lambda p: p["cohort"]["cases"][0].update(decision="defer")]
        for change in variants:
            self.install_parent(parent_image(self.review, self.row_spans, self.header, parameters_change=change))
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "identity differs|inventory differs"):
                self.enrich()
            self.assertFalse(self.output.exists())

    def test_month_must_be_within_the_existing_parent_year(self):
        self.install_parent(parent_image(self.review, self.row_spans, self.header, years={**YEARS, "11": "1999"}))
        with self.assertRaisesRegex(ValueError, "month must refine the parent year"):
            self.enrich()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.parent_raw, self.parent.read_bytes())

    def test_input_changed_during_population_never_publishes_successor(self):
        original = correspondence.populate
        def change_input(*args, **kwargs):
            result = original(*args, **kwargs)
            self.source.write_bytes(self.body + b" ")
            return result
        with mock.patch.object(correspondence, "populate", side_effect=change_input):
            with self.assertRaisesRegex(ValueError, "input changed during enrichment"):
                self.enrich()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.parent_raw, self.parent.read_bytes())

    def test_mid_population_failure_leaves_parent_and_output_untouched(self):
        original = repository.insert_claim
        inserted = 0
        def fail(*args, **kwargs):
            nonlocal inserted
            result = original(*args, **kwargs)
            inserted += 1
            if inserted == 9:
                raise RuntimeError("synthetic failure halfway through new assertions")
            return result
        with mock.patch.object(correspondence.repository, "insert_claim", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "halfway through"):
                self.enrich()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.parent_raw, self.parent.read_bytes())


if __name__ == "__main__":
    unittest.main()
