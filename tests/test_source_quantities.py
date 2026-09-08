"""Typed source quantities retain their qualifiers and cannot become physical capacity."""

import json
import sqlite3
import unittest
from dataclasses import asdict, replace

from semiconductor_atlas import database, models as m, repository as r, service
from semiconductor_atlas.source_quantities import SourceQuantityMetadata, add_metadata, metadata_for_claims, validate_quantities


CREATED = '2026-01-01T00:00:00Z'
ADMITTED = '2026-09-08T20:00:00Z'
LATER = '2026-09-08T20:01:00Z'


def quantity(**changes):
    return replace(SourceQuantityMetadata('quantity', 'wafer_capacity', 'company_aggregate',
        'Issuer-reported aggregate; no facility allocation', 'year', '2025-01-01', '2025-12-31',
        '2025', '12_inch_equivalent_wafers', None, '17', 1000000, 'greater_than', 'reported',
        'Annual capacity; physical basis unstated.'), **changes)


class SourceQuantityTests(unittest.TestCase):
    def setUp(self):
        self.c, _ = database.initialize(':memory:')
        self.addCleanup(self.c.close)
        r.add_source_family(self.c, m.SourceFamily('family', 'fixture', 'Fixture', CREATED))
        r.add_source(self.c, m.Source('source', 'family', 'fixture', 'Fixture', 'Issuer', 'https://example.test', CREATED))
        r.add_source_document(self.c, m.SourceDocument('doc', 'source', 'https://example.test/filing',
            'Synthetic source', CREATED, 'a' * 64))
        r.add_entity(self.c, m.Entity('company', m.EntityKind.ORGANIZATION, 'issuer:aggregate', CREATED))

    def insert(self, metadata=None, recorded=ADMITTED, **claim_changes):
        metadata = metadata or quantity()
        key = metadata.series_key('company')
        series = r.stable_id(key)
        r.add_claim_series(self.c, m.ClaimSeries(series, 'company', key, 'source_quantity.' + metadata.metric,
            m.ValueKind.SCALAR, CREATED))
        version = replace(m.ClaimVersion(metadata.claim_version_id, series, None, recorded,
            m.ClaimKind.SOURCE_STATEMENT, 'synthetic_source_quantity', None), **claim_changes)
        r.insert_claim(self.c, version, metadata.scalar(), evidence=[m.EvidenceLink('doc', locator='complete fixture')])
        add_metadata(self.c, metadata)
        return metadata

    def test_lower_bound_and_scale_survive_single_bulk_source_and_history_views(self):
        self.insert()
        value = service.claim_value(self.c, 'quantity', 'scalar')
        self.assertEqual(value['value'], 17000000)
        self.assertEqual(value['source_quantity']['numeric_qualifier'], 'greater_than')
        self.assertIsNone(value['source_quantity']['physical_capacity_basis'])
        self.assertEqual(value['source_quantity']['source_number_literal'], '17')
        self.assertEqual(value['source_quantity']['source_unit_scale'], 1000000)
        self.assertEqual(service.source_claim_records(self.c, recorded_at=CREATED), [])
        self.assertEqual(service.source_claim_records(self.c, recorded_at=ADMITTED)[0]['value'], value)
        self.assertEqual(service.claim_history_records(self.c, as_of='2026-09-08', recorded_at=ADMITTED)[0]['value'], value)
        self.assertEqual(service.claim_records(self.c, as_of='2026-09-08', recorded_at=LATER), [])
        self.assertEqual(r.validate_database(self.c), [])
        self.assertEqual(self.c.execute('SELECT count(*) FROM capacity_values').fetchone()[0], 0)

    def test_semantic_dimensions_do_not_collapse_period_unit_scope_metric_or_estimate_kind(self):
        first = quantity()
        variants = [replace(first, period_start='2024-01-01', period_end='2024-12-31', source_period_label='2024'),
            replace(first, period_kind='quarter', period_start='2025-01-01', period_end='2025-03-31'),
            replace(first, unit='native_wafers', native_wafer_diameter_inches=8),
            replace(first, unit='native_wafers', native_wafer_diameter_inches=12),
            replace(first, metric='wafer_shipments'), replace(first, scope_label='Another consolidation perimeter'),
            replace(first, assertion_kind='guidance'), replace(first, numeric_qualifier='approximate'),
            replace(first, source_definition='A different accounting definition')]
        keys = {first.series_key('company'), *(q.series_key('company') for q in variants)}
        self.assertEqual(len(keys), len(variants) + 1)
        self.insert(first)
        for index, item in enumerate(variants):
            self.insert(replace(item, claim_version_id=f'other-{index}'))
        self.assertEqual(len(service.source_claim_records(self.c, recorded_at=ADMITTED)), len(variants) + 1)

    def test_same_semantics_revision_preserves_both_knowledge_vintages(self):
        self.insert()
        revised = quantity(claim_version_id='revision', source_number_literal='18')
        self.insert(revised, recorded=LATER)
        self.assertEqual(service.source_claim_records(self.c, recorded_at=ADMITTED)[0]['value']['value'], 17000000)
        self.assertEqual(service.source_claim_records(self.c, recorded_at=LATER)[0]['value']['value'], 18000000)
        self.assertEqual(len(service.claim_history_records(self.c, as_of='2026-09-08', recorded_at=LATER)), 2)

    def test_invalid_periods_numbers_units_and_qualifiers_fail(self):
        mutations = [dict(period_start='2025-02-01'), dict(period_end='2026-01-01'), dict(period_kind='month'),
            dict(source_number_literal='17,000'), dict(source_number_literal='NaN'), dict(source_number_literal='1e6'),
            dict(source_number_literal='-1'), dict(source_unit_scale=True), dict(source_unit_scale=100),
            dict(numeric_qualifier='exact_physical_capacity'), dict(assertion_kind='qualified'),
            dict(unit='native_wafers'), dict(native_wafer_diameter_inches=12), dict(unit='percent'),
            dict(metric='capacity_utilization'), dict(scope_kind='canonical_fab'), dict(scope_label='')]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): quantity(**mutation)

    def test_native_diameter_and_decimal_percent_are_explicit(self):
        self.assertEqual(quantity(unit='native_wafers', native_wafer_diameter_inches=8).scalar().value, 17000000)
        percentage = quantity(metric='capacity_utilization', unit='percent', source_number_literal='75.2',
                              source_unit_scale=1, numeric_qualifier='as_reported_precision_unspecified')
        self.assertEqual(percentage.scalar().value, 75.2)
        with self.assertRaises(ValueError): replace(percentage, source_number_literal='101')

    def test_metadata_is_immutable_idempotent_and_bound_to_scalar(self):
        metadata = self.insert()
        self.assertFalse(add_metadata(self.c, metadata))
        with self.assertRaises(ValueError): add_metadata(self.c, replace(metadata, source_number_literal='18'))
        for statement in ('UPDATE source_quantity_metadata SET numeric_qualifier="approximate"',
                          'DELETE FROM source_quantity_metadata'):
            with self.assertRaises(sqlite3.IntegrityError): self.c.execute(statement)

    def test_effective_time_confidence_and_physical_observation_are_rejected(self):
        for mutation in (dict(valid_from='2025-01-01'), dict(confidence=0.9),
                         dict(claim_kind=m.ClaimKind.DIRECT_OBSERVATION, valid_from='2025-01-01')):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                with r._atomic(self.c): self.insert(**mutation)
        self.assertEqual(self.c.execute('SELECT count(*) FROM claim_versions').fetchone()[0], 0)

    def test_missing_metadata_never_returns_an_unqualified_scalar(self):
        self.insert()
        self.c.execute('DROP TRIGGER source_quantity_metadata_immutable_delete')
        self.c.execute('DELETE FROM source_quantity_metadata')
        self.assertTrue(validate_quantities(self.c))
        with self.assertRaises(ValueError): service.claim_value(self.c, 'quantity', 'scalar')
        with self.assertRaises(ValueError): service.source_claim_records(self.c, recorded_at=LATER)

    def test_metadata_mutation_is_detected_even_if_scalar_is_unchanged(self):
        self.insert()
        self.c.execute('DROP TRIGGER source_quantity_metadata_immutable_update')
        self.c.execute('UPDATE source_quantity_metadata SET numeric_qualifier="approximate"')
        self.assertTrue(validate_quantities(self.c))
        with self.assertRaises(ValueError): metadata_for_claims(self.c, ['quantity'])

    def test_legacy_schema_and_unannotated_legacy_scalars_keep_their_shape(self):
        old, _ = database.initialize(':memory:', target_version=6)
        try: self.assertEqual(metadata_for_claims(old, []), {})
        finally: old.close()
        r.add_claim_series(self.c, m.ClaimSeries('legacy', 'company', 'legacy', 'legacy_count', m.ValueKind.SCALAR, CREATED))
        r.insert_claim(self.c, m.ClaimVersion('legacy', 'legacy', None, ADMITTED, m.ClaimKind.SOURCE_STATEMENT,
            'fixture', None), m.ScalarValue(m.ScalarType.INTEGER, 3), evidence=[m.EvidenceLink('doc')])
        self.assertEqual(service.claim_value(self.c, 'legacy', 'scalar'), {'scalar_type': 'integer', 'value': 3, 'unit': None})


if __name__ == '__main__': unittest.main()
