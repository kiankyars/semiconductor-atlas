import unittest

from semiconductor_atlas.adapters.nist_discovery import parse_index


NEWS = "https://www.nist.gov/chips/chips-news-releases"
AWARDS = "https://www.nist.gov/chips/chips-program-office-awards"
ARTICLE = "/news-events/news/2026/07/chips-company-announcement"


def news(*, url=ARTICLE, title="TSMC announcement", about=None):
    about = url if about is None else about
    return f'''<article class="nist-teaser" about="{about}">
      <div class="nist-teaser__image"><a href="{url}"><img src="logo.png"></a></div>
      <header><h3 class="nist-teaser__title"><a href="{url}"><span property="schema:name">{title}</span></a>
        <a class="auto-anchor" href="#permalink"><img alt="permalink"></a></h3>
      <div class="nist-teaser__date"><div class="daterange"><time datetime="2026-07-16T12:00:00Z">July 16, 2026</time></div></div></header>
      <div class="nist-teaser__content"><div property="schema:text">A broader project &amp; a company announcement.</div></div>
    </article>'''


def award(*, office="CHIPS Program Office", url="/chips/intel-corporation-arizona-chandler"):
    return f'''<div class="margin-top-3"><span class="views-field views-field-title"><strong><a href="{url}">Intel Corporation (Arizona)</a></strong></span>
      <span class="views-field-field-chipfund-location-locality"><strong> – Chandler, </strong></span>
      <span class="views-field-field-chipfund-location-administrative-area"><strong>AZ</strong></span>
      <div class="views-field-field-chipfund-chips-org"><div><div class="nist-field__item">{office}</div></div></div>
      <div class="views-field-body"><div>Several fabs in a broader campus.</div></div></div>'''


def pager(current=0, next_page=1):
    following = "" if next_page is None else f'<li><a rel="next" href="?page={next_page}">Next</a></li>'
    return f'<nav class="pager"><ul><li><a aria-current="page" href="?page={current}">Current</a></li>{following}</ul></nav>'


def document(body, *, kind="news"):
    heading = "CHIPS News &amp; Releases" if kind == "news" else "CHIPS Program Office Awards"
    return f'<!doctype html><html><body><h1>{heading}</h1>{body}<footer>Updated January 1, 1999</footer></body></html>'.encode()


