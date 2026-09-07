from __future__ import annotations

import copy
from hashlib import sha256
from html import unescape
import re
import unittest
from unittest.mock import patch

from semiconductor_atlas import source_assertion_detector as detector


AMKOR_TITLE = "Amkor Technology Breaks Ground on Landmark U.S. Semiconductor Packaging Facility"
MTI_TITLE = "Speech by DPM and Minister for Trade and Industry Gan Kim Yong at the groundbreaking ceremony of Micron's HBM Manufacturing Facility"


def page(content, *, before="", after=""):
    return ("<!doctype html><html><body>" + before + content + after + "</body></html>").encode("utf-8")


def amkor_html(*, progress="progresses", extra="", metadata="October 6, 2025", before="", after=""):
    content = '<h1>Blog</h1><div class="blog_title_tier"><h2>' + AMKOR_TITLE + '</h2>'
    content += '<div class="blog_title_meta">' + metadata + ' in <strong>Company News</strong> by <strong>Amkor Marcom</strong></div></div>'
    content += '<div class="blog_content_tier"><p>On Monday, Oct. 6, Amkor Technology, Inc. celebrated a major advancement in American semiconductor manufacturing with the groundbreaking of its cutting-edge semiconductor packaging and test facility in Peoria, Arizona.</p>'
    content += '<p>As construction ' + progress + ', the focus will remain on expanding cutting-edge manufacturing capacity, creating career opportunities for a skilled workforce, and enhancing supply chain resilience.</p>'
    content += extra + '</div>'
    return page(content, before=before, after=after)


def chandler_html(*, opening="is now open", production="produces", extra="", other="Other employer information.", before="", after=""):
    content = '<h1>Advanced Manufacturing</h1><h2>A Few of the Leading Companies Operating in Chandler</h2>'
    content += '<div class="paragraph--type--accordion"><div class="card-header" id="intel-heading"><a role="button" href="#intel-region" aria-controls="intel-region">Intel</a></div>'
    content += '<div role="region" id="intel-region" aria-labelledby="intel-heading"><p>The multinational technology company located in Chandler in 1980 and has approximately 10,000 employees spread across two campuses. Intel announced an investment of more than $32 billion to build two new leading-edge chip factories and modernize an existing fab at its Ocotillo Campus in Chandler. One of those fabs, Fab 52, ' + opening + ' and ' + production + ' the most advanced logic chips in the U.S.</p>'
    content += extra + '</div></div><section id="other-employer"><h3>Other Employer</h3><p>' + other + '</p></section>'
    return page(content, before=before, after=after)


def micron_html(*, pleasure="great pleasure", completion="the successful completion", extra="", metadata="8 January 2025", before="", after=""):
    content = '<main id="main-content"><h1>' + MTI_TITLE + '</h1><p class="prose-label-sm-medium">' + metadata + '</p>'
    content += '<div class="overflow-x-auto"><p>Good morning. It is my ' + pleasure + ' to join you here today at the groundbreaking ceremony of Micron’s HBM Advanced Packaging facility.</p>'
    content += '<p>Let me congratulate Micron once again on the groundbreaking of this new HBM facility in Singapore. This is not just the start of an exciting new chapter for Micron, but also for our local semiconductor industry.</p>'
    content += '<p>I look forward to ' + completion + ' of this new facility, and to forge a deeper and stronger partnership with Micron in the years ahead.</p>'
    content += extra + '</div></main>'
    return page(content, before=before, after=after)


