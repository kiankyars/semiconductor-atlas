"""Admit one explicitly reviewed source rate into a new, replayable schema-9 image."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from scripts import frozen_runtime as runtime
from . import database, models, realized_milestones, repository, source_checks, source_rates


FORMAT = 'semiconductor-atlas-reviewed-source-rate-admission-v1'
RULE = 'reviewed-source-native-monthly-rate-v1'
BOUNDARIES = dict.fromkeys(('physical_capacity_basis_assigned', 'period_total_inferred',
    'wafer_diameter_inferred', 'wafer_equivalence_inferred', 'current_operation_inferred',
    'finished_wafer_measurement_inferred', 'yield_utilization_qualification_inferred',
    'canonical_identity_assigned', 'geometry_inferred', 'ownership_assigned',
    'independent_measurement', 'historical_system_knowledge_backdated', 'forecast_created',
    'forecast_scored', 'baseline_modified', 'scheduled_execution_rerouted',
    'raw_redistribution', 'model_training', 'source_acquisition'), False)


def now():
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def clock(value):
    return datetime.fromisoformat(models._iso_timestamp(value, 'clock').replace('Z', '+00:00'))


def codes():
    root = Path(__file__).parent.parent
    paths = sorted([*Path(__file__).parent.rglob('*.py'), *Path(__file__).parent.rglob('*.sql'),
                    root / 'scripts/admit_reviewed_source_rates.py', root / 'scripts/frozen_runtime.py'])
    return {p.relative_to(root).as_posix(): runtime.digest(runtime.read(p)) for p in paths}


def relative(value):
    if (not isinstance(value, str) or not value or len(value) > 500
            or PurePosixPath(value).is_absolute() or '\\' in value
            or any(p in ('', '.', '..') for p in value.split('/'))
            or value.split('/')[0] not in ('review_plans', 'artifacts')):
        raise ValueError('evidence paths must be bounded repository-relative paths')
    return value


def validate_review(raw, expected_sha256):
    runtime._hex(expected_sha256, 64)
    if runtime.digest(raw) != expected_sha256:
        raise ValueError('trusted source-rate admission review hash mismatch')
    review = realized_milestones._json(raw)
    runtime._keys(review, {'format', 'decision', 'reviewed_at', 'reviewed_by', 'prior_exposure',
        'parent_database_sha256', 'research_review', 'input_files', 'packets', 'source',
        'subject', 'source_family', 'rate', 'evidence', 'support', 'publication_literal', 'html_structure_review', 'rights', 'boundaries'})
    if review['format'] != FORMAT or review['decision'] != 'accept_one_source_reported_monthly_rate':
        raise ValueError('explicit source-rate acceptance required')
    for field in ('reviewed_by', 'prior_exposure'):
        models._required(review[field], field)
    clock(review['reviewed_at'])
    runtime._hex(review['parent_database_sha256'], 64)
    runtime._keys(review['research_review'], {'path', 'sha256'})
    relative(review['research_review']['path'])
    runtime._hex(review['research_review']['sha256'], 64)
    if review['boundaries'] != BOUNDARIES or any(v is not False for v in review['boundaries'].values()):
        raise ValueError('source-rate excluded-inference boundaries differ')
    if (review['rights'].get('use') != 'local_informational_review_only'
            or any(review['rights'].get(k) is not False for k in ('raw_redistribution', 'model_training'))):
        raise ValueError('source-rate admission does not authorize publication or training')
    models._required(review['rights'].get('basis'), 'rights basis')
    subject = review['subject']
    runtime._keys(review['source_family'], {'stable_key', 'label'})
    for value in review['source_family'].values():
        models._required(value, 'source family field')
    runtime._keys(subject, {'kind', 'stable_key', 'label', 'scope_basis'})
    for field in ('stable_key', 'label', 'scope_basis'):
        models._required(subject[field], field)
    metadata = source_rates.SourceRateMetadata('not-yet-admitted', **review['rate'])
    if subject['kind'] != source_rates.SCOPES[metadata.scope_kind]:
        raise ValueError('source-native entity kind differs from rate scope')
    if not isinstance(review['input_files'], dict) or not 1 <= len(review['input_files']) <= 150:
        raise ValueError('bounded complete evidence inventory required')
    for name, item in review['input_files'].items():
        relative(name)
        runtime._keys(item, {'sha256', 'bytes'})
        runtime._hex(item['sha256'], 64)
        if type(item['bytes']) is not int or not 0 < item['bytes'] <= 4_000_000:
            raise ValueError('invalid evidence byte bound')
    if not isinstance(review['packets'], list) or not 1 <= len(review['packets']) <= 10:
        raise ValueError('bounded retained acquisition packets required')
    if (not isinstance(review['evidence'], dict) or not 3 <= len(review['evidence']) <= 10
            or review['support'] not in review['evidence']
            or not {'title', 'publication_dateline'} <= set(review['evidence'])):
        raise ValueError('complete source support, title and publication context required')
    return review


def snapshot(review, reference_root):
    result = {}
    for name, expected in review['input_files'].items():
        body = runtime.read(Path(reference_root) / name, limit=4_000_000)
        if len(body) != expected['bytes'] or runtime.digest(body) != expected['sha256']:
            raise ValueError('retained source-rate evidence differs: ' + name)
        result[name] = body
    if sum(map(len, result.values())) > 20_000_000:
        raise ValueError('evidence inventory exceeds aggregate bound')
    return result


class RateEvidenceHTML(realized_milestones._EvidenceHTML):
    """Keep strict ancestry, with reviewed stray anchor closers before all evidence."""

    def __init__(self, body, begin, end, structure_review):
        runtime._keys(structure_review, {'mode', 'source_sha256', 'ignored_anchor_end_offsets', 'rationale'})
        if (structure_review['mode'] != 'reviewed_unmatched_anchor_closers_v1'
                or runtime.digest(body) != structure_review['source_sha256']):
            raise ValueError('HTML structure review does not bind the complete source')
        models._required(structure_review['rationale'], 'HTML structure rationale')
        offsets = structure_review['ignored_anchor_end_offsets']
        if (not isinstance(offsets, list) or len(offsets) > 10
                or any(type(p) is not int or not 0 <= p < begin - 3 or body[p:p+4] != b'</a>' for p in offsets)
                or offsets != sorted(set(offsets))):
            raise ValueError('only exact pre-evidence stray anchor end tags may be reviewed')
        self.reviewed_offsets, self.skipped_offsets = set(offsets), set()
        super().__init__(body, begin, end)
        if self.skipped_offsets != self.reviewed_offsets:
            raise ValueError('not all reviewed HTML exceptions were encountered')

    def handle_endtag(self, tag):
        position = self.position()
        if position in self.reviewed_offsets:
            if tag != 'a' or any(node.tag == 'a' for node in self.stack):
                raise ValueError('reviewed anchor closer is not actually unmatched')
            self.skipped_offsets.add(position)
            return
        super().handle_endtag(tag)


def load_evidence(review, payloads, reference_root):
    research_name = review['research_review']['path']
    if runtime.digest(payloads.get(research_name, b'')) != review['research_review']['sha256']:
        raise ValueError('source-rate research interpretation differs')
    research = realized_milestones._json(payloads[research_name])
    if (research['source'] != review['source'] or research['evidence'] != review['evidence']
            or research['claim_acceptance'] is not False or research['collector_enabled'] is not False
            or not clock(research['review_completed_at']) <= clock(review['reviewed_at'])):
        raise ValueError('admission differs from retained source research')
    required = {research_name}
    roots = set()
    documents = []
    latest_policy_finish = None
    for packet in review['packets']:
        runtime._keys(packet, {'packet', 'plan', 'manifest_sha256'})
        root, plan_name = relative(packet['packet']), relative(packet['plan'])
        if root in roots:
            raise ValueError('duplicate packet root')
        roots.add(root)
        manifest_name = root + '/manifest.json'
        if runtime.digest(payloads.get(manifest_name, b'')) != packet['manifest_sha256']:
            raise ValueError('retained acquisition manifest differs')
        manifest = realized_milestones._json(payloads[manifest_name])
        members = {relative(root + '/' + member) for member in manifest} | {manifest_name}
        directory = Path(reference_root) / root
        os.close(runtime._directory(directory))
        paths = list(directory.rglob('*'))
        if any(p.is_symlink() for p in paths):
            raise ValueError('symlink in retained evidence packet')
        if {p.relative_to(reference_root).as_posix() for p in paths if p.is_file()} != members:
            raise ValueError('complete retained acquisition inventory differs')
        required |= members | {plan_name}
        for member, identity in manifest.items():
            runtime._keys(identity, {'bytes', 'sha256'})
            body = payloads.get(root + '/' + member, b'')
            if len(body) != identity['bytes'] or runtime.digest(body) != identity['sha256']:
                raise ValueError('retained acquisition member differs')
        if payloads.get(plan_name) != payloads.get(root + '/plan.json'):
            raise ValueError('reviewed and retained acquisition plans differ')
        plan = realized_milestones._json(payloads[plan_name])
        if any(plan.get(field) is not False for field in ('claim_acceptance', 'redirects', 'retries', 'cookies')):
            raise ValueError('acquisition exceeds reviewed bounded scope')
        for index, dependency in enumerate(plan.get('dependencies', [])):
            name = relative(dependency['path'])
            body = payloads.get(name, b'')
            if (runtime.digest(body) != dependency['sha256']
                    or body != payloads.get(root + f'/dependencies/{index}-' + PurePosixPath(name).name)):
                raise ValueError('original and retained policy dependencies differ')
        ledger = realized_milestones._json(payloads[root + '/source_checks.json'])
        entries = plan.get('entries', [plan.get('entry')])
        if len(ledger['attempts']) != len(entries):
            raise ValueError('acquisition stopped before all reviewed entries were checked')
        for entry, attempt in zip(entries, ledger['attempts']):
            if any(entry[k] != attempt[k] for k in ('id', 'url', 'purpose', 'source_family')):
                raise ValueError('acquisition entry identity differs')
            if attempt['redirect_count'] != 0 or attempt['final_url'] != entry['url']:
                raise ValueError('unreviewed automatic redirect')
            if entry['purpose'] == 'source_document':
                if (plan.get('decision') != 'approved_exact_source_url_after_retained_policy_review'
                        or attempt['outcome'] != 'succeeded' or attempt['http_status'] != 200
                        or (attempt['content_type'] or '').split(';')[0] != 'text/html'
                        or not clock(plan['reviewed_at']) <= clock(attempt['started_at'])):
                    raise ValueError('source acquisition lacks explicit reviewed identity or successful HTML')
                documents.append((root, attempt, plan))
            elif attempt['outcome'] == 'succeeded':
                latest_policy_finish = max(latest_policy_finish or clock(attempt['finished_at']), clock(attempt['finished_at']))
    if set(payloads) != required or len(documents) != 1:
        raise ValueError('exact complete graph with one source document required')
    with tempfile.TemporaryDirectory(prefix='atlas-rate-evidence-') as temporary:
        private = Path(temporary).resolve()
        for name, body in payloads.items():
            p = private / name
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open('xb') as stream:
                stream.write(body)
        for packet in review['packets']:
            source_checks.validate_source_checks(private / packet['packet'] / 'source_checks.json', private / packet['packet'])
    root, attempt, plan = documents[0]
    source = review['source']
    body = payloads.get(relative(source['retained_path']), b'')
    if (source['url'] != attempt['url'] or source['sha256'] != attempt['sha256']
            or source['bytes'] != attempt['bytes'] or source['retained_path'] != root + '/' + attempt['path']
            or source['retrieval_started_at'] != attempt['started_at']
            or source['retrieval_finished_at'] != attempt['finished_at']
            or runtime.digest(body) != source['sha256'] or len(body) != source['bytes']
            or source['media_type'] != 'text/html' or source['publication_precision'] != 'day'
            or not clock(source['retrieval_finished_at']) <= clock(research['review_completed_at'])
            or latest_policy_finish is None or not latest_policy_finish <= clock(plan['reviewed_at'])):
        raise ValueError('source identity or policy/research chronology differs')
    models._iso_date(source['publication_date'], 'publication_date')
    if (datetime.strptime(review['publication_literal'], '%B %d, %Y').date().isoformat() != source['publication_date']
            or source['publication_date'] > clock(source['retrieval_finished_at']).date().isoformat()):
        raise ValueError('publication literal/date or retrieval chronology differs')
    texts = {}
    for label, span in review['evidence'].items():
        runtime._keys(span, {'start', 'end', 'sha256', 'tag'})
        if (type(span['start']) is not int or type(span['end']) is not int
                or not 0 <= span['start'] < span['end'] <= len(body)
                or runtime.digest(body[span['start']:span['end']]) != span['sha256']):
            raise ValueError('reviewed rate evidence span differs')
        parsed = RateEvidenceHTML(body, span['start'], span['end'], review['html_structure_review'])
        nodes = [n for n in parsed.nodes if n.start == span['start'] and n.end == span['end']
                 and n.tag == span['tag'] and not n.hidden]
        if len(nodes) != 1 or span['tag'] not in ('h1', 'h2', 'p', 'div', 'span', 'a'):
            raise ValueError('rate evidence requires a complete non-hidden HTML element')
        texts[label] = parsed.text(span)
    if (texts['title'] != source['title'] or texts[review['support']] != review['rate']['source_wording']
            or review['publication_literal'] != texts['publication_dateline']):
        raise ValueError('source title, date or complete rate support text differs')
    return source


def open_image(raw, version):
    connection = sqlite3.connect(':memory:')
    try:
        connection.deserialize(raw)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        if (database.schema_version(connection) != version
                or connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                or connection.execute('PRAGMA foreign_key_check').fetchall()):
            raise ValueError(f'expected intact schema-{version} image')
        return connection
    except BaseException:
        connection.close()
        raise


def populate(connection, review, review_sha256, started, admitted, code_sha256):
    if not clock(review['reviewed_at']) <= clock(started) < clock(admitted):
        raise ValueError('review/admission chronology differs')
    if database.apply_migrations(connection, target_version=9) != [9]:
        raise ValueError('only additive schema-9 migration expected')
    connection.execute('UPDATE schema_migrations SET applied_at=? WHERE version=9', (admitted,))
    source, subject = review['source'], review['subject']
    family_key = review['source_family']['stable_key']
    source_key = 'reviewed-source-rate:' + source['url']
    family_id, source_id, entity_id = [repository.stable_id(k) for k in (family_key, source_key, subject['stable_key'])]
    run_id = repository.stable_id(RULE, review_sha256)
    if (connection.execute('SELECT 1 FROM entities WHERE id=? OR stable_key=?', (entity_id, subject['stable_key'])).fetchone()
            or connection.execute('SELECT 1 FROM sources WHERE id=? OR stable_key=?', (source_id, source_key)).fetchone()):
        raise ValueError('source-native rate already admitted; this is not a refresh importer')
    family = connection.execute('SELECT * FROM source_families WHERE stable_key=?', (family_key,)).fetchone()
    if family:
        family_id = family['id']
    else:
        repository.add_source_family(connection, models.SourceFamily(family_id, family_key,
            review['source_family']['label'], admitted))
    rights = 'Local informational review only; raw publication and training not authorized by this admission'
    repository.add_source(connection, models.Source(source_id, family_id, source_key,
        source['title'], source['publisher'], source['url'], admitted, rights))
    document_id = repository.stable_id(source_id, source['sha256'], source['retrieval_finished_at'])
    repository.add_source_document(connection, models.SourceDocument(document_id, source_id, source['url'],
        source['title'], source['retrieval_finished_at'], source['sha256'], published_at=source['publication_date'],
        media_type='text/html', license=rights, metadata={'rights': review['rights'],
            'retained_body': source['retained_path'], 'research_review': review['research_review'],
            'publication_basis': source['publication_basis'], 'scope_basis': subject['scope_basis']}))
    repository.add_ingestion_run(connection, models.IngestionRun(run_id, source_id, started,
        models.IngestionStatus.SUCCEEDED, admitted, code_sha256['semiconductor_atlas/reviewed_source_rates.py'],
        document_id, {'rule_version': RULE, 'review_sha256': review_sha256, 'review': review,
            'code_sha256': code_sha256, 'interpreter': runtime.interpreter(),
            'boundaries': BOUNDARIES, 'accepted_at': admitted}))
    repository.add_entity(connection, models.Entity(entity_id, models.EntityKind(subject['kind']),
        subject['stable_key'], admitted, subject['label'], run_id))
    claim_id, record_id = [repository.stable_id(RULE, review_sha256, kind) for kind in ('claim', 'record')]
    metadata = source_rates.SourceRateMetadata(claim_id, **review['rate'])
    key = metadata.series_key(entity_id)
    series_id = repository.stable_id(key)
    repository.add_claim_series(connection, models.ClaimSeries(series_id, entity_id, key,
        'source_rate.' + metadata.metric, models.ValueKind.SCALAR, admitted))
    payload = {'source_rate': asdict(metadata), 'scope_basis': subject['scope_basis'],
               'review_sha256': review_sha256, 'core_admitted_at': admitted, 'physical_capacity_basis': None}
    repository.add_source_record(connection, models.SourceRecord(record_id, run_id, document_id,
        'reviewed-monthly-rate', source['retrieval_finished_at'], models.source_record_payload_sha256(payload), payload))
    evidence = [models.EvidenceLink(document_id,
        models.EvidenceRole.SUPPORT if label == review['support'] else models.EvidenceRole.CONTEXT, record_id,
        f"{label}; UTF-8 bytes [{span['start']},{span['end']}); SHA-256 {span['sha256']}")
        for label, span in sorted(review['evidence'].items())]
    with repository._atomic(connection):
        repository.insert_claim(connection, models.ClaimVersion(claim_id, series_id, None, admitted,
            models.ClaimKind.SOURCE_STATEMENT, RULE, None, created_by_run_id=run_id,
            notes=subject['scope_basis']), metadata.scalar(), evidence=evidence)
        source_rates.add_metadata(connection, metadata)
    issues = repository.validate_database(connection)
    if issues:
        raise ValueError('source-rate successor validation failed: ' + str(issues[:3]))
    connection.commit()
    return {'run_id': run_id, 'admitted_at': admitted, 'claim_id': claim_id,
            'source_native_entity_id': entity_id, 'source_rate': asdict(metadata), 'boundaries': BOUNDARIES}


def admit(*, parent, review_path, reference_root, expected_review_sha256, output,
          verify_only=False, expected_output_sha256=None):
    started, current_codes = now(), codes()
    if current_codes != _LOADED_CODES:
        raise ValueError('rate implementation changed since module loading')
    if verify_only:
        runtime._hex(expected_output_sha256, 64)
    elif expected_output_sha256 is not None:
        raise ValueError('successor hash is only an input for verification')
    review_raw = runtime.read(Path(review_path))
    review = validate_review(review_raw, expected_review_sha256)
    payloads = snapshot(review, reference_root)
    load_evidence(review, payloads, reference_root)
    parent_raw = runtime.read(Path(parent), limit=1_000_000_000)
    if runtime.digest(parent_raw) != review['parent_database_sha256']:
        raise ValueError('trusted parent database hash mismatch')
    target = Path(output)
    if not verify_only and (target.exists() or target.is_symlink()):
        raise ValueError('successor output must not exist')
    connection = open_image(parent_raw, 8)
    try:
        if verify_only:
            successor = runtime.read(target, limit=1_000_000_000)
            if runtime.digest(successor) != expected_output_sha256:
                raise ValueError('trusted successor hash mismatch')
            actual = open_image(successor, 9)
            try:
                run = actual.execute('SELECT * FROM ingestion_runs WHERE id=?',
                    (repository.stable_id(RULE, expected_review_sha256),)).fetchone()
                if run is None or clock(run['completed_at']) > clock(started):
                    raise ValueError('missing or future source-rate admission')
                admission_start, admitted = run['started_at'], run['completed_at']
            finally:
                actual.close()
        else:
            admission_start, admitted = started, now()
        result = populate(connection, review, expected_review_sha256, admission_start, admitted, current_codes)
        generated = connection.serialize()
        if verify_only and generated != successor:
            raise ValueError('successor differs from complete parent-plus-rate reconstruction')
        if (runtime.read(Path(review_path)) != review_raw
                or runtime.read(Path(parent), limit=1_000_000_000) != parent_raw
                or snapshot(review, reference_root) != payloads or codes() != current_codes):
            raise ValueError('source-rate inputs or implementation changed during admission/replay')
        load_evidence(review, payloads, reference_root)
        if verify_only and runtime.read(target, limit=1_000_000_000) != successor:
            raise ValueError('successor changed during verification')
        if not verify_only:
            runtime.write_new(target, generated)
        return {**result, 'database_sha256': runtime.digest(generated), 'database_bytes': len(generated),
                'verified': verify_only, 'parent_modified': False, 'new_source_rates': 1,
                'schema_version': 9, 'new_numeric_capacity_claims': 0}
    finally:
        connection.close()


_LOADED_CODES = codes()
