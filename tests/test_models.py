from __future__ import annotations

import unittest

from semiconductor_atlas.models import (
    CapabilityValue,
    CapacityBasis,
    CapacityValue,
    ClaimKind,
    ClaimVersion,
    ConstraintSeverity,
    ConstraintStatus,
    ConstraintValue,
    EntityKind,
    GeometryValue,
    MilestoneStatus,
    MilestoneValue,
    ResourceValue,
    ScalarType,
    ScalarValue,
    SourceDocument,
    ValueKind,
)


class ModelTests(unittest.TestCase):
    def test_entity_kinds_match_the_canonical_ontology(self) -> None:
        self.assertEqual(
            {kind.value for kind in EntityKind},
            {
                "organization",
                "site",
                "facility",
                "building",
                "production_unit",
                "project",
                "infrastructure_asset",
            },
        )

    def test_capacity_bases_are_exact_and_non_collapsible(self) -> None:
        self.assertEqual(
            [basis.value for basis in CapacityBasis],
            [
                "announced",
                "physical_construction",
                "tool_installed",
                "qualified",
                "economically_usable",
            ],
        )

    def test_claim_kinds_match_the_epistemic_contract(self) -> None:
        self.assertEqual(
            {kind.value for kind in ClaimKind},
            {
                "source_statement",
                "direct_observation",
                "reconciled_fact",
                "derived_estimate",
            },
        )

    def test_constraint_states_match_the_epistemic_contract(self) -> None:
        self.assertEqual(
            {status.value for status in ConstraintStatus},
            {"potential", "binding", "mitigated", "resolved"},
        )

    def test_capacity_and_resource_ranges_are_ordered(self) -> None:
        value = CapacityValue(
            metric="wafer_starts_per_month",
            basis=CapacityBasis.QUALIFIED,
            unit="300mm wafers/month",
            low=38_000,
            base=42_000,
            high=46_000,
        )
        self.assertEqual(value.kind, ValueKind.CAPACITY)
        with self.assertRaisesRegex(ValueError, "0 <= low <= base <= high"):
            CapacityValue(
                metric="wafer_starts_per_month",
                basis=CapacityBasis.ANNOUNCED,
                unit="wafers/month",
                low=10,
                base=5,
                high=12,
            )
        with self.assertRaisesRegex(ValueError, "0 <= low <= base <= high"):
            ResourceValue(
                resource_type="water",
                unit="m3/day",
                low=-1,
                base=1,
                high=2,
            )

    def test_scalar_types_reject_ambiguous_python_values(self) -> None:
        self.assertEqual(ScalarValue(ScalarType.INTEGER, 300).kind, ValueKind.SCALAR)
        self.assertEqual(ScalarValue(ScalarType.BOOLEAN, False).value, False)
        ScalarValue(ScalarType.DATE, "2026-07-17")
        ScalarValue(ScalarType.TIMESTAMP, "2026-07-17T12:00:00Z")
        with self.assertRaisesRegex(ValueError, "integer"):
            ScalarValue(ScalarType.INTEGER, True)
        with self.assertRaisesRegex(ValueError, "timezone"):
            ScalarValue(ScalarType.TIMESTAMP, "2026-07-17T12:00:00")

    def test_geometry_requires_supported_finite_wgs84_coordinates(self) -> None:
        geometry = GeometryValue(
            {"type": "Point", "coordinates": [-112.15, 33.45]}, precision_m=10
        )
        self.assertEqual(geometry.kind, ValueKind.GEOMETRY)
        with self.assertRaisesRegex(ValueError, "WGS84"):
            GeometryValue({"type": "Point", "coordinates": [200, 33.45]})
        with self.assertRaisesRegex(ValueError, "unsupported"):
            GeometryValue({"type": "GeometryCollection", "coordinates": []})
        with self.assertRaisesRegex(ValueError, "position"):
            GeometryValue({"type": "Point", "coordinates": [[1, 2], [3, 4]]})
        with self.assertRaisesRegex(ValueError, "closed"):
            GeometryValue(
                {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}
            )

    def test_milestone_dates_are_ordered(self) -> None:
        MilestoneValue(
            milestone_type="volume_production",
            status=MilestoneStatus.EXPECTED,
            date_low="2027-01-01",
            date_base="2027-04-01",
            date_high="2027-07-01",
        )
        with self.assertRaisesRegex(ValueError, "low <= base <= high"):
            MilestoneValue(
                milestone_type="volume_production",
                status=MilestoneStatus.DELAYED,
                date_low="2027-07-01",
                date_base="2027-04-01",
                date_high="2027-09-01",
            )

    def test_claim_version_validates_known_time_and_confidence(self) -> None:
        ClaimVersion(
            id="claim:1",
            series_id="series:1",
            valid_from="2026-01-01",
            valid_to="2027-01-01",
            recorded_at="2026-07-17T12:00:00Z",
            claim_kind=ClaimKind.RECONCILED_FACT,
            method="analyst_reconciliation",
            confidence=0.8,
        )
        with self.assertRaisesRegex(ValueError, "confidence"):
            ClaimVersion(
                id="claim:2",
                series_id="series:1",
                valid_from="2026-01-01",
                recorded_at="2026-07-17T12:00:00Z",
                claim_kind=ClaimKind.DERIVED_ESTIMATE,
                method="model",
                confidence=1.1,
            )
        with self.assertRaisesRegex(ValueError, "valid_to"):
            ClaimVersion(
                id="claim:3",
                series_id="series:1",
                valid_from="2026-01-01",
                valid_to="2025-01-01",
                recorded_at="2026-07-17T12:00:00Z",
                claim_kind=ClaimKind.SOURCE_STATEMENT,
                method="extraction",
                confidence=0.9,
            )

    def test_source_document_requires_url_timestamp_and_hash(self) -> None:
        document = SourceDocument(
            id="document:1",
            source_id="source:1",
            document_url="https://example.com/permit.pdf",
            title="Air permit",
            published_at="2026-06-01",
            retrieved_at="2026-07-17T12:00:00Z",
            content_sha256="a" * 64,
            metadata={"page": 12},
        )
        self.assertEqual(document.content_sha256, "a" * 64)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            SourceDocument(
                id="document:2",
                source_id="source:1",
                document_url="https://example.com/permit.pdf",
                title="Air permit",
                retrieved_at="2026-07-17T12:00:00Z",
                content_sha256="not-a-hash",
            )

    def test_all_domain_values_expose_their_value_kind(self) -> None:
        self.assertEqual(
            CapabilityValue("process_node", 3.0, unit="nm").kind,
            ValueKind.CAPABILITY,
        )
        self.assertEqual(
            ConstraintValue(
                constraint_type="water",
                status=ConstraintStatus.BINDING,
                severity=ConstraintSeverity.HIGH,
                description="Permit caps daily withdrawal",
            ).kind,
            ValueKind.CONSTRAINT,
        )


if __name__ == "__main__":
    unittest.main()
