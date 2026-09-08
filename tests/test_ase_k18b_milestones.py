"""Offline synthetic evidence, queue and complete-successor K18B admission tests."""

import copy
import json
import unittest
from unittest import mock

from semiconductor_atlas import curated_review, database, models as m
from semiconductor_atlas import repository as r, service
from semiconductor_atlas import ase_k18b_milestones as importer
from scripts import frozen_runtime as runtime
from tests import test_curated_capture


CREATED = '2026-01-01T00:00:00Z'
CAPTURED = '2026-09-08T08:00:00Z'
IMPORTED = '2026-09-08T08:01:00Z'
TEXT_REVIEWED = '2026-09-08T08:02:00Z'
HANDED_OFF = '2026-09-08T08:03:00Z'
RECHECKED = '2026-09-08T08:04:00Z'
REIMPORTED = '2026-09-08T08:05:00Z'
REVIEWED = '2026-09-08T10:00:00Z'
STARTED = '2026-09-08T11:00:00Z'
ADMITTED = '2026-09-08T11:00:01Z'
LATER = '2026-09-08T12:00:00Z'


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def html_fixture(*, paragraph=None, hidden=False):
    body = bytearray(b'<html><body>')
    evidence = {}
    for key, tag, text in [
        ('complete_opening_paragraph', 'p', paragraph or 'K18B廠於今(3)日舉行動土典禮。K18B廠房目標2028年第一季完工。'),
        ('publication_date', 'dd', '上版日期：114-10-03'),
        ('cms_update_and_publisher', 'dd', '資料更新時間：115-08-14 14:25 資料來源：經濟發展局'),
    ]:
        start = len(body)
        attribute = ' hidden' if hidden and key == 'complete_opening_paragraph' else ''
        fragment = f'<{tag}{attribute}>{text}</{tag}>'.encode()
        body.extend(fragment)
        evidence[key] = {'start': start, 'end': len(body), 'sha256': runtime.digest(fragment)}
    body.extend(b'</body></html>')
    return bytes(body), evidence


def parent_fixture():
    connection, _ = database.initialize(':memory:', target_version=7)
    try:
        r.add_source_family(connection, m.SourceFamily('family', 'fixture', 'Fixture', CREATED))
        r.add_source(connection, m.Source('source', 'family', 'fixture', 'Synthetic', 'Fixture',
            'https://example.test', CREATED))
        r.add_source_document(connection, m.SourceDocument('document', 'source',
            'https://example.test/prior', 'Unrelated prior document', CREATED, 'a' * 64))
        r.add_entity(connection, m.Entity('prior', m.EntityKind.FACILITY, 'prior:facility', CREATED))
        capacity = m.CapacityValue('wafer_capacity', m.CapacityBasis.ANNOUNCED, 'wafers_per_month', 1, 2, 3)
        r.add_claim_series(connection, m.ClaimSeries('prior', 'prior', 'prior:capacity',
            'capacity', capacity.kind, CREATED))
        r.insert_claim(connection, m.ClaimVersion('prior', 'prior', '2025-01-01', CREATED,
            m.ClaimKind.SOURCE_STATEMENT, 'fixture', None), capacity, evidence=[m.EvidenceLink('document')])
        connection.commit()
        return connection.serialize()
    finally:
        connection.close()


