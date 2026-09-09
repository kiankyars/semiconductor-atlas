"""Qualified source-native monthly rates, not period totals or physical capacity."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal

from . import models


METRICS = {'wafer_production_rate': 'native_wafers',
           'wafer_start_capacity_rate': 'native_wafer_starts'}
SCOPES = {'company_aggregate': 'organization', 'source_native_facility': 'facility',
          'source_native_site': 'site', 'source_native_project': 'project'}
QUALIFIERS = {'as_reported_precision_unspecified', 'approximate', 'greater_than',
              'at_least', 'less_than', 'at_most'}
_NUMBER = re.compile(r'(?:0|[1-9][0-9]{0,12}|[1-9][0-9]{0,2}(?:,[0-9]{3}){1,4})(?:\.[0-9]{1,6})?')


@dataclass(frozen=True, slots=True)
class SourceRateMetadata:
    claim_version_id: str
    metric: str
    scope_kind: str
    scope_label: str
    numerator_unit: str
    denominator_unit: str
    native_wafer_diameter_inches: int | None
    reference_period_kind: str
    reference_period_start: str | None
    reference_period_end: str | None
    source_reference_period_literal: str | None
    source_number_literal: str
    numeric_qualifier: str
    source_qualifier_literal: str | None
    assertion_kind: str
    source_wording: str
    source_definition: str

    def __post_init__(self):
        for field in ('claim_version_id', 'scope_label', 'source_wording', 'source_definition'):
            models._required(getattr(self, field), field)
            if len(getattr(self, field)) > 2000:
                raise ValueError('source rate text exceeds bound')
        if (not isinstance(self.metric, str) or self.metric not in METRICS
                or not isinstance(self.scope_kind, str) or self.scope_kind not in SCOPES):
            raise ValueError('unsupported source rate metric or accounting scope')
        if self.numerator_unit != METRICS[self.metric] or self.denominator_unit != 'month':
            raise ValueError('source rate requires its distinct native numerator and monthly denominator')
        diameter = self.native_wafer_diameter_inches
        if diameter is not None and (type(diameter) is not int or diameter not in (6, 8, 12)):
            raise ValueError('source native wafer diameter must be unknown or explicitly 6, 8, or 12 inches')
        if (self.reference_period_kind != 'not_stated' or self.reference_period_start is not None
                or self.reference_period_end is not None):
            raise ValueError('source rate reference interval must remain unknown, distinct from its denominator')
        if (not isinstance(self.numeric_qualifier, str) or self.numeric_qualifier not in QUALIFIERS
                or not isinstance(self.assertion_kind, str) or self.assertion_kind not in {'reported', 'estimate', 'guidance'}):
            raise ValueError('unsupported source rate qualifier or assertion kind')
        for field in ('source_reference_period_literal', 'source_qualifier_literal'):
            literal = getattr(self, field)
            if literal is not None:
                models._required(literal, field)
                if len(literal) > 2000 or literal not in self.source_wording:
                    raise ValueError('source rate literals must preserve exact source wording')
        if ((self.numeric_qualifier == 'as_reported_precision_unspecified')
                != (self.source_qualifier_literal is None)):
            raise ValueError('an explicit numeric qualifier requires its literal; unspecified precision has none')
        literal = self.source_number_literal
        if not isinstance(literal, str) or not _NUMBER.fullmatch(literal):
            raise ValueError('source rate number must be a bounded unsigned decimal with optional exact thousands grouping')
        if not re.search(r'(?<![\d,.])' + re.escape(literal) + r'(?![\d,.])', self.source_wording):
            raise ValueError('source rate number literal must occur unchanged in source wording')
        if self.reference_number > 2**53 - 1:
            raise ValueError('source rate reference exceeds exactly representable numeric range')

    @property
    def reference_number(self):
        return Decimal(self.source_number_literal.replace(',', ''))

    @property
    def unit(self):
        return self.numerator_unit + '_per_' + self.denominator_unit

    def scalar(self):
        number = self.reference_number
        if number == number.to_integral_value():
            return models.ScalarValue(models.ScalarType.INTEGER, int(number), self.unit)
        value = float(number)
        if Decimal(str(value)) != number:
            raise ValueError('source rate number cannot round trip through its numeric scalar')
        return models.ScalarValue(models.ScalarType.NUMBER, value, self.unit)

    def series_key(self, subject_entity_id):
        models._required(subject_entity_id, 'subject_entity_id')
        dimensions = asdict(self)
        for field in ('claim_version_id', 'source_number_literal', 'source_wording', 'source_qualifier_literal'):
            dimensions.pop(field)
        dimensions['subject_entity_id'] = subject_entity_id
        raw = json.dumps(dimensions, sort_keys=True, separators=(',', ':')).encode()
        return 'source-rate-v1:' + hashlib.sha256(raw).hexdigest()


def _table_exists(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_rate_metadata'").fetchone() is not None


def _validate_link(connection, metadata):
    row = connection.execute('''SELECT c.claim_kind,c.valid_from,c.valid_to,c.confidence,
        s.predicate,s.stable_key,s.subject_entity_id,e.kind,v.scalar_type,v.integer_value,v.number_value,v.unit
        FROM claim_versions c JOIN claim_series s ON s.id=c.series_id
        JOIN entities e ON e.id=s.subject_entity_id JOIN scalar_values v ON v.claim_version_id=c.id
        WHERE c.id=?''', (metadata.claim_version_id,)).fetchone()
    if (row is None or row['claim_kind'] != 'source_statement' or row['valid_from'] is not None
            or row['valid_to'] is not None or row['confidence'] is not None
            or row['predicate'] != 'source_rate.' + metadata.metric
            or row['kind'] != SCOPES[metadata.scope_kind]
            or row['stable_key'] != metadata.series_key(row['subject_entity_id'])):
        raise ValueError('source rate must match its source scalar, series dimensions and subject scope')
    scalar = metadata.scalar()
    stored = row['integer_value'] if row['scalar_type'] == 'integer' else row['number_value']
    if row['scalar_type'] != scalar.scalar_type.value or stored != scalar.value or row['unit'] != scalar.unit:
        raise ValueError('source scalar differs from its qualified source-native rate')


def add_metadata(connection, metadata):
    from . import repository
    if not isinstance(metadata, SourceRateMetadata):
        raise ValueError('typed source rate metadata required')
    _validate_link(connection, metadata)
    value = asdict(metadata)
    return repository._insert_or_verify_unique(connection, table='source_rate_metadata',
        lookup_sql='SELECT * FROM source_rate_metadata WHERE claim_version_id=?',
        lookup_params=(metadata.claim_version_id,),
        insert_sql=f"INSERT OR IGNORE INTO source_rate_metadata({','.join(value)}) VALUES ({','.join('?' for _ in value)})",
        params=tuple(value.values()), expected=value)


def metadata_for_claims(connection, claim_ids):
    if not claim_ids:
        return {}
    identifiers = json.dumps(list(claim_ids), separators=(',', ':'))
    required = {row[0] for row in connection.execute('''SELECT c.id FROM claim_versions c
        JOIN claim_series s ON s.id=c.series_id JOIN json_each(?) ids ON ids.value=c.id
        WHERE s.predicate GLOB 'source_rate.*' ''', (identifiers,))}
    if not _table_exists(connection):
        if required:
            raise ValueError('source rate claims have no metadata schema')
        return {}
    result = {}
    for row in connection.execute('''SELECT m.* FROM source_rate_metadata m
        JOIN json_each(?) ids ON ids.value=m.claim_version_id ORDER BY m.claim_version_id''', (identifiers,)):
        metadata = SourceRateMetadata(**dict(row))
        _validate_link(connection, metadata)
        item = asdict(metadata)
        identifier = item.pop('claim_version_id')
        result[identifier] = {**item, 'physical_capacity_basis': None,
            'numeric_interpretation': 'qualified_reference_rate_not_period_total_or_physical_capacity'}
    if set(result) != required:
        raise ValueError('source rate claims and mandatory metadata do not match')
    return result


def validate_rates(connection):
    from .database import schema_version
    if schema_version(connection) >= 9 and not _table_exists(connection):
        return ['source rate metadata schema is missing']
    ids = [row[0] for row in connection.execute("SELECT c.id FROM claim_versions c JOIN claim_series s ON s.id=c.series_id WHERE s.predicate GLOB 'source_rate.*'")]
    if _table_exists(connection):
        ids.extend(row[0] for row in connection.execute('SELECT claim_version_id FROM source_rate_metadata'))
    try:
        metadata_for_claims(connection, sorted(set(ids)))
    except (TypeError, ValueError) as error:
        return ['source rate metadata invalid: ' + str(error)]
    return []
