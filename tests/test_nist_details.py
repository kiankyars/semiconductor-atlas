from pathlib import Path
import unittest

from semiconductor_atlas.adapters.nist_details import parse_detail_file, parse_detail_html


FIXTURE = Path(__file__).parent / "fixtures" / "nist" / "detail.html"


class NISTDetailParserTests(unittest.TestCase):
    def test_extracts_source_visible_identity_dates_and_description(self) -> None:
        detail = parse_detail_file(FIXTURE)

        self.assertEqual(
            "https://www.nist.gov/chips/texas-instruments-texas-sherman",
            detail.canonical_url,
        )
        self.assertEqual("Texas Instruments (Texas)", detail.title)
        self.assertEqual(("Texas Instruments",), detail.recipients)
        self.assertEqual(("Sherman, Texas", "Lehi, Utah"), detail.locations)
        self.assertEqual("2024-08-14T14:26-04:00", detail.published_at)
        self.assertEqual("2026-01-20T15:02-05:00", detail.updated_at)
        self.assertEqual("Project Summary", detail.project_description.label)
        self.assertIn("up to $1.61 billion", detail.project_description.source_text)
        self.assertIn("three new 300-mm fabs", detail.project_description.source_text)

    def test_keeps_aggregate_and_site_disclosures_separate(self) -> None:
        detail = parse_detail_file(FIXTURE)

        self.assertEqual(2, len(detail.direct_funding))
        aggregate, site = detail.direct_funding
        self.assertEqual("Texas Instruments Project Overview", aggregate.scope)
        self.assertEqual("$1.61 billion", aggregate.source_text)
        self.assertEqual("unqualified", aggregate.qualifier)
        self.assertEqual("Project Statistics: Sherman, Texas", site.scope)
        self.assertEqual("$900 million", site.source_text)
        self.assertEqual("unqualified", site.qualifier)
        self.assertNotEqual(aggregate.source_text, site.source_text)

        self.assertEqual("$18 billion through the end of the decade", detail.expected_capex[0].source_text)
        self.assertEqual(2, len(detail.jobs))
        self.assertEqual("Final Award", detail.award_stage.source_text)

    def test_preserves_explicit_capability_and_timeline_wording(self) -> None:
        detail = parse_detail_file(FIXTURE)

        by_label = {disclosure.label: disclosure for disclosure in detail.capabilities}
        self.assertEqual(
            "Construction of two high-volume 300-mm fabrication facilities",
            by_label["Project Type"].source_text,
        )
        self.assertEqual(
            "65nm – 130nm analog and embedded processing chips",
            by_label["Technology"].source_text,
        )
        self.assertEqual(
            "Production expected by the end of the decade",
            by_label["Project Timeline"].source_text,
        )
        self.assertTrue(
            all(item.scope == "Project Statistics: Sherman, Texas" for item in detail.capabilities)
        )

    def test_classifies_only_literal_funding_qualifiers(self) -> None:
        raw = """
        <html><body>
          <table>
            <tr><th colspan="2">Project Overview</th></tr>
            <tr><th>Direct Funding Amount</th><td>Up to $93 million in proposed funding</td></tr>
            <tr><th>Direct Funding</th><td>Final award of $20 million</td></tr>
          </table>
          <div class="nist-field__label">Application Stage</div>
          <div class="nist-field__item">Final Award</div>
        </body></html>
        """

        detail = parse_detail_html(raw)

        self.assertEqual("proposed_up_to", detail.direct_funding[0].qualifier)
        self.assertEqual("final", detail.direct_funding[1].qualifier)
        self.assertEqual("Final Award", detail.award_stage.source_text)

    def test_does_not_split_or_infer_unlabeled_site_allocations(self) -> None:
        raw = """
        <html><body>
          <h1>Example recipient</h1>
          <h2>Project Summary</h2>
          <p>Up to $100 million is shared between Alpha, AZ and Beta, NM.</p>
          <table>
            <tr><th colspan="2">Project Overview</th></tr>
            <tr><th>Recipient</th><td>Example recipient</td></tr>
            <tr><th>Location(s)</th><td>Alpha, AZ; Beta, NM</td></tr>
            <tr><th>Direct Funding Amount</th><td>Up to $100 million</td></tr>
          </table>
        </body></html>
        """

        detail = parse_detail_html(raw)

        self.assertEqual(("Alpha, AZ; Beta, NM",), detail.locations)
        self.assertEqual(1, len(detail.direct_funding))
        self.assertEqual("up_to", detail.direct_funding[0].qualifier)
        self.assertEqual("Project Overview", detail.direct_funding[0].scope)


if __name__ == "__main__":
    unittest.main()
