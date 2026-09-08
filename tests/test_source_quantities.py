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


def wafer_area(**changes):
    return replace(quantity(metric='produced_wafer_area', unit='square_centimeters',
        scope_kind='source_native_site', scope_label='Issuer-reported site; no fab allocation',
        period_kind='fiscal_year', period_start='2023-10-01', period_end='2024-09-30',
        source_period_label='Geschäftsjahr 2023/24', source_number_literal='128.4',
        source_unit_scale=1000000, numeric_qualifier='as_reported_precision_unspecified',
        source_definition='Produced wafer area, not wafer count, capacity or qualified output.'), **changes)


class SourceQuantityTests(unittest.TestCase):
    def setUp(self):
        self.c, _ = database.initialize(':memory:')
        self.addCleanup(self.c.close)
        r.add_source_family(self.c, m.SourceFamily('family', 'fixture', 'Fixture', CREATED))
        r.add_source(self.c, m.Source('source', 'family', 'fixture', 'Fixture', 'Issuer', 'https://example.test', CREATED))
        r.add_source_document(self.c, m.SourceDocument('doc', 'source', 'https://example.test/filing',
            'Synthetic source', CREATED, 'a' * 64))
        r.add_entity(self.c, m.Entity('company', m.EntityKind.ORGANIZATION, 'issuer:aggregate', CREATED))
        r.add_entity(self.c, m.Entity('site', m.EntityKind.SITE, 'issuer:site', CREATED))

    def insert(self, metadata=None, recorded=ADMITTED, subject='company', annotate=True, **claim_changes):
        metadata = metadata or quantity()
        key = metadata.series_key(subject)
        series = r.stable_id(key)
        r.add_claim_series(self.c, m.ClaimSeries(series, subject, key, 'source_quantity.' + metadata.metric,
            m.ValueKind.SCALAR, CREATED))
        version = replace(m.ClaimVersion(metadata.claim_version_id, series, None, recorded,
            m.ClaimKind.SOURCE_STATEMENT, 'synthetic_source_quantity', None), **claim_changes)
        r.insert_claim(self.c, version, metadata.scalar(), evidence=[m.EvidenceLink('doc', locator='complete fixture')])
        if annotate:
            add_metadata(self.c, metadata)
        return metadata

    def insert_raw_metadata(self, metadata, **changes):
        fields = {**asdict(metadata), **changes}
        self.c.execute(f"INSERT INTO source_quantity_metadata ({','.join(fields)}) "
                       f"VALUES ({','.join('?' for _ in fields)})", tuple(fields.values()))

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

    def test_fiscal_year_area_site_query_hydration_and_no_physical_capacity(self):
        metadata = self.insert(wafer_area(), subject='site')
        value = service.claim_value(self.c, metadata.claim_version_id, 'scalar')
        self.assertEqual(value['value'], 128400000)
        self.assertEqual(value['unit'], 'square_centimeters')
        expected = asdict(metadata)
        expected.pop('claim_version_id')
        self.assertEqual(value['source_quantity'], {**expected,
            'physical_capacity_basis': None,
            'numeric_interpretation': 'qualified_reference_number_not_unqualified_point_estimate'})
        self.assertEqual(service.source_claim_records(self.c, recorded_at=ADMITTED)[0]['value'], value)
        self.assertEqual(service.claim_history_records(self.c, as_of='2026-09-08', recorded_at=ADMITTED)[0]['value'], value)
        self.assertEqual(service.claim_records(self.c, as_of='2026-09-08', recorded_at=LATER), [])
        self.assertEqual(r.validate_database(self.c), [])
        self.assertEqual(self.c.execute('SELECT count(*) FROM capacity_values').fetchone()[0], 0)

    def test_fiscal_years_include_leap_boundaries_and_retain_labels(self):
        for first, last in [('2023-03-01', '2024-02-29'), ('2024-03-01', '2025-02-28'),
                            ('1899-03-01', '1900-02-28'), ('1999-03-01', '2000-02-29'),
                            ('2023-02-01', '2024-01-31'), ('2023-12-01', '2024-11-30')]:
            with self.subTest(first=first):
                item = replace(wafer_area(), period_start=first, period_end=last)
                self.assertEqual(item.source_period_label, 'Geschäftsjahr 2023/24')
                self.assertEqual(item.period_kind, 'fiscal_year')
                self.insert(replace(item, claim_version_id='fiscal-' + first), subject='site')

    def test_fiscal_years_reject_partial_or_duplicate_calendar_periods(self):
        for changes in [dict(period_start='2023-10-02'), dict(period_end='2024-09-29'),
                        dict(period_end='2024-10-01'), dict(period_end='2023-09-30'),
                        dict(period_start='2023-01-01', period_end='2023-12-31'),
                        dict(period_start='2023-10-01', period_end='2023-12-31'),
                        dict(period_start='2023-03-01', period_end='2024-02-28'),
                        dict(period_start='2024-03-01', period_end='2025-02-29'),
                        dict(period_start='9999-10-01', period_end='9999-12-31'),
                        dict(period_kind='year'), dict(period_kind='quarter')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(wafer_area(), **changes)

    def test_area_and_existing_metrics_have_exclusive_units_and_diameters(self):
        for metric in ('wafer_capacity', 'wafer_shipments', 'wafer_output', 'capacity_utilization',
                       'produced_wafer_area'):
            for unit in ('12_inch_equivalent_wafers', 'native_wafers', 'percent', 'square_centimeters'):
                expected = ((metric in ('wafer_capacity', 'wafer_shipments', 'wafer_output')
                             and unit in ('12_inch_equivalent_wafers', 'native_wafers'))
                            or (metric == 'capacity_utilization' and unit == 'percent')
                            or (metric == 'produced_wafer_area' and unit == 'square_centimeters'))
                with self.subTest(metric=metric, unit=unit):
                    changes = dict(metric=metric, unit=unit, source_number_literal='17', source_unit_scale=1,
                                   native_wafer_diameter_inches=8 if unit == 'native_wafers' else None)
                    if expected:
                        self.assertEqual(quantity(**changes).scalar().unit, unit)
                    else:
                        with self.assertRaises(ValueError): quantity(**changes)
        for diameter in (6, 8, 12, True):
            with self.subTest(diameter=diameter), self.assertRaises(ValueError):
                replace(wafer_area(), native_wafer_diameter_inches=diameter)

    def test_area_metadata_retains_immutable_and_wrong_subject_guards(self):
        metadata = self.insert(wafer_area(), subject='site')
        self.assertFalse(add_metadata(self.c, metadata))
        for statement in ('UPDATE source_quantity_metadata SET source_period_label="changed"',
                          'DELETE FROM source_quantity_metadata'):
            with self.assertRaises(sqlite3.IntegrityError): self.c.execute(statement)
        for scope in ('company_aggregate', 'source_native_facility', 'source_native_project'):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                with r._atomic(self.c):
                    self.insert(replace(metadata, claim_version_id=scope, scope_kind=scope), subject='site')
        with self.assertRaises(ValueError):
            with r._atomic(self.c): self.insert(replace(metadata, claim_version_id='wrong-subject'))

    def test_schema8_direct_sql_enforces_site_scope_and_fiscal_bounds(self):
        metadata = self.insert(wafer_area(), subject='site', annotate=False)
        mutations = [dict(scope_kind='source_native_facility'), dict(native_wafer_diameter_inches=12),
            dict(period_start='2023-01-01', period_end='2023-12-31'),
            dict(period_start='2023-10-02'), dict(period_end='2024-10-01'),
            dict(period_start='2023-13-01'), dict(period_start='0000-10-01', period_end='0001-09-30'),
            dict(period_start='9999-10-01', period_end='10000-09-30'),
            dict(period_start='2023-03-01', period_end='2024-02-28')]
        for changes in mutations:
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                self.insert_raw_metadata(metadata, **changes)
        self.insert_raw_metadata(metadata)
        self.assertEqual(validate_quantities(self.c), [])

    def test_schema8_direct_sql_keeps_metric_unit_guards(self):
        for metric in ('wafer_capacity', 'wafer_shipments', 'wafer_output', 'capacity_utilization',
                       'produced_wafer_area'):
            for unit in ('12_inch_equivalent_wafers', 'native_wafers', 'percent', 'square_centimeters'):
                expected = ((metric in ('wafer_capacity', 'wafer_shipments', 'wafer_output')
                             and unit in ('12_inch_equivalent_wafers', 'native_wafers'))
                            or (metric == 'capacity_utilization' and unit == 'percent')
                            or (metric == 'produced_wafer_area' and unit == 'square_centimeters'))
                identifier = metric + '-' + unit
                with self.subTest(metric=metric, unit=unit):
                    r.add_claim_series(self.c, m.ClaimSeries(identifier, 'company', identifier,
                        'source_quantity.' + metric, m.ValueKind.SCALAR, CREATED))
                    r.insert_claim(self.c, m.ClaimVersion(identifier, identifier, None, ADMITTED,
                        m.ClaimKind.SOURCE_STATEMENT, 'synthetic_source_quantity', None),
                        m.ScalarValue(m.ScalarType.INTEGER, 17, unit), evidence=[m.EvidenceLink('doc')])
                    changes = dict(claim_version_id=identifier, metric=metric, unit=unit,
                                   native_wafer_diameter_inches=8 if unit == 'native_wafers' else None,
                                   source_number_literal='17', source_unit_scale=1)
                    if expected:
                        self.insert_raw_metadata(quantity(), **changes)
                    else:
                        with self.assertRaises(sqlite3.IntegrityError):
                            self.insert_raw_metadata(quantity(), **changes)

    def test_schema8_direct_sql_rejects_area_with_physical_or_effective_claim_semantics(self):
        metadata = wafer_area()
        for index, changes in enumerate((dict(valid_from='2023-10-01'), dict(confidence=0.8),
                                        dict(claim_kind=m.ClaimKind.DIRECT_OBSERVATION, valid_from='2023-10-01'))):
            with self.subTest(changes=changes):
                item = replace(metadata, claim_version_id=f'bad-semantics-{index}',
                               scope_label=f'Synthetic source site scope {index}')
                self.insert(item, subject='site', annotate=False, **changes)
                with self.assertRaises(sqlite3.IntegrityError): self.insert_raw_metadata(item)

    def test_existing_series_identity_stays_exact_and_new_dimensions_are_distinct(self):
        self.assertEqual(quantity().series_key('company'),
            'source-quantity-v1:c33846587ac2e843a1e571179995747eacdaf6863d0ae0741b82496bc2c1ef8b')
        self.assertEqual(quantity(source_number_literal='18', source_unit_scale=1,
                                  source_period_label='FY2025').series_key('company'),
                         quantity().series_key('company'))
        first = wafer_area()
        variants = [replace(first, scope_kind='source_native_facility'),
                    replace(first, period_kind='year', period_start='2024-01-01', period_end='2024-12-31'),
                    replace(first, period_start='2023-04-01', period_end='2024-03-31'),
                    replace(first, metric='wafer_output', unit='12_inch_equivalent_wafers')]
        self.assertEqual(len({first.series_key('site'), *(x.series_key('site') for x in variants)}), 5)

    def test_schema8_migration_preserves_all_existing_rows_and_series_keys(self):
        old, _ = database.initialize(':memory:', target_version=7)
        self.addCleanup(old.close)
        new = self.c
        self.c = old
        try:
            r.add_source_family(old, m.SourceFamily('family', 'fixture', 'Fixture', CREATED))
            r.add_source(old, m.Source('source', 'family', 'fixture', 'Fixture', 'Issuer', 'https://example.test', CREATED))
            r.add_source_document(old, m.SourceDocument('doc', 'source', 'https://example.test/filing',
                'Synthetic source', CREATED, 'a' * 64))
            r.add_entity(old, m.Entity('company', m.EntityKind.ORGANIZATION, 'issuer:aggregate', CREATED))
            self.insert()
            self.insert(quantity(claim_version_id='native', unit='native_wafers', native_wafer_diameter_inches=8))
            self.insert(quantity(claim_version_id='percent', metric='capacity_utilization', unit='percent',
                                 source_unit_scale=1, source_number_literal='75.2'))
            for entity_id, entity_kind, scope, metric in (
                    ('facility', m.EntityKind.FACILITY, 'source_native_facility', 'wafer_output'),
                    ('project', m.EntityKind.PROJECT, 'source_native_project', 'wafer_shipments')):
                r.add_entity(old, m.Entity(entity_id, entity_kind, 'issuer:' + entity_id, CREATED))
                self.insert(quantity(claim_version_id=entity_id, scope_kind=scope, metric=metric,
                                     period_kind='quarter', period_start='2025-10-01', period_end='2025-12-31'),
                            subject=entity_id)
            old.commit()
            tables = [row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'schema_migrations'")]
            before = {table: [tuple(row) for row in old.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                      for table in tables}
            old_views = service.source_claim_records(old, recorded_at=ADMITTED)
            self.assertEqual(database.apply_migrations(old), [8])
            self.assertEqual(database.schema_version(old), 8)
            self.assertEqual(database.apply_migrations(old), [])
            for table, rows in before.items():
                with self.subTest(table=table):
                    self.assertEqual([tuple(row) for row in old.execute(f'SELECT * FROM "{table}" ORDER BY rowid')], rows)
            self.assertEqual(service.source_claim_records(old, recorded_at=ADMITTED), old_views)
            self.assertEqual(r.validate_database(old), [])
            self.assertEqual(old.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(old.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(old.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            for statement in ('UPDATE source_quantity_metadata SET source_period_label="changed"',
                              'DELETE FROM source_quantity_metadata'):
                with self.assertRaises(sqlite3.IntegrityError): old.execute(statement)
        finally:
            self.c = new


if __name__ == '__main__': unittest.main()
