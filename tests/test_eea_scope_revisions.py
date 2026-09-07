from __future__ import annotations

import json
import io
import sqlite3
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from semiconductor_atlas import eea_scope_revisions as revisions
from semiconductor_atlas import eea_scope_history as history
from semiconductor_atlas import cli
from semiconductor_atlas.database import initialize
from semiconductor_atlas.eea_industrial_review import parse_eea_industrial_review_bytes
from semiconductor_atlas.repository import current_claims, known_source_claims
from tests import test_ingest_eea_industrial as fixtures


class EEAScopeRevisionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EEAIndustrialImportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.root = self.fixture.root.resolve()
        self.db = self.fixture.connection
        self.parent = self.fixture.root / 'parent.sqlite'
        self.db.execute('VACUUM INTO ?', (str(self.parent),))
        self.genesis = self.fixture._import()
        self.source_ids = [row.facility_inspire_id for row in self.fixture.queue.candidates]
        self.ids = {row.facility_inspire_id: row.candidate_id for row in self.fixture.queue.candidates}
        self.accepted = self.ids['DE.EEA/MixedCase-1.FACILITY']
        self.deferred = self.ids['FR.EEA/Deferred-2.FACILITY']
        self.rejected = self.ids['AT.EEA/Rejected-3.FACILITY']
        self.verifier = patch('semiconductor_atlas.ingest_eea_industrial.verify_eea_industrial_snapshot',
                              return_value=self.fixture.snapshot)
        self.verifier.start()
        self.addCleanup(self.verifier.stop)

    def review(self, minute, outcomes):
        payload = json.loads(self.fixture.review.raw_bytes)
        payload['reviewed_at'] = payload['knowledge_cutoff_at'] = f'2026-07-20T19:{minute:02d}:00Z'
        for row in payload['decisions']:
            row['outcome'] = outcomes.get(row['candidate_id'], row['outcome'])
            if row['outcome'] != 'defer' and not row['evidence']:
                row['evidence'] = [{
                    'accessed_at': payload['reviewed_at'], 'excerpt': 'Bounded fixture process evidence',
                    'title': 'Fixture site evidence', 'url': 'https://example.org/site/' + row['candidate_id'],
                }]
            row['reason'] = 'Explicit fixture scope review ' + str(minute)
        return parse_eea_industrial_review_bytes(fixtures._canonical(payload), queue=self.fixture.queue)

    def accept(self, review, previous, minute, **kwargs):
        with patch.object(revisions, '_now', side_effect=[
            f'2026-07-20T19:{minute:02d}:01Z', f'2026-07-20T19:{minute:02d}:02.123456Z',
            f'2026-07-20T19:{minute:02d}:03Z']):
            return revisions.accept_revision(self.db, snapshot=self.fixture.snapshot,
                candidate_queue=self.fixture.queue, review=review,
                expected_predecessor_run_id=previous, **kwargs)

    def report(self, clock):
        return revisions.scope_records(self.db, candidate_queue=self.fixture.queue, recorded_at=clock)

    def rows(self):
        return {row[0]: [tuple(value) for value in self.db.execute('SELECT * FROM "' + row[0] + '"')]
                for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_scope_transitions_preserve_sources_and_historical_views(self):
        original = self.rows()
        historical = self.report(self.genesis.accepted_at)
        self.assertEqual(9, len(historical['source_statements']))
        self.assertEqual([], self.report('2026-07-20T18:59:59.999999Z')['dispositions'])
        second_review = self.review(1, {self.accepted: 'defer', self.deferred: 'accept_in_scope'})
        second = self.accept(second_review, self.genesis.ingestion_run_id, 1)
        self.assertEqual(1, second['new_candidates'])
        self.assertEqual(historical['head_run_id'], self.report('2026-07-20T19:01:02.123455Z')['head_run_id'])
        at_second = self.report(second['accepted_at'])
        self.assertEqual(second['ingestion_run_id'], at_second['head_run_id'])
        third_review = self.review(2, {self.accepted:'accept_in_scope', self.deferred:'accept_in_scope',
                                       self.rejected:'accept_in_scope'})
        third = self.accept(third_review, second['ingestion_run_id'], 2)
        self.assertEqual(1, third['new_candidates'])
        fourth_review = self.review(3, {self.accepted:'reject_out_of_scope', self.deferred:'accept_in_scope',
                                        self.rejected:'accept_in_scope'})
        fourth = self.accept(fourth_review, third['ingestion_run_id'], 3)
        self.assertEqual(0, fourth['new_candidates'])
        self.assertEqual(0, fourth['claims_created'])
        self.assertEqual(historical, self.report(self.genesis.accepted_at))
        self.assertEqual(at_second, self.report(second['accepted_at']))
        self.assertEqual(2, self.report(fourth['accepted_at'])['outcome_counts']['accept_in_scope'])
        for table, rows in original.items():
            after = self.rows()[table]
            self.assertTrue(all(row in after for row in rows), table)
        self.assertEqual(3, self.db.execute('SELECT COUNT(*) FROM source_records').fetchone()[0])
        self.assertEqual([], current_claims(self.db, as_of='2030-01-01', recorded_at=fourth['accepted_at']))
        retained = known_source_claims(self.db, recorded_at=fourth['accepted_at'])
        self.assertGreater(len(retained), len(self.report(fourth['accepted_at'])['source_statements']))

    def test_historical_replay_after_later_revision_denies_writes(self):
        first_review = self.review(1, {})
        first = self.accept(first_review, self.genesis.ingestion_run_id, 1)
        later = self.accept(self.review(2, {self.accepted:'defer'}), first['ingestion_run_id'], 2)
        before = self.rows()
        changes = self.db.total_changes
        self.db.set_authorizer(lambda action,*_: sqlite3.SQLITE_DENY if action in (
            sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) else sqlite3.SQLITE_OK)
        try:
            replay = self.accept(first_review, self.genesis.ingestion_run_id, 3,
                                 accepted_at=first['accepted_at'])
            self.assertEqual(first['ingestion_run_id'], replay['ingestion_run_id'])
            self.assertEqual(later['ingestion_run_id'], replay['head_run_id'])
            self.assertTrue(replay['replayed_existing_run'])
            self.assertEqual(changes, self.db.total_changes)
        finally:
            self.db.set_authorizer(None)
        self.assertEqual(before, self.rows())
        with self.assertRaisesRegex(ValueError,'predecessor'):
            self.accept(first_review, later['ingestion_run_id'], 3)
        with self.assertRaisesRegex(ValueError,'acceptance clock'):
            self.accept(first_review,self.genesis.ingestion_run_id,3,accepted_at='2026-07-20T19:03:00Z')

    def test_stale_predecessor_and_backdating_rejected_atomically(self):
        first = self.accept(self.review(1, {}), self.genesis.ingestion_run_id, 1)
        before = self.rows()
        with self.assertRaisesRegex(ValueError,'stale'):
            self.accept(self.review(2, {}), self.genesis.ingestion_run_id, 2)
        with self.assertRaisesRegex(ValueError,'replay-only'):
            self.accept(self.review(2, {}), first['ingestion_run_id'], 2,
                        accepted_at='2026-07-20T19:02:02Z')
        self.assertEqual(before, self.rows())

    def test_postwrite_failure_rolls_back_all_added_rows(self):
        before = self.rows()
        original = revisions._history
        calls = 0
        def fail_after_write(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError('injected postwrite history failure')
            return original(*args)
        with patch.object(revisions,'_history',side_effect=fail_after_write):
            with self.assertRaisesRegex(ValueError,'injected'):
                self.accept(self.review(1,{self.deferred:'accept_in_scope'}),self.genesis.ingestion_run_id,1)
        self.assertEqual(before,self.rows())

    def test_caller_transaction_is_not_committed(self):
        self.db.execute('BEGIN IMMEDIATE')
        original=self.rows()
        self.accept(self.review(1,{self.deferred:'accept_in_scope'}),self.genesis.ingestion_run_id,1)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(original,self.rows())

    def test_extra_document_lineage_rejected_by_scoped_consumer(self):
        result=self.accept(self.review(1,{}),self.genesis.ingestion_run_id,1)
        self.db.execute('INSERT INTO ingestion_run_documents VALUES (?, ?, ?)',
            (result['ingestion_run_id'],self.genesis.candidate_document_id,'unexpected'))
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'document lineage'):
            self.report(result['accepted_at'])

    def test_portable_restore_and_continuation_preserve_every_cutoff(self):
        first = self.accept(self.review(1,{self.deferred:'accept_in_scope'}),self.genesis.ingestion_run_id,1)
        second = self.accept(self.review(2,{self.accepted:'defer',self.deferred:'accept_in_scope'}),first['ingestion_run_id'],2)
        packet = history.export_history(self.fixture.root/'atlas.sqlite', parent_database=self.parent,
            candidate_queue=self.fixture.queue,recorded_at=second['accepted_at'])
        packet_path = self.fixture.root/'history.json'
        packet_path.write_bytes(revisions._raw(packet))
        restored_path = self.fixture.root/'restored.sqlite'
        restored = history.restore_history(self.parent,history_file=packet_path,output_database=restored_path)
        self.assertEqual(second['ingestion_run_id'],restored['head_run_id'])
        original=self.db
        self.db=sqlite3.connect(restored_path)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        try:
            for cutoff in ('2026-07-20T18:00:00Z',self.genesis.accepted_at,first['accepted_at'],second['accepted_at']):
                self.assertEqual(revisions.scope_records(original,candidate_queue=self.fixture.queue,recorded_at=cutoff),self.report(cutoff))
            self.assertEqual(packet,history.export_history(restored_path,parent_database=self.parent,
                candidate_queue=self.fixture.queue,recorded_at=second['accepted_at']))
            third=self.accept(self.review(3,{self.accepted:'accept_in_scope',self.deferred:'accept_in_scope'}),second['ingestion_run_id'],3)
            self.assertEqual(0,third['new_candidates'])
            self.assertEqual(0,third['claims_created'])
        finally:
            self.db.close()
            self.db=original

    def test_cutoff_portable_export_excludes_future_review_and_materialization(self):
        old = history.export_history(self.fixture.root/'atlas.sqlite',parent_database=self.parent,
            candidate_queue=self.fixture.queue,recorded_at=self.genesis.accepted_at)
        self.accept(self.review(1,{self.deferred:'accept_in_scope'}),self.genesis.ingestion_run_id,1)
        after = history.export_history(self.fixture.root/'atlas.sqlite',parent_database=self.parent,
            candidate_queue=self.fixture.queue,recorded_at=self.genesis.accepted_at)
        self.assertEqual(old,after)
        before = history.export_history(self.fixture.root/'atlas.sqlite',parent_database=self.parent,
            candidate_queue=self.fixture.queue,recorded_at='2026-07-20T18:00:00Z')
        packet_path=self.fixture.root/'before.json'
        packet_path.write_bytes(revisions._raw(before))
        restored=history.restore_history(self.parent,history_file=packet_path,
            output_database=self.fixture.root/'before.sqlite')
        self.assertEqual(0,restored['restored_runs'])
        self.assertIsNone(restored['head_run_id'])

    def test_restore_rejects_tampered_chain_without_publishing_output(self):
        result=self.accept(self.review(1,{}),self.genesis.ingestion_run_id,1)
        packet=history.export_history(self.fixture.root/'atlas.sqlite',parent_database=self.parent,
            candidate_queue=self.fixture.queue,recorded_at=result['accepted_at'])
        rows=packet['tables']['ingestion_runs']
        column=rows['columns'].index('parameters_json')
        revised=next(row for row in rows['rows'] if row[rows['columns'].index('id')]==result['ingestion_run_id'])
        parameters=json.loads(revised[column])
        parameters['scope_revision']['previous_run_sha256']='0'*64
        revised[column]=fixtures._canonical(parameters).decode()
        packet['tables_sha256']=revisions._hash(packet['tables'])
        packet_path=self.fixture.root/'tampered.json'
        packet_path.write_bytes(revisions._raw(packet))
        output=self.fixture.root/'must-not-exist.sqlite'
        with self.assertRaisesRegex(ValueError,'predecessor binding'):
            history.restore_history(self.parent,history_file=packet_path,output_database=output)
        self.assertFalse(output.exists())

    def test_legacy_and_all_deferred_genesis_are_preserved(self):
        original_db=self.db
        original_fixture_db=self.fixture.connection
        for mode in ('legacy','all-deferred'):
            with self.subTest(mode=mode):
                self.db,_=initialize(self.fixture.root/(mode+'.sqlite'))
                self.fixture.connection=self.db
                try:
                    if mode=='legacy':
                        self.fixture._seed_legacy_v1()
                    else:
                        self.fixture._import(review=fixtures._review(self.fixture.queue,
                            {row.facility_inspire_id:'defer' for row in self.fixture.queue.candidates}))
                    before=self.rows()
                    genesis=revisions.validate_history(self.db,candidate_queue=self.fixture.queue)['head_run_id']
                    result=self.accept(self.review(1,{self.deferred:'accept_in_scope'}),genesis,1)
                    self.assertEqual(1 if mode=='legacy' else 2,result['new_candidates'])
                    for table,rows in before.items():
                        self.assertTrue(all(row in self.rows()[table] for row in rows),table)
                finally:
                    self.db.close()
                    self.db=original_db
                    self.fixture.connection=original_fixture_db

    def test_clock_regression_and_postwrite_clock_failure_roll_back(self):
        review=self.review(1,{self.deferred:'accept_in_scope'})
        before=self.rows()
        for clocks in (
            ['2026-07-20T19:01:01Z','2026-07-20T19:01:00Z'],
            ['2026-07-20T19:01:01Z','2026-07-20T19:01:02Z','2026-07-20T19:01:01Z'],
        ):
            with self.subTest(clocks=clocks),patch.object(revisions,'_now',side_effect=clocks):
                with self.assertRaises(ValueError):
                    revisions.accept_revision(self.db,snapshot=self.fixture.snapshot,
                        candidate_queue=self.fixture.queue,review=review,
                        expected_predecessor_run_id=self.genesis.ingestion_run_id)
            self.assertEqual(before,self.rows())

    def test_cli_revision_scope_export_restore_and_no_overwrite(self):
        queue=self.fixture.root/'queue.json'
        review=self.fixture.root/'review.json'
        queue.write_bytes(self.fixture.queue.raw_bytes)
        review.write_bytes(self.review(1,{self.deferred:'accept_in_scope'}).raw_bytes)
        def invoke(args):
            stream=io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(0,cli.main(args))
            return json.loads(stream.getvalue())
        db=str(self.fixture.root/'atlas.sqlite')
        with patch.object(revisions,'_now',side_effect=[
                '2026-07-20T19:01:01Z','2026-07-20T19:01:02Z','2026-07-20T19:01:03Z']):
            result=invoke(['accept-eea-scope-revision','--database',db,
                '--snapshot',str(self.fixture.snapshot.root),'--candidate-queue',str(queue),
                '--review',str(review),'--expected-predecessor-run-id',self.genesis.ingestion_run_id,
                '--accept-now'])
        report=invoke(['eea-scope-report','--database',db,'--candidate-queue',str(queue),
                       '--recorded-at',result['accepted_at']])
        packet=self.fixture.root/'cli-history.json'
        invoke(['export-eea-scope-history','--database',db,'--parent-database',str(self.parent),
            '--candidate-queue',str(queue),'--recorded-at',result['accepted_at'],'--output',str(packet)])
        output=self.fixture.root/'cli-restored.sqlite'
        restored=invoke(['restore-eea-scope-history','--parent-database',str(self.parent),
            '--history',str(packet),'--output-database',str(output)])
        self.assertEqual(report['head_run_id'],restored['head_run_id'])
        saved=output.read_bytes()
        with self.assertRaisesRegex(ValueError,'already exists'):
            history.restore_history(self.parent,history_file=packet,output_database=output)
        self.assertEqual(saved,output.read_bytes())


if __name__=='__main__':
    unittest.main()
