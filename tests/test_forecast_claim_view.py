"""Cutoff and snapshot regressions using synthetic forecast-bridge claims only."""

import copy
import sqlite3
import unittest
from datetime import date
from unittest import mock

from semiconductor_atlas import analytics, models, repository, service
from tests import test_analytics as fixtures


AS_OF = "2026-07-17"
RECORDED = fixtures.RECORDED_AT
LATER = "2026-07-19T00:00:00Z"


class ForecastClaimViewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ForecastBridgeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.connection = self.fixture.connection
        self.milestone = self.fixture._milestone(models.MilestoneStatus.EXPECTED)
        self.capacity = self.fixture._capacity(name="cutoff-capacity", basis=models.CapacityBasis.ANNOUNCED,
            milestone=self.milestone)
        self.connection.commit()

    def view(self, *, as_of=AS_OF, recorded_at=RECORDED):
        return service.claim_records(self.connection, as_of=as_of, recorded_at=recorded_at)

    def forecast(self, *, as_of=AS_OF, recorded_at=RECORDED, claims=None):
        return analytics.forecast_current_capacity(self.connection, as_of=as_of, recorded_at=recorded_at,
            forecast_start=date(2026, 7, 1), claims=claims,
            **self.fixture._scope_review(as_of=as_of, recorded_at=recorded_at))

    def database_rows(self):
        return {row[0]: [tuple(item) for item in self.connection.execute(f'SELECT * FROM "{row[0]}" ORDER BY rowid')]
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}

    def test_later_cached_claims_cannot_bypass_actual_knowledge_cutoff(self):
        future_view = self.view()
        self.assertEqual(1, len(self.forecast(claims=future_view)))
        before = "2026-07-17T23:59:59.999999Z"
        self.assertEqual([], self.view(recorded_at=before))
        self.assertEqual(0, len(self.forecast(recorded_at=before)))
        with self.assertRaisesRegex(ValueError, "complete database view at the requested cutoffs"):
            self.forecast(recorded_at=before, claims=future_view)
        self.assertFalse(self.connection.in_transaction)

    def test_future_effective_cached_claims_cannot_bypass_actual_world_cutoff(self):
        future_view = self.view()
        self.assertEqual([], self.view(as_of="2025-12-31"))
        self.assertEqual(0, len(self.forecast(as_of="2025-12-31")))
        with self.assertRaisesRegex(ValueError, "complete database view at the requested cutoffs"):
            self.forecast(as_of="2025-12-31", claims=future_view)
        self.assertFalse(self.connection.in_transaction)

    def test_reordered_complete_view_matches_authoritative_forecast_without_mutation(self):
        before = self.database_rows()
        view = self.view()
        supplied = copy.deepcopy(list(reversed(view)))
        expected = self.forecast()
        self.assertEqual(expected, self.forecast(claims=supplied))
        self.assertEqual(list(reversed(view)), supplied)
        self.assertEqual(before, self.database_rows())
        self.assertFalse(self.connection.in_transaction)

    def test_duplicate_omitted_extra_and_malformed_records_are_rejected(self):
        view = self.view()
        variants = [view + [copy.deepcopy(view[0])], view[:-1], [], ["not a record"],
            [{"id": None}], [{"id": "invented"}], view + [{"id": "invented"}]]
        before = self.database_rows()
        for supplied in variants:
            with self.subTest(supplied=supplied), self.assertRaisesRegex(ValueError, "supplied forecast claims"):
                self.forecast(claims=supplied)
            self.assertFalse(self.connection.in_transaction)
            self.assertEqual(before, self.database_rows())

    def test_forged_record_value_evidence_or_dependencies_cannot_replace_database_facts(self):
        original = self.view()
        changes = [
            (self.capacity.id, lambda r: r.update(id="forged-id")),
            (self.capacity.id, lambda r: r.update(recorded_at="2020-01-01T00:00:00Z")),
            (self.capacity.id, lambda r: r.update(valid_from="2020-01-01")),
            (self.capacity.id, lambda r: r.update(subject_entity_id="different-project")),
            (self.capacity.id, lambda r: r["value"].update(base=101)),
            (self.capacity.id, lambda r: r["value"].update(basis="economically_usable")),
            (self.capacity.id, lambda r: r.update(dependencies=[])),
            (self.capacity.id, lambda r: r["dependencies"][0].update(depends_on_claim_version_id="forged-parent")),
            (self.milestone.id, lambda r: r.update(evidence=[])),
            (self.milestone.id, lambda r: r["evidence"][0].update(locator="forged locator")),
            (self.milestone.id, lambda r: r["value"].update(date_base="2027-05-01")),
        ]
        for identifier, change in changes:
            supplied = copy.deepcopy(original)
            change(next(row for row in supplied if row["id"] == identifier))
            with self.subTest(identifier=identifier, change=change), self.assertRaisesRegex(ValueError, "complete database view"):
                self.forecast(claims=supplied)
        self.assertEqual(original, self.view())

    def test_omitted_competing_milestone_cannot_turn_exclusion_into_forecast(self):
        original = self.view()
        self.fixture._milestone(models.MilestoneStatus.STARTED)
        self.connection.commit()
        authoritative = self.forecast()
        self.assertEqual(0, len(authoritative))
        self.assertEqual({"ambiguous_linked_production_milestones"}, {row.reason for row in authoritative.exclusions})
        with self.assertRaisesRegex(ValueError, "complete database view"):
            self.forecast(claims=original)

    def test_omitted_cancellation_is_rejected_even_with_all_forecastable_rows(self):
        original = self.view()
        cancellation = self.fixture._milestone(models.MilestoneStatus.CANCELLED)
        self.connection.commit()
        self.assertIn(cancellation.id, {row["id"] for row in self.view()})
        with self.assertRaisesRegex(ValueError, "complete database view"):
            self.forecast(claims=original)
        authoritative = self.forecast()
        self.assertEqual(authoritative, self.forecast(claims=self.view()))
        self.assertEqual(0, len(authoritative))
        self.assertEqual("conflicting_production_milestone_statuses", authoritative.exclusions[0].reason)
        self.assertEqual(tuple(sorted((self.milestone.id, cancellation.id))),
                         authoritative.exclusions[0].related_claim_ids)

    def test_unlinked_cancellation_does_not_override_a_linked_production_target(self):
        source_id = self.connection.execute("SELECT source_id FROM source_documents WHERE id=?",
                                            (self.fixture.document_id,)).fetchone()[0]
        repository.add_source_document(self.connection, models.SourceDocument("unrelated-document", source_id,
            "https://example.test/unrelated-project-component", "Unrelated component", fixtures.CREATED_AT, "c" * 64))
        series = self.fixture._series("unrelated-cancellation", models.ValueKind.MILESTONE)
        repository.insert_claim(self.connection, models.ClaimVersion("unrelated-cancelled", series.id,
            "2026-01-01", RECORDED, models.ClaimKind.SOURCE_STATEMENT, "fixture", 1.0),
            models.MilestoneValue("production_start", models.MilestoneStatus.CANCELLED,
                "2028-01-01", "2028-04-01", "2028-07-01"),
            evidence=[models.EvidenceLink("unrelated-document")])
        self.connection.commit()
        result = self.forecast()
        self.assertEqual(1, len(result))
        self.assertEqual(self.capacity.id, result[0].capacity_input.capacity_claim_id)
        self.assertEqual((), result.exclusions)

    def test_cancelled_future_project_does_not_erase_existing_usable_capacity(self):
        self.fixture._milestone(models.MilestoneStatus.CANCELLED)
        usable = self.fixture._capacity(name="existing-usable", basis=models.CapacityBasis.ECONOMICALLY_USABLE)
        self.connection.commit()
        result = self.forecast()
        self.assertEqual(1, len(result))
        self.assertEqual(usable.id, result[0].capacity_input.capacity_claim_id)
        self.assertEqual("conflicting_production_milestone_statuses", result.exclusions[0].reason)

    def test_superseded_stale_view_rejected_but_original_historical_view_remains_valid(self):
        original = self.view()
        replacement = models.ClaimVersion("replacement-capacity", self.capacity.series_id, "2026-01-01", LATER,
            models.ClaimKind.DERIVED_ESTIMATE, "fixture-revision", 1.0)
        repository.insert_claim(self.connection, replacement,
            models.CapacityValue("wafer_starts", models.CapacityBasis.ANNOUNCED, "wafers/month", 120, 150, 180),
            dependencies=[models.DependencyLink(self.milestone.id)])
        self.connection.commit()
        self.assertEqual(original, self.view())
        self.assertEqual(1, len(self.forecast(claims=original)))
        current = self.view(recorded_at=LATER)
        self.assertNotIn(self.capacity.id, {row["id"] for row in current})
        with self.assertRaisesRegex(ValueError, "complete database view"):
            self.forecast(recorded_at=LATER, claims=original)
        result = self.forecast(recorded_at=LATER, claims=current)
        self.assertEqual("replacement-capacity", result[0].capacity_input.capacity_claim_id)
        self.assertEqual(150, result[0].capacity_input.base)

    def test_success_preserves_caller_uncommitted_changes_and_savepoints(self):
        committed = self.database_rows()
        self.connection.execute("BEGIN")
        self.connection.execute("SAVEPOINT atlas_forecast_read")
        extra = self.fixture._capacity(name="caller-uncommitted", basis=models.CapacityBasis.ECONOMICALLY_USABLE)
        before = self.database_rows()
        result = self.forecast(claims=self.view())
        self.assertEqual(2, len(result))
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(before, self.database_rows())
        self.assertIn(extra.id, {row["id"] for row in self.view()})
        self.connection.execute("ROLLBACK TO SAVEPOINT atlas_forecast_read")
        self.connection.execute("RELEASE SAVEPOINT atlas_forecast_read")
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()
        self.assertEqual(committed, self.database_rows())

    def test_failure_preserves_caller_uncommitted_changes_for_later_commit(self):
        stale = self.view()
        self.connection.execute("BEGIN")
        extra = self.fixture._capacity(name="caller-pending", basis=models.CapacityBasis.ECONOMICALLY_USABLE)
        before = self.database_rows()
        with self.assertRaisesRegex(ValueError, "complete database view"):
            self.forecast(claims=stale)
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(before, self.database_rows())
        self.connection.commit()
        self.assertIn(extra.id, {row["id"] for row in self.view()})
        self.assertFalse(self.connection.in_transaction)

    def test_engine_failure_rolls_back_only_inner_savepoint(self):
        self.connection.execute("BEGIN")
        self.fixture._capacity(name="caller-before-engine", basis=models.CapacityBasis.ECONOMICALLY_USABLE)
        before = self.database_rows()
        def fail(*args, **kwargs):
            self.fixture._capacity(name="unexpected-engine-write", basis=models.CapacityBasis.ECONOMICALLY_USABLE)
            raise RuntimeError("synthetic engine failure")
        with mock.patch.object(analytics, "_forecast_from_current_view", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic engine failure"):
                self.forecast()
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(before, self.database_rows())
        self.connection.rollback()

    def test_wal_writer_cannot_change_the_snapshot_between_view_and_lineage_reads(self):
        self.assertEqual("wal", self.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0])
        database_path = self.connection.execute("PRAGMA database_list").fetchone()[2]
        writer = sqlite3.connect(database_path)
        writer.row_factory = sqlite3.Row
        writer.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(writer.close)
        original_read = analytics.claim_records
        original_forecast = analytics._forecast_from_current_view
        def read_then_commit_competitor(*args, **kwargs):
            rows = original_read(*args, **kwargs)
            prior_connection = self.fixture.connection
            try:
                self.fixture.connection = writer
                self.fixture._milestone(models.MilestoneStatus.STARTED)
                writer.commit()
            finally:
                self.fixture.connection = prior_connection
            self.assertEqual(3, writer.execute("SELECT COUNT(*) FROM claim_versions").fetchone()[0])
            return rows
        def inspect_snapshot(connection, *args, **kwargs):
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM claim_versions").fetchone()[0])
            return original_forecast(connection, *args, **kwargs)
        with mock.patch.object(analytics, "claim_records", side_effect=read_then_commit_competitor), \
             mock.patch.object(analytics, "_forecast_from_current_view", side_effect=inspect_snapshot):
            self.assertEqual(1, len(self.forecast()))
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(3, self.connection.execute("SELECT COUNT(*) FROM claim_versions").fetchone()[0])
        later_view = self.forecast()
        self.assertEqual(0, len(later_view))
        self.assertEqual({"ambiguous_linked_production_milestones"}, {row.reason for row in later_view.exclusions})


if __name__ == "__main__":
    unittest.main()
