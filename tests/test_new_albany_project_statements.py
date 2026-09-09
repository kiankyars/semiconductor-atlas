"""Synthetic acquisition packets exercise municipal statement admission offline."""

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
from semiconductor_atlas import new_albany_project_statements as importer
from scripts import admit_new_albany_project_statements as cli
from scripts import frozen_runtime as runtime
from tests.test_ase_k18b_milestones import parent_fixture


REVIEWED = '2026-09-08T23:00:00Z'
STARTED = '2026-09-08T23:01:00Z'
ADMITTED = '2026-09-08T23:01:01Z'
LATER = '2026-09-08T23:02:00Z'
TIMELINE = 'new-albany-project-timeline'
INDEX = 'new-albany-construction-updates'


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def html_fixture(identity, *, hidden='', link=None, omit=None, prefix=b''):
    if identity == TIMELINE:
        paragraphs = [
            'Intel is adjusting the timeline for its two Ohio chip manufacturing plants. '
            'The City response is attributed to Mayor Sloan Spalding.',
            'Intel reaffirmed its commitment and decided to slow the construction pace '
            'in response to demand. This is a synthetic source fixture.',
            'The City supports the Ohio One campus in New Albany. '
            'Commitment does not establish a production date or capacity.',
        ]
    else:
        paragraphs = [
            'Intel announced in July 2025 that it was slowing construction for its '
            'two chip manufacturing plants and reiterated its full commitment.',
            '<a href="' + (link or importer.DOCUMENTS[TIMELINE]['url']) + '">READ MORE</a>',
        ]
    if omit is not None:
        paragraphs.pop(omit)
    fragment = ('<div class="text"' + hidden + '><h2 class="header">Project Timeline Update</h2>'
                + ''.join('<p>' + p + '</p>' for p in paragraphs) + '</div>').encode()
    before = b'<html><body>' + prefix
    body = before + fragment + b'<p>Illustration February 2025; copyright 2024. Synthetic.</p></body></html>'
    span = {'start': len(before), 'end': len(before) + len(fragment),
            'sha256': runtime.digest(fragment), 'tag': 'div'}
    return body, span


class NewAlbanyProjectStatementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='atlas-city-statements-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        connection = sqlite3.connect(':memory:')
        try:
            connection.row_factory = sqlite3.Row
            connection.deserialize(parent_fixture())
            database.apply_migrations(connection, target_version=9)
            self.parent = connection.serialize()
        finally:
            connection.close()
        self.parent_path = self.root / 'parent.sqlite'
        self.parent_path.write_bytes(self.parent)
        self.output = self.root / 'successor.sqlite'
        self.review_path = self.root / 'review.json'
        self.fixture_codes = {'semiconductor_atlas/new_albany_project_statements.py': 'b' * 64}
        self.patch(importer, 'codes', lambda: self.fixture_codes.copy())
        self.patch(importer, '_LOADED_CODES', self.fixture_codes.copy())
        self.documents = copy.deepcopy(importer.DOCUMENTS)
        self.patch(importer, 'DOCUMENTS', self.documents)
        self.build_fixture()

    def patch(self, module, name, value):
        patched = mock.patch.object(module, name, value)
        patched.start()
        self.addCleanup(patched.stop)

    def put(self, name, value):
        raw = value if isinstance(value, bytes) else canonical(value)
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return {'bytes': len(raw), 'sha256': runtime.digest(raw)}

    def inventory(self, directory):
        return {p.relative_to(self.root).as_posix(): {'sha256': runtime.digest(p.read_bytes()),
                'bytes': p.stat().st_size} for p in (self.root / directory).rglob('*') if p.is_file()}

    def manifest(self, packet):
        files = self.inventory(packet)
        return self.put(packet + '/manifest.json', {'format': 'semiconductor-atlas-policy-probe-manifest-v1', 'files': {
            name[len(packet) + 1:]: item for name, item in files.items()
            if name != packet + '/manifest.json'}})

    def probe(self, name, entries, minute):
        packet, plan_path = 'artifacts/' + name, 'review_plans/' + name + '.json'
        plan = {'format': 'semiconductor-atlas-bounded-policy-review-v1', 'entries': copy.deepcopy(entries),
                'minimum_interval_seconds': 1 if name == 'robots' else 10,
                'claim_acceptance': False, 'redirects': False, 'retries': False, 'cookies': False}
        if name == 'rights':
            plan['robots_capture'] = {'path': 'artifacts/robots',
                'manifest_sha256': runtime.digest((self.root / 'artifacts/robots/manifest.json').read_bytes())}
        if self.probe_plan_mutation:
            self.probe_plan_mutation(name, plan)
        self.put(plan_path, plan)
        self.put(packet + '/probe.json', plan)
        attempts = []
        for index, entry in enumerate(entries):
            body = self.bodies[entry['id']]
            path = 'responses/' + entry['id'] + '.body'
            self.put(packet + '/' + path, body)
            started = f'2026-09-08T22:{minute:02}:{index * 20:02}Z'
            finished = f'2026-09-08T22:{minute:02}:{index * 20 + 1:02}Z'
            attempts.append({**{k: entry[k] for k in ('id', 'url', 'purpose', 'company',
                'country_code', 'source_family', 'scope')}, 'final_url': entry['url'], 'path': path,
                'started_at': started, 'finished_at': finished, 'http_status': 200,
                'curl_exit_code': 0, 'error': None, 'tls_verification_result': 0,
                'outcome': 'succeeded', 'bytes': len(body), 'sha256': runtime.digest(body),
                'negative_evidence_eligible': False, 'redirect_count': 0})
            self.put(packet + '/responses/' + entry['id'] + '.attempt.json', attempts[-1])
            self.put(packet + '/responses/' + entry['id'] + '.transport.json', {
                'url_effective': entry['url'], 'http_code': 200, 'curl_exit_code': 0,
                'ssl_verify_result': 0, 'num_redirects': 0})
        if self.probe_attempt_mutation:
            self.probe_attempt_mutation(name, attempts)
        self.put(packet + '/source_checks.json', {
            'format': 'semiconductor-atlas-publisher-source-checks-v1',
            'review_scope_id': 'city-fixture', 'checked_source_id': 'city-fixture',
            'clock_precision': 'second', 'absence_inference_allowed': False, 'attempts': attempts})
        return {'path': packet, 'manifest_sha256': self.manifest(packet)['sha256'],
                'plan_path': plan_path, 'kind': 'robots_probe' if name == 'robots' else 'rights_probe'}

    def build_fixture(self, *, html_changes=None, access_changes=None,
                      probe_plan_mutation=None, probe_attempt_mutation=None, reversed_probe_clocks=False):
        html_changes = html_changes or {}
        self.probe_plan_mutation = probe_plan_mutation
        self.probe_attempt_mutation = probe_attempt_mutation
        self.bodies = {'robots': b'User-agent: *\nDisallow: /private/\nCrawl-delay: 10\n',
                       'rights': b'<html><body><p>City of New Albany copyright. Synthetic local review.</p></body></html>',
                       'main-robots': b'User-agent: *\nDisallow: /private/\nCrawl-delay: 10\n',
                       'privacy': b'<html><body><p>Synthetic privacy context, not an open license.</p></body></html>'}
        self.spans = {}
        for identity in self.documents:
            body, self.spans[identity] = html_fixture(identity, **html_changes.get(identity, {}))
            self.bodies[identity] = body
            self.documents[identity]['sha256'] = runtime.digest(body)
        common = {'company': 'Intel', 'country_code': 'US', 'source_family': importer.FAMILY,
                  'scope': 'Exact synthetic source URL; not real manufacturing evidence'}
        entries = []
        for identity, url, purpose in (
            ('robots', importer.ORIGIN + 'robots.txt', 'access_policy'),
            ('main-robots', 'https://newalbanyohio.org/robots.txt', 'access_policy'),
            ('rights', importer.ORIGIN, 'rights_policy'),
            ('privacy', 'https://newalbanyohio.org/privacy-policy/', 'rights_policy')):
            entries.append({**common, 'id': identity, 'url': url, 'purpose': purpose})
        packets = [self.probe('robots', entries[:2], 10 if reversed_probe_clocks else 0),
                   self.probe('rights', entries[2:], 0 if reversed_probe_clocks else 10)]
        self.access_name = 'review_plans/access.json'
        self.access = {
            'decision': 'approve_two_exact_city_pages_for_local_source_text_review_only',
            'reviewed_at': '2026-09-08T22:20:00Z',
            'rights': {'license_status': 'unknown_no_open_license_identified',
                'copyright_holder': 'City of New Albany', 'raw_redistribution': False,
                'model_training': False, 'third_party_resources': False, 'commercial_reuse_approved': False},
            'acquisition_boundary': dict.fromkeys(('redirects', 'retries', 'cookies', 'discovery',
                'claim_acceptance', 'delivery_eligible', 'scheduled_execution'), False),
            'policy_captures': [{'path': p['path'], 'manifest_sha256': p['manifest_sha256'],
                                 'review': 'Complete synthetic policy body reviewed.'} for p in packets],
            'bound_policies': [{'url': entry['url'], 'body_sha256': runtime.digest(self.bodies[entry['id']]),
                'normalization': normalization, 'normalized_sha256': runtime.digest(capture.normalized_bytes(
                    self.bodies[entry['id']], normalization))}
                for entry, normalization in ((entries[0], 'robots_text_v1'), (entries[2], 'html_policy_v1'))],
        }
        if access_changes:
            access_changes(self.access)
        access_identity = self.put(self.access_name, self.access)
        self.plan_name = 'review_plans/capture.json'
        self.plan = {'format': capture.PLAN_FORMAT, 'plan_id': 'city-fixture',
            'review_scope_id': 'city-fixture', 'checked_facility_key': 'city:ohio-one',
            'reviewed_at': self.access['reviewed_at'], 'expires_at': '2026-09-09T22:20:00Z',
            'decision': 'approved_exact_urls', 'user_agent': 'SemiconductorAtlas/0.1',
            'minimum_interval_seconds': 10, 'timeout_seconds': 2, 'max_response_bytes': 10000,
            'notes': 'Synthetic offline fixture only; not evidence or permission.',
            'review_record': {'path': self.access_name, 'sha256': access_identity['sha256']},
            'policies': [], 'documents': []}
        for entry in (entries[0], entries[2]):
            normalization = 'robots_text_v1' if entry['purpose'] == 'access_policy' else 'html_policy_v1'
            self.plan['policies'].append({**entry, 'normalization': normalization,
                'media_types': ['text/plain' if entry['purpose'] == 'access_policy' else 'text/html'],
                'normalized_sha256': runtime.digest(capture.normalized_bytes(self.bodies[entry['id']], normalization))})
        for identity, document in self.documents.items():
            self.plan['documents'].append({**common, 'id': identity, 'url': document['url'],
                'purpose': 'source_document', 'media_types': ['text/html'],
                'policy_ids': ['robots', 'rights'], 'required_text': ['Project Timeline Update']})
        self.put(self.plan_name, self.plan)
        self.capture_path = 'artifacts/capture'
        self.capture_calls = []
        tick = datetime(2026, 9, 8, 22, 30, tzinfo=timezone.utc)

        def clock():
            nonlocal tick
            value = tick.isoformat().replace('+00:00', 'Z')
            tick += timedelta(seconds=11)
            return value

        def transport(entry, destination, plan):
            self.capture_calls.append(entry['id'])
            destination.write_bytes(self.bodies[entry['id']])
            return {'http_code': 200, 'curl_exit_code': 0, 'content_type': entry['media_types'][0],
                    'url_effective': entry['url'], 'num_redirects': 0, 'ssl_verify_result': 0}

        with mock.patch.object(capture, '_now', side_effect=clock), mock.patch.object(capture.time, 'sleep'):
            capture.capture_sources(self.root / self.plan_name, self.root / self.capture_path,
                                    review_root=self.root, transport=transport)
        manifest = self.root / self.capture_path / 'manifest.json'
        packets.append({'path': self.capture_path, 'manifest_sha256': runtime.digest(manifest.read_bytes()),
                        'plan_path': self.plan_name, 'kind': 'curated_capture'})
        ledger = json.loads((self.root / self.capture_path / 'source_checks.json').read_bytes())
        attempts = {a['id']: a for a in ledger['attempts']}
        self.review = {'format': importer.FORMAT, 'decision': importer.DECISION,
            'reviewed_at': REVIEWED, 'reviewer': 'Synthetic exposed reviewer',
            'prior_exposure': 'All synthetic fixture assertions already exposed.',
            'interpretation': 'Complete municipal paragraphs; no physical or predictive inference.',
            'parent_database_sha256': runtime.digest(self.parent),
            'project': copy.deepcopy(importer.PROJECT), 'documents': {}, 'rows': importer.rows(),
            'packets': packets, 'access_review': self.plan['review_record'],
            'input_files': {**self.inventory('artifacts'), **self.inventory('review_plans')},
            'boundaries': copy.deepcopy(importer.BOUNDARIES)}
        for identity, document in self.documents.items():
            self.review['documents'][identity] = {**document, 'bytes': len(self.bodies[identity]),
                'published_at': None, 'retrieved_at': attempts[identity]['finished_at'],
                'body_path': self.capture_path + '/' + attempts[identity]['path'],
                'narrative': self.spans[identity]}
        self.write_review()

    def write_review(self):
        self.review_path.write_bytes(canonical(self.review))

    def validate(self, review=None):
        raw = canonical(self.review if review is None else review)
        return importer.validate_review(raw, runtime.digest(raw))

    def evidence(self):
        review = self.validate()
        return importer.load_evidence(review, importer.snapshot(review, self.root), self.root)

    def execute(self, verify=False, **changes):
        options = dict(parent=self.parent_path, review_path=self.review_path, reference_root=self.root,
            expected_review_sha256=runtime.digest(self.review_path.read_bytes()), output=self.output,
            verify_only=verify, expected_output_sha256=runtime.digest(self.output.read_bytes()) if verify else None)
        options.update(changes)
        with mock.patch.object(importer, 'now', side_effect=[LATER] if verify else [STARTED, ADMITTED]):
            return importer.admit(**options)

    def test_three_source_statements_exact_replay_and_all_parent_rows(self):
        result = self.execute()
        self.assertEqual(self.capture_calls, ['robots', 'rights', TIMELINE, INDEX])
        self.assertEqual((result['new_source_statements'], result['new_milestones'],
                          result['new_numeric_capacity_claims']), (3, 0, 0))
        self.assertEqual(result['admitted_at'], ADMITTED)
        old, new = importer.open_image(self.parent), importer.open_image(self.output.read_bytes())
        try:
            for table, in old.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                self.assertLessEqual(set(map(tuple, old.execute(f'SELECT * FROM "{table}"'))),
                                     set(map(tuple, new.execute(f'SELECT * FROM "{table}"'))), table)
            rows = service.source_claim_records(new, recorded_at=ADMITTED, subject_entity_id=result['project_id'])
            self.assertEqual({r['predicate']: r['value']['value'] for r in rows},
                             {r['predicate']: r['value'] for r in importer.rows()})
            for row in rows:
                self.assertEqual(row['claim_kind'], 'source_statement')
                self.assertEqual(row['value']['scalar_type'], 'text')
                self.assertIsNone(row['value']['unit'])
                self.assertIsNone(row['valid_from'])
                self.assertIsNone(row['valid_to'])
                self.assertIsNone(row['confidence'])
                self.assertEqual(row['recorded_at'], ADMITTED)
            self.assertEqual(len(rows), 3)
            self.assertEqual(new.execute('SELECT kind FROM entities WHERE id=?', (result['project_id'],)).fetchone()[0], 'project')
            self.assertEqual(repository.validate_database(new), [])
            self.assertEqual(service.validate_semantics(new), [])
        finally:
            old.close(); new.close()
        before = self.output.read_bytes()
        replay = self.execute(verify=True)
        self.assertTrue(replay['verified'])
        self.assertEqual(before, self.output.read_bytes())
        self.assertEqual(self.parent, self.parent_path.read_bytes())

    def test_two_documents_five_matched_links_and_index_only_month(self):
        result = self.execute()
        connection = importer.open_image(self.output.read_bytes())
        try:
            rows = connection.execute('''SELECT series.predicate, documents.document_url,
                evidence.source_document_id, records.source_document_id, evidence.excerpt,
                evidence.locator, documents.published_at, records.observed_at, documents.retrieved_at
                FROM claim_evidence evidence JOIN claim_versions versions ON versions.id=evidence.claim_version_id
                JOIN claim_series series ON series.id=versions.series_id
                JOIN source_documents documents ON documents.id=evidence.source_document_id
                JOIN source_records records ON records.id=evidence.source_record_id
                WHERE series.subject_entity_id=?''', (result['project_id'],)).fetchall()
            self.assertEqual(len(rows), 5)
            for row in rows:
                self.assertEqual(row[2], row[3])
                self.assertIsNone(row[4])
                self.assertIn('UTF-8 bytes [', row[5])
                self.assertIsNone(row[6])
                self.assertEqual(row[7], row[8])
            month = [r for r in rows if r[0].endswith('announcement_month')]
            self.assertEqual([r[1] for r in month], [self.documents[INDEX]['url']])
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM source_records WHERE ingestion_run_id=?', (result['run_id'],)).fetchone()[0], 2)
        finally:
            connection.close()

    def test_knowledge_cutoff_is_actual_admission_and_physical_view_unchanged(self):
        result = self.execute()
        old, new = importer.open_image(self.parent), importer.open_image(self.output.read_bytes())
        try:
            for as_of in ('2025-07-01', '2026-09-08', '2099-01-01'):
                self.assertEqual(service.claim_records(old, as_of=as_of, recorded_at=LATER),
                                 service.claim_records(new, as_of=as_of, recorded_at=LATER))
            for cutoff in ('2025-07-31T23:59:59Z', STARTED, '2026-09-08T23:01:00.999999Z'):
                self.assertEqual(service.source_claim_records(new, recorded_at=cutoff,
                    subject_entity_id=result['project_id']), [])
            self.assertEqual(alerts.detect_revision_alerts(old), alerts.detect_revision_alerts(new))
            history = service.claim_history_records(new, as_of='2024-01-01', recorded_at=LATER)
            self.assertEqual(len([r for r in history if r['subject_entity_id'] == result['project_id']]), 3)
        finally:
            old.close(); new.close()

    def test_review_hash_and_complete_selection_are_authoritative(self):
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            importer.validate_review(canonical(self.review), '0' * 64)
        changes = [lambda r: r['rows'].pop(), lambda r: r['rows'].append(r['rows'][0]),
                   lambda r: r['rows'][0].update(value='cancelled'),
                   lambda r: r['rows'][0].update(value='paused'),
                   lambda r: r['rows'][2].update(value='2025-07-01'),
                   lambda r: r['rows'][2].update(support=[TIMELINE]),
                   lambda r: r['project'].update(canonical_entity_id='nist-project'),
                   lambda r: r['documents'][INDEX].update(published_at='2025-07-01'),
                   lambda r: r['documents'].pop(TIMELINE)]
        for change in changes:
            review = copy.deepcopy(self.review)
            change(review)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(review)

    def test_every_inference_boundary_rejects_true_and_numeric_false(self):
        for boundary in self.review['boundaries']:
            for value in (True, 0):
                review = copy.deepcopy(self.review)
                review['boundaries'][boundary] = value
                with self.subTest(boundary=boundary, value=value), self.assertRaises(ValueError):
                    self.validate(review)

    def test_span_hash_bounds_and_partial_div_rejected(self):
        original = copy.deepcopy(self.review)
        for changes in ({'sha256': '0' * 64}, {'start': True}, {'end': 1}, {'tag': 'p'},
                        {'start': 0, 'end': len(self.bodies[TIMELINE]),
                         'sha256': runtime.digest(self.bodies[TIMELINE])}):
            self.review = copy.deepcopy(original)
            self.review['documents'][TIMELINE]['narrative'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.evidence()

    def test_source_bytes_mutated_without_resealing_rejected(self):
        source = self.root / self.review['documents'][TIMELINE]['body_path']
        source.write_bytes(source.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'retained evidence differs'):
            self.execute()
        self.assertFalse(self.output.exists())

    def test_missing_unmanaged_and_extra_graph_members_rejected(self):
        self.put(self.capture_path + '/unmanaged.txt', b'unmanaged')
        with self.assertRaisesRegex(ValueError, 'inventory differs'):
            self.evidence()
        (self.root / self.capture_path / 'unmanaged.txt').unlink()
        extra = 'review_plans/unrelated.json'
        self.review['input_files'][extra] = self.put(extra, {'unrelated': True})
        with self.assertRaisesRegex(ValueError, 'unrelated evidence'):
            self.evidence()

    def test_evidence_file_and_packet_directory_symlinks_rejected(self):
        body = self.root / self.review['documents'][TIMELINE]['body_path']
        real = self.root / 'retained-body'
        body.rename(real)
        body.symlink_to(real)
        with self.assertRaises((ValueError, OSError)):
            self.execute()
        body.unlink(); real.rename(body)
        packet = self.root / self.capture_path
        alternate = self.root / 'real-capture'
        packet.rename(alternate)
        packet.symlink_to(alternate, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.execute()
        self.assertFalse(self.output.exists())

    def test_relative_graph_paths_must_be_bounded(self):
        for value in ('../escape', '/tmp/escape', 'artifacts/../escape', 'artifacts\\escape', 'other/file'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                importer.relative(value)

    def test_retrieval_and_review_clocks_cannot_be_fabricated(self):
        original = copy.deepcopy(self.review)
        for change in (lambda r: r['documents'][INDEX].update(retrieved_at='2025-07-01T00:00:00Z'),
                       lambda r: r.update(reviewed_at='2026-09-08T22:20:00Z'),
                       lambda r: r.update(reviewed_at=LATER)):
            self.review = copy.deepcopy(original)
            change(self.review)
            self.write_review()
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.execute()
            self.assertFalse(self.output.exists())

    def test_admission_requires_strictly_increasing_actual_clocks(self):
        for times in ([ADMITTED, STARTED], [STARTED, STARTED]):
            with self.subTest(times=times), mock.patch.object(importer, 'now', side_effect=times), self.assertRaises(ValueError):
                importer.admit(parent=self.parent_path, review_path=self.review_path, reference_root=self.root,
                    expected_review_sha256=runtime.digest(self.review_path.read_bytes()), output=self.output)

    def test_output_cannot_overwrite_parent_existing_file_or_symlink(self):
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

    def test_parent_hash_and_schema_must_match(self):
        self.parent_path.write_bytes(self.parent + b'changed')
        with self.assertRaisesRegex(ValueError, 'parent database hash mismatch'):
            self.execute()
        connection, _ = database.initialize(':memory:', target_version=8)
        try:
            wrong = connection.serialize()
        finally:
            connection.close()
        self.parent_path.write_bytes(wrong)
        self.review['parent_database_sha256'] = runtime.digest(wrong)
        self.write_review()
        with self.assertRaisesRegex(ValueError, 'schema-9'):
            self.execute()

    def test_duplicate_source_project_admission_rejected(self):
        self.execute()
        self.review['parent_database_sha256'] = runtime.digest(self.output.read_bytes())
        self.write_review()
        with self.assertRaisesRegex(ValueError, 'already admitted'):
            self.execute(parent=self.output, output=self.root / 'duplicate.sqlite')
        self.assertFalse((self.root / 'duplicate.sqlite').exists())

    def test_verification_hash_and_full_parent_plus_claims_reconstruction(self):
        self.execute()
        with self.assertRaisesRegex(ValueError, 'successor hash mismatch'):
            self.execute(verify=True, expected_output_sha256='0' * 64)
        connection = importer.open_image(self.output.read_bytes())
        try:
            repository.add_entity(connection, models.Entity('unreviewed', models.EntityKind.PROJECT,
                'unreviewed:project', ADMITTED, 'Unreviewed extra entity'))
            connection.commit()
            self.output.write_bytes(connection.serialize())
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, 'full parent-plus-statements'):
            self.execute(verify=True)

    def test_code_drift_and_parent_change_during_admission_rejected(self):
        with mock.patch.object(importer, 'codes', return_value={}), self.assertRaisesRegex(ValueError, 'implementation changed'):
            self.execute()
        populate = importer.populate

        def mutate_parent(*args, **kwargs):
            result = populate(*args, **kwargs)
            self.parent_path.write_bytes(self.parent + b'changed during admission')
            return result

        with mock.patch.object(importer, 'populate', side_effect=mutate_parent), self.assertRaisesRegex(ValueError, 'parent changed'):
            self.execute()
        self.assertFalse(self.output.exists())

    def test_code_change_during_admission_leaves_no_output(self):
        with mock.patch.object(importer, 'codes', side_effect=[self.fixture_codes, {}]), self.assertRaisesRegex(ValueError, 'implementation changed'):
            self.execute()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.parent, self.parent_path.read_bytes())

    def test_future_successor_admission_cannot_verify_as_past_knowledge(self):
        with mock.patch.object(importer, 'now', side_effect=['2026-09-09T00:00:00Z', '2026-09-09T00:00:01Z']):
            importer.admit(parent=self.parent_path, review_path=self.review_path, reference_root=self.root,
                expected_review_sha256=runtime.digest(self.review_path.read_bytes()), output=self.output)
        with self.assertRaisesRegex(ValueError, 'future successor admission'):
            self.execute(verify=True)

    def test_cli_admit_verify_and_actionable_errors(self):
        options = ['--parent', str(self.parent_path), '--review', str(self.review_path),
                   '--reference-root', str(self.root), '--output', str(self.output),
                   '--expected-review-sha256', runtime.digest(self.review_path.read_bytes())]
        with mock.patch.object(importer, 'now', side_effect=[STARTED, ADMITTED]), mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(cli.main(['admit', *options]), 0)
            self.assertEqual(json.loads(out.getvalue())['new_source_statements'], 3)
        with mock.patch.object(importer, 'now', return_value=LATER), mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(cli.main(['verify', *options, '--expected-output-sha256', runtime.digest(self.output.read_bytes())]), 0)
            self.assertTrue(json.loads(out.getvalue())['verified'])
        with mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            self.assertEqual(cli.main(['admit', *options[:-1], '0' * 64]), 1)
            self.assertIn('review hash mismatch', err.getvalue())


    def fresh_fixture(self, **changes):
        temporary = tempfile.TemporaryDirectory(prefix='atlas-city-mutated-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.parent_path = self.root / 'parent.sqlite'
        self.parent_path.write_bytes(self.parent)
        self.output = self.root / 'successor.sqlite'
        self.review_path = self.root / 'review.json'
        self.build_fixture(**changes)

    def test_hidden_or_collapsed_project_blocks_rejected(self):
        for hidden in (' hidden', ' aria-hidden="true"', ' style="display:none"', ' inert'):
            self.fresh_fixture(html_changes={TIMELINE: {'hidden': hidden}})
            with self.subTest(hidden=hidden), self.assertRaisesRegex(ValueError, 'hidden|visible'):
                self.evidence()

    def test_wrong_index_link_and_omitted_complete_context_rejected(self):
        for changes in ({INDEX: {'link': 'https://example.test/unrelated'}},
                        {TIMELINE: {'omit': 2}}, {INDEX: {'omit': 1}}):
            self.fresh_fixture(html_changes=changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.evidence()

    def test_hidden_ancestor_rejected(self):
        self.fresh_fixture(html_changes={TIMELINE: {'prefix': b'<section hidden>'}})
        with self.assertRaisesRegex(ValueError, 'hidden'):
            self.evidence()

    def test_each_rights_promotion_and_acquisition_expansion_rejected(self):
        for field in ('raw_redistribution', 'model_training', 'third_party_resources', 'commercial_reuse_approved'):
            self.fresh_fixture(access_changes=lambda a: a['rights'].update({field: True}))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'rights'):
                self.evidence()
        for field in ('redirects', 'retries', 'cookies', 'discovery', 'claim_acceptance', 'delivery_eligible', 'scheduled_execution'):
            self.fresh_fixture(access_changes=lambda a: a['acquisition_boundary'].update({field: True}))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'limits'):
                self.evidence()

    def test_bound_original_policy_hash_normalization_and_url_must_match(self):
        for changes in ({'body_sha256': '0' * 64}, {'normalized_sha256': '0' * 64},
                        {'normalization': 'html_visible_text_v1'}, {'url': 'https://example.test/robots.txt'}):
            self.fresh_fixture(access_changes=lambda a: a['bound_policies'][0].update(changes))
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'policy body'):
                self.evidence()

    def test_rights_probe_cannot_predate_same_origin_robots(self):
        self.fresh_fixture(reversed_probe_clocks=True)
        with self.assertRaisesRegex(ValueError, 'cross-packet'):
            self.evidence()

    def test_rights_probe_must_bind_exact_robots_packet(self):
        def mutate(name, plan):
            if name == 'rights':
                plan['robots_capture']['manifest_sha256'] = '0' * 64

        self.fresh_fixture(probe_plan_mutation=mutate)
        with self.assertRaisesRegex(ValueError, 'rights probe must depend'):
            self.evidence()

    def test_probe_plan_cannot_substitute_other_urls_or_purposes(self):
        for key, value in (('url', 'https://example.test/unrelated'), ('purpose', 'source_document')):
            def mutate(name, plan):
                if name == 'rights':
                    plan['entries'][0][key] = value

            self.fresh_fixture(probe_plan_mutation=mutate)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'policy-probe'):
                self.evidence()

    def test_probe_receipt_cannot_differ_from_resealed_ledger(self):
        def mutate(name, attempts):
            if name == 'rights':
                attempts[0]['http_status'] = 201

        self.fresh_fixture(probe_attempt_mutation=mutate)
        with self.assertRaisesRegex(ValueError, 'attempt receipt differs'):
            self.evidence()

    def test_probe_transport_cannot_differ_from_receipt_and_ledger(self):
        def mutate(name, attempts):
            if name == 'rights':
                attempts[0]['http_status'] = 201
                self.put('artifacts/rights/responses/rights.attempt.json', attempts[0])

        self.fresh_fixture(probe_attempt_mutation=mutate)
        with self.assertRaisesRegex(ValueError, 'transport receipt differs'):
            self.evidence()


if __name__ == '__main__':
    unittest.main()
