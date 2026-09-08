"""Admit three reviewed historical events from one retained Tower issuer release."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from scripts import frozen_runtime as runtime
from . import database, models, realized_milestones, repository, source_checks


RULE = 'tower-source-native-closure-milestones-v1'
FORMAT = 'semiconductor-atlas-tower-closure-admission-review-v1'
DECISION = 'accept_three_historical_source_native_milestone_statements'
SOURCE_SHA = '7945c366226cf0564916fa2a4f946c663f90900c2a41c4bfaad5c9d7460fe255'
RESEARCH_SHA = 'bba060c31cc740d5abf0ff3a50445275b6d8b374b3a7fc16023a0c65a750237b'
SOURCE_URL = 'https://towersemi.com/2025/02/10/02102025/'
SUBJECTS = {
    'fab1': {'kind': 'facility', 'stable_key': 'tower-issuer:fab1-source-native-unresolved',
             'label': 'Tower Fab1 (source-native, canonical identity unresolved)',
             'scope': 'Fab1 as named in the issuer release; legacy-flow discontinuation and last output are distinct events on the same facility, not separate facilities.'},
    'arai': {'kind': 'facility', 'stable_key': 'tower-issuer:arai-source-native-unresolved',
             'label': 'Tower-reported Arai facility (source-native, canonical identity unresolved)',
             'scope': 'The Arai facility named in the issuer restructuring context; not all Japanese operations.'},
}
RIGHTS = {'use': 'local_personal_informational_noncommercial_review_only',
    'basis': 'Retained Tower main-origin terms sections 1 and 3; preserve original materials and notices, attribute Tower and do not transfer raw copies onward.',
    'raw_redistribution': False, 'commercial_use_authorized': False,
    'model_training_authorized': False, 'ir_subdomain_retention_authorized': False}
BOUNDARIES = dict.fromkeys(('current_physical_state_inferred', 'zero_capacity_inferred',
    'numeric_capacity_loss_inferred', 'exact_day_or_midpoint_inferred', 'transfer_event_date_assigned',
    'yield_utilization_qualification_inferred', 'canonical_identity_assigned', 'geometry_inferred',
    'location_from_dateline_inferred', 'ownership_assigned', 'independent_measurement',
    'historical_system_knowledge_backdated', 'forecast_created', 'forecast_scored', 'baseline_modified',
    'scheduled_execution_rerouted', 'raw_redistribution', 'model_training', 'source_acquisition'), False)


def now():
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def clock(value):
    return datetime.fromisoformat(models._iso_timestamp(value, 'clock').replace('Z', '+00:00'))


def codes():
    root = Path(__file__).parent.parent
    paths = sorted([*Path(__file__).parent.rglob('*.py'), *Path(__file__).parent.rglob('*.sql'),
                    root / 'scripts/admit_tower_closure_milestones.py', root / 'scripts/frozen_runtime.py'])
    return {str(p.relative_to(root)): runtime.digest(runtime.read(p)) for p in paths}


def rows():
    specifications = [
        ('tower-fab1-legacy-flow-discontinuation', 'fab1', 'legacy_150mm_flow_discontinuation',
         '2024-10-01', '2024-12-31', 'quarter', 'fourth quarter of 2024',
         ['fab_consolidation_heading', 'complete_fab_consolidation_paragraph'],
         'Legacy-flow discontinuation is distinct from the later last wafer output, facility demolition or loss of all company capacity.'),
        ('tower-fab1-last-fab-output', 'fab1', 'last_fab_output',
         '2025-01-01', '2025-01-31', 'month', 'January 2025',
         ['fab_consolidation_heading', 'complete_fab_consolidation_paragraph'],
         'Last output has no numeric volume, exact day, restart prohibition or independently established permanent building closure.'),
        ('tower-arai-operations-cessation', 'arai', 'operations_cessation',
         '2022-01-01', '2022-12-31', 'year', 'during 2022',
         ['non_gaap_heading', 'complete_arai_context_paragraph'],
         'Historical cessation reported in restructuring accounting context; not all Japanese fabs, a current physical state or independently measured capacity loss.'),
    ]
    return [{'key': key, 'subject_key': subject, 'value': asdict(models.MilestoneValue(kind,
                models.MilestoneStatus.COMPLETED, low, None, high, precision, literal)),
             'support': support, 'scope_limit': scope}
            for key, subject, kind, low, high, precision, literal, support, scope in specifications]


def research_candidates():
    labels = ['Tower Fab1 legacy 150mm flows', 'Tower Fab1', 'Tower-reported Arai facility']
    return [{'key': row['key'], 'source_native_subject': label, 'subject_kind': 'facility',
             'canonical_entity_id': None, **row['value'], 'support': row['support'],
             'scope_limit': row['scope_limit']} for row, label in zip(rows(), labels)]


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
        raise ValueError('trusted Tower admission review hash mismatch')
    review = realized_milestones._json(raw)
    runtime._keys(review, {'format', 'decision', 'reviewed_at', 'reviewed_by', 'prior_exposure',
        'selection', 'parent_database_sha256', 'research_review', 'input_files', 'subjects', 'rows',
        'rights', 'boundaries', 'interpretation_method'})
    if review['format'] != FORMAT or review['decision'] != DECISION:
        raise ValueError('explicit source-specific Tower admission review required')
    for field in ('reviewed_by', 'prior_exposure', 'selection', 'interpretation_method'):
        models._required(review[field], field)
    clock(review['reviewed_at'])
    runtime._hex(review['parent_database_sha256'], 64)
    if review['subjects'] != SUBJECTS or review['rights'] != RIGHTS or review['boundaries'] != BOUNDARIES:
        raise ValueError('Tower subjects, rights or excluded-inference boundaries differ')
    if any(value is not False for value in review['boundaries'].values()):
        raise ValueError('Tower boundaries must be boolean false')
    if any(review['rights'][field] is not False for field in
           ('raw_redistribution', 'commercial_use_authorized', 'model_training_authorized',
            'ir_subdomain_retention_authorized')):
        raise ValueError('Tower rights permissions must be boolean false')
    if review['rows'] != rows():
        raise ValueError('complete three-event selection or its semantics differs')
    runtime._keys(review['research_review'], {'path', 'sha256'})
    relative(review['research_review']['path'])
    if review['research_review']['sha256'] != RESEARCH_SHA:
        raise ValueError('the reviewed retained-HTML interpretation differs')
    if not isinstance(review['input_files'], dict) or len(review['input_files']) != 20:
        raise ValueError('the complete twenty-file evidence inventory is required')
    for name, item in review['input_files'].items():
        relative(name)
        runtime._keys(item, {'sha256', 'bytes'})
        runtime._hex(item['sha256'], 64)
        if type(item['bytes']) is not int or not 0 < item['bytes'] <= 4_000_000:
            raise ValueError('invalid evidence byte bound')
    return review


def snapshot(review, reference_root):
    result = {}
    for name, expected in review['input_files'].items():
        raw = runtime.read(Path(reference_root) / name, limit=4_000_000)
        if len(raw) != expected['bytes'] or runtime.digest(raw) != expected['sha256']:
            raise ValueError('retained Tower input bytes differ: ' + name)
        result[name] = raw
    if sum(map(len, result.values())) > 12_000_000:
        raise ValueError('evidence inventory exceeds total byte bound')
    return result


def _packet_names(root, packet):
    path = Path(root) / relative(packet)
    os.close(runtime._directory(path))
    names = set()
    for p in path.rglob('*'):
        if p.is_symlink():
            raise ValueError('symlinks are not retained packet members')
        if p.is_file():
            names.add(p.relative_to(root).as_posix())
    return names


def load_evidence(review, payloads, reference_root):
    """Verify the full retained graph and the selected complete HTML elements offline."""
    research_name = review['research_review']['path']
    if runtime.digest(payloads.get(research_name, b'')) != RESEARCH_SHA:
        raise ValueError('retained source research review is missing or changed')
    research = realized_milestones._json(payloads[research_name])
    source, acquisition = research['source'], research['acquisition']
    if (source['sha256'] != SOURCE_SHA or source['url'] != SOURCE_URL
            or source['publication_date'] != '2025-02-10' or source['publication_precision'] != 'day'
            or source['media_type'] != 'text/html' or research['claim_acceptance'] is not False
            or research['collector_enabled'] is not False or research['rights'] != RIGHTS
            or research['event_candidates'] != research_candidates()):
        raise ValueError('research is not the retained, unadmitted Tower three-event source review')
    if len(acquisition['policy_probes']) != 2:
        raise ValueError('both retained policy probes are required')
    required, packets = {research_name}, [*acquisition['policy_probes'], acquisition]
    if len({p['packet'] for p in packets}) != 3:
        raise ValueError('policy probes and source capture must be distinct packets')
    for packet in packets:
        name = relative(packet['packet'])
        manifest_name, plan_name = name + '/manifest.json', relative(packet['plan'])
        if runtime.digest(payloads.get(manifest_name, b'')) != packet['manifest_sha256']:
            raise ValueError('retained packet manifest differs')
        manifest = realized_milestones._json(payloads[manifest_name])
        expected_names = {relative(name + '/' + member) for member in manifest} | {manifest_name}
        if _packet_names(reference_root, name) != expected_names:
            raise ValueError('complete retained packet inventory differs')
        required |= expected_names | {plan_name}
        for member, identity in manifest.items():
            runtime._keys(identity, {'sha256', 'bytes'})
            body = payloads.get(name + '/' + member, b'')
            if len(body) != identity['bytes'] or runtime.digest(body) != identity['sha256']:
                raise ValueError('retained packet member differs')
        if (payloads.get(plan_name) != payloads.get(name + '/plan.json')
                or runtime.digest(payloads.get(plan_name, b'')) != packet['plan_sha256']):
            raise ValueError('original and packet acquisition plans differ')
    if set(payloads) != required:
        raise ValueError('evidence graph contains missing or unrelated files')
    capture = acquisition['packet']
    plan = realized_milestones._json(payloads[acquisition['plan']])
    if (plan['decision'] != 'approved_exact_url_local_personal_informational_noncommercial_review_only'
            or any(plan[field] is not False for field in ('raw_redistribution', 'claim_acceptance',
                'collector_enabled', 'redirects', 'retries', 'cookies'))
            or plan['minimum_interval_seconds'] != 10
            or [entry['url'] for entry in plan['entries']] != [SOURCE_URL]
            or len(plan['dependencies']) != 4):
        raise ValueError('retained exact-URL local-use acquisition approval differs')
    dependency_names = set()
    for i, dependency in enumerate(plan['dependencies']):
        name = relative(dependency['path'])
        dependency_names.add(name)
        body = payloads.get(name, b'')
        copy_name = capture + f'/dependencies/{i}-' + PurePosixPath(name).name
        if runtime.digest(body) != dependency['sha256'] or body != payloads.get(copy_name):
            raise ValueError('original and copied policy dependencies differ')
    probe_files = {name for p in acquisition['policy_probes'] for name in required
                   if name.startswith(p['packet'] + '/') and
                   (name.endswith('/source_checks.json') or '/responses/' in name)}
    if dependency_names != probe_files:
        raise ValueError('acquisition dependencies must bind both policy bodies and ledgers')
    source_name = relative(source['retained_path'])
    body = payloads.get(source_name, b'')
    if runtime.digest(body) != SOURCE_SHA or len(body) != source['bytes']:
        raise ValueError('exact reviewed HTML bytes are required')
    if runtime.digest(payloads[capture + '/source_checks.json']) != acquisition['ledger_sha256']:
        raise ValueError('source acquisition ledger differs')
    with tempfile.TemporaryDirectory(prefix='atlas-tower-evidence-') as directory:
        private = Path(directory).resolve()
        for name, raw in payloads.items():
            path = private / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(raw)
        reports = [source_checks.validate_source_checks(private / p['packet'] / 'source_checks.json',
                   private / p['packet']) for p in packets]
    if any(r['attempt_count'] != 1 or any(g['failed'] for g in r['groups']) for r in reports):
        raise ValueError('three single-attempt successful acquisition ledgers are required')
    attempts = [realized_milestones._json(payloads[p['packet'] + '/source_checks.json'])['attempts'][0]
                for p in packets]
    if ([a['purpose'] for a in attempts] != ['access_policy', 'rights_policy', 'source_document']
            or any(a['redirect_count'] != 0 for a in attempts)):
        raise ValueError('the ordered robots, terms and source no-redirect attempts are required')
    for prior, later in zip(attempts, attempts[1:]):
        if (clock(later['started_at']) - clock(prior['finished_at'])).total_seconds() < 10:
            raise ValueError('policy/source acquisition chronology violates the ten-second interval')
    document = attempts[-1]
    if (document['url'] != SOURCE_URL or document['sha256'] != SOURCE_SHA
            or document['started_at'] != source['retrieval_started_at']
            or document['finished_at'] != source['retrieval_finished_at']
            or capture + '/' + document['path'] != source_name):
        raise ValueError('HTML retrieval identity or actual clock differs')
    if (not clock(plan['reviewed_at']) <= clock(document['started_at']) <= clock(document['finished_at']) < clock(plan['expires_at'])
            or not clock(document['finished_at']) <= clock(research['review_completed_at']) <= clock(review['reviewed_at'])):
        raise ValueError('capture, interpretation and claim-review chronology differs')
    evidence = research['evidence']
    labels = {'title', 'publication_dateline', 'fab_consolidation_heading',
              'complete_fab_consolidation_paragraph', 'non_gaap_heading', 'complete_arai_context_paragraph'}
    if set(evidence) != labels:
        raise ValueError('all six reviewed complete HTML blocks are required')
    texts = {}
    for label, span in evidence.items():
        runtime._keys(span, {'start', 'end', 'sha256', 'tag'})
        if (type(span['start']) is not int or type(span['end']) is not int
                or not 0 <= span['start'] < span['end'] <= len(body)
                or runtime.digest(body[span['start']:span['end']]) != span['sha256']):
            raise ValueError('reviewed HTML byte span differs')
        parsed = realized_milestones._EvidenceHTML(body, span['start'], span['end'])
        nodes = [node for node in parsed.nodes if node.start == span['start'] and node.end == span['end']
                 and node.tag == span['tag'] and not node.hidden]
        if len(nodes) != 1 or span['tag'] != ('h1' if label == 'title' else 'p'):
            raise ValueError('evidence must select a complete non-hidden reviewed HTML element')
        texts[label] = parsed.text(span)
    if (texts['title'] != source['title'] or 'February 10, 2025' not in texts['publication_dateline']
            or texts['fab_consolidation_heading'] != '6” Fab Consolidation Update'
            or texts['non_gaap_heading'] != 'Non-GAAP Financial Measures'):
        raise ValueError('source title, dateline or section context differs')
    required_literals = {
        'complete_fab_consolidation_paragraph': ('During the fourth quarter of 2024',
            'legacy of 150mm flows were discontinued in Fab1', 'last Fab outs occurring in January 2025',
            'have been transferred into the Fab2 200mm factory'),
        'complete_arai_context_paragraph': ('the cessation of operations of the Arai facility, which occurred during 2022',),
    }
    if any(literal not in texts[label] for label, literals in required_literals.items() for literal in literals):
        raise ValueError('the exact reviewed event and facility clauses differ')
    return {'research': research, 'source': source, 'input_files': review['input_files']}


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


def populate(connection, review, data, review_sha256, started, admitted, code_sha256):
    if not clock(review['reviewed_at']) <= clock(started) < clock(admitted):
        raise ValueError('Tower review/admission chronology differs')
    source = data['source']
    family_key, source_key = 'tower-issuer', 'issuer-html:' + SOURCE_URL
    family_id, source_id = [repository.stable_id(key) for key in (family_key, source_key)]
    entity_ids = {key: repository.stable_id(subject['stable_key']) for key, subject in SUBJECTS.items()}
    run_id = repository.stable_id(RULE, review_sha256)
    if (any(connection.execute('SELECT 1 FROM entities WHERE id=? OR stable_key=?',
                (entity_ids[key], subject['stable_key'])).fetchone() for key, subject in SUBJECTS.items())
            or connection.execute('SELECT 1 FROM sources WHERE id=? OR stable_key=?', (source_id, source_key)).fetchone()
            or connection.execute('SELECT 1 FROM ingestion_runs WHERE id=?', (run_id,)).fetchone()):
        raise ValueError('Tower source/facilities already admitted; this is not a refresh importer')
    family = connection.execute('SELECT * FROM source_families WHERE stable_key=?', (family_key,)).fetchone()
    if family:
        family_id = family['id']
    else:
        repository.add_source_family(connection, models.SourceFamily(family_id, family_key,
            'Tower issuer disclosures', admitted))
    rights_label = 'Local personal informational noncommercial review only; no raw redistribution or training approval'
    repository.add_source(connection, models.Source(source_id, family_id, source_key,
        source['title'], source['publisher'], SOURCE_URL, admitted, rights_label))
    document_id = repository.stable_id(source_id, SOURCE_SHA, source['retrieval_finished_at'])
    repository.add_source_document(connection, models.SourceDocument(document_id, source_id, SOURCE_URL,
        source['title'], source['retrieval_finished_at'], SOURCE_SHA, published_at=source['publication_date'],
        media_type='text/html', license=rights_label, metadata={'rights': RIGHTS,
            'retained_body': source['retained_path'], 'research_review': review['research_review'],
            'publication_precision': 'day', 'publication_basis': 'Complete release dateline; not CMS modification or first availability of each statement.',
            'source_native_subjects': SUBJECTS, 'interpretation_method': review['interpretation_method']}))
    repository.add_ingestion_run(connection, models.IngestionRun(run_id, source_id, started,
        models.IngestionStatus.SUCCEEDED, admitted, code_sha256['semiconductor_atlas/tower_closure_milestones.py'],
        document_id, {'rule_version': RULE, 'review_sha256': review_sha256, 'review': review,
            'code_sha256': code_sha256, 'interpreter': runtime.interpreter(), 'input_files': data['input_files'],
            'rights': RIGHTS, 'boundaries': BOUNDARIES, 'accepted_at': admitted,
            'acceptance_timestamp_basis': 'actual_database_admission',
            'evidence_selection': 'Two support blocks plus title/dateline context per claim; twelve links.',
            'interpretation_limits': data['research']['excluded_inferences']}))
    for key, subject in SUBJECTS.items():
        repository.add_entity(connection, models.Entity(entity_ids[key], models.EntityKind.FACILITY,
            subject['stable_key'], admitted, subject['label'], run_id))
    claims = []
    for row in review['rows']:
        subject = SUBJECTS[row['subject_key']]
        value = models.MilestoneValue(**{**row['value'], 'status': models.MilestoneStatus(row['value']['status'])})
        predicate = 'milestone.' + value.milestone_type
        series_key = subject['stable_key'] + ':' + predicate
        series_id = repository.stable_id(series_key)
        claim_id, record_id = [repository.stable_id(RULE, review_sha256, row['key'], kind) for kind in ('claim', 'record')]
        repository.add_claim_series(connection, models.ClaimSeries(series_id, entity_ids[row['subject_key']],
            series_key, predicate, models.ValueKind.MILESTONE, admitted))
        payload = {'review_sha256': review_sha256, 'source_native_subject': subject, 'milestone': row,
            'evidence_spans': data['research']['evidence'], 'core_admitted_at': admitted,
            'claim_effective_at': None, 'confidence': None}
        repository.add_source_record(connection, models.SourceRecord(record_id, run_id, document_id,
            row['key'], source['retrieval_finished_at'], models.source_record_payload_sha256(payload), payload))
        evidence = []
        for label in [*row['support'], 'title', 'publication_dateline']:
            span = data['research']['evidence'][label]
            evidence.append(models.EvidenceLink(document_id,
                models.EvidenceRole.SUPPORT if label in row['support'] else models.EvidenceRole.CONTEXT,
                record_id, f"{label}; UTF-8 bytes [{span['start']},{span['end']}); SHA-256 {span['sha256']}"))
        repository.insert_claim(connection, models.ClaimVersion(claim_id, series_id, None, admitted,
            models.ClaimKind.SOURCE_STATEMENT, RULE, None, created_by_run_id=run_id,
            notes=row['scope_limit']), value, evidence=evidence)
        claims.append({'claim_id': claim_id, 'key': row['key'], 'subject_id': entity_ids[row['subject_key']],
                       'value': row['value']})
    issues = repository.validate_database(connection)
    if issues:
        raise ValueError('Tower successor validation failed: ' + str(issues[:3]))
    connection.commit()
    return {'run_id': run_id, 'admitted_at': admitted, 'source_native_subject_ids': entity_ids,
            'milestones': claims, 'boundaries': BOUNDARIES}


def admit(*, parent, review_path, reference_root, expected_review_sha256, output,
          verify_only=False, expected_output_sha256=None):
    started, current_codes = now(), codes()
    if current_codes != _LOADED_CODES:
        raise ValueError('Tower implementation changed since module loading')
    if verify_only:
        runtime._hex(expected_output_sha256, 64)
    elif expected_output_sha256 is not None:
        raise ValueError('successor hash is only an input for verification')
    review_raw = runtime.read(Path(review_path))
    review = validate_review(review_raw, expected_review_sha256)
    payloads = snapshot(review, reference_root)
    data = load_evidence(review, payloads, reference_root)
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
            actual = open_image(successor, 8)
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
        result = populate(connection, review, data, expected_review_sha256, admission_start, admitted, current_codes)
        generated = connection.serialize()
        if verify_only and generated != successor:
            raise ValueError('successor differs from complete parent-plus-events reconstruction')
        if (runtime.read(Path(review_path)) != review_raw
                or runtime.read(Path(parent), limit=1_000_000_000) != parent_raw
                or snapshot(review, reference_root) != payloads):
            raise ValueError('Tower evidence or parent changed during admission or replay')
        load_evidence(review, payloads, reference_root)
        if codes() != current_codes:
            raise ValueError('Tower implementation changed during admission or replay')
        if verify_only and runtime.read(target, limit=1_000_000_000) != successor:
            raise ValueError('successor changed during verification')
        if not verify_only:
            runtime.write_new(target, generated)
        return {**result, 'database_sha256': runtime.digest(generated), 'database_bytes': len(generated),
                'verified': verify_only, 'parent_modified': False, 'new_milestones': 3,
                'schema_version': 8, 'new_numeric_capacity_claims': 0}
    finally:
        connection.close()


_LOADED_CODES = codes()
