"""Admit two explicitly reviewed K18B source statements, never production capacity."""

from __future__ import annotations

import sqlite3
from contextlib import ExitStack
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from scripts import frozen_runtime as runtime
from . import curated_capture, curated_review, database, models, realized_milestones, repository
from .discovery_handoff import _binding, _candidate, _current_source, _file_state, _history, _one


RULE = 'ase-k18b-reviewed-source-milestones-v1'
FORMAT = 'semiconductor-atlas-ase-k18b-claim-admission-review-v1'
DECISION = 'accept_two_source_native_k18b_milestone_statements'
SOURCE_URL = 'https://invest.kcg.gov.tw/News_Content.aspx?n=8CE973AC5F6EE514&s=BA84DF962F234D8E&sms=D7173346CB507235'
SOURCE_SHA = '34c23c7e387e8f7e3b25b99939839b84cb68bbf52399f7763a003202a0234380'
SCOPE_KEY = 'ase:kaohsiung-k18b-source-native-project'
PUBLISHER = 'Economic Development Bureau, Kaohsiung City Government'
PROJECT = {'source_native_subject': 'K18B',
    'label': 'ASE K18B building project (source-native, canonical identity unresolved)',
    'scope': 'One K18B building project; not the Kaohsiung campus aggregate, Dashe K28 or Nanzih Third Park.',
    'canonical_facility_assignment': None}
BOUNDARIES = dict.fromkeys(('baseline_modified', 'current_stage_reaffirmed', 'canonical_assignment',
    'production_attainment', 'capacity_claim', 'geometry_inferred', 'ownership_assigned',
    'qualification_yield_utilization_inferred', 'independent_corroboration', 'forecast_created',
    'forecast_scored', 'delivery_eligible', 'raw_redistribution', 'model_training',
    'scheduled_execution_rerouted', 'registered_study_modified'), False)
EVIDENCE_TEXT = {
    'complete_opening_paragraph': ('K18B廠於今(3)日舉行動土典禮', 'K18B廠房目標2028年第一季完工'),
    'publication_date': ('上版日期：114-10-03',),
    'cms_update_and_publisher': ('資料更新時間：115-08-14 14:25', '資料來源：經濟發展局')}


def now():
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def clock(value):
    return datetime.fromisoformat(models._iso_timestamp(value, 'clock').replace('Z', '+00:00'))


def codes():
    root = Path(__file__).parent.parent
    paths = sorted([*Path(__file__).parent.rglob('*.py'), *Path(__file__).parent.rglob('*.sql'),
        root / 'scripts/admit_ase_k18b_milestones.py', root / 'scripts/frozen_runtime.py'])
    return {str(path.relative_to(root)): runtime.digest(runtime.read(path)) for path in paths}


_LOADED_CODES = codes()


def rows():
    return [
        {'key': 'groundbreaking', 'value': asdict(models.MilestoneValue('groundbreaking',
            models.MilestoneStatus.COMPLETED, '2025-10-03', None, '2025-10-03', 'day',
            '今(3)日 / 上版日期：114-10-03')),
         'support': ['complete_opening_paragraph', 'publication_date'],
         'dating_basis': 'Reviewer binds the relative third-day ceremony statement to the article publication date; ROC year 114 + 1911 = 2025. Not an independently dated observation.'},
        {'key': 'building_complete', 'value': asdict(models.MilestoneValue('building_complete',
            models.MilestoneStatus.EXPECTED, '2028-01-01', None, '2028-03-31', 'quarter', '2028年第一季')),
         'support': ['complete_opening_paragraph'],
         'dating_basis': 'Source-stated first-quarter building-completion target; inclusive calendar bounds, no midpoint. Not production, mass production, HVM or qualification.'}]


