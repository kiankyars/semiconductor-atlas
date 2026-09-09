"""Evidence-adapter contracts with synthetic source bytes, not publisher truth."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import evaluate_source_guidance as adapter
from scripts import frozen_runtime as runtime


class RetainedGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        (self.root / 'artifacts').mkdir()
        (self.root / 'review_plans').mkdir()
        self.body = b'Synthetic target and outcome context, not primary evidence.'
        self.name = 'artifacts/evidence.body'
        runtime.write_new(self.root / self.name, self.body)
        self.ledger_name = 'artifacts/source_checks.json'
        ledger = {'attempts': [{'id': 'source-a', 'path': 'evidence.body',
            'url': 'https://example.test/source', 'final_url': 'https://example.test/source',
            'started_at': '2026-09-08T00:00:00Z', 'finished_at': '2026-09-08T00:00:00Z',
            'http_status': 200, 'outcome': 'succeeded', 'redirect_count': 0,
            'curl_exit_code': 0, 'tls_verification_result': 0,
            'sha256': runtime.digest(self.body), 'bytes': len(self.body)}]}
        self.ledger = runtime.canonical(ledger)
        runtime.write_new(self.root / self.ledger_name, self.ledger)
        identity = {'case_id': 'a', 'project_id': 'a', 'project_group': 'a',
                    'event_type': 'production_start', 'scope_id': 'new-fab'}
        ref = {'kind': 'reviewed_primary', 'body_path': self.name,
               'support_spans': [{'path': self.name, 'start': 0, 'end': len(self.body),
                                  'sha256': runtime.digest(self.body)}],
               'interpretation': 'Synthetic manual review only.',
               'source_document_date': '2021-02-04', 'capture_finished_at': '2026-09-08T00:00:00Z',
               'source_url': 'https://example.test/source',
               'capture': {'ledger_path': self.ledger_name, 'attempt_id': 'source-a'}}
        self.review = {'format': adapter.REVIEW_FORMAT, 'reviewed_at': '2026-09-08T01:00:00Z',
            'reviewed_by': 'Synthetic test', 'selection': 'One synthetic test case.',
            'prior_exposure': 'Exposed synthetic outcome.', 'rights': 'No real source permissions tested.',
            'boundaries': dict(adapter.BOUNDARIES),
            'input_files': {self.name: {'bytes': len(self.body), 'sha256': runtime.digest(self.body)},
                           self.ledger_name: {'bytes': len(self.ledger), 'sha256': runtime.digest(self.ledger)}},
            'source_references': {'ref': ref},
            'roster': [{**identity, 'disposition': 'eligible', 'reason': 'Synthetic review.',
                        'target': {'kind': 'interval', 'low': '2021-07-01', 'high': '2021-09-30'},
                        'source_reference_ids': ['ref'], 'source_document_date': '2021-02-04'}],
            'outcomes': [{**identity, 'reason': 'Synthetic review.',
                          'outcome': {'status': 'observed', 'low': '2021-08-01', 'high': '2021-08-31'},
                          'source_reference_ids': ['ref'], 'source_document_date': '2021-02-04'}]}
        self.path = self.root / 'review_plans/review.json'

    def retain(self, review=None):
        raw = runtime.canonical(review or self.review)
        self.path.write_bytes(raw)
        return runtime.digest(raw)

    def load(self, review=None):
        return adapter.load_review(self.path, self.retain(review), self.root)

    def test_create_and_exact_read_only_verification(self):
        sha = self.retain()
        output = self.root / 'artifacts/result.json'
        with patch.object(adapter, 'now', return_value='2026-09-09T00:00:00Z'):
            created = adapter.create(self.path, sha, self.root, output)
            before = output.read_bytes()
            verified = adapter.verify(output, created['sha256'], self.path, sha, self.root)
        self.assertIs(verified['verified'], True)
        self.assertEqual(before, output.read_bytes())
        self.assertEqual(1, created['denominators']['scored_cases'])
        report = adapter.strict_json(before)
        self.assertEqual(adapter.BOUNDARIES, report['result']['review']['boundaries'])
        self.assertIn('manually reviewed', report['result']['verification_scope'])

    def test_create_does_not_replace_existing_output(self):
        sha = self.retain()
        output = self.root / 'artifacts/result.json'
        output.write_bytes(b'User owned data')
        with self.assertRaises(FileExistsError):
            adapter.create(self.path, sha, self.root, output)
        self.assertEqual(b'User owned data', output.read_bytes())

    def test_wrong_review_and_input_hashes_fail(self):
        self.retain()
        with self.assertRaisesRegex(ValueError, 'review hash'):
            adapter.load_review(self.path, '0' * 64, self.root)
        (self.root / self.name).write_bytes(self.body + b' changed')
        with self.assertRaisesRegex(ValueError, 'retained evidence'):
            self.load()

    def test_support_span_bounds_hash_and_membership_fail(self):
        for field, value in (('start', False), ('start', -1), ('end', len(self.body) + 1),
                             ('end', 0), ('sha256', '0' * 64), ('path', 'artifacts/missing')):
            review = copy.deepcopy(self.review)
            review['source_references']['ref']['support_spans'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load(review)

    def test_missing_extra_or_screening_only_references_fail_for_eligible(self):
        for mode in ('missing', 'extra', 'screening'):
            review = copy.deepcopy(self.review)
            if mode == 'missing':
                review['roster'][0]['source_reference_ids'] = ['absent']
            elif mode == 'extra':
                review['source_references']['extra'] = copy.deepcopy(review['source_references']['ref'])
            else:
                review['source_references']['ref']['kind'] = 'screening_note'
                review['source_references']['ref']['capture_finished_at'] = None
                review['source_references']['ref']['source_url'] = None
                review['source_references']['ref']['capture'] = None
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.load(review)

    def test_source_dates_need_reference_and_review_follows_capture(self):
        for mode in ('date', 'capture', 'execution'):
            review = copy.deepcopy(self.review)
            if mode == 'date':
                review['roster'][0]['source_document_date'] = '2020-01-01'
            elif mode == 'capture':
                review['source_references']['ref']['capture_finished_at'] = '2026-09-10T00:00:00Z'
            else:
                review['reviewed_at'] = '9999-01-01T00:00:00Z'
            sha = self.retain(review)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                adapter.create(self.path, sha, self.root, self.root / 'artifacts/result.json')

    def test_prospective_or_publication_boundary_cannot_be_enabled(self):
        for field in adapter.BOUNDARIES:
            for value in (True, 0):
                review = copy.deepcopy(self.review)
                review['boundaries'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.load(review)

    def test_duplicate_json_nonfinite_and_unsafe_paths_fail(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                adapter.strict_json(raw)
        for name in ('/tmp/source', '../source', 'artifacts/../source', 'artifacts//source',
                     'artifacts/./source', 'artifacts\\source', 'scripts/source'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                adapter.relative(name)

    def test_symlinked_evidence_cannot_replace_regular_file(self):
        other = self.root / 'artifacts/other.body'
        other.write_bytes(self.body)
        (self.root / self.name).unlink()
        (self.root / self.name).symlink_to(other)
        with self.assertRaises(OSError):
            self.load()

    def test_verification_rejects_changed_score_clock_or_producer(self):
        sha = self.retain()
        output = self.root / 'artifacts/result.json'
        with patch.object(adapter, 'now', return_value='2026-09-09T00:00:00Z'):
            adapter.create(self.path, sha, self.root, output)
        original = adapter.strict_json(output.read_bytes())
        for mode in ('score', 'clock', 'producer'):
            report = copy.deepcopy(original)
            if mode == 'score':
                report['result']['diagnostic']['denominators']['scored_cases'] = 5
            elif mode == 'clock':
                report['execution_started_at'] = '2021-01-01T00:00:00Z'
            else:
                report['producer_files']['scripts/evaluate_source_guidance.py'] = '0' * 64
            raw = runtime.canonical(report)
            output.write_bytes(raw)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                adapter.verify(output, runtime.digest(raw), self.path, sha, self.root)

    def test_all_unresolved_screening_notes_remain_unscored(self):
        review = copy.deepcopy(self.review)
        review['roster'][0].update(disposition='unresolved', target=None, source_document_date=None)
        review['outcomes'][0].update(outcome={'status': 'unknown'}, source_document_date=None)
        review['source_references']['ref'].update(kind='screening_note', capture_finished_at=None,
                                                  source_document_date=None, source_url=None, capture=None)
        result = self.load(review)['diagnostic']
        self.assertEqual(1, result['denominators']['unresolved_cases'])
        self.assertEqual(0, result['denominators']['scored_cases'])

    def test_substantive_outcomes_require_primary_review_for_every_disposition(self):
        outcomes = ({'status': 'observed', 'low': '2021-08-01', 'high': '2021-08-31'},
                    {'status': 'cancelled'},
                    {'status': 'explicit_nonoccurrence_through', 'through': '2021-09-30'})
        for disposition in ('eligible', 'unresolved', 'excluded'):
            for outcome in outcomes:
                for refs in (['note'], ['note', 'ref']):
                    review = copy.deepcopy(self.review)
                    review['roster'][0]['disposition'] = disposition
                    review['outcomes'][0].update(outcome=outcome, source_reference_ids=refs)
                    note = copy.deepcopy(review['source_references']['ref'])
                    note.update(kind='screening_note', capture_finished_at=None, source_url=None, capture=None)
                    review['source_references']['note'] = note
                    with self.subTest(disposition=disposition, outcome=outcome, refs=refs):
                        with self.assertRaisesRegex(ValueError, 'substantive outcomes require primary-source'):
                            self.load(review)

    def test_primary_outcomes_for_unresolved_and_excluded_cases_stay_unscored(self):
        outcomes = ({'status': 'observed', 'low': '2021-08-01', 'high': '2021-08-31'},
                    {'status': 'cancelled'},
                    {'status': 'explicit_nonoccurrence_through', 'through': '2021-09-30'})
        for disposition in ('unresolved', 'excluded'):
            for outcome in outcomes:
                review = copy.deepcopy(self.review)
                review['roster'][0].update(disposition=disposition, target=None)
                review['outcomes'][0]['outcome'] = outcome
                with self.subTest(disposition=disposition, outcome=outcome):
                    result = self.load(review)['diagnostic']
                    self.assertEqual(1, result['denominators']['screened_cases'])
                    self.assertEqual(1, result['denominators'][disposition + '_cases'])
                    self.assertEqual(0, result['denominators']['scored_cases'])
                    self.assertEqual({outcome['status']: 1}, result['screened_outcome_status_counts'])
                    self.assertIsNone(result['cases'][0]['target_met'])

    def test_unknown_outcomes_allow_screening_notes_or_no_references(self):
        for disposition in ('eligible', 'unresolved', 'excluded'):
            for refs in ([], ['note']):
                review = copy.deepcopy(self.review)
                review['roster'][0]['disposition'] = disposition
                review['outcomes'][0].update(outcome={'status': 'unknown'}, source_reference_ids=refs,
                                              source_document_date=None)
                if refs:
                    note = copy.deepcopy(review['source_references']['ref'])
                    note.update(kind='screening_note', capture_finished_at=None, source_url=None, capture=None)
                    review['source_references']['note'] = note
                with self.subTest(disposition=disposition, refs=refs):
                    result = self.load(review)['diagnostic']
                    self.assertEqual({'unknown': 1}, result['screened_outcome_status_counts'])
                    self.assertEqual(0, result['denominators']['scored_cases'])

    def test_eligible_target_still_requires_primary_review_when_outcome_is_unknown(self):
        review = copy.deepcopy(self.review)
        review['source_references']['ref'].update(kind='screening_note', capture_finished_at=None,
                                                  source_url=None, capture=None)
        review['outcomes'][0].update(outcome={'status': 'unknown'}, source_reference_ids=[],
                                      source_document_date=None)
        with self.assertRaisesRegex(ValueError, 'eligible targets.*primary-source review'):
            self.load(review)

    def test_capture_binding_cannot_be_changed_by_rehashing_ledger(self):
        for field, value in (('url', 'https://example.test/wrong'), ('http_status', 302),
                             ('finished_at', '2026-09-08T00:01:00Z'), ('sha256', '0' * 64),
                             ('path', 'wrong.body'), ('redirect_count', 1)):
            ledger = adapter.strict_json(self.ledger)
            ledger['attempts'][0][field] = value
            body = runtime.canonical(ledger)
            (self.root / self.ledger_name).write_bytes(body)
            review = copy.deepcopy(self.review)
            review['input_files'][self.ledger_name] = {'bytes': len(body), 'sha256': runtime.digest(body)}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'primary capture'):
                self.load(review)