class AseK18bMilestoneTests(unittest.TestCase):
    """Exercise real offline capture replay and event history, not mocked lineage."""

    def setUp(self):
        self.capture = test_curated_capture.CuratedCaptureTests()
        self.capture.setUp()
        self.addCleanup(self.capture.doCleanups)
        self.root = self.capture.root
        self.body, self.evidence = html_fixture()
        self.parent = parent_fixture()
        (self.root / 'parent.sqlite').write_bytes(self.parent)
        sha_patch = mock.patch.object(importer, 'SOURCE_SHA', runtime.digest(self.body))
        sha_patch.start()
        self.addCleanup(sha_patch.stop)
        self.capture.missing_robots_plan()
        plan = self.capture.plan
        plan['checked_facility_key'] = importer.SCOPE_KEY
        for policy in plan['policies']:
            policy['url'] = policy['url'].replace('https://example.org', 'https://invest.kcg.gov.tw')
        plan['documents'][0].update(url=importer.SOURCE_URL, company='ASE', country_code='TW',
            scope='City-authored K18B building groundbreaking and completion-target narrative only; not the existing Kaohsiung campus aggregate or attained production/qualification/capacity.',
            required_text=['K18B', '上版日期', '資料來源：經濟發展局'])
        self.access_path = self.root / 'review.json'
        self.access = {
            'format': 'semiconductor-atlas-ase-k18b-municipal-source-review-v1',
            'decision': 'approve_one_exact_municipal_article_for_local_text_review_only',
            'reviewed_at': CREATED, 'reviewer': 'Synthetic access reviewer',
            'source_native_scope': importer.SCOPE_KEY, 'canonical_facility_assignment': None,
            'document': {key: plan['documents'][0][key] for key in ('id', 'url', 'scope')},
            'rights': {'raw_redistribution': False, 'model_training': False,
                'license_status': 'unknown_no_open_license_identified', 'copyright_holder': importer.PUBLISHER},
            **dict.fromkeys(('claim_acceptance', 'delivery_eligible', 'scheduled_execution_changed',
                'registered_study_changed', 'baseline_changed'), False),
        }
        self.access['document']['publisher'] = importer.PUBLISHER
        self.access['document']['scope'] = 'Source-native K18B building project only. Separate from the complete Kaohsiung campus aggregate, K28 at Dashe and the Nanzih Third Park buildings.'
        self.write(self.access_path, self.access)
        plan['review_record'] = self.binding(self.access_path)
        self.capture.bodies['document'] = self.body
        self.capture.clock = CAPTURED
        self.packet, _ = self.capture.run_capture('capture-v1')
        self.queue = self.root / 'queue.sqlite'
        curated_review.initialize_queue(self.queue)
        with mock.patch.object(curated_review, '_now', return_value=IMPORTED):
            imported = curated_review.import_capture(self.queue, self.packet)
        candidate = curated_review.queue_report(self.queue)['candidates'][0]
        self.text_path = self.root / 'text-review.json'
        self.text_review = {
            'format': 'semiconductor-atlas-ase-k18b-source-text-adjudication-v1',
            'decision': 'handoff_reviewed_text_for_separate_core_claim_admission',
            'reviewed_at': TEXT_REVIEWED, 'reviewer': 'Synthetic exposed source-text reviewer',
            'candidate_id': candidate['id'], 'expected_event_id': candidate['last_event_id'],
            'source_url': importer.SOURCE_URL, 'access_review': self.binding(self.access_path),
            'evidence_spans': copy.deepcopy(self.evidence),
            'source_capture': {'path': self.packet.relative_to(self.root).as_posix(),
                'manifest_sha256': imported['run_id'], 'body_path': 'responses/document.body',
                'body_sha256': runtime.digest(self.body), 'body_bytes': len(self.body),
                'normalized_sha256': candidate['current_text_sha256'], 'retrieved_at': CAPTURED,
                'queue_admitted_at': IMPORTED},
            'boundaries': {'core_claim_acceptance': False, 'production_attainment': False},
        }
        self.write(self.text_path, self.text_review)
        with mock.patch.object(curated_review, '_now', return_value=HANDED_OFF):
            candidate = curated_review.record_decision(self.queue, candidate['id'], action='handoff',
                reviewer='Synthetic reviewer', reason='Separate core review of two milestone statements',
                expected_event_id=candidate['last_event_id'], evidence_ref='text-review.json')
        # An unchanged capture advances the queue head without changing the candidate CAS.
        self.capture.clock = RECHECKED
        self.recheck_packet, _ = self.capture.run_capture('capture-v2', self.packet)
        with mock.patch.object(curated_review, '_now', return_value=REIMPORTED):
            curated_review.import_capture(self.queue, self.recheck_packet)
        self.events_path = self.root / 'events.json'
        self.events = curated_review.export_events(self.queue)
        self.write(self.events_path, self.events)
        self.review = {
            'format': importer.FORMAT, 'decision': importer.DECISION, 'reviewed_at': REVIEWED,
            'reviewed_by': 'Synthetic claim reviewer', 'prior_exposure': 'Exposed synthetic fixture; not independent.',
            'selection': 'The complete two-statement fixture.', 'parent_database_sha256': runtime.digest(self.parent),
            'source_url': importer.SOURCE_URL, 'source_sha256': runtime.digest(self.body), 'source_bytes': len(self.body),
            'project': copy.deepcopy(importer.PROJECT), 'access_review': self.binding(self.access_path),
            'source_review': self.binding(self.text_path), 'evidence': copy.deepcopy(self.evidence),
            'rows': importer.rows(), 'boundaries': copy.deepcopy(importer.BOUNDARIES),
            'source': {'events': self.binding(self.events_path), 'queue_head': self.events['events'][-1]['event_id'],
                'candidate_id': candidate['id'], 'expected_event_id': candidate['last_event_id'],
                'run_id': imported['run_id'], 'document_id': 'document',
                'plan_sha256': runtime.digest((self.packet / 'plan.json').read_bytes()),
                'text_sha256': candidate['current_text_sha256'],
                'body': self.binding(self.packet / 'responses/document.body'), 'retrieved_at': CAPTURED},
        }
        self.review_path = self.root / 'claim-review.json'
        self.write(self.review_path, self.review)

    def write(self, path, value):
        path.write_bytes(canonical(value))

    def binding(self, path):
        return {'path': path.relative_to(self.root).as_posix(), 'sha256': runtime.digest(path.read_bytes())}

    def validate(self, review=None, body=None):
        raw = canonical(self.review if review is None else review)
        return importer.validate_review(raw, self.body if body is None else body, runtime.digest(raw))

    def execute(self, verify=False, **changes):
        options = dict(parent=self.root / 'parent.sqlite', review_path=self.review_path,
            reference_root=self.root, expected_review_sha256=runtime.digest(self.review_path.read_bytes()),
            output=self.root / 'successor.sqlite', verify_only=verify)
        if verify:
            options['expected_output_sha256'] = runtime.digest((self.root / 'successor.sqlite').read_bytes())
        else:
            options['queue'] = self.queue
        options.update(changes)
        with mock.patch.object(importer, 'now', side_effect=[LATER] if verify else [STARTED, ADMITTED]):
            return importer.admit(**options)

    def reopen(self):
        with mock.patch.object(curated_review, '_now', return_value=LATER):
            curated_review.record_decision(self.queue, self.review['source']['candidate_id'], action='reopen',
                reviewer='Synthetic reviewer', reason='Later source-review work',
                expected_event_id=self.review['source']['expected_event_id'])

    def test_exact_two_source_milestones_preserve_every_parent_row_and_replay(self):
        queue_before = self.queue.read_bytes()
        result = self.execute()
        raw = (self.root / 'successor.sqlite').read_bytes()
        verified = self.execute(True)
        self.assertEqual(verified['database_sha256'], runtime.digest(raw))
        self.assertEqual((self.root / 'successor.sqlite').read_bytes(), raw)
        self.assertEqual((self.root / 'parent.sqlite').read_bytes(), self.parent)
        self.assertEqual(self.queue.read_bytes(), queue_before)
        self.assertTrue(verified['verified'])
        self.assertEqual(result['new_milestone_statements'], 2)
        self.assertFalse(any(result['boundaries'].values()))
        original, successor = importer.open_image(self.parent), importer.open_image(raw)
        try:
            self.assertEqual(r.validate_database(successor), [])
            changes = {'source_families': 1, 'sources': 1, 'source_documents': 1, 'ingestion_runs': 1,
                'ingestion_run_documents': 2,
                'entities': 1, 'claim_series': 2, 'claim_versions': 2, 'claim_values': 2, 'milestone_values': 2,
                'source_records': 2, 'claim_evidence': 5}
            tables = [row[0] for row in original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                before = [tuple(row) for row in original.execute(f'SELECT * FROM "{table}"')]
                after = [tuple(row) for row in successor.execute(f'SELECT * FROM "{table}"')]
                self.assertEqual(len(after) - len(before), changes.get(table, 0), table)
                self.assertTrue(all(row in after for row in before), table)
            entity = successor.execute('SELECT * FROM entities WHERE id=?', (result['source_native_entity_id'],)).fetchone()
            self.assertEqual(entity['kind'], 'project')
            self.assertEqual(service.source_claim_records(successor, recorded_at=STARTED,
                subject_entity_id=entity['id']), [])
            claims = service.source_claim_records(successor, recorded_at=ADMITTED, subject_entity_id=entity['id'])
            self.assertEqual(len(claims), 2)
            self.assertTrue(all(row['valid_from'] is None and row['valid_to'] is None
                and row['confidence'] is None and row['claim_kind'] == 'source_statement' for row in claims))
            values = {row['milestone_type']: dict(row) for row in successor.execute('SELECT * FROM milestone_values')}
            self.assertEqual(set(values), {'groundbreaking', 'building_complete'})
            self.assertEqual((values['groundbreaking']['status'], values['groundbreaking']['date_low'],
                values['groundbreaking']['date_high'], values['groundbreaking']['date_precision']),
                ('completed', '2025-10-03', '2025-10-03', 'day'))
            self.assertEqual((values['building_complete']['status'], values['building_complete']['date_low'],
                values['building_complete']['date_high'], values['building_complete']['date_precision']),
                ('expected', '2028-01-01', '2028-03-31', 'quarter'))
            self.assertTrue(all(value['date_base'] is None for value in values.values()))
            self.assertEqual(len(service.claim_records(successor, as_of='2026-09-08', recorded_at=LATER)), 1)
            self.assertEqual(successor.execute('SELECT count(*) FROM capacity_values').fetchone()[0], 1)
            self.assertEqual(successor.execute('SELECT count(*) FROM source_quantity_metadata').fetchone()[0], 0)
            evidence = successor.execute("SELECT role, excerpt FROM claim_evidence WHERE claim_version_id != 'prior'").fetchall()
            self.assertEqual(sum(row['role'] == 'support' for row in evidence), 3)
            self.assertEqual(sum(row['role'] == 'context' for row in evidence), 2)
            self.assertTrue(all(row['excerpt'] is None for row in evidence))
        finally:
            original.close()
            successor.close()

    def test_complete_block_validation_and_explicit_derived_groundbreaking_date(self):
        review = self.validate()
        self.assertIn('ROC year 114 + 1911 = 2025', review['rows'][0]['dating_basis'])
        self.assertIn('Not an independently dated observation', review['rows'][0]['dating_basis'])
        self.assertIn('Not production', review['rows'][1]['dating_basis'])
        self.assertEqual(review['rows'][0]['support'], ['complete_opening_paragraph', 'publication_date'])

    def test_missing_duplicate_reordered_or_reinterpreted_rows_reject(self):
        for rows in (self.review['rows'][:-1], [*self.review['rows'], self.review['rows'][0]],
                     list(reversed(self.review['rows']))):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, 'two-milestone'):
                self.validate({**self.review, 'rows': rows})
        for index, field, value in [(0, 'date_low', '2026-08-14'), (0, 'status', 'expected'),
                (1, 'milestone_type', 'production_start'), (1, 'status', 'completed'),
                (1, 'date_base', '2028-02-15'), (1, 'date_precision', 'day'),
                (1, 'date_high', '2028-12-31')]:
            review = copy.deepcopy(self.review)
            review['rows'][index]['value'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'semantics differs'):
                self.validate(review)

    def test_source_hash_url_size_and_review_hash_are_independent_gates(self):
        with self.assertRaisesRegex(ValueError, 'publisher bytes'):
            self.validate(body=self.body + b' ')
        for field, value in [('source_url', importer.SOURCE_URL + '&different=1'),
                ('source_sha256', '0' * 64), ('source_bytes', True), ('source_bytes', len(self.body) + 1)]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'publisher bytes'):
                self.validate({**self.review, field: value})
        with self.assertRaisesRegex(ValueError, 'review hash'):
            importer.validate_review(canonical(self.review), self.body, '0' * 64)

    def test_access_or_source_text_approval_cannot_substitute_for_claim_review(self):
        for decision in (self.access['decision'], self.text_review['decision']):
            with self.subTest(decision=decision), self.assertRaisesRegex(ValueError, 'explicit K18B claim'):
                self.validate({**self.review, 'decision': decision})

    def test_scope_and_every_boundary_fail_closed_including_numeric_false(self):
        for field, value in [('canonical_facility_assignment', 'ase:kaohsiung'),
                ('source_native_subject', 'Kaohsiung campus')]:
            review = copy.deepcopy(self.review)
            review['project'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'identity'):
                self.validate(review)
        for field in importer.BOUNDARIES:
            for value in (True, 0):
                review = copy.deepcopy(self.review)
                review['boundaries'][field] = value
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'excluded claims'):
                    self.validate(review)

    def test_shortened_hidden_or_missing_publication_context_rejects_resealed_review(self):
        review = copy.deepcopy(self.review)
        span = review['evidence']['publication_date']
        span['start'] += len(b'<dd>')
        span['sha256'] = runtime.digest(self.body[span['start']:span['end']])
        with self.assertRaisesRegex(ValueError, 'complete visible HTML block'):
            self.validate(review)
        for options in ({'hidden': True}, {'paragraph': 'K18B廠房目標2028年第一季完工。'}):
            body, evidence = html_fixture(**options)
            review = {**self.review, 'source_bytes': len(body), 'source_sha256': runtime.digest(body), 'evidence': evidence}
            with mock.patch.object(importer, 'SOURCE_SHA', runtime.digest(body)), self.subTest(options=options):
                with self.assertRaises(ValueError):
                    self.validate(review, body)

    def test_current_queue_must_match_frozen_reviewed_head(self):
        self.assertNotEqual(self.review['source']['queue_head'], self.review['source']['expected_event_id'])
        self.reopen()
        with self.assertRaisesRegex(ValueError, 'current K18B queue differs'):
            self.execute()
        self.assertFalse((self.root / 'successor.sqlite').exists())

    def test_historical_replay_ignores_later_live_queue_without_reading_it(self):
        result = self.execute()
        self.reopen()
        later_queue = self.queue.read_bytes()
        with mock.patch.object(curated_review, '_connection', side_effect=AssertionError('historical replay read live queue')):
            verified = self.execute(True)
        self.assertEqual(result['database_sha256'], verified['database_sha256'])
        self.assertEqual(self.queue.read_bytes(), later_queue)

    def test_wrong_queue_mode_or_successor_input_hash_rejects(self):
        with self.assertRaisesRegex(ValueError, 'requires a current queue'):
            self.execute(queue=None)
        with self.assertRaisesRegex(ValueError, 'requires a current queue'):
            self.execute(expected_output_sha256='a' * 64)
        self.execute()
        with self.assertRaisesRegex(ValueError, 'must not depend on the live queue'):
            self.execute(True, queue=self.queue)

    def test_existing_output_and_bad_trusted_hash_do_not_overwrite(self):
        self.execute()
        before = (self.root / 'successor.sqlite').read_bytes()
        with self.assertRaisesRegex(ValueError, 'must not exist'):
            self.execute()
        with self.assertRaisesRegex(ValueError, 'successor hash'):
            self.execute(True, expected_output_sha256='0' * 64)
        self.assertEqual((self.root / 'successor.sqlite').read_bytes(), before)

    def test_parent_hash_and_claim_review_chronology_reject_before_output(self):
        for field, value in [('parent_database_sha256', '0' * 64),
                ('reviewed_at', TEXT_REVIEWED), ('reviewed_at', '2026-09-09T00:00:00Z')]:
            self.write(self.review_path, {**self.review, field: value})
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.execute()
            self.assertFalse((self.root / 'successor.sqlite').exists())

    def test_source_handoff_cas_and_frozen_head_cannot_be_substituted(self):
        for field, value in [('queue_head', self.review['source']['expected_event_id']),
                ('expected_event_id', self.text_review['expected_event_id']), ('candidate_id', '0' * 64),
                ('run_id', '0' * 64), ('plan_sha256', '0' * 64), ('text_sha256', '0' * 64),
                ('retrieved_at', RECHECKED)]:
            review = copy.deepcopy(self.review)
            review['source'][field] = value
            self.write(self.review_path, review)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.execute()
            self.assertFalse((self.root / 'successor.sqlite').exists())

    def test_resealed_text_review_must_match_capture_predecision_and_boundaries(self):
        for field, value in [('decision', 'acknowledge'), ('expected_event_id', '0' * 64),
                ('reviewed_at', CAPTURED), ('reviewed_at', REVIEWED),
                ('boundaries', {'core_claim_acceptance': True})]:
            self.write(self.text_path, {**self.text_review, field: value})
            self.write(self.review_path, {**self.review, 'source_review': self.binding(self.text_path)})
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.execute()
            self.assertFalse((self.root / 'successor.sqlite').exists())

    def test_all_captures_in_frozen_history_are_revalidated(self):
        # The later unchanged capture is not the evidence body used by the two claims.
        path = self.recheck_packet / 'responses/document.body'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(ValueError):
            self.execute()
        self.assertFalse((self.root / 'successor.sqlite').exists())

    def test_loaded_or_mid_admission_code_drift_rejects_without_output(self):
        with mock.patch.object(importer, 'codes', return_value={}):
            with self.assertRaisesRegex(ValueError, 'since module loading'):
                self.execute()
        with mock.patch.object(importer, 'codes', side_effect=[importer._LOADED_CODES, {}]):
            with self.assertRaisesRegex(ValueError, 'during admission or replay'):
                self.execute()
        self.assertFalse((self.root / 'successor.sqlite').exists())

    def test_dependency_and_parent_mutation_after_population_prevents_installation(self):
        original = importer.populate
        for target in (self.review_path, self.root / 'parent.sqlite'):
            prior = target.read_bytes()
            def mutate(*args, **kwargs):
                result = original(*args, **kwargs)
                target.write_bytes(prior + b' ')
                return result
            with self.subTest(target=target.name), mock.patch.object(importer, 'populate', side_effect=mutate):
                with self.assertRaisesRegex(ValueError, 'inputs changed'):
                    self.execute()
            self.assertFalse((self.root / 'successor.sqlite').exists())
            target.write_bytes(prior)

    def test_second_claim_failure_does_not_install_partial_successor(self):
        original = r.insert_claim
        calls = 0
        def fail(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError('synthetic second-claim failure')
            return original(*args, **kwargs)
        queue_before = self.queue.read_bytes()
        with mock.patch.object(r, 'insert_claim', side_effect=fail):
            with self.assertRaisesRegex(ValueError, 'second-claim'):
                self.execute()
        self.assertFalse((self.root / 'successor.sqlite').exists())
        self.assertEqual((self.root / 'parent.sqlite').read_bytes(), self.parent)
        self.assertEqual(self.queue.read_bytes(), queue_before)

    def test_resealed_successor_semantics_mutation_fails_whole_image_reconstruction(self):
        self.execute()
        path = self.root / 'successor.sqlite'
        connection = importer.open_image(path.read_bytes())
        try:
            connection.execute('DROP TRIGGER milestone_values_immutable_update')
            connection.execute("UPDATE milestone_values SET date_literal='Q1 2028 production' WHERE milestone_type='building_complete'")
            connection.commit()
            path.write_bytes(connection.serialize())
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, 'complete parent-plus-K18B'):
            self.execute(True)

    def test_verification_before_successor_admission_cannot_use_future_knowledge(self):
        self.execute()
        with mock.patch.object(importer, 'now', return_value=STARTED):
            with self.assertRaisesRegex(ValueError, 'future K18B successor admission'):
                importer.admit(parent=self.root / 'parent.sqlite', review_path=self.review_path,
                    reference_root=self.root, expected_review_sha256=runtime.digest(self.review_path.read_bytes()),
                    output=self.root / 'successor.sqlite', verify_only=True,
                    expected_output_sha256=runtime.digest((self.root / 'successor.sqlite').read_bytes()))

    def test_already_admitted_project_requires_separate_refresh_contract(self):
        self.execute()
        successor = (self.root / 'successor.sqlite').read_bytes()
        self.write(self.review_path, {**self.review, 'parent_database_sha256': runtime.digest(successor)})
        with self.assertRaisesRegex(ValueError, 'not a refresh importer'):
            self.execute(parent=self.root / 'successor.sqlite', output=self.root / 'duplicate.sqlite')
        self.assertFalse((self.root / 'duplicate.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
