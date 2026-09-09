"""Source rate references retain wording without becoming physical capacity."""

import sqlite3
import unittest
from dataclasses import asdict, replace

from semiconductor_atlas import database, models as m, release, repository as r, service
from semiconductor_atlas.source_quantities import add_metadata as add_quantity_metadata
from semiconductor_atlas.source_rates import (
    SourceRateMetadata,
    add_metadata,
    metadata_for_claims,
    validate_rates,
)
from tests.test_source_quantities import quantity, wafer_area


CREATED = '2026-01-01T00:00:00Z'
ADMITTED = '2026-09-08T23:00:00Z'
LATER = '2026-09-08T23:01:00Z'
WORDING = 'The company produces 21,000 wafer semiconductors per month.'


def rate(**changes):
    return replace(SourceRateMetadata(
        claim_version_id='rate',
        metric='wafer_production_rate',
        scope_kind='source_native_facility',
        scope_label='Synthetic source-native facility; identity unresolved',
        numerator_unit='native_wafers',
        denominator_unit='month',
        native_wafer_diameter_inches=None,
        reference_period_kind='not_stated',
        reference_period_start=None,
        reference_period_end=None,
        source_reference_period_literal=None,
        source_number_literal='21,000',
        numeric_qualifier='as_reported_precision_unspecified',
        source_qualifier_literal=None,
        assertion_kind='reported',
        source_wording=WORDING,
        source_definition='Reported wafer production rate; no measured output interval or physical capacity basis.'),
        **changes)


def starts(**changes):
    return replace(rate(metric='wafer_start_capacity_rate', numerator_unit='native_wafer_starts',
        native_wafer_diameter_inches=8,
        source_wording='The facility has capacity for 21,000 wafer starts per month.',
        source_definition='Source-reported wafer-start capacity rate; installed or qualified basis unstated.'),
        **changes)


