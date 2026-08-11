from __future__ import annotations

import unittest

from semiconductor_atlas.coverage import coverage_report
from semiconductor_atlas.database import initialize
from semiconductor_atlas.models import (
    ClaimKind,
    ClaimSeries,
    ClaimVersion,
    Entity,
    EntityKind,
    EvidenceLink,
    ScalarType,
    ScalarValue,
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


class CoverageTests(unittest.TestCase):
    def test_empty_database_reports_all_five_bases_and_explicit_gaps(self) -> None:
        connection, _ = initialize(":memory:")
        report = coverage_report(
            connection,
            as_of="2026-07-17",
            recorded_at="2026-07-17T12:00:00Z",
        )
        self.assertEqual(
            {
                "announced": 0,
                "physical_construction": 0,
                "tool_installed": 0,
                "qualified": 0,
                "economically_usable": 0,
            },
            report["capacity_claims_by_basis"],
        )
        self.assertEqual(0.0, report["geometry_coverage_fraction"])
        self.assertIsNone(report["comparative_coverage_claim"])
        self.assertTrue(any("not a global census" in item for item in report["known_gaps"]))
        connection.close()

    def test_epa_frs_namespace_and_limitations_are_explicit(self) -> None:
        connection, _ = initialize(":memory:")
        recorded_at = "2026-07-19T12:00:00Z"
        family_id = stable_id("source-family", "epa-frs")
        source_id = stable_id(
            "source",
            "epa-frs-national-single:epa-frs-semiconductor-direct-v1",
        )
        document_id = stable_id("document", source_id, "fixture")
        add_source_family(
            connection,
            SourceFamily(
                family_id,
                "epa-frs",
                "EPA Facility Registry Service",
                recorded_at,
            ),
        )
        add_source(
            connection,
            Source(
                source_id,
                family_id,
                "epa-frs-national-single:epa-frs-semiconductor-direct-v1",
                "EPA FRS National Single File semiconductor direct-code candidates",
                "U.S. Environmental Protection Agency",
                "https://ordsext.epa.gov/FLA/www3/state_files/national_single.zip",
                recorded_at,
                license="https://edg.epa.gov/EPA_Data_License.html",
            ),
        )
        add_source_document(
            connection,
            SourceDocument(
                document_id,
                source_id,
                "https://ordsext.epa.gov/FLA/www3/state_files/national_single.zip",
                "EPA FRS National Single File fixture",
                recorded_at,
                "a" * 64,
                media_type="application/zip",
                license="https://edg.epa.gov/EPA_Data_License.html",
            ),
        )
        entity_id = stable_id("entity", "epa:frs:110000000001")
        add_entity(
            connection,
            Entity(
                entity_id,
                EntityKind.SITE,
                "epa:frs:110000000001",
                recorded_at,
                "FRS fixture candidate",
            ),
        )
        series = ClaimSeries(
            stable_id("series", entity_id, "candidate_classification"),
            entity_id,
            "epa-frs:110000000001:candidate_classification",
            "candidate_classification",
            ValueKind.SCALAR,
            recorded_at,
        )
        add_claim_series(connection, series)
        insert_claim(
            connection,
            ClaimVersion(
                stable_id("claim", series.id),
                series.id,
                "2026-07-19",
                recorded_at,
                ClaimKind.SOURCE_STATEMENT,
                "epa_frs_semiconductor_direct_v1",
                0.5,
            ),
            ScalarValue(ScalarType.TEXT, "semiconductor_facility_candidate"),
            evidence=[EvidenceLink(document_id)],
        )

        report = coverage_report(
            connection,
            as_of="2026-07-19",
            recorded_at=recorded_at,
        )
        self.assertEqual({"epa_frs": 1}, report["entity_namespace_counts"])
        gaps = "\n".join(report["known_gaps"])
        for expected in (
            "NAICS 334413",
            "SIC 3674",
            "adjacent NAICS",
            "no global recall denominator",
            "NAD83",
            "no CRS transform",
            "Registry ID merge",
            "closure",
        ):
            self.assertIn(expected, gaps)
        connection.close()


if __name__ == "__main__":
    unittest.main()
