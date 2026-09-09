"""Synthetic, offline tests of the exact Bosch capability admission contract."""

import copy
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from semiconductor_atlas import alerts, curated_capture as capture, database, models, repository, service
from semiconductor_atlas import bosch_sample_production as importer
from semiconductor_atlas.evidence_changes import evidence_changes
from semiconductor_atlas.evidence_dossier import build_evidence_dossier
from scripts import admit_bosch_sample_production as cli
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
        'Synthetic funding context for Robert Bosch Semiconductor LLC.',
        'Synthetic Roseville, CA facility: Sample production has already commenced with commercial production expected to begin in 2026.'
        if not omit_expansion else 'Sample production context omitted.',
        'Synthetic SiC semiconductor context, not an independent physical observation.',
        'Bosch produced more than 60 million SiC chips worldwide since the first generation went into production in 2021.',
        'Synthetic wide bandgap technology context.',
        'Synthetic PDF link context, not permission to fetch another resource.',
    ]
    parts = [
        ('title', 'h1', ' class="nist-page__title"', title or importer.SOURCE['title']),
        ('publication', 'time', ' datetime="' + (date_time or '2026-07-13T12:00:00Z') + '"', 'July 13, 2026'),
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


class BoschSampleProductionTests(unittest.TestCase):
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
        self.fixture_codes = {'semiconductor_atlas/bosch_sample_production.py': 'b' * 64}
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
        temporary = tempfile.TemporaryDirectory(prefix='atlas-bosch-test-')
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
        common = dict(company='Bosch', country_code='US', source_family=importer.FAMILY,
                      scope='Synthetic exact source fixture only, not manufacturing evidence')
        self.bodies = {
            'robots': b'User-agent: *\nDisallow: /private/\n',
            'rights': b'<p>Synthetic rights review: local informational use only.</p>',
            'bosch': b'<h1>Synthetic Bosch context</h1>',
            self.source['id']: self.body,
            'powerex': b'<h1>Synthetic Powerex context</h1>',
        }
        self.plan = dict(format=capture.PLAN_FORMAT, plan_id='bosch-synthetic',
            review_scope_id='bosch-synthetic', checked_facility_key=importer.SUBJECT['stable_key'],
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
                required_text=['Synthetic'] if identity != self.source['id'] else ['Bosch']))
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
            decision='accept_one_source_reported_sample_production_upper_bound', reviewed_at=REVIEWED,
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

    def test_exact_replay_preserves_parent_rows_and_family(self):
        with mock.patch.object(capture, 'curl_fetch', side_effect=AssertionError('network forbidden')):
            result = self.execute()
            replay = self.execute(verify=True)
        self.assertTrue(replay['verified'])
        self.assertEqual(result['database_sha256'], replay['database_sha256'])
        self.assertEqual((result['new_milestones'], result['new_capability_statements'],
                          result['new_numeric_capacity_claims']), (1, 0, 0))
        self.assertEqual(self.parent, self.parent_path.read_bytes())
        old = importer.open_image(self.parent)
        new = importer.open_image(self.output.read_bytes(), version=11)
        self.addCleanup(old.close)
        self.addCleanup(new.close)
        expected = dict(schema_migrations=1, sources=1, source_documents=1, ingestion_runs=1,
            ingestion_run_documents=2,
            entities=1, source_records=1, claim_series=1, claim_versions=1, claim_values=1,
            milestone_values=1, claim_evidence=2)
        for table, in old.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            columns = [r[1] for r in old.execute(f'PRAGMA table_info("{table}")')]
            projection = ','.join('"' + c + '"' for c in columns)
            before = set(map(tuple, old.execute(f'SELECT {projection} FROM "{table}"')))
            after = set(map(tuple, new.execute(f'SELECT {projection} FROM "{table}"')))
            self.assertLessEqual(before, after, table)
            self.assertEqual(len(after) - len(before), expected.get(table, 0), table)
        self.assertEqual(new.execute('SELECT applied_at FROM schema_migrations WHERE version=11').fetchone()[0], ADMITTED)
        self.assertEqual([], repository.validate_database(new))
        self.assertEqual([], service.validate_semantics(new))
        self.assertEqual(alerts.detect_revision_alerts(old), alerts.detect_revision_alerts(new))
        self.assertEqual(new.execute('SELECT family_id FROM sources WHERE stable_key=?',
                                    (importer.SOURCE_KEY,)).fetchone()[0], 'nist-family')

    def test_one_bounded_source_milestone_no_physical_inference(self):
        result = self.execute()
        c = importer.open_image(self.output.read_bytes(), version=11)
        self.addCleanup(c.close)
        claims = service.source_claim_records(c, recorded_at=ADMITTED, subject_entity_id=result['subject_id'])
        self.assertEqual(len(claims), 1)
        expected = {k: v for k, v in importer.rows()[0].items() if k != 'key'}
        self.assertEqual(claims[0]['value'], expected)
        self.assertEqual(claims[0]['claim_kind'], 'source_statement')
        self.assertEqual(claims[0]['value_kind'], 'milestone')
        for field in ('valid_from', 'valid_to', 'confidence'):
            self.assertIsNone(claims[0][field])
        for day in ('1900-01-01', '2026-07-13', '2099-01-01'):
            self.assertEqual([], service.claim_records(c, as_of=day, recorded_at=LATER,
                                                       subject_entity_id=result['subject_id']))
        self.assertEqual(c.execute('SELECT COUNT(*) FROM source_entity_assignments').fetchone()[0], 0)

    def test_dossier_and_feed_have_actual_admission_clock_and_honest_prose(self):
        from semiconductor_atlas.evidence_dossier import markdown_bytes
        result = self.execute()
        c = importer.open_image(self.output.read_bytes(), version=11)
        self.addCleanup(c.close)
        for cutoff in (self.source['retrieved_at'], REVIEWED, BEFORE):
            with self.assertRaisesRegex(ValueError, 'entity is unavailable'):
                build_evidence_dossier(c, subject_entity_id=result['subject_id'],
                                       as_of='2026-07-13', recorded_at=cutoff)
        dossier = build_evidence_dossier(c, subject_entity_id=result['subject_id'],
                                       as_of='2026-07-13', recorded_at=ADMITTED)
        self.assertEqual(dossier['world_current_claims'], [])
        self.assertEqual(len(dossier['source_statements']), 1)
        self.assertEqual(dossier['evidence_summary']['source_family_count'], 1)
        self.assertEqual(dossier['evidence_summary']['independent_corroboration'], 'not_assessed')
        self.assertIn(b'occurred on or before 2026\\-07\\-13', markdown_bytes(dossier))
        self.assertIn(b'lower bound and exact date unknown', markdown_bytes(dossier))
        self.assertNotIn(b'None to', markdown_bytes(dossier))
        for cutoff, count in ((BEFORE, 0), (ADMITTED, 1)):
            feed = evidence_changes(c, since=STARTED, recorded_at=cutoff,
                as_of='2026-07-13', subject_entity_id=result['subject_id'])
            self.assertEqual(feed['total_events'], count)
            if count:
                self.assertEqual(feed['events'][0]['claim']['value']['date_relation'], 'on_or_before')

    def test_occurrence_and_publication_anchor_are_separate_evidence_links(self):
        result = self.execute()
        c = importer.open_image(self.output.read_bytes(), version=11)
        self.addCleanup(c.close)
        evidence = c.execute('SELECT role,locator,excerpt FROM claim_evidence WHERE claim_version_id=?',
                             (result['claims'][0]['claim_id'],)).fetchall()
        self.assertEqual({r['role'] for r in evidence}, {'support', 'context'})
        self.assertTrue(all(r['excerpt'] is None for r in evidence))
        for key in ('narrative', 'publication'):
            self.assertTrue(any(self.spans[key]['sha256'] in r['locator'] for r in evidence))

    def test_exact_review_rejects_extra_inference(self):
        mutations = [lambda r: r['rows'][0].update(date_low='2026-01-01'),
            lambda r: r['rows'][0].update(date_high='2026-07-14'),
            lambda r: r['rows'][0].update(milestone_type='first_200mm_chips'),
            lambda r: r['rows'][0].update(date_relation='exact'),
            lambda r: r['rows'].append(copy.deepcopy(r['rows'][0])),
            lambda r: r['subject'].update(canonical_entity_id='epa-site'),
            lambda r: r.update(source_family='new-independent-NIST'),
            lambda r: r.update(decision='approve_acquisition')]
        for mutate in mutations:
            review = copy.deepcopy(self.review)
            mutate(review)
            with self.subTest(review=review), self.assertRaises(ValueError): self.validate(review)
        for key in importer.BOUNDARIES:
            for value in (True, 0):
                review = copy.deepcopy(self.review)
                review['boundaries'][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.validate(review)

    def test_complete_visible_evidence_and_publication_anchor_required(self):
        for change in (dict(hidden=' hidden'), dict(hidden=' aria-hidden="true"'),
                dict(ancestor='<section hidden>'), dict(omit_expansion=True),
                dict(paragraph_count=5), dict(title='Wrong article'), dict(date_time='2026-07-14T12:00:00Z')):
            self.build_fixture(**change)
            with self.subTest(change=change), self.assertRaises(ValueError): self.evidence()

    def test_source_packet_tamper_or_unlisted_files_fail_closed(self):
        self.put(importer.PACKET + '/unlisted.txt', b'unlisted')
        with self.assertRaisesRegex(ValueError, 'unlisted or missing'): self.execute()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.parent, self.parent_path.read_bytes())

    def test_parent_hash_drift_and_code_drift_fail_without_output(self):
        self.parent_path.write_bytes(self.parent + b'drift')
        with self.assertRaisesRegex(ValueError, 'parent database hash'): self.execute()
        self.assertFalse(self.output.exists())
        self.parent_path.write_bytes(self.parent)
        self.fixture_codes['new.py'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'implementation changed'): self.execute()
        self.assertFalse(self.output.exists())

    def test_missing_existing_family_fails_without_publishing(self):
        c = importer.open_image(self.parent)
        self.addCleanup(c.close)
        trigger = c.execute("SELECT sql FROM sqlite_master WHERE name='source_families_immutable_delete'").fetchone()[0]
        c.execute('DROP TRIGGER source_families_immutable_delete')
        c.execute("DELETE FROM source_families WHERE id='nist-family'")
        c.execute(trigger)
        c.commit()
        raw = c.serialize()
        c.close()
        self.parent_path.write_bytes(raw)
        self.review['parent_database_sha256'] = runtime.digest(raw)
        self.write_review()
        with self.assertRaisesRegex(ValueError, 'existing NIST source family'): self.execute()
        self.assertFalse(self.output.exists())
        self.assertEqual(raw, self.parent_path.read_bytes())

    def test_output_never_overwrites_or_enters_capture_packet(self):
        for name in (importer.PACKET + '/successor.sqlite', importer.PACKET.upper() + '/successor.sqlite'):
            target = self.root / name
            if not target.parent.exists():
                continue
            self.assertTrue(os.path.samefile(target.parent, self.root / importer.PACKET))
            with self.assertRaisesRegex(ValueError, 'outside the retained acquisition packet'):
                self.execute(output=target)
            self.assertFalse(target.exists())
        self.output.write_bytes(b'user-owned')
        with self.assertRaisesRegex(ValueError, 'must not exist'): self.execute()
        self.assertEqual(self.output.read_bytes(), b'user-owned')

    def test_replay_detects_successor_and_code_tampering(self):
        self.execute()
        with self.assertRaisesRegex(ValueError, 'trusted successor hash'):
            self.execute(verify=True, expected_output_sha256='0' * 64)
        c = importer.open_image(self.output.read_bytes(), version=11)
        c.execute("UPDATE schema_migrations SET applied_at='2026-09-09T05:01:00Z' WHERE version=11")
        c.commit()
        self.output.write_bytes(c.serialize())
        c.close()
        with self.assertRaisesRegex(ValueError, 'full parent-plus-migration'): self.execute(verify=True)

    def test_capture_review_does_not_become_claim_acceptance(self):
        payloads = importer.snapshot(self.root)
        changed = copy.deepcopy(self.access)
        changed['boundaries']['source_claim_acceptance'] = True
        payloads[importer.ACCESS_REVIEW] = payloads[importer.PACKET + '/review.json'] = canonical(changed)
        with self.assertRaisesRegex(ValueError, 'separate from core acceptance'):
            importer.load_evidence(self.validate(), payloads)


if __name__ == '__main__':
    unittest.main()
