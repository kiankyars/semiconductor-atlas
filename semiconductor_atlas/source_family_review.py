"""Reviewed, knowledge-time publisher grouping; original importer families stay intact."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from .database import _timestamp_microseconds, register_clock_functions
from .models import _required


FORMAT = 'semiconductor-atlas-source-family-review-v1'
BOUNDARIES = dict.fromkeys(('source_rows_modified', 'family_rows_merged', 'claims_modified',
    'documents_deduplicated', 'independent_corroboration_inferred', 'physical_state_inferred',
    'importer_population_changed', 'historical_knowledge_backdated', 'source_acquisition',
    'raw_redistribution', 'model_training'), False)


def _clock(value):
    result = _timestamp_microseconds(value)
    if result is None:
        raise ValueError('source family review requires an aware knowledge timestamp')
    return result


def available(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_family_assignments'").fetchone() is not None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate source family review key')
        result[key] = value
    return result


def parse_review(raw, expected_sha256):
    if (not isinstance(raw, bytes) or not isinstance(expected_sha256, str)
            or hashlib.sha256(raw).hexdigest() != expected_sha256):
        raise ValueError('trusted source family review hash differs')
    review = json.loads(raw, object_pairs_hook=_pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite review number')))
    fields = {'format', 'reviewer', 'reviewed_at', 'reason', 'prior_exposure', 'source', 'target_family',
              'expected_predecessor_id', 'evidence_documents', 'boundaries'}
    if not isinstance(review, dict) or set(review) != fields or review['format'] != FORMAT:
        raise ValueError('exact source family review contract required')
    for key in ('reviewer', 'reason', 'prior_exposure'):
        _required(review[key], key)
    _clock(review['reviewed_at'])
    if review['expected_predecessor_id'] is not None:
        _required(review['expected_predecessor_id'], 'expected predecessor')
    if (review['boundaries'] != BOUNDARIES
            or any(v is not False for v in review['boundaries'].values())):
        raise ValueError('source family review cannot widen its provenance-only boundaries')
    if not isinstance(review['source'], dict) or not isinstance(review['target_family'], dict):
        raise ValueError('exact source and target family records required')
    docs = review['evidence_documents']
    if not isinstance(docs, list) or not 1 <= len(docs) <= 100:
        raise ValueError('bounded evidence document inventory required')
    ids = []
    for doc in docs:
        if not isinstance(doc, dict) or set(doc) != {'id', 'source_id', 'document_url', 'content_sha256', 'retrieved_at'}:
            raise ValueError('exact evidence document identities required')
        for key in doc:
            _required(doc[key], key)
        _clock(doc['retrieved_at'])
        ids.append(doc['id'])
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate source family evidence documents')
    return review


def _check_review(connection, review, *, recorded_at):
    reviewed = _clock(review['reviewed_at'])
    if reviewed > _clock(recorded_at):
        raise ValueError('family assignment cannot precede review')
    for key, table in (('source', 'sources'), ('target_family', 'source_families')):
        if not isinstance(review[key].get('id'), str):
            raise ValueError('source family endpoint lacks an id')
        row = connection.execute(f'SELECT * FROM {table} WHERE id=?', (review[key]['id'],)).fetchone()
        if row is None or dict(row) != review[key] or _clock(row['created_at']) > reviewed:
            raise ValueError('source family endpoint differs or is unavailable at review')
    original = connection.execute('SELECT created_at FROM source_families WHERE id=?',
                                  (review['source']['family_id'],)).fetchone()
    if original is None or _clock(original['created_at']) > reviewed:
        raise ValueError('original source family was unavailable at review')
    subject_document = False
    for doc in review['evidence_documents']:
        row = connection.execute('''SELECT id,source_id,document_url,content_sha256,retrieved_at
            FROM source_documents WHERE id=?''', (doc['id'],)).fetchone()
        if row is None or dict(row) != doc or _clock(doc['retrieved_at']) > reviewed:
            raise ValueError('family review evidence identity or chronology differs')
        source = connection.execute('SELECT created_at FROM sources WHERE id=?', (doc['source_id'],)).fetchone()
        if source is None or _clock(source['created_at']) > reviewed:
            raise ValueError('family review evidence source was unavailable')
        subject_document |= doc['source_id'] == review['source']['id']
    if not subject_document:
        raise ValueError('family review requires evidence from its exact source')


def assignment_history(connection, *, recorded_at=None):
    """Verify immutable review/chain semantics; filter only after clock validation."""
    if not available(connection):
        return []
    register_clock_functions(connection)
    cutoff = _clock(recorded_at) if recorded_at is not None else None
    rows = [dict(r) for r in connection.execute('SELECT * FROM source_family_assignments')]
    for row in rows:
        _clock(row['recorded_at'])
        _clock(row['reviewed_at'])
    selected = sorted((r for r in rows if cutoff is None or _clock(r['recorded_at']) <= cutoff),
                      key=lambda r: (_clock(r['recorded_at']), r['id']))
    current, result = {}, []
    from .repository import stable_id
    for row in selected:
        raw = row['review_json'].encode('utf-8')
        review = parse_review(raw, row['review_sha256'])
        _check_review(connection, review, recorded_at=row['recorded_at'])
        if (row['id'] != stable_id(FORMAT, row['review_sha256'])
                or row['source_id'] != review['source']['id']
                or row['family_id'] != review['target_family']['id']
                or row['reviewed_at'] != review['reviewed_at']
                or row['supersedes_id'] != review['expected_predecessor_id']):
            raise ValueError('stored family assignment differs from its reviewed content')
        prior = current.get(row['source_id'])
        if row['supersedes_id'] != (prior['id'] if prior else None):
            raise ValueError('family assignment predecessor is not the current source assignment')
        old_family = prior['family_id'] if prior else review['source']['family_id']
        if old_family == row['family_id']:
            raise ValueError('family assignment must change the current grouping')
        if prior and not (_clock(prior['recorded_at']) < _clock(row['recorded_at'])
                          and _clock(prior['recorded_at']) <= _clock(row['reviewed_at'])):
            raise ValueError('family assignment chronology is inconsistent')
        result.append(row | {'review': review})
        current[row['source_id']] = row
    return result


def insert_review(connection, raw, *, expected_sha256, recorded_at):
    """Low-level insertion with an explicit admission clock; no implicit migration."""
    if not available(connection):
        raise ValueError('source family review requires schema 12; migrate a private copy first')
    review = parse_review(raw, expected_sha256)
    _check_review(connection, review, recorded_at=recorded_at)
    history = assignment_history(connection)
    prior = next((r for r in reversed(history) if r['source_id'] == review['source']['id']), None)
    if review['expected_predecessor_id'] != (prior['id'] if prior else None):
        raise ValueError('family review predecessor differs from current assignment')
    from .repository import stable_id
    identifier = stable_id(FORMAT, expected_sha256)
    connection.execute('SAVEPOINT source_family_review')
    try:
        connection.execute('''INSERT INTO source_family_assignments
            (id,source_id,family_id,supersedes_id,recorded_at,reviewed_at,review_sha256,review_json)
            VALUES (?,?,?,?,?,?,?,?)''', (identifier, review['source']['id'], review['target_family']['id'],
            review['expected_predecessor_id'], recorded_at, review['reviewed_at'], expected_sha256, raw.decode('utf-8')))
        assignment_history(connection)
    except BaseException:
        connection.execute('ROLLBACK TO source_family_review')
        raise
    finally:
        connection.execute('RELEASE source_family_review')
    return identifier


def resolutions(connection, *, recorded_at):
    result = {}
    for row in assignment_history(connection, recorded_at=recorded_at):
        source, target = row['review']['source'], row['review']['target_family']
        original = dict(connection.execute('SELECT * FROM source_families WHERE id=?', (source['family_id'],)).fetchone())
        result[row['source_id']] = {'source_family_id': target['id'], 'source_family': target['stable_key'],
            'source_family_name': target['name'], 'source_family_resolution': {
                'assignment_id': row['id'], 'recorded_at': row['recorded_at'], 'review_sha256': row['review_sha256'],
                'original_source_family_id': original['id'], 'original_source_family': original['stable_key'],
                'original_source_family_name': original['name']}}
    return result


def apply_resolution(item, source_id, mapping):
    if source_id in mapping:
        resolved = mapping[source_id]
        for key in ('source_family', 'source_family_id', 'source_family_name'):
            if key in item:
                item[key] = resolved[key]
        item['source_family_resolution'] = dict(resolved['source_family_resolution'])
    return item


def original_family(item):
    """Importer-specific rights and population rules use the unchanged original route."""
    return item.get('source_family_resolution', {}).get('original_source_family', item.get('source_family'))


def validate_assignments(connection):
    try:
        assignment_history(connection)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as error:
        return ['source family assignment validation failed: ' + str(error)]
    return []
