"""Source-reported numeric references with mandatory scope, units and qualifiers.

These annotate numeric source scalars. They are neither physical capacity values
nor measured outcomes, and do not support inference of missing physical bases.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal

from . import models


METRICS = {'wafer_capacity', 'wafer_shipments', 'wafer_output', 'capacity_utilization',
           'produced_wafer_area'}
SCOPES = {'company_aggregate': 'organization', 'source_native_facility': 'facility',
          'source_native_project': 'project', 'source_native_site': 'site'}
QUALIFIERS = {'as_reported_precision_unspecified', 'approximate', 'greater_than', 'at_least',
              'less_than', 'at_most'}


@dataclass(frozen=True, slots=True)
class SourceQuantityMetadata:
    claim_version_id: str
    metric: str
    scope_kind: str
    scope_label: str
    period_kind: str
    period_start: str
    period_end: str
    source_period_label: str
    unit: str
    native_wafer_diameter_inches: int | None
    source_number_literal: str
    source_unit_scale: int
    numeric_qualifier: str
    assertion_kind: str
    source_definition: str

    def __post_init__(self):
        for field in ('claim_version_id', 'scope_label', 'source_period_label', 'source_definition'):
            models._required(getattr(self, field), field)
            if len(getattr(self, field)) > 2000:
                raise ValueError('source quantity text exceeds bound')
        if self.metric not in METRICS or self.scope_kind not in SCOPES:
            raise ValueError('unsupported source quantity metric or accounting scope')
        if self.numeric_qualifier not in QUALIFIERS or self.assertion_kind not in {'reported', 'estimate', 'guidance'}:
            raise ValueError('unsupported source quantity qualifier or assertion kind')
        for field in ('period_start', 'period_end'):
            models._iso_date(getattr(self, field), field)
        first, last = date.fromisoformat(self.period_start), date.fromisoformat(self.period_end)
        if self.period_kind == 'year':
            valid = first.month == 1 and first.day == 1 and last == date(first.year, 12, 31)
        elif self.period_kind == 'quarter':
            valid = first.month in (1, 4, 7, 10) and first.day == 1 and last == date(
                first.year, first.month + 2, calendar.monthrange(first.year, first.month + 2)[1])
        elif self.period_kind == 'fiscal_year':
            valid = (first.month != 1 and first.day == 1 and first.year < 9999
                     and last == date(first.year + 1, first.month, 1) - timedelta(days=1))
        else:
            valid = False
        if not valid:
            raise ValueError('source quantity period must be one complete calendar year, calendar quarter, or non-calendar fiscal year')
        if self.unit not in {'native_wafers', '12_inch_equivalent_wafers', 'percent', 'square_centimeters'}:
            raise ValueError('unsupported source quantity unit')
        diameter = self.native_wafer_diameter_inches
        if ((self.unit == 'native_wafers' and (type(diameter) is not int or diameter not in (6, 8, 12)))
                or (self.unit != 'native_wafers' and diameter is not None)):
            raise ValueError('native wafer diameter must match the native-unit contract')
        if (self.metric == 'capacity_utilization') != (self.unit == 'percent'):
            raise ValueError('utilization and wafer quantities have distinct units')
        if (self.metric == 'produced_wafer_area') != (self.unit == 'square_centimeters'):
            raise ValueError('produced wafer area and wafer counts have distinct units')
        if type(self.source_unit_scale) is not int or self.source_unit_scale not in (1, 1000, 1000000):
            raise ValueError('unsupported exact source-unit scale')
        if (not isinstance(self.source_number_literal, str)
                or not re.fullmatch(r'(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,6})?', self.source_number_literal)):
            raise ValueError('source number must be a bounded unsigned decimal literal without regrouping')
        if self.reference_number > 2**53 - 1:
            raise ValueError('source reference exceeds exactly representable numeric range')
        if self.unit == 'percent' and (self.source_unit_scale != 1 or self.reference_number > 100):
            raise ValueError('utilization percentage reference must be in [0,100] without scaling')

    @property
    def reference_number(self):
        return Decimal(self.source_number_literal) * self.source_unit_scale

    def scalar(self):
        number = self.reference_number
        if number == number.to_integral_value():
            return models.ScalarValue(models.ScalarType.INTEGER, int(number), self.unit)
        value = float(number)
        if Decimal(str(value)) != number:
            raise ValueError('source number cannot round trip through the numeric scalar representation')
        return models.ScalarValue(models.ScalarType.NUMBER, value, self.unit)

    def series_key(self, subject_entity_id):
        models._required(subject_entity_id, 'subject_entity_id')
        dimensions = asdict(self)
        for field in ('claim_version_id', 'source_number_literal', 'source_period_label', 'source_unit_scale'):
            dimensions.pop(field)
        dimensions['subject_entity_id'] = subject_entity_id
        raw = json.dumps(dimensions, sort_keys=True, separators=(',', ':')).encode()
        return 'source-quantity-v1:' + hashlib.sha256(raw).hexdigest()


def _table_exists(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_quantity_metadata'").fetchone() is not None


def _validate_link(connection, metadata):
    row = connection.execute('''SELECT c.claim_kind,c.valid_from,c.valid_to,c.confidence,
        s.predicate,s.stable_key,s.subject_entity_id,e.kind,v.scalar_type,v.integer_value,v.number_value,v.unit
        FROM claim_versions c JOIN claim_series s ON s.id=c.series_id
        JOIN entities e ON e.id=s.subject_entity_id JOIN scalar_values v ON v.claim_version_id=c.id
        WHERE c.id=?''', (metadata.claim_version_id,)).fetchone()
    if (row is None or row['claim_kind'] != 'source_statement' or row['valid_from'] is not None
            or row['valid_to'] is not None or row['confidence'] is not None
            or row['predicate'] != 'source_quantity.' + metadata.metric
            or row['kind'] != SCOPES[metadata.scope_kind]
            or row['stable_key'] != metadata.series_key(row['subject_entity_id'])):
        raise ValueError('source quantity must match its source scalar, series dimensions and subject scope')
    scalar = metadata.scalar()
    stored = row['integer_value'] if row['scalar_type'] == 'integer' else row['number_value']
    if row['scalar_type'] != scalar.scalar_type.value or stored != scalar.value or row['unit'] != scalar.unit:
        raise ValueError('source scalar differs from qualified literal and unit scale')


def add_metadata(connection, metadata):
    from . import repository
    if not isinstance(metadata, SourceQuantityMetadata):
        raise ValueError('typed source quantity metadata required')
    _validate_link(connection, metadata)
    value = asdict(metadata)
    columns = ','.join(value)
    return repository._insert_or_verify_unique(connection, table='source_quantity_metadata',
        lookup_sql='SELECT * FROM source_quantity_metadata WHERE claim_version_id=?',
        lookup_params=(metadata.claim_version_id,),
        insert_sql=f"INSERT OR IGNORE INTO source_quantity_metadata({columns}) VALUES ({','.join('?' for _ in value)})",
        params=tuple(value.values()), expected=value)


def metadata_for_claims(connection, claim_ids):
    if not claim_ids:
        return {}
    identifiers = json.dumps(list(claim_ids), separators=(',', ':'))
    required = {row[0] for row in connection.execute('''SELECT c.id FROM claim_versions c
        JOIN claim_series s ON s.id=c.series_id JOIN json_each(?) ids ON ids.value=c.id
        WHERE s.predicate LIKE 'source_quantity.%' ''', (identifiers,))}
    if not _table_exists(connection):
        if required:
            raise ValueError('source quantity claims have no metadata schema')
        return {}
    result = {}
    for row in connection.execute('''SELECT m.* FROM source_quantity_metadata m
        JOIN json_each(?) ids ON ids.value=m.claim_version_id ORDER BY m.claim_version_id''', (identifiers,)):
        metadata = SourceQuantityMetadata(**dict(row))
        _validate_link(connection, metadata)
        item = asdict(metadata)
        identifier = item.pop('claim_version_id')
        result[identifier] = {**item, 'physical_capacity_basis': None,
            'numeric_interpretation': 'qualified_reference_number_not_unqualified_point_estimate'}
    if set(result) != required:
        raise ValueError('source quantity claims and mandatory metadata do not match')
    return result


def validate_quantities(connection):
    from .database import schema_version
    if schema_version(connection) >= 7 and not _table_exists(connection):
        return ['source quantity metadata schema is missing']
    ids = [row[0] for row in connection.execute("SELECT c.id FROM claim_versions c JOIN claim_series s ON s.id=c.series_id WHERE s.predicate LIKE 'source_quantity.%'")]
    if _table_exists(connection):
        ids.extend(row[0] for row in connection.execute('SELECT claim_version_id FROM source_quantity_metadata'))
    try:
        metadata_for_claims(connection, sorted(set(ids)))
    except (TypeError, ValueError) as error:
        return ['source quantity metadata invalid: ' + str(error)]
    return []
