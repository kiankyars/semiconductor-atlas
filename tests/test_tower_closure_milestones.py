"""Synthetic offline packets test Tower event admission without manufacturing evidence."""

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semiconductor_atlas import database, repository, service
from semiconductor_atlas import tower_closure_milestones as importer
from scripts import frozen_runtime as runtime
from tests.test_ase_k18b_milestones import parent_fixture


REVIEWED = '2026-09-08T23:00:00Z'
STARTED = '2026-09-08T23:01:00Z'
ADMITTED = '2026-09-08T23:01:01Z'
LATER = '2026-09-08T23:02:00Z'
RETRIEVED = '2026-09-08T22:49:04Z'
TITLE = 'Tower Semiconductor Reports 2024 Fourth Quarter and Full Year Financial Results'


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def html_fixture(*, hidden=None, paragraph=None, arai=None):
    body, evidence = bytearray(b'<html><body>'), {}
    blocks = [
        ('title', 'h1', TITLE),
        ('publication_dateline', 'p', 'MIGDAL HAEMEK, Israel, February 10, 2025. Synthetic source fixture.'),
        ('fab_consolidation_heading', 'p', '6” Fab Consolidation Update'),
        ('complete_fab_consolidation_paragraph', 'p', paragraph or
            'During the fourth quarter of 2024, legacy of 150mm flows were discontinued in Fab1, '
            'with last Fab outs occurring in January 2025. Strategic flows '
            'have been transferred into the Fab2 200mm factory. '
            'This is a synthetic test fixture, not manufacturing evidence.'),
        ('non_gaap_heading', 'p', 'Non-GAAP Financial Measures'),
        ('complete_arai_context_paragraph', 'p', arai or
            'Non-GAAP reconciliation includes the cessation of operations of the Arai facility, '
            'which occurred during 2022. '
            'This complete synthetic paragraph is not a numeric capacity assertion.'),
    ]
    for key, tag, text in blocks:
        start = len(body)
        attributes = ' hidden' if hidden == key else ''
        fragment = f'<{tag}{attributes}>{text}</{tag}>'.encode()
        body.extend(fragment)
        evidence[key] = {'start': start, 'end': len(body), 'sha256': runtime.digest(fragment), 'tag': tag}
    body.extend(b'</body></html>')
    return bytes(body), evidence


class TowerClosureMilestoneTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='atlas-tower-closure-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        connection = sqlite3.connect(':memory:')
        try:
            connection.row_factory = sqlite3.Row
            connection.deserialize(parent_fixture())
            database.apply_migrations(connection, target_version=8)
            self.parent = connection.serialize()
        finally:
            connection.close()
        self.parent_path = self.root / 'parent.sqlite'
        self.parent_path.write_bytes(self.parent)
        self.output = self.root / 'successor.sqlite'
        self.files = {}
        self.capture = 'artifacts/capture'
        self.research_name, self.plan_name = 'review_plans/research.json', 'review_plans/acquisition.json'
        self.source_path = self.capture + '/responses/release.body'
        self.body, self.evidence = html_fixture()
        self.add(self.source_path, self.body)
        self.patch('SOURCE_SHA', runtime.digest(self.body))
        dependencies, probes = [], []
        for name, minute, purpose in [('robots', '43', 'access_policy'), ('terms', '44', 'rights_policy')]:
            packet, plan_name = 'artifacts/' + name, 'review_plans/' + name + '.json'
            plan = {'format': 'synthetic-policy-probe', 'claim_acceptance': False,
                    'redirects': False, 'retries': False, 'cookies': False}
            self.add(plan_name, plan)
            self.add(packet + '/plan.json', plan)
            body = ('Synthetic ' + name + ' policy fixture; not a rights grant.').encode()
            self.add(packet + '/responses/policy.body', body)
            attempt = self.attempt(name, 'https://example.test/' + name, 'responses/policy.body',
                body, f'2026-09-08T22:{minute}:00Z', f'2026-09-08T22:{minute}:01Z', purpose)
            self.add(packet + '/source_checks.json', self.ledger([attempt]))
            self.manifest(packet)
            probes.append({'packet': packet, 'manifest_sha256': self.digest(packet + '/manifest.json'),
                           'plan': plan_name, 'plan_sha256': self.digest(plan_name)})
            for path in (packet + '/responses/policy.body', packet + '/source_checks.json'):
                dependencies.append({'path': path, 'sha256': self.digest(path)})
        self.plan = {'decision': 'approved_exact_url_local_personal_informational_noncommercial_review_only',
            'reviewed_at': '2026-09-08T22:45:00Z', 'expires_at': '2026-09-09T22:45:00Z',
            'minimum_interval_seconds': 10, 'dependencies': dependencies,
            **dict.fromkeys(('raw_redistribution', 'claim_acceptance', 'collector_enabled',
                             'redirects', 'retries', 'cookies'), False),
            'entries': [{'url': importer.SOURCE_URL}]}
        self.add(self.plan_name, self.plan)
        self.add(self.capture + '/plan.json', self.plan)
        for index, dependency in enumerate(dependencies):
            self.add(self.capture + f'/dependencies/{index}-' + Path(dependency['path']).name,
                     self.files[dependency['path']])
        self.attempts = [self.attempt('release', importer.SOURCE_URL, 'responses/release.body', self.body,
            '2026-09-08T22:49:03Z', RETRIEVED, 'source_document')]
        self.add(self.capture + '/source_checks.json', self.ledger(self.attempts))
        self.manifest(self.capture)
        self.research = {'source': {'sha256': importer.SOURCE_SHA, 'url': importer.SOURCE_URL,
            'publisher': 'Tower Semiconductor Ltd.', 'title': TITLE,
            'publication_date': '2025-02-10', 'publication_precision': 'day',
            'retained_path': self.source_path, 'media_type': 'text/html', 'bytes': len(self.body),
            'retrieval_started_at': '2026-09-08T22:49:03Z', 'retrieval_finished_at': RETRIEVED},
            'claim_acceptance': False, 'collector_enabled': False,
            'review_completed_at': '2026-09-08T22:51:00Z',
            'acquisition': {'plan': self.plan_name, 'packet': self.capture,
                'plan_sha256': self.digest(self.plan_name), 'manifest_sha256': self.digest(self.capture + '/manifest.json'),
                'ledger_sha256': self.digest(self.capture + '/source_checks.json'), 'policy_probes': probes},
            'evidence': copy.deepcopy(self.evidence), 'event_candidates': self.candidates(),
            'rights': copy.deepcopy(importer.RIGHTS),
            'excluded_inferences': ['Synthetic fixture; no physical inference or independent event verification.']}
        self.add(self.research_name, self.research)
        self.patch('RESEARCH_SHA', self.digest(self.research_name))
        self.review = {'format': importer.FORMAT, 'decision': importer.DECISION, 'reviewed_at': REVIEWED,
            'reviewed_by': 'Synthetic exposed reviewer', 'prior_exposure': 'All fixture events previously seen.',
            'interpretation_method': 'Read complete synthetic blocks and preserve distinct source-native event periods.',
            'selection': 'Complete three-event source-native selection, not a closure census.',
            'parent_database_sha256': runtime.digest(self.parent),
            'research_review': {'path': self.research_name, 'sha256': importer.RESEARCH_SHA},
            'input_files': self.inventory(), 'subjects': copy.deepcopy(importer.SUBJECTS), 'rows': importer.rows(),
            'rights': copy.deepcopy(importer.RIGHTS), 'boundaries': copy.deepcopy(importer.BOUNDARIES)}
        self.review_path = self.root / 'review.json'
        self.write_review()

    def patch(self, name, value):
        patcher = mock.patch.object(importer, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def candidates(self):
        subjects = ['Tower Fab1 legacy 150mm flows', 'Tower Fab1', 'Tower-reported Arai facility']
        return [{'key': row['key'], 'source_native_subject': subject,
                 'subject_kind': 'facility', 'canonical_entity_id': None, **row['value'],
                 'support': row['support'], 'scope_limit': row['scope_limit']}
                for row, subject in zip(importer.rows(), subjects)]

    def add(self, name, value):
        raw = value if isinstance(value, bytes) else canonical(value)
        self.files[name] = raw
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def digest(self, name):
        return runtime.digest(self.files[name])

    def inventory(self):
        return {name: {'sha256': runtime.digest(body), 'bytes': len(body)} for name, body in self.files.items()}

    def manifest(self, packet):
        self.add(packet + '/manifest.json', {
            name[len(packet) + 1:]: {'sha256': runtime.digest(body), 'bytes': len(body)}
            for name, body in self.files.items()
            if name.startswith(packet + '/') and name != packet + '/manifest.json'})

    def attempt(self, identity, url, path, body, started, finished, purpose):
        return {'id': identity, 'url': url, 'final_url': url, 'path': path,
            'started_at': started, 'finished_at': finished, 'http_status': 200,
            'curl_exit_code': 0, 'error': None, 'tls_verification_result': 0, 'outcome': 'succeeded',
            'bytes': len(body), 'sha256': runtime.digest(body), 'purpose': purpose, 'company': 'Tower Semiconductor',
            'country_code': 'IL', 'source_family': 'tower-issuer', 'scope': 'Synthetic exact URL fixture',
            'negative_evidence_eligible': False, 'redirect_count': 0}

    def ledger(self, attempts):
        return {'format': 'semiconductor-atlas-publisher-source-checks-v1', 'review_scope_id': 'synthetic',
            'checked_source_id': 'tower-fixture', 'clock_precision': 'second',
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
        self.research['acquisition']['manifest_sha256'] = self.digest(self.capture + '/manifest.json')
        self.research['acquisition']['ledger_sha256'] = self.digest(self.capture + '/source_checks.json')
        self.add(self.research_name, self.research)
        self.patch('RESEARCH_SHA', self.digest(self.research_name))
        self.review['research_review']['sha256'] = importer.RESEARCH_SHA
        self.review['input_files'] = self.inventory()
        self.write_review()

    def replace_source(self, body, evidence):
        self.body, self.evidence = body, evidence
        self.add(self.source_path, body)
        self.patch('SOURCE_SHA', runtime.digest(body))
        self.attempts[0].update(sha256=importer.SOURCE_SHA, bytes=len(body))
        self.add(self.capture + '/source_checks.json', self.ledger(self.attempts))
        self.research['source'].update(sha256=importer.SOURCE_SHA, bytes=len(body))
        self.research['evidence'] = copy.deepcopy(evidence)
        self.reseal_research()

    def test_three_events_replay_exactly_and_preserve_every_inherited_row(self):
        result = self.execute()
        raw = self.output.read_bytes()
        self.assertEqual(result['admitted_at'], ADMITTED)
        old, successor = importer.open_image(self.parent, 8), importer.open_image(raw, 8)
        self.addCleanup(old.close)
        self.addCleanup(successor.close)
        self.assertEqual(repository.validate_database(successor), [])
        for table, in old.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            before = {tuple(row) for row in old.execute(f'SELECT * FROM "{table}"')}
            after = {tuple(row) for row in successor.execute(f'SELECT * FROM "{table}"')}
            self.assertTrue(before <= after, table)
        self.assertEqual(database.schema_version(successor), 8)
        self.assertEqual([tuple(row) for row in old.execute('SELECT * FROM schema_migrations')],
                         [tuple(row) for row in successor.execute('SELECT * FROM schema_migrations')])
        self.assertEqual(successor.execute('SELECT count(*) FROM milestone_values').fetchone()[0], 3)
        for table in ('capacity_values', 'source_quantity_metadata', 'geometry_values',
                      'source_entity_assignments', 'entity_resolution_decisions'):
            self.assertEqual(successor.execute(f'SELECT count(*) FROM {table}').fetchone()[0],
                             old.execute(f'SELECT count(*) FROM {table}').fetchone()[0])
        self.assertEqual(successor.execute('SELECT count(*) FROM entities').fetchone()[0], 3)
        self.assertEqual(successor.execute("SELECT count(*) FROM entities WHERE kind='facility'").fetchone()[0], 3)
        self.assertEqual(len(service.source_claim_records(successor, recorded_at=STARTED)), 1)
        self.assertEqual(len(service.source_claim_records(successor, recorded_at=ADMITTED)), 4)
        self.assertEqual(len(service.claim_records(successor, as_of='2026-09-08', recorded_at=LATER)), 1)
        claims = successor.execute("SELECT * FROM claim_versions WHERE id != 'prior'").fetchall()
        self.assertTrue(all(row['claim_kind'] == 'source_statement' and row['valid_from'] is None
            and row['valid_to'] is None and row['confidence'] is None and row['recorded_at'] == ADMITTED for row in claims))
        evidence = successor.execute("SELECT role FROM claim_evidence WHERE claim_version_id != 'prior'").fetchall()
        self.assertEqual(sorted(row[0] for row in evidence), ['context'] * 6 + ['support'] * 6)
        subjects = {row['milestone_type']: row['subject_entity_id'] for row in successor.execute(
            'SELECT m.milestone_type,s.subject_entity_id FROM milestone_values AS m '
            'JOIN claim_versions AS v ON v.id=m.claim_version_id JOIN claim_series AS s ON s.id=v.series_id')}
        self.assertEqual(subjects['legacy_150mm_flow_discontinuation'], subjects['last_fab_output'])
        self.assertNotEqual(subjects['last_fab_output'], subjects['operations_cessation'])
        values = {row['milestone_type']: dict(row) for row in successor.execute('SELECT * FROM milestone_values')}
        for metric, low, high, precision in (
            ('legacy_150mm_flow_discontinuation', '2024-10-01', '2024-12-31', 'quarter'),
            ('last_fab_output', '2025-01-01', '2025-01-31', 'month'),
            ('operations_cessation', '2022-01-01', '2022-12-31', 'year')):
            value = values[metric]
            self.assertEqual((value['status'], value['date_low'], value['date_base'], value['date_high'], value['date_precision']),
                             ('completed', low, None, high, precision))
        verified = self.execute(True)
        self.assertTrue(verified['verified'])
        self.assertEqual(verified['database_sha256'], result['database_sha256'])
        self.assertEqual(self.output.read_bytes(), raw)
        self.assertEqual(self.parent_path.read_bytes(), self.parent)

    def test_complete_three_event_selection_and_semantics_are_required(self):
        for rows in (self.review['rows'][:2], self.review['rows'][::-1], self.review['rows'] * 2):
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                self.validate({**self.review, 'rows': rows})
        review = copy.deepcopy(self.review)
        review['rows'][0]['unreviewed_inference'] = 'zero_capacity'
        with self.assertRaises(ValueError): self.validate(review)

    def test_publication_retrieval_admission_and_event_clocks_stay_distinct(self):
        self.execute()
        connection = importer.open_image(self.output.read_bytes(), 8)
        self.addCleanup(connection.close)
        document = connection.execute('SELECT * FROM source_documents WHERE document_url=?',
                                      (importer.SOURCE_URL,)).fetchone()
        self.assertEqual(document['published_at'], '2025-02-10')
        self.assertEqual(document['retrieved_at'], RETRIEVED)
        self.assertEqual(document['content_sha256'], runtime.digest(self.body))
        records = connection.execute('SELECT observed_at FROM source_records WHERE source_document_id=?',
                                     (document['id'],)).fetchall()
        self.assertEqual([row[0] for row in records], [RETRIEVED] * 3)
        run = connection.execute('SELECT * FROM ingestion_runs').fetchone()
        self.assertEqual((run['started_at'], run['completed_at']), (STARTED, ADMITTED))
        for table in ('sources', 'entities', 'claim_series'):
            rows = connection.execute(f'SELECT created_at FROM {table} WHERE created_at=?', (ADMITTED,)).fetchall()
            self.assertTrue(rows, table)
        self.assertEqual(len(service.source_claim_records(connection, recorded_at='2025-02-10T23:59:59Z')), 0)
        self.assertEqual(len(service.source_claim_records(connection, recorded_at=RETRIEVED)), 1)

    def test_resealed_source_with_wrong_event_dates_cannot_supply_reviewed_events(self):
        self.replace_source(*html_fixture(paragraph=
            'During the fourth quarter of 2023, the Company discontinued its legacy 150mm flows in Fab1, '
            'with the last wafer output in January 2024. Strategic flows were transferred to Fab2.'))
        with self.assertRaises(ValueError): self.execute()
        self.replace_source(*html_fixture(arai=
            'Non-GAAP reconciliation concerns cessation of operations at a different facility during 2021.'))
        with self.assertRaises(ValueError): self.execute()
        self.assertFalse(self.output.exists())

    def test_subjects_rights_and_boolean_boundaries_cannot_change(self):
        for field in importer.BOUNDARIES:
            for value in (True, 0):
                review = copy.deepcopy(self.review)
                review['boundaries'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError): self.validate(review)
        for field, value in [('rights', {**importer.RIGHTS, 'commercial_use_authorized': True}),
                             ('subjects', {}), ('decision', 'research_only')]:
            with self.subTest(field=field), self.assertRaises(ValueError): self.validate({**self.review, field: value})

    def test_review_hash_and_unreviewed_research_are_rejected(self):
        with self.assertRaises(ValueError): importer.validate_review(canonical(self.review), '0' * 64)
        review = copy.deepcopy(self.review)
        review['research_review']['sha256'] = '0' * 64
        with self.assertRaises(ValueError): self.validate(review)

    def test_review_requires_explicit_exposure_selection_and_interpretation(self):
        for field in ('reviewed_by', 'prior_exposure', 'selection', 'interpretation_method'):
            for value in ('', '  ', None):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.validate({**self.review, field: value})

    def test_resealed_candidate_cannot_gain_canonical_identity_or_narrower_dates(self):
        original = copy.deepcopy(self.research['event_candidates'])
        for field, value in [('canonical_entity_id', 'unreviewed:facility'),
                             ('source_native_subject', 'All Tower facilities'),
                             ('subject_kind', 'organization'), ('date_base', '2024-11-15'),
                             ('date_low', '2024-12-31'), ('date_literal', 'December 31, 2024'),
                             ('support', ['publication_dateline'])]:
            self.research['event_candidates'] = copy.deepcopy(original)
            self.research['event_candidates'][0][field] = value
            self.reseal_research()
            with self.subTest(field=field), self.assertRaises(ValueError): self.execute()
        self.assertFalse(self.output.exists())

    def test_changed_or_missing_source_is_not_an_empty_observation(self):
        path = self.root / self.source_path
        path.write_bytes(self.body + b'changed')
        with self.assertRaises(ValueError): self.execute()
        path.unlink()
        with self.assertRaises(OSError): self.execute()
        self.assertFalse(self.output.exists())

    def test_extra_packet_file_symlink_and_unrelated_input_are_rejected(self):
        extra = self.root / self.capture / 'extra.body'
        extra.write_bytes(b'unreviewed')
        with self.assertRaises(ValueError): self.execute()
        extra.unlink()
        extra.symlink_to(self.root / self.source_path)
        with self.assertRaises(ValueError): self.execute()
        extra.unlink()
        self.add('artifacts/unrelated', b'unrelated')
        self.review['input_files'] = self.inventory()
        self.write_review()
        with self.assertRaises(ValueError): self.execute()

    def test_inventory_is_bounded_to_safe_repository_paths(self):
        for name in ('../escape', '/absolute', 'artifacts/../escape', 'artifacts//bad', 'unknown/body'):
            review = copy.deepcopy(self.review)
            review['input_files'][name] = {'sha256': 'a' * 64, 'bytes': 1}
            with self.subTest(name=name), self.assertRaises(ValueError): self.validate(review)

    def test_policy_copy_is_bound_to_original_even_when_resealed(self):
        name = self.capture + '/dependencies/0-policy.body'
        self.add(name, b'Changed synthetic policy')
        self.reseal_research()
        with self.assertRaises(ValueError): self.execute()
        self.assertFalse(self.output.exists())

    def test_redirect_and_post_review_capture_fail_after_reseal(self):
        for field, value in [('redirect_count', 1), ('finished_at', '2026-09-08T23:30:00Z')]:
            attempts = copy.deepcopy(self.attempts)
            attempts[0][field] = value
            self.add(self.capture + '/source_checks.json', self.ledger(attempts))
            self.research['source']['retrieval_finished_at'] = attempts[0]['finished_at']
            self.reseal_research()
            with self.subTest(field=field), self.assertRaises(ValueError): self.execute()

    def test_hidden_missing_or_incomplete_evidence_cannot_be_admitted(self):
        self.replace_source(*html_fixture(hidden='complete_fab_consolidation_paragraph'))
        with self.assertRaises(ValueError): self.execute()
        self.replace_source(*html_fixture())
        self.research['evidence'].pop('complete_arai_context_paragraph')
        self.reseal_research()
        with self.assertRaises((ValueError, KeyError)): self.execute()
        self.replace_source(*html_fixture())
        span = self.research['evidence']['complete_fab_consolidation_paragraph']
        span['end'] -= 4
        span['sha256'] = runtime.digest(self.body[span['start']:span['end']])
        self.reseal_research()
        with self.assertRaises(ValueError): self.execute()

    def test_parent_review_and_actual_admission_clocks_are_enforced(self):
        for field, value in [('parent_database_sha256', '0' * 64),
                             ('reviewed_at', '2026-09-08T22:00:00Z'), ('reviewed_at', LATER)]:
            prior = self.review[field]
            self.review[field] = value
            self.write_review()
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): self.execute()
            self.review[field] = prior
        self.write_review()
        with self.assertRaises(ValueError): importer.open_image(parent_fixture(), 8)
        self.assertFalse(self.output.exists())

    def test_output_collision_and_untrusted_successor_hash_never_overwrite(self):
        self.execute()
        before = self.output.read_bytes()
        with self.assertRaises(ValueError): self.execute()
        with self.assertRaises(ValueError): self.execute(True, expected_output_sha256='0' * 64)
        with self.assertRaises(ValueError): self.execute(expected_output_sha256='0' * 64)
        self.assertEqual(self.output.read_bytes(), before)

    def test_resealed_database_tampering_fails_complete_successor_reconstruction(self):
        self.execute()
        connection = sqlite3.connect(self.output)
        try:
            connection.execute('DROP TRIGGER milestone_values_immutable_update')
            connection.execute("UPDATE milestone_values SET date_literal='changed'")
            connection.commit()
        finally:
            connection.close()
        before = self.output.read_bytes()
        with self.assertRaises(ValueError): self.execute(True)
        self.assertEqual(self.output.read_bytes(), before)

    def test_input_and_code_drift_fail_before_output_installation(self):
        original = importer.populate
        def drift(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.root / self.source_path).write_bytes(self.body + b'drift')
            return result
        with mock.patch.object(importer, 'populate', side_effect=drift), self.assertRaises(ValueError): self.execute()
        self.assertFalse(self.output.exists())
        (self.root / self.source_path).write_bytes(self.body)
        with mock.patch.object(importer, 'codes', return_value={'changed': 'a' * 64}):
            with self.assertRaises(ValueError): self.execute()
        with mock.patch.object(importer, 'codes', side_effect=[importer._LOADED_CODES, {'changed': 'b' * 64}]):
            with self.assertRaises(ValueError): self.execute()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