class SourceAssertionDetectorTests(unittest.TestCase):
    """Invented minimal layouts and controlled mutations, not retained publisher bodies or an evaluation cohort."""

    def routes(self):
        return ((detector.AMKOR_GROUNDBREAKING, amkor_html),
                (detector.CHANDLER, chandler_html), (detector.MICRON_MTI, micron_html))

    def assert_extracted(self, url, raw):
        result = detector.extract(url, raw)
        self.assertEqual("extracted", result["status"], result)
        self.assertTrue(result["assertions"], result)
        return result

    def assert_abstains(self, url, raw):
        result = detector.extract(url, raw)
        self.assertEqual("abstain", result["status"], result)
        self.assertEqual([], result["assertions"], result)
        comparison = detector.analyze(url, raw, raw)
        self.assertEqual("abstain", comparison["result"], comparison)
        return result

    def assert_not_quiet(self, url, before, after):
        result = detector.analyze(url, before, after)
        self.assertIn(result["result"], {"abstain", "revision_candidate"}, result)
        return result

    def assert_span(self, raw, span):
        self.assertEqual({"start", "end", "sha256", "locator"}, set(span))
        self.assertIs(type(span["start"]), int)
        self.assertIs(type(span["end"]), int)
        self.assertLess(span["start"], span["end"])
        self.assertGreaterEqual(span["start"], 0)
        self.assertLessEqual(span["end"], len(raw))
        fragment = raw[span["start"]:span["end"]]
        self.assertEqual(sha256(fragment).hexdigest(), span["sha256"])
        fragment.decode("utf-8")
        self.assertTrue(span["locator"])
        return fragment

    def test_exact_route_roster_is_only_the_three_additive_sources(self):
        self.assertEqual({url for url, _ in self.routes()}, set(detector.SUPPORTED_URLS))
        self.assertEqual(3, len(detector.SUPPORTED_URLS))

    def test_three_structures_extract_typed_undated_source_assertions(self):
        for url, builder in self.routes():
            with self.subTest(url=url):
                result = self.assert_extracted(url, builder())
                keys = [row["assertion_key"] for row in result["assertions"]]
                self.assertEqual(len(keys), len(set(keys)))
                for assertion in result["assertions"]:
                    self.assertTrue(assertion["source_native_subject"])
                    self.assertTrue(assertion["predicate"])
                    self.assertTrue(assertion["literal"])
                    self.assertIsNone(assertion["event_date"])
                    self.assertIsNone(assertion["calendar_target"])
                    self.assertNotIn("high_volume", assertion["predicate"])
                    self.assertNotIn("capacity_value", assertion)
                    self.assertNotIn("canonical_entity_id", assertion)
                self.assertFalse(result["coverage"]["whole_document_complete"])
                self.assertTrue(result["coverage"]["source_unparsed"])
                self.assertTrue(all(value is False for value in result["boundaries"].values()))

    def test_exact_evidence_and_context_byte_spans_bind_each_side(self):
        for url, builder in self.routes():
            with self.subTest(url=url):
                raw = builder(before="<nav>Synthetic café navigation</nav>")
                result = self.assert_extracted(url, raw)
                for container in result["coverage"]["containers"]:
                    self.assert_span(raw, container)
                for assertion in result["assertions"]:
                    evidence = assertion["evidence"]
                    fragment = self.assert_span(raw, evidence["fragment"])
                    visible = " ".join(unescape(re.sub(r"<[^>]*>", " ", fragment.decode())).split())
                    self.assertIn(assertion["literal"], visible)
                    self.assertTrue(evidence["context"])
                    for span in evidence["context"]:
                        self.assert_span(raw, span)

    def test_first_observations_extract_but_cannot_generate_change_candidates(self):
        for url, builder in self.routes():
            with self.subTest(url=url):
                result = detector.analyze(url, None, builder())
                self.assertEqual("extraction_only", result["result"], result)
                self.assertEqual("extracted", result["extractions"]["after"]["status"])
                self.assertEqual("abstain", result["extractions"]["before"]["status"])
                self.assertIsNone(result["extractions"]["before"]["body_sha256"])

    def test_empty_predecessor_is_invalid_not_an_unrecorded_first_observation(self):
        for url, builder in self.routes():
            with self.subTest(url=url):
                self.assertEqual("abstain", detector.analyze(url, b"", builder())["result"])

    def test_identical_recognized_bodies_require_parsing_before_quiet_result(self):
        for url, builder in self.routes():
            with self.subTest(url=url):
                result = detector.analyze(url, builder(), builder())
                self.assertEqual("no_candidate", result["result"], result)
                self.assertEqual("extracted", result["extractions"]["before"]["status"])
                self.assertEqual("extracted", result["extractions"]["after"]["status"])
                self.assertTrue(result["assertions"])

    def test_supported_vocabulary_variations_remain_extractable_source_text(self):
        variants = ((detector.AMKOR_GROUNDBREAKING, amkor_html(progress="continues")),
                    (detector.CHANDLER, chandler_html(opening="is open", production="is producing")),
                    (detector.MICRON_MTI, micron_html(pleasure="pleasure", completion="completion")))
        for url, raw in variants:
            with self.subTest(url=url):
                self.assert_extracted(url, raw)

    def test_navigation_and_footer_edits_outside_reviewed_scope_are_ignored(self):
        for url, builder in self.routes():
            with self.subTest(url=url):
                before = builder(before="<nav>2025 navigation</nav>", after="<footer>Copyright 2025</footer>")
                after = builder(before="<nav>2026 navigation</nav>", after="<footer>Copyright 2026</footer>")
                self.assertEqual("no_candidate", detector.analyze(url, before, after)["result"])

    def test_other_employer_changes_do_not_become_intel_assertion_changes(self):
        before = chandler_html(other="A different employer plans a facility in 2028.")
        after = chandler_html(other="A different employer cancelled its facility in 2029.")
        self.assertEqual("no_candidate", detector.analyze(detector.CHANDLER, before, after)["result"])

    def test_script_style_template_and_comments_cannot_supply_required_assertions(self):
        for url, builder in self.routes():
            for wrapper in ("script", "style", "template", "noscript"):
                with self.subTest(url=url, wrapper=wrapper):
                    raw = builder().replace(b"<body>", ("<body><" + wrapper + ">").encode()).replace(
                        b"</body>", ("</" + wrapper + "></body>").encode())
                    self.assert_abstains(url, raw)
            with self.subTest(url=url, wrapper="comment"):
                raw = builder().replace(b"<body>", b"<body><!--").replace(b"</body>", b"--></body>")
                self.assert_abstains(url, raw)

    def test_hidden_aria_hidden_inert_and_inline_style_cannot_supply_identity(self):
        attributes = (b"hidden", b'aria-hidden="true"', b"inert", b'style="display: none"',
                      b'style="VISIBILITY: HIDDEN"')
        for url, builder in self.routes():
            for attribute in attributes:
                with self.subTest(url=url, attribute=attribute):
                    raw = builder().replace(b"<body>", b"<body><section " + attribute + b">").replace(
                        b"</body>", b"</section></body>")
                    self.assert_abstains(url, raw)

    def test_closed_details_do_not_supply_assertions(self):
        for url, builder in self.routes():
            with self.subTest(url=url):
                raw = builder().replace(b"<body>", b"<body><details><summary>Archived project</summary>").replace(
                    b"</body>", b"</details></body>")
                self.assert_abstains(url, raw)

    def test_closed_dialog_does_not_supply_assertions(self):
        for url, builder in self.routes():
            with self.subTest(url=url):
                raw = builder().replace(b"<body>", b"<body><dialog>").replace(
                    b"</body>", b"</dialog></body>")
                self.assert_abstains(url, raw)

    def test_hidden_positive_lure_cannot_override_visible_negation(self):
        before = chandler_html()
        after = before.replace(b"is now open", b'is <span hidden>now open</span>not open')
        self.assert_not_quiet(detector.CHANDLER, before, after)
        self.assert_abstains(detector.CHANDLER, after)

    def test_malformed_missing_and_wrong_input_types_fail_closed(self):
        for raw in (b"", None, "<html>not bytes</html>", b"\xff", b"<html><body>\x00</body></html>",
                    b"<html><body><p>broken</body></html>", b"<p>Project paragraph only</p>"):
            with self.subTest(raw=raw):
                self.assert_abstains(detector.CHANDLER, raw)
        self.assertEqual("abstain", detector.analyze(detector.MICRON_MTI, None, b"")["result"])

    def test_html_resource_limits_fail_closed(self):
        self.assert_abstains(detector.CHANDLER, b" " * 2_000_001)
        nested = page("<div>" * 250 + "Intel" + "</div>" * 250)
        self.assert_abstains(detector.CHANDLER, nested)

    def test_duplicate_attributes_and_document_identities_are_rejected(self):
        for url, builder in self.routes():
            with self.subTest(url=url, mode="duplicate_attribute"):
                self.assert_abstains(url, builder().replace(b"<body>", b'<body id="a" id="b">'))
            with self.subTest(url=url, mode="duplicate_heading"):
                raw = builder()
                heading = re.search(rb"<h1>.*?</h1>", raw).group()
                self.assert_abstains(url, raw.replace(heading, heading + heading))

    def test_duplicate_intel_accordion_region_is_ambiguous(self):
        raw = chandler_html()
        start = raw.index(b'<div class="paragraph--type--accordion">')
        end = raw.index(b'<section id="other-employer">')
        self.assert_abstains(detector.CHANDLER, raw[:end] + raw[start:end] + raw[end:])

    def test_missing_required_subject_and_wrong_facility_cannot_be_aligned(self):
        variants = ((detector.CHANDLER, chandler_html(), b"Fab 52", b"Fab 62"),
                    (detector.AMKOR_GROUNDBREAKING, amkor_html(), b"Peoria, Arizona", b"Portland, Oregon"),
                    (detector.MICRON_MTI, micron_html(), b"HBM Advanced Packaging", b"NAND wafer fabrication"))
        for url, before, original, replacement in variants:
            with self.subTest(url=url):
                self.assert_not_quiet(url, before, before.replace(original, replacement))
                self.assert_abstains(url, before.replace(original, replacement))

    def test_exact_url_identity_does_not_accept_query_alias_or_other_documents(self):
        for url, builder in self.routes():
            for altered in (url + "?preview=true", url.replace("https://", "http://"), url + "other"):
                with self.subTest(url=altered):
                    self.assert_abstains(altered, builder())
        self.assert_abstains("https://www.nist.gov/chips/tsmc-arizona-phoenix", chandler_html())

    def test_foreign_supported_page_layout_is_not_source_identity(self):
        routes = self.routes()
        for index, (url, _) in enumerate(routes):
            with self.subTest(url=url):
                self.assert_abstains(url, routes[(index + 1) % len(routes)][1]())

    def test_intel_opening_and_production_negation_never_return_quiet(self):
        before = chandler_html()
        replacements = ((b"is now open", b"is not open"), (b"produces the", b"does not produce the"),
                        (b"is now open", b"is expected to open"), (b"produces the", b"may produce the"))
        for original, replacement in replacements:
            with self.subTest(replacement=replacement):
                after = before.replace(original, replacement)
                self.assert_not_quiet(detector.CHANDLER, before, after)
                self.assert_abstains(detector.CHANDLER, after)

    def test_amkor_reported_progress_cannot_be_promoted_to_completion_or_output(self):
        before = amkor_html()
        for replacement in ("is complete", "has stopped", "will start", "progresses toward high-volume production in 2028"):
            with self.subTest(replacement=replacement):
                after = amkor_html(progress=replacement)
                self.assert_not_quiet(detector.AMKOR_GROUNDBREAKING, before, after)

    def test_micron_completion_aspiration_is_not_a_completed_or_dated_event(self):
        before = micron_html()
        for replacement in (b"We have completed", b"I look forward to the successful completion in 2028",
                            b"I no longer look forward to the successful completion"):
            with self.subTest(replacement=replacement):
                after = before.replace(b"I look forward to the successful completion", replacement)
                self.assert_not_quiet(detector.MICRON_MTI, before, after)

    def test_page_publication_dates_do_not_become_assertion_event_dates(self):
        for url, raw in ((detector.AMKOR_GROUNDBREAKING, amkor_html()), (detector.MICRON_MTI, micron_html())):
            with self.subTest(url=url):
                for assertion in self.assert_extracted(url, raw)["assertions"]:
                    self.assertIsNone(assertion["event_date"])
                    self.assertIsNone(assertion["calendar_target"])

    def test_dates_added_to_assertion_do_not_silently_expand_grammar(self):
        variants = ((detector.CHANDLER, chandler_html(), b"is now open", b"is now open as of January 2026"),
                    (detector.AMKOR_GROUNDBREAKING, amkor_html(), b"On Monday, Oct. 6,", b"On Monday, Oct. 6, 2025,"),
                    (detector.MICRON_MTI, micron_html(), b"here today", b"here on 8 January 2025"))
        for url, before, original, replacement in variants:
            with self.subTest(url=url):
                after = before.replace(original, replacement)
                self.assert_not_quiet(url, before, after)

    def test_visible_unknown_project_additions_inside_scope_require_review(self):
        additions = ("The project has been cancelled.", "All previous schedules are withdrawn.",
                     "Only the office building is open.", "Another phase will open in 2032.",
                     "Production qualification remains uncertain.", "This facility has been abandoned.")
        for url, builder in self.routes():
            for sentence in additions:
                for tag in ("p", "div", "span", None):
                    extra = f"<{tag}>{sentence}</{tag}>" if tag else sentence
                    with self.subTest(url=url, sentence=sentence, tag=tag):
                        self.assert_not_quiet(url, builder(), builder(extra=extra))

    def test_identical_bodies_with_unknown_manufacturing_context_do_not_claim_coverage(self):
        for url, builder in self.routes():
            for extra in ("<p>The project has been cancelled.</p>",
                          "<span>The fourth production phase will open in 2032.</span>",
                          "Production qualification remains uncertain.",
                          "The factory shuttered operations.", "Completion: unknown."):
                with self.subTest(url=url, extra=extra):
                    self.assert_abstains(url, builder(extra=extra))

    def test_unknown_nonmanufacturing_text_inside_reviewed_scope_still_requires_review(self):
        for url, builder in self.routes():
            with self.subTest(url=url):
                self.assert_not_quiet(url, builder(), builder(extra="<p>A new interpretation applies to the preceding statements.</p>"))

    def test_recognized_assertion_removal_does_not_become_no_change(self):
        variants = ((detector.CHANDLER, chandler_html(), b"One of those fabs, Fab 52,"),
                    (detector.AMKOR_GROUNDBREAKING, amkor_html(), b"As construction"),
                    (detector.MICRON_MTI, micron_html(), b"I look forward"))
        for url, before, anchor in variants:
            start = before.rfind(b"<p>", 0, before.index(anchor))
            end = before.index(b"</p>", before.index(anchor)) + 4
            with self.subTest(url=url):
                self.assert_not_quiet(url, before, before[:start] + before[end:])

    def test_mark_up_only_assertion_changes_keep_semantic_literals_stable(self):
        variants = ((detector.CHANDLER, chandler_html(), b"Fab 52", b"Fab <em>52</em>"),
                    (detector.AMKOR_GROUNDBREAKING, amkor_html(), b"Peoria, Arizona", b"<strong>Peoria, Arizona</strong>"),
                    (detector.MICRON_MTI, micron_html(), b"HBM Advanced Packaging", b"HBM <em>Advanced Packaging</em>"))
        for url, before, original, replacement in variants:
            with self.subTest(url=url):
                result = detector.analyze(url, before, before.replace(original, replacement))
                self.assertEqual("no_candidate", result["result"], result)

    def test_unicode_prefix_moves_spans_by_bytes_and_not_characters(self):
        raw = micron_html(before="<nav>東京 café</nav>\n")
        extraction = self.assert_extracted(detector.MICRON_MTI, raw)
        for assertion in extraction["assertions"]:
            span = assertion["evidence"]["fragment"]
            fragment = self.assert_span(raw, span)
            self.assertEqual(raw.index(fragment), span["start"])
            self.assertGreater(span["start"], raw.decode("utf-8").index(fragment.decode("utf-8")))

    def test_decoding_html_entities_preserves_original_utf8_evidence(self):
        raw = micron_html().replace("Micron’s".encode(), b"Micron&#8217;s")
        extraction = self.assert_extracted(detector.MICRON_MTI, raw)
        self.assertTrue(any(b"&#8217;" in self.assert_span(raw, row["evidence"]["fragment"])
                            for row in extraction["assertions"]))

    def test_analysis_is_pure_and_deterministic_without_source_or_network_io(self):
        url, raw = detector.CHANDLER, chandler_html()
        expected = detector.analyze(url, raw, raw)
        with patch("builtins.open", side_effect=AssertionError("unexpected source I/O")), \
                patch("socket.socket", side_effect=AssertionError("unexpected network I/O")):
            self.assertEqual(expected, detector.analyze(url, raw, raw))
            self.assertEqual(expected["extractions"]["after"], detector.extract(url, raw))

    def test_mutating_returned_records_does_not_change_future_extractions(self):
        expected = detector.extract(detector.CHANDLER, chandler_html())
        altered = copy.deepcopy(expected)
        actual = detector.extract(detector.CHANDLER, chandler_html())
        actual["assertions"].clear()
        actual["boundaries"].clear()
        self.assertEqual(altered, detector.extract(detector.CHANDLER, chandler_html()))


if __name__ == "__main__":
    unittest.main()