class NISTDiscoveryParserTests(unittest.TestCase):
    def parse_news(self, body, *, url=NEWS):
        return parse_index(document(body), page_url=url, kind="news")

    def test_news_extracts_only_title_identity_and_publisher_date(self):
        result = self.parse_news(news() + pager())
        self.assertEqual(1, result["entry_count"])
        self.assertEqual(0, result["excluded_count"])
        self.assertEqual(NEWS + "?page=1", result["next_url"])
        entry = result["entries"][0]
        self.assertEqual("https://www.nist.gov" + ARTICLE, entry["url"])
        self.assertEqual("2026-07-16T12:00:00Z", entry["index_date"])
        self.assertEqual("TSMC announcement", entry["title"])
        self.assertEqual("A broader project & a company announcement.", entry["summary"])
        self.assertIsNone(entry["source_native_scope"])

    def test_awards_preserve_native_scope_and_account_for_other_offices(self):
        result = parse_index(document(award() + award(office="CHIPS NAPMP", url="/chips/research-award") + pager(), kind="awards"), page_url=AWARDS, kind="awards")
        self.assertEqual(1, result["entry_count"])
        self.assertEqual(1, result["excluded_count"])
        self.assertEqual({"locality": "Chandler", "region": "AZ"}, result["entries"][0]["source_native_scope"])
        self.assertIsNone(result["entries"][0]["index_date"])

    def test_hidden_and_outside_content_do_not_become_records(self):
        fake = news(url="/news-events/news/2026/07/hidden-announcement")
        for hiding in ('hidden', 'aria-hidden="true"', 'style="display: none !important"', 'class="visually-hidden"', 'style="visibility:hidden"'):
            with self.subTest(hiding=hiding):
                result = self.parse_news(f'<div {hiding}>{fake}</div><a href="{ARTICLE}">Outside</a>' + news())
                self.assertEqual(1, result["entry_count"])
        for tag in ("script", "style", "template", "noscript"):
            with self.subTest(tag=tag):
                self.assertEqual(1, self.parse_news(f'<{tag}>{fake}</{tag}>' + news())["entry_count"])

    def test_hidden_field_cannot_supply_required_identity(self):
        with self.assertRaises(ValueError):
            self.parse_news(news().replace('class="nist-teaser__title"', 'class="nist-teaser__title" hidden'))

    def test_wrong_page_kind_heading_and_noncanonical_requested_urls(self):
        for value in (NEWS + "?page=0", NEWS + "?page=01", NEWS + "?page=-1", NEWS + "?page=1&x=y", NEWS + "#x", NEWS.replace("https", "http"), NEWS.replace("www.nist.gov", "www.nist.gov:443"), AWARDS):
            with self.subTest(url=value), self.assertRaises(ValueError):
                self.parse_news(news(), url=value)
        with self.assertRaises(ValueError):
            parse_index(document(news()), page_url=AWARDS, kind="awards")
        with self.assertRaises(ValueError):
            parse_index(document(news()), page_url=NEWS, kind="unknown")

    def test_unsafe_document_links_and_identity_disagreement_are_rejected(self):
        for url in ("https://evil.example" + ARTICLE, "//www.nist.gov" + ARTICLE, "https://user@www.nist.gov" + ARTICLE, ARTICLE + "?x=y", ARTICLE + "#x", "/news-events/news/2026/07/../other", ARTICLE + "%2fmore", "/chips/facility"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.parse_news(news(url=url))
        with self.assertRaises(ValueError):
            self.parse_news(news(about="/news-events/news/2026/07/different-announcement"))
        result = self.parse_news(news(about="https://www.nist.gov" + ARTICLE))
        self.assertEqual(1, result["entry_count"])

    def test_missing_and_malformed_fields_fail_closed(self):
        cases = [news().replace(f'about="{ARTICLE}"', ""), news().replace('datetime="2026-07-16T12:00:00Z"', ""), news().replace("2026-07-16T12:00:00Z", "not-a-date"), news().replace("TSMC announcement", ""), news().replace('property="schema:text"', ""), news().replace("</article>", ""), news().replace("</h3>", "</h2>"), news().replace(f'href="{ARTICLE}"', f'href="{ARTICLE}" href="/other"')]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.parse_news(body)

    def test_missing_and_malformed_awards_fail_closed(self):
        for body in (award().replace('class="views-field-field-chipfund-location-locality"', ""), award().replace("CHIPS Program Office", ""), award().replace('views-field-title', 'other'), award().replace("</strong></span>", "</span>", 1)):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse_index(document(body, kind="awards"), page_url=AWARDS, kind="awards")

    def test_duplicate_conflicts_fail_but_identical_observations_coalesce(self):
        duplicate = self.parse_news(news() + news())
        self.assertEqual(1, duplicate["entry_count"])
        self.assertEqual(2, duplicate["record_count"])
        self.assertEqual(1, duplicate["duplicate_count"])
        with self.assertRaises(ValueError):
            self.parse_news(news() + news(title="Changed title"))
        with self.assertRaises(ValueError):
            parse_index(document(award() + award(office="CHIPS NAPMP"), kind="awards"), page_url=AWARDS, kind="awards")

    def test_pager_terminal_and_initial_single_page(self):
        self.assertIsNone(self.parse_news(news())["next_url"])
        self.assertIsNone(self.parse_news(news() + pager(1, None), url=NEWS + "?page=1")["next_url"])
        with self.assertRaises(ValueError):
            self.parse_news(news(), url=NEWS + "?page=1")

    def test_pager_requires_unique_current_and_sequential_next(self):
        cases = [pager(1, 2), pager(0, 2), pager(0, 0), pager().replace('aria-current="page"', ""), pager() + pager(), pager().replace("</ul>", '<a rel="next" href="?page=1">Next again</a></ul>')]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.parse_news(news() + body)

    def test_pager_refuses_unsafe_next_destinations(self):
        for target in ("//www.nist.gov/chips/chips-news-releases?page=1", "https://evil.example/chips/chips-news-releases?page=1", "https://user@www.nist.gov/chips/chips-news-releases?page=1", "?page=1#frag", "?page=1&sort_by=date", "?page=01", "?page=1&page=1", "/chips/chips-program-office-awards?page=1", "?page=1%00", "../chips/chips-news-releases?page=1"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.parse_news(news() + pager().replace('href="?page=1"', f'href="{target}"'))

    def test_pager_checks_all_visible_links_not_only_current_and_next(self):
        for target in ("https://evil.example/?page=3", "?page=03", "?page=2&sort=date",
                       "/chips/chips-program-office-awards?page=2", "?page=2#fragment"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                extra = f'<li><a href="{target}">Last</a></li>'
                self.parse_news(news() + pager().replace("</ul>", extra + "</ul>"))
        numbered = '<li><a href="?page=3">Last</a></li>'
        result = self.parse_news(news() + pager().replace("</ul>", numbered + "</ul>"))
        self.assertEqual(NEWS + "?page=1", result["next_url"])

    def test_future_page_without_consistent_next_is_not_terminal(self):
        for navigation in (pager().replace('rel="next"', ''),
                           pager(0, None).replace("</ul>", '<a href="?page=4">Last</a></ul>'),
                           pager().replace('href="?page=1"', 'href="?page=2"')):
            with self.subTest(navigation=navigation), self.assertRaises(ValueError):
                self.parse_news(news() + navigation)
        terminal = pager(1, None).replace("</ul>", '<a href="?page=0" rel="prev">Previous</a></ul>')
        self.assertIsNone(self.parse_news(news() + terminal, url=NEWS + "?page=1")["next_url"])
        self.assertEqual(NEWS + "?page=1", self.parse_news(news() + pager().replace('rel="next"', 'rel="NEXT"'))["next_url"])

    def test_outer_document_must_be_complete_before_accepting_index(self):
        for body in (news(), news() + pager(), news() + pager(0, None)):
            raw = document(body)
            cases = (raw.replace(b"</body></html>", b""),
                     raw.replace(b"</html>", b""),
                     raw.replace(b"</body>", b""),
                     raw.replace(b"<html>", b""),
                     raw.replace(b"<body>", b""),
                     raw.replace(b"</body>", b"<div></body>"),
                     raw + b"<html></html>",
                     raw.replace(b"<body>", b"<body/>"))
            for incomplete in cases:
                with self.subTest(incomplete=incomplete), self.assertRaises(ValueError):
                    parse_index(incomplete, page_url=NEWS, kind="news")
        self.assertIsNone(self.parse_news(news())["next_url"])

    def test_nested_record_and_nonrecognized_or_empty_page_rejected(self):
        with self.assertRaises(ValueError):
            self.parse_news(news().replace("</article>", news() + "</article>"))
        for body in ("", f'<a href="{ARTICLE}">Not a card</a>', '<div hidden>' + news() + '</div>'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.parse_news(body)

    def test_bytes_contract_and_encoding(self):
        for raw in ("text", b"\xff"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_index(raw, page_url=NEWS, kind="news")


if __name__ == "__main__":
    unittest.main()
