from pathlib import Path
import tempfile
import unittest

from semiconductor_atlas.alerts import detect_revision_alerts, serialize_alerts
from semiconductor_atlas.database import initialize
from semiconductor_atlas.models import (
    CapacityBasis,
    CapacityValue,
    ClaimKind,
    ClaimSeries,
    ClaimVersion,
    Entity,
    EntityKind,
    EvidenceLink,
    MilestoneStatus,
    MilestoneValue,
    Source,
    SourceDocument,
    SourceFamily,
    ValueKind,
)
from semiconductor_atlas.repository import (
    add_claim_series,
    add_entity,
    add_source,
    add_source_document,
    add_source_family,
    insert_claim,
    stable_id,
)


NOW = "2026-07-17T12:00:00Z"
EVIDENCE_RETRIEVED_AT = "2026-05-01T00:00:00Z"
ENTITY_CREATED_AT = "2026-05-01T00:01:00Z"


class AlertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.connection, _ = initialize(Path(self.temp.name) / "atlas.sqlite")
        family_id = stable_id("family", "test")
        source_id = stable_id("source", "test")
        document_id = stable_id("document", "test")
        add_source_family(self.connection, SourceFamily(family_id, "test", "Test", EVIDENCE_RETRIEVED_AT))
        add_source(self.connection, Source(source_id, family_id, "test", "Test", "Test", "https://example.com", EVIDENCE_RETRIEVED_AT))
        add_source_document(self.connection, SourceDocument(document_id, source_id, "https://example.com/a", "A", EVIDENCE_RETRIEVED_AT, "a" * 64))
        self.evidence = [EvidenceLink(document_id)]
        self.entity_id = stable_id("entity", "site")
        add_entity(self.connection, Entity(self.entity_id, EntityKind.SITE, "site", ENTITY_CREATED_AT, "Site"))

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _series(self, predicate: str, kind: ValueKind) -> ClaimSeries:
        series = ClaimSeries(stable_id("series", predicate), self.entity_id, predicate, predicate, kind, ENTITY_CREATED_AT)
        add_claim_series(self.connection, series)
        return series

    def _version(self, series: ClaimSeries, name: str, recorded_at: str) -> ClaimVersion:
        return ClaimVersion(stable_id("claim", series.id, name), series.id, "2026-01-01", recorded_at, ClaimKind.RECONCILED_FACT, "review", 0.8)

    def test_capacity_and_milestone_revisions_create_deterministic_alerts(self) -> None:
        capacity = self._series("capacity", ValueKind.CAPACITY)
        first = self._version(capacity, "first", "2026-06-01T00:00:00Z")
        second = self._version(capacity, "second", "2026-07-01T00:00:00Z")
        insert_claim(self.connection, first, CapacityValue("wspm", CapacityBasis.ANNOUNCED, "wafers/month", 80, 100, 120), evidence=self.evidence)
        insert_claim(self.connection, second, CapacityValue("wspm", CapacityBasis.ANNOUNCED, "wafers/month", 55, 60, 70), evidence=self.evidence)

        milestone = self._series("production_start", ValueKind.MILESTONE)
        early = self._version(milestone, "early", "2026-06-02T00:00:00Z")
        late = self._version(milestone, "late", "2026-07-02T00:00:00Z")
        insert_claim(self.connection, early, MilestoneValue("production_start", MilestoneStatus.EXPECTED, "2027-01-01", "2027-03-01", "2027-06-01"), evidence=self.evidence)
        insert_claim(self.connection, late, MilestoneValue("production_start", MilestoneStatus.DELAYED, "2027-07-01", "2027-10-01", "2028-01-01"), evidence=self.evidence)

        alerts = detect_revision_alerts(self.connection)
        self.assertEqual(["capacity_revision", "completion_delay"], [alert.alert_type for alert in alerts])
        self.assertEqual(serialize_alerts(alerts), serialize_alerts(detect_revision_alerts(self.connection)))
        self.assertTrue(all(alert.prior_claim_id and alert.current_claim_id for alert in alerts))

    def test_small_changes_do_not_alert(self) -> None:
        series = self._series("capacity", ValueKind.CAPACITY)
        first = self._version(series, "first", "2026-06-01T00:00:00Z")
        second = self._version(series, "second", "2026-07-01T00:00:00Z")
        insert_claim(self.connection, first, CapacityValue("wspm", CapacityBasis.ANNOUNCED, "wafers/month", 90, 100, 110), evidence=self.evidence)
        insert_claim(self.connection, second, CapacityValue("wspm", CapacityBasis.ANNOUNCED, "wafers/month", 92, 105, 115), evidence=self.evidence)
        self.assertEqual([], detect_revision_alerts(self.connection))

    def test_cancelled_milestone_alerts_even_when_date_is_unchanged(self) -> None:
        milestone = self._series("production_start", ValueKind.MILESTONE)
        expected = self._version(milestone, "expected", "2026-06-01T00:00:00Z")
        cancelled = self._version(milestone, "cancelled", "2026-07-01T00:00:00Z")
        dates = ("2027-01-01", "2027-03-01", "2027-06-01")
        insert_claim(
            self.connection,
            expected,
            MilestoneValue("production_start", MilestoneStatus.EXPECTED, *dates),
            evidence=self.evidence,
        )
        insert_claim(
            self.connection,
            cancelled,
            MilestoneValue("production_start", MilestoneStatus.CANCELLED, *dates),
            evidence=self.evidence,
        )

        alerts = detect_revision_alerts(self.connection)
        self.assertEqual(1, len(alerts))
        self.assertEqual("project_cancellation", alerts[0].alert_type)
        self.assertEqual("high", alerts[0].severity)
        self.assertEqual(
            ["expected", "cancelled"],
            alerts[0].reasoning["status_transition"],
        )

    def test_delayed_status_alerts_even_when_date_is_unchanged(self) -> None:
        milestone = self._series("delayed_production_start", ValueKind.MILESTONE)
        expected = self._version(milestone, "expected", "2026-06-01T00:00:00Z")
        delayed = self._version(milestone, "delayed", "2026-07-01T00:00:00Z")
        dates = ("2027-01-01", "2027-03-01", "2027-06-01")
        insert_claim(
            self.connection,
            expected,
            MilestoneValue("production_start", MilestoneStatus.EXPECTED, *dates),
            evidence=self.evidence,
        )
        insert_claim(
            self.connection,
            delayed,
            MilestoneValue("production_start", MilestoneStatus.DELAYED, *dates),
            evidence=self.evidence,
        )

        alerts = detect_revision_alerts(self.connection)
        self.assertEqual(1, len(alerts))
        self.assertEqual("completion_delay", alerts[0].alert_type)
        self.assertEqual("medium", alerts[0].severity)


if __name__ == "__main__":
    unittest.main()
