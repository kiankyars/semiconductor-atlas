"""Coverage and connection consumers must share schema 10's exact clock."""

import unittest

from semiconductor_atlas import coverage, database, models as m, repository as r
from tests import test_exact_knowledge_clocks as fixture


class ExactClockCoverageTests(fixture._ClockFixture, unittest.TestCase):
    def seed_scopes(self):
        scopes = {
            "epa-frs": "epa_frs_candidate_scope",
            coverage.TAIWAN_MOENV_FAMILY_KEY: "taiwan_moenv_candidate_scope",
            coverage.TAIWAN_FACTORY_FAMILY_KEY: "taiwan_factory_candidate_scope",
            coverage.TAIWAN_MOF_FAMILY_KEY: "taiwan_mof_tax_registration_scope",
        }
        for family in scopes:
            r.add_source_family(self.c, m.SourceFamily(family, family, family, fixture.CREATED))
            r.add_source(self.c, m.Source(family, family, family, family, "Synthetic",
                "https://example.test", fixture.CREATED))
            r.add_source_document(self.c, m.SourceDocument(family, family,
                "https://example.test/document", "Synthetic", fixture.CREATED, "a" * 64))
            for label, started in (("old", fixture.ADMITTED), ("future", fixture.JUST_AFTER)):
                r.add_ingestion_run(self.c, m.IngestionRun(family + "-" + label, family, started,
                    m.IngestionStatus.SUCCEEDED, input_document_id=family,
                    completed_at="2026-09-10T00:00:00Z", parameters={
                        "candidate_count": 0, "upstream_total_rows": 1,
                        "as_of_date": fixture.AS_OF, "publisher_date": fixture.AS_OF,
                        "dataset_updated_at": "2026-09-09T00:00:00Z",
                        "source_updated_at": "2026-09-09T00:00:00Z"}))
        return scopes

    def test_four_source_scopes_exclude_future_run_metadata(self):
        scopes = self.seed_scopes()
        for cutoff, label in ((fixture.JUST_BEFORE, None), (fixture.ADMITTED, "old"),
                              (fixture.OFFSET_ADMITTED, "old"), (fixture.JUST_AFTER, "future")):
            report = coverage.coverage_report(self.c, as_of=fixture.AS_OF, recorded_at=cutoff)
            for family, key in scopes.items():
                with self.subTest(cutoff=cutoff, scope=key):
                    if label is None:
                        self.assertIsNone(report.get(key))
                    else:
                        self.assertEqual(report[key]["processing_run_id"], family + "-" + label)

    def test_review_scope_uses_exact_completion_and_latest_order(self):
        self.reviewed_run("seed", completed=fixture.ADMITTED)
        for identifier, completed in (("z-old", fixture.ADMITTED), ("a-new", fixture.JUST_AFTER)):
            r.add_ingestion_run(self.c, m.IngestionRun(identifier, "review-source", fixture.CREATED,
                m.IngestionStatus.SUCCEEDED, completed_at=completed,
                code_version=coverage.TAIWAN_TAX_RELATIONSHIP_REVIEW_VERSION, parameters={
                    "workflow": coverage.TAIWAN_TAX_RELATIONSHIP_REVIEW_VERSION,
                    "relationship_type": coverage.TAIWAN_TAX_RELATIONSHIP_TYPE,
                    "candidate_artifact": {}, "review_artifact": {}, "source_bindings": {},
                    "decisions": [], "created_claims": [], "reaffirmed_claims": [],
                    "superseded_claim_ids": []}))
        for cutoff, expected in ((fixture.JUST_BEFORE, None), (fixture.ADMITTED, "z-old"),
                                 (fixture.JUST_AFTER, "a-new")):
            row = coverage._taiwan_tax_relationship_review_scope(self.c, recorded_at=cutoff, claims=[])
            with self.subTest(cutoff=cutoff):
                self.assertEqual(None if row is None else row["review_ingestion_run_id"], expected)

    def test_review_effects_wait_for_created_reaffirmed_and_superseded_clocks(self):
        self.claim("old", series="series")
        self.claim("new", series="series", predicate="source_reported.old",
            recorded=fixture.JUST_AFTER, value=m.ScalarValue(m.ScalarType.TEXT, "corrected"))
        for field in ("created_claims", "reaffirmed_claims", "superseded_claim_ids"):
            parameters = {"created_claims": [], "reaffirmed_claims": [], "superseded_claim_ids": []}
            parameters[field] = (["old"] if field == "superseded_claim_ids" else
                [{"claim_id": "new", "recorded_at": fixture.JUST_AFTER}])
            with self.subTest(effect=field):
                self.assertIsNone(coverage.reviewed_relationship_claim_ids_if_visible(self.c,
                    parameters=parameters, recorded_at=fixture.ADMITTED, context="synthetic"))
                self.assertEqual(coverage.reviewed_relationship_claim_ids_if_visible(self.c,
                    parameters=parameters, recorded_at=fixture.JUST_AFTER, context="synthetic"),
                    {"old"} if field == "superseded_claim_ids" else {"new"})

    def test_clock_registration_is_safe_while_a_statement_is_active(self):
        cursor = self.c.execute("SELECT id FROM entities ORDER BY id")
        self.assertIsNotNone(cursor.fetchone())
        for register in (database.register_clock_functions, database.admission_clock_sql,
                         database.knowledge_clock_sql):
            register(self.c)
        self.assertGreater(len(cursor.fetchall()), 0)
        self.assertEqual(self.c.execute("SELECT atlas_timestamp_us(?) - atlas_timestamp_us(?)",
            (fixture.JUST_AFTER, fixture.ADMITTED)).fetchone()[0], 1)


class LegacyClockCoverageTests(fixture._ClockFixture, unittest.TestCase):
    target_version = 9
    seed_scopes = ExactClockCoverageTests.seed_scopes

    def test_schema9_scope_metadata_keeps_its_original_rounded_cutoff(self):
        scopes = self.seed_scopes()
        report = coverage.coverage_report(self.c, as_of=fixture.AS_OF, recorded_at=fixture.ADMITTED)
        for family, key in scopes.items():
            with self.subTest(scope=key):
                self.assertEqual(report[key]["processing_run_id"], family + "-future")


if __name__ == "__main__":
    unittest.main()
