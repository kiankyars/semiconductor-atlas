from pathlib import Path
import unittest

from semiconductor_atlas.adapters.nist_awards import parse_award_files


FIXTURE = Path(__file__).parent / "fixtures" / "nist" / "awards.html"


class NISTAwardsParserTests(unittest.TestCase):
    def test_filters_to_chips_program_office_and_preserves_source_wording(self) -> None:
        records = parse_award_files([FIXTURE])

        self.assertEqual(2, len(records))
        self.assertTrue(all(record.chips_organization == "CHIPS Program Office" for record in records))
        self.assertEqual("Texas Instruments", records[0].recipient)
        self.assertEqual("Sherman", records[0].locality)
        self.assertEqual("TX", records[0].region)
        self.assertEqual(900_000_000, records[0].amount.amount_usd)
        self.assertEqual("reported_amount", records[0].amount.qualifier)

    def test_extracts_only_explicit_capabilities(self) -> None:
        records = parse_award_files([FIXTURE])

        texas, infinera = records
        self.assertEqual((300,), texas.wafer_sizes_mm)
        self.assertEqual((65.0, 130.0), texas.process_nodes_nm)
        self.assertIn("wafer_fabrication", texas.facility_activities)
        self.assertEqual(40_000, infinera.cleanroom_area_ft2)
        self.assertIn("photonics", infinera.facility_activities)
        self.assertIn("indium_phosphide", infinera.technologies)

    def test_shared_proposed_amount_is_not_treated_as_site_allocation(self) -> None:
        record = parse_award_files([FIXTURE])[1]

        self.assertEqual(93_000_000, record.amount.amount_usd)
        self.assertEqual("proposed_up_to", record.amount.qualifier)
        self.assertEqual("multi_site_shared", record.amount.allocation_scope)


if __name__ == "__main__":
    unittest.main()

