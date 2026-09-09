"""Synthetic, offline tests of the exact Coherent capability admission contract."""

import copy
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from semiconductor_atlas import alerts, curated_capture as capture, database, models, repository, service
from semiconductor_atlas import coherent_sherman_capability as importer
from semiconductor_atlas.evidence_changes import evidence_changes
from semiconductor_atlas.evidence_dossier import build_evidence_dossier
from scripts import admit_coherent_sherman_capability as cli
from scripts import frozen_runtime as runtime
from tests.test_ase_k18b_milestones import parent_fixture


REVIEWED = '2026-09-09T05:00:00Z'
STARTED = '2026-09-09T05:01:00Z'
BEFORE = '2026-09-09T05:01:00.000001Z'
ADMITTED = '2026-09-09T05:01:00.000002Z'
LATER = '2026-09-09T05:02:00Z'


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def html_fixture(*, hidden='', omit_expansion=False, title=None, date_time=None,
                 paragraph_count=6, ancestor=''):
    body = bytearray(('<html><body>' + ancestor).encode())
    evidence = {}
    paragraphs = [
        'Synthetic fixture: a letter of intent to provide up to $50 million to Coherent Corp.',
        'Synthetic existing facility in Sherman, Texas: high-volume 150mm (6-inch) '
        'Indium Phosphide (InP) semiconductor manufacturing facility.',
        'Synthetic photonics context, not an independent physical observation.',
        'The expansion will add advanced wafer fabrication equipment and cleanroom capacity'
        if not omit_expansion else 'The separate expansion wording has been omitted.',
        'Synthetic quoted context; no quantity or dated production start.',
        'Synthetic PDF link context, not permission to fetch another resource.',
    ]
    parts = [
        ('title', 'h1', ' class="nist-page__title"', title or importer.SOURCE['title']),
        ('publication', 'time', ' datetime="' + (date_time or '2026-06-16T12:00:00Z') + '"', 'June 16, 2026'),
        ('narrative', 'div', ' class="text-with-summary"' + hidden,
         ''.join('<p>' + p + '</p>' for p in paragraphs[:paragraph_count])),
    ]
    for key, tag, attrs, text in parts:
        fragment = f'<{tag}{attrs}>{text}</{tag}>'.encode()
        start = len(body)
        body.extend(fragment)
        evidence[key] = dict(start=start, end=len(body), sha256=runtime.digest(fragment), tag=tag)
    body.extend(b'</body></html>')
    return bytes(body), evidence


class CoherentShermanCapabilityTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(':memory:')
        try:
            connection.deserialize(parent_fixture())
            connection.row_factory = sqlite3.Row
            database.apply_migrations(connection, target_version=10)
            repository.add_source_family(connection, models.SourceFamily('nist-family', importer.FAMILY,
                'Existing official disclosure family', '2026-01-01T00:00:00Z'))
            connection.commit()
            self.parent = connection.serialize()
        finally:
            connection.close()
        self.fixture_codes = {'semiconductor_atlas/coherent_sherman_capability.py': 'b' * 64}
        self.patch(importer, 'codes', lambda: self.fixture_codes.copy())
        self.patch(importer, '_LOADED_CODES', self.fixture_codes.copy())
        self.source = copy.deepcopy(importer.SOURCE)
        self.patch(importer, 'SOURCE', self.source)
        self.build_fixture()

    def patch(self, module, name, value):
        patcher = mock.patch.object(module, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def put(self, name, value):
        raw = value if isinstance(value, bytes) else canonical(value)
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return dict(bytes=len(raw), sha256=runtime.digest(raw))

    def build_fixture(self, *, capture_step_seconds=2, **html_changes):
        temporary = tempfile.TemporaryDirectory(prefix='atlas-coherent-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.parent_path = self.root / 'parent.sqlite'
        self.parent_path.write_bytes(self.parent)
        self.output = self.root / 'successor.sqlite'
        self.review_path = self.root / 'claim-review.json'
        self.body, self.spans = html_fixture(**html_changes)
        self.source.update(sha256=runtime.digest(self.body), bytes=len(self.body))
        self.access = dict(
            decision='approved_exact_urls_local_informational_review_only',
            reviewed_at='2026-09-09T04:00:00Z',
            boundaries=dict(source_claim_acceptance=False, physical_capacity_inference=False,
                            raw_redistribution=False, model_training=False),
        )
        access_identity = self.put(importer.ACCESS_REVIEW, self.access)
        self.patch(importer, 'ACCESS_SHA256', access_identity['sha256'])
        common = dict(company='Coherent', country_code='US', source_family=importer.FAMILY,
                      scope='Synthetic exact source fixture only, not manufacturing evidence')
        self.bodies = {
            'robots': b'User-agent: *\nDisallow: /private/\n',
            'rights': b'<p>Synthetic rights review: local informational use only.</p>',
            'bosch': b'<h1>Synthetic Bosch context</h1>',
            self.source['id']: self.body,
            'powerex': b'<h1>Synthetic Powerex context</h1>',
        }
        self.plan = dict(format=capture.PLAN_FORMAT, plan_id='coherent-synthetic',
            review_scope_id='coherent-synthetic', checked_facility_key=importer.SUBJECT['stable_key'],
            reviewed_at=self.access['reviewed_at'], expires_at='2026-09-10T04:00:00Z',
            decision='approved_exact_urls', user_agent='SemiconductorAtlas/0.1',
            minimum_interval_seconds=1, timeout_seconds=2, max_response_bytes=10000,
            notes='Synthetic offline capture; no real acquisition or evidence.',
            review_record=dict(path=importer.ACCESS_REVIEW, sha256=access_identity['sha256']),
            policies=[], documents=[])
        for identity, purpose, normalization, media in (
                ('robots', 'access_policy', 'robots_text_v1', 'text/plain'),
                ('rights', 'rights_policy', 'html_policy_v1', 'text/html')):
            self.plan['policies'].append(dict(common, id=identity,
                url='https://www.nist.gov/' + ('robots.txt' if identity == 'robots' else 'terms'),
                purpose=purpose, normalization=normalization, media_types=[media],
                normalized_sha256=runtime.digest(capture.normalized_bytes(self.bodies[identity], normalization))))
        for identity in ('bosch', self.source['id'], 'powerex'):
            self.plan['documents'].append(dict(common, id=identity,
                url=self.source['url'] if identity == self.source['id'] else 'https://www.nist.gov/' + identity,
                purpose='source_document', media_types=['text/html'], policy_ids=['robots', 'rights'],
                required_text=['Synthetic'] if identity != self.source['id'] else ['Coherent']))
        self.put('review_plans/synthetic-capture-plan.json', self.plan)
        tick = datetime(2026, 9, 9, 4, 10, tzinfo=timezone.utc)
        self.calls = []

        def clock():
            nonlocal tick
            result = tick.isoformat().replace('+00:00', 'Z')
            tick += timedelta(seconds=capture_step_seconds)
            return result

        def transport(entry, destination, plan):
            self.calls.append(entry['id'])
            destination.write_bytes(self.bodies[entry['id']])
            return dict(http_code=200, curl_exit_code=0, content_type=entry['media_types'][0],
                url_effective=entry['url'], num_redirects=0, ssl_verify_result=0)

        with mock.patch.object(capture, '_now', side_effect=clock), mock.patch.object(capture.time, 'sleep'):
            capture.capture_sources(self.root / 'review_plans/synthetic-capture-plan.json', self.root / importer.PACKET,
                                    review_root=self.root, transport=transport)
        ledger = json.loads((self.root / importer.PACKET / 'source_checks.json').read_bytes())
        selected = next(a for a in ledger['attempts'] if a['id'] == self.source['id'])
        self.source['retrieved_at'] = selected['finished_at']
        manifest_sha = runtime.digest((self.root / importer.PACKET / 'manifest.json').read_bytes())
        self.patch(importer, 'PACKET_SHA256', manifest_sha)
        self.review = dict(format=importer.FORMAT,
            decision='accept_three_existing_facility_capability_statements', reviewed_at=REVIEWED,
            reviewer='Synthetic reviewer', prior_exposure='Synthetic fully exposed fixture',
            interpretation='Existing capability separate from prospective expansion; no current physical inference.',
            identity_review='New source-native facility, no canonical assignment by city or name.',
            parent_database_sha256=runtime.digest(self.parent), subject=copy.deepcopy(importer.SUBJECT),
            source_family=importer.FAMILY,
            source=copy.deepcopy(self.source), rows=importer.rows(),
            packet=dict(path=importer.PACKET, manifest_sha256=manifest_sha),
            access_review=self.plan['review_record'], evidence=copy.deepcopy(self.spans),
            rights=dict(use='local_informational_review_only', basis='Synthetic local informational review',
                        raw_redistribution=False, model_training=False),
            boundaries=copy.deepcopy(importer.BOUNDARIES))
        self.write_review()

    def write_review(self):
        self.review_path.write_bytes(canonical(self.review))

    def validate(self, review=None):
        raw = canonical(self.review if review is None else review)
        return importer.validate_review(raw, runtime.digest(raw))

    def evidence(self):
        return importer.load_evidence(self.validate(), importer.snapshot(self.root))

    def execute(self, verify=False, **changes):
        options = dict(parent=self.parent_path, review_path=self.review_path, reference_root=self.root,
            expected_review_sha256=runtime.digest(self.review_path.read_bytes()), output=self.output,
            verify_only=verify, expected_output_sha256=runtime.digest(self.output.read_bytes()) if verify else None)
        options.update(changes)
        with mock.patch.object(importer, 'now', side_effect=[LATER] if verify else [STARTED, ADMITTED]):
            return importer.admit(**options)

    def test_real_offline_packet_replay_and_every_parent_row_preserved(self):
        with mock.patch.object(capture, 'curl_fetch', side_effect=AssertionError('network forbidden')):
            result = self.execute()
            before = self.output.read_bytes()
            replay = self.execute(verify=True)
        self.assertEqual(self.calls, ['robots', 'rights', 'bosch', self.source['id'], 'powerex'])
        self.assertEqual((result['new_capability_statements'], result['new_milestones'],
                          result['new_numeric_capacity_claims']), (3, 0, 0))
        self.assertTrue(replay['verified'])
        self.assertEqual(before, self.output.read_bytes())
        self.assertEqual(self.parent, self.parent_path.read_bytes())
        old, new = importer.open_image(self.parent), importer.open_image(before)
        try:
            for table, in old.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                self.assertLessEqual(set(map(tuple, old.execute(f'SELECT * FROM "{table}"'))),
                    set(map(tuple, new.execute(f'SELECT * FROM "{table}"'))), table)
            self.assertEqual(repository.validate_database(new), [])
            self.assertEqual(list(old.execute('SELECT * FROM source_families')),
                             list(new.execute('SELECT * FROM source_families')))
            self.assertEqual(new.execute('SELECT family_id FROM sources WHERE stable_key=?',
                                        (importer.SOURCE_KEY,)).fetchone()[0], 'nist-family')
            self.assertEqual(service.validate_semantics(new), [])
            self.assertEqual(alerts.detect_revision_alerts(old), alerts.detect_revision_alerts(new))
            for day in ('1900-01-01', '2026-06-16', '2026-09-09', '2099-01-01'):
                self.assertEqual(service.claim_records(old, as_of=day, recorded_at=LATER),
                                 service.claim_records(new, as_of=day, recorded_at=LATER))
        finally:
            old.close()
            new.close()

    def test_exact_atomic_capability_values_and_unknown_physical_dates(self):
        result = self.execute()
        connection = importer.open_image(self.output.read_bytes())
        self.addCleanup(connection.close)
        claims = service.source_claim_records(connection, recorded_at=ADMITTED,
                                              subject_entity_id=result['subject_id'])
        self.assertEqual(len(claims), 3)
        expected = {'material': ('indium_phosphide', None), 'wafer_size': (150, 'mm'),
                    'production_scale': ('high_volume', None)}
        self.assertEqual({c['value']['capability_type']: (c['value']['value'], c['value']['unit'])
                          for c in claims}, expected)
        for claim in claims:
            self.assertEqual(claim['value_kind'], 'capability')
            self.assertEqual(claim['claim_kind'], 'source_statement')
            self.assertIsNone(claim['valid_from'])
            self.assertIsNone(claim['valid_to'])
            self.assertIsNone(claim['confidence'])
            self.assertEqual(claim['recorded_at'], ADMITTED)
            self.assertEqual(claim['value']['qualifier'], importer.QUALIFIER)
        self.assertEqual(connection.execute('SELECT kind FROM entities WHERE id=?',
                                            (result['subject_id'],)).fetchone()[0], 'facility')
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM source_entity_assignments').fetchone()[0], 0)

    def test_exact_cutoff_dossier_and_feed_do_not_backdate_or_create_physical_state(self):
        result = self.execute()
        connection = importer.open_image(self.output.read_bytes())
        self.addCleanup(connection.close)
        for cutoff in (self.source['retrieved_at'], REVIEWED, STARTED, BEFORE):
            self.assertEqual(service.source_claim_records(connection, recorded_at=cutoff,
                subject_entity_id=result['subject_id']), [])
            with self.assertRaisesRegex(ValueError, 'entity is unavailable'):
                build_evidence_dossier(connection, subject_entity_id=result['subject_id'],
                                       as_of='2026-06-16', recorded_at=cutoff)
        for day in ('1900-01-01', '2099-01-01'):
            dossier = build_evidence_dossier(connection, subject_entity_id=result['subject_id'],
                                             as_of=day, recorded_at=ADMITTED)
            self.assertEqual(dossier['world_current_claims'], [])
            self.assertEqual(len(dossier['source_statements']), 3)
            self.assertEqual(dossier['evidence_summary']['source_family_count'], 1)
            self.assertEqual(dossier['evidence_summary']['source_document_count'], 1)
            self.assertEqual(dossier['evidence_summary']['independent_corroboration'], 'not_assessed')
            feed = evidence_changes(connection, since=BEFORE, recorded_at=ADMITTED,
                as_of=day, subject_entity_id=result['subject_id'])
            self.assertEqual(feed['total_events'], 3)
            self.assertEqual(feed['distinct_subjects'], 1)
            self.assertEqual(feed['event_counts'], dict(claim_admitted=3, claim_superseded=0))
            self.assertEqual({e['claim']['id'] for e in feed['events']},
                             {c['claim_id'] for c in result['claims']})
            self.assertNotIn('<p>', json.dumps(feed))
        self.assertEqual(evidence_changes(connection, since=STARTED, recorded_at=BEFORE,
            as_of='2026-06-16', subject_entity_id=result['subject_id'])['total_events'], 0)
        self.assertEqual(evidence_changes(connection, since=ADMITTED, recorded_at=LATER,
            as_of='2026-06-16', subject_entity_id=result['subject_id'])['total_events'], 0)

    def test_one_complete_narrative_three_links_and_capture_clock(self):
        result = self.execute()
        connection = importer.open_image(self.output.read_bytes())
        self.addCleanup(connection.close)
        rows = connection.execute('''SELECT e.excerpt,e.locator,d.published_at,d.retrieved_at,
            r.observed_at,e.source_document_id,r.source_document_id FROM claim_evidence e
            JOIN claim_versions v ON v.id=e.claim_version_id
            JOIN source_documents d ON d.id=e.source_document_id
            JOIN source_records r ON r.id=e.source_record_id WHERE v.created_by_run_id=?''',
            (result['run_id'],)).fetchall()
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertIsNone(row[0])
            self.assertIn(self.spans['narrative']['sha256'], row[1])
            self.assertEqual(row[2], '2026-06-16')
            self.assertEqual(row[3], self.source['retrieved_at'])
            self.assertEqual(row[3], row[4])
            self.assertEqual(row[5], row[6])

    def test_review_hash_and_exact_semantic_population(self):
        with self.assertRaisesRegex(ValueError, 'review hash mismatch'):
            importer.validate_review(canonical(self.review), '0' * 64)
        for mutate in (lambda r: r['rows'].pop(), lambda r: r['rows'].reverse(),
                       lambda r: r['rows'][1].update(value=200),
                       lambda r: r['rows'][2].update(value='qualified'),
                       lambda r: r['rows'][0].update(qualifier='current operating fact'),
                       lambda r: r['subject'].update(canonical_entity_id='finisar'),
                       lambda r: r['subject'].update(kind='project'),
                       lambda r: r['source'].update(retrieved_at='2026-06-16T12:00:00Z'),
                       lambda r: r['packet'].update(manifest_sha256='0' * 64),
                       lambda r: r.update(decision='approve_acquisition'),
                       lambda r: r.update(unreviewed=True)):
            review = copy.deepcopy(self.review)
            mutate(review)
            with self.subTest(review=review), self.assertRaises(ValueError):
                self.validate(review)

    def test_excluded_inferences_and_rights_require_literal_false(self):
        for key in importer.BOUNDARIES:
            for value in (True, 0):
                review = copy.deepcopy(self.review)
                review['boundaries'][key] = value
                with self.subTest(boundary=key, value=value), self.assertRaises(ValueError):
                    self.validate(review)
        for field, value in (('raw_redistribution', True), ('model_training', 0),
                             ('use', 'open_publication'), ('basis', '')):
            review = copy.deepcopy(self.review)
            review['rights'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(review)

    def test_invalid_span_bounds_hash_tag_and_partial_element_rejected(self):
        original = copy.deepcopy(self.review)
        for change in (dict(start=True), dict(start=-1), dict(end=len(self.body) + 1),
                       dict(end=0), dict(sha256='0' * 64), dict(tag='p'),
                       dict(start=0, end=len(self.body), sha256=runtime.digest(self.body))):
            self.review = copy.deepcopy(original)
            self.review['evidence']['narrative'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.evidence()

    def test_hidden_incomplete_or_semantically_inadequate_evidence_rejected(self):
        for change in (dict(hidden=' hidden'), dict(hidden=' aria-hidden="true"'),
                       dict(ancestor='<section hidden>'), dict(omit_expansion=True),
                       dict(paragraph_count=5), dict(title='Other article'),
                       dict(date_time='2026-07-16T12:00:00Z')):
            self.build_fixture(**change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.evidence()

    def test_capture_acceptance_is_distinct_and_cannot_be_silently_widened(self):
        payloads = importer.snapshot(self.root)
        access = copy.deepcopy(self.access)
        access['boundaries']['source_claim_acceptance'] = True
        for both in (False, True):
            changed = dict(payloads)
            changed[importer.ACCESS_REVIEW] = canonical(access)
            if both:
                changed[importer.PACKET + '/review.json'] = canonical(access)
            with self.subTest(both=both), self.assertRaisesRegex(ValueError, 'separate from core'):
                importer.load_evidence(self.validate(), changed)

    def test_unsealed_source_tamper_missing_member_and_unlisted_member_rejected(self):
        body_path = self.root / importer.PACKET / 'responses' / (self.source['id'] + '.body')
        body_path.write_bytes(self.body + b'tampered')
        with self.assertRaisesRegex(ValueError, 'member differs'):
            self.execute()
        body_path.write_bytes(self.body)
        body_path.unlink()
        with self.assertRaises((ValueError, OSError)):
            self.execute()
        body_path.write_bytes(self.body)
        self.put(importer.PACKET + '/unlisted.txt', b'not retained')
        with self.assertRaisesRegex(ValueError, 'unlisted or missing'):
            self.execute()
        self.assertFalse(self.output.exists())

    def test_manifest_access_hash_and_unsafe_manifest_member_rejected(self):
        manifest = self.root / importer.PACKET / 'manifest.json'
        original = manifest.read_bytes()
        manifest.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'packet or access review hash'):
            importer.snapshot(self.root)
        manifest.write_bytes(original)
        access = self.root / importer.ACCESS_REVIEW
        access.write_bytes(access.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'packet or access review hash'):
            importer.snapshot(self.root)
        access.write_bytes(canonical(self.access))
        for name in ('../escape', '/absolute', 'responses\\escape', 'responses/./escape', 'manifest.json'):
            value = json.loads(original)
            value['files'][name] = dict(bytes=1, sha256='a' * 64)
            raw = canonical(value)
            manifest.write_bytes(raw)
            with self.subTest(name=name), mock.patch.object(importer, 'PACKET_SHA256', runtime.digest(raw)), self.assertRaisesRegex(ValueError, 'unsafe'):
                importer.snapshot(self.root)

    def test_evidence_and_packet_symlinks_rejected(self):
        source = self.root / importer.PACKET / 'responses' / (self.source['id'] + '.body')
        alternate = self.root / 'real-body'
        source.rename(alternate)
        source.symlink_to(alternate)
        with self.assertRaises((ValueError, OSError)):
            self.execute()
        source.unlink()
        alternate.rename(source)
        packet = self.root / importer.PACKET
        alternate = self.root / 'real-packet'
        packet.rename(alternate)
        packet.symlink_to(alternate, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.execute()
        self.assertFalse(self.output.exists())

    def test_review_capture_and_admission_chronology_cannot_be_backdated(self):
        for reviewed in ('2026-09-09T04:00:00Z', LATER):
            self.review['reviewed_at'] = reviewed
            self.write_review()
            with self.subTest(reviewed=reviewed), self.assertRaises(ValueError):
                self.execute()
        self.review['reviewed_at'] = REVIEWED
        self.write_review()
        for times in ([STARTED, STARTED], [ADMITTED, STARTED]):
            with self.subTest(times=times), mock.patch.object(importer, 'now', side_effect=times), self.assertRaisesRegex(ValueError, 'actual database admission'):
                importer.admit(parent=self.parent_path, review_path=self.review_path, reference_root=self.root,
                    expected_review_sha256=runtime.digest(self.review_path.read_bytes()), output=self.output)
        self.assertFalse(self.output.exists())

    def test_retained_request_cadence_is_verified_after_offline_replay(self):
        self.build_fixture(capture_step_seconds=0.1)
        with self.assertRaisesRegex(ValueError, 'cadence'):
            self.evidence()

    def test_selected_response_exact_retrieval_clock_cannot_be_replaced(self):
        self.source['retrieved_at'] = self.source['retrieved_at'].replace('Z', '.000001Z')
        self.review['source'] = copy.deepcopy(self.source)
        with self.assertRaisesRegex(ValueError, 'actual retrieval differs'):
            self.evidence()

    def test_parent_hash_and_schema_are_required(self):
        self.parent_path.write_bytes(self.parent + b'changed')
        with self.assertRaisesRegex(ValueError, 'parent database hash'):
            self.execute()
        connection, _ = database.initialize(':memory:', target_version=9)
        try:
            raw = connection.serialize()
        finally:
            connection.close()
        self.parent_path.write_bytes(raw)
        self.review['parent_database_sha256'] = runtime.digest(raw)
        self.write_review()
        with self.assertRaisesRegex(ValueError, 'schema-10'):
            self.execute()
        self.assertFalse(self.output.exists())

    def test_output_is_new_only_including_parent_and_dangling_symlink(self):
        for output in (self.parent_path, self.output):
            if output == self.output:
                output.write_bytes(b'user output')
            before = output.read_bytes()
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'must not exist'):
                self.execute(output=output)
            self.assertEqual(before, output.read_bytes())
        self.output.unlink()
        self.output.symlink_to(self.root / 'missing-target')
        with self.assertRaisesRegex(ValueError, 'must not exist'):
            self.execute()
        self.assertFalse((self.root / 'missing-target').exists())

    def test_output_cannot_invalidate_the_retained_capture_packet(self):
        before = importer.snapshot(self.root)
        output = self.root / importer.PACKET / 'successor.sqlite'
        with self.assertRaisesRegex(ValueError, 'outside the retained acquisition packet'):
            self.execute(output=output)
        self.assertFalse(output.exists())
        self.assertEqual(importer.snapshot(self.root), before)

    def test_missing_core_family_cannot_create_spurious_publisher_independence(self):
        connection = importer.open_image(self.parent)
        try:
            with mock.patch.object(importer, 'FAMILY', 'new-duplicate-family'):
                with self.assertRaisesRegex(ValueError, 'do not split publisher independence'):
                    importer.populate(connection, self.review, {}, 'a' * 64, STARTED,
                                      ADMITTED, self.fixture_codes)
        finally:
            connection.close()

    def test_no_duplicate_refresh_after_first_admission(self):
        self.execute()
        self.review['parent_database_sha256'] = runtime.digest(self.output.read_bytes())
        self.write_review()
        second = self.root / 'second.sqlite'
        with self.assertRaisesRegex(ValueError, 'already admitted'):
            self.execute(parent=self.output, output=second)
        self.assertFalse(second.exists())

    def test_verification_requires_hash_and_complete_parent_plus_claim_reconstruction(self):
        self.execute()
        with self.assertRaisesRegex(ValueError, 'successor hash'):
            self.execute(verify=True, expected_output_sha256='0' * 64)
        connection = importer.open_image(self.output.read_bytes())
        try:
            repository.add_entity(connection, models.Entity('extra', models.EntityKind.FACILITY,
                'unreviewed:extra', ADMITTED, 'Unreviewed extra facility'))
            connection.commit()
            self.output.write_bytes(connection.serialize())
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, 'full parent-plus-capabilities'):
            self.execute(verify=True)

    def test_future_successor_cannot_be_verified_at_earlier_knowledge(self):
        self.execute()
        with mock.patch.object(importer, 'now', return_value=BEFORE), self.assertRaisesRegex(ValueError, 'future successor'):
            importer.admit(parent=self.parent_path, review_path=self.review_path, reference_root=self.root,
                expected_review_sha256=runtime.digest(self.review_path.read_bytes()), output=self.output,
                verify_only=True, expected_output_sha256=runtime.digest(self.output.read_bytes()))

    def test_code_drift_before_and_during_admission_leaves_no_output(self):
        for codes in ([{}], [self.fixture_codes, {}]):
            with self.subTest(codes=codes), mock.patch.object(importer, 'codes', side_effect=codes), self.assertRaisesRegex(ValueError, 'implementation changed'):
                self.execute()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.parent_path.read_bytes(), self.parent)

    def test_parent_or_review_mutation_during_admission_leaves_no_output(self):
        populate = importer.populate
        for path in (self.parent_path, self.review_path):
            original = path.read_bytes()

            def changed(*args, **kwargs):
                result = populate(*args, **kwargs)
                path.write_bytes(original + b'changed')
                return result

            with self.subTest(path=path), mock.patch.object(importer, 'populate', side_effect=changed), self.assertRaisesRegex(ValueError, 'changed during admission'):
                self.execute()
            self.assertFalse(self.output.exists())
            path.write_bytes(original)

    def test_source_snapshot_changed_during_admission_is_rejected(self):
        snapshot = importer.snapshot
        calls = 0

        def changed(root):
            nonlocal calls
            result = snapshot(root)
            calls += 1
            if calls == 2:
                result = dict(result, unreviewed=b'changed after replay')
            return result

        with mock.patch.object(importer, 'snapshot', side_effect=changed), self.assertRaisesRegex(ValueError, 'changed during admission'):
            self.execute()
        self.assertFalse(self.output.exists())

    def test_cli_admit_verify_and_actionable_error(self):
        options = ['--parent', str(self.parent_path), '--review', str(self.review_path),
            '--reference-root', str(self.root), '--output', str(self.output),
            '--expected-review-sha256', runtime.digest(self.review_path.read_bytes())]
        with mock.patch.object(importer, 'now', side_effect=[STARTED, ADMITTED]), mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(cli.main(['admit', *options]), 0)
            self.assertEqual(json.loads(out.getvalue())['new_capability_statements'], 3)
        with mock.patch.object(importer, 'now', return_value=LATER), mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(cli.main(['verify', *options, '--expected-output-sha256',
                                      runtime.digest(self.output.read_bytes())]), 0)
            self.assertTrue(json.loads(out.getvalue())['verified'])
        with mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            self.assertEqual(cli.main(['admit', *options[:-1], '0' * 64]), 1)
            self.assertIn('review hash mismatch', err.getvalue())


if __name__ == '__main__':
    unittest.main()
