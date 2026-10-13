"""A future opt-in policy rule; no live plan or publisher requests are used."""

import copy
import json
import unittest
from unittest.mock import patch

from semiconductor_atlas import curated_capture as capture
from tests import test_curated_capture as fixtures


def policy(date="4 September 2026"):
    paragraph = '<p class="prose-label-md-regular text-base-content-inverse-subtle">'
    notice = f'© <!-- -->2026<!-- --> Government of Singapore<!-- -->, last updated<!-- --> {date}</p>'
    return ('<html><head><meta charset="utf-8"/></head><body>'
        '<main id="main-content"><h1>Terms of Use</h1><p>Research only. No redistribution.</p>'
        '<p>Last updated 22 October 2025</p><a href="/terms">Terms</a></main>'
        '<footer class="bg-base-canvas-inverse text-base-content-inverse">'
        '<div class="flex flex-col gap-8 px-6 py-11 md:px-10 lg:hidden lg:py-16"><div>'
        + paragraph + notice + '</div></div><div class="hidden px-10 py-14 lg:block"><div>'
        + paragraph + notice + '</div></div><a href="/privacy">Privacy</a></footer>'
        '<script>const date = "4 September 2026";</script></body></html>').encode()


class MtiNormalizationTests(unittest.TestCase):
    def normalize(self, raw):
        return capture.normalized_bytes(raw, capture.MTI_POLICY_NORMALIZATION)

    def reject(self, raw):
        with self.assertRaises(capture.PolicyNormalizationError):
            self.normalize(raw)

    def test_only_two_reviewed_footer_dates_are_canonicalized(self):
        before, after = policy(), policy("8 September 2026")
        self.assertEqual(self.normalize(before), self.normalize(after))
        self.assertEqual(2, self.normalize(before).count(b"[reviewed-site-footer-date]"))
        for version in ("html_policy_v1", "html_policy_v2"):
            self.assertNotEqual(capture.normalized_bytes(before, version), capture.normalized_bytes(after, version))

    def test_terms_update_date_is_still_significant(self):
        self.assertNotEqual(self.normalize(policy()), self.normalize(policy().replace(b"22 October 2025", b"8 September 2026")))

    def test_substantive_clause_addition_change_and_removal_are_significant(self):
        for replacement in (b"Commercial use permitted.", b"", b"Research only. No redistribution. No automated access."):
            with self.subTest(replacement=replacement):
                self.assertNotEqual(self.normalize(policy()), self.normalize(policy().replace(b"Research only. No redistribution.", replacement)))

    def test_every_original_href_is_preserved_including_footer_and_date_links(self):
        for old, new in ((b'href="/terms"', b'href="/terms-v2"'),
                         (b'href="/privacy"', b'href="/privacy?date=8-September-2026"')):
            with self.subTest(old=old):
                self.assertNotEqual(self.normalize(policy()), self.normalize(policy().replace(old, new)))
        normalized = self.normalize(policy())
        self.assertEqual(normalized.split(b"\nLINKS\n")[1], capture.normalized_bytes(policy(), "html_policy_v1").split(b"\nLINKS\n")[1])

    def test_changed_copyright_year_is_significant(self):
        self.assertNotEqual(self.normalize(policy()), self.normalize(policy().replace(b"2026<!-- --> Government", b"2027<!-- --> Government")))

    def test_changed_publisher_label_or_extra_notice_text_is_rejected(self):
        for old, new in ((b"Government of Singapore", b"Another publisher"),
                         (b", last updated", b", terms effective"),
                         (b"4 September 2026</p>", b"4 September 2026. New restrictions.</p>")):
            with self.subTest(old=old):
                self.reject(policy().replace(old, new))

    def test_nested_markup_in_date_paragraph_is_rejected(self):
        for replacement in (b"<span>4 September 2026</span>", b'<a href="/other">4 September 2026</a>',
                            b"<br/>4 September 2026", b"<p>4 September 2026</p>"):
            with self.subTest(replacement=replacement):
                self.reject(policy().replace(b"4 September 2026</p>", replacement + b"</p>"))

    def test_missing_or_duplicate_branch_is_rejected(self):
        raw = policy()
        self.reject(raw.replace(b"hidden px-10 py-14 lg:block", b"unknown desktop"))
        self.reject(raw.replace(b"hidden px-10 py-14 lg:block", b"flex flex-col gap-8 px-6 py-11 md:px-10 lg:hidden lg:py-16"))

    def test_mobile_and_desktop_dates_must_match(self):
        self.reject(policy().replace(b"4 September 2026", b"8 September 2026", 1))

    def test_real_calendar_dates_required(self):
        for date in ("31 February 2026", "29 February 2026", "0 September 2026", "04 September 2026",
                     "32 September 2026", "4 Unknown 2026", "4 September 0000", "4 September 2026 extra"):
            with self.subTest(date=date):
                self.reject(policy(date))
        self.assertEqual(self.normalize(policy("29 February 2024")), self.normalize(policy("8 September 2026")))

    def test_footer_inside_main_before_main_or_duplicated_is_rejected(self):
        raw = policy()
        self.reject(raw.replace(b"</main><footer", b"<footer").replace(b"</footer>", b"</footer></main>"))
        self.reject(raw.replace(b"<main id=\"main-content\">", b"<div>").replace(b"</main>", b"</div>"))
        self.reject(raw.replace(b"</footer>", b'</footer><footer class="bg-base-canvas-inverse text-base-content-inverse"></footer>'))
        main = raw[raw.index(b"<main"):raw.index(b"</main>") + len(b"</main>")]
        self.reject(raw.replace(main, b"").replace(b"</footer>", b"</footer>" + main))

    def test_two_document_bodies_cannot_share_the_normalization_scope(self):
        self.reject(policy().replace(b"</main>", b"</main></body></html><html><body>"))
        self.reject(policy().replace(b"</main>", b"</main></body><body>"))

    def test_duplicate_candidate_and_extra_scope_attributes_rejected(self):
        raw = policy()
        start = raw.index(b'<p class="prose-label-md-regular')
        end = raw.index(b"</p>", start) + len(b"</p>")
        self.reject(raw[:end] + raw[start:end] + raw[end:])
        self.reject(raw.replace(b'<footer class=', b'<footer role="contentinfo" class='))
        self.reject(raw.replace(b'<p class="prose-label-md-regular', b'<p data-extra="new" class="prose-label-md-regular'))

    def test_malformed_nesting_duplicate_attributes_or_changed_scope_rejected(self):
        for old, new in ((b"</footer>", b"</div>"), (b"</html>", b""),
                         (b'<main id="main-content">', b'<main id="main-content" id="other">'),
                         (b"bg-base-canvas-inverse text-base-content-inverse", b"another-footer"),
                         (b'<main id="main-content">', b'<main id="other">')):
            with self.subTest(old=old, new=new):
                self.reject(policy().replace(old, new))

    def test_footer_like_date_in_main_remains_significant(self):
        first = policy().replace(b"<h1>", b"<p>Government of Singapore, last updated 4 September 2026</p><h1>")
        second = first.replace(b"updated 4 September 2026</p><h1>", b"updated 8 September 2026</p><h1>")
        self.assertNotEqual(self.normalize(first), self.normalize(second))

    def test_comment_entity_split_or_nonliteral_date_does_not_broaden_scope(self):
        for new in (b"4 Sept<!-- -->ember 2026", b"&#52; September 2026", b"4 September 2026<!-- trailing -->"):
            with self.subTest(new=new):
                self.reject(policy().replace(b"4 September 2026</p>", new + b"</p>"))

    def test_multiline_offsets_preserve_other_unicode_text(self):
        raw = policy().replace(b"><", b">\n<").replace(b"Terms of Use", "Terms — 利用規約".encode())
        self.assertEqual(self.normalize(raw), self.normalize(raw.replace(b"4 September 2026", b"8 September 2026")))
        self.assertIn("Terms — 利用規約".encode(), self.normalize(raw))

    def test_invalid_utf8_retains_encoding_failure(self):
        with self.assertRaises(UnicodeError):
            self.normalize(policy() + b"\xff")


class MtiCaptureGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CuratedCaptureTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = self.fixture.plan
        self.plan["format"] = capture.PLAN_FORMAT_V2
        self.plan["policies"][0]["url"] = "https://www.mti.gov.sg/robots.txt"
        self.plan["policies"][1].update(url=capture.MTI_TERMS_URL, normalization=capture.MTI_POLICY_NORMALIZATION)
        self.plan["documents"][0]["url"] = "https://www.mti.gov.sg/newsroom/fixture-only"
        self.fixture.bodies["rights"] = policy()
        self.plan["policies"][1]["normalized_sha256"] = capture._sha(capture.normalized_bytes(policy(), capture.MTI_POLICY_NORMALIZATION))

    def load(self):
        self.fixture.plan_path.write_text(json.dumps(self.plan))
        return capture.load_plan(self.fixture.plan_path)

    def test_exact_url_rights_policy_and_v2_plan_are_required(self):
        self.load()
        original = copy.deepcopy(self.plan)
        mutations = [lambda p: p.update(format=capture.PLAN_FORMAT),
            lambda p: p["policies"][1].update(url="https://www.mti.gov.sg/terms-of-use"),
            lambda p: p["policies"][1].update(url="https://www.mti.gov.sg:443/terms-of-use/"),
            lambda p: p["policies"][1].update(url="https://www.mti.gov.sg/terms-of-use/?id=1"),
            lambda p: p["policies"][1].update(url="https://example.org/terms-of-use/"),
            lambda p: p["policies"][1].update(purpose="access_policy"),
            lambda p: p["policies"][1].update(media_types=["text/html", "text/plain"])]
        for mutate in mutations:
            self.plan.clear(); self.plan.update(copy.deepcopy(original)); mutate(self.plan)
            with self.subTest(plan=self.plan), self.assertRaises(ValueError):
                self.load()

    def test_candidate_plan_allows_only_footer_date_change_and_replays_offline(self):
        self.fixture.bodies["rights"] = policy("8 September 2026")
        root, report = self.fixture.run_capture()
        self.assertEqual(["robots", "rights", "document"], self.fixture.calls)
        self.assertTrue(report["policies"][1]["accepted"])
        with patch.object(capture, "curl_fetch", side_effect=AssertionError("no network")):
            self.assertEqual(report, capture.validate_capture(root))
        self.assertFalse(report["absence_inference_allowed"])

    def test_changed_terms_blocks_document_but_preserves_terminal_capture(self):
        self.fixture.bodies["rights"] = policy().replace(b"No redistribution.", b"No automated access.")
        root, report = self.fixture.run_capture()
        self.assertEqual(["robots", "rights"], self.fixture.calls)
        self.assertEqual("policy_changed_requires_review", report["policies"][1]["reason"])
        self.assertEqual("not_attempted_policy_blocked", report["documents"][0]["status"])
        self.assertEqual(report, capture.validate_capture(root))

    def test_structural_rejection_blocks_document_and_replays_explicit_reason(self):
        self.fixture.bodies["rights"] = policy().replace(b"Government of Singapore", b"another publisher")
        root, report = self.fixture.run_capture()
        self.assertEqual(["robots", "rights"], self.fixture.calls)
        self.assertEqual("policy_normalization_scope_changed_requires_review", report["policies"][1]["reason"])
        self.assertEqual("not_attempted_policy_blocked", report["documents"][0]["status"])
        self.assertIsNone(report["policies"][1]["normalized_sha256"])
        self.assertEqual(report, capture.validate_capture(root))

    def test_structural_failure_never_falls_back_to_a_matching_v1_pin(self):
        malformed = policy().replace(b"Government of Singapore", b"another publisher")
        self.fixture.bodies["rights"] = malformed
        self.plan["policies"][1]["normalized_sha256"] = capture._sha(capture.normalized_bytes(malformed, "html_policy_v1"))
        _, report = self.fixture.run_capture()
        self.assertEqual(["robots", "rights"], self.fixture.calls)
        self.assertFalse(report["policies"][1]["accepted"])

    def test_old_policy_rule_still_blocks_footer_date_drift(self):
        self.plan["policies"][1].update(normalization="html_policy_v1", normalized_sha256=capture._sha(capture.normalized_bytes(policy(), "html_policy_v1")))
        self.fixture.bodies["rights"] = policy("8 September 2026")
        root, report = self.fixture.run_capture()
        self.assertEqual(["robots", "rights"], self.fixture.calls)
        self.assertEqual("policy_changed_requires_review", report["policies"][1]["reason"])
        self.assertEqual(report, capture.validate_capture(root))

    def test_invalid_encoding_blocks_without_masking_its_reason(self):
        self.fixture.bodies["rights"] = policy() + b"\xff"
        root, report = self.fixture.run_capture()
        self.assertEqual(["robots", "rights"], self.fixture.calls)
        self.assertEqual("unsupported_encoding", report["policies"][1]["reason"])
        self.assertEqual(report, capture.validate_capture(root))


if __name__ == "__main__":
    unittest.main()