def validate_review(raw, body, expected_sha256):
    runtime._hex(expected_sha256, 64)
    if runtime.digest(raw) != expected_sha256:
        raise ValueError('trusted K18B claim review hash mismatch')
    review = realized_milestones._json(raw)
    runtime._keys(review, {'format', 'decision', 'reviewed_at', 'reviewed_by', 'prior_exposure',
        'selection', 'parent_database_sha256', 'source_url', 'source_sha256', 'source_bytes',
        'project', 'access_review', 'source_review', 'source', 'evidence', 'rows', 'boundaries'})
    if review['format'] != FORMAT or review['decision'] != DECISION:
        raise ValueError('separate explicit K18B claim admission review is required')
    if (review['source_url'] != SOURCE_URL or review['source_sha256'] != SOURCE_SHA
            or runtime.digest(body) != SOURCE_SHA or type(review['source_bytes']) is not int
            or len(body) != review['source_bytes']):
        raise ValueError('retained K18B publisher bytes or exact URL differ')
    if review['project'] != PROJECT:
        raise ValueError('K18B source-native project identity must remain unresolved')
    if (review['boundaries'] != BOUNDARIES
            or any(value is not False for value in review['boundaries'].values())):
        raise ValueError('K18B admission cannot assert capacity, production or other excluded claims')
    runtime._hex(review['parent_database_sha256'], 64)
    for key in ('reviewed_by', 'prior_exposure', 'selection'):
        models._required(review[key], key)
    clock(review['reviewed_at'])
    source = review['source']
    runtime._keys(source, {'events', 'queue_head', 'candidate_id', 'expected_event_id', 'run_id',
        'document_id', 'plan_sha256', 'text_sha256', 'body', 'retrieved_at'})
    for key in ('queue_head', 'candidate_id', 'expected_event_id', 'run_id', 'plan_sha256', 'text_sha256'):
        runtime._hex(source[key], 64)
    models._required(source['document_id'], 'document_id')
    clock(source['retrieved_at'])
    runtime._keys(review['evidence'], EVIDENCE_TEXT)
    for key, span in review['evidence'].items():
        runtime._keys(span, {'start', 'end', 'sha256'})
        if (type(span['start']) is not int or type(span['end']) is not int
                or not 0 <= span['start'] < span['end'] <= len(body)
                or runtime.digest(body[span['start']:span['end']]) != span['sha256']):
            raise ValueError('K18B evidence span mismatch')
        document = realized_milestones._EvidenceHTML(body, span['start'], span['end'])
        tag = 'p' if key == 'complete_opening_paragraph' else 'dd'
        blocks = [node for node in document.nodes if node.tag == tag and not node.hidden
                  and node.start == span['start'] and node.end == span['end']]
        if len(blocks) != 1:
            raise ValueError('K18B evidence must retain one complete visible HTML block')
        if any(phrase not in document.text(span) for phrase in EVIDENCE_TEXT[key]):
            raise ValueError('K18B event, date, publication or publisher context is missing')
    if review['rows'] != rows():
        raise ValueError('the complete two-milestone selection or source semantics differs')
    return review


def dependency_snapshot(review_path, root):
    raw = runtime.read(review_path)
    review = realized_milestones._json(raw)
    files = {str(review_path): _file_state(review_path, raw)}
    for binding in (review['access_review'], review['source_review'], review['source']['events'],
                    review['source']['body']):
        path, body = _binding(root, binding)
        files[str(path)] = _file_state(path, body)
    _, events_raw = _binding(root, review['source']['events'])
    events = realized_milestones._json(events_raw)['events']
    curated_review._validate_events(events)
    captures = {}
    for event in events:
        if event['payload']['kind'] != 'capture_imported':
            continue
        packet = Path(event['payload']['source_path'])
        captures[str(packet)] = curated_capture._inventory(packet)
        files[str(packet / 'manifest.json')] = _file_state(packet / 'manifest.json')
        for relative, expected in captures[str(packet)].items():
            path = packet / relative
            state = _file_state(path)
            if any(state[key] != expected[key] for key in ('sha256', 'bytes')):
                raise ValueError('K18B capture changed during dependency snapshot')
            files[str(path)] = state
    return {'files': files, 'captures': captures}


