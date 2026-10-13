"""Synthetic source and successor tests; no real publisher data is modified."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semiconductor_atlas import database, models as m, repository as r, service
from semiconductor_atlas import tsmc_source_quantities as importer
from scripts import frozen_runtime as runtime


CREATED = '2026-01-01T00:00:00Z'
REVIEWED = '2026-09-08T10:00:00Z'
STARTED = '2026-09-08T11:00:00Z'
ADMITTED = '2026-09-08T11:00:01Z'
LATER = '2026-09-08T12:00:00Z'


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def fixture():
    body = bytearray(b'<html><body>')
    evidence = {}
    for key, text in [('capacity', importer.CAPACITY), ('capacity_repeat', importer.CAPACITY),
                      ('shipments', importer.SHIPMENTS)]:
        start = len(body)
        fragment = ('<p>' + text + '</p>').encode()
        body.extend(fragment)
        evidence[key] = {'start': start, 'end': len(body), 'sha256': runtime.digest(fragment)}
    body.extend(b'</body></html>')
    c, _ = database.initialize(':memory:', target_version=6)
    try:
        r.add_source_family(c, m.SourceFamily('family', 'fixture', 'Fixture', CREATED))
        r.add_source(c, m.Source('source', 'family', 'fixture', 'Synthetic', 'Fixture', 'https://example.test', CREATED))
        r.add_source_document(c, m.SourceDocument('document', 'source', 'https://example.test/source',
            'Synthetic filing', CREATED, runtime.digest(body), published_at='2025-12-31',
            metadata={'rights': {'use': 'local_review_only', 'redistribution_authorized': False}}))
        r.add_entity(c, m.Entity('prior', m.EntityKind.FACILITY, 'prior:facility', CREATED))
        cap = m.CapacityValue('wafer_capacity', m.CapacityBasis.ANNOUNCED, 'wafers_per_month', 1, 2, 3)
        r.add_claim_series(c, m.ClaimSeries('prior', 'prior', 'prior:capacity', 'capacity', cap.kind, CREATED))
        r.insert_claim(c, m.ClaimVersion('prior', 'prior', '2025-01-01', CREATED,
            m.ClaimKind.SOURCE_STATEMENT, 'fixture', None), cap, evidence=[m.EvidenceLink('document')])
        c.commit()
        parent = c.serialize()
    finally:
        c.close()
    review = {'format': importer.FORMAT, 'reviewed_at': REVIEWED, 'reviewed_by': 'Synthetic reviewer',
        'prior_exposure': 'Exposed synthetic fixture; not independent physical evidence.',
        'selection': 'Complete four-quantity fixture.', 'parent_database_sha256': runtime.digest(parent),
        'source_document_id': 'document', 'source_sha256': runtime.digest(body), 'source_bytes': len(body),
        'evidence': evidence, 'rows': importer.rows()}
    return bytes(body), parent, review


class TsmcSourceQuantityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.body, self.parent, self.review = fixture()
        for name, raw in [('source.html', self.body), ('parent.sqlite', self.parent), ('review.json', canonical(self.review))]:
            (self.root / name).write_bytes(raw)
        self.sha_patch = mock.patch.object(importer, 'SOURCE_SHA', runtime.digest(self.body))
        self.sha_patch.start()
        self.addCleanup(self.sha_patch.stop)

    def validate(self, review=None, body=None):
        raw = canonical(review or self.review)
        return importer.validate_review(raw, self.body if body is None else body, runtime.digest(raw))

    def execute(self, verify=False, **changes):
        options = dict(parent=self.root / 'parent.sqlite', review_path=self.root / 'review.json',
            source_body=self.root / 'source.html', expected_review_sha256=runtime.digest(canonical(self.review)),
            output=self.root / 'successor.sqlite', verify_only=verify)
        if verify:
            options['expected_output_sha256'] = runtime.digest((self.root / 'successor.sqlite').read_bytes())
        options.update(changes)
        with mock.patch.object(importer, 'now', side_effect=[LATER] if verify else [STARTED, ADMITTED]):
            return importer.admit(**options)

    def test_complete_four_quantities_replay_and_exclude_physical_capacity(self):
        result = self.execute()
        original = (self.root / 'successor.sqlite').read_bytes()
        self.assertEqual(self.execute(True)['database_sha256'], runtime.digest(original))
        self.assertEqual((self.root / 'parent.sqlite').read_bytes(), self.parent)
        self.assertEqual((self.root / 'successor.sqlite').read_bytes(), original)
        c = importer.open_image(original, 7)
        try:
            self.assertEqual(service.source_claim_records(c, recorded_at=STARTED,
                subject_entity_id=result['source_native_entity_id']), [])
            quantities = service.source_claim_records(c, recorded_at=ADMITTED,
                subject_entity_id=result['source_native_entity_id'])
            self.assertEqual(len(quantities), 4)
            self.assertTrue(all(q['value']['source_quantity']['physical_capacity_basis'] is None for q in quantities))
            self.assertTrue(all(q['valid_from'] is None and q['confidence'] is None for q in quantities))
            self.assertEqual(len(service.claim_records(c, as_of='2026-09-08', recorded_at=LATER)), 1)
            self.assertEqual(c.execute('SELECT count(*) FROM capacity_values').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT count(*) FROM source_quantity_metadata').fetchone()[0], 4)
            self.assertEqual(c.execute('SELECT applied_at FROM schema_migrations WHERE version=7').fetchone()[0], ADMITTED)
        finally:
            c.close()
        self.assertFalse(any(result['boundaries'].values()))

    def test_missing_duplicate_reordered_or_reinterpreted_rows_reject(self):
        for rows in (self.review['rows'][:-1], [*self.review['rows'], self.review['rows'][0]],
                     list(reversed(self.review['rows']))):
            with self.assertRaisesRegex(ValueError, 'four-quantity'): self.validate({**self.review, 'rows': rows})
        for field, value in [('numeric_qualifier', 'as_reported_precision_unspecified'),
                             ('scope_kind', 'source_native_facility'), ('source_unit_scale', 1000),
                             ('period_end', '2026-12-31'), ('metric', 'wafer_output')]:
            review = copy.deepcopy(self.review)
            review['rows'][0]['metadata'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'semantics differs'): self.validate(review)

    def test_wrong_body_or_trusted_review_hash_rejects(self):
        with self.assertRaisesRegex(ValueError, 'publisher bytes'): self.validate(body=self.body + b' ')
        with self.assertRaisesRegex(ValueError, 'review hash'):
            importer.validate_review(canonical(self.review), self.body, '0' * 64)

    def test_shortened_or_hidden_context_cannot_supply_the_numeric_claim(self):
        review = copy.deepcopy(self.review)
        span = review['evidence']['capacity']
        span['start'] += 3
        span['sha256'] = runtime.digest(self.body[span['start']:span['end']])
        with self.assertRaisesRegex(ValueError, 'complete visible blocks'): self.validate(review)
        body = self.body.replace(b'<p>', b'<p hidden>', 1)
        review = copy.deepcopy(self.review)
        review['source_bytes'], review['source_sha256'] = len(body), runtime.digest(body)
        for key, span in review['evidence'].items():
            span['end'] += 7
            if key != 'capacity': span['start'] += 7
            span['sha256'] = runtime.digest(body[span['start']:span['end']])
        with mock.patch.object(importer, 'SOURCE_SHA', runtime.digest(body)), self.assertRaises(ValueError): self.validate(review, body)

    def test_parent_hash_review_chronology_and_rights_remain_required(self):
        for field, value in [('parent_database_sha256', '0' * 64), ('reviewed_at', '2026-09-09T00:00:00Z'),
                             ('source_document_id', 'unrelated')]:
            review = {**self.review, field: value}
            (self.root / 'review.json').write_bytes(canonical(review))
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.execute(expected_review_sha256=runtime.digest(canonical(review)))
            self.assertFalse((self.root / 'successor.sqlite').exists())

    def test_existing_output_and_bad_trusted_successor_hash_do_not_overwrite(self):
        self.execute()
        raw = (self.root / 'successor.sqlite').read_bytes()
        with self.assertRaisesRegex(ValueError, 'must not exist'): self.execute()
        with self.assertRaisesRegex(ValueError, 'successor hash'): self.execute(True, expected_output_sha256='0' * 64)
        self.assertEqual((self.root / 'successor.sqlite').read_bytes(), raw)

    def test_missing_retained_source_rights_rejects(self):
        c = importer.open_image(self.parent, 6)
        try:
            c.execute('DROP TRIGGER source_documents_immutable_update')
            c.execute("UPDATE source_documents SET metadata_json='{}'")
            c.commit()
            parent = c.serialize()
        finally: c.close()
        review = {**self.review, 'parent_database_sha256': runtime.digest(parent)}
        (self.root / 'parent.sqlite').write_bytes(parent)
        (self.root / 'review.json').write_bytes(canonical(review))
        with self.assertRaisesRegex(ValueError, 'source rights'):
            self.execute(expected_review_sha256=runtime.digest(canonical(review)))
        self.assertFalse((self.root / 'successor.sqlite').exists())

    def test_resealed_metadata_mutation_fails_complete_successor_reconstruction(self):
        self.execute()
        path = self.root / 'successor.sqlite'
        c = importer.open_image(path.read_bytes(), 7)
        try:
            c.execute('DROP TRIGGER source_quantity_metadata_immutable_update')
            c.execute("UPDATE source_quantity_metadata SET source_number_literal='17.0' WHERE source_number_literal='17'")
            c.commit()
            path.write_bytes(c.serialize())
        finally: c.close()
        with self.assertRaisesRegex(ValueError, 'complete parent-plus-quantities'): self.execute(True)

    def test_loaded_producer_drift_rejects_before_output(self):
        with mock.patch.object(importer, 'codes', return_value={}):
            with self.assertRaisesRegex(ValueError, 'since module loading'): self.execute()
        self.assertFalse((self.root / 'successor.sqlite').exists())


if __name__ == '__main__': unittest.main()
