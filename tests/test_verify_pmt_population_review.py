"""Synthetic contract checks; passing tests do not adjudicate publisher semantics."""
import copy
import re
import tempfile
import unittest
from pathlib import Path

from scripts import frozen_runtime as runtime
from scripts import verify_pmt_population_review as verifier


class PMTPopulationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        (self.root / 'artifacts').mkdir()
        (self.root / 'review_plans').mkdir()
        self.index = {'records': []}
        self.roster = {'documents': [], 'capture_packets': []}
        self.narratives = {'records': []}
        self.ledger = {'attempts': []}
        self.fragment = {'articles': [], 'projects': []}
        for pos, classification in ((12, 'new_manufacturing_pmt'), (13, 'research_only'),
                                    (14, 'final_award_only')):
            raw = (f'<html><meta property="article:published_time" content="2024-06-01T07:00-04:00">'
                '<meta property="article:modified_time" content="2025-10-06T13:44-04:00">'
                '<time datetime="2024-06-01T12:00:00Z">June 1, 2024</time>'
                '<div class="text-with-summary"><p>A synthetic article, not real evidence. '
                'Mass production expected in the second half of 2028.</p>'
                '<p>Nonbinding proposed funding, subject to due diligence and final terms.</p></div>'
                f'<footer>Document {pos}</footer></html>').encode()
            name = f'artifacts/document-{pos}.body'
            (self.root / name).write_bytes(raw)
            document = {'displayed_index_position': pos,
                'url': f'https://www.nist.gov/news-events/news/2024/06/synthetic-{pos}',
                'index_date': '2024-06-01T12:00:00Z', 'index_title': f'Synthetic article {pos}',
                'body_path': name, 'sha256': runtime.digest(raw), 'bytes': len(raw),
                'source_checks_path': 'artifacts/source_checks.json', 'attempt_id': str(pos),
                'capture_started_at': '2026-01-01T00:00:00Z',
                'capture_finished_at': '2026-01-01T00:00:01Z'}
            self.roster['documents'].append(document)
            self.index['records'].append({'displayed_position': pos, 'url': document['url'],
                'index_date': document['index_date'], 'title': document['index_title'],
                'calendar_2024': True})
            self.ledger['attempts'].append({'id': str(pos), 'path': Path(name).name,
                'url': document['url'], 'final_url': document['url'], 'http_status': 200,
                'outcome': 'succeeded', 'redirect_count': 0, 'curl_exit_code': 0,
                'tls_verification_result': 0, 'sha256': document['sha256'], 'bytes': len(raw),
                'started_at': document['capture_started_at'], 'finished_at': document['capture_finished_at']})
            start, end = verifier.Metadata(raw).narratives[0]
            blocks = []
            for ordinal, match in enumerate(re.finditer(rb'<p>.*?</p>', raw), 1):
                block = match.group()
                blocks.append({'id': f'd{pos}-b{ordinal:03}', 'start': match.start(),
                    'end': match.end(), 'sha256': runtime.digest(block),
                    'text': verifier._html_visible_text(block.decode())})
            self.narratives['records'].append({**document,
                'narrative_span': {'start': start, 'end': end, 'sha256': runtime.digest(raw[start:end])},
                'full_narrative_text': verifier._html_visible_text(raw[start:end].decode()), 'blocks': blocks})
            self.fragment['articles'].append({'position': pos, 'classification': classification,
                'reason': 'Synthetic manual classification.', 'evidence': [f'd{pos}-b001'],
                'non_target_dates': ['Modification date is not a manufacturing event.']})
        outside = [{'displayed_position': pos,
                    'url': f'https://www.nist.gov/news-events/news/2025/06/synthetic-{pos}',
                    'index_date': '2025-06-01T12:00:00Z', 'title': f'Synthetic article {pos}',
                    'calendar_2024': False} for pos in range(1, 12)]
        self.index['records'] = outside + self.index['records']
        teasers = []
        for row in self.index['records']:
            visible = 'June 1, ' + row['index_date'][:4]
            row.update(visible_index_date=visible, summary='Synthetic summary.',
                       source_native_scope=None, pmt_title_review_candidate=False)
            teasers.append(f'<article class="nist-teaser" about="{row["url"]}">'
                f'<h3 class="nist-teaser__title"><a href="{row["url"]}">{row["title"]}</a></h3>'
                f'<div class="nist-teaser__date"><time datetime="{row["index_date"]}">{visible}</time></div>'
                '<div class="nist-teaser__content"><div property="schema:text">Synthetic summary.</div></div>'
                '</article>')
        index_raw = ('<html><body><h1>Archived News</h1><div>Displaying 1 - 14 of 14</div>'
            + ''.join(teasers) + '<nav class="pager"><a href="?page=0">1</a></nav></body></html>').encode()
        (self.root / 'artifacts/index.body').write_bytes(index_raw)
        index_url = 'https://www.nist.gov/news-events/news-updates/archive/search?combine=&o%5B0%5D=2810151&tag='
        for row, match in zip(self.index['records'], re.finditer(rb'<article\b.*?</article>', index_raw), strict=True):
            row.update(source_url=index_url, source_body_path='artifacts/index.body',
                source_body_sha256=runtime.digest(index_raw), article_span={'start': match.start(),
                'end': match.end(), 'sha256': runtime.digest(match.group())})
        self.index['pages'] = [{'page': 0, 'url': index_url, 'displayed_low': 1, 'displayed_high': 14,
            'displayed_total': 14, 'records': 14, 'calendar_2024_records': 3,
            'observed_pager_links': ['?page=0']}]
        self.index['denominators'] = {'all_index_rows': 14, 'calendar_2024_rows': 3,
            'outside_calendar_2024': 11, 'pmt_title_review_candidates': 0,
            'body_reviewed_articles': 0, 'scored_cases': 0}
        self.project = {'id': 'nist2024-d12-example', 'position': 12,
            'organization': 'Synthetic manufacturer', 'location': 'Source-native location',
            'scope': 'Two unnamed new production units, grouped without inferred identities.',
            'selection': 'included_manufacturing', 'selection_reason': 'Synthetic commercial scope.',
            'components': [{'description': 'Two unnamed units', 'count': 2}],
            'evidence': ['d12-b001'], 'conditions': [{'kind': 'funding_general',
                'interpretation': 'Proposed nonbinding award, not a physical prerequisite.',
                'evidence': ['d12-b002']}], 'timing': [{'event': 'mass_production',
                'literal': 'second half of 2028', 'classification': 'calendar_interval',
                'target': {'kind': 'interval', 'low': '2028-07-01', 'high': '2028-12-31'},
                'reason': 'Literal half-year, no midpoint.', 'evidence': ['d12-b001']}]}
        self.fragment['projects'] = [self.project]
        self.review = {'format': verifier.FORMAT, 'reviewed_at': '2026-01-02T00:00:00Z',
            'reviewed_by': 'Synthetic test', 'selection': 'Complete synthetic current index.',
            'prior_exposure': 'Exposed, not blind.', 'rights': 'Synthetic text, no publisher permission claim.',
            'boundaries': dict(verifier.BOUNDARIES)}

    def retain(self):
        def write(name, value):
            raw = runtime.canonical(value)
            (self.root / name).write_bytes(raw)
            return {'path': name, 'bytes': len(raw), 'sha256': runtime.digest(raw)}
        ledger = write('artifacts/source_checks.json', self.ledger)
        self.roster['capture_packets'] = [{'path': 'artifacts', 'source_checks_sha256': ledger['sha256']}]
        self.review['index_roster'] = write('review_plans/index.json', self.index)
        self.review['retention_roster'] = write('review_plans/retention.json', self.roster)
        self.narratives['source_roster_sha256'] = self.review['retention_roster']['sha256']
        self.review['narratives'] = write('artifacts/narratives.json', self.narratives)
        self.review['fragments'] = [write('review_plans/fragment.json', self.fragment)]
        descriptor = write('review_plans/review.json', self.review)
        return self.root / descriptor['path'], descriptor['sha256']

    def verify(self):
        path, sha = self.retain()
        return verifier.verify(path, sha, self.root)

    def test_complete_population_and_read_only_verification(self):
        path, sha = self.retain()
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = verifier.verify(path, sha, self.root)
        self.assertTrue(result['verified'])
        self.assertEqual(3, result['denominators']['articles'])
        self.assertEqual(1, result['denominators']['included_manufacturing_groups'])
        self.assertEqual(0, result['denominators']['scored_cases'])
        self.assertEqual(6, result['verified_blocks'])
        self.assertEqual('2025-10-06T13:44-04:00', result['source_metadata'][0]['article:modified_time'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_review_hash_and_source_mutation_fail(self):
        path, sha = self.retain()
        with self.assertRaisesRegex(ValueError, 'review hash'):
            verifier.verify(path, '0' * 64, self.root)
        (self.root / 'artifacts/document-12.body').write_bytes(b'Changed')
        with self.assertRaisesRegex(ValueError, 'input identity'):
            verifier.verify(path, sha, self.root)

    def test_all_articles_required_not_just_pmt_titles(self):
        self.fragment['articles'].pop()
        with self.assertRaisesRegex(ValueError, 'denominator'):
            self.verify()

    def test_duplicate_and_reordered_article_decisions_fail(self):
        original = copy.deepcopy(self.fragment['articles'])
        for rows in ([original[0], original[0], original[2]], list(reversed(original))):
            self.fragment['articles'] = rows
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, 'ordered source'):
                self.verify()

    def test_calendar_selection_cannot_hide_a_2024_article(self):
        self.index['records'][-1]['calendar_2024'] = False
        with self.assertRaisesRegex(ValueError, 'calendar-2024'):
            self.verify()

    def test_narrative_population_cannot_drop_a_body(self):
        self.narratives['records'].pop()
        with self.assertRaisesRegex(ValueError, 'populations'):
            self.verify()

    def test_source_identity_allocation_cannot_change(self):
        self.narratives['records'][0]['url'] = 'https://example.test/different'
        with self.assertRaisesRegex(ValueError, 'source identity'):
            self.verify()

    def test_capture_identity_and_chronology_fail_closed(self):
        original = copy.deepcopy(self.ledger['attempts'][0])
        for key, value in [('redirect_count', 1), ('http_status', 404),
                           ('finished_at', '2026-01-03T00:00:00Z'), ('url', 'https://example.test/other')]:
            self.ledger['attempts'][0] = {**original, key: value}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'capture identity|chronology'):
                self.verify()

    def test_future_review_timestamp_fails(self):
        self.review['reviewed_at'] = '9999-01-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'future'):
            self.verify()

    def test_full_narrative_cannot_be_replaced_with_one_valid_block(self):
        record = self.narratives['records'][0]
        block = record['blocks'][0]
        record['narrative_span'] = {key: block[key] for key in ('start', 'end', 'sha256')}
        record['full_narrative_text'] = block['text']
        with self.assertRaisesRegex(ValueError, 'complete source article'):
            self.verify()

    def test_all_narrative_blocks_are_retained(self):
        self.narratives['records'][0]['blocks'].pop()
        with self.assertRaisesRegex(ValueError, 'block inventory'):
            self.verify()

    def test_block_text_and_hash_are_bound(self):
        original = copy.deepcopy(self.narratives['records'][0]['blocks'][0])
        for key, value in [('text', 'Fabricated target'), ('sha256', '0' * 64), ('start', False)]:
            self.narratives['records'][0]['blocks'][0] = {**original, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify()

    def test_article_evidence_cannot_cross_source_documents(self):
        self.project['evidence'] = ['d13-b001']
        with self.assertRaisesRegex(ValueError, 'exact article'):
            self.verify()

    def test_new_pmt_requires_manufacturing_inventory(self):
        self.fragment['projects'] = []
        with self.assertRaisesRegex(ValueError, 'manufacturing project inventory'):
            self.verify()

    def test_final_award_cannot_become_new_pmt_project(self):
        self.project['position'] = 14
        with self.assertRaisesRegex(ValueError, 'newly announced PMT'):
            self.verify()

    def test_unnamed_group_multiplicity_and_unknown_count_preserved(self):
        self.assertEqual(1, self.verify()['denominators']['included_manufacturing_groups'])
        self.project['components'][0]['count'] = None
        self.assertTrue(self.verify()['verified'])
        for count in (0, False, -1, 1.5):
            self.project['components'][0]['count'] = count
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'component count'):
                self.verify()

    def test_duplicate_project_identity_fails(self):
        self.fragment['projects'].append(copy.deepcopy(self.project))
        with self.assertRaisesRegex(ValueError, 'unique source-native'):
            self.verify()

    def test_nonbinding_conditions_cannot_be_dropped(self):
        self.project['conditions'] = []
        with self.assertRaisesRegex(ValueError, 'nonbinding'):
            self.verify()

    def test_source_literal_and_normalized_precision_bound(self):
        event = self.project['timing'][0]
        original = copy.deepcopy(event)
        for key, value in [('literal', 'summer 2027'), ('classification', 'vague'),
                           ('target', {'kind': 'interval', 'low': '2028-12-31', 'high': '2028-07-01'})]:
            self.project['timing'][0] = {**original, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify()

    def test_undated_and_relative_projects_stay_in_population(self):
        self.project['timing'] = []
        result = self.verify()['denominators']
        self.assertEqual(1, result['manufacturing_groups_without_timing_assertions'])
        self.assertEqual(0, result['manufacturing_groups_with_calendar_targets'])
        self.project['timing'] = [{'event': 'synthetic_unresolved', 'literal': 'second half of 2028',
            'classification': 'relative', 'target': None, 'reason': 'Synthetic schema exercise only.',
            'evidence': ['d12-b001']}]
        self.assertEqual({'relative': 1}, self.verify()['denominators']['timing_association_classifications'])

    def test_shared_timing_counted_as_associations_not_independent_statements(self):
        other = copy.deepcopy(self.project)
        other['id'] = 'nist2024-d12-related-project'
        self.fragment['projects'].append(other)
        result = self.verify()['denominators']
        self.assertEqual(2, result['manufacturing_project_timing_associations'])
        self.assertEqual(1, result['distinct_evidence_bound_timing_statements'])
        other['timing'][0]['target']['high'] = '2028-12-30'
        with self.assertRaisesRegex(ValueError, 'conflicting interpretations'):
            self.verify()

    def test_consistently_removed_article_fails_against_original_index_body(self):
        self.index['records'].pop()
        self.roster['documents'].pop()
        self.narratives['records'].pop()
        self.fragment['articles'].pop()
        with self.assertRaisesRegex(ValueError, 'denominator'):
            self.verify()

    def test_missing_or_corrupt_index_source_evidence_fails(self):
        self.index['records'][0]['source_body_path'] = 'artifacts/nonexistent.body'
        with self.assertRaisesRegex(ValueError, 'source identity conflict'):
            self.verify()
        self.index['records'][0]['source_body_path'] = 'artifacts/index.body'
        self.index['records'][0]['article_span']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'exact span'):
            self.verify()

    def test_nonexistent_index_body_cannot_pass_a_rebound_roster(self):
        for row in self.index['records']:
            row['source_body_path'] = 'artifacts/nonexistent.body'
        with self.assertRaises(FileNotFoundError):
            self.verify()

    def test_publication_metadata_visible_and_index_dates_must_agree(self):
        name = self.root / 'artifacts/document-12.body'
        original = name.read_bytes()
        for old, replacement in ((b'2024-06-01T07:00-04:00', b'2025-06-01T07:00-04:00'),
                                 (b'2024-06-01T07:00-04:00', b'xxxx-06-01T07:00-04:00'),
                                 (b'article:published_time', b'article:publisher_time'),
                                 (b'June 1, 2024', b'June 2, 2024')):
            raw = original.replace(old, replacement)
            name.write_bytes(raw)
            sha = runtime.digest(raw)
            self.roster['documents'][0]['sha256'] = sha
            self.narratives['records'][0]['sha256'] = sha
            self.ledger['attempts'][0]['sha256'] = sha
            with self.subTest(replacement=replacement), self.assertRaisesRegex(ValueError, 'publication metadata'):
                self.verify()

    def test_research_branch_retained_but_not_manufacturing_target(self):
        research = copy.deepcopy(self.project)
        research.update(id='nist2024-d12-research', selection='excluded_research', timing=[])
        self.fragment['projects'].append(research)
        self.assertEqual(1, self.verify()['denominators']['excluded_research_groups'])
        research['conditions'] = []
        self.assertTrue(self.verify()['verified'])
        research['timing'] = copy.deepcopy(self.project['timing'])
        with self.assertRaisesRegex(ValueError, 'research scope'):
            self.verify()

    def test_boundary_claims_and_unrequested_fields_rejected(self):
        self.review['boundaries']['outcomes_adjudicated'] = True
        with self.assertRaisesRegex(ValueError, 'boundaries'):
            self.verify()
        self.review['boundaries']['outcomes_adjudicated'] = False
        self.project['outcome'] = 'operational'
        with self.assertRaises(ValueError):
            self.verify()

    def test_path_traversal_and_symlink_input_rejected(self):
        path, sha = self.retain()
        self.review['narratives']['path'] = 'artifacts/../artifacts/narratives.json'
        path.write_bytes(runtime.canonical(self.review))
        with self.assertRaisesRegex(ValueError, 'relative evidence path'):
            verifier.verify(path, runtime.digest(path.read_bytes()), self.root)
        path, sha = self.retain()
        body = self.root / 'artifacts/document-12.body'
        body.rename(self.root / 'artifacts/moved.body')
        body.symlink_to(self.root / 'artifacts/moved.body')
        with self.assertRaises(OSError):
            verifier.verify(path, sha, self.root)


if __name__ == '__main__':
    unittest.main()
