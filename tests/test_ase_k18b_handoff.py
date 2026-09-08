"""Synthetic handoff graph tests; capture replay is independently covered by its own suite."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import frozen_runtime as runtime
from semiconductor_atlas import ase_k18b_milestones as k, curated_review as q


ROOT = Path(__file__).resolve().parents[1]


def encoded(value):
    return runtime.canonical(value)


class K18BHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.review = json.loads((ROOT / 'review_plans/2026-09-08-ase-k18b-claim-admission-v1.json').read_bytes())
        self.text = json.loads((ROOT / self.review['source_review']['path']).read_bytes())
        self.access = json.loads((ROOT / self.review['access_review']['path']).read_bytes())
        self.plan = json.loads((ROOT / 'acquisition_plans/ase_k18b_kcg_v1.json').read_bytes())
        body = bytearray(b'<html><body><dl>')
        evidence = {}
        for key, phrases in k.EVIDENCE_TEXT.items():
            tag = 'p' if key == 'complete_opening_paragraph' else 'dd'
            fragment = (f'<{tag}>' + ' '.join(phrases) + f'</{tag}>').encode()
            start = len(body)
            body.extend(fragment)
            evidence[key] = {'start': start, 'end': len(body), 'sha256': runtime.digest(fragment)}
        body.extend(b'</dl></body></html>')
        self.body = bytes(body)
        self.sha = runtime.digest(self.body)
        self.patch = mock.patch.object(k, 'SOURCE_SHA', self.sha)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        source = self.review['source']
        source['run_id'], source['text_sha256'] = 'a' * 64, 'b' * 64
        source['body']['path'] = 'capture/responses/kcg-ase-k18b-groundbreaking.body'
        source['body']['sha256'] = self.sha
        self.review['source_sha256'], self.review['source_bytes'] = self.sha, len(body)
        self.review['evidence'] = self.text['evidence_spans'] = evidence
        self.write(source['body']['path'], self.body)
        self.review['access_review']['sha256'] = runtime.digest(encoded(self.access))
        self.text['access_review'] = copy.deepcopy(self.review['access_review'])
        self.plan['review_record'] = copy.deepcopy(self.review['access_review'])
        self.write(self.review['access_review']['path'], encoded(self.access))
        self.write('capture/review.json', encoded(self.access))
        self.write('capture/plan.json', encoded(self.plan))
        source['plan_sha256'] = runtime.digest(encoded(self.plan))
        document = {'id': source['document_id'], 'url': k.SOURCE_URL, 'company': 'ASE',
            'country_code': 'TW', 'source_family': 'municipal-government-news',
            'status': 'first_observation_requires_review', 'review_required': True,
            'current_sha256': self.sha, 'current_text_sha256': source['text_sha256'],
            'prior_sha256': None, 'prior_text_sha256': None, 'prior_success_at': None,
            'response_finished_at': source['retrieved_at']}
        payload = {'kind': 'capture_imported', 'rule_version': q.RULE_VERSION,
            'run_id': source['run_id'], 'source_path': str(self.root / 'capture'),
            'plan_sha256': source['plan_sha256'], 'review_scope_id': self.plan['review_scope_id'],
            'facility_key': k.SCOPE_KEY, 'text_normalization': 'html_visible_text_v1',
            'started_at': '2026-09-08T20:58:31.935341Z', 'finished_at': '2026-09-08T20:58:37.066079Z',
            'documents': [document]}
        source['candidate_id'] = q._candidate_id(payload, document)
        self.events = []
        self.append(payload, '2026-09-08T20:58:37.103830Z')
        self.text['candidate_id'] = source['candidate_id']
        self.text['expected_event_id'] = self.events[0]['event_id']
        self.text['source_capture'] = {'path': 'capture', 'manifest_sha256': source['run_id'],
            'body_path': 'responses/kcg-ase-k18b-groundbreaking.body', 'body_sha256': self.sha,
            'body_bytes': len(body), 'normalized_sha256': source['text_sha256'],
            'retrieved_at': source['retrieved_at'], 'queue_admitted_at': self.events[0]['recorded_at']}
        self.append({'kind': 'review_decision', 'rule_version': q.RULE_VERSION, 'action': 'handoff',
            'candidate_id': source['candidate_id'], 'expected_event_id': self.events[0]['event_id'],
            'reviewer': 'Synthetic reviewer', 'reason': 'Synthetic source-native handoff',
            'evidence_ref': self.review['source_review']['path']}, '2026-09-08T21:01:34.022508Z')
        repeated = copy.deepcopy(payload)
        repeated['run_id'] = 'c' * 64
        repeated['source_path'] = str(self.root / 'repeat')
        repeated['started_at'] = '2026-09-08T21:01:35Z'
        repeated['finished_at'] = '2026-09-08T21:01:39.084742Z'
        repeated['documents'][0].update(status='unchanged', review_required=False,
            response_finished_at='2026-09-08T21:01:39.070580Z', prior_sha256=self.sha,
            prior_text_sha256=source['text_sha256'], prior_success_at=source['retrieved_at'])
        self.append(repeated, '2026-09-08T21:01:39.125403Z')
        self.seal_text()
        self.seal_events()
        self.review_path = self.root / 'claim-review.json'

    def write(self, relative, raw):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def append(self, payload, at):
        event = {'sequence': len(self.events) + 1, 'previous_event_id': self.events[-1]['event_id'] if self.events else None,
                 'recorded_at': at, 'payload': payload}
        self.events.append({**event, 'event_id': q._hash(event)})

    def seal_text(self):
        raw = encoded(self.text)
        self.write(self.review['source_review']['path'], raw)
        self.review['source_review']['sha256'] = runtime.digest(raw)

    def seal_events(self):
        old, self.events = self.events, []
        for event in old:
            self.append(event['payload'], event['recorded_at'])
        source = self.review['source']
        source['expected_event_id'] = self.events[1]['event_id']
        source['queue_head'] = self.events[-1]['event_id']
        raw = encoded({'format': 'semiconductor-atlas-curated-review-events-v1', 'events': self.events})
        self.write(source['events']['path'], raw)
        source['events']['sha256'] = runtime.digest(raw)

    def load(self, payload_change=False):
        raw = encoded(self.review)
        self.review_path.write_bytes(raw)
        payloads = {e['payload']['source_path']: e['payload'] for e in self.events
                    if e['payload']['kind'] == 'capture_imported'}
        def captured(path):
            result = copy.deepcopy(payloads[str(path)])
            if payload_change: result['started_at'] = '2026-09-08T20:58:30Z'
            return result
        with mock.patch.object(q, '_capture_payload', side_effect=captured):
            return k.load_review(self.review_path, self.root, runtime.digest(raw), '2026-09-09T00:00:00Z')

    def test_three_events_two_observations_and_bare_path_handoff_load(self):
        result = self.load()
        self.assertEqual(len(result['events']), 3)
        self.assertNotEqual(self.review['source']['queue_head'], self.review['source']['expected_event_id'])
        self.assertEqual(result['source_handoff_at'], '2026-09-08T21:01:34.022508Z')

    def test_replayed_capture_payload_must_equal_admitted_payload(self):
        with self.assertRaisesRegex(ValueError, 'retained packet differs'): self.load(payload_change=True)

    def test_source_review_cannot_precede_retrieval_or_follow_handoff(self):
        for at in ('2026-09-08T20:58:00Z', '2026-09-08T21:02:00Z'):
            with self.subTest(at=at):
                self.text['reviewed_at'] = at
                self.seal_text()
                with self.assertRaises(ValueError): self.load()

    def test_claim_review_must_follow_complete_queue_history(self):
        self.review['reviewed_at'] = '2026-09-08T21:01:38Z'
        with self.assertRaisesRegex(ValueError, 'could not yet know'): self.load()

    def test_candidate_handoff_and_queue_head_are_not_interchangeable(self):
        self.review['source']['expected_event_id'] = self.review['source']['queue_head']
        with self.assertRaisesRegex(ValueError, 'stale expected event'): self.load()

    def test_resealed_handoff_to_unrelated_text_review_is_rejected(self):
        self.events[1]['payload']['evidence_ref'] = 'review_plans/unrelated.json'
        self.seal_events()
        with self.assertRaisesRegex(ValueError, 'source-text handoff review differs'): self.load()

    def test_resealed_text_review_cannot_change_capture_cas_or_evidence(self):
        original = copy.deepcopy(self.text)
        for field in ('expected_event_id', 'source_capture', 'evidence_spans', 'boundaries'):
            with self.subTest(field=field):
                self.text = copy.deepcopy(original)
                if field == 'expected_event_id': self.text[field] = '0' * 64
                elif field == 'source_capture': self.text[field]['retrieved_at'] = '2025-10-03T00:00:00Z'
                elif field == 'evidence_spans': self.text[field]['publication_date']['start'] += 1
                else: self.text[field]['core_claim_acceptance'] = True
                self.seal_text()
                with self.assertRaises(ValueError): self.load()

    def test_capture_review_bytes_cannot_differ_from_bound_access_review(self):
        self.write('capture/review.json', encoded({**self.access, 'claim_acceptance': True}))
        with self.assertRaisesRegex(ValueError, 'access approval differs'): self.load()

    def test_newer_failed_check_in_frozen_history_blocks_admission(self):
        self.events[2]['payload']['documents'][0]['status'] = 'failed'
        self.seal_events()
        with self.assertRaisesRegex(ValueError, 'changed or failed check'): self.load()


if __name__ == '__main__':
    unittest.main()
