"""Admit four scoped quantities from one retained filing into an isolated successor."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from scripts import frozen_runtime as runtime
from . import database, models, repository, realized_milestones, source_quantities


RULE = 'tsmc-company-source-quantities-v1'
FORMAT = 'semiconductor-atlas-tsmc-source-quantity-review-v1'
SOURCE_SHA = 'c3ebd05cd8fb383f53fc21a0ac497ee12cf908b709c380f9bec4f39c4916647b'
CAPACITY = ('In 2025, our annual capacity (in 12-inch equivalent wafers) exceeded 17 million wafers, '
            'compared to approximately 17 million wafers in 2024.')
SHIPMENTS = 'We shipped approximately 15 million 12-inch equivalent wafers in 2025 compared to 13 million in 2024.'
SCOPE = 'TSMC company-reported aggregate; no fab allocation or independently resolved consolidation perimeter'
DEFINITIONS = {
    'wafer_capacity': 'Issuer annual-capacity reference; installed, qualified and economically usable bases are not stated.',
    'wafer_shipments': 'Issuer annual wafer shipments; not wafer production, wafer-out, usable capacity or utilization.'}
BOUNDARIES = dict.fromkeys(('physical_capacity_basis_assigned', 'facility_allocation', 'utilization_inferred',
    'yield_inferred', 'canonical_entity_assignment', 'independent_corroboration', 'forecast_created',
    'forecast_scored', 'source_acquisition', 'raw_redistribution', 'scheduled_execution_rerouted'), False)


def now():
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def clock(value):
    value = models._iso_timestamp(value, 'clock')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def codes():
    root = Path(__file__).parent.parent
    paths = sorted([*Path(__file__).parent.rglob('*.py'), *Path(__file__).parent.rglob('*.sql'),
        root / 'scripts/admit_tsmc_source_quantities.py', root / 'scripts/frozen_runtime.py'])
    return {str(path.relative_to(root)): runtime.digest(runtime.read(path)) for path in paths}


_LOADED_CODES = codes()


def rows():
    result = []
    for metric, year, number, qualifier, support in (
        ('wafer_capacity', 2024, '17', 'approximate', 'capacity'),
        ('wafer_capacity', 2025, '17', 'greater_than', 'capacity'),
        ('wafer_shipments', 2024, '13', 'as_reported_precision_unspecified', 'shipments'),
        ('wafer_shipments', 2025, '15', 'approximate', 'shipments')):
        result.append({'key': f'{metric}:{year}', 'support': support,
            'metadata': {key: value for key, value in asdict(source_quantities.SourceQuantityMetadata(
                'not-yet-admitted', metric, 'company_aggregate', SCOPE, 'year', f'{year}-01-01',
                f'{year}-12-31', str(year), '12_inch_equivalent_wafers', None, number, 1000000,
                qualifier, 'reported', DEFINITIONS[metric])).items() if key != 'claim_version_id'}})
    return result


def validate_review(raw, body, expected_sha256):
    runtime._hex(expected_sha256, 64)
    if runtime.digest(raw) != expected_sha256:
        raise ValueError('trusted source-quantity review hash mismatch')
    review = realized_milestones._json(raw)
    runtime._keys(review, {'format', 'reviewed_at', 'reviewed_by', 'prior_exposure', 'selection',
        'parent_database_sha256', 'source_document_id', 'source_sha256', 'source_bytes', 'evidence', 'rows'})
    if review['format'] != FORMAT or review['source_sha256'] != SOURCE_SHA:
        raise ValueError('unsupported source-specific quantity review')
    if runtime.digest(body) != SOURCE_SHA or type(review['source_bytes']) is not int or len(body) != review['source_bytes']:
        raise ValueError('retained publisher bytes differ')
    runtime._hex(review['parent_database_sha256'], 64)
    for key in ('reviewed_by', 'prior_exposure', 'selection', 'source_document_id'):
        models._required(review[key], key)
    clock(review['reviewed_at'])
    runtime._keys(review['evidence'], {'capacity', 'capacity_repeat', 'shipments'})
    for key, span in review['evidence'].items():
        runtime._keys(span, {'start', 'end', 'sha256'})
        if (type(span['start']) is not int or type(span['end']) is not int
                or not 0 <= span['start'] < span['end'] <= len(body)
                or runtime.digest(body[span['start']:span['end']]) != span['sha256']):
            raise ValueError('quantity evidence span mismatch')
        document = realized_milestones._EvidenceHTML(body, span['start'], span['end'])
        blocks = [node for node in document.nodes if node.tag in ('p', 'div') and not node.hidden
                  and node.start >= span['start'] and node.end is not None and node.end <= span['end']]
        if (not blocks or min(n.start for n in blocks) != span['start']
                or max(n.end for n in blocks) != span['end']):
            raise ValueError('quantity evidence must retain complete visible blocks')
        text = document.text(span)
        required = CAPACITY if key != 'shipments' else SHIPMENTS
        if required not in text:
            raise ValueError('complete quantity, qualifier, unit, scope and period wording is missing')
    if review['rows'] != rows():
        raise ValueError('the complete four-quantity selection or its semantics differs')
    return review


def open_image(raw, version):
    connection = sqlite3.connect(':memory:')
    try:
        connection.deserialize(raw)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        if database.schema_version(connection) != version or connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError(f'expected intact schema-{version} image')
        return connection
    except BaseException:
        connection.close()
        raise


def populate(connection, review, review_sha256, started, admitted, code_sha256):
    if not clock(review['reviewed_at']) <= clock(started) < clock(admitted):
        raise ValueError('review/admission chronology differs')
    document = connection.execute('SELECT * FROM source_documents WHERE id=?', (review['source_document_id'],)).fetchone()
    if (document is None or document['content_sha256'] != SOURCE_SHA
            or clock(document['retrieved_at']) > clock(review['reviewed_at'])):
        raise ValueError('retained source-document identity or chronology differs')
    source_metadata = json.loads(document['metadata_json'])
    rights = source_metadata.get('rights')
    if rights is None or rights.get('use') != 'local_review_only' or rights.get('redistribution_authorized') is not False:
        raise ValueError('expected retained local-review-only source rights')
    if database.apply_migrations(connection, target_version=7) != [7]:
        raise ValueError('only the additive source-quantity migration is expected')
    connection.execute('UPDATE schema_migrations SET applied_at=? WHERE version=7', (admitted,))
    run_id = repository.stable_id(RULE, review_sha256)
    repository.add_ingestion_run(connection, models.IngestionRun(run_id, document['source_id'], started,
        models.IngestionStatus.SUCCEEDED, admitted, code_sha256['semiconductor_atlas/tsmc_source_quantities.py'],
        document['id'], {'rule_version': RULE, 'review_sha256': review_sha256, 'review': review,
            'code_sha256': code_sha256, 'rights': rights, 'boundaries': BOUNDARIES,
            'accepted_at': admitted, 'interpreter': runtime.interpreter(),
            'document_scope_note': 'Existing document title/table_as_of metadata belongs to its earlier fab-table use; quantity periods and scope are independently explicit here.'}))
    entity_key = 'tsmc-2025-20f:company-reported-aggregate'
    entity_id = repository.stable_id(entity_key)
    repository.add_entity(connection, models.Entity(entity_id, models.EntityKind.ORGANIZATION,
        entity_key, admitted, 'TSMC company-reported aggregate (unresolved legal consolidation scope)', run_id))
    claims = []
    for row in review['rows']:
        claim_id = repository.stable_id(RULE, review_sha256, row['key'], 'claim')
        metadata = source_quantities.SourceQuantityMetadata(claim_id, **row['metadata'])
        key = metadata.series_key(entity_id)
        series_id = repository.stable_id(key)
        repository.add_claim_series(connection, models.ClaimSeries(series_id, entity_id, key,
            'source_quantity.' + metadata.metric, models.ValueKind.SCALAR, admitted))
        record_id = repository.stable_id(RULE, review_sha256, row['key'], 'record')
        payload = {'review_sha256': review_sha256, 'quantity': asdict(metadata), 'core_admitted_at': admitted,
                   'source_native_accounting_scope': entity_key, 'physical_capacity_basis': None}
        repository.add_source_record(connection, models.SourceRecord(record_id, run_id, document['id'],
            row['key'], document['retrieved_at'], models.source_record_payload_sha256(payload), payload))
        supports = [row['support']] + (['capacity_repeat'] if row['support'] == 'capacity' else [])
        evidence = [models.EvidenceLink(document['id'], models.EvidenceRole.SUPPORT if i == 0 else models.EvidenceRole.CONTEXT,
            record_id, f"{label}; UTF-8 bytes [{review['evidence'][label]['start']},{review['evidence'][label]['end']}); SHA-256 {review['evidence'][label]['sha256']}")
            for i, label in enumerate(supports)]
        with repository._atomic(connection):
            repository.insert_claim(connection, models.ClaimVersion(claim_id, series_id, None, admitted,
                models.ClaimKind.SOURCE_STATEMENT, RULE, None, created_by_run_id=run_id,
                notes='Qualified source reference, not a physical capacity point; source quantity metadata is mandatory.'),
                metadata.scalar(), evidence=evidence)
            source_quantities.add_metadata(connection, metadata)
        claims.append({'claim_id': claim_id, 'key': row['key'], 'metadata': asdict(metadata)})
    issues = repository.validate_database(connection)
    if issues:
        raise ValueError('source quantity successor validation failed: ' + str(issues[:3]))
    connection.commit()
    return {'run_id': run_id, 'admitted_at': admitted, 'source_native_entity_id': entity_id,
            'source_quantities': claims, 'boundaries': BOUNDARIES}


def admit(*, parent, review_path, source_body, expected_review_sha256, output,
          verify_only=False, expected_output_sha256=None):
    started, current_codes = now(), codes()
    if current_codes != _LOADED_CODES:
        raise ValueError('quantity implementation changed since module loading')
    if verify_only:
        runtime._hex(expected_output_sha256, 64)
    elif expected_output_sha256 is not None:
        raise ValueError('successor hash is only an input for verification')
    paths = {'parent': Path(parent), 'review': Path(review_path), 'source': Path(source_body)}
    raw = {key: runtime.read(path, limit=1_000_000_000 if key == 'parent' else 20_000_000)
           for key, path in paths.items()}
    review = validate_review(raw['review'], raw['source'], expected_review_sha256)
    if runtime.digest(raw['parent']) != review['parent_database_sha256']:
        raise ValueError('trusted parent database hash mismatch')
    target = Path(output)
    if not verify_only and (target.exists() or target.is_symlink()):
        raise ValueError('successor output must not exist')
    connection = open_image(raw['parent'], 6)
    try:
        if verify_only:
            successor = runtime.read(target, limit=1_000_000_000)
            if runtime.digest(successor) != expected_output_sha256:
                raise ValueError('trusted successor hash mismatch')
            actual = open_image(successor, 7)
            try:
                run = actual.execute('SELECT * FROM ingestion_runs WHERE id=?',
                    (repository.stable_id(RULE, expected_review_sha256),)).fetchone()
                if run is None or clock(run['completed_at']) > clock(started):
                    raise ValueError('missing or future successor admission')
                admission_start, admitted = run['started_at'], run['completed_at']
            finally:
                actual.close()
        else:
            admission_start, admitted = started, now()
        result = populate(connection, review, expected_review_sha256, admission_start, admitted, current_codes)
        generated = connection.serialize()
        if verify_only and generated != successor:
            raise ValueError('successor differs from complete parent-plus-quantities reconstruction')
        for key, path in paths.items():
            if runtime.read(path, limit=1_000_000_000 if key == 'parent' else 20_000_000) != raw[key]:
                raise ValueError('quantity input changed during admission or replay')
        if codes() != current_codes:
            raise ValueError('quantity implementation changed during admission or replay')
        if verify_only and runtime.read(target, limit=1_000_000_000) != successor:
            raise ValueError('successor changed during verification')
        if not verify_only:
            runtime.write_new(target, generated)
        return {**result, 'database_sha256': runtime.digest(generated), 'database_bytes': len(generated),
                'verified': verify_only, 'parent_modified': False, 'new_source_quantities': 4}
    finally:
        connection.close()