def load_review(review_path, root, expected_sha256, at):
    raw = runtime.read(review_path)
    untrusted = realized_milestones._json(raw)
    body_path, body = _binding(root, untrusted['source']['body'])
    review = validate_review(raw, body, expected_sha256)
    source = review['source']
    events, report = _history(root, source['events'], curated_review, at)
    if not events or events[-1]['event_id'] != source['queue_head']:
        raise ValueError('frozen K18B queue head differs')
    candidate = _candidate(report, source, 'handed_off')
    if (candidate['source_url'] != SOURCE_URL or candidate['facility_key'] != SCOPE_KEY
            or candidate['current_text_sha256'] != source['text_sha256']):
        raise ValueError('K18B handoff URL, scope or text differs')
    route = {'facility_key': SCOPE_KEY, 'source_url': SOURCE_URL, 'source': source}
    _current_source(report, route)
    run = _one(report['runs'], 'run_id', source['run_id'], 'reviewed K18B capture')
    observation = _one(candidate['observations'], 'run_id', source['run_id'], 'K18B observation')
    packet = Path(run['source_path'])
    plan = realized_milestones._json(runtime.read(packet / 'plan.json'))
    document = _one(plan['documents'], 'id', source['document_id'], 'K18B document')
    if (run['plan_sha256'] != source['plan_sha256'] or plan['review_record'] != review['access_review']
            or document['url'] != SOURCE_URL or observation['document_id'] != source['document_id']
            or body_path != packet / 'responses' / f"{source['document_id']}.body"
            or observation['current_sha256'] != SOURCE_SHA
            or source['body']['sha256'] != SOURCE_SHA
            or source['retrieved_at'] != observation['captured_at']):
        raise ValueError('K18B plan, body or actual retrieval clock differs')
    _, access_raw = _binding(root, review['access_review'])
    access = realized_milestones._json(access_raw)
    if (access_raw != runtime.read(packet / 'review.json')
            or access.get('format') != 'semiconductor-atlas-ase-k18b-municipal-source-review-v1'
            or access.get('decision') != 'approve_one_exact_municipal_article_for_local_text_review_only'
            or access.get('reviewed_at') != plan['reviewed_at']
            or access.get('source_native_scope') != SCOPE_KEY
            or access.get('canonical_facility_assignment') is not None
            or any(access.get(key) is not False for key in ('claim_acceptance', 'delivery_eligible',
                'scheduled_execution_changed', 'registered_study_changed', 'baseline_changed'))):
        raise ValueError('separate bounded K18B access approval differs')
    approved = access.get('document', {})
    if (any(approved.get(key) != document[key] for key in ('id', 'url'))
            or approved.get('publisher') != PUBLISHER
            or approved.get('scope') != 'Source-native K18B building project only. Separate from the complete Kaohsiung campus aggregate, K28 at Dashe and the Nanzih Third Park buildings.'
            or document.get('scope') != 'City-authored K18B building groundbreaking and completion-target narrative only; not the existing Kaohsiung campus aggregate or attained production/qualification/capacity.'):
        raise ValueError('K18B access-approved document scope differs')
    rights = access.get('rights', {})
    if (rights.get('raw_redistribution') is not False or rights.get('model_training') is not False
            or rights.get('license_status') != 'unknown_no_open_license_identified'
            or rights.get('copyright_holder') != PUBLISHER):
        raise ValueError('K18B local-review-only rights must remain explicit')
    _, text_raw = _binding(root, review['source_review'])
    text_review = realized_milestones._json(text_raw)
    decision = candidate['decisions'][-1]
    if (text_review.get('format') != 'semiconductor-atlas-ase-k18b-source-text-adjudication-v1'
            or text_review.get('decision') != 'handoff_reviewed_text_for_separate_core_claim_admission'
            or decision['action'] != 'handoff'
            or decision['evidence_ref'] != review['source_review']['path']
            or text_review.get('candidate_id') != source['candidate_id']
            or text_review.get('expected_event_id') != decision['expected_event_id']
            or text_review.get('source_url') != SOURCE_URL
            or text_review.get('access_review') != review['access_review']
            or text_review.get('evidence_spans') != review['evidence']
            or not text_review.get('boundaries')
            or any(value is not False for value in text_review['boundaries'].values())
            or text_review['boundaries'].get('core_claim_acceptance') is not False):
        raise ValueError('separate K18B source-text handoff review differs')
    models._required(access.get('reviewer'), 'access reviewer')
    models._required(text_review.get('reviewer'), 'text reviewer')
    at_text = curated_review._fold(events, as_of=text_review['reviewed_at'])
    pre = _one(at_text['candidates'], 'id', candidate['id'], 'K18B predecision candidate')
    if (pre['status'] not in curated_review.OPEN_STATUSES or pre['requires_reopen']
            or pre['last_event_id'] != decision['expected_event_id']):
        raise ValueError('K18B source-text predecision CAS was not current at review')
    _current_source(at_text, route)
    capture = text_review.get('source_capture', {})
    if capture != {'path': str(packet.relative_to(root)), 'manifest_sha256': source['run_id'],
            'body_path': f"responses/{source['document_id']}.body", 'body_sha256': SOURCE_SHA,
            'body_bytes': len(body), 'normalized_sha256': source['text_sha256'],
            'retrieved_at': source['retrieved_at'], 'queue_admitted_at': observation['imported_at']}:
        raise ValueError('K18B text-review capture identity or chronology differs')
    clocks = [access['reviewed_at'], run['started_at'], source['retrieved_at'], run['finished_at'],
        observation['imported_at'], text_review['reviewed_at'], decision['recorded_at'],
        review['reviewed_at'], at]
    if any(clock(a) > clock(b) for a, b in zip(clocks, clocks[1:])):
        raise ValueError('K18B access, capture, text review and claim admission chronology differs')
    if any(clock(event['recorded_at']) > clock(review['reviewed_at']) for event in events):
        raise ValueError('K18B claim review could not yet know the frozen queue history')
    return {'review': review, 'events': events, 'rights': rights,
            'source_queue_admitted_at': observation['imported_at'], 'source_handoff_at': decision['recorded_at']}


