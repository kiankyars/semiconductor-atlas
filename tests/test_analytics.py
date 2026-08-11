from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from semiconductor_atlas.analytics import (
    forecast_csv,
    forecast_current_capacity,
    forecast_summary,
)
from semiconductor_atlas.database import initialize
from semiconductor_atlas.models import (
    CapacityBasis,
    CapacityValue,
    ClaimKind,
    ClaimSeries,
    ClaimVersion,
    DependencyLink,
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


CREATED_AT = "2026-01-01T00:00:00Z"
RECORDED_AT = "2026-07-18T00:00:00Z"


class ForecastBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.connection, _ = initialize(Path(self.temporary.name) / "atlas.sqlite")
        family_id = stable_id("family", "analytics")
        source_id = stable_id("source", "analytics")
        self.document_id = stable_id("document", "analytics")
        add_source_family(
            self.connection,
            SourceFamily(family_id, "analytics", "Analytics fixture", CREATED_AT),
        )
        add_source(
            self.connection,
            Source(
                source_id,
                family_id,
                "analytics",
                "Analytics fixture",
                "Fixture",
                "https://example.test",
                CREATED_AT,
            ),
        )
        add_source_document(
            self.connection,
            SourceDocument(
                self.document_id,
                source_id,
                "https://example.test/source",
                "Source",
                CREATED_AT,
                "b" * 64,
            ),
        )
        self.entity_id = stable_id("entity", "analytics")
        add_entity(
            self.connection,
            Entity(self.entity_id, EntityKind.PROJECT, "analytics", CREATED_AT, "Project"),
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def _series(self, name: str, kind: ValueKind) -> ClaimSeries:
        series = ClaimSeries(
            stable_id("series", name),
            self.entity_id,
            name,
            name,
            kind,
            CREATED_AT,
        )
        add_claim_series(self.connection, series)
        return series

    def _milestone(self, status: MilestoneStatus) -> ClaimVersion:
        series = self._series(f"milestone-{status.value}", ValueKind.MILESTONE)
        version = ClaimVersion(
            stable_id("claim", series.id),
            series.id,
            "2026-01-01",
            RECORDED_AT,
            ClaimKind.SOURCE_STATEMENT,
            "fixture",
            1.0,
        )
        insert_claim(
            self.connection,
            version,
            MilestoneValue(
                "production_start",
                status,
                "2027-01-01",
                "2027-04-01",
                "2027-07-01",
            ),
            evidence=[EvidenceLink(self.document_id)],
        )
        return version

    def _capacity(
        self,
        *,
        name: str,
        basis: CapacityBasis,
        unit: str = "wafers/month",
        metric: str = "wafer_starts",
        period_start: str | None = None,
        period_end: str | None = None,
        milestone: ClaimVersion | None = None,
    ) -> ClaimVersion:
        series = self._series(name, ValueKind.CAPACITY)
        version = ClaimVersion(
            stable_id("claim", series.id),
            series.id,
            "2026-01-01",
            RECORDED_AT,
            ClaimKind.DERIVED_ESTIMATE if milestone else ClaimKind.SOURCE_STATEMENT,
            "fixture",
            1.0,
        )
        lineage = (
            {"dependencies": [DependencyLink(milestone.id)]}
            if milestone
            else {"evidence": [EvidenceLink(self.document_id)]}
        )
        insert_claim(
            self.connection,
            version,
            CapacityValue(
                metric,
                basis,
                unit,
                80,
                100,
                120,
                period_start,
                period_end,
            ),
            **lineage,
        )
        return version

    def _forecast(self):
        return forecast_current_capacity(
            self.connection,
            as_of="2026-07-17",
            recorded_at=RECORDED_AT,
            forecast_start=date(2026, 7, 1),
        )

    def test_requires_one_explicitly_linked_active_milestone(self) -> None:
        milestone = self._milestone(MilestoneStatus.EXPECTED)
        capacity = self._capacity(
            name="linked-capacity",
            basis=CapacityBasis.ANNOUNCED,
            milestone=milestone,
        )

        selection = self._forecast()

        self.assertEqual(1, len(selection))
        self.assertEqual((), selection.exclusions)
        self.assertEqual(1, len(selection.pairings))
        self.assertEqual("claim_lineage", selection.pairings[0].method)
        self.assertEqual(capacity.id, selection[0].capacity_input.capacity_claim_id)
        self.assertEqual(milestone.id, selection[0].capacity_input.production_start.milestone_claim_id)

    def test_cancelled_relative_and_expired_inputs_are_excluded_with_reasons(self) -> None:
        cancelled = self._milestone(MilestoneStatus.CANCELLED)
        self._capacity(
            name="cancelled-capacity",
            basis=CapacityBasis.ANNOUNCED,
            milestone=cancelled,
        )
        self._capacity(
            name="relative-capacity",
            basis=CapacityBasis.ANNOUNCED,
            unit="percent",
        )
        self._capacity(
            name="expired-capacity",
            basis=CapacityBasis.ECONOMICALLY_USABLE,
            period_end="2026-06-30",
        )

        selection = self._forecast()
        reasons = {item.reason for item in selection.exclusions}

        self.assertEqual(0, len(selection))
        self.assertEqual(
            {
                "cancelled_production_milestone",
                "expired_capacity_period",
                "relative_capacity_not_forecastable",
            },
            reasons,
        )
        summary = json.loads(forecast_summary(selection))
        self.assertEqual(3, summary["excluded_capacity_inputs"])
        self.assertEqual(3, sum(summary["exclusion_reason_counts"].values()))

    def test_relative_unit_variants_are_excluded(self) -> None:
        for index, unit in enumerate(("x", "times", "pct", "percentage points")):
            self._capacity(
                name=f"relative-capacity-{index}",
                basis=CapacityBasis.ANNOUNCED,
                unit=unit,
            )

        selection = self._forecast()

        self.assertEqual(0, len(selection))
        self.assertEqual(4, len(selection.exclusions))
        self.assertEqual(
            {"relative_capacity_not_forecastable"},
            {item.reason for item in selection.exclusions},
        )

    def test_forecast_csv_escapes_source_derived_formula_text(self) -> None:
        self._capacity(
            name="formula-capacity",
            basis=CapacityBasis.ECONOMICALLY_USABLE,
            metric="=SUM(A1:A2)",
            unit="@malicious",
        )
        selection = self._forecast()

        rows = list(csv.DictReader(io.StringIO(forecast_csv(selection).decode("utf-8"))))

        self.assertEqual(20, len(rows))
        self.assertEqual("'=SUM(A1:A2)", rows[0]["metric"])
        self.assertEqual("'@malicious", rows[0]["unit"])


if __name__ == "__main__":
    unittest.main()
