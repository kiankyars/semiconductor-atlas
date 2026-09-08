"""Synthetic retained packets exercise the complete offline admission and replay path."""

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semiconductor_atlas import database, repository, service
from semiconductor_atlas import infineon_villach_output as importer
from scripts import frozen_runtime as runtime
from tests.test_ase_k18b_milestones import parent_fixture


REVIEWED = '2026-09-08T22:00:00Z'
STARTED = '2026-09-08T22:01:00Z'
ADMITTED = '2026-09-08T22:01:01Z'
LATER = '2026-09-08T22:02:00Z'


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


class VillachOutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='atlas-villach-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.parent = parent_fixture()
        self.parent_path = self.root / 'parent.sqlite'
        self.parent_path.write_bytes(self.parent)
        self.output = self.root / 'successor.sqlite'
        self.files = {}
        self.capture, self.visual = 'artifacts/capture', 'artifacts/visual'
        self.research_name, self.plan_name = 'review_plans/research.json', 'review_plans/acquisition.json'
        self.source_path = self.capture + '/responses/pdf.body'
        self.pdf = b'%PDF-1.7\nSynthetic manually reviewed PDF fixture, not manufacturing evidence.\n'
        self.add(self.source_path, self.pdf)
        self.patch('SOURCE_SHA', runtime.digest(self.pdf))
        dependencies = []
        for name, minute, purpose in [('robots', '43', 'access_policy'), ('terms', '44', 'rights_policy')]:
            packet = 'artifacts/' + name
            self.add(packet + '/plan.json', {'format': 'synthetic-policy-probe'})
            body = ('Synthetic ' + name + ' policy').encode()
            self.add(packet + '/responses/policy.body', body)
            attempt = self.attempt(name, 'https://example.test/' + name, 'responses/policy.body',
                body, f'2026-09-08T21:{minute}:00Z', f'2026-09-08T21:{minute}:01Z', purpose)
            self.add(packet + '/source_checks.json', self.ledger([attempt]))
            for path in (packet + '/responses/policy.body', packet + '/source_checks.json'):
                dependencies.append({'path': path, 'sha256': runtime.digest(self.files[path])})
        self.plan = {'decision': 'approved_exact_urls_local_informational_noncommercial_review_only',
            'reviewed_at': '2026-09-08T21:45:00Z', 'expires_at': '2026-09-09T21:45:00Z',
            'dependencies': dependencies,
            **dict.fromkeys(('raw_redistribution', 'claim_acceptance', 'collector_enabled',
                             'redirects', 'retries', 'cookies'), False),
            'entries': [{'url': importer.SOURCE_URL}, {'url': 'https://example.test/context'}]}
        self.add(self.plan_name, self.plan)
        self.add(self.capture + '/plan.json', self.plan)
        for index, dependency in enumerate(dependencies):
            self.add(self.capture + f'/dependencies/{index}.body', self.files[dependency['path']])
        context = b'Synthetic unselected context document'
        self.add(self.capture + '/responses/context.body', context)
        self.attempts = [self.attempt('pdf', importer.SOURCE_URL, 'responses/pdf.body', self.pdf,
            '2026-09-08T21:45:38Z', '2026-09-08T21:45:39Z', 'source_document'),
            self.attempt('context', 'https://example.test/context', 'responses/context.body', context,
            '2026-09-08T21:45:36Z', '2026-09-08T21:45:37Z', 'source_document')]
        self.add(self.capture + '/source_checks.json', self.ledger(self.attempts))
        self.add(self.visual + '/page39.png', b'Synthetic visual-review fixture, not a rendered source')
        self.manifest(self.capture)
        self.manifest(self.visual)
        self.research = {'source': {'sha256': importer.SOURCE_SHA, 'url': importer.SOURCE_URL,
            'publication_date': None, 'pdf_pages': 72, 'retained_path': self.source_path,
            'bytes': len(self.pdf), 'retrieval_finished_at': '2026-09-08T21:45:39Z'},
            'claim_acceptance': False, 'collector_enabled': False,
            'review_completed_at': '2026-09-08T21:50:00Z',
            'acquisition': {'plan': self.plan_name, 'packet': self.capture,
                'plan_sha256': runtime.digest(self.files[self.plan_name]),
                'manifest_sha256': runtime.digest(self.files[self.capture + '/manifest.json'])},
            'visual_review': {'retained_visual_packet': self.visual,
                'retained_visual_manifest_sha256': runtime.digest(self.files[self.visual + '/manifest.json'])},
            'reviewed_output_series': {'comparability_limits': ['Synthetic fixture; no manufacturing claim.']}}
        self.add(self.research_name, self.research)
        self.patch('RESEARCH_SHA', runtime.digest(self.files[self.research_name]))
        self.review = {'format': importer.FORMAT, 'decision': importer.DECISION, 'reviewed_at': REVIEWED,
            'reviewed_by': 'Synthetic exposed reviewer', 'prior_exposure': 'All fixture rows previously seen.',
            'selection': 'Complete five-row chart, no diameter disaggregation.',
            'parent_database_sha256': runtime.digest(self.parent),
            'research_review': {'path': self.research_name, 'sha256': importer.RESEARCH_SHA},
            'input_files': self.inventory(), 'site': copy.deepcopy(importer.SITE), 'rows': importer.rows(),
            'rights': copy.deepcopy(importer.RIGHTS), 'boundaries': copy.deepcopy(importer.BOUNDARIES),
            'interpretation_method': 'Synthetic manual visual review fixture; no automated numeric extraction.'}
        self.review_path = self.root / 'review.json'
        self.write_review()

    def patch(self, name, value):
        patcher = mock.patch.object(importer, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, name, value):
        raw = value if isinstance(value, bytes) else canonical(value)
        self.files[name] = raw
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def inventory(self):
        return {name: {'sha256': runtime.digest(body), 'bytes': len(body)} for name, body in self.files.items()}

    def manifest(self, packet):
        files = {name[len(packet) + 1:]: {'sha256': runtime.digest(body), 'bytes': len(body)}
                 for name, body in self.files.items() if name.startswith(packet + '/')
                 and name != packet + '/manifest.json'}
        self.add(packet + '/manifest.json', {'files': files, 'raw_redistribution': False, 'claim_acceptance': False})

    def attempt(self, identity, url, path, body, started, finished, purpose):
        return {'id': identity, 'url': url, 'final_url': url, 'path': path,
            'started_at': started, 'finished_at': finished, 'http_status': 200,
            'curl_exit_code': 0, 'error': None, 'tls_verification_result': 0, 'outcome': 'succeeded',
            'bytes': len(body), 'sha256': runtime.digest(body), 'purpose': purpose, 'company': 'Infineon',
            'country_code': 'AT', 'source_family': 'infineon-issuer', 'scope': 'Synthetic exact URL fixture',
            'negative_evidence_eligible': False, 'redirect_count': 0}

    def ledger(self, attempts):
        return {'format': 'semiconductor-atlas-publisher-source-checks-v1', 'review_scope_id': 'synthetic',
            'checked_source_id': 'infineon-fixture', 'clock_precision': 'second',
            'absence_inference_allowed': False, 'attempts': attempts}

    def write_review(self):
        self.review_path.write_bytes(canonical(self.review))

    def validate(self, review=None):
        raw = canonical(self.review if review is None else review)
        return importer.validate_review(raw, runtime.digest(raw))

    def execute(self, verify=False, **changes):
        options = dict(parent=self.parent_path, review_path=self.review_path, reference_root=self.root,
            expected_review_sha256=runtime.digest(self.review_path.read_bytes()), output=self.output,
            verify_only=verify, expected_output_sha256=runtime.digest(self.output.read_bytes()) if verify else None)
        options.update(changes)
        with mock.patch.object(importer, 'now', side_effect=[LATER] if verify else [STARTED, ADMITTED]):
            return importer.admit(**options)

    def reseal_research(self):
        self.manifest(self.capture)
        self.research['acquisition']['manifest_sha256'] = runtime.digest(self.files[self.capture + '/manifest.json'])
        self.add(self.research_name, self.research)
        self.patch('RESEARCH_SHA', runtime.digest(self.files[self.research_name]))
        self.review['research_review']['sha256'] = importer.RESEARCH_SHA
        self.review['input_files'] = self.inventory()
        self.write_review()

    def test_all_five_qualified_source_rows_and_exact_replay_preserve_parent(self):
        result = self.execute()
        self.assertEqual(result['new_source_quantities'], 5)
        self.assertEqual(result['admitted_at'], ADMITTED)
        connection = importer.open_image(self.output.read_bytes(), 8)
        self.addCleanup(connection.close)
        records = service.source_claim_records(connection, recorded_at=ADMITTED)
        quantities = [record for record in records if record['value'].get('source_quantity')]
        self.assertEqual(len(quantities), 5)
        values = {record['value']['source_quantity']['source_period_label']: record['value'] for record in quantities}
        self.assertEqual([values[label]['value'] for label in ('2020/21', '2021/22', '2022/23', '2023/24', '2024/25')],
                         [462500000, 488900000, 517800000, 524000000, 505500000])
        for value in values.values():
            metadata = value['source_quantity']
            self.assertEqual(metadata['scope_kind'], 'source_native_site')
            self.assertEqual(metadata['unit'], 'square_centimeters')
            self.assertEqual(metadata['period_kind'], 'fiscal_year')
            self.assertIsNone(metadata['physical_capacity_basis'])
            self.assertIsNone(metadata['native_wafer_diameter_inches'])
        self.assertEqual(len(service.source_claim_records(connection, recorded_at=STARTED)), 1)
        self.assertEqual(len(service.claim_records(connection, as_of='2026-09-08', recorded_at=LATER)), 1)
        self.assertEqual(connection.execute('SELECT count(*) FROM capacity_values').fetchone()[0], 1)
        self.assertEqual(repository.validate_database(connection), [])
        old = importer.open_image(self.parent, 7)
        self.addCleanup(old.close)
        for table, in old.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            before = {tuple(row) for row in old.execute(f'SELECT * FROM "{table}"')}
            after = {tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')}
            self.assertTrue(before <= after, table)
        before = self.output.read_bytes()
        verified = self.execute(True)
        self.assertTrue(verified['verified'])
        self.assertEqual(verified['database_sha256'], result['database_sha256'])
        self.assertEqual(self.output.read_bytes(), before)
        self.assertEqual(self.parent_path.read_bytes(), self.parent)

    def test_complete_chart_selection_and_semantics_are_required(self):
        for rows in (self.review['rows'][:4], self.review['rows'][::-1], self.review['rows'] * 2):
            with self.subTest(rows=len(rows)), self.assertRaisesRegex(ValueError, 'five-row'):
                self.validate({**self.review, 'rows': rows})
        for field, value in [('unit', 'native_wafers'), ('period_kind', 'year'),
                             ('source_number_literal', '600'), ('scope_kind', 'company_aggregate')]:
            review = copy.deepcopy(self.review)
            review['rows'][0]['metadata'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'semantics'):
                self.validate(review)

    def test_rights_site_and_boolean_boundaries_cannot_change(self):
        for field in importer.BOUNDARIES:
            for value in (True, 0):
                review = copy.deepcopy(self.review)
                review['boundaries'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError): self.validate(review)
        for field, value in [('rights', {**importer.RIGHTS, 'commercial_use_authorized': True}),
                             ('site', {**importer.SITE, 'kind': 'facility'}), ('decision', 'research_only')]:
            with self.subTest(field=field), self.assertRaises(ValueError): self.validate({**self.review, field: value})

    def test_untrusted_review_hash_and_unreviewed_research_fail(self):
        with self.assertRaisesRegex(ValueError, 'review hash'):
            importer.validate_review(canonical(self.review), '0' * 64)
        review = copy.deepcopy(self.review)
        review['research_review']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'interpretation'): self.validate(review)

    def test_missing_or_changed_input_is_not_an_empty_observation(self):
        path = self.root / self.source_path
        path.write_bytes(self.pdf + b'changed')
        with self.assertRaisesRegex(ValueError, 'input bytes differ'): self.execute()
        path.unlink()
        with self.assertRaises(OSError): self.execute()
        self.assertFalse(self.output.exists())

    def test_extra_packet_file_and_symlink_are_rejected(self):
        extra = self.root / self.visual / 'extra.png'
        extra.write_bytes(b'unreviewed')
        with self.assertRaisesRegex(ValueError, 'inventory'): self.execute()
        extra.unlink()
        extra.symlink_to(self.root / self.source_path)
        with self.assertRaisesRegex(ValueError, 'symlinks'): self.execute()

    def test_inventory_cannot_escape_root_or_add_unrelated_inputs(self):
        for name in ('../escape', '/absolute', 'artifacts/../escape', 'artifacts//bad', 'unknown/body'):
            review = copy.deepcopy(self.review)
            review['input_files'][name] = {'sha256': 'a' * 64, 'bytes': 1}
            with self.subTest(name=name), self.assertRaises(ValueError): self.validate(review)
        self.add('artifacts/unrelated', b'unrelated')
        self.review['input_files'] = self.inventory()
        self.write_review()
        with self.assertRaisesRegex(ValueError, 'unrelated'): self.execute()

    def test_policy_copy_must_match_original_even_after_packet_is_resealed(self):
        self.add(self.capture + '/dependencies/0.body', b'changed policy')
        self.reseal_research()
        with self.assertRaisesRegex(ValueError, 'policy dependencies'): self.execute()

    def test_redirect_and_post_review_capture_fail_after_packet_reseal(self):
        for field, value in [('redirect_count', 1), ('finished_at', '2026-09-08T23:00:00Z')]:
            attempts = copy.deepcopy(self.attempts)
            attempts[1][field] = value
            self.add(self.capture + '/source_checks.json', self.ledger(attempts))
            self.reseal_research()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'chronology|redirect'):
                self.execute()

    def test_review_parent_schema_and_admission_clocks_are_enforced(self):
        for field, value in [('parent_database_sha256', '0' * 64),
                             ('reviewed_at', '2026-09-08T21:00:00Z'), ('reviewed_at', LATER)]:
            prior = self.review[field]
            self.review[field] = value
            self.write_review()
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): self.execute()
            self.review[field] = prior
        self.write_review()
        with self.assertRaises(ValueError): importer.open_image(self.parent, 8)
        self.assertFalse(self.output.exists())

    def test_new_only_output_and_expected_hash_checks_never_overwrite(self):
        self.execute()
        before = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, 'must not exist'): self.execute()
        with self.assertRaisesRegex(ValueError, 'successor hash'):
            self.execute(True, expected_output_sha256='0' * 64)
        with self.assertRaisesRegex(ValueError, 'only an input for verification'):
            self.execute(expected_output_sha256='0' * 64)
        self.assertEqual(self.output.read_bytes(), before)

    def test_resealed_database_metadata_mutation_fails_complete_reconstruction(self):
        self.execute()
        connection = sqlite3.connect(self.output)
        try:
            connection.execute('DROP TRIGGER source_quantity_metadata_immutable_update')
            connection.execute("UPDATE source_quantity_metadata SET source_period_label='changed'")
            connection.commit()
        finally:
            connection.close()
        before = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, 'complete parent-plus-output reconstruction'): self.execute(True)
        self.assertEqual(self.output.read_bytes(), before)

    def test_input_and_code_drift_fail_before_output_installation(self):
        original = importer.populate
        def drift(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.root / self.source_path).write_bytes(self.pdf + b'drift')
            return result
        with mock.patch.object(importer, 'populate', side_effect=drift), self.assertRaises(ValueError): self.execute()
        self.assertFalse(self.output.exists())
        (self.root / self.source_path).write_bytes(self.pdf)
        with mock.patch.object(importer, 'codes', return_value={'changed': 'a' * 64}):
            with self.assertRaisesRegex(ValueError, 'module loading'): self.execute()
        with mock.patch.object(importer, 'codes', side_effect=[importer._LOADED_CODES, {'changed': 'b' * 64}]):
            with self.assertRaisesRegex(ValueError, 'during admission'): self.execute()
        self.assertFalse(self.output.exists())
