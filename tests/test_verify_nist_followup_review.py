"""Synthetic correspondence contracts, not independent source adjudication."""
import copy
import json
from pathlib import Path
import unittest

from scripts import frozen_runtime as runtime
from scripts import verify_nist_followup_review as review


class FollowupDecisionTests(unittest.TestCase):
    def setUp(self):
        self.projects = [{'id': 'nist2024-d12-first', 'position': 12},
                         {'id': 'nist2024-d13-second', 'position': 13}]
        self.blocks = {'d1-b001': {'text': 'Synthetic funding finalization.'},
                       'd2-b001': {'text': 'Synthetic unrelated research.'}}
        self.parents = {'d12-b001': {'text': 'Synthetic original project.'},
                        'd13-b001': {'text': 'Synthetic second project.'}}
        self.documents = [{'position': pos, 'body': {}, 'narrative_span': {},
            'classification': classification, 'reason': 'Synthetic manual review.',
            'evidence': [f'd{pos}-b001'], 'links': []}
            for pos, classification in [(1, 'final_award'), (2, 'research')]]
        self.link = {'project_id': 'nist2024-d12-first', 'relation': 'funding_finalized',
                     'reason': 'Synthetic source-native correspondence, not a physical outcome.',
                     'evidence': ['d1-b001'], 'parent_evidence': ['d12-b001']}
        self.documents[0]['links'] = [self.link]

    def validate(self):
        return review.validate_decisions(self.documents, [1, 2], self.projects,
                                         self.blocks, self.parents)

    def test_unmatched_project_is_retained_without_negative_outcome(self):
        counts, screens = self.validate()
        self.assertEqual(2, counts['manufacturing_groups'])
        self.assertEqual(1, counts['groups_with_finalized_funding_link'])
        self.assertEqual(1, counts['groups_without_finalized_funding_link_in_this_source_set'])
        self.assertEqual([], screens['nist2024-d13-second'])
        self.assertEqual(0, counts['physical_outcomes_adjudicated'])
        self.assertEqual(0, counts['forecast_scores'])

    def test_distinct_project_does_not_count_as_funding_match(self):
        self.documents[0]['classification'] = 'new_pmt'
        self.link['relation'] = 'distinct_project_same_company'
        counts, _ = self.validate()
        self.assertEqual(0, counts['groups_with_finalized_funding_link'])
        self.assertEqual(2, counts['groups_without_finalized_funding_link_in_this_source_set'])

    def test_scope_amendment_does_not_count_as_original_funding_match(self):
        self.documents[0]['classification'] = 'final_awards_and_additional_scope'
        self.link['relation'] = 'additional_scope'
        counts, _ = self.validate()
        self.assertEqual({'additional_scope': 1}, counts['project_link_relations'])
        self.assertEqual(0, counts['groups_with_finalized_funding_link'])

    def test_missing_or_reordered_article_fails(self):
        for documents in (self.documents[:1], list(reversed(self.documents)),
                          [self.documents[0], self.documents[0]]):
            with self.subTest(documents=documents), self.assertRaises(ValueError):
                review.validate_decisions(documents, [1, 2], self.projects, self.blocks, self.parents)

    def test_empty_evidence_fails(self):
        for field in ('evidence', 'parent_evidence'):
            with self.subTest(field=field):
                original = self.link[field]
                self.link[field] = []
                with self.assertRaises(ValueError):
                    self.validate()
                self.link[field] = original

    def test_wrong_later_article_evidence_fails(self):
        self.link['evidence'] = ['d2-b001']
        with self.assertRaises(ValueError):
            self.validate()

    def test_wrong_parent_article_evidence_fails(self):
        self.link['parent_evidence'] = ['d13-b001']
        with self.assertRaises(ValueError):
            self.validate()

    def test_unknown_project_fails(self):
        self.link['project_id'] = 'nist2025-new-project'
        with self.assertRaises(ValueError):
            self.validate()

    def test_duplicate_project_link_fails(self):
        self.documents[0]['links'].append(copy.deepcopy(self.link))
        with self.assertRaises(ValueError):
            self.validate()

    def test_duplicate_parent_identity_fails(self):
        self.projects.append(copy.deepcopy(self.projects[0]))
        with self.assertRaises(ValueError):
            self.validate()

    def test_nonfinal_funding_article_fails(self):
        for classification in ('research', 'new_pmt', 'governance'):
            with self.subTest(classification=classification):
                self.documents[0]['classification'] = classification
                with self.assertRaises(ValueError):
                    self.validate()

    def test_additional_scope_requires_scope_amendment_class(self):
        self.link['relation'] = 'additional_scope'
        with self.assertRaises(ValueError):
            self.validate()

    def test_physical_outcome_or_capacity_fields_fail(self):
        for field in ('outcome', 'capacity', 'target', 'forecast_score'):
            with self.subTest(field=field):
                self.link[field] = 'unsupported'
                with self.assertRaises(ValueError):
                    self.validate()
                del self.link[field]

    def test_physical_outcome_relation_fails(self):
        for relation in ('production_started', 'cancelled', 'unknown', 'qualified'):
            with self.subTest(relation=relation):
                self.link['relation'] = relation
                with self.assertRaises(ValueError):
                    self.validate()

    def test_unknown_document_field_fails(self):
        self.documents[0]['physical_status'] = 'complete'
        with self.assertRaises(ValueError):
            self.validate()

    def test_article_decision_requires_its_own_evidence(self):
        self.documents[0]['evidence'] = ['d2-b001']
        with self.assertRaises(ValueError):
            self.validate()

    def test_empty_explanation_fails(self):
        self.link['reason'] = ' '
        with self.assertRaises(ValueError):
            self.validate()

    def test_validation_does_not_mutate_inputs(self):
        before = copy.deepcopy((self.documents, self.projects, self.blocks, self.parents))
        self.validate()
        self.assertEqual(before, (self.documents, self.projects, self.blocks, self.parents))


