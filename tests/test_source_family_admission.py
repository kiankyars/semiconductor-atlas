"""A family correction is one actual-time metadata row on an exact private successor."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from semiconductor_atlas import database, models as m, repository as r
from semiconductor_atlas import source_family_admission as admission, source_family_review as review
from scripts import frozen_runtime as runtime


CREATED = '2026-01-01T00:00:00Z'
REVIEWED = '2026-09-09T09:00:00Z'
STARTED = '2026-09-09T10:00:00Z'
ADMITTED = '2026-09-09T10:00:00.123456Z'


class SourceFamilyAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.body = b'<title>Synthetic publisher</title>'
        runtime.write_new(self.root / 'source.html', self.body)
        c, _ = database.initialize(':memory:', target_version=11)
        self.addCleanup(c.close)
        for key in ('original', 'target'):
            r.add_source_family(c, m.SourceFamily(key, key, key, CREATED))
        r.add_source(c, m.Source('source', 'original', 'source', 'Source', 'Publisher',
                                'https://example.test', CREATED))
        r.add_source_document(c, m.SourceDocument('doc', 'source', 'https://example.test/doc',
            'Document', CREATED, runtime.digest(self.body), metadata={'retained_body': {
                'path': 'source.html', 'sha256': runtime.digest(self.body)}}))
        self.payload = {'format': review.FORMAT, 'reviewer': 'Synthetic reviewer', 'reviewed_at': REVIEWED,
            'reason': 'One synthetic publisher was grouped by its import route.', 'prior_exposure': 'Synthetic fixture',
            'source': dict(c.execute('SELECT * FROM sources').fetchone()),
            'target_family': dict(c.execute("SELECT * FROM source_families WHERE id='target'").fetchone()),
            'evidence_documents': [dict(c.execute('SELECT id,source_id,document_url,content_sha256,retrieved_at FROM source_documents').fetchone())],
            'expected_predecessor_id': None, 'boundaries': review.BOUNDARIES}
        c.commit()
        self.parent_raw = c.serialize()
        self.review_raw = (json.dumps(self.payload, indent=2) + '\n').encode()
        runtime.write_new(self.root / 'parent.sqlite', self.parent_raw)
        runtime.write_new(self.root / 'review.json', self.review_raw)
        self.args = dict(parent=self.root / 'parent.sqlite', expected_parent_sha256=runtime.digest(self.parent_raw),
            review=self.root / 'review.json', expected_review_sha256=runtime.digest(self.review_raw),
            reference_root=self.root, output=self.root / 'successor.sqlite')

    def admit(self, **changes):
        with patch.object(admission, 'now', side_effect=[STARTED, ADMITTED]):
            return admission.admit(**(self.args | changes))

    def test_exact_replay_and_inherited_rows_schema_are_preserved(self):
        result = self.admit()
        raw = runtime.read(self.args['output'])
        with patch.object(admission, 'now', return_value=ADMITTED):
            verified = admission.admit(**self.args, verify_only=True,
                                       expected_output_sha256=result['database_sha256'])
        self.assertTrue(verified['verified'])
        self.assertEqual(result['database_sha256'], verified['database_sha256'])
        self.assertEqual(raw, runtime.read(self.args['output']))
        self.assertEqual(self.parent_raw, runtime.read(self.args['parent']))
        old, new = admission.open_image(self.parent_raw), admission.open_image(raw)
        try:
            for table, in old.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                before = [tuple(r) for r in old.execute('SELECT * FROM "' + table + '" ORDER BY rowid')]
                after = [tuple(r) for r in new.execute('SELECT * FROM "' + table + '" ORDER BY rowid')]
                self.assertEqual(before, after[:-1] if table == 'schema_migrations' else after, table)
            for name, sql in old.execute('SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL'):
                self.assertEqual(sql, new.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0])
            self.assertEqual(ADMITTED, new.execute('SELECT recorded_at FROM source_family_assignments').fetchone()[0])
            self.assertEqual(ADMITTED, new.execute('SELECT applied_at FROM schema_migrations WHERE version=12').fetchone()[0])
            self.assertEqual(1, len(review.assignment_history(new)))
            self.assertEqual([], r.validate_database(new))
        finally:
            old.close(); new.close()

    def test_hashes_existing_output_and_future_review_fail_without_publication(self):
        for changes in ({'expected_parent_sha256': '0' * 64}, {'expected_review_sha256': '0' * 64},
                        {'output': self.args['parent']}, {'output': self.args['review']}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.admit(**changes)
            self.assertFalse(self.args['output'].exists())
        with patch.object(admission, 'now', side_effect=[CREATED, '2026-01-01T00:00:01Z']), self.assertRaisesRegex(ValueError, 'precede actual'):
            admission.admit(**self.args)
        self.assertFalse(self.args['output'].exists())

    def test_backward_or_equal_admission_clock_is_rejected(self):
        for second in (STARTED, '2026-09-09T09:30:00Z'):
            with patch.object(admission, 'now', side_effect=[STARTED, second]), self.assertRaisesRegex(ValueError, 'must advance'):
                admission.admit(**self.args)
            self.assertFalse(self.args['output'].exists())

    def test_subject_document_requires_own_retained_path(self):
        c, _ = database.initialize(':memory:', target_version=11)
        try:
            r.add_source_family(c, m.SourceFamily('family', 'family', 'Family', CREATED))
            for source in ('subject', 'context'):
                r.add_source(c, m.Source(source, 'family', source, source, 'Publisher', 'https://example.test', CREATED))
                r.add_source_document(c, m.SourceDocument(source, source, 'https://example.test/doc', 'Doc',
                    CREATED, runtime.digest(self.body), metadata={} if source == 'subject' else {
                        'retained_body': {'path': 'source.html', 'sha256': runtime.digest(self.body)}}))
            payload = {'source': {'id': 'subject'}, 'evidence_documents': [dict(row) for row in c.execute(
                'SELECT id,source_id,document_url,content_sha256,retrieved_at FROM source_documents')]}
            with self.assertRaisesRegex(ValueError, 'own retained path'):
                admission.retained_evidence(c, payload, self.root)
        finally:
            c.close()

    def test_wrong_retained_body_and_execution_time_mutation_fail(self):
        missing_root = self.root / 'missing'; missing_root.mkdir()
        with self.assertRaises(OSError):
            self.admit(reference_root=missing_root)
        altered_root = self.root / 'altered'; altered_root.mkdir()
        runtime.write_new(altered_root / 'source.html', b'changed')
        with self.assertRaisesRegex(ValueError, 'body hash differs'):
            self.admit(reference_root=altered_root)
        with patch.object(admission, 'codes', return_value={}), self.assertRaisesRegex(ValueError, 'changed since loading'):
            self.admit()
        self.assertFalse(self.args['output'].exists())

    def test_replay_rejects_hash_valid_unrelated_successor_row(self):
        result = self.admit()
        c = admission.open_image(runtime.read(self.args['output']))
        try:
            r.add_entity(c, m.Entity('unrelated', m.EntityKind.FACILITY, 'unrelated', ADMITTED))
            c.commit()
            changed = c.serialize()
        finally:
            c.close()
        extra = self.root / 'extra.sqlite'; runtime.write_new(extra, changed)
        with patch.object(admission, 'now', return_value=ADMITTED), self.assertRaisesRegex(ValueError, 'reconstruction'):
            admission.admit(**(self.args | {'output': extra}), verify_only=True,
                            expected_output_sha256=runtime.digest(changed))
        with self.assertRaisesRegex(ValueError, 'must not exist'):
            self.admit()


if __name__ == '__main__':
    unittest.main()
