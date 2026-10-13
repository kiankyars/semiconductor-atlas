"""Synthetic legacy lineage and original-case preservation, not source authenticity."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import evaluate_legacy_pmt_outcomes as legacy
from scripts import frozen_runtime as runtime


class LegacySourceTests(unittest.TestCase):
    def setUp(self):
        self.body = b'<div>TSMC Arizona. We plan multiple advanced semiconductor manufacturing facilities in Phoenix, Arizona. Our first facility successfully entered high volume production at the end of 2024. Construction of our second facility is ongoing, and the construction of our third facility commenced in 2025.</div>'
        self.source = {'source_id': legacy.SOURCE_ID, 'url': legacy.SOURCE_URL,
            'archive_path': legacy.SOURCE_REFERENCE, 'bytes': len(self.body),
            'content_sha256': runtime.digest(self.body), 'media_type': 'text/html',
            'source_type': 'official_primary', 'source_family': 'sec-edgar-filing',
            'publisher': 'Taiwan Semiconductor Manufacturing Company Limited',
            'published_at': '2026-04-16', 'published_at_precision': 'day',
            'published_at_basis': 'Synthetic legacy filing date context.',
            'retrieved_at': '2026-08-20T16:11:52Z', 'acquired_at': '2026-08-20T16:11:52Z',
            'adapter_version': 'official-primary-exact-byte-capture-v1', 'language': 'en',
            'ingestion_run_id': 'ingest:synthetic',
            'rights': {'access': 'public', 'attribution': 'Synthetic attribution.',
                'license': 'Synthetic historical rights record, not a new grant.',
                'license_url': 'https://example.test/rights', 'redistribution': 'licensed_redistribution',
                'reviewed_at': '2026-08-20'}}
        self.capture = {key: self.source[key] for key in ('bytes', 'url', 'publisher', 'published_at',
            'published_at_basis', 'published_at_precision', 'retrieved_at', 'acquired_at', 'adapter_version', 'language')}
        self.capture.update(path=Path(legacy.SOURCE_REFERENCE).name, content_type='text/html',
            sha256=runtime.digest(self.body), record_type='sec_filing',
            rights={key: value for key, value in self.source['rights'].items() if key != 'access'})
        self.run = {'run_id': 'ingest:synthetic', 'source_id': legacy.SOURCE_ID, 'outcome': 'succeeded',
            'inputs': [{'source_id': legacy.SOURCE_ID, 'content_sha256': runtime.digest(self.body),
                        'document_role': 'primary'}], 'code_version': 'official-primary-exact-byte-capture-v1',
            'started_at': '2026-08-20T17:06:35Z', 'finished_at': '2026-08-20T17:06:36Z'}
        self.provenance = {'format': 'semiconductor-atlas-ai-critical-input-v1',
            'sources': [self.source], 'ingestion_runs': [self.run], 'recorded_at': '2026-08-20T17:48:42Z'}
        self.manifest = {'format': 'semiconductor-atlas-source-inputs-v1', 'inputs': [self.capture],
            'retrieval_timestamp_basis': 'UTC batch-completion timestamps recorded after downloads.'}
        self.review = {'reviewed_at': '2026-09-09T05:00:00Z'}

    def descriptor(self, name, raw):
        return {'path': name, 'bytes': len(raw), 'sha256': runtime.digest(raw)}

    def inputs(self):
        provenance, manifest = runtime.canonical(self.provenance), runtime.canonical(self.manifest)
        self.review['legacy_inputs'] = {
            'body': self.descriptor(legacy.SOURCE_REFERENCE, self.body),
            'provenance': self.descriptor(legacy.PROVENANCE_REFERENCE, provenance),
            'snapshot_manifest': self.descriptor(legacy.MANIFEST_REFERENCE, manifest)}
        return self.body, provenance, manifest

    def validate(self):
        raw = self.inputs()
        with patch.object(legacy, 'now', return_value='2026-09-09T06:00:00Z'):
            return legacy.validate_legacy_source(self.review, *raw)

    def span(self, body=None):
        body = self.body if body is None else body
        return {'start': 0, 'end': len(body), 'sha256': runtime.digest(body)}

    def test_recorded_lineage_never_becomes_http_attestation_or_new_rights(self):
        result = self.validate()
        self.assertEqual('reviewed_legacy_recorded_primary', result['kind'])
        self.assertFalse(result['historical_http_transaction_verified'])
        self.assertFalse(result['external_timestamp_attestation'])
        self.assertEqual('2026-04-16', result['source_document_date'])
        self.assertEqual(self.source, result['source'])
        self.assertEqual(self.capture, result['snapshot_member'])
        self.assertIn('batch-completion', result['retrieval_timestamp_basis'])
        self.assertNotIn('http_status', result)
        self.assertFalse(legacy.RIGHTS['raw_redistribution'])
        result['source']['rights']['access'] = 'changed'
        self.assertEqual('public', self.source['rights']['access'])

    def test_each_exact_byte_input_is_required(self):
        raw = self.inputs()
        for ordinal in range(3):
            changed = list(raw); changed[ordinal] += b' '
            with self.subTest(ordinal=ordinal), self.assertRaises(ValueError):
                legacy.validate_legacy_source(self.review, *changed)

    def test_legacy_descriptors_have_separate_bounds_and_fixed_references(self):
        for role in ('body', 'provenance', 'snapshot_manifest'):
            for field, value in (('path', '../unexpected'), ('bytes', True), ('bytes', 0),
                                 ('bytes', 20_000_001), ('sha256', '0' * 64)):
                raw = self.inputs()
                self.review['legacy_inputs'][role][field] = value
                with self.subTest(role=role, field=field), self.assertRaises(ValueError):
                    legacy.validate_legacy_source(self.review, *raw)

    def test_no_synthetic_http_ledger_role_is_accepted(self):
        raw = self.inputs(); self.review['legacy_inputs']['http_ledger'] = {}
        with self.assertRaises(ValueError):
            legacy.validate_legacy_source(self.review, *raw)

    def test_unsupported_source_identity_media_publisher_and_precision_fail(self):
        for field, value in (('source_id', 'other'), ('url', 'https://example.test/other'),
                             ('publisher', 'Another issuer'), ('source_family', 'another-source'),
                             ('archive_path', 'artifacts/other'), ('media_type', 'application/pdf'),
                             ('source_type', 'secondary'), ('published_at_precision', 'year'),
                             ('adapter_version', 'invented-modern-http-capture')):
            saved = self.source[field]; self.source[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate()
            self.source[field] = saved

    def test_unique_source_snapshot_and_ingestion_records_required(self):
        for collection in (self.provenance['sources'], self.manifest['inputs'], self.provenance['ingestion_runs']):
            collection.append(copy.deepcopy(collection[0]))
            with self.assertRaises(ValueError):
                self.validate()
            collection.pop()
            saved = collection.pop()
            with self.assertRaises(ValueError):
                self.validate()
            collection.append(saved)

    def test_snapshot_metadata_and_rights_cannot_drift_from_baseline(self):
        for field, value in (('sha256', '0' * 64), ('bytes', 1), ('url', 'https://example.test/other'),
                             ('published_at', '2026-04-15'), ('retrieved_at', '2026-08-20T16:11:53Z'),
                             ('content_type', 'text/plain'), ('record_type', 'news_release')):
            saved = self.capture[field]; self.capture[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate()
            self.capture[field] = saved
        self.capture['rights']['attribution'] = 'Different attribution.'
        with self.assertRaises(ValueError):
            self.validate()

    def test_ingestion_must_succeed_with_exact_primary_input_and_producer(self):
        for field, value in (('outcome', 'failed'), ('source_id', 'other'), ('inputs', []),
                             ('code_version', 'different'), ('run_id', 'other')):
            saved = self.run[field]; self.run[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate()
            self.run[field] = saved

    def test_recorded_clock_order_and_batch_precision_are_not_synthesized(self):
        for row, field, value in ((self.run, 'started_at', '2026-08-20T15:00:00Z'),
                                 (self.run, 'finished_at', '2026-08-20T17:06:34Z'),
                                 (self.provenance, 'recorded_at', '2026-08-20T17:00:00Z'),
                                 (self.review, 'reviewed_at', '2026-08-19T00:00:00Z'),
                                 (self.review, 'reviewed_at', '2099-01-01T00:00:00Z'),
                                 (self.manifest, 'retrieval_timestamp_basis', 'Exact HTTP request finish')):
            saved = row[field]; row[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate()
            row[field] = saved

    def test_end_year_only_supplies_conservative_upper_bound(self):
        self.assertEqual({'status': 'observed_by', 'through': '2024-12-31'},
                         legacy.first_phoenix_hvm(self.body, self.span()))

    def test_full_phase_context_and_exact_event_are_required(self):
        for old, new in ((b'Phoenix, Arizona', b'Hsinchu, Taiwan'),
                         (b'Our first facility', b'Our manufacturing entity Fab 21'),
                         (b'high volume production', b'commercial production'),
                         (b'Construction of our second facility', b'Second facility'),
                         (b'construction of our third facility commenced', b'land acquired'),
                         (b'at the end of 2024', b'in late 2024'),
                         (b'TSMC Arizona.', b'TSMC Washington.')):
            body = self.body.replace(old, new)
            with self.subTest(old=old), self.assertRaises(ValueError):
                legacy.first_phoenix_hvm(body, self.span(body))

    def test_duplicate_first_facility_statement_rejected(self):
        statement = b'Our first facility successfully entered high volume production at the end of 2024.'
        body = self.body + statement
        with self.assertRaises(ValueError):
            legacy.first_phoenix_hvm(body, self.span(body))

    def test_narrative_span_rejects_hash_bounds_and_boolean_offsets(self):
        for field, value in (('start', True), ('start', -1), ('end', len(self.body) + 1),
                             ('end', 0), ('sha256', '0' * 64)):
            span = self.span(); span[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                legacy.first_phoenix_hvm(self.body, span)


class OutcomeOverlayTests(unittest.TestCase):
    def setUp(self):
        project = 'nist2024-d67-tsmc-phoenix-first-fab'
        identity = {'case_id': legacy.CASE_ID, 'project_id': project, 'project_group': project,
                    'event_type': 'high_volume_production', 'scope_id': project + '--original-pmt-scope'}
        case = {**identity, 'target': {'kind': 'deadline', 'through': '2025-06-30'},
            'disposition': 'eligible', 'reason': 'Synthetic original target.',
            'source_reference_ids': ['nist2024-d67'], 'source_document_date': '2024-04-08'}
        unknown = {**identity, 'reason': 'Previously unknown.', 'outcome': {'status': 'unknown'},
                   'source_reference_ids': [], 'source_document_date': None}
        other_case = {**case, 'case_id': 'z-other', 'project_id': 'other', 'project_group': 'other',
                      'event_type': 'other_event', 'scope_id': 'other-scope', 'target': None,
                      'disposition': 'unresolved'}
        other_outcome = {**unknown, **{key: other_case[key] for key in legacy.arithmetic.IDENTITY}}
        self.base = {'roster': [case, other_case], 'outcomes': [unknown, other_outcome],
                     'source_references': {'nist2024-d67': {}}}
        self.evidence = {'source_document_date': '2026-04-16',
                         'derived_outcome': {'status': 'observed_by', 'through': '2024-12-31'}}
        self.review = {'outcome': {**unknown, 'reason': 'Synthetic reviewed source report.',
            'outcome': copy.deepcopy(self.evidence['derived_outcome']),
            'source_reference_ids': [legacy.LEGACY_REFERENCE_ID], 'source_document_date': '2026-04-16'},
            'correspondence': {'guidance_reference_id': 'nist2024-d67',
                **dict.fromkeys(('organization', 'location', 'phase', 'event', 'limitations'), 'Synthetic reviewed context.')}}

    def evaluate(self):
        return legacy.overlay(self.base, self.review, self.evidence)

    def test_exactly_one_outcome_changes_with_roster_and_other_cases_unchanged(self):
        before = copy.deepcopy(self.base)
        result = self.evaluate()
        self.assertEqual(before, self.base)
        self.assertEqual(self.base['roster'], [row['case'] for row in result['cases']])
        self.assertEqual(self.base['outcomes'][1], result['cases'][1]['review'])
        self.assertEqual('deadline_met', result['cases'][0]['classification'])
        self.assertEqual(1, result['denominators']['scored_cases'])
        self.assertEqual(2, result['denominators']['screened_projects'])
        self.assertNotIn('success_rate', result)

    def test_cannot_change_original_phase_or_event_into_fab21_alias(self):
        for field, value in (('project_id', 'fab21'), ('project_group', 'fab21'),
                             ('scope_id', 'operating-table'), ('event_type', 'commercial_production_commencement'),
                             ('source_reference_ids', ['unrelated'])):
            saved = self.base['roster'][0][field]; self.base['roster'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.evaluate()
            self.base['roster'][0][field] = saved

    def test_outcome_cannot_invent_quarter_or_exact_day(self):
        for value in ({'status': 'observed', 'low': '2024-10-01', 'high': '2024-12-31'},
                      {'status': 'observed', 'low': '2024-12-31', 'high': '2024-12-31'},
                      {'status': 'observed_by', 'through': '2024-12-30'}):
            self.review['outcome']['outcome'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.evaluate()

    def test_outcome_requires_own_legacy_reference_and_recorded_document_day(self):
        for field, value in (('source_reference_ids', []), ('source_reference_ids', ['nist2024-d67']),
                             ('source_document_date', '2025-01-01'), ('case_id', 'other'),
                             ('event_type', 'commercial_production_commencement')):
            saved = self.review['outcome'][field]; self.review['outcome'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.evaluate()
            self.review['outcome'][field] = saved

    def test_cannot_overwrite_previous_substantive_outcome_or_reference(self):
        self.base['outcomes'][0]['outcome'] = {'status': 'cancelled'}
        with self.assertRaises(ValueError):
            self.evaluate()
        self.base['outcomes'][0]['outcome'] = {'status': 'unknown'}
        self.base['source_references'][legacy.LEGACY_REFERENCE_ID] = {}
        with self.assertRaises(ValueError):
            self.evaluate()

    def test_each_cross_source_correspondence_dimension_is_required(self):
        for field in ('organization', 'location', 'phase', 'event', 'limitations'):
            saved = self.review['correspondence'][field]
            self.review['correspondence'][field] = ''
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.evaluate()
            self.review['correspondence'][field] = saved


class ReportLifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.result = {'review': {'reviewed_at': '2026-09-09T00:00:00Z'},
            'diagnostic': {'denominators': {'scored_cases': 1}}, 'verification_scope': 'Synthetic only.'}

    def test_new_only_report_and_exact_replay(self):
        output = self.root / 'report.json'
        with patch.object(legacy, 'evaluate', return_value=self.result), \
                patch.object(legacy, 'now', return_value='2026-09-09T01:00:00Z'):
            created = legacy.create(output=output)
            before = output.read_bytes()
            self.assertTrue(legacy.verify(report_path=output, report_sha256=created['sha256'])['verified'])
            with self.assertRaises(FileExistsError):
                legacy.create(output=output)
            self.assertEqual(before, output.read_bytes())

    def test_execution_cannot_precede_review_and_report_mutations_fail(self):
        output = self.root / 'report.json'
        with patch.object(legacy, 'evaluate', return_value=self.result), \
                patch.object(legacy, 'now', return_value='2026-09-08T00:00:00Z'):
            with self.assertRaises(ValueError):
                legacy.create(output=output)
        with patch.object(legacy, 'evaluate', return_value=self.result), \
                patch.object(legacy, 'now', return_value='2026-09-09T01:00:00Z'):
            legacy.create(output=output)
            original = legacy.guidance.strict_json(output.read_bytes())
            for field, value in (('producer_files', {}), ('format', 'other'),
                                 ('execution_started_at', '2020-01-01T00:00:00Z'), ('result', {})):
                report = copy.deepcopy(original); report[field] = value
                raw = runtime.canonical(report); output.write_bytes(raw)
                with self.subTest(field=field), self.assertRaises(ValueError):
                    legacy.verify(report_path=output, report_sha256=runtime.digest(raw))


class LegacyAdapterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.source_fixture = LegacySourceTests()
        self.source_fixture.setUp()
        self.overlay_fixture = OutcomeOverlayTests()
        self.overlay_fixture.setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        (self.root / 'review_plans').mkdir()
        (self.root / 'artifacts').mkdir()
        self.path = self.root / 'review_plans/new-review.json'
        self.review = {'format': legacy.REVIEW_FORMAT, 'rule': legacy.RULE,
            'reviewed_at': '2026-09-09T05:00:00Z', 'reviewed_by': 'Synthetic reviewer.',
            'selection': 'Synthetic preserved population.', 'prior_exposure': 'Synthetic exposed outcome.',
            'rights': dict(legacy.RIGHTS), 'boundaries': dict(legacy.BOUNDARIES),
            'evidence': {'arizona_narrative': self.source_fixture.span()},
            **copy.deepcopy(self.overlay_fixture.review)}
        parent = {'reviewed_at': '2026-09-09T04:00:00Z',
            'diagnostic_review': {'path': 'review_plans/base.json', 'sha256': '0' * 64},
            'supplemental_cases': []}
        raw = runtime.canonical(parent)
        (self.root / 'review_plans/parent.json').write_bytes(raw)
        self.review['parent_screen'] = self.source_fixture.descriptor('review_plans/parent.json', raw)
        self.inputs = {'review_path': self.path, 'reference_root': self.root,
            'source_body': self.root / 'artifacts/body', 'provenance': self.root / 'artifacts/provenance.json',
            'source_manifest': self.root / 'artifacts/manifest.json'}
        self.addCleanup(patch.stopall)
        patch.object(legacy, 'now', return_value='2026-09-09T06:00:00Z').start()
        patch.object(legacy, 'SOURCE_SHA', runtime.digest(self.source_fixture.body)).start()
        self.parent_verifier = patch.object(legacy.population_screen, 'verify', return_value={}).start()
        self.base_loader = patch.object(legacy.guidance, 'load_review',
            return_value={'review': self.overlay_fixture.base}).start()
        self.retain()

    def retain(self):
        raw = self.source_fixture.inputs()
        self.review['legacy_inputs'] = copy.deepcopy(self.source_fixture.review['legacy_inputs'])
        for key, value in zip(('source_body', 'provenance', 'source_manifest'), raw, strict=True):
            self.inputs[key].write_bytes(value)
        reviewed = runtime.canonical(self.review)
        self.path.write_bytes(reviewed)
        self.inputs['review_sha256'] = runtime.digest(reviewed)

    def test_complete_legacy_adapter_path_and_report_replay(self):
        before = copy.deepcopy(self.overlay_fixture.base)
        output = self.root / 'artifacts/report.json'
        created = legacy.create(output=output, **self.inputs)
        self.assertTrue(legacy.verify(report_path=output, report_sha256=created['sha256'], **self.inputs)['verified'])
        result = legacy.guidance.strict_json(output.read_bytes())['result']
        self.assertEqual(1, result['original']['scored_cases'])
        self.assertEqual(1, result['original']['unknown_outcomes'])
        self.assertEqual(before, self.overlay_fixture.base)
        self.assertFalse(result['legacy_source_verification']['historical_http_transaction_verified'])
        self.parent_verifier.assert_called()
        self.base_loader.assert_called()

    def test_exact_previously_retained_source_is_required(self):
        with patch.object(legacy, 'SOURCE_SHA', '0' * 64), self.assertRaises(ValueError):
            legacy.evaluate(**self.inputs)

    def test_parent_screen_bytes_and_review_identity_remain_pinned(self):
        for key in ('review_path',):
            raw = self.inputs[key].read_bytes(); self.inputs[key].write_bytes(raw + b' ')
            with self.assertRaises(ValueError):
                legacy.evaluate(**self.inputs)
            self.inputs[key].write_bytes(raw)
        parent = self.root / 'review_plans/parent.json'
        parent.write_bytes(parent.read_bytes() + b' ')
        with self.assertRaises(ValueError):
            legacy.evaluate(**self.inputs)

    def test_legacy_boundary_and_rights_flags_cannot_claim_new_authority(self):
        for group, field in (('boundaries', 'historical_http_transaction_verified'),
                             ('boundaries', 'canonical_fab21_phase_identity'),
                             ('rights', 'new_network_acquisition'), ('rights', 'raw_redistribution')):
            for value in (True, 0):
                self.review[group][field] = value; self.retain()
                with self.subTest(group=group, field=field, value=value), self.assertRaises(ValueError):
                    legacy.evaluate(**self.inputs)
            self.review[group][field] = False

    def test_new_review_cannot_precede_frozen_parent(self):
        self.review['reviewed_at'] = '2026-09-09T03:00:00Z'; self.retain()
        with self.assertRaises(ValueError):
            legacy.evaluate(**self.inputs)

    def test_completed_event_cannot_extend_beyond_recorded_document_day(self):
        self.source_fixture.source['published_at'] = '2024-01-01'
        self.source_fixture.capture['published_at'] = '2024-01-01'
        self.review['outcome']['source_document_date'] = '2024-01-01'
        self.retain()
        with self.assertRaisesRegex(ValueError, 'completed event cannot extend'):
            legacy.evaluate(**self.inputs)

    def test_symlinked_source_is_not_accepted(self):
        body = self.inputs['source_body']; moved = body.with_name('other-body')
        body.rename(moved); body.symlink_to(moved)
        with self.assertRaises(OSError):
            legacy.evaluate(**self.inputs)


if __name__ == '__main__':
    unittest.main()
