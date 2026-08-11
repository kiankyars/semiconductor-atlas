from pathlib import Path
import unittest

from semiconductor_atlas.adapters.osm import (
    is_explicit_semiconductor,
    parse_overpass_file,
)


FIXTURE = Path(__file__).parent / "fixtures" / "osm" / "semiconductor.json"


class OpenStreetMapParserTests(unittest.TestCase):
    def test_requires_an_explicit_production_signal(self) -> None:
        self.assertFalse(is_explicit_semiconductor({"name": "Semiconductor Campus"}))
        self.assertFalse(is_explicit_semiconductor({"product": "integrated_circuits"}))
        self.assertTrue(
            is_explicit_semiconductor({"man_made": "works", "product": "integrated_circuits"})
        )
        self.assertFalse(
            is_explicit_semiconductor(
                {"amenity": "parking", "industrial": "semiconductor"}
            )
        )

    def test_parses_geometry_without_inferring_operations(self) -> None:
        records = parse_overpass_file(FIXTURE)

        self.assertEqual([101, 202, 505], [record.element_id for record in records])
        self.assertEqual("unknown", records[0].lifecycle)
        self.assertEqual("under_construction", records[1].lifecycle)
        self.assertEqual("Polygon", records[1].geometry["type"])
        self.assertAlmostEqual(43.05, records[1].latitude)
        self.assertAlmostEqual(-83.95, records[1].longitude)
        self.assertEqual(("photomask",), records[2].facility_activities)

    def test_preserves_osm_element_url(self) -> None:
        record = parse_overpass_file(FIXTURE)[0]

        self.assertEqual("https://www.openstreetmap.org/node/101", record.source_url)
        self.assertEqual("2026-07-01T00:00:00Z", record.published_at)

    def test_explicit_negative_lifecycle_tags_do_not_promote_candidate(self) -> None:
        from semiconductor_atlas.adapters.osm import infer_lifecycle

        self.assertEqual("unknown", infer_lifecycle({"construction": "no"})[0])
        self.assertEqual("unknown", infer_lifecycle({"proposed": "false"})[0])


if __name__ == "__main__":
    unittest.main()
