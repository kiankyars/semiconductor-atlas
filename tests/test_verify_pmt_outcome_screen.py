"""Synthetic frozen-population guards, not independent evidence adjudication."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import frozen_runtime as runtime
from scripts import verify_pmt_outcome_screen as screen


class FrozenCaseTests(unittest.TestCase):
    def setUp(self):
        self.projects = [
            {'id': 'nist2024-d17-first', 'position': 17, 'selection': 'included_manufacturing',
             'timing': [{'event': 'first_200mm_chip',
                         'target': {'kind': 'interval', 'low': '2026-01-01', 'high': '2026-12-31'}},
                        {'event': 'additional_expansion', 'target': None}]},
            {'id': 'nist2024-d18-second', 'position': 18, 'selection': 'included_manufacturing',
             'timing': []}]
        self.dates = {17: '2024-12-13', 18: '2024-12-12'}
        self.roster = []
        for project in self.projects:
            key, pos = project['id'], project['position']
            for ordinal, timing in enumerate(project['timing'] or [None], 1):
                target = timing['target'] if timing else None
                self.roster.append({'case_id': f'{key}--timing-{ordinal:02d}' if timing else f'{key}--no-timing',
                    'project_id': key, 'project_group': key, 'scope_id': f'{key}--original-pmt-scope',
                    'event_type': timing['event'] if timing else 'project_intervention_undated',
                    'disposition': 'eligible' if target is not None else 'unresolved', 'target': target,
                    'reason': 'Synthetic frozen original case.', 'source_reference_ids': [f'nist2024-d{pos}'],
                    'source_document_date': self.dates[pos]})
        self.outcomes = [{**{key: row[key] for key in screen.diagnostic.IDENTITY},
            'reason': 'No matched original outcome is asserted.', 'outcome': {'status': 'unknown'},
            'source_reference_ids': [], 'source_document_date': None} for row in self.roster]
        self.supplemental = []

    def validate(self):
        return screen.validate_cases(self.projects, self.roster, self.outcomes, self.supplemental, self.dates)

    def add_supplement(self):
        project = self.projects[0]['id']
        case = {'case_id': f'{project}--supplement-sample', 'project_id': project, 'project_group': project,
            'scope_id': f'{project}--sample-scope', 'event_type': 'sample_production_started',
            'disposition': 'unresolved', 'target': None, 'reason': 'Distinct sample event, not first 200mm chip.',
            'source_reference_ids': ['later-primary'], 'source_document_date': '2026-07-13'}
        outcome = {**{key: case[key] for key in screen.diagnostic.IDENTITY},
            'reason': 'Source reports samples had started by publication, without exact start day.',
            'outcome': {'status': 'observed_by', 'through': '2026-07-13'},
            'source_reference_ids': ['later-primary'], 'source_document_date': '2026-07-13'}
        declared = {'case_id': case['case_id'], 'project_id': project,
                    'relation': 'distinct_event_same_project', 'reason': 'Does not resolve original target.'}
        self.roster.append(case)
        self.outcomes.append(outcome)
        self.roster.sort(key=lambda row: row['case_id'])
        self.outcomes.sort(key=lambda row: row['case_id'])
        self.supplemental.append(declared)
        return case, outcome, declared

    def test_all_original_associations_and_untimed_groups_count(self):
        counts = self.validate()
        self.assertEqual(2, counts['manufacturing_project_groups'])
        self.assertEqual(3, counts['original']['cases'])
        self.assertEqual(1, counts['original']['calendar_target_cases'])
        self.assertEqual(3, counts['original']['unknown_outcomes'])
        self.assertEqual(0, counts['original']['scored_cases'])
        self.assertEqual(0, counts['supplemental']['cases'])

    def test_separate_samples_do_not_resolve_original_target(self):
        self.add_supplement()
        counts = self.validate()
        self.assertEqual(3, counts['original']['unknown_outcomes'])
        self.assertEqual(0, counts['original']['scored_cases'])
        self.assertEqual(1, counts['supplemental']['cases'])
        self.assertEqual({'observed_by': 1}, counts['supplemental']['outcome_status_counts'])
        self.assertEqual(0, counts['supplemental']['calendar_target_cases'])
        self.assertEqual(0, counts['supplemental']['scored_cases'])
        self.assertEqual(4, counts['combined']['screened_cases'])
        self.assertEqual(2, counts['combined']['screened_project_groups'])

    def test_dropping_each_original_case_fails_even_with_paired_outcome(self):
        for ordinal in range(3):
            with self.subTest(ordinal=ordinal), self.assertRaises(ValueError):
                screen.validate_cases(self.projects, self.roster[:ordinal] + self.roster[ordinal + 1:],
                    self.outcomes[:ordinal] + self.outcomes[ordinal + 1:], [], self.dates)

    def test_original_target_cannot_change_to_deadline_or_shifted_interval(self):
        for target in (None, {'kind': 'deadline', 'through': '2026-12-31'},
                       {'kind': 'interval', 'low': '2026-07-01', 'high': '2026-12-31'}):
            with self.subTest(target=target), self.assertRaises(ValueError):
                roster = copy.deepcopy(self.roster)
                roster[0]['target'] = target
                screen.validate_cases(self.projects, roster, self.outcomes, [], self.dates)

    def test_original_identity_changes_fail_even_when_outcome_agrees(self):
        for field in screen.diagnostic.IDENTITY:
            with self.subTest(field=field), self.assertRaises(ValueError):
                roster, outcomes = copy.deepcopy((self.roster, self.outcomes))
                roster[0][field] += '-changed'
                outcomes[0][field] = roster[0][field]
                screen.validate_cases(self.projects, roster, outcomes, [], self.dates)

    def test_original_date_or_reference_changes_fail(self):
        for field, value in (('source_document_date', '2025-01-01'),
                             ('source_reference_ids', ['later-primary']), ('disposition', 'unresolved')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                roster = copy.deepcopy(self.roster)
                roster[0][field] = value
                screen.validate_cases(self.projects, roster, self.outcomes, [], self.dates)

    def test_untimed_original_cannot_gain_target(self):
        self.roster[-1]['target'] = {'kind': 'deadline', 'through': '2027-12-31'}
        self.roster[-1]['disposition'] = 'eligible'
        with self.assertRaises(ValueError):
            self.validate()

    def test_supplement_cannot_replace_dropped_original(self):
        self.add_supplement()
        original = self.projects[0]['id'] + '--timing-01'
        self.roster = [row for row in self.roster if row['case_id'] != original]
        self.outcomes = [row for row in self.outcomes if row['case_id'] != original]
        with self.assertRaises(ValueError):
            self.validate()

    def test_supplement_cannot_use_original_case_id(self):
        _, _, item = self.add_supplement()
        item['case_id'] = self.projects[0]['id'] + '--timing-01'
        with self.assertRaises(ValueError):
            self.validate()

    def test_supplement_cannot_add_target_even_if_unresolved(self):
        case, _, _ = self.add_supplement()
        case['target'] = {'kind': 'deadline', 'through': '2026-12-31'}
        with self.assertRaises(ValueError):
            self.validate()

    def test_supplement_cannot_be_eligible_or_excluded(self):
        case, _, _ = self.add_supplement()
        for value in ('eligible', 'excluded'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                case['disposition'] = value
                self.validate()

    def test_supplement_cannot_add_project(self):
        case, outcome, declared = self.add_supplement()
        for row in (case, outcome, declared):
            row['project_id'] = 'nist2024-d99-other'
        for row in (case, outcome):
            row['project_group'] = 'nist2024-d99-other'
        with self.assertRaises(ValueError):
            self.validate()

    def test_supplement_event_and_scope_must_be_distinct(self):
        case, outcome, _ = self.add_supplement()
        for field, value in (('event_type', 'first_200mm_chip'),
                             ('scope_id', self.projects[0]['id'] + '--original-pmt-scope')):
            original = case[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                case[field] = outcome[field] = value
                self.validate()
            case[field] = outcome[field] = original

    def test_supplement_unknown_or_without_evidence_fails(self):
        _, outcome, _ = self.add_supplement()
        for field, value in (('outcome', {'status': 'unknown'}), ('source_reference_ids', [])):
            original = outcome[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                outcome[field] = value
                self.validate()
            outcome[field] = original

    def test_supplement_requires_explicit_declaration(self):
        self.add_supplement()
        self.supplemental = []
        with self.assertRaises(ValueError):
            self.validate()

    def test_duplicate_supplement_declaration_fails(self):
        self.add_supplement()
        self.supplemental += copy.deepcopy(self.supplemental)
        with self.assertRaises(ValueError):
            self.validate()

    def test_duplicate_case_ids_fail(self):
        self.roster.insert(0, copy.deepcopy(self.roster[0]))
        self.outcomes.insert(0, copy.deepcopy(self.outcomes[0]))
        with self.assertRaises(ValueError):
            self.validate()

    def test_duplicate_project_ids_fail(self):
        self.projects.append(copy.deepcopy(self.projects[0]))
        with self.assertRaises(ValueError):
            self.validate()

    def test_research_groups_cannot_enter_frozen_manufacturing_inputs(self):
        self.projects[0]['selection'] = 'excluded_research'
        with self.assertRaises(ValueError):
            self.validate()

    def test_wrong_supplement_relation_or_extra_claim_field_fails(self):
        _, _, declaration = self.add_supplement()
        declaration['relation'] = 'original_target_attained'
        with self.assertRaises(ValueError):
            self.validate()
        declaration['relation'] = 'distinct_event_same_project'
        declaration['capacity'] = 1
        with self.assertRaises(ValueError):
            self.validate()

    def test_explanation_must_remain_nonempty(self):
        self.roster[0]['reason'] = ' '
        with self.assertRaises(ValueError):
            self.validate()

    def test_original_boolean_scores_remain_distinct_from_supplement_counts(self):
        self.outcomes[0].update(outcome={'status': 'observed', 'low': '2026-05-01', 'high': '2026-05-31'},
                                source_reference_ids=['later-primary'], source_document_date='2026-07-13')
        self.add_supplement()
        counts = self.validate()
        self.assertEqual(1, counts['original']['scored_cases'])
        self.assertEqual(0, counts['supplemental']['scored_cases'])

    def test_validation_is_nonmutating(self):
        self.add_supplement()
        before = copy.deepcopy((self.projects, self.roster, self.outcomes, self.supplemental, self.dates))
        self.validate()
        self.assertEqual(before, (self.projects, self.roster, self.outcomes, self.supplemental, self.dates))


class ReferenceBindingTests(unittest.TestCase):
    def setUp(self):
        self.document = {'body_path': 'artifacts/capture/source.body', 'bytes': 50, 'sha256': 'a' * 64,
            'source_document_date': '2026-07-13', 'url': 'https://www.nist.gov/news-events/synthetic',
            'capture_finished_at': '2026-09-09T04:00:00Z',
            'source_checks_path': 'artifacts/capture/source_checks.json', 'attempt_id': 'source'}
        self.narrative = {'start': 5, 'end': 40, 'sha256': 'b' * 64}
        self.reference = {'kind': 'reviewed_primary', 'body_path': self.document['body_path'],
            'source_document_date': self.document['source_document_date'],
            'source_url': self.document['url'], 'capture_finished_at': self.document['capture_finished_at'],
            'capture': {'ledger_path': self.document['source_checks_path'], 'attempt_id': 'source'},
            'support_spans': [{'path': self.document['body_path'], **self.narrative}],
            'interpretation': 'Synthetic full narrative.'}
        self.inventory = {self.document['body_path']: {'bytes': 50, 'sha256': 'a' * 64},
                          self.document['source_checks_path']: {'bytes': 100, 'sha256': 'c' * 64}}

    def test_exact_reference_and_inventory_bind(self):
        screen.validate_primary_reference(self.reference, self.document, self.narrative)
        screen.validate_input_binding(self.inventory, self.document, 'c' * 64)

    def test_each_primary_identity_field_is_required(self):
        for field in ('kind', 'body_path', 'source_document_date', 'source_url', 'capture_finished_at'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                reference = copy.deepcopy(self.reference)
                reference[field] = 'wrong'
                screen.validate_primary_reference(reference, self.document, self.narrative)

    def test_capture_ledger_and_attempt_are_exact(self):
        for field in ('ledger_path', 'attempt_id'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                reference = copy.deepcopy(self.reference)
                reference['capture'][field] = 'wrong'
                screen.validate_primary_reference(reference, self.document, self.narrative)

    def test_narrative_cannot_be_narrowed_or_swapped(self):
        for field, value in (('path', 'artifacts/unrelated.body'), ('start', 6), ('end', 39), ('sha256', 'd' * 64)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                reference = copy.deepcopy(self.reference)
                reference['support_spans'][0][field] = value
                screen.validate_primary_reference(reference, self.document, self.narrative)

    def test_extra_span_is_not_full_single_narrative_contract(self):
        self.reference['support_spans'] *= 2
        with self.assertRaises(ValueError):
            screen.validate_primary_reference(self.reference, self.document, self.narrative)

    def test_inventory_body_hash_and_size_preserve_frozen_values(self):
        for field, value in (('bytes', 49), ('sha256', 'd' * 64)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                inventory = copy.deepcopy(self.inventory)
                inventory[self.document['body_path']][field] = value
                screen.validate_input_binding(inventory, self.document, 'c' * 64)

    def test_inventory_capture_ledger_hash_preserves_frozen_value(self):
        with self.assertRaises(ValueError):
            screen.validate_input_binding(self.inventory, self.document, 'd' * 64)


class ArticleMetadataTests(unittest.TestCase):
    def setUp(self):
        self.raw = (b'<meta property="article:published_time" content="2026-07-13T08:00-04:00">'
                    b'<meta property="article:modified_time" content="2026-07-13T11:42-04:00">'
                    b'<time datetime="2026-07-13T12:00:00Z">July 13, 2026</time>'
                    b'<div class="text-with-summary"><p>Samples started.</p></div>')

    def test_source_day_and_exact_complete_narrative(self):
        day, span, metadata = screen.article_metadata(self.raw, '2026-09-09T04:00:00Z')
        self.assertEqual('2026-07-13', day)
        self.assertEqual(b'<div class="text-with-summary"><p>Samples started.</p></div>',
                         self.raw[span['start']:span['end']])
        self.assertEqual(runtime.digest(self.raw[span['start']:span['end']]), span['sha256'])
        self.assertEqual('2026-07-13T08:00-04:00', metadata['article:published_time'])

    def test_visible_date_mismatch_fails(self):
        with self.assertRaises(ValueError):
            screen.article_metadata(self.raw.replace(b'July 13, 2026', b'July 12, 2026'), '2026-09-09T04:00:00Z')

    def test_visible_time_mismatch_fails(self):
        with self.assertRaises(ValueError):
            screen.article_metadata(self.raw.replace(b'2026-07-13T12:00:00Z', b'2026-07-12T12:00:00Z'), '2026-09-09T04:00:00Z')

    def test_metadata_cannot_postdate_capture(self):
        with self.assertRaises(ValueError):
            screen.article_metadata(self.raw, '2026-07-13T12:00:00Z')

    def test_modified_cannot_precede_published(self):
        with self.assertRaises(ValueError):
            screen.article_metadata(self.raw.replace(b'2026-07-13T11:42-04:00', b'2026-07-12T11:42-04:00'),
                                    '2026-09-09T04:00:00Z')

    def test_naive_metadata_date_fails(self):
        with self.assertRaises(ValueError):
            screen.article_metadata(self.raw.replace(b'2026-07-13T08:00-04:00', b'2026-07-13T08:00'),
                                    '2026-09-09T04:00:00Z')

    def test_duplicate_narrative_or_publication_metadata_fails(self):
        for extra in (b'<div class="text-with-summary"><p>Other.</p></div>',
                      b'<meta property="article:published_time" content="2026-07-13T08:00-04:00">'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                screen.article_metadata(self.raw + extra, '2026-09-09T04:00:00Z')


class WrapperIntegrationTests(unittest.TestCase):
    """Test joins with real diagnostic loading; upstream capture/population are mocked.

    The mocks stand in for separately tested full archive/capture validation, not
    for bytes, ledger identity, source metadata, case membership or chronology.
    """
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = {'id': 'nist2024-d17-synthetic', 'position': 17,
                        'selection': 'included_manufacturing', 'timing': []}
        inventory, references, documents = {}, {}, []
        for label, day, clock in [('original', '2024-12-13', '2025-05-31T12:00:00Z'),
                                  ('later', '2025-06-01', '2025-06-02T12:00:00Z')]:
            visible = 'December 13, 2024' if label == 'original' else 'June 01, 2025'
            raw = (f'<meta property="article:published_time" content="{day}T12:00+00:00">'
                f'<meta property="article:modified_time" content="{day}T13:00+00:00">'
                f'<time datetime="{day}T12:00:00Z">{visible}</time>'
                '<div class="text-with-summary"><p>Synthetic statement.</p></div>').encode()
            body = self.put(f'artifacts/{label}/responses/source.body', raw)
            attempt = {'id': 'source', 'purpose': 'source_document', 'path': 'responses/source.body',
                'url': f'https://www.nist.gov/news-events/{label}',
                'final_url': f'https://www.nist.gov/news-events/{label}',
                'redirect_count': 0, 'http_status': 200, 'outcome': 'succeeded',
                'curl_exit_code': 0, 'tls_verification_result': 0,
                'sha256': body['sha256'], 'bytes': body['bytes'], 'started_at': clock, 'finished_at': clock}
            ledger = self.put(f'artifacts/{label}/source_checks.json', {'attempts': [attempt]})
            doc = {'body_path': body['path'], 'bytes': body['bytes'], 'sha256': body['sha256'],
                'url': attempt['url'], 'source_document_date': day, 'capture_finished_at': clock,
                'source_checks_path': ledger['path'], 'attempt_id': 'source', 'displayed_index_position': 17}
            _, narrative, metadata = screen.article_metadata(raw, clock)
            ref = {'kind': 'reviewed_primary', 'body_path': body['path'],
                'source_document_date': day, 'source_url': attempt['url'], 'capture_finished_at': clock,
                'capture': {'ledger_path': ledger['path'], 'attempt_id': 'source'},
                'support_spans': [{'path': body['path'], **narrative}], 'interpretation': 'Synthetic review.'}
            references['nist2024-d17' if label == 'original' else 'later-source'] = ref
            for descriptor in (body, ledger):
                inventory[descriptor['path']] = {key: descriptor[key] for key in ('bytes', 'sha256')}
            if label == 'original':
                documents.append(doc)
                original_narrative, original_metadata, original_ledger = narrative, metadata, ledger
            else:
                run = self.put('artifacts/later/run.json', {'started_at': clock, 'finished_at': clock})
                self.manifest = self.put('artifacts/later/manifest.json', {'files': {
                    'source_checks.json': {key: ledger[key] for key in ('bytes', 'sha256')},
                    'run.json': {key: run[key] for key in ('bytes', 'sha256')}}})
        fragments = self.put('review_plans/fragment.json', {'projects': [self.project]})
        retention = self.put('review_plans/retention.json', {'documents': documents, 'capture_packets': [
            {'path': 'artifacts/original', 'source_checks_sha256': original_ledger['sha256']}]})
        narratives = self.put('review_plans/narratives.json', {'records': [
            {'displayed_index_position': 17, 'narrative_span': original_narrative}]})
        parent = self.put('review_plans/parent.json', {'reviewed_at': '2025-06-01T12:00:00Z',
            'fragments': [fragments], 'retention_roster': retention, 'narratives': narratives})
        self.parent_result = {'verified': True, 'source_metadata': [{'position': 17, **original_metadata}]}
        key = self.project['id']
        identity = {'case_id': key + '--no-timing', 'project_id': key, 'project_group': key,
                    'scope_id': key + '--original-pmt-scope', 'event_type': 'project_intervention_undated'}
        self.diagnostic = {'format': screen.adapter.REVIEW_FORMAT, 'reviewed_at': '2025-06-03T12:00:00Z',
            'reviewed_by': 'Synthetic test.', 'selection': 'Synthetic complete original population.',
            'prior_exposure': 'Exposed retrospective fixture.', 'rights': 'No external requests.',
            'boundaries': screen.adapter.BOUNDARIES, 'input_files': inventory, 'source_references': references,
            'roster': [{**identity, 'reason': 'Original untimed group.', 'target': None,
                'disposition': 'unresolved', 'source_reference_ids': ['nist2024-d17'],
                'source_document_date': '2024-12-13'}],
            'outcomes': [{**identity, 'reason': 'No matched event outcome.', 'outcome': {'status': 'unknown'},
                'source_reference_ids': ['later-source'], 'source_document_date': '2025-06-01'}]}
        child = self.put('review_plans/diagnostic.json', self.diagnostic)
        self.wrapper = {'format': screen.FORMAT, 'reviewed_at': '2025-06-04T12:00:00Z',
            'reviewed_by': 'Synthetic wrapper.', 'selection': 'Complete synthetic denominator.',
            'prior_exposure': 'Exposed retrospective fixture.', 'boundaries': screen.BOUNDARIES,
            'population_review': parent, 'diagnostic_review': child,
            'later_capture_manifest': self.manifest, 'supplemental_cases': []}

    def put(self, name, value):
        raw = value if isinstance(value, bytes) else runtime.canonical(value)
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return {'path': name, 'bytes': len(raw), 'sha256': runtime.digest(raw)}

    def verify(self):
        self.wrapper['diagnostic_review'] = self.put('review_plans/diagnostic.json', self.diagnostic)
        wrapper = self.put('review_plans/wrapper.json', self.wrapper)
        with patch.object(screen.population, 'verify', return_value=self.parent_result), \
                patch.object(screen, 'validate_capture', return_value={}):
            return screen.verify(self.root / wrapper['path'], wrapper['sha256'], self.root)

    def test_full_wrapper_joins_real_diagnostic_and_source_bindings(self):
        result = self.verify()
        self.assertTrue(result['verified'])
        self.assertEqual(1, result['denominators']['original']['cases'])
        self.assertEqual(0, result['denominators']['supplemental']['cases'])
        self.assertEqual('2025-06-01', result['later_source_metadata'][0]['source_document_date'])
        self.assertIn('scripts/verify_pmt_outcome_screen.py', result['producer_files'])

    def test_wrapper_cannot_precede_child_review(self):
        self.wrapper['reviewed_at'] = '2025-06-02T12:00:00Z'
        with self.assertRaises(ValueError):
            self.verify()

    def test_child_cannot_precede_parent_review(self):
        self.diagnostic['reviewed_at'] = '2025-05-30T12:00:00Z'
        with self.assertRaises(ValueError):
            self.verify()

    def test_false_only_boundaries_reject_integer_zero(self):
        self.wrapper['boundaries'] = dict(screen.BOUNDARIES, calibration=0)
        with self.assertRaises(ValueError):
            self.verify()

    def test_forged_later_source_date_fails_after_consistent_case_date_change(self):
        self.diagnostic['source_references']['later-source']['source_document_date'] = '2025-06-02'
        self.diagnostic['outcomes'][0]['source_document_date'] = '2025-06-02'
        with self.assertRaises(ValueError):
            self.verify()

    def test_narrow_later_span_rejected_even_with_correct_narrow_span_hash(self):
        ref = self.diagnostic['source_references']['later-source']
        raw = (self.root / ref['body_path']).read_bytes()
        start, end = raw.index(b'<p>'), raw.index(b'</p>') + 4
        ref['support_spans'] = [{'path': ref['body_path'], 'start': start, 'end': end,
                                 'sha256': runtime.digest(raw[start:end])}]
        with self.assertRaises(ValueError):
            self.verify()

    def test_capture_run_must_finish_before_diagnostic_review(self):
        run = self.put('artifacts/later/run.json', {'started_at': '2025-06-02T12:00:00Z',
            'finished_at': '2025-06-04T12:00:00Z'})
        manifest = screen.adapter.strict_json((self.root / self.manifest['path']).read_bytes())
        manifest['files']['run.json'] = {key: run[key] for key in ('bytes', 'sha256')}
        self.wrapper['later_capture_manifest'] = self.put(self.manifest['path'], manifest)
        with self.assertRaises(ValueError):
            self.verify()

    def test_supplement_cannot_borrow_old_primary_evidence(self):
        key = self.project['id']
        identity = {'case_id': key + '--supplement-sample', 'project_id': key, 'project_group': key,
                    'scope_id': key + '--sample-scope', 'event_type': 'sample_started'}
        case = {**identity, 'reason': 'Synthetic separately scoped sample.', 'disposition': 'unresolved',
                'target': None, 'source_reference_ids': ['later-source'], 'source_document_date': '2025-06-01'}
        outcome = {**identity, 'reason': 'Synthetic sample observation.',
                   'outcome': {'status': 'observed_by', 'through': '2025-06-01'},
                   'source_reference_ids': ['later-source'], 'source_document_date': '2025-06-01'}
        self.diagnostic['roster'].append(case)
        self.diagnostic['outcomes'].append(outcome)
        self.wrapper['supplemental_cases'] = [{'case_id': identity['case_id'], 'project_id': key,
            'relation': 'distinct_event_same_project', 'reason': 'Not original intervention completion.'}]
        self.assertEqual(1, self.verify()['denominators']['supplemental']['cases'])
        for row in (case, outcome):
            with self.subTest(row=row), self.assertRaises(ValueError):
                row['source_reference_ids'] = ['nist2024-d17']
                row['source_document_date'] = '2024-12-13'
                self.verify()
            row['source_reference_ids'] = ['later-source']
            row['source_document_date'] = '2025-06-01'

    def test_complete_later_capture_cannot_drop_unmatched_document(self):
        ledger_path = 'artifacts/later/source_checks.json'
        ledger = screen.adapter.strict_json((self.root / ledger_path).read_bytes())
        extra = copy.deepcopy(ledger['attempts'][0])
        extra.update(id='extra', path='responses/extra.body',
                     url='https://www.nist.gov/news-events/extra',
                     final_url='https://www.nist.gov/news-events/extra')
        self.put('artifacts/later/responses/extra.body',
                 (self.root / 'artifacts/later/responses/source.body').read_bytes())
        ledger['attempts'].append(extra)
        descriptor = self.put(ledger_path, ledger)
        identity = {key: descriptor[key] for key in ('bytes', 'sha256')}
        self.diagnostic['input_files'][ledger_path] = identity
        manifest = screen.adapter.strict_json((self.root / self.manifest['path']).read_bytes())
        manifest['files']['source_checks.json'] = identity
        self.wrapper['later_capture_manifest'] = self.put(self.manifest['path'], manifest)
        with self.assertRaisesRegex(ValueError, 'each later captured article'):
            self.verify()


if __name__ == '__main__':
    unittest.main()
