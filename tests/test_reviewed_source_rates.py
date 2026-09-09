"""Offline admission, complete evidence graph, and scoped HTML recovery checks."""

import copy
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from semiconductor_atlas import database, repository, service, reviewed_source_rates as rates
from semiconductor_atlas import realized_milestones, source_checks
from tests.test_source_rates import rate


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


class HTMLStructureTests(unittest.TestCase):
    def document(self, prefix=b'</a>', inner=b'<p>Evidence</p>'):
        body = b'<html><body><nav>' + prefix + b'</nav><div>' + inner + b'</div></body></html>'
        begin = body.index(inner)
        span = {'start': begin, 'end': begin + len(inner)}
        review = {'mode': 'reviewed_unmatched_anchor_closers_v1',
                  'source_sha256': rates.runtime.digest(body),
                  'ignored_anchor_end_offsets': [body.index(b'</a>')], 'rationale': 'Synthetic stray navigation closer.'}
        return body, span, review

    def test_only_reviewed_unmatched_pre_evidence_anchor_is_ignored(self):
        body, span, review = self.document()
        with self.assertRaises(ValueError):
            realized_milestones._EvidenceHTML(body, span['start'], span['end'])
        parsed = rates.RateEvidenceHTML(body, span['start'], span['end'], review)
        self.assertEqual(parsed.text(span), 'Evidence')
        self.assertEqual(parsed.skipped_offsets, set(review['ignored_anchor_end_offsets']))

    def test_matched_anchor_cannot_be_ignored(self):
        body, span, review = self.document(prefix=b'<a>Link</a>')
        with self.assertRaises(ValueError): rates.RateEvidenceHTML(body, **{'begin': span['start'], 'end': span['end'], 'structure_review': review})

    def test_extra_unreviewed_error_still_fails(self):
        body, span, review = self.document(prefix=b'</a></a>')
        with self.assertRaises(ValueError): rates.RateEvidenceHTML(body, span['start'], span['end'], review)

    def test_hidden_ancestor_still_rejects(self):
        body, span, review = self.document(inner=b'<p hidden>Evidence</p>')
        with self.assertRaises(ValueError): rates.RateEvidenceHTML(body, span['start'], span['end'], review)

    def test_evidence_internal_exception_rejected(self):
        body, span, review = self.document(inner=b'<p>Evidence</a></p>')
        review['ignored_anchor_end_offsets'].append(body.rindex(b'</a>'))
        with self.assertRaises(ValueError): rates.RateEvidenceHTML(body, span['start'], span['end'], review)

    def test_changed_body_and_unvisited_offsets_rejected(self):
        body, span, review = self.document()
        for changes in ({'source_sha256': '0'*64}, {'ignored_anchor_end_offsets': []},
                        {'ignored_anchor_end_offsets': [True]}, {'ignored_anchor_end_offsets': [17, 17]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                rates.RateEvidenceHTML(body, span['start'], span['end'], {**review, **changes})


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.parent = self.root / 'parent.sqlite'
        connection, _ = database.initialize(self.parent, target_version=8)
        connection.close()
        wording = rate().source_wording
        self.body = ('<html><body><h1>Fixture release</h1><span>September 24, 2024</span>'
                     '<p>' + wording + '</p></body></html>').encode()
        self.source = {'publisher': 'Synthetic publisher', 'url': 'https://example.org/release',
            'title': 'Fixture release', 'retained_path': 'artifacts/source/responses/source.body',
            'media_type': 'text/html', 'bytes': len(self.body), 'sha256': rates.runtime.digest(self.body),
            'publication_date': '2024-09-24', 'publication_precision': 'day',
            'publication_literal': 'September 24, 2024', 'publication_basis': 'Complete fixture dateline',
            'retrieval_started_at': '2026-09-08T20:00:00Z', 'retrieval_finished_at': '2026-09-08T20:00:01Z'}
        self.evidence = {}
        for label, tag, text in [('title', 'h1', 'Fixture release'),
                ('publication_dateline', 'span', 'September 24, 2024'), ('production', 'p', wording)]:
            fragment = f'<{tag}>{text}</{tag}>'.encode()
            begin = self.body.index(fragment)
            self.evidence[label] = {'start': begin, 'end': begin + len(fragment), 'tag': tag,
                                    'sha256': rates.runtime.digest(fragment)}
        packets = [self.packet('policy', b'<html><body>Fixture copying policy</body></html>', 'rights_policy'),
                   self.packet('source', self.body, 'source_document')]
        research = {'source': self.source, 'evidence': self.evidence, 'claim_acceptance': False,
                    'collector_enabled': False, 'review_completed_at': '2026-09-08T20:00:02Z'}
        research_name = 'review_plans/research.json'
        self.write(research_name, encoded(research))
        metadata = asdict(rate())
        metadata.pop('claim_version_id')
        self.review = {'format': rates.FORMAT, 'decision': 'accept_one_source_reported_monthly_rate',
            'reviewed_at': '2026-09-08T20:00:03Z', 'reviewed_by': 'Synthetic reviewer',
            'prior_exposure': 'Synthetic fixture, not an independent historical evaluation.',
            'parent_database_sha256': rates.runtime.digest(self.parent.read_bytes()),
            'research_review': {'path': research_name, 'sha256': rates.runtime.digest(encoded(research))},
            'input_files': self.inventory(), 'packets': packets, 'source': self.source,
            'source_family': {'stable_key': 'synthetic-family', 'label': 'Synthetic publisher'},
            'subject': {'kind': 'facility', 'stable_key': 'fixture-facility', 'label': 'Synthetic facility',
                        'scope_basis': 'Complete synthetic source paragraph; no canonical assignment.'},
            'rate': metadata, 'evidence': self.evidence, 'support': 'production',
            'publication_literal': 'September 24, 2024',
            'html_structure_review': {'mode': 'reviewed_unmatched_anchor_closers_v1',
                'source_sha256': self.source['sha256'], 'ignored_anchor_end_offsets': [], 'rationale': 'Strict fixture has no exceptions.'},
            'rights': {'use': 'local_informational_review_only', 'basis': 'Synthetic policy fixture',
                       'raw_redistribution': False, 'model_training': False}, 'boundaries': rates.BOUNDARIES}
        self.review_path = self.root / 'review.json'
        self.review_path.write_bytes(encoded(self.review))
        self.sha = rates.runtime.digest(encoded(self.review))

    def write(self, name, raw):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)

    def inventory(self):
        return {p.relative_to(self.root).as_posix(): {'bytes': len(p.read_bytes()), 'sha256': rates.runtime.digest(p.read_bytes())}
                for directory in ('artifacts', 'review_plans') for p in (self.root / directory).rglob('*') if p.is_file()}

    def packet(self, name, body, purpose):
        root = 'artifacts/' + name
        entry = {'id': name, 'url': 'https://example.org/' + name if name == 'policy' else self.source['url'],
                 'purpose': purpose, 'source_family': 'synthetic-family'}
        source = purpose == 'source_document'
        plan = {'entry': entry, 'redirects': False, 'retries': False, 'cookies': False, 'claim_acceptance': False,
                'reviewed_at': '2026-09-08T19:00:03Z',
                'decision': 'approved_exact_source_url_after_retained_policy_review' if source else 'policy_probe'}
        plan_name = 'review_plans/' + name + '.json'
        self.write(plan_name, encoded(plan))
        self.write(root + '/plan.json', encoded(plan))
        self.write(root + '/responses/' + name + '.body', body)
        attempt = {**entry, 'company': 'Synthetic', 'country_code': 'US', 'scope': 'Synthetic source scope',
            'started_at': self.source['retrieval_started_at'] if source else '2026-09-08T19:00:00Z',
            'finished_at': self.source['retrieval_finished_at'] if source else '2026-09-08T19:00:01Z',
            'http_status': 200, 'content_type': 'text/html', 'curl_exit_code': 0, 'error': None,
            'final_url': entry['url'], 'redirect_count': 0, 'tls_verification_result': 0,
            'path': 'responses/' + name + '.body', 'bytes': len(body), 'sha256': rates.runtime.digest(body),
            'outcome': 'succeeded', 'negative_evidence_eligible': False}
        ledger = {'format': source_checks.SOURCE_FORMAT, 'review_scope_id': 'synthetic',
                  'checked_source_id': 'synthetic', 'clock_precision': 'fixture exact seconds',
                  'absence_inference_allowed': False, 'attempts': [attempt]}
        self.write(root + '/source_checks.json', encoded(ledger))
        manifest = {p.relative_to(self.root / root).as_posix(): {'bytes': len(p.read_bytes()), 'sha256': rates.runtime.digest(p.read_bytes())}
                    for p in (self.root / root).rglob('*') if p.is_file()}
        self.write(root + '/manifest.json', encoded(manifest))
        return {'packet': root, 'plan': plan_name, 'manifest_sha256': rates.runtime.digest(encoded(manifest))}

    def admit(self, output='result.sqlite', **changes):
        return rates.admit(parent=self.parent, review_path=self.review_path, reference_root=self.root,
            expected_review_sha256=self.sha, output=self.root / output, **changes)

    def test_full_admission_and_exact_replay_preserve_parent_and_source_only_semantics(self):
        before = self.parent.read_bytes()
        result = self.admit()
        replay = self.admit(verify_only=True, expected_output_sha256=result['database_sha256'])
        self.assertTrue(replay['verified'])
        self.assertEqual(self.parent.read_bytes(), before)
        connection = database.connect(self.root / 'result.sqlite')
        self.addCleanup(connection.close)
        self.assertEqual(repository.validate_database(connection), [])
        self.assertEqual(database.schema_version(connection), 9)
        self.assertEqual(service.claim_records(connection, as_of='2026-09-08', recorded_at=result['admitted_at']), [])
        self.assertEqual(len(service.source_claim_records(connection, recorded_at=result['admitted_at'])), 1)
        self.assertEqual(service.source_claim_records(connection, recorded_at=self.review['reviewed_at']), [])
        self.assertEqual(connection.execute('SELECT count(*) FROM capacity_values').fetchone()[0], 0)
        with self.assertRaises(ValueError): self.admit()

    def test_hash_tampering_and_unlisted_packet_members_fail_without_output(self):
        self.write('artifacts/source/unlisted.txt', b'unreviewed')
        with self.assertRaises(ValueError): self.admit()
        self.assertFalse((self.root / 'result.sqlite').exists())

    def test_semantic_rewrite_even_with_new_review_hash_fails_source_binding(self):
        review = copy.deepcopy(self.review)
        review['rate']['source_wording'] = 'The company produces approximately 21,000 wafer semiconductors per month.'
        review['rate']['numeric_qualifier'] = 'approximate'
        review['rate']['source_qualifier_literal'] = 'approximately'
        self.review_path.write_bytes(encoded(review))
        self.sha = rates.runtime.digest(encoded(review))
        with self.assertRaises(ValueError): self.admit()

    def test_research_bytes_and_parent_hash_are_required(self):
        self.write('review_plans/research.json', b'{}')
        with self.assertRaises(ValueError): self.admit()
        self.assertFalse((self.root / 'result.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
