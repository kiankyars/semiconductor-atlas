from __future__ import annotations

from hashlib import sha256
import unittest
from unittest.mock import patch

from semiconductor_atlas import source_target_detector as detector
from semiconductor_atlas import source_statement_review as statements


def _page(title, content):
    return ("<!doctype html><html><body><h1>" + title + "</h1>" + content + "</body></html>").encode()


def _row(label, value):
    return f"<tr><td><strong>{label}</strong></td><td>{value}</td></tr>"


def _table(title, rows):
    return f"<table><thead><tr><th><h3>{title}</h3></th></tr></thead><tbody>{rows}</tbody></table>"


def tsmc_html(second="in 2028", *, modern=False, timeline_second=None):
    """Synthetic project structure; calendar inputs are not supplied to analyze()."""
    timeline_second = timeline_second or second
    clause = f"production in the second fab targeted for {second}" if modern else f"production beginning in the second fab {second}"
    content = '<div class="text-with-summary"><h2>Project Summary</h2><p>TSMC Arizona plans three greenfield leading-edge fabs in Phoenix, Arizona.</p>'
    content += '<h2>Economic and National Security Impact</h2><p>TSMC Arizona is on track to begin high-volume production in the first fab by the first half of 2025, with '
    content += clause + ' and in the third by the end of the decade.</p><h2>Financial and Commercial Terms</h2>'
    content += _table("Project Statistics: TSMC Arizona Corporation", _row("Location(s)", "Phoenix, Arizona")
        + _row("Project Type", "Construction of three greenfield leading-edge fabs")
        + _row("Project Timeline", '<strong>Fab 1</strong>: On track to start production in first half of 2025<br>'
            + '<strong>Fab 2</strong>: Expected to begin production ' + timeline_second
            + '<br><strong>Fab 3</strong>: Expected to begin production by the end of the decade')) + '</div>'
    return _page("TSMC Arizona", content)


def samsung_html(date="by 2030"):
    content = '<div class="text-with-summary"><h2>Project Summary</h2><p>Samsung Texas award project.</p><h2>Economic and National Security Impact</h2>'
    content += '<p>This project includes two new leading-edge logic fabs and an R&amp;D fab in Taylor, as well as an expansion to the company’s existing Austin facility.</p>'
    content += '<h2>Financial and Commercial Terms</h2>' + _table("Samsung Project Overview",
        _row("Location(s)", "Taylor, Texas<br>Austin, Texas")
        + _row("Project Type", "<p>2 leading-edge logic fabs, and an R&amp;D fab</p><p>Expansion of existing facility</p>")
        + _row("Project Timeline", "All facilities are expected to be operational " + date)) + '</div>'
    return _page("Samsung Electronics (Texas)", content)


def amkor_html(date="at the end of 2027"):
    content = '<div class="text-with-summary"><h2>Project Summary</h2><p>Amkor will build a new advanced packaging and test facility in Peoria, Arizona.</p>'
    content += '<h2>Economic and National Security Impact</h2><p>Advanced packaging for AI.</p><h2>Workforce and Community Impact</h2><p>Manufacturing jobs.</p>'
    content += '<h2>Environmental and Worker Safety Commitments</h2><p>By 2050, emissions will be reduced.</p><h2>Financial and Commercial Terms</h2>'
    content += _table("Amkor Technology Project Overview", _row("Location(s)", "Peoria, Arizona"))
    content += _table("Project Statistics: Peoria, Arizona", _row("Project Type", "Construction of a new manufacturing facility for advanced production lines to interconnect, protect and test integrated circuits")
        + _row("Project Timeline", "Mass production expected to begin " + date)) + '</div>'
    return _page("Amkor Technology, Inc. (Arizona)", content)


def company_html(date="in 2028"):
    content = '<div class="blog_main_tier blog_content_tier"><p>Our advanced semiconductor packaging and test campus in Peoria, Arizona is a synthetic fixture.</p>'
    content += '<p><strong>Supporting the future of U.S. semiconductor manufacturing</strong><br>Phase One of the Amkor campus is expected to open '
    content += date + ', and the facility is anticipated to become one of the largest advanced semiconductor packaging facilities in the world. This synthetic tail retains context.</p></div>'
    return _page("Blog", content)


