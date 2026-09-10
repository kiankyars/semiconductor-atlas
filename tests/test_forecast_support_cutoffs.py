"""Input lineage availability is independent of a capacity/event scope review."""

from datetime import date
import json
import unittest

from semiconductor_atlas import analytics, evidence_dossier, models as m, repository as r
from tests import test_analytics as fixtures


RECORDED = fixtures.RECORDED_AT
BEFORE = "2026-07-19T00:00:00Z"
COMPLETED = "2026-07-20T00:00:00Z"
AS_OF = "2026-07-17"
REASON = "forecast_input_support_unavailable_or_invalid"


class ForecastSupportCutoffTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ForecastBridgeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.c = self.fixture.connection
        self.source = self.c.execute("SELECT source_id FROM source_documents WHERE id=?",
                                     (self.fixture.document_id,)).fetchone()[0]

    def run_record(self, name="late-run", *, completed=COMPLETED, accepted=COMPLETED):
        r.add_ingestion_run(self.c, m.IngestionRun(name, self.source, fixtures.CREATED_AT,
            m.IngestionStatus.SUCCEEDED, completed_at=completed,
            input_document_id=self.fixture.document_id,
            parameters={"accepted_at": accepted, "private": "DO_NOT_EXPORT"}))
        return name

    def scalar(self, name="parent", *, run=None, parent=None, recorded=RECORDED, evidence=None):
        series = self.fixture._series(name, m.ValueKind.SCALAR)
        version = m.ClaimVersion(name, series.id, "2026-01-01" if parent else None, recorded,
            m.ClaimKind.DERIVED_ESTIMATE if parent else m.ClaimKind.SOURCE_STATEMENT,
            "synthetic-support", 1.0 if parent else None, created_by_run_id=run)
        r.insert_claim(self.c, version, m.ScalarValue(m.ScalarType.TEXT, "Synthetic retained support"),
            dependencies=[m.DependencyLink(parent.id)] if parent else [],
            evidence=[] if parent else evidence if evidence is not None else [m.EvidenceLink(self.fixture.document_id)])
        return version

    def capacity(self, name="capacity", *, parent=None, run=None):
        series = self.fixture._series(name, m.ValueKind.CAPACITY)
        version = m.ClaimVersion(name, series.id, "2026-01-01", RECORDED,
            m.ClaimKind.DERIVED_ESTIMATE if parent else m.ClaimKind.SOURCE_STATEMENT,
            "synthetic-support", 1.0, created_by_run_id=run)
        r.insert_claim(self.c, version,
            m.CapacityValue("wafer_starts", m.CapacityBasis.ECONOMICALLY_USABLE, "wafers/month", 80, 100, 120),
            dependencies=[m.DependencyLink(parent.id)] if parent else [],
            evidence=[] if parent else [m.EvidenceLink(self.fixture.document_id)])
        return version

    def forecast(self, recorded=BEFORE):
        return analytics.forecast_current_capacity(self.c, as_of=AS_OF, recorded_at=recorded,
                                                   forecast_start=date(2026, 7, 1))

    def test_valid_store_late_ancestor_run_is_not_available_to_an_earlier_forecast(self):
        parent = self.scalar(run=self.run_record())
        capacity = self.capacity(parent=parent)
        self.c.commit()
        self.assertEqual([], r.validate_database(self.c))
        result = self.forecast()
        self.assertEqual(0, len(result))
        self.assertEqual(1, len(result.exclusions))
        self.assertEqual(capacity.id, result.exclusions[0].capacity_claim_id)
        self.assertEqual(REASON, result.exclusions[0].reason)
        self.assertIn("run completed_at", result.exclusions[0].detail)
        self.assertEqual((), result.pairings)
        self.assertNotIn("DO_NOT_EXPORT", analytics.forecast_exclusions_jsonl(result).decode())

    def test_exact_completion_boundary_allows_usable_capacity_without_milestone(self):
        self.capacity(parent=self.scalar(run=self.run_record()))
        self.assertEqual(0, len(self.forecast("2026-07-19T23:59:59.999999Z")))
        result = self.forecast(COMPLETED)
        self.assertEqual(1, len(result))
        self.assertEqual(20, len(result[0].points))
        self.assertIsNone(result[0].capacity_input.production_start)
        self.assertEqual((), result.pairings)
        self.assertEqual((), result.exclusions)
        self.assertEqual(result, self.forecast("2026-07-19T17:00:00-07:00"))

    def test_direct_capacity_run_is_checked_without_dependencies(self):
        self.capacity(run=self.run_record())
        self.assertEqual([], r.validate_database(self.c))
        self.assertEqual(REASON, self.forecast().exclusions[0].reason)
        self.assertEqual(1, len(self.forecast(COMPLETED)))

    def test_deep_dependency_run_is_checked(self):
        grandparent = self.scalar("grandparent", run=self.run_record())
        parent = self.scalar("parent", parent=grandparent)
        self.capacity(parent=parent)
        self.assertEqual([], r.validate_database(self.c))
        self.assertEqual(0, len(self.forecast()))
        self.assertEqual(1, len(self.forecast(COMPLETED)))

    def test_source_record_run_is_checked_even_without_claim_run(self):
        run = self.run_record()
        payload = {"row": "synthetic"}
        r.add_source_record(self.c, m.SourceRecord("record", run, self.fixture.document_id,
            "synthetic-record", fixtures.CREATED_AT, m.source_record_payload_sha256(payload), payload))
        parent = self.scalar(evidence=[m.EvidenceLink(self.fixture.document_id, source_record_id="record")])
        self.capacity(parent=parent)
        self.assertEqual([], r.validate_database(self.c))
        self.assertEqual(REASON, self.forecast().exclusions[0].reason)
        self.assertEqual(1, len(self.forecast(COMPLETED)))

    def test_entity_creation_run_is_checked(self):
        run = self.run_record()
        r.add_entity(self.c, m.Entity("run-entity", m.EntityKind.PROJECT, "run-entity",
            fixtures.CREATED_AT, "Synthetic run-created project", created_by_run_id=run))
        self.fixture.entity_id = "run-entity"
        self.capacity()
        self.assertEqual([], r.validate_database(self.c))
        self.assertEqual(REASON, self.forecast().exclusions[0].reason)
        self.assertEqual(1, len(self.forecast(COMPLETED)))

    def test_unrelated_unavailable_history_does_not_exclude_supported_capacity(self):
        self.scalar("unrelated", run=self.run_record())
        capacity = self.capacity()
        with self.assertRaisesRegex(ValueError, "run completed_at"):
            evidence_dossier.build_evidence_dossier(self.c, subject_entity_id=self.fixture.entity_id,
                                                    as_of=AS_OF, recorded_at=BEFORE)
        result = self.forecast()
        self.assertEqual([capacity.id], [row.capacity_input.capacity_claim_id for row in result])
        self.assertEqual((), result.exclusions)

    def test_one_unavailable_input_does_not_remove_an_independent_supported_series(self):
        bad = self.capacity("unavailable", parent=self.scalar(run=self.run_record()))
        good = self.capacity("available")
        result = self.forecast()
        self.assertEqual([good.id], [row.capacity_input.capacity_claim_id for row in result])
        self.assertEqual([bad.id], [row.capacity_claim_id for row in result.exclusions])
        summary = json.loads(analytics.forecast_summary(result))
        self.assertEqual(1, summary["capacity_series"])
        self.assertEqual(20, summary["forecast_points"])
        self.assertEqual({REASON: 1}, summary["exclusion_reason_counts"])

    def test_superseded_dependency_version_remains_valid_historical_support(self):
        parent = self.scalar()
        self.capacity(parent=parent)
        r.insert_claim(self.c, m.ClaimVersion("replacement", parent.series_id, None, BEFORE,
            m.ClaimKind.SOURCE_STATEMENT, "synthetic-correction", None),
            m.ScalarValue(m.ScalarType.TEXT, "Later source wording"),
            evidence=[m.EvidenceLink(self.fixture.document_id)])
        self.assertEqual([], r.validate_database(self.c))
        self.assertEqual(1, len(self.forecast()))

    def test_invalid_explicit_admission_is_not_treated_as_absent(self):
        self.capacity(parent=self.scalar(run=self.run_record(completed=RECORDED, accepted=None)))
        result = self.forecast()
        self.assertEqual(0, len(result))
        self.assertEqual(REASON, result.exclusions[0].reason)
        self.assertIn("invalid explicit accepted_at", result.exclusions[0].detail)

    def test_support_validation_preserves_caller_transaction_and_savepoint(self):
        self.c.commit()
        self.c.execute("BEGIN")
        self.c.execute("SAVEPOINT atlas_claim_support_read")
        capacity = self.capacity(parent=self.scalar(run=self.run_record()))
        before = self.c.serialize()
        self.assertEqual(0, len(self.forecast()))
        self.assertEqual(before, self.c.serialize())
        self.assertTrue(self.c.in_transaction)
        self.c.execute("ROLLBACK TO SAVEPOINT atlas_claim_support_read")
        self.c.execute("RELEASE SAVEPOINT atlas_claim_support_read")
        self.assertIsNone(self.c.execute("SELECT id FROM claim_versions WHERE id=?", (capacity.id,)).fetchone())
        self.c.rollback()

    def test_unknown_and_malformed_support_roots_fail_without_writes(self):
        self.capacity()
        before = self.c.serialize()
        for roots in ([], "capacity", [None], [""], ["missing"]):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                evidence_dossier.validate_claim_support(self.c, claim_ids=roots, recorded_at=BEFORE)
            self.assertEqual(before, self.c.serialize())


if __name__ == "__main__":
    unittest.main()
