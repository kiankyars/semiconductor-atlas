"""Admit the City's bounded Ohio One construction report, not a physical milestone."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from scripts import frozen_runtime as runtime
from . import curated_capture, database, models, realized_milestones, repository, source_checks


RULE = 'new-albany-source-reported-project-statements-v1'
FORMAT = 'semiconductor-atlas-new-albany-project-admission-v1'
DECISION = 'accept_three_source_reported_project_statements'
ORIGIN = 'https://siliconheartland.newalbanyohio.org/'
FAMILY = 'new-albany-city-government'
PROJECT = {
    'stable_key': 'new-albany-city:ohio-one-two-fab-program-source-native-unresolved',
    'label': 'City-reported Ohio One two-fab program (canonical identity unresolved)',
    'scope': 'The two Intel chip plants and Ohio One campus in New Albany described together by the City; no allocation to individual fabs or assignment to the NIST project.',
    'canonical_entity_id': None,
}
DOCUMENTS = {
    'new-albany-project-timeline': {
        'url': ORIGIN + 'project-timeline-update/', 'title': 'Project Timeline Update',
        'sha256': '51312492b646ccc2ebc08adaf3fe97436326d6c6036b592cdfc90cb08d3dad74',
    },
    'new-albany-construction-updates': {
        'url': ORIGIN + 'construction-updates/', 'title': 'Construction Updates',
        'sha256': '48856cac6ecff00a555b30ded0b69da399d6612d140b9f10c4fccb84ed5d7e08',
    },
}
BOUNDARIES = dict.fromkeys((
    'current_physical_state_inferred', 'physical_effective_date_inferred',
    'publication_date_inferred', 'cancellation_inferred', 'full_pause_inferred',
    'completed_milestone_inferred', 'numeric_capacity_inferred', 'canonical_identity_assigned',
    'individual_fab_allocation', 'independent_physical_corroboration',
    'historical_system_knowledge_backdated', 'forecast_created', 'forecast_scored',
    'original_pmt_target_changed', 'original_pmt_outcome_changed', 'alert_generated',
    'baseline_modified', 'scheduled_execution_changed', 'raw_redistribution', 'source_acquisition',
), False)


def rows():
    both = list(DOCUMENTS)
    return [
        {'key': 'construction-pace', 'predicate': 'source_reported.construction_pace',
         'value': 'slowing', 'support': both,
         'scope_limit': 'Municipal report of a construction-pace decision, not full suspension, cancellation, achieved production or a quantified capacity change.'},
        {'key': 'project-commitment', 'predicate': 'source_reported.project_commitment',
         'value': 'reaffirmed', 'support': both,
         'scope_limit': 'Commitment language accompanies the slowdown; it does not establish funding sufficiency, completion probability or current physical work.'},
        {'key': 'slowdown-announcement-month', 'predicate': 'source_reported.slowdown_announcement_month',
         'value': '2025-07', 'support': ['new-albany-construction-updates'],
         'scope_limit': 'Month of the Intel announcement as reported by the City index, not page publication, physical slowdown onset or a deadline; no day or midpoint is assigned.'},
    ]


def now():
    return curated_capture._now()


def clock(value):
    return curated_capture._instant(value)


def codes():
    root = Path(__file__).parent.parent
    paths = sorted([*Path(__file__).parent.rglob('*.py'), *Path(__file__).parent.rglob('*.sql'),
                    root / 'scripts/admit_new_albany_project_statements.py', root / 'scripts/frozen_runtime.py'])
    return {str(p.relative_to(root)): runtime.digest(runtime.read(p)) for p in paths}


def relative(value):
    if (not isinstance(value, str) or not value or len(value) > 500
            or PurePosixPath(value).is_absolute() or '\\' in value
            or any(p in ('', '.', '..') for p in value.split('/'))
            or value.split('/')[0] not in ('review_plans', 'artifacts')):
        raise ValueError('evidence must use bounded repository-relative paths')
    return value


def validate_review(raw, expected_sha256):
    runtime._hex(expected_sha256, 64)
    if runtime.digest(raw) != expected_sha256:
        raise ValueError('trusted New Albany review hash mismatch')
    review = realized_milestones._json(raw)
    runtime._keys(review, {'format', 'decision', 'reviewed_at', 'reviewer', 'prior_exposure',
        'interpretation', 'parent_database_sha256', 'project', 'documents', 'rows', 'packets',
        'access_review', 'input_files', 'boundaries'})
    if review['format'] != FORMAT or review['decision'] != DECISION:
        raise ValueError('explicit New Albany project-statement review required')
    clock(review['reviewed_at'])
    for key in ('reviewer', 'prior_exposure', 'interpretation'):
        models._required(review[key], key)
    runtime._hex(review['parent_database_sha256'], 64)
    if review['project'] != PROJECT or review['rows'] != rows():
        raise ValueError('source-native project or complete three-statement selection differs')
    if review['boundaries'] != BOUNDARIES or any(v is not False for v in review['boundaries'].values()):
        raise ValueError('excluded inference boundaries must remain false')
    if set(review['documents']) != set(DOCUMENTS):
        raise ValueError('both reviewed municipal documents are required')
    for key, document in review['documents'].items():
        runtime._keys(document, {'url', 'title', 'sha256', 'bytes', 'published_at',
            'retrieved_at', 'body_path', 'narrative'})
        if (any(document[k] != v for k, v in DOCUMENTS[key].items())
                or document['published_at'] is not None):
            raise ValueError('document identity or unknown publication date differs')
        clock(document['retrieved_at'])
        relative(document['body_path'])
    if not isinstance(review['packets'], list) or len(review['packets']) != 3:
        raise ValueError('robots, rights and document capture packets are required')
    for packet in review['packets']:
        runtime._keys(packet, {'path', 'manifest_sha256', 'plan_path', 'kind'})
        relative(packet['path']); relative(packet['plan_path'])
        runtime._hex(packet['manifest_sha256'], 64)
    if [p['kind'] for p in review['packets']] != ['robots_probe', 'rights_probe', 'curated_capture']:
        raise ValueError('ordered policy and capture packet roles differ')
    if len({p['path'] for p in review['packets']}) != 3:
        raise ValueError('retained packets must be distinct')
    runtime._keys(review['access_review'], {'path', 'sha256'})
    relative(review['access_review']['path']); runtime._hex(review['access_review']['sha256'], 64)
    files = review['input_files']
    if not isinstance(files, dict) or not 10 <= len(files) <= 80:
        raise ValueError('bounded complete evidence inventory required')
    for name, identity in files.items():
        relative(name)
        runtime._keys(identity, {'sha256', 'bytes'})
        runtime._hex(identity['sha256'], 64)
        if type(identity['bytes']) is not int or not 0 < identity['bytes'] <= 4_000_000:
            raise ValueError('invalid evidence byte bound')
    return review


def snapshot(review, root):
    payloads = {}
    for name, expected in review['input_files'].items():
        raw = runtime.read(Path(root) / name, limit=4_000_000)
        if len(raw) != expected['bytes'] or runtime.digest(raw) != expected['sha256']:
            raise ValueError('retained evidence differs: ' + name)
        payloads[name] = raw
    if sum(map(len, payloads.values())) > 16_000_000:
        raise ValueError('retained evidence exceeds total byte bound')
    return payloads


def _packet_names(root, name):
    packet = Path(root) / name
    os.close(runtime._directory(packet))
    names = set()
    for path in packet.rglob('*'):
        if path.is_symlink():
            raise ValueError('symlink in retained packet')
        if path.is_file():
            names.add(path.relative_to(root).as_posix())
    return names


def load_evidence(review, payloads, reference_root):
    access_name = review['access_review']['path']
    if runtime.digest(payloads.get(access_name, b'')) != review['access_review']['sha256']:
        raise ValueError('access review binding differs')
    access = realized_milestones._json(payloads[access_name])
    if (access['decision'] != 'approve_two_exact_city_pages_for_local_source_text_review_only'
            or access['rights']['license_status'] != 'unknown_no_open_license_identified'
            or access['rights']['copyright_holder'] != 'City of New Albany'
            or any(access['rights'][k] is not False for k in
                   ('raw_redistribution', 'model_training', 'third_party_resources', 'commercial_reuse_approved'))
            or any(access['acquisition_boundary'][k] is not False for k in
                   ('redirects', 'retries', 'cookies', 'discovery', 'claim_acceptance',
                    'delivery_eligible', 'scheduled_execution'))):
        raise ValueError('bounded local-review rights or acquisition limits differ')
    required = {access_name}
    ledgers = []
    for packet in review['packets']:
        name = packet['path']
        manifest_name = name + '/manifest.json'
        if runtime.digest(payloads.get(manifest_name, b'')) != packet['manifest_sha256']:
            raise ValueError('retained packet manifest differs')
        manifest = realized_milestones._json(payloads[manifest_name])
        runtime._keys(manifest, {'format', 'files'})
        expected_format = ('semiconductor-atlas-curated-acquisition-manifest-v1'
                           if packet['kind'] == 'curated_capture' else 'semiconductor-atlas-policy-probe-manifest-v1')
        if manifest['format'] != expected_format:
            raise ValueError('retained packet format differs')
        members = manifest['files']
        names = {relative(name + '/' + member) for member in members} | {manifest_name}
        if _packet_names(reference_root, name) != names:
            raise ValueError('complete retained packet inventory differs')
        required |= names | {packet['plan_path']}
        for member, identity in members.items():
            raw = payloads.get(name + '/' + member, b'')
            if runtime.digest(raw) != identity['sha256'] or len(raw) != identity['bytes']:
                raise ValueError('retained packet member bytes differ')
        copy_name = name + ('/plan.json' if packet['kind'] == 'curated_capture' else '/probe.json')
        if payloads.get(packet['plan_path']) != payloads.get(copy_name):
            raise ValueError('original and captured acquisition plans differ')
        ledgers.append(realized_milestones._json(payloads[name + '/source_checks.json']))
    if set(payloads) != required:
        raise ValueError('missing or unrelated evidence inventory members')
    probe_urls = [[ORIGIN + 'robots.txt', 'https://newalbanyohio.org/robots.txt'],
                  [ORIGIN, 'https://newalbanyohio.org/privacy-policy/']]
    for index, (packet, ledger) in enumerate(zip(review['packets'][:2], ledgers[:2])):
        probe = realized_milestones._json(payloads[packet['plan_path']])
        if (probe['format'] != 'semiconductor-atlas-bounded-policy-review-v1'
                or [e['url'] for e in probe['entries']] != probe_urls[index]
                or len(ledger['attempts']) != 2):
            raise ValueError('exact initial policy-probe selection differs')
        for entry, attempt in zip(probe['entries'], ledger['attempts']):
            if (any(entry[key] != attempt[key] for key in
                    ('id', 'url', 'purpose', 'company', 'country_code', 'source_family', 'scope'))
                    or attempt['purpose'] != ('access_policy' if index == 0 else 'rights_policy')
                    or attempt['path'] != f"responses/{attempt['id']}.body"):
                raise ValueError('policy-probe plan and ledger differ')
            receipt = packet['path'] + f"/responses/{attempt['id']}.attempt.json"
            if realized_milestones._json(payloads[receipt]) != attempt:
                raise ValueError('policy-probe attempt receipt differs from ledger')
            transport = packet['path'] + f"/responses/{attempt['id']}.transport.json"
            if transport in payloads:
                observed = realized_milestones._json(payloads[transport])
                if (observed['url_effective'] != attempt['final_url']
                        or observed['http_code'] != attempt['http_status']
                        or observed['curl_exit_code'] != attempt['curl_exit_code']
                        or observed['ssl_verify_result'] != attempt['tls_verification_result']
                        or observed['num_redirects'] != attempt['redirect_count']):
                    raise ValueError('policy-probe transport receipt differs from ledger')
    rights_probe = realized_milestones._json(payloads[review['packets'][1]['plan_path']])
    if rights_probe['robots_capture'] != {
            'path': review['packets'][0]['path'], 'manifest_sha256': review['packets'][0]['manifest_sha256']}:
        raise ValueError('rights probe must depend on the retained robots packet')
    capture = review['packets'][-1]['path']
    plan = realized_milestones._json(payloads[capture + '/plan.json'])
    if (plan['review_record'] != review['access_review'] or payloads[capture + '/review.json'] != payloads[access_name]
            or plan['minimum_interval_seconds'] != 10
            or [d['url'] for d in plan['documents']] != [d['url'] for d in DOCUMENTS.values()]
            or [p['url'] for p in plan['policies']] != [ORIGIN + 'robots.txt', ORIGIN]):
        raise ValueError('exact reviewed source/policy graph differs')
    if access['policy_captures'] != [
        {'path': p['path'], 'manifest_sha256': p['manifest_sha256'], 'review': a['review']}
        for p, a in zip(review['packets'][:2], access['policy_captures'])
    ] or len(access['policy_captures']) != 2:
        raise ValueError('initial policy review packet bindings differ')
    if len(access['bound_policies']) != 2:
        raise ValueError('both same-origin policy body bindings are required')
    for index, (bound, policy) in enumerate(zip(access['bound_policies'], plan['policies'])):
        probe = review['packets'][index]
        attempt = ledgers[index]['attempts'][0]
        raw = payloads[probe['path'] + '/' + attempt['path']]
        if (bound['url'] != policy['url'] or bound['url'] != attempt['url']
                or bound['body_sha256'] != runtime.digest(raw)
                or bound['normalization'] != policy['normalization']
                or bound['normalized_sha256'] != policy['normalized_sha256']
                or runtime.digest(curated_capture.normalized_bytes(raw, bound['normalization'])) != bound['normalized_sha256']):
            raise ValueError('reviewed policy body and capture-plan normalization differ')
    with tempfile.TemporaryDirectory(prefix='atlas-new-albany-evidence-') as directory:
        private = Path(directory).resolve()
        for name, raw in payloads.items():
            path = private / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(raw)
        for packet in review['packets']:
            source_checks.validate_source_checks(private / packet['path'] / 'source_checks.json', private / packet['path'])
        result = curated_capture.validate_capture(private / capture)
    if any(d['status'] != 'first_observation_requires_review' for d in result['documents']):
        raise ValueError('both first observations must have successful source-text captures')
    attempts = ledgers[-1]['attempts']
    if (len(attempts) != 4 or [a['purpose'] for a in attempts] !=
            ['access_policy', 'rights_policy', 'source_document', 'source_document']
            or any(a['outcome'] != 'succeeded' or a['redirect_count'] != 0 for ledger in ledgers for a in ledger['attempts'])):
        raise ValueError('successful no-redirect policy/source attempts required')
    for ledger in ledgers[1:]:
        for previous, later in zip(ledger['attempts'], ledger['attempts'][1:]):
            if (clock(later['started_at']) - clock(previous['finished_at'])).total_seconds() < 10:
                raise ValueError('rights/source requests violate the ten-second cadence')
    previous_by_origin = {}
    for ledger in ledgers:
        for attempt in ledger['attempts']:
            origin = urlsplit(attempt['url']).netloc
            previous = previous_by_origin.get(origin)
            if previous and (clock(attempt['started_at']) - clock(previous['finished_at'])).total_seconds() < 10:
                raise ValueError('cross-packet policy/source order or origin cadence differs')
            previous_by_origin[origin] = attempt
    if (any(clock(a['finished_at']) > clock(access['reviewed_at']) for ledger in ledgers[:2] for a in ledger['attempts'])
            or not clock(access['reviewed_at']) <= clock(attempts[0]['started_at'])
            or not clock(attempts[-1]['finished_at']) <= clock(review['reviewed_at'])):
        raise ValueError('policy, capture and interpretation chronology differs')
    for identity, attempt in zip(DOCUMENTS, attempts[2:]):
        source = review['documents'][identity]
        if (attempt['id'] != identity or attempt['url'] != source['url']
                or attempt['sha256'] != source['sha256'] or attempt['bytes'] != source['bytes']
                or attempt['finished_at'] != source['retrieved_at']
                or capture + '/' + attempt['path'] != source['body_path']):
            raise ValueError('document bytes or actual retrieval clock differ')
        raw, span = payloads[source['body_path']], source['narrative']
        runtime._keys(span, {'start', 'end', 'sha256', 'tag'})
        if (type(span['start']) is not int or type(span['end']) is not int
                or not 0 <= span['start'] < span['end'] <= len(raw)
                or runtime.digest(raw[span['start']:span['end']]) != span['sha256']):
            raise ValueError('complete narrative span differs')
        parsed = realized_milestones._EvidenceHTML(raw, span['start'], span['end'])
        roots = [n for n in parsed.nodes if n.start == span['start'] and n.end == span['end']
                 and n.tag == span['tag'] == 'div' and not n.hidden]
        if len(roots) != 1 or roots[0].attrs.get('class') != 'text':
            raise ValueError('evidence must be the complete visible project text block')
        headings = [n for n in parsed.nodes if n.attrs.get('class') == 'header']
        if len(headings) != 1 or headings[0].text() != 'Project Timeline Update':
            raise ValueError('project narrative heading differs')
        text = parsed.text(span)
        if identity == 'new-albany-project-timeline':
            markers = ('two Ohio chip manufacturing plants', 'Mayor Sloan Spalding',
                       'reaffirmed its commitment', 'slow the construction pace', 'Ohio One campus in New Albany')
            paragraphs = 3
        else:
            markers = ('Intel announced in July 2025', 'slowing construction',
                       'two chip manufacturing plants', 'reiterated its full commitment')
            paragraphs = 2
            links = [n for n in parsed.nodes if n.tag == 'a']
            if len(links) != 1 or links[0].attrs.get('href') != DOCUMENTS['new-albany-project-timeline']['url']:
                raise ValueError('index must link the reviewed timeline narrative')
        if any(marker not in text for marker in markers) or len([n for n in parsed.nodes if n.tag == 'p']) != paragraphs:
            raise ValueError('complete reviewed project scope, pace or commitment context differs')
    return {'rights': access['rights']}


def open_image(raw):
    connection = sqlite3.connect(':memory:')
    try:
        connection.deserialize(raw)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        if (database.schema_version(connection) != 9
                or connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                or connection.execute('PRAGMA foreign_key_check').fetchall()):
            raise ValueError('expected intact schema-9 database image')
        return connection
    except BaseException:
        connection.close()
        raise


def populate(connection, review, data, review_sha256, started, admitted, code_sha256):
    if not clock(review['reviewed_at']) <= clock(started) < clock(admitted):
        raise ValueError('source review must precede actual database admission')
    source_key = FAMILY + ':silicon-heartland-project-reporting'
    family_id, source_id, project_id = [repository.stable_id(k) for k in (FAMILY, source_key, PROJECT['stable_key'])]
    run_id = repository.stable_id(RULE, review_sha256)
    if (connection.execute('SELECT 1 FROM entities WHERE id=? OR stable_key=?', (project_id, PROJECT['stable_key'])).fetchone()
            or connection.execute('SELECT 1 FROM sources WHERE id=? OR stable_key=?', (source_id, source_key)).fetchone()
            or connection.execute('SELECT 1 FROM ingestion_runs WHERE id=?', (run_id,)).fetchone()):
        raise ValueError('New Albany project/source already admitted; not a refresh importer')
    family = connection.execute('SELECT * FROM source_families WHERE stable_key=?', (FAMILY,)).fetchone()
    if family:
        family_id = family['id']
    else:
        repository.add_source_family(connection, models.SourceFamily(family_id, FAMILY, 'City of New Albany project reporting', admitted))
    rights_label = 'No open license identified; bounded local review only, no raw redistribution or training approval'
    repository.add_source(connection, models.Source(source_id, family_id, source_key,
        'Silicon Heartland municipal project reporting', 'City of New Albany', ORIGIN, admitted, rights_label))
    document_ids = {}
    for key, source in review['documents'].items():
        doc_id = repository.stable_id(source_id, source['sha256'], source['retrieved_at'])
        document_ids[key] = doc_id
        repository.add_source_document(connection, models.SourceDocument(doc_id, source_id, source['url'],
            source['title'], source['retrieved_at'], source['sha256'], published_at=None,
            media_type='text/html', license=rights_label, metadata={'rights': data['rights'],
                'retained_body': source['body_path'], 'publication_precision': 'unknown',
                'publication_basis': 'No visible publication date or publication metadata identified; announcement month, illustration caption and copyright footer are not publication dates.',
                'source_native_project': PROJECT, 'narrative': source['narrative']}))
    repository.add_ingestion_run(connection, models.IngestionRun(run_id, source_id, started,
        models.IngestionStatus.SUCCEEDED, admitted, code_sha256['semiconductor_atlas/new_albany_project_statements.py'],
        None, {'rule_version': RULE, 'review_sha256': review_sha256, 'review': review,
               'code_sha256': code_sha256, 'interpreter': runtime.interpreter(),
               'rights': data['rights'], 'boundaries': BOUNDARIES, 'accepted_at': admitted,
               'acceptance_timestamp_basis': 'actual_database_admission'}))
    repository.add_entity(connection, models.Entity(project_id, models.EntityKind.PROJECT,
        PROJECT['stable_key'], admitted, PROJECT['label'], run_id))
    records = {}
    for key, source in review['documents'].items():
        record_id = repository.stable_id(RULE, review_sha256, key, 'record')
        records[key] = record_id
        payload = {'review_sha256': review_sha256, 'source_native_project': PROJECT,
                   'narrative': source['narrative'], 'supported_rows': [r for r in rows() if key in r['support']],
                   'core_admitted_at': admitted, 'claim_effective_at': None, 'confidence': None}
        repository.add_source_record(connection, models.SourceRecord(record_id, run_id, document_ids[key],
            key, source['retrieved_at'], models.source_record_payload_sha256(payload), payload))
    claims = []
    for row in rows():
        series_key = PROJECT['stable_key'] + ':' + row['predicate']
        series_id = repository.stable_id(series_key)
        claim_id = repository.stable_id(RULE, review_sha256, row['key'], 'claim')
        repository.add_claim_series(connection, models.ClaimSeries(series_id, project_id, series_key,
            row['predicate'], models.ValueKind.SCALAR, admitted))
        evidence = []
        for key in row['support']:
            span = review['documents'][key]['narrative']
            evidence.append(models.EvidenceLink(document_ids[key], models.EvidenceRole.SUPPORT, records[key],
                f"Complete Project Timeline Update narrative; UTF-8 bytes [{span['start']},{span['end']}); SHA-256 {span['sha256']}"))
        repository.insert_claim(connection, models.ClaimVersion(claim_id, series_id, None, admitted,
            models.ClaimKind.SOURCE_STATEMENT, RULE, None, created_by_run_id=run_id, notes=row['scope_limit']),
            models.ScalarValue(models.ScalarType.TEXT, row['value']), evidence=evidence)
        claims.append({'claim_id': claim_id, 'predicate': row['predicate'], 'value': row['value']})
    issues = repository.validate_database(connection)
    if issues:
        raise ValueError('New Albany successor validation failed: ' + str(issues[:3]))
    connection.commit()
    return {'run_id': run_id, 'admitted_at': admitted, 'project_id': project_id, 'statements': claims,
            'boundaries': BOUNDARIES}


def admit(*, parent, review_path, reference_root, expected_review_sha256, output,
          verify_only=False, expected_output_sha256=None):
    started, current_codes = now(), codes()
    if current_codes != _LOADED_CODES:
        raise ValueError('New Albany implementation changed since module loading')
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
    connection = open_image(parent_raw)
    try:
        if verify_only:
            successor = runtime.read(target, limit=1_000_000_000)
            if runtime.digest(successor) != expected_output_sha256:
                raise ValueError('trusted successor hash mismatch')
            actual = open_image(successor)
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
            raise ValueError('successor differs from full parent-plus-statements reconstruction')
        if (runtime.read(Path(review_path)) != review_raw
                or runtime.read(Path(parent), limit=1_000_000_000) != parent_raw
                or snapshot(review, reference_root) != payloads):
            raise ValueError('evidence or parent changed during admission or replay')
        load_evidence(review, payloads, reference_root)
        if codes() != current_codes:
            raise ValueError('New Albany implementation changed during admission or replay')
        if verify_only and runtime.read(target, limit=1_000_000_000) != successor:
            raise ValueError('successor changed during verification')
        if not verify_only:
            runtime.write_new(target, generated)
        return {**result, 'database_sha256': runtime.digest(generated), 'database_bytes': len(generated),
                'verified': verify_only, 'parent_modified': False, 'new_source_statements': 3,
                'schema_version': 9, 'new_milestones': 0, 'new_numeric_capacity_claims': 0}
    finally:
        connection.close()


_LOADED_CODES = codes()