class SourceRateTests(unittest.TestCase):
    def setUp(self):
        self.c, _ = database.initialize(':memory:', target_version=9)
        self.addCleanup(self.c.close)
        self.seed(self.c)

    def seed(self, connection):
        r.add_source_family(connection, m.SourceFamily('family', 'fixture', 'Fixture', CREATED))
        r.add_source(connection, m.Source('source', 'family', 'fixture', 'Fixture', 'Synthetic publisher',
            'https://example.test', CREATED))
        r.add_source_document(connection, m.SourceDocument('doc', 'source', 'https://example.test/source',
            'Synthetic evidence, not manufacturing truth', CREATED, 'a' * 64))
        for entity_id, kind in [('facility', m.EntityKind.FACILITY), ('company', m.EntityKind.ORGANIZATION),
                                ('site', m.EntityKind.SITE), ('project', m.EntityKind.PROJECT)]:
            r.add_entity(connection, m.Entity(entity_id, kind, 'fixture:' + entity_id, CREATED))

    def insert(self, metadata=None, *, subject='facility', annotate=True, recorded=ADMITTED,
               scalar=None, **claim_changes):
        metadata = metadata or rate()
        key = metadata.series_key(subject)
        series_id = r.stable_id(key)
        r.add_claim_series(self.c, m.ClaimSeries(series_id, subject, key,
            'source_rate.' + metadata.metric, m.ValueKind.SCALAR, CREATED))
        version = replace(m.ClaimVersion(metadata.claim_version_id, series_id, None, recorded,
            m.ClaimKind.SOURCE_STATEMENT, 'synthetic_source_rate', None), **claim_changes)
        r.insert_claim(self.c, version, scalar or metadata.scalar(),
            evidence=[m.EvidenceLink('doc', locator='Complete synthetic source statement')])
        if annotate:
            add_metadata(self.c, metadata)
        return metadata

    def raw_metadata(self, metadata, **changes):
        fields = {**asdict(metadata), **changes}
        self.c.execute(f"INSERT INTO source_rate_metadata ({','.join(fields)}) "
                       f"VALUES ({','.join('?' for _ in fields)})", tuple(fields.values()))

    def test_grouped_literal_and_exact_unqualified_wording_survive_all_source_views(self):
        metadata = self.insert()
        value = service.claim_value(self.c, 'rate', 'scalar')
        self.assertEqual(value['value'], 21000)
        self.assertEqual(value['unit'], 'native_wafers_per_month')
        expected = asdict(metadata)
        expected.pop('claim_version_id')
        self.assertEqual({key: value['source_rate'][key] for key in expected}, expected)
        self.assertIsNone(value['source_rate']['physical_capacity_basis'])
        self.assertEqual(value['source_rate']['source_wording'], WORDING)
        self.assertEqual(value['source_rate']['numeric_qualifier'], 'as_reported_precision_unspecified')
        self.assertIsNone(value['source_rate']['source_qualifier_literal'])
        self.assertIsNone(value['source_rate']['source_reference_period_literal'])
        self.assertEqual(service.source_claim_records(self.c, recorded_at=CREATED), [])
        self.assertEqual(service.source_claim_records(self.c, recorded_at=ADMITTED)[0]['value'], value)
        self.assertEqual(service.claim_history_records(self.c, as_of='2026-09-08', recorded_at=LATER)[0]['value'], value)
        self.assertEqual(metadata_for_claims(self.c, ['rate'])['rate'], value['source_rate'])
        self.assertEqual(r.validate_database(self.c), [])

    def test_qualifier_preserves_exact_phrase_without_claiming_an_exact_count(self):
        metadata = self.insert(rate(numeric_qualifier='approximate', source_qualifier_literal='approximately',
            source_wording='The facility produces approximately 21,000 wafers per month.'))
        value = service.claim_value(self.c, metadata.claim_version_id, 'scalar')
        self.assertEqual(value['source_rate']['source_number_literal'], '21,000')
        self.assertEqual(value['source_rate']['numeric_qualifier'], 'approximate')
        self.assertEqual(value['source_rate']['source_qualifier_literal'], 'approximately')
        for changes in [dict(source_qualifier_literal=None), dict(source_qualifier_literal='about'),
                        dict(source_wording=WORDING)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(metadata, **changes)

    def test_month_denominator_does_not_invent_an_observation_month(self):
        metadata = self.insert()
        self.assertEqual(metadata.denominator_unit, 'month')
        self.assertEqual(metadata.reference_period_kind, 'not_stated')
        self.assertIsNone(metadata.reference_period_start)
        self.assertIsNone(metadata.reference_period_end)
        for changes in [dict(reference_period_kind='month'), dict(reference_period_start='2025-01-01'),
                        dict(reference_period_end='2025-01-31'),
                        dict(reference_period_start='2025-01-01', reference_period_end='2025-01-31'),
                        dict(denominator_unit='year')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError): rate(**changes)

    def test_relative_source_clock_is_literal_context_with_no_resolved_interval(self):
        metadata = rate(source_reference_period_literal='currently',
            source_wording='The facility currently produces 21,000 wafers per month.')
        self.insert(metadata)
        self.assertIsNone(metadata.reference_period_start)
        self.assertIsNone(metadata.reference_period_end)
        with self.assertRaises(ValueError): rate(source_reference_period_literal='currently')

    def test_unknown_and_known_diameters_never_convert_the_native_rate(self):
        keys = set()
        for diameter in (None, 6, 8, 12):
            metadata = rate(claim_version_id='diameter-' + str(diameter),
                            native_wafer_diameter_inches=diameter)
            self.insert(metadata)
            keys.add(metadata.series_key('facility'))
            value = service.claim_value(self.c, metadata.claim_version_id, 'scalar')
            self.assertEqual((value['value'], value['unit']), (21000, 'native_wafers_per_month'))
            self.assertEqual(value['source_rate']['native_wafer_diameter_inches'], diameter)
            self.assertIsNone(value['source_rate']['physical_capacity_basis'])
        self.assertEqual(len(keys), 4)
        for changes in [dict(native_wafer_diameter_inches=0), dict(native_wafer_diameter_inches=True),
                        dict(native_wafer_diameter_inches=200), dict(native_wafer_diameter_inches='8'),
                        dict(numerator_unit='12_inch_equivalent_wafers')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError): rate(**changes)

    def test_start_capacity_and_wafer_production_remain_distinct(self):
        production = self.insert()
        capacity = self.insert(starts(claim_version_id='starts'))
        self.assertNotEqual(production.series_key('facility'), capacity.series_key('facility'))
        records = service.source_claim_records(self.c, recorded_at=ADMITTED)
        self.assertEqual({record['predicate'] for record in records},
            {'source_rate.wafer_production_rate', 'source_rate.wafer_start_capacity_rate'})
        self.assertEqual({record['value']['unit'] for record in records},
            {'native_wafers_per_month', 'native_wafer_starts_per_month'})
        for changes in [dict(metric='wafer_start_capacity_rate'), dict(numerator_unit='native_wafer_starts'),
                        dict(metric='wafer_capacity'), dict(metric='wafer_shipments'),
                        dict(metric='capacity_utilization')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError): rate(**changes)

    def test_qualifier_scope_and_assertion_kind_are_series_dimensions(self):
        first = rate()
        variants = [replace(first, scope_label='Another reported perimeter'),
            replace(first, scope_kind='source_native_site'), replace(first, assertion_kind='estimate'),
            replace(first, assertion_kind='guidance'),
            replace(first, numeric_qualifier='at_least', source_qualifier_literal='at least',
                    source_wording='The facility produces at least 21,000 wafers per month.')]
        self.assertEqual(len({first.series_key('facility'), *(item.series_key('facility') for item in variants)}),
                         len(variants) + 1)

    def test_numeric_revision_keeps_series_identity_and_both_knowledge_vintages(self):
        original = self.insert()
        revised = rate(claim_version_id='revision', source_number_literal='22,000',
                       source_wording=WORDING.replace('21,000', '22,000'))
        self.assertEqual(original.series_key('facility'), revised.series_key('facility'))
        self.insert(revised, recorded=LATER)
        early = service.source_claim_records(self.c, recorded_at=ADMITTED)
        late = service.source_claim_records(self.c, recorded_at=LATER)
        self.assertEqual(len(early), 1)
        self.assertEqual(len(late), 1)
        self.assertEqual(early[0]['value']['value'], 21000)
        self.assertEqual(early[0]['value']['source_rate']['source_wording'], WORDING)
        self.assertEqual(late[0]['value']['value'], 22000)
        self.assertEqual(late[0]['value']['source_rate']['source_wording'], revised.source_wording)
        self.assertEqual(len(service.claim_history_records(self.c, as_of='2026-09-08', recorded_at=LATER)), 2)
        self.assertEqual(r.validate_database(self.c), [])

    def test_invalid_grouping_and_number_substrings_do_not_become_source_numbers(self):
        for literal in ('21,00', '2,1000', ',21000', '021,000', '21 000', '1e3', '-1', 'NaN'):
            with self.subTest(literal=literal), self.assertRaises(ValueError):
                rate(source_number_literal=literal, source_wording=f'Produces {literal} wafers per month.')
        for wording in ('Produces 121,000 wafers per month.', 'Produces 21,0000 wafers per month.',
                        'Produces 21,000.5 wafers per month.'):
            with self.subTest(wording=wording), self.assertRaises(ValueError): rate(source_wording=wording)
        self.assertEqual(rate(source_number_literal='21000',
            source_wording='Produces 21000 wafers per month.').scalar().value, 21000)

    def test_metadata_is_mandatory_for_single_bulk_and_history_hydration(self):
        self.insert(annotate=False)
        self.assertTrue(validate_rates(self.c))
        self.assertTrue(r.validate_database(self.c))
        for call in [lambda: service.claim_value(self.c, 'rate', 'scalar'),
                     lambda: service.source_claim_records(self.c, recorded_at=LATER),
                     lambda: service.claim_history_records(self.c, as_of='2026-09-08', recorded_at=LATER)]:
            with self.assertRaises(ValueError): call()

    def test_metadata_is_idempotent_immutable_and_scalar_bound(self):
        metadata = self.insert()
        self.assertFalse(add_metadata(self.c, metadata))
        changed = replace(metadata, source_number_literal='22,000', source_wording=WORDING.replace('21,000', '22,000'))
        with self.assertRaises(ValueError): add_metadata(self.c, changed)
        for statement in ('UPDATE source_rate_metadata SET source_wording="changed"',
                          'DELETE FROM source_rate_metadata'):
            with self.assertRaises(sqlite3.IntegrityError): self.c.execute(statement)

    def test_validity_confidence_physical_kind_and_wrong_subject_are_rejected(self):
        for changes in [dict(valid_from='2025-01-01'), dict(confidence=0.8),
                        dict(claim_kind=m.ClaimKind.DIRECT_OBSERVATION, valid_from='2025-01-01')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                with r._atomic(self.c): self.insert(**changes)
        with self.assertRaises(ValueError):
            with r._atomic(self.c): self.insert(subject='company')
        self.assertEqual(self.c.execute('SELECT count(*) FROM claim_versions').fetchone()[0], 0)

    def test_scalar_number_or_unit_tampering_is_detected_by_hydration(self):
        for index, scalar in enumerate((m.ScalarValue(m.ScalarType.INTEGER, 42000, 'native_wafers_per_month'),
                                       m.ScalarValue(m.ScalarType.INTEGER, 21000, '12_inch_equivalent_wafers'))):
            with self.subTest(scalar=scalar):
                metadata = rate(claim_version_id=f'bad-scalar-{index}', scope_label=f'Synthetic scope {index}')
                self.insert(metadata, annotate=False, scalar=scalar)
                with self.assertRaises(ValueError): add_metadata(self.c, metadata)
                with self.assertRaises(ValueError):
                    service.claim_value(self.c, metadata.claim_version_id, 'scalar')

    def test_resealed_metadata_cannot_change_qualifier_or_literal_under_existing_series(self):
        self.insert()
        self.c.execute('DROP TRIGGER source_rate_metadata_immutable_update')
        self.c.execute("UPDATE source_rate_metadata SET source_number_literal='22,000', "
                       "source_wording='The company produces 22,000 wafer semiconductors per month.'")
        self.assertTrue(validate_rates(self.c))
        with self.assertRaises(ValueError): metadata_for_claims(self.c, ['rate'])
        with self.assertRaises(ValueError): service.source_claim_records(self.c, recorded_at=LATER)

    def test_deleted_metadata_never_degrades_to_an_unqualified_number(self):
        self.insert()
        self.c.execute('DROP TRIGGER source_rate_metadata_immutable_delete')
        self.c.execute('DELETE FROM source_rate_metadata')
        self.assertTrue(validate_rates(self.c))
        with self.assertRaises(ValueError): service.claim_value(self.c, 'rate', 'scalar')

    def test_direct_sql_enforces_unknown_reference_interval_and_source_claim_semantics(self):
        metadata = self.insert(annotate=False)
        for changes in [dict(reference_period_kind='month'), dict(reference_period_start='2025-01-01'),
                        dict(reference_period_end='2025-01-31'), dict(denominator_unit='year'),
                        dict(numerator_unit='native_wafer_starts'), dict(native_wafer_diameter_inches=200),
                        dict(scope_kind='company_aggregate')]:
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                self.raw_metadata(metadata, **changes)
        self.raw_metadata(metadata)
        self.assertEqual(validate_rates(self.c), [])

    def test_direct_sql_cannot_add_rate_metadata_to_an_effective_or_confident_claim(self):
        for index, changes in enumerate([dict(valid_from='2025-01-01'), dict(confidence=0.8),
                                       dict(claim_kind=m.ClaimKind.DIRECT_OBSERVATION, valid_from='2025-01-01')]):
            metadata = rate(claim_version_id=f'bad-{index}', scope_label=f'Synthetic facility scope {index}')
            self.insert(metadata, annotate=False, **changes)
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                self.raw_metadata(metadata)

    def test_source_rates_do_not_leak_to_current_physical_views_or_capacity_exports(self):
        self.insert()
        self.insert(starts(claim_version_id='starts'))
        source_records = service.source_claim_records(self.c, recorded_at=LATER)
        self.assertEqual(len(source_records), 2)
        for as_of in ('2020-01-01', '2026-09-08', '2030-01-01'):
            self.assertEqual(service.claim_records(self.c, as_of=as_of, recorded_at=LATER), [])
        self.assertEqual(release._capacity_rows(source_records), [])
        self.assertEqual(release._capacity_rows(service.claim_history_records(
            self.c, as_of='2026-09-08', recorded_at=LATER)), [])
        self.assertEqual(self.c.execute('SELECT count(*) FROM capacity_values').fetchone()[0], 0)
        rows = self.c.execute('SELECT claim_kind,valid_from,valid_to,confidence FROM claim_versions').fetchall()
        self.assertEqual([tuple(row) for row in rows], [('source_statement', None, None, None)] * 2)

    def test_schema9_preserves_all_prior_rows_quantity_keys_and_hydrated_values(self):
        old, _ = database.initialize(':memory:', target_version=8)
        self.addCleanup(old.close)
        self.seed(old)
        old_items = [(quantity(), 'company'), (wafer_area(claim_version_id='area'), 'site'),
            (quantity(claim_version_id='native', unit='native_wafers', native_wafer_diameter_inches=8), 'company')]
        for metadata, subject in old_items:
            key = metadata.series_key(subject)
            series_id = r.stable_id(key)
            r.add_claim_series(old, m.ClaimSeries(series_id, subject, key,
                'source_quantity.' + metadata.metric, m.ValueKind.SCALAR, CREATED))
            r.insert_claim(old, m.ClaimVersion(metadata.claim_version_id, series_id, None, ADMITTED,
                m.ClaimKind.SOURCE_STATEMENT, 'synthetic_source_quantity', None), metadata.scalar(),
                evidence=[m.EvidenceLink('doc')])
            add_quantity_metadata(old, metadata)
        self.assertEqual(quantity().series_key('company'),
            'source-quantity-v1:c33846587ac2e843a1e571179995747eacdaf6863d0ae0741b82496bc2c1ef8b')
        old.commit()
        before = {name: [tuple(row) for row in old.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                  for name, in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        values = service.source_claim_records(old, recorded_at=LATER)
        self.assertEqual(metadata_for_claims(old, ['quantity', 'area', 'native']), {})
        self.assertEqual(database.apply_migrations(old, target_version=9), [9])
        self.assertEqual(database.apply_migrations(old, target_version=9), [])
        for table, expected in before.items():
            actual = [tuple(row) for row in old.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            with self.subTest(table=table):
                self.assertEqual(actual[:len(expected)] if table == 'schema_migrations' else actual, expected)
        self.assertEqual(service.source_claim_records(old, recorded_at=LATER), values)
        self.assertEqual(old.execute('SELECT count(*) FROM source_rate_metadata').fetchone()[0], 0)
        self.assertEqual(r.validate_database(old), [])
        self.assertEqual(old.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(old.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(old.execute('PRAGMA foreign_keys').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