def open_image(raw):
    connection = sqlite3.connect(':memory:')
    try:
        connection.deserialize(raw)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        if database.schema_version(connection) != 7 or connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('expected intact schema-7 database image')
        return connection
    except BaseException:
        connection.close()
        raise


def populate(connection, data, review_sha256, started, admitted, code_sha256, dependencies):
    review = data['review']
    if not clock(review['reviewed_at']) <= clock(started) < clock(admitted):
        raise ValueError('K18B review/admission chronology differs')
    family_key = 'kcg-economic-development-bureau'
    source_key = 'municipal-article:' + SOURCE_URL
    entity_key = source_key + ':K18B'
    family_id, source_id, entity_id = [repository.stable_id(key) for key in (family_key, source_key, entity_key)]
    run_id = repository.stable_id(RULE, review_sha256)
    if (connection.execute('SELECT 1 FROM entities WHERE id=? OR stable_key=?', (entity_id, entity_key)).fetchone()
            or connection.execute('SELECT 1 FROM sources WHERE id=? OR stable_key=?', (source_id, source_key)).fetchone()
            or connection.execute('SELECT 1 FROM ingestion_runs WHERE id=?', (run_id,)).fetchone()):
        raise ValueError('K18B source/project already admitted; this bounded route is not a refresh importer')
    family = connection.execute('SELECT * FROM source_families WHERE stable_key=?', (family_key,)).fetchone()
    family_created = family['created_at'] if family else admitted
    repository.add_source_family(connection, models.SourceFamily(family_id, family_key, PUBLISHER, family_created))
    rights_label = 'No open license identified; local review only; raw redistribution and model training not authorized'
    repository.add_source(connection, models.Source(source_id, family_id, source_key,
        'Kaohsiung municipal K18B article', PUBLISHER, SOURCE_URL, admitted, rights_label))
    document_id = repository.stable_id(source_id, SOURCE_SHA, review['source']['retrieved_at'])
    metadata = {'source_native_project': PROJECT, 'retained_body': review['source']['body'],
        'rights': data['rights'], 'publication_date_basis': 'Page publication label ROC 114-10-03, converted to Gregorian 2025-10-03; not proof of first availability of each statement.',
        'cms_update_literal': '115-08-14 14:25', 'cms_timezone': None,
        'first_publication_of_target': None, 'claim_effective_at': None}
    repository.add_source_document(connection, models.SourceDocument(document_id, source_id, SOURCE_URL,
        'Municipal report of K18B groundbreaking and building-completion target',
        review['source']['retrieved_at'], SOURCE_SHA, published_at='2025-10-03',
        media_type='text/html', license=rights_label, metadata=metadata))
    repository.add_ingestion_run(connection, models.IngestionRun(run_id, source_id, started,
        models.IngestionStatus.SUCCEEDED, admitted, code_sha256['semiconductor_atlas/ase_k18b_milestones.py'],
        document_id, {'rule_version': RULE, 'review_sha256': review_sha256, 'review': review,
            'code_sha256': code_sha256, 'interpreter': runtime.interpreter(), 'input_files': dependencies,
            'rights': data['rights'], 'boundaries': BOUNDARIES, 'accepted_at': admitted,
            'source_queue_admitted_at': data['source_queue_admitted_at'], 'source_handoff_at': data['source_handoff_at'],
            'acceptance_timestamp_basis': 'actual_database_admission',
            'queue_policy': 'New admission reserves and matches the current queue; historical replay uses only the frozen reviewed event history.'}))
    repository.add_entity(connection, models.Entity(entity_id, models.EntityKind.PROJECT, entity_key,
        admitted, PROJECT['label'], run_id))
    claims = []
    for row in review['rows']:
        series_key = entity_key + ':milestone.' + row['key']
        series_id = repository.stable_id(series_key)
        claim_id, record_id = [repository.stable_id(RULE, review_sha256, row['key'], kind) for kind in ('claim', 'record')]
        value = models.MilestoneValue(**{**row['value'], 'status': models.MilestoneStatus(row['value']['status'])})
        repository.add_claim_series(connection, models.ClaimSeries(series_id, entity_id, series_key,
            'milestone.' + row['key'], models.ValueKind.MILESTONE, admitted))
        payload = {'review_sha256': review_sha256, 'source_native_project': PROJECT,
            'milestone': row, 'evidence_spans': review['evidence'], 'core_admitted_at': admitted,
            'claim_effective_at': None, 'confidence': None}
        repository.add_source_record(connection, models.SourceRecord(record_id, run_id, document_id,
            row['key'], review['source']['retrieved_at'], models.source_record_payload_sha256(payload), payload))
        evidence = []
        for label in [*row['support'], 'cms_update_and_publisher']:
            span = review['evidence'][label]
            evidence.append(models.EvidenceLink(document_id,
                models.EvidenceRole.SUPPORT if label in row['support'] else models.EvidenceRole.CONTEXT,
                record_id, f"{label}; UTF-8 bytes [{span['start']},{span['end']}); SHA-256 {span['sha256']}"))
        repository.insert_claim(connection, models.ClaimVersion(claim_id, series_id, None, admitted,
            models.ClaimKind.SOURCE_STATEMENT, RULE, None, created_by_run_id=run_id,
            notes=row['dating_basis']), value, evidence=evidence)
        claims.append({'claim_id': claim_id, 'key': row['key'], 'value': row['value']})
    issues = repository.validate_database(connection)
    if issues:
        raise ValueError('K18B successor validation failed: ' + str(issues[:3]))
    connection.commit()
    return {'run_id': run_id, 'admitted_at': admitted, 'source_native_entity_id': entity_id,
        'milestones': claims, 'boundaries': BOUNDARIES}