class SourceTargetDetectorTests(unittest.TestCase):
    def assert_abstain(self, url, before, after):
        result = detector.analyze(url, before, after)
        self.assertEqual("abstain", result["result"], result)
        self.assertEqual([], result["subjects"])
        return result

    def test_machine_extracts_and_bundles_three_tsmc_subjects_six_formulations(self):
        result = detector.analyze(detector.TSMC, tsmc_html(), tsmc_html("the second half in 2027", modern=True, timeline_second="in second half of 2027"))
        self.assertEqual("revision_candidate", result["result"])
        self.assertEqual((3, 6), (result["coverage"]["after"]["subject_count"], result["coverage"]["after"]["target_count"]))
        self.assertEqual(["unchanged", "revision_candidate", "unchanged"], [row["status"] for row in result["subjects"]])
        changed = result["subjects"][1]
        self.assertEqual("TSMC Arizona second fab", changed["source_native_subject"])
        self.assertEqual(["target_literal_changed", "target_literal_changed"], [row["status"] for row in changed["formulations"]])
        third = result["subjects"][2]["formulations"][0]
        self.assertEqual("planned production", third["milestone"])
        self.assertEqual("by the end of the decade", third["after"]["literal"])

    def test_equal_bodies_still_require_successful_project_parsing(self):
        valid = tsmc_html()
        self.assertEqual("no_candidate", detector.analyze(detector.TSMC, valid, valid)["result"])
        invalid = _page("TSMC Arizona", "<p>We cannot load this page.</p>")
        self.assert_abstain(detector.TSMC, invalid, invalid)

    def test_samsung_operational_deadline_is_award_wide(self):
        result = detector.analyze(detector.SAMSUNG, samsung_html(), samsung_html("by 2031"))
        self.assertEqual("revision_candidate", result["result"])
        evidence = result["subjects"][0]["formulations"][0]["after"]
        self.assertIn("Austin expansion", evidence["scope"])
        self.assertIn("not Taylor-only", evidence["scope"])
        self.assertEqual("planned operational state", evidence["predicate"])

    def test_amkor_nist_and_company_milestones_and_scopes_remain_distinct(self):
        for url, builder, literal, milestone in ((detector.AMKOR, amkor_html, "at the end of 2028", "planned mass-production start"),
                                                (detector.AMKOR_COMPANY, company_html, "in 2029", "planned opening")):
            with self.subTest(url=url):
                result = detector.analyze(url, builder(), builder(literal))
                self.assertEqual("revision_candidate", result["result"])
                self.assertEqual(milestone, result["subjects"][0]["formulations"][0]["milestone"])
        self.assert_abstain(detector.AMKOR, amkor_html(), company_html())

    def test_unknown_and_configured_unsupported_urls_explicitly_abstain(self):
        for url in (detector.SAMSUNG.replace("austin", "taylor"), detector.TSMC + "?preview=true",
                    "https://example.com/project", "https://amkor.com/blog/amkor-semiconductor-packaging-facility-peoria-arizona/",
                    "https://www.chandleraz.gov/business/economic-development/key-industries-and-employers/advanced-manufacturing",
                    "https://www.mti.gov.sg/newsroom/speech-by-dpm-and-minister-for-trade-and-industry-gan-kim-yong-at-the-groundbreaking-ceremony-of-micron-s-hbm-manufacturing-facility/"):
            with self.subTest(url=url):
                self.assertEqual("unsupported_exact_url", self.assert_abstain(url, tsmc_html(), tsmc_html())["reason"])

    def test_unchanged_navigation_or_copyright_is_not_a_target_revision(self):
        before = tsmc_html().replace(b"<body>", b"<body><nav>Copyright 2026</nav>")
        after = before.replace(b"Copyright 2026", b"Copyright 2027")
        self.assertEqual("no_candidate", detector.analyze(detector.TSMC, before, after)["result"])

    def test_markup_only_target_changes_do_not_generate_candidates(self):
        before = tsmc_html()
        after = before.replace(b"in 2028", b"in <em>2028</em>")
        self.assertEqual("no_candidate", detector.analyze(detector.TSMC, before, after)["result"])

    def test_date_relation_change_is_retained_without_date_coercion(self):
        result = detector.analyze(detector.SAMSUNG, samsung_html("by 2030"), samsung_html("in 2030"))
        self.assertEqual("revision_candidate", result["result"])
        self.assertEqual("in 2030", result["subjects"][0]["formulations"][0]["after"]["literal"])
        self.assertNotIn("confidence", str(result))
        self.assertNotIn("midpoint", str(result))

    def test_reverse_calendar_revision_is_also_only_a_source_candidate(self):
        result = detector.analyze(detector.TSMC, tsmc_html("in 2027"), tsmc_html("in 2029"))
        self.assertEqual("revision_candidate", result["result"])
        self.assertTrue(all(value is False for value in result["boundaries"].values()))

    def test_changed_project_scope_abstains_even_if_targets_unchanged(self):
        original = tsmc_html()
        changed = original.replace(b"three greenfield", b"four greenfield")
        self.assert_abstain(detector.TSMC, original, changed)
        original = samsung_html()
        self.assert_abstain(detector.SAMSUNG, original, original.replace(b"2 leading-edge logic fabs", b"1 leading-edge logic fab"))

    def test_changed_scope_context_is_not_silently_treated_as_no_candidate(self):
        original = tsmc_html()
        changed = original.replace(b"TSMC Arizona plans three", b"TSMC Arizona may plan three")
        result = self.assert_abstain(detector.TSMC, original, changed)
        self.assertEqual("source_scope_or_context_changed", result["reason"])

    def test_negated_or_changed_predicate_abstains_with_same_date(self):
        before = tsmc_html()
        for phrase in (b"not on track to begin", b"on track to cease", b"scheduled to begin"):
            with self.subTest(phrase=phrase):
                after = before.replace(b"on track to begin", phrase)
                self.assert_abstain(detector.TSMC, before, after)
        before = samsung_html()
        self.assert_abstain(detector.SAMSUNG, before, before.replace(b"are expected", b"are not expected"))

    def test_inherited_third_fab_predicate_cannot_be_removed(self):
        before = tsmc_html()
        after = before.replace(b"with production beginning", b"with equipment installation beginning")
        self.assert_abstain(detector.TSMC, before, after)

    def test_removed_narrative_or_timeline_requires_abstention(self):
        before = tsmc_html()
        for after in (before.replace(b"<strong>Fab 3</strong>", b"<strong>Unknown</strong>"),
                      before.replace(b" and in the third by the end of the decade", b"")):
            self.assert_abstain(detector.TSMC, before, after)

    def test_duplicate_ambiguous_fab_target_is_not_deduplicated_away(self):
        before = tsmc_html()
        after = before.replace(b"<h2>Financial", b"<p>The second fab is expected to begin production in 2030.</p><h2>Financial")
        self.assert_abstain(detector.TSMC, before, after)

    def test_added_unparsed_subject_or_calendar_target_abstains(self):
        before = tsmc_html()
        for addition in (b"<p>The fourth fab is planned for 2031.</p>", b"<p>Construction is planned to finish in 2029.</p>",
                         b"<p>Another fab is included in the award.</p>", b"<p>Production has been cancelled.</p>"):
            with self.subTest(addition=addition):
                self.assert_abstain(detector.TSMC, before, before.replace(b"<h2>Financial", addition + b"<h2>Financial"))

    def test_new_manufacturing_calendar_inside_environmental_section_abstains(self):
        before = amkor_html()
        after = before.replace(b"<p>By 2050, emissions will be reduced.</p>", b"<p>Production will begin in 2029.</p>")
        self.assert_abstain(detector.AMKOR, before, after)

    def test_unclassified_environmental_context_change_abstains_not_a_manufacturing_candidate(self):
        before = amkor_html()
        self.assert_abstain(detector.AMKOR, before, before.replace(b"2050", b"2040"))

    def test_visible_div_span_and_untagged_targets_cannot_evade_coverage(self):
        before = tsmc_html()
        for addition in (b"<div>Construction is planned to finish in 2029.</div>",
                         b"<span>The fourth fab is planned for 2031.</span>",
                         b"The fourth fab is planned for 2031."):
            with self.subTest(addition=addition):
                after = before.replace(b"<h2>Financial", addition + b"<h2>Financial")
                self.assert_abstain(detector.TSMC, before, after)
                self.assert_abstain(detector.TSMC, after, after)

    def test_project_and_timeline_withdrawal_cannot_evade_coverage(self):
        before = samsung_html()
        for addition in (b"<p>The project has been cancelled.</p>", b"<p>All previously announced timelines are withdrawn.</p>",
                         b"<p>The facilities have been abandoned.</p>"):
            with self.subTest(addition=addition):
                after = before.replace(b"<h2>Financial", addition + b"<h2>Financial")
                self.assert_abstain(detector.SAMSUNG, before, after)
                self.assert_abstain(detector.SAMSUNG, after, after)

    def test_unclassified_changed_project_text_fails_closed_without_keyword_inference(self):
        before = samsung_html()
        after = before.replace(b"<h2>Financial", b"<div>The previous plan is under reconsideration.</div><h2>Financial")
        self.assertEqual("source_scope_or_context_changed", self.assert_abstain(detector.SAMSUNG, before, after)["reason"])

    def test_terminal_full_stop_in_unparsed_context_is_not_semantic_coverage_change(self):
        before = tsmc_html().replace(b"<h2>Financial", b"<p>Other descriptive context</p><h2>Financial")
        after = before.replace(b"descriptive context</p>", b"descriptive context.</p>")
        self.assertEqual("no_candidate", detector.analyze(detector.TSMC, before, after)["result"])
        self.assert_abstain(detector.TSMC, before, before.replace(b"Other descriptive context", b"Different descriptive context"))

    def test_unknown_headings_tables_and_container_layout_abstain(self):
        before = tsmc_html()
        for after in (before.replace(b"Project Summary</h2>", b"Project Details</h2>"),
                      before.replace(b"text-with-summary", b"unknown-layout"),
                      before.replace(b"<h2>Financial", b"<h3>New project subsection</h3><h2>Financial"),
                      before.replace(b"</div>", b"<table></table></div>")):
            self.assert_abstain(detector.TSMC, before, after)

    def test_scope_and_timeline_must_both_be_present_and_unique(self):
        before = samsung_html()
        for after in (before.replace(b"Project Timeline", b"Timeline omitted"),
                      before.replace(b"<h2>Project Summary</h2>", b"<h2>Project Summary</h2><h2>Project Summary</h2>"),
                      before.replace(b"<h1>", b"<h1>Other page</h1><h1>")):
            self.assert_abstain(detector.SAMSUNG, before, after)

    def test_hidden_targets_are_not_visible_parser_coverage(self):
        before = tsmc_html()
        for attributes in (b"hidden", b'aria-hidden="true"', b'style="display: none"', b'style="visibility: hidden"', b"inert"):
            with self.subTest(attributes=attributes):
                hidden = before.replace(b'<div class="text-with-summary">', b'<div class="text-with-summary" ' + attributes + b'>')
                self.assert_abstain(detector.TSMC, hidden, hidden)

    def test_script_style_comments_and_hidden_duplicate_targets_are_ignored(self):
        before = tsmc_html()
        extra = b'<script>Fab 2 production in 2099</script><style>.x { content: "production in 2099"; }</style><!-- production in 2099 --><div hidden><h2>Project Summary</h2></div>'
        after = before.replace(b"<body>", b"<body>" + extra)
        self.assertEqual("no_candidate", detector.analyze(detector.TSMC, before, after)["result"])

    def test_invalid_utf8_missing_large_and_malformed_html_abstain(self):
        before = tsmc_html()
        for after in (None, b"", b"\xff", before[:-10], before.replace(b"</p>", b"</aside>", 1), b"x" * (detector.MAX_BYTES + 1)):
            with self.subTest(kind=type(after).__name__):
                self.assert_abstain(detector.TSMC, before, after)

    def test_bogus_comments_declarations_and_processing_instructions_abstain(self):
        before = tsmc_html()
        for markup in (b"<![wrong]>", b"<![CDATA[foo]]>", b"<!-->", b"<?xml test?>", b'<!DOCTYPE html [ <!ENTITY x "y"> ]>'):
            with self.subTest(markup=markup):
                self.assert_abstain(detector.TSMC, before, before.replace(b"<body>", b"<body>" + markup))
        self.assert_abstain(detector.TSMC, before, before.replace(b"</p>", b"</p unknown>", 1))

    def test_phase_allocation_in_nist_scope_cannot_be_called_unspecified(self):
        before = amkor_html()
        phase = before.replace(b"new advanced packaging and test facility", b"new advanced packaging and test facility for Phase One")
        self.assert_abstain(detector.AMKOR, phase, phase)

    def test_duplicate_attributes_and_excessive_nesting_abstain(self):
        before = tsmc_html()
        self.assert_abstain(detector.TSMC, before, before.replace(b'<div class="text-with-summary">', b'<div class="text-with-summary" class="other">'))
        with patch.object(detector, "MAX_DEPTH", 3):
            self.assert_abstain(detector.TSMC, before, before)

    def test_evidence_offsets_hashes_and_full_body_visible_literals_are_exact(self):
        before = samsung_html().replace(b"<body>", "<body>Été 🌞".encode())
        after = before.replace(b"by 2030", b"by <strong>2031</strong>")
        result = detector.analyze(detector.SAMSUNG, before, after)
        self.assertEqual("revision_candidate", result["result"])
        for side, raw in (("before", before), ("after", after)):
            evidence = result["subjects"][0]["formulations"][0][side]
            self.assertIn(evidence["literal"], statements._span(evidence["fragment"], raw))
            for span in [evidence["fragment"], *evidence["context"]]:
                fragment = raw[span["start"]:span["end"]]
                self.assertEqual(sha256(fragment).hexdigest(), span["sha256"])
                fragment.decode("utf-8", errors="strict")

    def test_entities_preserve_visible_scope_and_literal(self):
        before = samsung_html()
        after = before.replace(b"2030", b"20&#51;0")
        self.assertEqual("no_candidate", detector.analyze(detector.SAMSUNG, before, after)["result"])

    def test_amkor_company_requires_phase_one_and_does_not_inherit_nist_scope(self):
        before = company_html()
        self.assert_abstain(detector.AMKOR_COMPANY, before, before.replace(b"Phase One", b"Phase Two"))
        self.assert_abstain(detector.AMKOR_COMPANY, before, before.replace(b"expected to open", b"expected to begin production"))

    def test_company_unknown_sections_and_added_dates_abstain(self):
        before = company_html()
        self.assert_abstain(detector.AMKOR_COMPANY, before, before.replace(b"</div>", b"<h2>New section</h2></div>"))
        self.assert_abstain(detector.AMKOR_COMPANY, before, before.replace(b"This synthetic tail retains context.", b"A second opening follows in 2030."))

    def test_deterministic_pure_call_has_no_io_or_clock_dependency(self):
        before, after = tsmc_html(), tsmc_html("in 2029")
        expected = detector.analyze(detector.TSMC, before, after)
        with patch("builtins.open", side_effect=AssertionError("analyze must not read files")), patch("time.time", side_effect=AssertionError("analyze must not read clocks")):
            self.assertEqual(expected, detector.analyze(detector.TSMC, before, after))
        self.assertEqual(detector.RULE_VERSION, expected["rule_version"])
        self.assertEqual(sha256(before).hexdigest(), expected["body_sha256"]["before"])


if __name__ == "__main__":
    unittest.main()