class FrozenLaterLeadRosterTests(unittest.TestCase):
    def test_all_frozen_manufacturing_groups_remain_in_research_screen(self):
        root = Path(__file__).resolve().parents[1]
        screen = json.loads((root / 'review_plans/2026-09-09-nist-pmt-later-evidence-leads-v1.json').read_bytes())
        descriptor = screen['population_review']
        raw = (root / descriptor['path']).read_bytes()
        self.assertEqual(descriptor['sha256'], runtime.digest(raw))
        self.assertEqual(descriptor['bytes'], len(raw))
        parent = json.loads(raw)
        projects = []
        for fragment in parent['fragments']:
            raw = (root / fragment['path']).read_bytes()
            self.assertEqual(fragment['sha256'], runtime.digest(raw))
            projects.extend(row['id'] for row in json.loads(raw)['projects']
                            if row['selection'] == 'included_manufacturing')
        self.assertEqual(45, len(projects))
        self.assertEqual(projects, [row['project_id'] for row in screen['cases']])
        source_ids = [row['id'] for row in screen['sources']]
        self.assertEqual(len(source_ids), len(set(source_ids)))
        referenced = set()
        for case in screen['cases']:
            self.assertEqual({'project_id', 'retained_nist_archive_positions', 'lead_source_ids',
                              'finding', 'next_action'}, set(case))
            self.assertTrue(case['finding'].strip())
            self.assertTrue(case['next_action'].strip())
            self.assertEqual(len(case['lead_source_ids']), len(set(case['lead_source_ids'])))
            referenced.update(case['lead_source_ids'])
        self.assertEqual(set(source_ids), referenced)
        for source in screen['sources']:
            self.assertEqual('not_retained_in_this_screen', source['retention_status'])
            self.assertTrue(source['url'].startswith('https://'))
            self.assertIn(source['rights_status'], {'fresh_exact_source_review_required',
                'derivative_use_unapproved_no_capture', 'issuer_copying_restriction_no_capture'})


if __name__ == '__main__':
    unittest.main()