def admit(*, parent, review_path, reference_root, expected_review_sha256, output,
          queue=None, verify_only=False, expected_output_sha256=None):
    started, current_codes = now(), codes()
    if current_codes != _LOADED_CODES:
        raise ValueError('K18B implementation changed since module loading')
    if verify_only:
        runtime._hex(expected_output_sha256, 64)
        if queue is not None:
            raise ValueError('historical verification must not depend on the live queue')
    elif expected_output_sha256 is not None or queue is None:
        raise ValueError('new admission requires a current queue and no successor input hash')
    parent, review_path, root, target = map(lambda p: Path(p).absolute(), (parent, review_path, reference_root, output))
    snapshot = dependency_snapshot(review_path, root)
    data = load_review(review_path, root, expected_review_sha256, started)
    parent_raw = runtime.read(parent, limit=1_000_000_000)
    if runtime.digest(parent_raw) != data['review']['parent_database_sha256']:
        raise ValueError('trusted K18B parent database hash mismatch')
    if not verify_only and (target.exists() or target.is_symlink()):
        raise ValueError('K18B successor output must not exist')
    with ExitStack() as stack:
        if not verify_only:
            queue_path = Path(queue).absolute()
            queue_state = _file_state(queue_path)
            reserved = stack.enter_context(curated_review._connection(queue_path, write=True))
            if curated_review._events(reserved) != data['events']:
                raise ValueError('current K18B queue differs from the frozen reviewed handoff')
        connection = open_image(parent_raw)
        stack.callback(connection.close)
        if verify_only:
            successor = runtime.read(target, limit=1_000_000_000)
            if runtime.digest(successor) != expected_output_sha256:
                raise ValueError('trusted K18B successor hash mismatch')
            actual = open_image(successor)
            try:
                run = actual.execute('SELECT * FROM ingestion_runs WHERE id=?',
                    (repository.stable_id(RULE, expected_review_sha256),)).fetchone()
                if run is None or clock(run['completed_at']) > clock(started):
                    raise ValueError('missing or future K18B successor admission')
                admission_start, admitted = run['started_at'], run['completed_at']
            finally:
                actual.close()
        else:
            admission_start, admitted = started, now()
        dependencies = {path: {key: state[key] for key in ('sha256', 'bytes')}
                        for path, state in snapshot['files'].items()}
        result = populate(connection, data, expected_review_sha256, admission_start, admitted, current_codes, dependencies)
        generated = connection.serialize()
        if verify_only and generated != successor:
            raise ValueError('successor differs from complete parent-plus-K18B reconstruction')
        if (dependency_snapshot(review_path, root) != snapshot
                or runtime.read(parent, limit=1_000_000_000) != parent_raw):
            raise ValueError('K18B inputs changed during admission or replay')
        if codes() != current_codes:
            raise ValueError('K18B implementation changed during admission or replay')
        if verify_only:
            if runtime.read(target, limit=1_000_000_000) != successor:
                raise ValueError('K18B successor changed during verification')
        else:
            if _file_state(queue_path) != queue_state or curated_review._events(reserved) != data['events']:
                raise ValueError('K18B live queue changed across the acceptance boundary')
            runtime.write_new(target, generated)
    return {**result, 'database_sha256': runtime.digest(generated), 'database_bytes': len(generated),
        'verified': verify_only, 'parent_modified': False, 'queue_modified': False, 'new_milestone_statements': 2}
