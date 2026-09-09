"""Admit NIST-reported Bosch samples with a one-sided occurrence bound."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path, PurePosixPath

from scripts import frozen_runtime as runtime
from . import curated_capture, database, models, realized_milestones, repository


FORMAT = 'semiconductor-atlas-bosch-sample-production-admission-v1'
RULE = 'nist-bosch-roseville-sample-production-v1'
PACKET = 'artifacts/2026-09-09-nist-2026-progress-capture-v1'
PACKET_SHA256 = '05a33260bde85bf62efe4c3e8c5bd81430ba3c9c62f140ad718785cc5064579a'
ACCESS_REVIEW = 'review_plans/2026-09-09-nist-2026-progress-source-review.json'
ACCESS_SHA256 = '942abcff0fbffcf74d318d05de6ec8c6bd8539e4ca06479b8ecd9dae065fbbe5'
FAMILY = 'us-official-semiconductor-awards'
SOURCE_KEY = 'nist-commerce-government:bosch-roseville-sample-production'
SUBJECT = {
    'stable_key': 'nist:bosch-roseville-2026-sample-production-source-native',
    'label': 'NIST-reported Bosch Roseville SiC facility (canonical identity unresolved)',
    'kind': 'facility',
    'scope': 'Bosch Roseville, California silicon-carbide facility with commenced sample production of unspecified wafer diameter; commercial production remains separate.',
    'canonical_entity_id': None,
}
SOURCE = {
    'id': 'nist-bosch-2026-dfa',
    'url': 'https://www.nist.gov/news-events/news/2026/07/department-commerce-announces-direct-funding-agreement-bosch-225-million',
    'title': 'Department of Commerce Announces Direct Funding Agreement with Bosch for a $225 Million CHIPS Program Award to Support Domestic Production of Silicon Carbide Semiconductors',
    'sha256': '928d2b6a812e6d9842272497dcbe48202c70c5f6c8828d114be36d3c0e298c42',
    'bytes': 87744,
    'published_at': '2026-07-13',
    'retrieved_at': '2026-09-09T04:23:21.385466Z',
}
BOUNDARIES = dict.fromkeys((
    'physical_current_state_inferred', 'physical_effective_date_inferred',
    'hvm_start_date_inferred', 'exact_start_date_inferred', 'lower_date_bound_inferred',
    'sample_wafer_diameter_inferred', 'first_200mm_chips_target_attained', 'expansion_completion_inferred', 'numeric_capacity_inferred',
    'measured_output_inferred', 'yield_utilization_qualification_inferred',
    'canonical_identity_assigned', 'geometry_inferred', 'ownership_assigned',
    'original_pmt_superseded', 'independent_physical_corroboration',
    'historical_system_knowledge_backdated', 'forecast_created', 'forecast_scored',
    'original_pmt_target_changed', 'original_pmt_outcome_changed', 'alert_generated',
    'baseline_modified', 'scheduled_execution_changed', 'raw_redistribution',
    'model_training', 'source_acquisition',
), False)
QUALIFIER = 'NIST reports commenced SiC sample production by July 13, 2026. Lower occurrence bound, exact start, sample wafer diameter, commercial production, qualification and numeric capacity remain unknown.'


def rows():
    return [{
        'key': 'sample-production-start', 'milestone_type': 'sample_production_start',
        'status': 'completed', 'date_low': None, 'date_base': None, 'date_high': '2026-07-13',
        'date_precision': 'day', 'date_literal': 'Sample production has already commenced',
        'date_relation': 'on_or_before',
    }]


now, clock = curated_capture._now, curated_capture._instant


def codes():
    root = Path(__file__).parent.parent
    paths = sorted([*Path(__file__).parent.rglob('*.py'), *Path(__file__).parent.rglob('*.sql'),
                    root / 'scripts/admit_bosch_sample_production.py', root / 'scripts/frozen_runtime.py'])
    return {p.relative_to(root).as_posix(): runtime.digest(runtime.read(p)) for p in paths}


def validate_review(raw, expected_sha256):
    runtime._hex(expected_sha256, 64)
    if runtime.digest(raw) != expected_sha256:
        raise ValueError('trusted Bosch admission review hash mismatch')
    review = realized_milestones._json(raw)
    runtime._keys(review, {'format', 'decision', 'reviewed_at', 'reviewer', 'prior_exposure',
        'interpretation', 'identity_review', 'parent_database_sha256', 'subject', 'source',
        'rows', 'source_family', 'packet', 'access_review', 'evidence', 'rights', 'boundaries'})
    if review['format'] != FORMAT or review['decision'] != 'accept_one_source_reported_sample_production_upper_bound':
        raise ValueError('explicit Bosch sample-production acceptance required')
    for key in ('reviewer', 'prior_exposure', 'interpretation', 'identity_review'):
        models._required(review[key], key)
    clock(review['reviewed_at'])
    runtime._hex(review['parent_database_sha256'], 64)
    if review['subject'] != SUBJECT or review['source'] != SOURCE or review['rows'] != rows():
        raise ValueError('exact source, subject or one source-reported sample-production upper bound differ')
    if review['source_family'] != FAMILY:
        raise ValueError('NIST evidence must reuse the existing official-disclosure source family')
    if (review['packet'] != {'path': PACKET, 'manifest_sha256': PACKET_SHA256}
            or review['access_review'] != {'path': ACCESS_REVIEW, 'sha256': ACCESS_SHA256}):
        raise ValueError('trusted retained acquisition bindings differ')
    if review['boundaries'] != BOUNDARIES or any(v is not False for v in review['boundaries'].values()):
        raise ValueError('excluded inference boundaries must remain false')
    runtime._keys(review['rights'], {'use', 'basis', 'raw_redistribution', 'model_training'})
    if (review['rights']['use'] != 'local_informational_review_only'
            or any(review['rights'][k] is not False for k in ('raw_redistribution', 'model_training'))):
        raise ValueError('admission does not authorize publication or training')
    models._required(review['rights']['basis'], 'rights basis')
    runtime._keys(review['evidence'], {'title', 'publication', 'narrative'})
    for span in review['evidence'].values():
        runtime._keys(span, {'start', 'end', 'sha256', 'tag'})
        if (type(span['start']) is not int or type(span['end']) is not int
                or not 0 <= span['start'] < span['end'] <= SOURCE['bytes']):
            raise ValueError('invalid complete evidence span')
        runtime._hex(span['sha256'], 64)
    return review


def snapshot(reference_root):
    root = Path(reference_root)
    manifest_raw = runtime.read(root / PACKET / 'manifest.json')
    access_raw = runtime.read(root / ACCESS_REVIEW)
    if runtime.digest(manifest_raw) != PACKET_SHA256 or runtime.digest(access_raw) != ACCESS_SHA256:
        raise ValueError('trusted acquisition packet or access review hash mismatch')
    manifest = realized_milestones._json(manifest_raw)
    runtime._keys(manifest, {'format', 'files'})
    if manifest['format'] != 'semiconductor-atlas-curated-acquisition-manifest-v1':
        raise ValueError('expected curated acquisition manifest')
    members = manifest['files']
    if not isinstance(members, dict) or not 1 <= len(members) <= 80:
        raise ValueError('bounded complete acquisition inventory required')
    payloads = {PACKET + '/manifest.json': manifest_raw, ACCESS_REVIEW: access_raw}
    for name, identity in members.items():
        if (not isinstance(name, str) or PurePosixPath(name).is_absolute() or '\\' in name
                or any(p in ('', '.', '..') for p in name.split('/')) or name == 'manifest.json'):
            raise ValueError('unsafe retained acquisition member')
        runtime._keys(identity, {'bytes', 'sha256'})
        raw = runtime.read(root / PACKET / name, limit=4_000_000)
        if len(raw) != identity['bytes'] or runtime.digest(raw) != identity['sha256']:
            raise ValueError('retained acquisition member differs: ' + name)
        payloads[PACKET + '/' + name] = raw
    if sum(map(len, payloads.values())) > 16_000_000:
        raise ValueError('acquisition packet exceeds aggregate byte bound')
    os.close(runtime._directory(root / PACKET))
    actual = set()
    for path in (root / PACKET).rglob('*'):
        if path.is_symlink():
            raise ValueError('symlink in retained acquisition packet')
        if path.is_file():
            actual.add(path.relative_to(root / PACKET).as_posix())
    if actual != set(members) | {'manifest.json'}:
        raise ValueError('unlisted or missing acquisition packet members')
    return payloads


def load_evidence(review, payloads):
    access = realized_milestones._json(payloads[ACCESS_REVIEW])
    if (payloads[ACCESS_REVIEW] != payloads[PACKET + '/review.json']
            or access['decision'] != 'approved_exact_urls_local_informational_review_only'
            or any(v is not False for v in access['boundaries'].values())):
        raise ValueError('capture review must remain separate from core acceptance')
    with tempfile.TemporaryDirectory(prefix='atlas-bosch-evidence-') as directory:
        private = Path(directory).resolve()
        for name, raw in payloads.items():
            path = private / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(raw)
        result = curated_capture.validate_capture(private / PACKET)
    plan = realized_milestones._json(payloads[PACKET + '/plan.json'])
    if plan['review_record'] != review['access_review']:
        raise ValueError('capture plan must bind the unchanged access review')
    ledger = realized_milestones._json(payloads[PACKET + '/source_checks.json'])
    attempts = ledger['attempts']
    if (len(attempts) != 5 or any(a['outcome'] != 'succeeded' or a['redirect_count'] != 0 for a in attempts)
            or any(d['status'] != 'first_observation_requires_review' for d in result['documents'])):
        raise ValueError('all original policy and three-source captures must remain successful')
    for earlier, later in zip(attempts, attempts[1:]):
        if (clock(later['started_at']) - clock(earlier['finished_at'])).total_seconds() < plan['minimum_interval_seconds']:
            raise ValueError('retained request cadence differs')
    if not clock(access['reviewed_at']) <= clock(attempts[0]['started_at']) or not clock(attempts[-1]['finished_at']) <= clock(review['reviewed_at']):
        raise ValueError('access, capture and claim review chronology differs')
    selected = [a for a in attempts if a['id'] == SOURCE['id']]
    if len(selected) != 1:
        raise ValueError('one exact Bosch response required')
    attempt = selected[0]
    if (any(attempt[k] != SOURCE[k] for k in ('url', 'sha256', 'bytes'))
            or attempt['finished_at'] != SOURCE['retrieved_at']
            or attempt['path'] != 'responses/' + SOURCE['id'] + '.body'):
        raise ValueError('source identity or actual retrieval differs')
    raw = payloads[PACKET + '/' + attempt['path']]
    texts = {}
    for key, span in review['evidence'].items():
        if runtime.digest(raw[span['start']:span['end']]) != span['sha256']:
            raise ValueError('reviewed evidence bytes differ')
        parsed = realized_milestones._EvidenceHTML(raw, span['start'], span['end'])
        roots = [n for n in parsed.nodes if n.start == span['start'] and n.end == span['end'] and n.tag == span['tag'] and not n.hidden]
        if len(roots) != 1:
            raise ValueError('evidence must be one complete visible HTML element')
        node = roots[0]
        if key == 'title' and (node.tag != 'h1' or node.attrs.get('class') != 'nist-page__title'):
            raise ValueError('complete NIST article title required')
        if key == 'publication' and (node.tag != 'time' or node.attrs.get('datetime') != '2026-07-13T12:00:00Z'):
            raise ValueError('complete NIST publication dateline required')
        if key == 'narrative' and (node.tag != 'div' or node.attrs.get('class') != 'text-with-summary'
                or len([n for n in parsed.nodes if n.tag == 'p']) != 6):
            raise ValueError('complete six-paragraph NIST narrative required')
        texts[key] = parsed.text(span)
    if texts['title'] != SOURCE['title'] or texts['publication'] != 'July 13, 2026':
        raise ValueError('source title or publication day differs')
    if any(marker not in texts['narrative'] for marker in (
            'Robert Bosch Semiconductor LLC', 'Roseville, CA',
            'Sample production has already commenced with commercial production expected to begin in 2026.',
            'more than 60 million SiC chips worldwide since the first generation went into production in 2021')):
        raise ValueError('sample occurrence, separate commercial target and worldwide scope context must all be retained')

    return {name: {'bytes': len(raw), 'sha256': runtime.digest(raw)} for name, raw in payloads.items()}


def open_image(raw, *, version=10):
    connection = sqlite3.connect(':memory:')
    try:
        connection.deserialize(raw)
        connection.row_factory = sqlite3.Row
        database.register_clock_functions(connection)
        connection.execute('PRAGMA foreign_keys=ON')
        if (database.schema_version(connection) != version
                or connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                or connection.execute('PRAGMA foreign_key_check').fetchall()):
            raise ValueError(f'expected intact schema-{version} database image')
        return connection
    except BaseException:
        connection.close()
        raise


def populate(connection, review, inventory, review_sha256, started, admitted, code_sha256):
    if not clock(review['reviewed_at']) <= clock(started) < clock(admitted):
        raise ValueError('claim review must precede actual database admission')
    if database.schema_version(connection) != 10:
        raise ValueError('Bosch admission requires the unchanged schema-10 parent')
    database.apply_migrations(connection, target_version=11, applied_at=admitted)
    subject_id, source_id = [repository.stable_id(k) for k in (SUBJECT['stable_key'], SOURCE_KEY)]
    run_id = repository.stable_id(RULE, review_sha256)
    if (connection.execute('SELECT 1 FROM entities WHERE id=? OR stable_key=?', (subject_id, SUBJECT['stable_key'])).fetchone()
            or connection.execute('SELECT 1 FROM sources WHERE id=? OR stable_key=?', (source_id, SOURCE_KEY)).fetchone()
            or connection.execute('SELECT 1 FROM ingestion_runs WHERE id=?', (run_id,)).fetchone()):
        raise ValueError('Bosch sample production already admitted; not a refresh importer')
    family = connection.execute('SELECT * FROM source_families WHERE stable_key=?', (FAMILY,)).fetchone()
    if family is None:
        raise ValueError('existing NIST source family is required; do not split publisher independence')
    family_id = family['id']
    rights_label = 'NIST informational text; local reviewed use only, no raw redistribution or training authorized by this admission'
    repository.add_source(connection, models.Source(source_id, family_id, SOURCE_KEY,
        'NIST Bosch Roseville sample-production statement', 'NIST / Department of Commerce', 'https://www.nist.gov/', admitted, rights_label))
    document_id = repository.stable_id(source_id, SOURCE['sha256'], SOURCE['retrieved_at'])
    repository.add_source_document(connection, models.SourceDocument(document_id, source_id, SOURCE['url'],
        SOURCE['title'], SOURCE['retrieved_at'], SOURCE['sha256'], published_at=SOURCE['published_at'],
        media_type='text/html', license=rights_label, metadata={'rights': review['rights'],
            'retained_body': PACKET + '/responses/' + SOURCE['id'] + '.body',
            'publication_precision': 'day', 'publication_basis': 'Visible July 13, 2026 dateline; time element is retained in evidence, not a physical event time.',
            'source_native_subject': SUBJECT, 'evidence': review['evidence']}))
    repository.add_ingestion_run(connection, models.IngestionRun(run_id, source_id, started,
        models.IngestionStatus.SUCCEEDED, admitted, code_sha256['semiconductor_atlas/bosch_sample_production.py'],
        document_id, {'rule_version': RULE, 'review_sha256': review_sha256, 'review': review,
            'input_files': inventory, 'code_sha256': code_sha256, 'interpreter': runtime.interpreter(),
            'accepted_at': admitted, 'acceptance_timestamp_basis': 'actual_database_admission', 'boundaries': BOUNDARIES}))
    repository.add_entity(connection, models.Entity(subject_id, models.EntityKind.FACILITY,
        SUBJECT['stable_key'], admitted, SUBJECT['label'], run_id))
    record_id = repository.stable_id(RULE, review_sha256, 'record')
    payload = {'review_sha256': review_sha256, 'source_native_subject': SUBJECT, 'evidence': review['evidence'],
        'rows': rows(), 'core_admitted_at': admitted, 'claim_effective_at': None, 'confidence': None}
    repository.add_source_record(connection, models.SourceRecord(record_id, run_id, document_id,
        SOURCE['id'], SOURCE['retrieved_at'], models.source_record_payload_sha256(payload), payload))
    claims = []
    for row in rows():
        predicate = 'source_reported.milestone.sample_production_start'
        series_key = SUBJECT['stable_key'] + ':' + predicate
        series_id, claim_id = repository.stable_id(series_key), repository.stable_id(RULE, review_sha256, row['key'], 'claim')
        repository.add_claim_series(connection, models.ClaimSeries(series_id, subject_id, series_key,
            predicate, models.ValueKind.MILESTONE, admitted))
        span = review['evidence']['narrative']
        publication = review['evidence']['publication']
        repository.insert_claim(connection, models.ClaimVersion(claim_id, series_id, None, admitted,
            models.ClaimKind.SOURCE_STATEMENT, RULE, None, created_by_run_id=run_id, notes=QUALIFIER),
            models.MilestoneUpperBoundValue(**({k: v for k, v in row.items() if k not in {'key', 'status'}}
                | {'status': models.MilestoneStatus.COMPLETED})),
            evidence=[models.EvidenceLink(document_id, models.EvidenceRole.SUPPORT, record_id,
                f"Complete NIST narrative; UTF-8 bytes [{span['start']},{span['end']}); SHA-256 {span['sha256']}"),
                models.EvidenceLink(document_id, models.EvidenceRole.CONTEXT, record_id,
                f"Publication date anchors upper occurrence bound, not exact event time; UTF-8 bytes [{publication['start']},{publication['end']}); SHA-256 {publication['sha256']}")])
        claims.append({'claim_id': claim_id, 'predicate': predicate, **row})
    issues = repository.validate_database(connection)
    if issues:
        raise ValueError('Bosch successor validation failed: ' + str(issues[:3]))
    connection.commit()
    return {'run_id': run_id, 'admitted_at': admitted, 'subject_id': subject_id, 'claims': claims, 'boundaries': BOUNDARIES}


def admit(*, parent, review_path, reference_root, expected_review_sha256, output,
          verify_only=False, expected_output_sha256=None):
    started, current_codes = now(), codes()
    if current_codes != _LOADED_CODES:
        raise ValueError('Bosch implementation changed since module loading')
    if verify_only:
        runtime._hex(expected_output_sha256, 64)
    elif expected_output_sha256 is not None:
        raise ValueError('successor hash is only an input for verification')
    review_raw = runtime.read(Path(review_path))
    review = validate_review(review_raw, expected_review_sha256)
    payloads = snapshot(reference_root)
    inventory = load_evidence(review, payloads)
    parent_raw = runtime.read(Path(parent), limit=1_000_000_000)
    if runtime.digest(parent_raw) != review['parent_database_sha256']:
        raise ValueError('trusted parent database hash mismatch')
    target = Path(output)
    packet_path = Path(reference_root) / PACKET
    for ancestor in target.absolute().parents:
        if ancestor.exists() and os.path.samefile(ancestor, packet_path):
            raise ValueError('successor output must be outside the retained acquisition packet')
    if not verify_only and (target.exists() or target.is_symlink()):
        raise ValueError('successor output must not exist')
    connection = open_image(parent_raw)
    try:
        if verify_only:
            successor = runtime.read(target, limit=1_000_000_000)
            if runtime.digest(successor) != expected_output_sha256:
                raise ValueError('trusted successor hash mismatch')
            actual = open_image(successor, version=11)
            try:
                run = actual.execute('SELECT * FROM ingestion_runs WHERE id=?', (repository.stable_id(RULE, expected_review_sha256),)).fetchone()
                if run is None or clock(run['completed_at']) > clock(started):
                    raise ValueError('missing or future successor admission')
                admission_start, admitted = run['started_at'], run['completed_at']
            finally:
                actual.close()
        else:
            admission_start, admitted = started, now()
        result = populate(connection, review, inventory, expected_review_sha256, admission_start, admitted, current_codes)
        generated = connection.serialize()
        if verify_only and generated != successor:
            raise ValueError('successor differs from full parent-plus-migration-and-sample-event reconstruction')
        if (runtime.read(Path(review_path)) != review_raw
                or runtime.read(Path(parent), limit=1_000_000_000) != parent_raw
                or snapshot(reference_root) != payloads):
            raise ValueError('evidence or parent changed during admission or replay')
        if codes() != current_codes:
            raise ValueError('Bosch implementation changed during admission or replay')
        if verify_only and runtime.read(target, limit=1_000_000_000) != successor:
            raise ValueError('successor changed during verification')
        if not verify_only:
            runtime.write_new(target, generated)
        return {**result, 'database_sha256': runtime.digest(generated), 'database_bytes': len(generated),
            'verified': verify_only, 'parent_modified': False, 'schema_version': 11,
            'new_capability_statements': 0, 'new_milestones': 1, 'new_numeric_capacity_claims': 0}
    finally:
        connection.close()


_LOADED_CODES = codes()
