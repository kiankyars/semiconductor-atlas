from __future__ import annotations

import unittest

from semiconductor_atlas.ingest_nist import (
    _money_values,
    _production_timeline_windows,
    _throughput_values,
)


class NISTExtractionTests(unittest.TestCase):
    def test_money_tokens_preserve_qualifiers_and_full_magnitude(self) -> None:
        self.assertEqual(
            [("Nearly $90 billion", 90_000_000_000, "approximate"),
             ("$100+ billion", 100_000_000_000, "lower_bound")],
            _money_values("Nearly $90 billion (part of a $100+ billion plan)"),
        )

    def test_forward_production_extracts_each_explicit_throughput(self) -> None:
        text = (
            "Upon completion, the facility is expected to produce approximately 14,500 "
            "wafers per month and 3,700,000 units per month."
        )
        self.assertEqual(
            [("approximately 14,500 wafers per month", 14_500.0, "wafers", "month"),
             ("3,700,000 units per month", 3_700_000.0, "units", "month")],
            _throughput_values(text),
        )

    def test_production_timeline_maps_vague_window_without_false_precision(self) -> None:
        self.assertEqual(
            [("Mass production expected to begin at the end of 2027", "mass_production",
              "2027-10-01", "2027-12-01", "2027-12-31")],
            _production_timeline_windows(
                "Mass production expected to begin at the end of 2027"
            ),
        )
        self.assertEqual([], _production_timeline_windows("Target completion by 2029"))


if __name__ == "__main__":
    unittest.main()
